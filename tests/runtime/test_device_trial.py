"""Device inputも共通ModelExecution/TrialRunnerで実行し、元sampleの鮮度を守る。"""
import json
from pathlib import Path
import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile, decode_launch_profile
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from tests.plugins.input_sources.selfrionette_device.test_source import parameters, wire
from tests.plugins.mappings.normalized_endpoint_velocity.test_mapping import mapping_parameters


def profile(roles=("left",)):
    base = load_launch_profile("fast-arm-single-gamepad" if len(roles)==1 else "fast-arm-bimanual-gamepad")
    raw = json.loads(base.document_json)
    raw["input"] = {"plugin": {"name": "selfrionette_device", "version": 1},
        "provider": "selfrionette-device/v1", "preset": None, "parameters": parameters(roles)}
    raw["mapping"] = {"plugin": {"name": "normalized_endpoint_velocity", "version": 1},
        "parameters": mapping_parameters(roles)}
    return decode_launch_profile(json.dumps(raw).encode(), source_path=base.source_path)


@pytest.mark.parametrize("roles", [("left",), ("left", "right")])
def test_device_runs_in_one_world_without_reemitting_samples(tmp_path, roles):
    now = [10.]
    runner = TrialRunner(result_root=tmp_path, software_revision="software-fixture", clock=lambda: now[0])
    try:
        ticket = runner.prepare(profile(roles), TrialLimits(3))
        assert runner.condition.to_document()["input"]["provider"] == "selfrionette-device/v1"
        runner.start(ticket, input_provenance={"source": "explicit-device-fixture"})
        now[0] += .01
        for side in roles:
            runner.ingest(ticket, wire(side), received_at_s=now[0])
        runner.advance(ticket)
        assert runner.status == "running" and runner.tick_count == 0
        now[0] += .01
        for side in roles:
            runner.ingest(ticket, wire(side, sequence=82, value=.1), received_at_s=now[0])
        for i in range(3):
            now[0] += 1/60
            runner.advance(ticket)
        assert runner.status == "terminal" and runner.tick_count == 3
        assert runner.processed_input_sequence == 82
        assert runner.model_build_count == 1
        sample = runner.snapshot().metadata["selfrionette_device_input_v1"]
        assert all(device["sequence"] == 82 for device in sample["devices"].values())
    finally:
        runner.close()


@pytest.mark.parametrize("name", ["fast-arm-single-device", "fast-arm-bimanual-device"])
def test_registered_device_fixture_uses_common_finite_driver(tmp_path, name):
    from xpotato_sim.runtime.experiment.trial_fixture import load_trial_fixture
    from xpotato_sim.runtime.runners.finite_trial import run_finite_trial
    now = [10.]
    fixture = load_trial_fixture(Path(__file__).parents[1] / "fixtures/selfrionette_device" / f"{name}.json")
    result = run_finite_trial(load_launch_profile(name), fixture=fixture, limits=TrialLimits(12),
        result_root=tmp_path, software_revision="explicit-software-fixture", clock=lambda: now[0],
        sleeper=lambda delta: now.__setitem__(0, now[0]+delta)).to_document()
    assert result["ticks"] == 12 and result["runner_stop_reason"] == "simulation_budget"
    assert fixture.identity()["schema_version"] == "trial-input-fixture/v1"
    with pytest.raises(ValueError, match="fixture input binding"):
        fixture.assert_profile(load_launch_profile("fast-arm-single-gamepad"))


def test_device_identity_and_mapping_are_explicit_editable_frozen_conditions():
    from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition
    document = preset_condition("fast-arm-single-device")
    document["configuration"]["input"]["parameters"]["bindings"]["left"]["operator"]["posture"] = "explicit-posture"
    document["configuration"]["mapping"]["parameters"]["channel_axis_weights"]["left"][0] = [0., 1., 0.]
    p, limits, normalized = resolve_condition(document)
    assert p.input_parameters["bindings"]["left"]["operator"]["posture"] == "explicit-posture"
    assert normalized["configuration"]["mapping"]["parameters"]["channel_axis_weights"]["left"][0] == [0., 1., 0.]
    document["configuration"]["input"]["parameters"]["bindings"]["left"]["domain"] = "force_n"
    with pytest.raises(ValueError):
        resolve_condition(document)


def test_stale_other_device_trips_whole_world_and_retry_requires_new_session(tmp_path):
    now = [10.]
    runner = TrialRunner(result_root=tmp_path, software_revision="software-fixture", clock=lambda: now[0])
    try:
        ticket = runner.prepare(profile(("left", "right")), TrialLimits(100))
        runner.start(ticket, input_provenance={"source": "explicit-device-fixture"})
        now[0] += .01
        runner.ingest(ticket, wire(), received_at_s=now[0])
        runner.advance(ticket)
        assert runner.status == "waiting_input"
        runner.ingest(ticket, wire("right"), received_at_s=now[0])
        runner.advance(ticket)
        now[0] += .21
        runner.ingest(ticket, wire(sequence=82), received_at_s=now[0])
        runner.advance(ticket)
        assert runner.status == "terminal" and runner.result.to_document()["runner_stop_reason"] == "technical_invalid"
        ticket = runner.retry()
        runner.start(ticket, input_provenance={"source": "explicit-device-fixture"})
        now[0] += .01
        with pytest.raises(ValueError, match="previous execution"):
            runner.ingest(ticket, wire(sequence=83), received_at_s=now[0])
    finally:
        runner.close()


def test_burst_ceil_millisecond_age_keeps_original_microsecond_order(tmp_path):
    now = [10.]
    runner = TrialRunner(result_root=tmp_path, software_revision="software-fixture", clock=lambda: now[0])
    try:
        ticket = runner.prepare(profile(), TrialLimits(3))
        runner.start(ticket, input_provenance={"source": "clock-rounding-fixture"})
        now[0] += .02
        for sequence, received_us, emitted_us, age_ms, age in (
                (81, 1000000, 1000999, 1, .001001),
                (82, 1000010, 1001011, 2, .002001)):
            raw = json.loads(wire(sequence=sequence, age=age))
            raw["event"].update(host_received_us=received_us, host_emitted_us=emitted_us, sample_age_ms=age_ms)
            runner.ingest(ticket, json.dumps(raw), received_at_s=now[0])
        assert runner.processed_input_sequence == 82
        assert runner.processed_input_diagnostics["streams"]["left"]["ordering_receipt_s"] == 1.00001
        assert runner.processed_input_diagnostics["processed_receipt_s"] == pytest.approx(now[0] - .002001)
        runner.advance(ticket)
        assert runner.status == "running"
        now[0] += .21
        runner.advance(ticket)
        assert runner.status == "terminal"
        terminal = runner.result.to_document()
        assert terminal["runner_stop_reason"] == "technical_invalid"
        assert "input_stale" in terminal["error"]
    finally:
        runner.close()


def test_fixture_size_bound_is_one_mib_while_scene_bound_is_unchanged(tmp_path):
    from xpotato_sim.runtime.experiment.trial_fixture import load_trial_fixture, MAX_FIXTURE_BYTES
    from xpotato_sim.runtime.scene.objects import strict_json
    source = Path(__file__).parents[1] / "fixtures/selfrionette_device/fast-arm-single-device.json"
    raw = source.read_bytes()
    padded = raw + b" " * (300000 - len(raw))
    path = tmp_path / "large-explicit-fixture.json"
    path.write_bytes(padded)
    assert load_trial_fixture(path).byte_count == 300000
    with pytest.raises(ValueError, match="bounded"):
        strict_json(padded)
    path.write_bytes(raw + b" " * (MAX_FIXTURE_BYTES + 1 - len(raw)))
    with pytest.raises(ValueError, match="1 MiB"):
        load_trial_fixture(path)
