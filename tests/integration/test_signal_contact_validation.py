"""宣言条件・診断・終了情報の整合性を、物理再計算なしに検証する回帰。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import FastArmRuntimePlugin
from xpotato_sim.runtime.runners.signal_contact import capture_signal_contact, canonical, strict_json
from xpotato_sim.runtime.runners.signal_contact_artifact import decode_signal_trace

ROOT = Path(__file__).resolve().parents[2]
REVISION = "test-only-r7l-validation"


def fixture(source="selfrionette"):
    """既存の合成条件をそのまま使い、異常条件だけを呼出側で変更する。"""
    return json.loads((ROOT / "tests/fixtures/prehardware_signal" / f"{source}.json").read_text(encoding="utf-8"))


def readback(envelope):
    """hash一致だけでは検出できない不整合を読戻しへ渡す。"""
    envelope["payload_sha256"] = sha256(canonical(envelope["payload"])).hexdigest()
    return decode_signal_trace(canonical(envelope) + b"\n", expected_revision=REVISION)


@pytest.fixture(scope="module")
def captured():
    return strict_json(capture_signal_contact(fixture(), software_revision=REVISION))


@pytest.mark.parametrize("evaluators", (
    [{"plugin_id": "nonexistent_evaluator", "contract_version": 1}],
    [{"plugin_id": "contact_outcome", "contract_version": 2}],
    [{"plugin_id": "completion_time", "contract_version": 1}],
    [{"plugin_id": "contact_outcome", "contract_version": 1}, {"plugin_id": "nonexistent_evaluator", "contract_version": 999}],
    [{"plugin_id": "contact_outcome", "contract_version": 1}, {"plugin_id": "completion_time", "contract_version": 1}],
    [{"plugin_id": "contact_outcome", "contract_version": 1}] * 2,
))
def test_unsupported_evaluators_fail_before_model_or_reader(monkeypatch, evaluators):
    """宣言の無視や実行後の遅い失敗ではなく、最初のmodel生成前に拒否する。"""
    config = fixture()
    config["contact_manifest"]["evaluators"] = evaluators

    def forbidden(*args, **kwargs):
        raise AssertionError("model construction must not precede evaluator validation")

    monkeypatch.setattr(FastArmRuntimePlugin, "build_simulator", forbidden)
    with pytest.raises(ValueError, match="evaluator"):
        capture_signal_contact(config, software_revision=REVISION)


@pytest.mark.parametrize("field,value", (
    ("is_stale", True), ("qpos_rejected", True), ("reason", "invented"),
    ("is_stale", 0), ("qpos_rejected", 0), ("unexpected", True),
))
def test_rehashed_runtime_diagnostics_must_match_recorded_state(captured, field, value):
    envelope = deepcopy(captured)
    envelope["payload"]["records"][1]["runtime_safety"][field] = value
    with pytest.raises(ValueError):
        readback(envelope)


@pytest.mark.parametrize("terminal", (
    {"kind": "execution_failure", "reason": "invented", "input_index": 500, "exception_type": "InventedError"},
    {"kind": "execution_failure", "reason": "invented", "input_index": 5, "exception_type": "InventedError"},
    {"kind": "task_terminal", "reason": "invented", "input_index": 4, "exception_type": None},
    {"kind": "task_terminal", "reason": None, "input_index": 4.0, "exception_type": None},
    {"kind": "task_terminal", "reason": None, "input_index": 4, "exception_type": "InventedError"},
    {"kind": "cleanup_failure", "reason": None, "input_index": 5, "exception_type": None},
    {"kind": "cleanup_failure", "reason": "cleanup", "input_index": True, "exception_type": "OSError"},
    {"kind": "response_failure", "reason": "simulated_router_observation_correlated", "input_index": 4, "exception_type": None},
))
def test_rehashed_termination_must_fit_execution(captured, terminal):
    envelope = deepcopy(captured)
    envelope["payload"]["termination"] = terminal
    with pytest.raises(ValueError):
        readback(envelope)


@pytest.mark.parametrize("version", (123, True, None, "", "3", "3.9", "3.9.0\n", "invalid"))
def test_environment_version_requires_a_version_string(captured, version):
    envelope = deepcopy(captured)
    envelope["payload"]["mujoco_version"] = version
    with pytest.raises(ValueError):
        readback(envelope)


def test_other_valid_environment_version_is_not_presented_as_verified(captured):
    """decoderの実行環境と異なることだけで、旧環境の記録を排除しない。"""
    envelope = deepcopy(captured)
    envelope["payload"]["mujoco_version"] = "3.0.0"
    assert readback(envelope)["mujoco_version"] == "3.0.0"


@pytest.mark.parametrize("source", ("gamepad", "selfrionette"))
@pytest.mark.parametrize("mode", ("nominal", "zero", "reverse", "malformed", "missing", "disconnect"))
def test_legitimate_success_and_failures_remain_readable(source, mode):
    config = fixture(source)
    if mode in ("zero", "reverse"):
        value = 0.0 if mode == "zero" else 1.0
        if source == "selfrionette":
            config["payloads"] = [",".join(line.split(",")[:2] + [str(value)] + line.split(",")[3:]) for line in config["payloads"]]
        else:
            payloads = [json.loads(p) for p in config["payloads"]]
            for payload in payloads:
                payload["gamepad"]["axes"][0] = value
            config["payloads"] = [json.dumps(p) for p in payloads]
    elif mode == "malformed":
        config["payloads"][2] = "vector,invalid" if source == "selfrionette" else "{broken"
    elif mode in ("missing", "disconnect"):
        config["response_mode"] = mode
    result = decode_signal_trace(capture_signal_contact(config, software_revision=REVISION), expected_revision=REVISION)
    assert result["metric"]["value"]["classification"] == ("success" if mode == "nominal" else "failure")


@pytest.mark.parametrize("kind", ("held", "rejected", "invalid_contact"))
def test_true_hold_reject_and_unavailable_contact_are_readable(monkeypatch, kind):
    """既存処理境界への故障注入で、正当な非成功記録を誤拒否しないことを確認する。"""
    from dataclasses import replace
    from xpotato_sim.motion import LocalEndpointMotionGenerator
    from xpotato_sim.schemas import JointCommand
    from xpotato_sim.runtime.contact.scene import ContactSceneInstance
    from xpotato_sim.runtime.contact.evidence import ContactEvidenceStatus

    if kind == "invalid_contact":
        original = ContactSceneInstance.measure_contact_evidence

        def unavailable(instance, **kwargs):
            evidence = original(instance, **kwargs)
            if kwargs.get("frame_index") == 2:
                return replace(evidence, status=ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
                               aggregate=None, contacts=(), reason="test-only unavailable contact")
            return evidence

        monkeypatch.setattr(ContactSceneInstance, "measure_contact_evidence", unavailable)
    else:
        original = LocalEndpointMotionGenerator.update_delta

        def replace_candidate(generator, intent, dt):
            command = original(generator, intent, dt)
            before = command.metadata["qpos_before_rad"]
            if kind == "held":
                return generator._build_holding_command(intent=intent, reason="test-only hold",
                    qpos_before_rad=tuple(before), endpoint_delta_requested_m=(0.0, 0.0, 0.0))
            candidate = (100.0, *before[1:])
            return replace(command, joint=JointCommand(joint_angles_rad=candidate),
                           metadata={**command.metadata, "candidate_qpos_rad": candidate})

        monkeypatch.setattr(LocalEndpointMotionGenerator, "update_delta", replace_candidate)
    result = decode_signal_trace(capture_signal_contact(fixture(), software_revision=REVISION), expected_revision=REVISION)
    assert result["metric"]["status"] == ("invalid" if kind == "invalid_contact" else "measured")
    if kind == "rejected":
        assert result["records"][0]["runtime_safety"]["qpos_rejected"] is True
        assert result["records"][0]["after_robot_qpos"] == result["records"][0]["before_robot_qpos"]


@pytest.mark.parametrize("fail_start", (False, True))
def test_cleanup_and_start_errors_keep_their_real_termination(monkeypatch, fail_start):
    from xpotato_sim.plugins.input_sources.selfrionette import SelfrionetteInputSource
    original_close = SelfrionetteInputSource.close

    def broken_close(reader):
        original_close(reader)
        raise OSError("test-only cleanup error")

    def broken_start(reader):
        raise OSError("test-only source start error")

    with monkeypatch.context() as patcher:
        patcher.setattr(SelfrionetteInputSource, "close", broken_close)
        if fail_start:
            patcher.setattr(SelfrionetteInputSource, "start", broken_start)
        raw = capture_signal_contact(fixture(), software_revision=REVISION)
    result = decode_signal_trace(raw, expected_revision=REVISION)
    assert result["termination"]["kind"] == ("execution_failure" if fail_start else "cleanup_failure")
    assert result["termination"]["reason"] == ("test-only source start error" if fail_start else "test-only cleanup error")


def test_budget_failure_cannot_be_relabelled_as_task_terminal():
    config = fixture()
    config["host_times_s"] = config["host_times_s"][:1]
    config["payloads"] = config["payloads"][:1]
    envelope = strict_json(capture_signal_contact(config, software_revision=REVISION))
    result = readback(deepcopy(envelope))
    assert result["termination"]["kind"] == "budget_exhausted"
    assert result["metric"]["value"]["classification"] == "failure"
    envelope["payload"]["termination"] = {"kind": "task_terminal", "reason": None, "input_index": 0, "exception_type": None}
    with pytest.raises(ValueError, match="Task termination"):
        readback(envelope)


def test_readback_uses_robot_preflight_without_simulation_step_or_network(monkeypatch):
    from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
    import socket
    raw = capture_signal_contact(fixture(), software_revision=REVISION)

    def forbidden(*args, **kwargs):
        raise AssertionError("readback must not step simulation or open a socket")

    monkeypatch.setattr(HeadlessMuJoCoSimulator, "step", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    assert decode_signal_trace(raw, expected_revision=REVISION)["metric"]["status"] == "measured"


@pytest.mark.parametrize("phase", ("start", "close"))
def test_empty_exception_message_remains_a_readable_failure(monkeypatch, phase):
    """例外の空messageを架空の原因で補わず、型名で理由を保持する。"""
    from xpotato_sim.plugins.input_sources.selfrionette import SelfrionetteInputSource
    original_close = SelfrionetteInputSource.close

    def empty_error(reader):
        if phase == "close":
            original_close(reader)
        raise OSError()

    with monkeypatch.context() as patcher:
        patcher.setattr(SelfrionetteInputSource, phase, empty_error)
        raw = capture_signal_contact(fixture(), software_revision=REVISION)
    result = decode_signal_trace(raw, expected_revision=REVISION)
    assert result["termination"]["exception_type"] == "OSError"
    assert result["termination"]["reason"] == "OSError"
