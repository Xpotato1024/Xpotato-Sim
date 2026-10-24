"""共通Device fixture、元時刻、左右bindingと異常失効の回帰。実serialは使わない。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from xpotato_sim.plugins.input_sources.selfrionette_device.source import (
    DeviceSessionSource, canonical, INPUT_SCHEMA, BINDING_FIELDS)

FIXTURE = Path(__file__).resolve().parents[3]/"fixtures/selfrionette_device/bridge-sample-v1.json"


def event(role="left", *, sequence=81, value=0., session=2147):
    result = json.loads(FIXTURE.read_text())
    if role == "right":
        result["source"]["device_id"] = "srn-fedcba9876543210fedcba9876543210"
    result.update(sequence=sequence, session=session, device_ms=2000+sequence,
        command=[value, 0., 0., 0., 0., 0., 0.], normalized=[value, 0., 0., 0., 0., 0., 0.],
        active=[value != 0, False, False, False, False, False, False])
    return result


def parameters(roles=("left",)):
    return {"bridge_revision": event()["bridge_revision"],
        "bindings": {role: {key: event(role)[key] for key in BINDING_FIELDS} for role in roles}}


def wire(role="left", *, age=.002, **kwargs):
    return canonical({"schema_version": INPUT_SCHEMA, "role": role, "source_age_s": age,
        "event": event(role, **kwargs)})


def test_source_preserves_shared_service_values_without_processing_or_refresh():
    now = [1.]
    source = DeviceSessionSource(parameters=parameters(), clock=lambda: now[0])
    sample = source.parse_message(wire(value=.25))
    source.ingest_session_message(sample)
    frame = source.read_frame()
    assert frame.values == (.25, 0., 0., 0., 0., 0., 0.)
    assert frame.metadata["input_unit"] == "1"
    assert source.last_received_at_s == .998
    assert sample.sequence == 81 and sample.timestamp_s == 2.081
    now[0] += .1
    assert source.read_frame() is frame
    assert source.last_received_at_s == .998


def test_two_devices_require_explicit_roles_and_oldest_receipt():
    now = [1.]
    source = DeviceSessionSource(parameters=parameters(("left", "right")), clock=lambda: now[0])
    source.ingest_session_message(source.parse_message(wire()))
    assert source.last_received_at_s is None
    assert source.read_frame().metadata["source_active"] is False
    now[0] = 1.03
    source.ingest_session_message(source.parse_message(wire("right", value=.5)))
    assert source.last_received_at_s == .998
    frame = source.read_frame()
    assert frame.values[7] == .5
    assert set(frame.metadata["selfrionette_device_input_v1"]["devices"]) == {"left", "right"}
    wrong = json.loads(wire("right"))
    wrong["event"]["source"]["device_id"] = event()["source"]["device_id"]
    with pytest.raises(ValueError, match="binding mismatch"):
        source.parse_message(canonical(wrong))


@pytest.mark.parametrize("key,value", [("quality_flags", [2]*7), ("quality_flags", [3]*7),
    ("quality_flags", [5]*7), ("quality_flags", [9]*7), ("ready", False), ("missing", True),
    ("command", [None]*7), ("command", [float("inf")]*7), ("command", [1.01]*7),
    ("normalized", [float("nan")]*7), ("span_exceeded", [True]*7),
    ("unsupported_direction", [True]*7), ("domain", "newtons"),
    ("settings_generation", 2), ("bridge_revision", "1"*40)])
def test_bad_quality_and_provenance_never_become_neutral(key, value):
    source = DeviceSessionSource(parameters=parameters(), clock=lambda: 1.)
    raw = json.loads(wire())
    raw["event"][key] = value
    with pytest.raises(ValueError):
        source.parse_message(json.dumps(raw))
    assert source.last_received_at_s is None


def test_sequence_epoch_and_missing_initial_age_are_rejected():
    source = DeviceSessionSource(parameters=parameters(), clock=lambda: 1.)
    source.ingest_session_message(source.parse_message(wire()))
    for candidate in (wire(), wire(sequence=80), wire(sequence=82, session=2)):
        with pytest.raises(ValueError):
            source.ingest_session_message(source.parse_message(candidate))
    with pytest.raises(ValueError, match="age was refreshed"):
        source.parse_message(wire(age=0.))


def test_parameters_freeze_identity_and_reject_duplicate_device():
    raw = parameters()
    source = DeviceSessionSource(parameters=raw)
    raw["bindings"]["left"]["profile"]["revision"] = 99
    assert source.parameters["bindings"]["left"]["profile"]["revision"] == 1
    bad = parameters(("left", "right"))
    bad["bindings"]["right"] = deepcopy(bad["bindings"]["left"])
    with pytest.raises(ValueError, match="distinct"):
        DeviceSessionSource(parameters=bad)
