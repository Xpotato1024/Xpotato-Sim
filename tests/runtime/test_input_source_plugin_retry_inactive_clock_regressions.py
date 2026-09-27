from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace

import pytest

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.control.input_source_selection import (
    select_runtime_input_source,
)
from xpotato_sim.runtime.control.input_source_state import (
    annotate_raw_input_frame,
    build_runtime_input_source_state_from_health,
)
from xpotato_sim.runtime.control.viewer_control_ingress import (
    ingest_viewer_control_message,
)
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
)
from xpotato_sim.runtime.safety.input_safety import (
    DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS,
)
from xpotato_sim.schemas import (
    RawInputFrame,
    ViewerControlKeyboardMessage,
    ViewerControlMessage,
)


def test_loadcell_plugin_rejects_read_after_close_and_restarts_cleanly() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    reader = plugin.create_runtime_reader(
        {"lines": ("vector,1000,1,2,3,4,5,6,7",)}
    )

    reader.start()
    first = reader.read_frame()
    reader.close()

    with pytest.raises(
        RuntimeError,
        match="Selfrionette input source is not started",
    ):
        reader.read_frame()

    reader.start()
    restarted = reader.read_frame()
    reader.close()

    assert restarted == first


def _analog_sample(
    timestamp_s: float,
    *,
    active: bool,
    stale_reason: str | None,
) -> Mapping[str, object]:
    return {
        "timestamp_s": timestamp_s,
        "raw_values": (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0),
        "active": active,
        "stale_reason": stale_reason,
    }


def test_analog_plugin_preserves_active_inactive_and_stale_states() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("analog_fixture").plugin
    reader = plugin.create_runtime_reader(
        {
            "samples": (
                _analog_sample(1.0, active=True, stale_reason=None),
                _analog_sample(2.0, active=False, stale_reason=None),
                _analog_sample(
                    3.0,
                    active=False,
                    stale_reason="recording_stale",
                ),
            )
        }
    )

    expected = (
        (InputSourceHealthStatus.ACTIVE, True, None),
        (InputSourceHealthStatus.INACTIVE, False, None),
        (InputSourceHealthStatus.STALE, False, "recording_stale"),
    )
    frames: list[RawInputFrame] = []
    for expected_status, expected_active, expected_reason in expected:
        frame = reader.read_frame()
        health = reader.current_health()
        state = build_runtime_input_source_state_from_health(
            health,
            source_kind="analog_fixture",
        )
        projected = annotate_raw_input_frame(frame, state)

        assert health.status is expected_status
        assert health.reason == expected_reason
        assert projected.metadata["source_active"] is expected_active
        assert projected.metadata.get("stale_reason") == expected_reason
        frames.append(projected)

    held_frame = reader.read_frame()
    held_health = reader.current_health()
    held_state = build_runtime_input_source_state_from_health(
        held_health,
        source_kind="analog_fixture",
    )
    assert annotate_raw_input_frame(held_frame, held_state) == frames[-1]


def test_inactive_health_rejects_failure_reason() -> None:
    health = InputSourceHealth(
        InputSourceHealthStatus.INACTIVE,
        age_ms=0,
    )
    state = build_runtime_input_source_state_from_health(
        health,
        source_kind="fixture",
    )

    assert state.source_active is False
    assert state.stale_reason is None

    with pytest.raises(ValueError, match="must not have a failure reason"):
        InputSourceHealth(
            InputSourceHealthStatus.INACTIVE,
            reason="not_stale",
            age_ms=0,
        )


def _replay_state_frame(
    timestamp_s: float,
    **state_metadata: object,
) -> RawInputFrame:
    endpoint = (0.6, 0.0, 0.1)
    return RawInputFrame(
        source="replay",
        timestamp_s=timestamp_s,
        metadata={
            "desired_endpoint_m": endpoint,
            "target_position_m": endpoint,
            **state_metadata,
        },
    )


def test_custom_replay_preserves_recorded_source_state_through_runtime() -> None:
    recorded_frames = (
        _replay_state_frame(1.0, source_active=False),
        _replay_state_frame(
            2.0,
            source_active=True,
            command_age_ms=DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS + 1,
        ),
        _replay_state_frame(
            3.0,
            source_active=True,
            stale_reason="explicit_stale",
        ),
    )
    selection = select_runtime_input_source(
        "replay",
        steps=len(recorded_frames),
        frames=recorded_frames,
    )

    assert selection.frames[0].metadata["source_active"] is False
    assert selection.frames[0].metadata["command_age_ms"] == 0
    assert "stale_reason" not in selection.frames[0].metadata
    assert selection.frames[1].metadata["source_active"] is True
    assert selection.frames[1].metadata["command_age_ms"] == 251
    assert "stale_reason" not in selection.frames[1].metadata
    assert selection.frames[2].metadata["source_active"] is True
    assert selection.frames[2].metadata["command_age_ms"] == 0
    assert selection.frames[2].metadata["stale_reason"] == "explicit_stale"
    assert selection.initial_metadata["source_active"] is False

    plan = build_runtime_input_source_step_loop_plan(selection)
    records = asyncio.run(
        run_runtime_input_source_step_loop(
            plan,
            steps=len(recorded_frames),
        )
    )

    assert tuple(record.frame for record in records) == selection.frames
    assert records[0].motion_command.metadata["stale_reason"] == "source_inactive"
    assert records[1].motion_command.metadata["stale_reason"] == (
        f"command_age_ms_exceeded_timeout_{DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS}"
    )
    assert records[2].motion_command.metadata["stale_reason"] == "explicit_stale"


class _PartialMetadataReader:
    def __init__(self, frame: RawInputFrame) -> None:
        self._frame = frame

    def read_frame(self) -> RawInputFrame:
        return self._frame

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(
            InputSourceHealthStatus.INACTIVE,
            age_ms=None,
        )


def test_live_health_comparison_only_checks_metadata_keys_that_exist() -> None:
    selection = select_runtime_input_source("programmed_target", steps=1)
    metadata = dict(selection.frames[0].metadata)
    metadata["source_active"] = False
    metadata.pop("command_age_ms", None)
    metadata.pop("stale_reason", None)
    partial_frame = replace(selection.frames[0], metadata=metadata)
    plan = build_runtime_input_source_step_loop_plan(selection)
    plan.pipeline.input_source = _PartialMetadataReader(partial_frame)

    record = asyncio.run(
        run_runtime_input_source_step_loop(plan, steps=1)
    )[0]

    assert record.frame.metadata["source_active"] is False
    assert "command_age_ms" not in record.frame.metadata
    assert "stale_reason" not in record.frame.metadata
    assert record.motion_command.metadata["stale_reason"] == "source_inactive"


class _MutableClock:
    def __init__(self, now_s: float) -> None:
        self.now_s = now_s

    def __call__(self) -> float:
        return self.now_s


def _viewer_message(timestamp_s: float) -> ViewerControlMessage:
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


def test_plugin_backed_viewer_plan_rebinds_clock_without_replacing_capability() -> None:
    clock = _MutableClock(10.0)
    selection = select_runtime_input_source("viewer", steps=1)
    original_capability = selection.viewer_bridge_capability
    assert original_capability is not None

    ingest_viewer_control_message(original_capability, _viewer_message(1.0))
    plan = build_runtime_input_source_step_loop_plan(
        selection,
        viewer_clock=clock,
    )

    assert plan.selection is selection
    assert plan.viewer_bridge_capability is original_capability
    assert plan.pipeline.input_source is selection.runtime_reader

    fresh_frame = plan.pipeline.input_source.read_frame()
    assert fresh_frame.metadata["viewer_source_kind"] == "keyboard"
    assert fresh_frame.metadata["source_active"] is True

    clock.now_s = 10.251
    stale_frame = plan.pipeline.input_source.read_frame()
    stale_health = plan.pipeline.input_source.current_health()
    assert stale_frame.metadata["command_age_ms"] >= 251
    assert stale_frame.metadata["source_active"] is False
    assert stale_frame.metadata["stale_reason"] == "command_age_ms_exceeded_timeout_250"
    assert stale_health.status is InputSourceHealthStatus.STALE

    clock.now_s = 11.0
    ingest_viewer_control_message(original_capability, _viewer_message(2.0))
    refreshed_frame = plan.pipeline.input_source.read_frame()
    assert refreshed_frame.metadata["source_active"] is True
    assert refreshed_frame.metadata["command_age_ms"] == 0
