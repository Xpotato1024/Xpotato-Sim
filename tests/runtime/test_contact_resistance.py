"""接近成分、原力の因果順序、same-input/STOP/resetの閉じたsoftware比較。"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from xpotato_sim.plugins.mappings._contact_resistance import approach_velocity, resistance_gain
from xpotato_sim.runtime.scene.command_mapping import ContactResistanceCommandMapping, validate_command_mapping
from xpotato_sim.runtime.scene.force_signal import default_force_config
from xpotato_sim.runtime.scene.objects import canonical
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.execution.model_execution import ModelExecution
from xpotato_sim.schemas.coordinated import CoordinatedInput, CoordinatedSnapshot, EndpointVelocity
from tests.runtime.test_scene_force_signal import processor, apply, contact
from tests.runtime.test_trial_runner import Clock, start, message


def condition(mode="contact_dependent"):
    return {"schema_version": "approach-resistance/v1", "mode": mode, "frame": "mujoco_world",
        "targets": {"left": {"object_id": "cube", "approach_normal_world": [1., 0., 0.]},
            "right": {"object_id": "cube", "approach_normal_world": [-1., 0., 0.]}},
        "fixed_gain": .5, "half_gain_load_n": 10., "minimum_gain": .1,
        "normal_match_cos_min": .99, "max_sample_age_s": .2}


def mapping(mode="contact_dependent"):
    return ContactResistanceCommandMapping(condition(mode), epoch="trial-one", model_sha256="1"*64,
        scene_digest="2"*64, settings_digest="3"*64)


def mapped(m, contacts, *, velocity=(.06, .02, .03), **changes):
    commands = (EndpointVelocity("left", velocity, "world"), EndpointVelocity("right", (0., 0., 0.), "world"))
    signal = apply(processor(config=replace(default_force_config(.2), magnitude_clamp_n=1.)), contacts)
    before = CoordinatedSnapshot("1"*64, 1, .01, (), (), (), "test")
    source_input = CoordinatedInput(commands,"source",1,.001,.01,True,False,"a"*64)
    args = dict(source_input=source_input, signal=signal, contacts=contacts, epoch="trial-one", before=before, tick=1, now_s=.02, dt_s=.01)
    args.update(changes)
    return m.apply(commands, **args)


@pytest.mark.parametrize("normal,velocity,expected", [
    ((1,0,0), (.06,.02,.03), (.03,.02,.03)),
    ((1,0,0), (-.06,.02,.03), (-.06,.02,.03)),
    ((-1,0,0), (-.06,.02,.03), (-.03,.02,.03)),
    ((1,0,0), (0,.02,.03), (0,.02,.03)),
    ((1,0,0), (0,0,0), (0,0,0)),
])
def test_only_approach_changes_without_autonomous_motion(normal, velocity, expected):
    assert approach_velocity(velocity, normal, gain=.5) == pytest.approx(expected)


def test_gain_is_nonincreasing_with_explicit_saturation_and_raw_force():
    gains = [resistance_gain(v, half_gain_load_n=10, minimum_gain=.1) for v in (0, 5, 10, 90, 1000, 1e300)]
    assert gains == sorted(gains, reverse=True) and gains[0] == 1 and gains[-1] == .1
    for reverse in (False, True):
        m = mapping()
        out = mapped(m, [contact(native=(10.,0.,0.), reverse=reverse)])
        assert out[0].velocity_m_s == pytest.approx((.03,.02,.03))
        trace = m.latest["endpoints"][0]
        assert trace["raw_force_world_n"] == (-10.,0.,0.) and trace["compressive_load_n"] == 10
        assert trace["gain"] == .5  # #629の表示向け1N clampを使っていない。
        assert m.latest["source_tick"] == 1 and m.latest["command_tick"] == 2


def test_baseline_fixed_and_no_contact_are_separate():
    assert mapped(mapping("baseline"), [contact(native=(100.,0.,0.))])[0].velocity_m_s == (.06,.02,.03)
    assert mapped(mapping("fixed_gain"), [])[0].velocity_m_s == (.03,.02,.03)
    assert mapped(mapping(), [])[0].velocity_m_s == (.06,.02,.03)


@pytest.mark.parametrize("ordering", [None, 12.345])
def test_trace_keeps_optional_producer_ordering_separate_from_freshness(ordering):
    commands = (EndpointVelocity("left", (.06, .02, .03), "world"), EndpointVelocity("right", (0., 0., 0.), "world"))
    source = CoordinatedInput(commands, "source", 1, .001, .01, True, False, "a"*64,
        ordering_receipt_s=ordering)
    owner = mapping()
    mapped(owner, [], source_input=source)
    expected = {"source_epoch": "source", "source_sequence": 1, "source_timestamp_s": .001,
        "received_at_s": .01, "source_payload_sha256": "a"*64}
    if ordering is not None:
        expected["ordering_receipt_s"] = ordering
    assert owner.latest["input_ref"] == expected


@pytest.mark.parametrize("contacts,changes", [
    ([contact(status="measurement_unavailable")], {}),
    ([contact(), contact(status="measurement_unavailable")], {}),
    ([contact(native=(0.,10.,0.), normal=(0.,1.,0.))], {}),
    ([contact(), contact(native=(-10.,0.,0.), normal=(-1.,0.,0.))], {}),
    ([contact()], {"now_s": .211}), ([contact()], {"now_s": 0}),
    ([contact()], {"tick": 2}), ([contact()], {"signal": None}),
])
def test_unknown_force_or_multiple_faces_never_continue_push(contacts, changes):
    with pytest.raises(ValueError, match="force_or_target_face_unavailable"):
        mapped(mapping(), contacts, **changes)


@pytest.mark.parametrize("field,value", [("fixed_gain", 1.1), ("minimum_gain", -.1),
    ("half_gain_load_n", 0), ("max_sample_age_s", .201), ("normal_match_cos_min", .5),
    ("fixed_gain", True), ("mode", "adaptive"), ("frame", "tool")])
def test_invalid_condition_rejected(field, value):
    cfg = condition(); cfg[field] = value
    with pytest.raises(ValueError):
        validate_command_mapping(cfg)


def selected(mode):
    document = preset_condition("tasks/dynamic-fixed-contact")
    document["command_mapping"] = None if mode is None else condition(mode)
    document["limits"]["max_ticks"] = 30
    return resolve_condition(document)


def test_condition_freezes_independently_and_rejects_target_or_normal_change(tmp_path):
    p, limits, document = selected("contact_dependent")
    assert resolve_condition(canonical(document))[2] == document
    conditions = []
    runner = TrialRunner(result_root=tmp_path, software_revision="test", clock=Clock())
    for mode in ("baseline", "fixed_gain", "contact_dependent"):
        profile, limits, _ = selected(mode)
        ticket = runner.prepare(profile, limits)
        assert runner.model_build_count == 1
        saved = runner.condition.to_document()
        assert saved["command_mapping"]["mode"] == mode
        conditions.append(saved)
    runner.close()
    for value in conditions:
        value["command_mapping"].pop("mode")
    assert conditions[0] == conditions[1] == conditions[2]
    for target in ({"object_id":"absent","approach_normal_world":[1,0,0]},
                   {"object_id":"cube","approach_normal_world":[2,0,0]}):
        bad = deepcopy(document);bad["command_mapping"]["targets"]["left"] = target
        with pytest.raises(ValueError):
            resolve_condition(bad)
    dynamic = preset_condition("tasks/dynamic-cube-drop");dynamic["command_mapping"] = condition()
    with pytest.raises(ValueError, match="fixed Task target"):
        resolve_condition(dynamic)


def test_native_baseline_preserves_same_input_and_viewer_cannot_update_control(tmp_path, monkeypatch):
    results = []
    for mode in (None, "baseline", "contact_dependent"):
        profile, limits, _ = selected(mode)
        clock = Clock()
        runner = TrialRunner(result_root=tmp_path/str(mode), software_revision="test", clock=clock)
        ticket = runner.prepare(profile, limits)
        start(runner, ticket, clock)
        execution = runner._execution
        calls = []
        original = execution.instance.provider.sample
        def observe(**kwargs):
            calls.append(kwargs["frame_index"])
            return original(**kwargs)
        monkeypatch.setattr(execution.instance.provider, "sample", observe)
        trace = []
        # 同じsource sampleを複数tick使っても、力に応じたcommandをinput変更と誤認しない。
        for i in range(6):
            runner.ingest(ticket, message(i+1,(0,-.3,0,0)), received_at_s=clock())
            for _ in range(2):
                runner.advance(ticket)
                sample = runner.snapshot()
                signal = execution._command_signal
                count = len(calls)
                for _ in range(3):
                    runner.snapshot()
                if mode is not None:
                    assert len(calls) == count and execution._command_signal is signal
                    command = sample.metadata["command_mapping_v1"]
                    assert command["command_tick"] == runner.tick_count
                    assert command["source_tick"] == runner.tick_count-1
                trace.append((sample.qpos, sample.qvel, sample.time_s))
        results.append(trace)
        if mode is not None:
            original_signal = execution._command_signal
            clock.now += .201
            runner.snapshot()
            assert execution._command_signal is original_signal  # 表示側の失効も制御時計を更新しない。
        runner.abort(ticket)
        assert execution._command_sample is execution._command_signal is None
        retry = runner.prepare(profile, limits)
        assert retry.epoch != ticket.epoch
        assert (runner._execution.command_mapping is None) == (mode is None)
        assert runner._execution._command_signal is None
        runner.close()
    assert results[0] == results[1] == results[2]


def test_force_jump_changes_only_next_command_and_stop_reset_invalidates():
    m = mapping()
    for dt in (.001,.02,.1):
        assert mapped(m,[contact(native=(10.,0.,0.))],dt_s=dt)[0].velocity_m_s == pytest.approx((.03,.02,.03))
    assert mapped(m,[contact(native=(1000.,0.,0.))])[0].velocity_m_s == pytest.approx((.006,.02,.03))
    assert mapped(m,[])[0].velocity_m_s == (.06,.02,.03)
    assert mapped(m,[contact(native=(1000.,0.,0.))],velocity=(0.,0.,0.))[0].velocity_m_s == (0.,0.,0.)
    m.invalidate("stop")
    assert m.latest["status"] == "invalidated"


def test_real_native_contact_is_used_by_the_next_command_and_reset_clears_it():
    profile, _, _ = selected("contact_dependent")
    clock = Clock(); execution = ModelExecution(profile, clock=clock)
    execution.ingest(message()); execution.tick()
    first_contact = first_reduction = None
    for tick in range(1, 81):
        previous = execution._command_signal
        clock.now += profile.dt_s
        execution.ingest(message(tick,(0,-.64,0,0))); execution.tick()
        assert execution.state == "running"
        trace = execution.command_mapping.latest
        assert trace["source_tick"] == tick-1 and trace["command_tick"] == tick
        assert trace["input_ref"]["source_sequence"] == tick
        assert trace["endpoints"][0]["raw_force_world_n"] == previous.pairs[0].raw_force_world_n
        if first_contact is None and execution._command_signal.pairs[0].status == "active":
            first_contact = tick
            sample = execution.sample(tick)
            native = deepcopy(execution._command_sample.dynamics)
            sample.metadata["scene_dynamics_v1"]["contacts"][0]["normal_geom1_to_geom2_world"] = (0,0,1)
            assert execution._command_sample.dynamics == native
        if first_reduction is None and trace["endpoints"][0]["gain"] < 1:
            first_reduction = tick
    assert first_contact is not None and first_reduction == first_contact+1
    execution.stop(); before = execution.instance.provider.snapshot()
    execution.ingest(message(81,(0,-.64,0,0))); execution.tick()
    assert execution.instance.provider.snapshot() == before
    execution.reset("new-epoch")
    assert execution.command_mapping.latest is execution._command_signal is execution._command_sample is None


def test_missing_control_evidence_faults_before_native_prepare(monkeypatch):
    profile, _, _ = selected("contact_dependent")
    clock = Clock(); execution = ModelExecution(profile, clock=clock)
    execution.ingest(message()); execution.tick()
    original = execution.instance.provider.snapshot()
    execution._command_signal = replace(execution._command_signal, status="missing")
    prepared = []
    monkeypatch.setattr(execution.instance.provider,"prepare",lambda *args: prepared.append(args))
    clock.now += profile.dt_s
    execution.ingest(message(1,(0,-.64,0,0))); execution.tick()
    assert execution.state == "faulted" and "force_or_target_face_unavailable" in execution.reason
    assert execution.tick_count == 0 and not prepared
    assert execution.instance.provider.snapshot() == original


@pytest.mark.parametrize("terminal", ["natural", "budget"])
def test_task_terminal_frame_invalidates_command_trace_without_resampling_or_steps(monkeypatch, terminal):
    profile, _, document = selected("contact_dependent")
    if terminal == "natural":
        document["task"]["parameters"]["duration_s"] = profile.dt_s/2
        profile, _, _ = resolve_condition(document)
    clock = Clock()
    execution = ModelExecution(profile, clock=clock)
    try:
        execution.ingest(message()); execution.tick()
        assert "command_mapping_v1" not in execution.sample(0, advance_task=True).metadata
        clock.now += profile.dt_s
        execution.ingest(message(1, (0, -.3, 0, 0))); execution.tick()
        previous_frame = execution.sample(1)
        issued_trace = execution.command_mapping.latest
        original_trace = deepcopy(issued_trace)
        before = execution.instance.provider.snapshot()
        assert issued_trace["status"] == "mapped" and execution.tick_count == 1

        def unexpected(*args, **kwargs):
            pytest.fail("terminal projection must reuse the committed sample without preparing or stepping")

        for method in ("sample", "prepare", "commit"):
            monkeypatch.setattr(execution.instance.provider, method, unexpected)
        frame = execution.sample(2, advance_task=True, budget_exhausted=terminal == "budget")
        trace = frame.metadata["command_mapping_v1"]
        assert execution.state == frame.metadata["motion_status"] == "stopped"
        assert trace == execution.command_mapping.latest
        assert trace["status"] == "invalidated" and trace["reason"] == execution.reason
        assert trace["command_tick"] == 1 and trace["source_tick"] == 0
        task = frame.metadata["scene_contact_task_v1"]
        assert task["classification"] == ("success" if terminal == "natural" else "failure")
        assert task["reason"] == ("observation window completed; not contact success"
            if terminal == "natural" else "execution_budget_exhausted")
        assert frame.metadata["scene_force_signal_v1"]["status"] == "unavailable"
        assert execution._command_sample is execution._command_signal is None
        assert (frame.qpos, frame.qvel, frame.time_s) == (previous_frame.qpos, previous_frame.qvel, previous_frame.time_s)
        assert execution.instance.provider.snapshot() == before and execution.tick() == 1

        assert previous_frame.metadata["command_mapping_v1"] == issued_trace == original_trace
        assert trace is not execution.command_mapping.latest
        trace["endpoints"][0]["processed_velocity_world_m_s"] = (999., 0., 0.)
        trace["input_ref"]["source_sequence"] = -1
        assert previous_frame.metadata["command_mapping_v1"] == issued_trace == original_trace
        assert execution.command_mapping.latest["input_ref"] == original_trace["input_ref"]
        assert execution.command_mapping.latest["endpoints"] == original_trace["endpoints"]
    finally:
        execution.stop()
