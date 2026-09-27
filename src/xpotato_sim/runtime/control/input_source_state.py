"""Runtime input-source state contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
)
from xpotato_sim.schemas import RawInputFrame


@dataclass(frozen=True, slots=True)
class RuntimeInputSourceState:
    source_kind: str
    source_active: bool = True
    command_age_ms: int | None = 0
    stale_reason: str | None = None


def _coerce_source_kind(
    value: object,
    *,
    default_source_kind: str | None,
) -> str:
    if value is None:
        if default_source_kind is None:
            raise ValueError("source_kind is required")
        return default_source_kind

    return str(value)


def _coerce_source_active(value: object | None) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    raise ValueError("source_active must be a boolean when present")


def _coerce_command_age_ms(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "command_age_ms must be a non-negative integer when present"
        )
    if value < 0:
        raise ValueError(
            "command_age_ms must be a non-negative integer when present"
        )
    return value


def _coerce_stale_reason(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def build_runtime_input_source_state(
    source_kind: str,
    *,
    source_active: bool = True,
    command_age_ms: int | None = 0,
    stale_reason: str | None = None,
) -> RuntimeInputSourceState:
    return RuntimeInputSourceState(
        source_kind=source_kind,
        source_active=source_active,
        command_age_ms=command_age_ms,
        stale_reason=stale_reason,
    )


def build_runtime_input_source_state_from_metadata(
    metadata: Mapping[str, object],
    *,
    default_source_kind: str | None = None,
) -> RuntimeInputSourceState:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")

    source_kind = _coerce_source_kind(
        metadata.get("source_kind"),
        default_source_kind=default_source_kind,
    )
    source_active = _coerce_source_active(metadata.get("source_active"))

    if "command_age_ms" in metadata:
        command_age_ms = _coerce_command_age_ms(
            metadata.get("command_age_ms")
        )
    else:
        command_age_ms = 0

    stale_reason = _coerce_stale_reason(metadata.get("stale_reason"))

    return build_runtime_input_source_state(
        source_kind,
        source_active=source_active,
        command_age_ms=command_age_ms,
        stale_reason=stale_reason,
    )


def build_runtime_input_source_state_from_health(
    health: InputSourceHealth,
    *,
    source_kind: str,
) -> RuntimeInputSourceState:
    """Project source-owned typed health without recreating source reasons."""

    if not isinstance(health, InputSourceHealth):
        raise TypeError(
            "input source health projection requires InputSourceHealth"
        )

    resolved_source_kind = _coerce_source_kind(
        health.metadata.get("source_kind"),
        default_source_kind=source_kind,
    )

    if health.status is InputSourceHealthStatus.ACTIVE:
        source_active = True
        if health.reason is not None:
            raise ValueError(
                "active input source health cannot carry a stale reason"
            )
    elif health.status is InputSourceHealthStatus.INACTIVE:
        source_active = False
        if health.reason is not None:
            raise ValueError(
                "inactive input source health cannot carry a stale reason"
            )
    else:
        source_active = False
        if not health.reason:
            raise ValueError(
                "failed input source health requires a reason"
            )

    return build_runtime_input_source_state(
        resolved_source_kind,
        source_active=source_active,
        command_age_ms=health.age_ms,
        stale_reason=health.reason,
    )


def reconcile_runtime_input_source_state(
    frame: RawInputFrame, health: InputSourceHealth, *, source_kind: str
) -> RuntimeInputSourceState:
    """read後のhealthを使い、frameに明示されたstateだけを照合する。

    省略値をactiveの根拠にしない。recorded replayはこの照合を使わず、記録済みstateを保持する。
    """
    state = build_runtime_input_source_state_from_health(health, source_kind=source_kind)
    native = build_runtime_input_source_state_from_metadata(
        frame.metadata, default_source_kind=state.source_kind
    )
    if any(
        key in frame.metadata and getattr(native, key) != getattr(state, key)
        for key in ("source_active", "command_age_ms", "stale_reason")
    ):
        raise ValueError("input source frame metadata and typed health disagree")
    return state


def runtime_input_source_state_to_metadata(
    state: RuntimeInputSourceState,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source_kind": state.source_kind,
        "source_active": state.source_active,
    }
    if state.command_age_ms is not None:
        metadata["command_age_ms"] = state.command_age_ms
    if state.stale_reason is not None:
        metadata["stale_reason"] = state.stale_reason
    return metadata


def annotate_runtime_input_source_metadata(
    metadata: Mapping[str, object],
    state: RuntimeInputSourceState,
) -> dict[str, object]:
    annotated_metadata = dict(metadata)
    state_metadata = runtime_input_source_state_to_metadata(state)
    annotated_metadata.setdefault(
        "source_kind",
        state_metadata.pop("source_kind"),
    )
    annotated_metadata.update(state_metadata)
    return annotated_metadata


def annotate_raw_input_frame(
    frame: RawInputFrame,
    state: RuntimeInputSourceState,
) -> RawInputFrame:
    return replace(
        frame,
        metadata=annotate_runtime_input_source_metadata(
            frame.metadata,
            state,
        ),
    )


__all__ = [
    "RuntimeInputSourceState",
    "annotate_raw_input_frame",
    "annotate_runtime_input_source_metadata",
    "build_runtime_input_source_state",
    "build_runtime_input_source_state_from_metadata",
    "build_runtime_input_source_state_from_health",
    "runtime_input_source_state_to_metadata",
    "reconcile_runtime_input_source_state",
]
