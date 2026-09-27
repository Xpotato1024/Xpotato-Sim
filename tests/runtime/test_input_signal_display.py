"""取得済みraw frameとread-only表示projectionの対応を検証する。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import socket

import pytest

from xpotato_sim.runtime.control.input_step_diagnostics import input_signal_display_projection
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.execution.input_step_loop import build_runtime_input_source_step_loop_plan, run_runtime_input_source_step_loop
from xpotato_sim.schemas import MuJoCoState, RawInputFrame


def test_projection_preserves_raw_values_and_distinct_clocks():
    frame = RawInputFrame("selfrionette", 900.0, (4.0, -2.0, 3.0, 0.0, 0.25, 0.0, -0.2))
    state = MuJoCoState(8, 0.2, qpos=(0, -0.5, 0, -0.5))
    result = input_signal_display_projection(frame, sample_schema="loadcell_vector_sample/v1", state=state)
    assert result["values"] == list(frame.values)
    assert result["source_timestamp_s"] == 900.0
    assert result["simulation_time_s"] == 0.2 and result["frame_index"] == 8
    assert "unit" not in result and "calibration" not in result
    result["values"][0] = 999
    assert frame.values[0] == 4.0


@pytest.mark.parametrize("value", (True, float("nan"), float("inf"), "1"))
def test_invalid_raw_values_not_relabelled_as_zero(value):
    frame = RawInputFrame("test", 1.0, (value,))
    with pytest.raises(ValueError):
        input_signal_display_projection(frame, sample_schema="test/v1", state=MuJoCoState(0, 0.0))


def test_real_input_step_loop_uses_raw_not_mapped_values(monkeypatch):
    loop = asyncio.new_event_loop()  # asyncio自身のself-pipeだけはtripwire前に構成する。
    def forbidden(*args, **kwargs):
        raise AssertionError("no network/serial in injected source test")
    for name in ("connect", "connect_ex", "bind", "listen", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    lines = ("vector,900000,2,-3,0,0,0,0,0", "vector,900020,0,0,0,0,0,0,0")
    selection = select_runtime_input_source("selfrionette", steps=2, line_source=lines,
        control_mapping_parameters={"mapping_config": {
            "channel_axis_weights": ((0.0, 1.0, 0.0),) + ((0.0, 0.0, 0.0),) * 6,
            "gain_m": 0.002, "max_delta_m": 0.002,
        }})
    plan = build_runtime_input_source_step_loop_plan(selection)
    try:
        records = loop.run_until_complete(run_runtime_input_source_step_loop(plan, steps=2, dt_s=0.02))
    finally:
        loop.close()
    first, second = (record.state.metadata["input_signal_v1"] for record in records)
    assert first["sample_schema"] == "loadcell_vector_sample/v1"
    assert first["values"] == [2.0, -3.0, 0, 0, 0, 0, 0]
    assert second["values"] == [0.0] * 7
    assert first["frame_index"] == records[0].state.frame_index
    assert first["simulation_time_s"] == records[0].state.time_s
    assert first["source_timestamp_s"] == 900.0
    assert records[0].frame.values == (1.0, -1.0, 0, 0, 0, 0, 0)  # 既存の正規化値とは分離する。
    assert plan.selection.runtime_reader.current_health().status.value == "disconnected"


@pytest.mark.parametrize('field,value', [
    ('source', ''), ('source', ' selfrionette'), ('source', 'bad\x00source'),
    ('timestamp_s', True), ('timestamp_s', float('inf')),
])
def test_projection_rejects_invalid_source_identity_and_clock(field, value):
    frame = replace(RawInputFrame('selfrionette', 900.0, (0.0,) * 7), **{field: value})
    with pytest.raises(ValueError):
        input_signal_display_projection(frame, sample_schema='loadcell_vector_sample/v1', state=MuJoCoState(0, 0.0))


@pytest.mark.parametrize('state', [MuJoCoState(True, 0.0), MuJoCoState(-1, 0.0),
                                   MuJoCoState(2**53, 0.0), MuJoCoState(0, True), MuJoCoState(0, float('nan'))])
def test_projection_rejects_unbindable_simulation_state(state):
    with pytest.raises(ValueError):
        input_signal_display_projection(RawInputFrame('test', 1.0, (0.0,)), sample_schema='test/v1', state=state)


def test_projection_rejects_float_overflow_without_relabelling():
    with pytest.raises(ValueError):
        input_signal_display_projection(RawInputFrame('test', 1.0, (10**1000,)),
                                        sample_schema='test/v1', state=MuJoCoState(0, 0.0))


@pytest.mark.parametrize("has_schema", [False, True])
def test_upstream_signal_metadata_cannot_claim_runtime_provenance(has_schema):
    forged = {"source": "forged", "values": [999] * 7}
    frame = RawInputFrame("replay", 0.0, metadata={
        "target_position_m": (0.6, 0.0, 0.1), "desired_endpoint_m": (0.6, 0.0, 0.1),
        "input_signal_v1": forged})
    selection = select_runtime_input_source("replay", steps=1, frames=(frame,))
    plan = build_runtime_input_source_step_loop_plan(selection)
    if not has_schema:
        plan = replace(plan, selection=replace(plan.selection, produced_sample_schema=None))
    record = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=0.02))[0]
    if has_schema:
        assert record.state.metadata["input_signal_v1"]["source"] == "replay"
        assert record.state.metadata["input_signal_v1"]["values"] == list(frame.values)
    else:
        assert "input_signal_v1" not in record.state.metadata
