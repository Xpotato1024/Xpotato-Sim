"""装置へ接続せずproduction readerの時間・容量・失敗境界を検証する。"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.input_sources.selfrionette import (
    SelfrionetteInputSource, SerialInputSource, SerialAcquisitionError, SerialFrameParseError, build_reader,
)
from xpotato_sim.plugins.input_sources.selfrionette.protocol import (
    MAX_LINES_PER_FRAME, MAX_LINE_BYTES, MAX_DIAGNOSTICS,
)
from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies, InputSourceHealthStatus

LINE = "vector,2147483648,0.5,0,0,0,0,0,0"
ZERO = "vector,0,0,0,0,0,0,0,0"


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


def injected(lines, clock=None):
    return SelfrionetteInputSource(port=None, baud_rate=115200, injected_lines=tuple(lines), clock=clock)


def serial_reader(monkeypatch, responses, clock):
    """pySerialのtimeout/size契約を模擬し、real serial importを完全に置き換える。"""
    instances = []

    class FakeSerial:
        def __init__(self, **kwargs):
            assert kwargs["port"] == "FAKE-NOT-A-DEVICE"
            assert kwargs["timeout"] == 0.25
            self.timeout = kwargs["timeout"]
            self.reads = []
            self.close_calls = 0
            self.responses = iter(responses)
            instances.append(self)

        def read_until(self, expected, size):
            assert expected == b"\n"
            assert size == MAX_LINE_BYTES + 1
            assert 0 < self.timeout <= 0.25
            self.reads.append(self.timeout)
            item = next(self.responses)
            if isinstance(item, BaseException):
                raise item
            delay, payload = item if isinstance(item, tuple) else (0.0, item)
            clock.now += delay
            return payload

        def close(self):
            self.close_calls += 1

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    reader = build_reader(
        {"port": "FAKE-NOT-A-DEVICE"}, runtime_dependencies=InputSourceRuntimeDependencies(clock=clock),
    )
    assert not instances
    reader.start()
    return reader, instances[0]


def test_initial_zero_fresh_stale_restart_and_source_clock_independence():
    clock = Clock()
    reader = injected((ZERO, LINE), clock)
    assert reader.current_health().reason == "not_started"
    reader.start()
    assert reader.current_health().status is InputSourceHealthStatus.INACTIVE
    assert reader.current_health().age_ms is None
    clock.now = 10.3
    assert reader.current_health().reason == "no_vector_received"
    frame = reader.read_frame()
    assert frame.values == (0.0,) * 7
    assert reader.current_health().status is InputSourceHealthStatus.ACTIVE
    assert frame.timestamp_s == 0.0
    assert frame.metadata["acquisition_v1"]["receipt_monotonic_s"] == 10.3
    clock.now = 10.55
    assert reader.current_health().status is InputSourceHealthStatus.ACTIVE
    clock.now = 10.551
    assert reader.current_health().status is InputSourceHealthStatus.STALE
    assert reader.current_health().reason == "sample_stale"
    second = reader.read_frame()
    assert second.timestamp_s == 2147483.648
    assert second.metadata["acquisition_v1"]["receipt_monotonic_s"] == 10.551
    assert reader.current_health().age_ms == 0
    reader.close()
    assert reader.current_health().status is InputSourceHealthStatus.DISCONNECTED
    reader.start()
    assert reader.current_health().status is InputSourceHealthStatus.INACTIVE
    assert reader.read_frame().values == (0.0,) * 7
    reader.close()


def test_default_injected_clock_is_deterministic_and_declared():
    frames = []
    for _ in range(2):
        reader = injected((LINE,))
        reader.start()
        frames.append(reader.read_frame())
        reader.close()
    assert frames[0] == frames[1]
    assert frames[0].metadata["acquisition_v1"]["clock_kind"] == "injected_static"


def test_line_budget_stops_diagnostic_storm_without_consuming_next_vector():
    count = 0

    def diagnostic_stream():
        nonlocal count
        count += 1
        assert count <= MAX_LINES_PER_FRAME, "unbounded read crossed the declared budget"
        return "status,waiting"

    source = SerialInputSource(diagnostic_stream)
    with pytest.raises(SerialAcquisitionError, match="no_vector_line_budget"):
        source.read_frame()
    assert count == MAX_LINES_PER_FRAME
    assert len(source.diagnostics) == MAX_DIAGNOSTICS


def test_last_allowed_line_can_be_vector_and_diagnostics_memory_is_bounded():
    lines = (("status,waiting",) * (MAX_LINES_PER_FRAME - 1) + (LINE,)) * 3
    source = SerialInputSource.from_lines(lines)
    for _ in range(3):
        assert source.read_frame().values[0] == 0.5
    assert len(source.diagnostics) == MAX_DIAGNOSTICS
    assert source.diagnostics_dropped == 3 * (MAX_LINES_PER_FRAME - 1) - MAX_DIAGNOSTICS


@pytest.mark.parametrize("line", ("x" * (MAX_LINE_BYTES + 1), "status," + "あ" * 400, "vector,0,1,2", "vector,0,nan,0,0,0,0,0,0", ""))
def test_bad_or_oversize_frame_latches_invalid_until_explicit_restart(line):
    reader = injected((LINE, line, ZERO))
    reader.start()
    assert reader.read_frame().values[0] == 0.5
    with pytest.raises(SerialFrameParseError):
        reader.read_frame()
    assert reader.current_health().status is InputSourceHealthStatus.INVALID
    with pytest.raises(SerialAcquisitionError, match="close_required"):
        reader.read_frame()
    with pytest.raises(SerialAcquisitionError, match="close_required"):
        reader.start()
    reader.close()
    reader.start()
    assert reader.read_frame().values[0] == 0.5
    reader.close()


def test_exact_byte_limit_is_accepted_for_diagnostic():
    text = "status," + "x" * (MAX_LINE_BYTES - len("status,"))
    source = SerialInputSource.from_lines((text, LINE))
    assert source.read_frame().values[0] == 0.5
    assert source.diagnostics[0].raw_line == text


def test_eof_is_not_old_frame_or_zero_and_stays_disconnected():
    reader = injected((LINE,))
    reader.start()
    reader.read_frame()
    with pytest.raises(StopIteration):
        reader.read_frame()
    assert reader.current_health().reason == "end_of_stream"
    with pytest.raises(SerialAcquisitionError):
        reader.read_frame()
    reader.close()


@pytest.mark.parametrize("raw,status,exception", (
    (b"", InputSourceHealthStatus.STALE, SerialAcquisitionError),
    (None, InputSourceHealthStatus.INVALID, SerialAcquisitionError),
    ("", InputSourceHealthStatus.INVALID, SerialAcquisitionError),
    (b"vector,1,1,0,0,0,0,0,0", InputSourceHealthStatus.INVALID, SerialFrameParseError),
    (b"x" * (MAX_LINE_BYTES + 1), InputSourceHealthStatus.INVALID, SerialFrameParseError),
    (b"status,\xff\n", InputSourceHealthStatus.INVALID, UnicodeDecodeError),
    (OSError("unplugged"), InputSourceHealthStatus.DISCONNECTED, OSError),
))
def test_fake_serial_failure_returns_once_and_does_not_retry(monkeypatch, raw, status, exception):
    clock = Clock()
    reader, port = serial_reader(monkeypatch, (LINE.encode() + b"\r\n", raw, ZERO.encode() + b"\n"), clock)
    frame = reader.read_frame()
    assert frame.values[0] == 0.5
    with pytest.raises(exception):
        reader.read_frame()
    assert reader.current_health().status is status
    with pytest.raises(SerialAcquisitionError, match="close_required"):
        reader.read_frame()
    assert len(port.reads) == 2
    reader.close()
    reader.close()
    assert port.close_calls == 1


def test_one_frame_deadline_is_shared_across_diagnostics(monkeypatch):
    clock = Clock()
    reader, port = serial_reader(monkeypatch, ((0.1, b"status,a\n"), (0.1, b"warn,b\n"), (0.06, LINE.encode() + b"\n")), clock)
    with pytest.raises(SerialAcquisitionError, match="read_timeout"):
        reader.read_frame()
    assert port.reads == pytest.approx([0.25, 0.15, 0.05])
    assert reader.current_health().status is InputSourceHealthStatus.STALE
    assert reader.current_health().age_ms is None
    assert len(reader.diagnostics) == 2
    reader.close()


def test_live_and_injected_share_parser_without_altering_device_time(monkeypatch):
    clock = Clock()
    live, _ = serial_reader(monkeypatch, (b"status,ok\n", LINE.encode() + b"\r\n"), clock)
    offline = injected(("status,ok", LINE), clock)
    offline.start()
    a, b = live.read_frame(), offline.read_frame()
    assert a.values == b.values and a.timestamp_s == b.timestamp_s
    assert a.metadata["raw_line"] == b.metadata["raw_line"]
    assert live.diagnostics == offline.diagnostics
    live.close(); offline.close()


@pytest.mark.parametrize("now", (float("nan"), float("inf"), 9.0, True, 1e308, 10 ** 400))
def test_bad_clock_cannot_refresh_previous_sample(now):
    clock = Clock()
    reader = injected((LINE, ZERO), clock)
    reader.start(); reader.read_frame()
    clock.now = now
    assert reader.current_health().status is InputSourceHealthStatus.INVALID
    with pytest.raises(SerialAcquisitionError):
        reader.read_frame()
    reader.close()


def test_factory_uses_existing_clock_dependency():
    clock = Clock()
    reader = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin.create_runtime_reader(
        {"lines": (LINE,)}, runtime_dependencies=InputSourceRuntimeDependencies(clock=clock),
    )
    reader.start()
    assert reader.read_frame().metadata["acquisition_v1"]["receipt_monotonic_s"] == clock.now
    clock.now += 1
    assert reader.current_health().reason == "sample_stale"
    reader.close()


@pytest.mark.parametrize("entry", ("loop", "run_once"))
@pytest.mark.parametrize("failure", (b"", b"vector,1,1,2\n", b"vector,2,0,0,0,0,0,0,0", OSError("unplugged")))
def test_acquisition_failure_after_motion_cannot_replay_last_command(monkeypatch, entry, failure):
    from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
    from xpotato_sim.runtime.execution.input_step_loop import build_runtime_input_source_step_loop_plan, run_runtime_input_source_step_loop

    clock = Clock()
    reader, port = serial_reader(monkeypatch, (LINE.encode() + b"\n", failure, ZERO.encode() + b"\n"), clock)
    selection = select_runtime_input_source("selfrionette", steps=2, line_source=(LINE,), control_mapping_parameters={
        "mapping_config": {"channel_axis_weights": ((0.0, 1.0, 0.0),) + ((0.0, 0.0, 0.0),) * 6,
                           "gain_m": 0.002, "max_delta_m": 0.002},
    })
    plan = build_runtime_input_source_step_loop_plan(selection)
    plan.pipeline.input_source = reader
    simulator = plan.pipeline.simulator
    initial = simulator.snapshot()
    commands = []
    apply_command = type(simulator).apply_joint_position_command

    def capture(backend, command):
        commands.append(command)
        return apply_command(backend, command)

    monkeypatch.setattr(type(simulator), "apply_joint_position_command", capture)

    async def run():
        if entry == "loop":
            await run_runtime_input_source_step_loop(plan, steps=2, dt_s=0.02)
        else:
            try:
                await plan.pipeline.run_once(0.02)
                await plan.pipeline.run_once(0.02)
            finally:
                reader.close()

    with pytest.raises((SerialAcquisitionError, SerialFrameParseError, OSError)):
        asyncio.run(run())
    assert len(commands) == 1
    assert simulator.snapshot().frame_index == 1
    assert simulator.snapshot().time_s == pytest.approx(0.02)
    assert simulator.snapshot().qpos != initial.qpos
    assert port.close_calls == 1
    assert len(port.reads) == 2


def test_diagnostic_budget_failure_latches_and_keeps_bounded_evidence_after_close():
    reader = injected(("status,waiting",) * MAX_LINES_PER_FRAME + (LINE,))
    reader.start()
    with pytest.raises(SerialAcquisitionError, match="no_vector_line_budget"):
        reader.read_frame()
    assert reader.current_health().status is InputSourceHealthStatus.STALE
    with pytest.raises(SerialAcquisitionError, match="close_required"):
        reader.read_frame()
    reader.close()
    assert len(reader.diagnostics) == MAX_DIAGNOSTICS


def test_close_failure_is_not_reported_as_success_or_reactivated(monkeypatch):
    clock = Clock()
    reader, port = serial_reader(monkeypatch, (LINE.encode() + b"\n",), clock)
    reader.read_frame()

    def fail_close():
        raise OSError("close failed")

    port.close = fail_close
    with pytest.raises(OSError, match="close failed"):
        reader.close()
    assert reader.current_health().reason == "close_failed"
    with pytest.raises(SerialAcquisitionError, match="close_required"):
        reader.read_frame()


@pytest.mark.parametrize("timestamp", ("9" * 500,))
def test_unrepresentable_device_timestamp_is_invalid_not_active(timestamp):
    reader = injected((f"vector,{timestamp},0,0,0,0,0,0,0",))
    reader.start()
    with pytest.raises(SerialFrameParseError, match="timestamp out of range"):
        reader.read_frame()
    assert reader.current_health().status is InputSourceHealthStatus.INVALID
    reader.close()
