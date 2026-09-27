from __future__ import annotations

import asyncio
import json

from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.control.input_source_state import (
    build_runtime_input_source_state,
    build_runtime_input_source_state_from_metadata,
    runtime_input_source_state_to_metadata,
)
from xpotato_sim.schemas import MuJoCoState
from xpotato_sim.transport import mujoco_state_to_payload


class RecordingPublisher:
    def __init__(self) -> None:
        self.states: list[MuJoCoState] = []

    async def publish(self, state: MuJoCoState) -> None:
        self.states.append(state)


def test_runtime_input_source_state_metadata_keeps_optional_fields_optional() -> None:
    active_state = build_runtime_input_source_state("replay")
    stale_state = build_runtime_input_source_state("replay", stale_reason="older_command")
    missing_age_state = build_runtime_input_source_state("replay", command_age_ms=None, stale_reason=None)

    assert runtime_input_source_state_to_metadata(active_state) == {
        "source_kind": "replay",
        "source_active": True,
        "command_age_ms": 0,
    }
    assert runtime_input_source_state_to_metadata(stale_state)["stale_reason"] == "older_command"
    assert runtime_input_source_state_to_metadata(missing_age_state) == {
        "source_kind": "replay",
        "source_active": True,
    }


def test_runtime_input_source_state_metadata_parser_rehydrates_stale_fields_deterministically() -> None:
    state = build_runtime_input_source_state_from_metadata(
        {
            "source_kind": "replay",
            "source_active": False,
            "command_age_ms": 480,
            "stale_reason": "source_inactive",
        }
    )

    assert state.source_kind == "replay"
    assert state.source_active is False
    assert state.command_age_ms == 480
    assert state.stale_reason == "source_inactive"


def test_runtime_input_source_selection_includes_source_state_metadata() -> None:
    selection = select_runtime_input_source("programmed_target", steps=2)

    assert selection.initial_metadata["source_kind"] == "programmed_target"
    assert selection.initial_metadata["source_active"] is True
    assert selection.initial_metadata["command_age_ms"] == 0
    assert "stale_reason" not in selection.initial_metadata

    assert selection.frames[0].metadata["source_kind"] == "programmed_target"
    assert selection.frames[0].metadata["source_active"] is True
    assert selection.frames[0].metadata["command_age_ms"] == 0


def test_runtime_input_source_state_payload_stays_json_serializable_and_legacy_parseable() -> None:
    selection = select_runtime_input_source("replay", steps=1)
    publisher = RecordingPublisher()
    plan = build_runtime_input_source_step_loop_plan(selection, publisher=publisher)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))
    payload = mujoco_state_to_payload(records[0].state)

    assert payload["metadata"]["source_kind"] == "replay"
    assert payload["metadata"]["source_active"] is True
    assert payload["metadata"]["command_age_ms"] == 0
    assert "stale_reason" not in payload["metadata"]
    json.dumps(payload)

    legacy_payload = mujoco_state_to_payload(
        MuJoCoState(
            frame_index=1,
            time_s=0.0,
            metadata={"source_kind": "replay"},
        )
    )
    assert legacy_payload["metadata"] == {"source_kind": "replay"}
    json.dumps(legacy_payload)
