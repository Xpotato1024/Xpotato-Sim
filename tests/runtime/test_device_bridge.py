"""本物のbounded pipeとprocess寿命をsoftware fixtureだけで検証する。"""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from xpotato_sim.runtime.application.device_bridge import BridgeProcess, DeviceBridgeGroup, load_connection_config
from tests.runtime.test_device_trial import profile

FIXTURE = Path(__file__).parents[1] / "fixtures/selfrionette_device/scripted_bridge.py"


def process_type(executable, **kwargs):
    return BridgeProcess(executable, **kwargs, process_factory=lambda args, **options:
        subprocess.Popen([sys.executable, str(FIXTURE)], **options))


def group(roles=("left",), scenario="normal"):
    p = profile(roles)
    return DeviceBridgeGroup(p, {"executable": str(FIXTURE), "roles": {
        role: {"port": scenario, "profile_path": "explicit-software-fixture.srncal"} for role in roles}}, process_type=process_type)


def until(group, predicate, limit=2):
    end = time.monotonic() + limit
    samples = []
    while time.monotonic() < end:
        samples.extend(group.poll())
        if predicate(group.state(), samples):
            return samples
        time.sleep(.002)
    raise AssertionError(f"fixture did not reach expected state: {group.state()}")


def prepare(group):
    until(group, lambda s, _: all(r["clock_synchronized"] and not r["pending"] for r in s["roles"].values()))
    for action, phase in (("connect", "configured"), ("tare", "connected"),
            ("neutral", "awaiting_neutral"), ("apply", "neutral_ready")):
        for role in group.state()["roles"]:
            group.operate(role, action, True, phase)
        until(group, lambda s, _: all(r["pending"] is None for r in s["roles"].values()))
    assert group.state()["ready"]


@pytest.mark.parametrize("roles", [("left",), ("left", "right")])
def test_explicit_steps_same_samples_clock_and_process_lifetime(roles):
    bridge = group(roles)
    processes = tuple(bridge.processes.values())
    try:
        with pytest.raises(ValueError, match="tare"):
            bridge.start()
        prepare(bridge)
        bridge.start()
        samples = until(bridge, lambda _, samples: len(samples) == len(roles))
        for message, received_at in samples:
            raw = json.loads(message)
            assert raw["event"]["sequence"] == 81 and raw["event"]["device_ms"] == 2000
            assert raw["event"]["command"] == [.25, -.1, 0, 0, 0, 0, 0]
            assert raw["source_age_s"] >= .002
            assert received_at - raw["source_age_s"] < received_at
        assert bridge.poll() == ()
    finally:
        bridge.close()
    assert all(p._process.poll() is not None and not any(t.is_alive() for t in p._threads) for p in processes)
    assert not bridge.state()["ready"]


def test_initial_ipc_delay_is_not_hidden_by_receipt_clock():
    bridge = group(scenario="delayed_sample")
    try:
        prepare(bridge)
        bridge.start()
        samples = until(bridge, lambda _, values: bool(values))
        assert json.loads(samples[0][0])["source_age_s"] >= .3
    finally:
        bridge.close()


@pytest.mark.parametrize("scenario, reason", [("overflow", "overflow"), ("oversize", "64 KiB"),
    ("eof", "closed"), ("bad_revision", "revision")])
def test_invalid_bridge_is_bounded_and_invalidates_every_role(scenario, reason):
    bridge = group(scenario=scenario)
    try:
        with pytest.raises((RuntimeError, ValueError), match=reason):
            if scenario == "overflow":
                # configure応答でstatus送信を確認後、owner側の消費だけを停止する。
                process = bridge.processes["left"]
                until(bridge, lambda s, _: s["roles"]["left"]["pending"] == "status")
                deadline = time.monotonic() + 2
                while not process._failure and time.monotonic() < deadline:
                    time.sleep(.002)
            until(bridge, lambda s, _: False)
        assert bridge.state()["error"] and not bridge.state()["ready"]
        assert all(p._process.poll() is not None for p in bridge.processes.values())
    finally:
        bridge.close()


def test_stalled_child_stop_is_bounded_and_no_readiness_reuse():
    bridge = group(scenario="stall")
    prepare(bridge)
    bridge.start()
    until(bridge, lambda s, _: s["roles"]["left"]["phase"] == "streaming")
    started = time.monotonic()
    bridge.close()
    assert time.monotonic() - started < 1.5
    assert not bridge.state()["ready"]
    with pytest.raises(ValueError):
        bridge.start()


def test_operator_actions_reject_old_phase_unconfirmed_and_implicit_roles():
    bridge = group()
    try:
        until(bridge, lambda s, _: s["roles"]["left"]["clock_synchronized"])
        for role, action, confirmed, phase in (("right", "connect", True, "configured"),
                ("left", "connect", False, "configured"), ("left", "neutral", True, "configured"),
                ("left", "connect", True, "connected")):
            with pytest.raises(ValueError):
                bridge.operate(role, action, confirmed, phase)
        assert bridge.state()["roles"]["left"]["phase"] == "configured"
    finally:
        bridge.close()


def test_config_loading_does_not_launch_or_open_any_port(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("configuration may not launch a process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    calibration = tmp_path / "explicit.srncal"
    calibration.write_text("software fixture")
    raw = {"schema_version": "selfrionette-workbench-connection/v1", "executable": sys.executable,
        "roles": {"left": {"port": "explicit-test-port", "profile_path": str(calibration)}}}
    path = tmp_path / "connections.json"
    path.write_text(json.dumps(raw))
    assert load_connection_config(path) == raw
    raw["roles"]["right"] = raw["roles"]["left"]
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="both roles"):
        load_connection_config(path)
