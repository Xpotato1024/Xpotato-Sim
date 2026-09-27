from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from xpotato_sim.runtime.control.input_source_state import RuntimeInputSourceState, runtime_input_source_state_to_metadata
from xpotato_sim.runtime.safety.qpos_feasibility import (
    NoOpQposFeasibilityGuard,
    QposFeasibilityDiagnostic,
    QposFeasibilityGuard,
    QposFeasibilityResult,
)
from xpotato_sim.schemas import JointCommand, MotionCommand, MuJoCoState

DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS = 250


@dataclass(frozen=True, slots=True)
class RuntimeInputSafetyResult:
    motion_command: MotionCommand
    source_state: RuntimeInputSourceState
    is_stale: bool
    should_update_target_position_m: bool
    stale_reason: str | None
    command_age_ms: int | None
    qpos_feasibility_rejected: bool = False
    qpos_diagnostics: tuple[QposFeasibilityDiagnostic, ...] = ()
    qpos_feasibility_result: QposFeasibilityResult | None = None


def _coerce_current_qpos(current_qpos: Sequence[float] | None) -> tuple[float, ...] | None:
    if current_qpos is None:
        return None

    qpos = tuple(float(value) for value in current_qpos)
    if not qpos:
        return None

    return qpos


def _derive_stale_reason(
    source_state: RuntimeInputSourceState,
    *,
    timeout_ms: int,
) -> str | None:
    if source_state.stale_reason is not None:
        return source_state.stale_reason

    if not source_state.source_active:
        return "source_inactive"

    if source_state.command_age_ms is not None and source_state.command_age_ms > timeout_ms:
        return f"command_age_ms_exceeded_timeout_{timeout_ms}"

    return None


def _build_hold_motion_command(
    command: MotionCommand,
    *,
    current_qpos: Sequence[float] | None,
    source_state: RuntimeInputSourceState,
    stale_reason: str,
) -> MotionCommand:
    qpos = _coerce_current_qpos(current_qpos)
    metadata = {
        **dict(command.metadata),
        **runtime_input_source_state_to_metadata(
            RuntimeInputSourceState(
                source_kind=source_state.source_kind,
                source_active=source_state.source_active,
                command_age_ms=source_state.command_age_ms,
                stale_reason=stale_reason,
            )
        ),
    }
    metadata.pop("desired_endpoint_m", None)
    metadata.pop("target_position_m", None)
    metadata["runtime_input_safety_applied"] = True

    return replace(
        command,
        target=None,
        joint=None if qpos is None else JointCommand(joint_angles_rad=qpos),
        metadata=metadata,
    )


def build_runtime_input_safety_result(
    command: MotionCommand,
    *,
    source_state: RuntimeInputSourceState,
    current_state: MuJoCoState | None = None,
    timeout_ms: int = DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS,
    qpos_feasibility_guard: QposFeasibilityGuard | None = None,
) -> RuntimeInputSafetyResult:
    if timeout_ms < 0:
        raise ValueError("timeout_ms must be non-negative")

    stale_reason = _derive_stale_reason(source_state, timeout_ms=timeout_ms)
    is_stale = stale_reason is not None

    if is_stale:
        safe_motion_command = _build_hold_motion_command(
            command,
            current_qpos=None if current_state is None else current_state.qpos,
            source_state=source_state,
            stale_reason=stale_reason,
        )
    else:
        safe_motion_command = command

    qpos_result: QposFeasibilityResult | None = None
    qpos_rejected = False
    qpos_diagnostics: tuple[QposFeasibilityDiagnostic, ...] = ()
    if current_state is not None:
        qpos_guard = qpos_feasibility_guard or NoOpQposFeasibilityGuard()
        qpos_result = qpos_guard.evaluate(
            safe_motion_command,
            current_qpos_rad=current_state.qpos,
        )
        safe_motion_command = qpos_result.motion_command
        qpos_rejected = not qpos_result.accepted
        qpos_diagnostics = qpos_result.diagnostics

    safe_source_state = RuntimeInputSourceState(
        source_kind=source_state.source_kind,
        source_active=source_state.source_active,
        command_age_ms=source_state.command_age_ms,
        stale_reason=stale_reason,
    )

    return RuntimeInputSafetyResult(
        motion_command=safe_motion_command,
        source_state=safe_source_state,
        is_stale=is_stale,
        should_update_target_position_m=not is_stale and not qpos_rejected,
        stale_reason=stale_reason,
        command_age_ms=source_state.command_age_ms,
        qpos_feasibility_rejected=qpos_rejected,
        qpos_diagnostics=qpos_diagnostics,
        qpos_feasibility_result=qpos_result,
    )


__all__ = [
    "DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS",
    "RuntimeInputSafetyResult",
    "build_runtime_input_safety_result",
]
