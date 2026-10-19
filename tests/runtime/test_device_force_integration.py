"""Deviceの元入力と接触力提示を同じnative worldで比較する。実機は使用しない。"""
from copy import deepcopy
from hashlib import sha256
import json
from math import hypot

import pytest

from tests.plugins.input_sources.selfrionette_device.test_source import event
from tests.runtime.test_trial_runner import Clock
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.experiment.edited_condition import descriptors, preset_condition, resolve_condition
from xpotato_sim.runtime.experiment.trial_condition import semantic_parameters
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.scene.force_presentation import profile_presentation
from xpotato_sim.runtime.scene.objects import canonical


TICKS = 180
PROFILES = ("dynamic-fixed-contact-single-device", "dynamic-fixed-contact-bimanual-device")


def selected(name, mode="no_force_cue"):
    """既存editorで公開する位置編集だけで低gain接触を作る。モデル・閾値は変えない。"""
    document = preset_condition(name)
    document["display_comparison"]["mode"] = mode
    document["limits"]["max_ticks"] = TICKS
    # single_originalとbimanualは異なるhome位置。初期貫通なしの明示software配置を使う。
    position = ([.315, -.246, .284] if name.endswith("single-device") else [.315, .56, .46])
    document["environment"]["parameters"]["scene"]["objects"][0]["pose"]["position_m"] = position
    return resolve_condition(document)


def device_message(role, tick, *, session=2147):
    """各roleの元identityを保持した新規sample。0.02 m/sの既定gainへ小さい入力を渡す。"""
    value = 0. if tick == 0 else .5 if role == "left" else .25
    sample = event(role, sequence=81 + tick, session=session, value=value)
    receipt_us = 1000000 + round(tick * 1000000 / 60)
    sample.update(device_ms=2000 + round(tick * 1000 / 60),
        host_received_us=receipt_us, host_emitted_us=receipt_us + 1500)
    return {"schema_version": "selfrionette-device-input/v1", "role": role,
        "source_age_s": .002, "event": sample}


def ingest(runner, ticket, clock, roles, tick):
    originals = {}
    for index, role in enumerate(roles):
        raw = device_message(role, tick, session=2147 + index)
        originals[role] = raw["event"]
        runner.ingest(ticket, json.dumps(raw), received_at_s=clock())
    return originals


def run_contact(tmp_path, name, mode):
    profile, limits, _ = selected(name, mode)
    roles = tuple(profile.side_to_endpoint)
    clock = Clock()
    runner = TrialRunner(result_root=tmp_path, software_revision="device-force-software-fixture", clock=clock)
    trace, active_ticks = [], []
    try:
        ticket = runner.prepare(profile, limits)
        condition = runner.condition.to_document()
        runner.start(ticket, input_provenance={"source": "explicit-device-force-software-fixture",
            "hardware_acceptance": False})
        clock.sleep(.02)
        ingest(runner, ticket, clock, roles, 0)
        runner.advance(ticket)
        assert runner.status == "running" and runner.tick_count == 0
        for tick in range(1, TICKS + 1):
            clock.sleep(profile.dt_s)
            originals = ingest(runner, ticket, clock, roles, tick)
            runner.advance(ticket)
            assert runner.tick_count == tick, runner.error
            sample = runner.take_committed_projection()
            if tick == TICKS:
                # 正式終端は直前projectionを破棄し、同じworldの終了状態を再観測する。
                assert runner.status == "terminal" and sample is None
                sample = runner.snapshot()
            assert sample is not None, (tick, runner.status, runner.error)
            metadata = sample.metadata
            source = deepcopy(metadata["selfrionette_device_input_v1"])
            raw_dynamics = deepcopy(metadata["scene_dynamics_v1"])
            force = deepcopy(metadata["scene_force_signal_v1"])
            task = deepcopy(metadata["scene_contact_task_v1"])
            presentation = metadata["force_presentation_v1"]

            assert set(source["devices"]) == set(roles)
            for role, original in originals.items():
                actual = source["devices"][role]
                assert {key: actual[key] for key in original} == original
                assert actual["received_at_s"] == pytest.approx(clock() - .002)
            assert presentation["condition"]["mode"] == mode
            assert presentation["epoch"] == force["epoch"] == task["epoch"] == ticket.epoch
            assert metadata["coordinated_runtime_v1"]["tick"] == force["control_tick"] == tick
            assert raw_dynamics["simulation_time_s"] == force["simulation_time_s"] == sample.time_s
            assert task["presentation_frame_index"] == raw_dynamics["frame_index"]
            assert task["presentation_time_s"] == sample.time_s
            assert (presentation["model_sha256"], presentation["scene_digest"], presentation["settings_digest"]) == (
                force["model_sha256"], force["scene_digest"], force["settings_digest"])
            assert {(pair["endpoint_id"], pair["object_id"]) for pair in force["pairs"]} == {
                (endpoint, "cube") for endpoint in profile.side_to_endpoint.values()}

            if tick < TICKS:
                assert force["status"] == "available"
                assert raw_dynamics["frame_index"] == force["source_frame_index"] == tick
                if any(pair["status"] == "active" and hypot(*pair["raw_force_world_n"]) > .05
                        for pair in force["pairs"]):
                    active_ticks.append(tick)
                    assert any(contact["status"] == "measured" and
                        (contact["role1"]["kind"] == "tool" or contact["role2"]["kind"] == "tool")
                        for contact in raw_dynamics["contacts"])
                # 描画用の再snapshotは元sampleの観測時刻とidentityを更新しない。
                assert runner.snapshot().metadata["scene_force_signal_v1"] == force
            else:
                assert force["status"] == "unavailable"
                assert force["source_frame_index"] == tick
            force.pop("epoch")
            task.pop("epoch")
            trace.append((sample.qpos, sample.qvel, sample.time_s, source, raw_dynamics, force, task))
        assert active_ticks, "Deviceの低gain入力で実native接触が一度も発生しなかった"
        assert runner.status == "terminal"
        assert runner.result.to_document()["runner_stop_reason"] == "simulation_budget"
        assert runner.model_build_count == 1
        return condition, trace, active_ticks
    finally:
        runner.close()


@pytest.mark.parametrize("name", PROFILES)
def test_device_modes_share_inputs_native_trajectory_raw_force_and_task(tmp_path, name):
    """全180tickを比較し、no cueでもraw force取得とTaskを止めない。"""
    conditions, traces, contact_ticks = [], [], []
    for mode in ("no_force_cue", "force_cue"):
        condition, trace, active = run_contact(tmp_path / mode, name, mode)
        assert condition["display_comparison"]["presentation"]["mode"] == mode
        condition.pop("display_comparison")
        conditions.append(condition)
        traces.append(trace)
        contact_ticks.append(active)
    assert conditions[0] == conditions[1]
    assert traces[0] == traces[1]
    assert contact_ticks[0] == contact_ticks[1]


@pytest.mark.parametrize("name", PROFILES)
def test_device_display_change_is_editable_and_reuses_native_identity(tmp_path, name):
    """表示だけの編集は新trial条件を作り、Device binding・model・scene・設定を保持する。"""
    preset = load_launch_profile(name)
    assert json.loads(preset.document_json)["schema_version"] == "xpotato-sim-launch-profile/v4"
    assert preset.scene_plan.dynamics is not None
    a, limits, document = selected(name)
    b, _, _ = selected(name, "force_cue")
    field = next(item for item in descriptors(document) if item["path"] == ["display_comparison", "mode"])
    assert field["available"] and field["choices"] == ["no_force_cue", "force_cue"]
    assert profile_presentation(a)["mode"] == "no_force_cue"
    assert resolve_condition(canonical(document))[2] == document
    physical_a, physical_b = semantic_parameters(a, limits), semantic_parameters(b, limits)
    assert physical_a.pop("display_comparison") != physical_b.pop("display_comparison")
    assert physical_a == physical_b

    runner = TrialRunner(result_root=tmp_path, software_revision="device-force-software-fixture", clock=Clock())
    try:
        first = runner.prepare(a, limits)
        instance = runner._execution.instance
        initial = instance.provider.trial_state()
        first_source = runner._execution.runtime.source
        second = runner.prepare(b, limits)
        assert first.condition_sha256 != second.condition_sha256 and first.epoch != second.epoch
        assert runner._execution.instance is instance and runner.model_build_count == 1
        assert runner._execution.instance.provider.trial_state() == initial
        assert runner._execution.runtime.source is not first_source
        assert runner._execution.runtime.source.parameters == a.input_parameters == b.input_parameters
        assert runner._execution.runtime.source.last_received_at_s is None
        snapshot = runner.snapshot()
        binding = snapshot.metadata["force_presentation_v1"]
        assert binding["condition"]["mode"] == "force_cue" and binding["epoch"] == second.epoch
        assert binding["condition_sha256"] == sha256(canonical(binding["condition"])).hexdigest()
        assert binding["model_sha256"] == instance.viewer.metadata["model_sha256"]
        assert binding["scene_digest"] == a.scene_plan.manifest.digest == b.scene_plan.manifest.digest
        assert binding["settings_digest"] == a.scene_plan.dynamics.digest == b.scene_plan.dynamics.digest
        assert runner.status == "ready" and runner.tick_count == 0
        assert not list(tmp_path.iterdir())
    finally:
        runner.close()
