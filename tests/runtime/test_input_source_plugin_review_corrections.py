from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.control.input_source_state import (
    annotate_raw_input_frame,
    build_runtime_input_source_state_from_health,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
)
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.control.viewer_control_ingress import ingest_viewer_control_message
from xpotato_sim.schemas import (
    RawInputFrame,
    ViewerControlGamepadButtonMessage,
    ViewerControlGamepadMessage,
    ViewerControlKeyboardMessage,
    ViewerControlMessage,
)


def _keyboard_message(timestamp_s: float = 1.0) -> ViewerControlMessage:
    return ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=timestamp_s,
        source_kind="keyboard",
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=("KeyD",),
            key_state={"KeyD": True},
            focus_state="focused",
            zero_state=False,
        ),
    )


def test_noop_plugin_reader_is_a_raw_frame_and_runs_multiple_runtime_steps() -> None:
    selection = select_runtime_input_source("noop", steps=2)
    plan = build_runtime_input_source_step_loop_plan(selection)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=2))

    assert len(records) == 2
    assert all(isinstance(record.frame, RawInputFrame) for record in records)
    assert records[0].frame == records[1].frame
    assert records[0].frame.source == "noop"
    assert records[0].frame.timestamp_s == 0.0
    assert records[0].frame.metadata["source_active"] is True
    assert records[0].frame.metadata["preset"] == "noop"


class _ManagedReader:
    def __init__(
        self,
        *,
        fail_on_start: bool = False,
        fail_on_read: bool = False,
        fail_on_close: bool = False,
    ) -> None:
        self.start_calls = 0
        self.close_calls = 0
        self._fail_on_start = fail_on_start
        self._fail_on_read = fail_on_read
        self._fail_on_close = fail_on_close

    def start(self) -> None:
        self.start_calls += 1
        if self._fail_on_start:
            raise RuntimeError("start failure")

    def read_frame(self) -> RawInputFrame:
        if self._fail_on_read:
            raise RuntimeError("loop failure")
        return RawInputFrame(source="replay", timestamp_s=0.0)

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)

    def close(self) -> None:
        self.close_calls += 1
        if self._fail_on_close:
            raise RuntimeError("close failure")


def _build_managed_test_plan(reader: _ManagedReader):
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("replay", steps=1)
    )
    plan.pipeline.input_source = reader
    return plan


def test_managed_reader_closes_once_after_normal_loop_and_loop_failure() -> None:
    normal = _ManagedReader()
    asyncio.run(run_runtime_input_source_step_loop(_build_managed_test_plan(normal), steps=1))
    assert (normal.start_calls, normal.close_calls) == (1, 1)

    failed = _ManagedReader(fail_on_read=True)
    with pytest.raises(RuntimeError, match="loop failure"):
        asyncio.run(run_runtime_input_source_step_loop(_build_managed_test_plan(failed), steps=1))
    assert (failed.start_calls, failed.close_calls) == (1, 1)


def test_invalid_steps_are_rejected_before_managed_lifecycle() -> None:
    reader = _ManagedReader()

    with pytest.raises(ValueError, match="steps must be a positive integer"):
        asyncio.run(
            run_runtime_input_source_step_loop(
                _build_managed_test_plan(reader),
                steps=0,
            )
        )

    assert (reader.start_calls, reader.close_calls) == (0, 0)


def test_managed_reader_start_failure_is_preserved_when_cleanup_also_fails() -> None:
    reader = _ManagedReader(fail_on_start=True, fail_on_close=True)

    with pytest.raises(RuntimeError, match="start failure") as error:
        asyncio.run(run_runtime_input_source_step_loop(_build_managed_test_plan(reader), steps=1))

    assert reader.close_calls == 1
    assert any("cleanup failed" in note for note in error.value.__notes__)


def test_managed_reader_normal_close_failure_is_surfaced() -> None:
    reader = _ManagedReader(fail_on_close=True)

    with pytest.raises(RuntimeError, match="close failure"):
        asyncio.run(run_runtime_input_source_step_loop(_build_managed_test_plan(reader), steps=1))
    assert reader.close_calls == 1

def test_loadcell_factory_has_no_io_and_read_before_start_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"open": 0, "close": 0}

    class FakeSerial:
        def __init__(self, **_: object) -> None:
            calls["open"] += 1

        def close(self) -> None:
            calls["close"] += 1

        def readline(self) -> bytes:
            return b"vector,0,1,2,3,4,5,6,7\n"

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    reader = plugin.create_runtime_reader({"port": "COM-test", "baud_rate": 115200})

    with pytest.raises(RuntimeError, match="Selfrionette input source is not started"):
        reader.read_frame()
    assert calls["open"] == 0

    reader.start()
    reader.close()
    reader.close()
    assert calls == {"open": 1, "close": 1}


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({}, "port or injected lines are required for Selfrionette"),
        ({"port": "   "}, "port must be a non-empty string"),
        ({"port": "COM-test", "baud_rate": 0}, "baud_rate must be positive"),
        (
            {"port": "COM-test", "baud_rate": True},
            "plugin parameter 'baud_rate' must be int",
        ),
        (
            {
                "port": "COM-test",
                "lines": ("vector,0,1,2,3,4,5,6,7",),
            },
            "cannot combine port and injected lines",
        ),
    ],
)
def test_loadcell_direct_factory_rejects_invalid_configuration_before_io(
    monkeypatch: pytest.MonkeyPatch,
    parameters: dict[str, object],
    message: str,
) -> None:
    calls = {"open": 0}

    class FakeSerial:
        def __init__(self, **_: object) -> None:
            calls["open"] += 1

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin

    with pytest.raises(ValueError, match=message):
        plugin.create_runtime_reader(parameters)

    assert calls["open"] == 0


def test_loadcell_direct_factory_accepts_injected_lines_without_serial_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"open": 0}

    class FakeSerial:
        def __init__(self, **_: object) -> None:
            calls["open"] += 1

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    reader = plugin.create_runtime_reader(
        {"lines": ("vector,0,1,2,3,4,5,6,7",)}
    )

    reader.start()
    frame = reader.read_frame()
    reader.close()

    assert frame.values == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    assert calls["open"] == 0


@pytest.mark.parametrize("steps", [0, -1])
def test_programmed_target_rejects_non_positive_steps(steps: int) -> None:
    with pytest.raises(ValueError, match="steps must be a positive integer"):
        select_runtime_input_source("programmed_target", steps=steps)


def test_programmed_target_plugin_preserves_terminal_hold_and_selection_reader_parity() -> None:
    selection = select_runtime_input_source("programmed_target", steps=1)
    assert selection.runtime_reader is not None
    first_runtime_frame = selection.runtime_reader.read_frame()
    health = selection.runtime_reader.current_health()
    projected = annotate_raw_input_frame(
        first_runtime_frame,
        build_runtime_input_source_state_from_health(health, source_kind="programmed_target"),
    )
    assert projected == selection.frames[0]

    plugin = INPUT_SOURCE_CATALOG.resolve("programmed_target").plugin
    reader = plugin.create_runtime_reader(
        {"steps": 1, "initial_position_m": (0.0, 0.0, 0.0), "preset": "sweep_x"}
    )
    frames = [reader.read_frame() for _ in range(23)]
    assert frames[20] == frames[21] == frames[22]


@pytest.mark.parametrize("source_name", ["programmed_target", "replay", "noop"])
def test_selection_frames_and_runtime_first_read_share_canonical_state_projection(
    source_name: str,
) -> None:
    kwargs = {}
    if source_name == "replay":
        kwargs["frames"] = (RawInputFrame(source="replay", timestamp_s=4.0, values=(1.0,)),)
    selection = select_runtime_input_source(source_name, steps=1, **kwargs)
    assert selection.runtime_reader is not None

    runtime_frame = selection.runtime_reader.read_frame()
    runtime_state = build_runtime_input_source_state_from_health(
        selection.runtime_reader.current_health(),
        source_kind=selection.source_name,
    )

    assert annotate_raw_input_frame(runtime_frame, runtime_state) == selection.frames[0]


def test_plugin_backed_viewer_exposes_ingress_and_rebases_the_same_underlying_source() -> None:
    selection = select_runtime_input_source("viewer", steps=1)
    capability = selection.viewer_bridge_capability
    assert capability is not None
    plan = build_runtime_input_source_step_loop_plan(selection)
    assert plan.viewer_bridge_capability is capability

    initial_tip = plan.endpoint_pose_provider.observe_endpoint_pose(
        plan.pipeline.simulator.snapshot()
    ).position_m
    assert capability.current_endpoint_m == pytest.approx(initial_tip)

    frame = ingest_viewer_control_message(capability, _keyboard_message())
    assert frame.metadata["viewer_source_kind"] == "keyboard"
    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1))

    assert records[0].frame.metadata["source_active"] is True
    assert records[0].frame.metadata["viewer_source_kind"] == "keyboard"
    assert capability.current_endpoint_m == pytest.approx(
        records[0].motion_command.metadata["desired_endpoint_m"]
    )


def test_plugin_backed_viewer_ingress_preserves_gamepad_frame_semantics() -> None:
    selection = select_runtime_input_source("viewer", steps=1)
    capability = selection.viewer_bridge_capability
    assert capability is not None
    plan = build_runtime_input_source_step_loop_plan(selection)

    frame = ingest_viewer_control_message(
        capability,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=2.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="test-pad",
                axes=(0.25, -0.5, 0.0),
                buttons=(ViewerControlGamepadButtonMessage(pressed=True, value=1.0),),
            ),
        ),
    )
    assert frame.metadata["viewer_source_kind"] == "gamepad"

    record = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1))[0]
    assert record.frame.metadata["source_active"] is True
    assert record.frame.metadata["viewer_source_kind"] == "gamepad"
