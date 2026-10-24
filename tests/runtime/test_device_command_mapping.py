"""Device binding・二つの入力時計・前commit原力をcommand Mappingまで保持する。"""
from copy import deepcopy

import pytest

from tests.runtime.test_contact_resistance import condition
from tests.runtime.test_device_force_integration import PROFILES, ingest, selected
from tests.runtime.test_trial_runner import Clock
from xpotato_sim.runtime.experiment.edited_condition import resolve_condition
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner


def selected_command(name, mode="contact_dependent", display="no_force_cue"):
    profile, _, document = selected(name, display)
    command = condition(mode)
    command["targets"] = {endpoint: command["targets"][role]
        for role, endpoint in profile.side_to_endpoint.items()}
    document["command_mapping"] = command
    document["limits"]["max_ticks"] = 240
    return resolve_condition(document)


@pytest.mark.parametrize("name", PROFILES)
def test_device_command_conditions_preserve_bindings_and_reuse_native_model(tmp_path, name):
    runner = TrialRunner(result_root=tmp_path, software_revision="device-command-fixture", clock=Clock())
    instance = previous_source = previous_ticket = None
    conditions = []
    try:
        for mode, display in (("baseline", "no_force_cue"), ("fixed_gain", "no_force_cue"),
                ("contact_dependent", "no_force_cue"), ("contact_dependent", "force_cue")):
            profile, limits, _ = selected_command(name, mode, display)
            ticket = runner.prepare(profile, limits)
            execution = runner._execution
            saved = runner.condition.to_document()
            assert saved["input"]["parameters"] == execution.runtime.source.parameters == profile.input_parameters
            assert saved["side_to_endpoint"] == profile.side_to_endpoint
            assert saved["command_mapping"]["mode"] == mode
            assert saved["display_comparison"]["presentation"]["mode"] == display
            assert execution.runtime.source.last_received_at_s is None
            assert execution._command_signal is execution._command_sample is execution.command_mapping.latest is None
            if instance is not None:
                assert execution.instance is instance and execution.runtime.source is not previous_source
                assert ticket.epoch != previous_ticket.epoch and ticket.condition_sha256 != previous_ticket.condition_sha256
            instance, previous_source, previous_ticket = execution.instance, execution.runtime.source, ticket
            saved["command_mapping"].pop("mode")
            saved.pop("display_comparison")
            conditions.append(saved)
        assert runner.model_build_count == 1 and conditions.count(conditions[0]) == len(conditions)
    finally:
        runner.close()


@pytest.mark.parametrize("name", PROFILES)
def test_device_contact_maps_previous_commit_and_preserves_original_event_clocks(tmp_path, name):
    profile, limits, _ = selected_command(name)
    roles = tuple(profile.side_to_endpoint)
    clock = Clock()
    runner = TrialRunner(result_root=tmp_path, software_revision="device-command-fixture", clock=clock)
    first_contact = first_reduction = None
    try:
        ticket = runner.prepare(profile, limits)
        runner.start(ticket, input_provenance={"source": "explicit-device-command-software-fixture"})
        clock.sleep(.02)
        ingest(runner, ticket, clock, roles, 0)
        runner.advance(ticket)
        execution = runner._execution
        assert runner.status == "running" and runner.tick_count == 0
        for tick in range(1, 181):
            previous = execution._command_signal
            clock.sleep(profile.dt_s)
            originals = ingest(runner, ticket, clock, roles, tick)
            runner.advance(ticket)
            assert runner.status == "running" and runner.tick_count == tick, runner.error
            sample = runner.take_committed_projection()
            assert sample is not None
            trace = sample.metadata["command_mapping_v1"]
            devices = sample.metadata["selfrionette_device_input_v1"]
            assert trace["command_tick"] == tick and trace["source_tick"] == previous.control_tick == tick-1
            assert trace["source_simulation_time_s"] == previous.simulation_time_s
            assert trace["input_ref"]["source_sequence"] == devices["update_sequence"]
            assert trace["input_ref"]["received_at_s"] == pytest.approx(clock()-.002)
            assert trace["input_ref"]["ordering_receipt_s"] == min(v["host_received_us"] for v in originals.values())/1e6
            for role, original in originals.items():
                assert {key: devices["devices"][role][key] for key in original} == original
            for item in trace["endpoints"]:
                pair = next(p for p in previous.pairs if (p.endpoint_id, p.object_id) == (item["endpoint_id"], item["object_id"]))
                assert item["raw_force_world_n"] == pair.raw_force_world_n
            signal, before_trace = execution._command_signal, deepcopy(trace)
            runner.snapshot()
            assert execution._command_signal is signal and execution.command_mapping.latest == before_trace
            if first_contact is None and any(p.status == "active" for p in signal.pairs):
                first_contact = tick
            if any(item["gain"] < 1 for item in trace["endpoints"]):
                first_reduction = tick
                break
        assert first_contact is not None and first_reduction == first_contact+1
        runner.abort(ticket)
        assert execution._command_sample is execution._command_signal is None
        assert execution.command_mapping.latest["status"] == "invalidated"
        retry = runner.prepare(profile, limits)
        assert retry.epoch != ticket.epoch and runner.model_build_count == 1
        assert runner._execution.runtime.source.last_received_at_s is None
    finally:
        runner.close()
