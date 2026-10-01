from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.control.viewer_control_ingress import ingest_viewer_control_message
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.schemas import ViewerControlKeyboardMessage, ViewerControlMessage
from xpotato_sim.transport.payload import mujoco_state_to_payload


def _message(timestamp_s: float, *keys: str, zero: bool = False) -> ViewerControlMessage:
    return ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=timestamp_s,
        source_kind="keyboard",
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=keys,
            key_state={key: True for key in keys},
            focus_state="focused",
            zero_state=zero,
        ),
    )


def _build_plan(steps: int) -> tuple[ViewerInputSource, object, tuple[float, ...], tuple[float, float, float]]:
    source = ViewerInputSource(clock=lambda: 0.0)
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("viewer", steps=steps),
        viewer_clock=lambda: 0.0,
        viewer_input_source=source,
    )
    state = plan.pipeline.simulator.snapshot()
    tip = extract_fast_arm_tip_site_endpoint_from_state(state).position_m
    return source, plan, tuple(state.qpos), tip


def _record(label: str, initial_qpos: tuple[float, ...], runtime_record: object) -> dict[str, object]:
    state = runtime_record.state
    command = runtime_record.motion_command
    metadata = command.metadata
    payload = mujoco_state_to_payload(state)
    qpos_delta = tuple(state.qpos[index] - initial_qpos[index] for index in range(len(initial_qpos)))
    return {
        "label": label,
        "qpos": tuple(state.qpos),
        "qpos_delta_from_initial_rad": qpos_delta,
        "qpos_delta_norm_from_initial_rad": math.sqrt(sum(value * value for value in qpos_delta)),
        "qpos_discontinuity_norm_rad": metadata.get("qpos_delta_norm_rad", 0.0),
        "requested_local_velocity_m_s": metadata.get("local_endpoint_velocity_m_s"),
        "resolved_world_velocity_m_s": metadata.get("resolved_world_endpoint_velocity_m_s"),
        "requested_endpoint_delta_m": metadata.get("endpoint_delta_requested_m"),
        "predicted_endpoint_delta_m": metadata.get("endpoint_delta_achieved_m"),
        "measured_tip_delta_m": state.metadata.get("actual_tip_delta_m"),
        "progress_status": state.metadata.get("endpoint_progress_status"),
        "motion_status": metadata.get("motion_status"),
        "hold_or_reject_reason": metadata.get("motion_rejection_reason")
        or metadata.get("target_rejection_reason")
        or state.metadata.get("runtime_input_safety_reason"),
        "target_marker_m": state.target_position_m,
        "viewer_payload_qpos": tuple(payload["qpos"]),
        "viewer_payload_target_marker_m": payload["target_position_m"],
    }


def _run_first(label: str, keys: tuple[str, ...] | None) -> dict[str, object]:
    source, plan, initial_qpos, initial_tip = _build_plan(1)
    if keys is not None:
        ingest_viewer_control_message(source, _message(1.0, *keys, zero=not keys))
    record = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))[0]
    result = _record(label, initial_qpos, record)
    result["initial_qpos"] = initial_qpos
    result["initial_tip_m"] = initial_tip
    result["input_endpoint_after_rebase_m"] = source.current_endpoint_m
    return result


def _run_held(key: str) -> dict[str, object]:
    source, plan, initial_qpos, initial_tip = _build_plan(3)
    ingest_viewer_control_message(source, _message(2.0, key))
    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=3, dt_s=1.0 / 60.0))
    return {
        "label": f"held_{key}",
        "initial_qpos": initial_qpos,
        "initial_tip_m": initial_tip,
        "ticks": tuple(_record(f"{key}_tick_{index + 1}", initial_qpos, record) for index, record in enumerate(records)),
    }


def _run_release() -> dict[str, object]:
    source, plan, initial_qpos, initial_tip = _build_plan(2)
    ingest_viewer_control_message(source, _message(3.0, "Space"))
    first = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))[0]
    ingest_viewer_control_message(source, _message(4.0, zero=True))
    released = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))[0]
    return {
        "label": "release_after_space",
        "initial_qpos": initial_qpos,
        "initial_tip_m": initial_tip,
        "pressed": _record("Space_pressed", initial_qpos, first),
        "released": _record("Space_released_zero", tuple(first.state.qpos), released),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run selected fast_arm neutral-pose startup and first-input smoke.")
    parser.add_argument("--output", type=Path, default=None, help="optional JSON path; writes nowhere by default")
    args = parser.parse_args()
    cases = [
        _run_first("no_input", None),
        *(_run_first(f"first_{key}", (key,)) for key in ("Space", "ShiftLeft", "ShiftRight", "KeyW", "KeyS", "KeyA", "KeyD")),
        _run_first("explicit_zero", ()),
        *(_run_held(key) for key in ("Space", "KeyW", "KeyA", "KeyD")),
        _run_release(),
    ]
    payload = json.dumps({"schema_version": "r7-e-p22-startup-smoke-v1", "cases": cases}, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8", newline="\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
