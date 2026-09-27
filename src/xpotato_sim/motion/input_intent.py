from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from xpotato_sim.kinematics import InverseKinematicsSolver
from xpotato_sim.schemas import InputIntent, JointCommand, MotionCommand, TargetCommand

DEFAULT_TARGET_DISCONTINUITY_THRESHOLD_RAD = 2.0
_KNOWN_TARGET_REJECTION_MESSAGES = {
    "target_position_m must remain on the solver plane",
    "target_position_m is outside the reachable workspace",
    "target_position_m did not converge",
}
_TARGET_REJECTION_REASON_BY_MESSAGE = {
    "target_position_m must remain on the solver plane": "target_plane_mismatch",
    "target_position_m is outside the reachable workspace": "target_unreachable",
    "target_position_m did not converge": "target_non_convergence",
}


def _has_non_zero_delta(delta_m: tuple[float, float, float]) -> bool:
    return any(component != 0.0 for component in delta_m)


def _coerce_vector3(name: str, value: object) -> tuple[float, float, float]:
    if not isinstance(value, Sequence):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    for component_index, component in enumerate(components):
        if not math.isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {component_index}")

    return components


def _coerce_joint_angles(name: str, values: Sequence[float] | None) -> tuple[float, ...] | None:
    if values is None:
        return None

    joint_angles_rad = tuple(float(value) for value in values)
    if not joint_angles_rad:
        return None

    return joint_angles_rad


def _build_rejected_metadata(
    metadata: Mapping[str, object] | None,
    *,
    reason: str,
    rejection_message: str,
    rejected_desired_endpoint_m: tuple[float, float, float] | None,
) -> dict[str, object]:
    rejected_metadata = {} if metadata is None else dict(metadata)
    rejected_metadata.pop("target_position_m", None)
    rejected_metadata["runtime_input_safety_applied"] = True
    rejected_metadata["target_status"] = "held"
    rejected_metadata["target_rejected"] = True
    rejected_metadata["target_rejection_reason"] = reason
    rejected_metadata["target_rejection_message"] = rejection_message
    if rejected_desired_endpoint_m is not None:
        rejected_metadata["rejected_desired_endpoint_m"] = rejected_desired_endpoint_m
    return rejected_metadata


def _is_target_rejection_error(exc: ValueError) -> bool:
    return str(exc) in _KNOWN_TARGET_REJECTION_MESSAGES


def _is_seed_shape_error(exc: ValueError) -> bool:
    return "seed_joint_angles_rad" in str(exc)


def _target_rejection_reason_for_error(exc: ValueError) -> str:
    return _TARGET_REJECTION_REASON_BY_MESSAGE.get(str(exc), "invalid_target")


def _qpos_discontinuity_norm_rad(
    candidate_qpos_rad: Sequence[float],
    current_qpos_rad: Sequence[float],
) -> float:
    overlap = min(len(candidate_qpos_rad), len(current_qpos_rad))
    if overlap == 0:
        return 0.0

    return math.sqrt(
        sum(
            (float(candidate_qpos_rad[index]) - float(current_qpos_rad[index])) ** 2
            for index in range(overlap)
        )
    )


def _metadata_with_qpos_diagnostics(
    metadata: Mapping[str, object] | None,
    *,
    qpos_before_ik_rad: Sequence[float] | None,
    ik_output_qpos_rad: Sequence[float] | None,
    qpos_discontinuity_norm_rad: float | None,
) -> dict[str, object]:
    diagnostic_metadata = {} if metadata is None else dict(metadata)
    if qpos_before_ik_rad is not None:
        diagnostic_metadata["qpos_before_ik_rad"] = tuple(float(value) for value in qpos_before_ik_rad)
    if ik_output_qpos_rad is not None:
        diagnostic_metadata["ik_output_qpos_rad"] = tuple(float(value) for value in ik_output_qpos_rad)
    if qpos_discontinuity_norm_rad is not None:
        diagnostic_metadata["qpos_discontinuity_norm_rad"] = float(qpos_discontinuity_norm_rad)
    return diagnostic_metadata


def _resolve_target_endpoint_m(intent: InputIntent) -> tuple[float, float, float] | None:
    ik_target_endpoint_m = intent.metadata.get("ik_target_endpoint_m")
    source_name = "ik_target_endpoint_m"
    if ik_target_endpoint_m is not None:
        return _coerce_vector3(source_name, ik_target_endpoint_m)

    desired_endpoint_m = getattr(intent, "desired_endpoint_m", None)
    source_name = "desired_endpoint_m"
    if desired_endpoint_m is None:
        desired_endpoint_m = intent.metadata.get("desired_endpoint_m")

    if desired_endpoint_m is None:
        desired_endpoint_m = getattr(intent, "target_position_m", None)
        source_name = "target_position_m"
    if desired_endpoint_m is None:
        desired_endpoint_m = intent.metadata.get("target_position_m")
        source_name = "target_position_m"

    if desired_endpoint_m is None:
        return None

    return _coerce_vector3(source_name, desired_endpoint_m)


def _resolve_rejected_desired_endpoint_m(
    intent: InputIntent,
    *,
    fallback_endpoint_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    desired_endpoint_m = getattr(intent, "desired_endpoint_m", None)
    source_name = "desired_endpoint_m"
    if desired_endpoint_m is None:
        desired_endpoint_m = intent.metadata.get("desired_endpoint_m")

    if desired_endpoint_m is None:
        return fallback_endpoint_m

    return _coerce_vector3(source_name, desired_endpoint_m)


def _candidate_seed_joint_angles_rad(
    *,
    current_qpos_rad: tuple[float, ...] | None,
    explicit_seed_joint_angles_rad: tuple[float, ...] | None,
) -> tuple[tuple[float, ...] | None, ...]:
    candidates: list[tuple[float, ...] | None] = []
    if current_qpos_rad is not None:
        candidates.append(current_qpos_rad)
    if explicit_seed_joint_angles_rad is not None and explicit_seed_joint_angles_rad not in candidates:
        candidates.append(explicit_seed_joint_angles_rad)
    if current_qpos_rad is not None and len(current_qpos_rad) >= 2:
        truncated_current_qpos_rad = current_qpos_rad[:2]
        if truncated_current_qpos_rad not in candidates:
            candidates.append(truncated_current_qpos_rad)
    if not candidates:
        candidates.append(None)

    return tuple(candidates)


def _build_motion_command(
    *,
    timestamp_s: float,
    target: TargetCommand | None = None,
    joint: JointCommand | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MotionCommand:
    return MotionCommand(
        timestamp_s=timestamp_s,
        target=target,
        joint=joint,
        metadata={} if metadata is None else dict(metadata),
    )


def build_motion_command_from_target_command(
    *,
    timestamp_s: float,
    target_command: TargetCommand | None,
    metadata: Mapping[str, object] | None = None,
    joint_command: JointCommand | None = None,
) -> MotionCommand:
    """Build a MotionCommand from a command-side target boundary."""

    return _build_motion_command(
        timestamp_s=timestamp_s,
        target=target_command,
        joint=joint_command,
        metadata=metadata,
    )


def build_motion_command_from_input_intent(intent: InputIntent) -> MotionCommand:
    if intent.joint_delta_rad:
        raise ValueError("joint_delta_rad to MotionCommand.joint conversion is not supported")

    target = TargetCommand(delta_m=intent.target_delta_m) if _has_non_zero_delta(intent.target_delta_m) else None
    return build_motion_command_from_target_command(
        timestamp_s=intent.timestamp_s,
        target_command=target,
        metadata=intent.metadata,
    )


class InputIntentMotionGenerator:
    """Minimal motion skeleton that turns replay intent into MotionCommand."""

    def __init__(self) -> None:
        self._current_qpos_rad: tuple[float, ...] | None = None

    def set_current_qpos_rad(
        self, current_qpos_rad: Sequence[float] | None
    ) -> None:
        self._current_qpos_rad = _coerce_joint_angles(
            "current_qpos_rad", current_qpos_rad
        )

    def update(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        _ = dt_s  # Protocol compatibility; this skeleton does not use delta time yet.
        command = build_motion_command_from_input_intent(intent)
        if (
            command.target is None
            and command.joint is None
            and self._current_qpos_rad is not None
        ):
            return MotionCommand(
                timestamp_s=command.timestamp_s,
                joint=JointCommand(
                    joint_angles_rad=self._current_qpos_rad
                ),
                metadata=command.metadata,
            )
        return command


class TargetToJointMotionGenerator:
    """Skeleton that resolves a target position through IK."""

    def __init__(
        self,
        ik_solver: InverseKinematicsSolver,
        *,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
        current_qpos_rad: tuple[float, ...] | None = None,
        qpos_joint_count: int | None = None,
        discontinuity_threshold_rad: float = DEFAULT_TARGET_DISCONTINUITY_THRESHOLD_RAD,
        discontinuity_threshold_label: str = "global safety threshold",
    ) -> None:
        self._ik_solver = ik_solver
        self._seed_joint_angles_rad = seed_joint_angles_rad
        self._current_qpos_rad = current_qpos_rad
        self._qpos_joint_count = qpos_joint_count
        self._discontinuity_threshold_rad = float(discontinuity_threshold_rad)
        self._discontinuity_threshold_label = discontinuity_threshold_label

    def set_current_qpos_rad(self, current_qpos_rad: Sequence[float] | None) -> None:
        self._current_qpos_rad = _coerce_joint_angles("current_qpos_rad", current_qpos_rad)

    def update(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        _ = dt_s  # Protocol compatibility; this skeleton does not use delta time yet.

        if intent.joint_delta_rad:
            raise ValueError("joint_delta_rad to MotionCommand.joint conversion is not supported")

        desired_endpoint_m = _resolve_target_endpoint_m(intent)
        if desired_endpoint_m is None:
            if _has_non_zero_delta(intent.target_delta_m):
                target = TargetCommand(delta_m=intent.target_delta_m)
                return build_motion_command_from_target_command(
                    timestamp_s=intent.timestamp_s,
                    target_command=target,
                    metadata=intent.metadata,
                )

            raise ValueError("desired_endpoint_m or target_position_m is required for TargetToJointMotionGenerator")

        target = TargetCommand(delta_m=intent.target_delta_m) if _has_non_zero_delta(intent.target_delta_m) else None
        seed_joint_angles_candidates_rad = _candidate_seed_joint_angles_rad(
            current_qpos_rad=self._current_qpos_rad,
            explicit_seed_joint_angles_rad=self._seed_joint_angles_rad,
        )
        last_seed_shape_error: ValueError | None = None

        joint = None
        for candidate_seed_joint_angles_rad in seed_joint_angles_candidates_rad:
            try:
                joint = self._ik_solver.solve(
                    desired_endpoint_m,
                    seed_joint_angles_rad=candidate_seed_joint_angles_rad,
                )
            except ValueError as exc:
                if _is_seed_shape_error(exc):
                    last_seed_shape_error = exc
                    continue
                if not _is_target_rejection_error(exc):
                    raise

                hold_qpos_rad = self._current_qpos_rad
                if hold_qpos_rad is None and candidate_seed_joint_angles_rad is not None:
                    hold_qpos_rad = tuple(candidate_seed_joint_angles_rad)

                if (
                    self._qpos_joint_count is not None
                    and hold_qpos_rad is not None
                    and len(hold_qpos_rad) < self._qpos_joint_count
                ):
                    hold_qpos_rad = hold_qpos_rad + (0.0,) * (self._qpos_joint_count - len(hold_qpos_rad))

                if hold_qpos_rad is None:
                    hold_joint = None
                else:
                    hold_joint = JointCommand(joint_angles_rad=tuple(hold_qpos_rad))

                rejected_metadata = _build_rejected_metadata(
                    intent.metadata,
                    reason=_target_rejection_reason_for_error(exc),
                    rejection_message=str(exc),
                    rejected_desired_endpoint_m=_resolve_rejected_desired_endpoint_m(
                        intent,
                        fallback_endpoint_m=desired_endpoint_m,
                    ),
                )
                rejected_metadata.update(
                    _metadata_with_qpos_diagnostics(
                        {},
                        qpos_before_ik_rad=self._current_qpos_rad,
                        ik_output_qpos_rad=None,
                        qpos_discontinuity_norm_rad=0.0 if self._current_qpos_rad is not None else None,
                    )
                )
                rejected_metadata["target_discontinuity_threshold_rad"] = self._discontinuity_threshold_rad
                rejected_metadata["target_discontinuity_threshold_label"] = self._discontinuity_threshold_label
                return MotionCommand(
                    timestamp_s=intent.timestamp_s,
                    target=None,
                    joint=hold_joint,
                    metadata=rejected_metadata,
                )
            else:
                break
        else:
            if last_seed_shape_error is not None:
                raise last_seed_shape_error
            raise ValueError("seed_joint_angles_rad is required for TargetToJointMotionGenerator")

        if self._current_qpos_rad is not None:
            current_qpos_rad = self._current_qpos_rad
            candidate_qpos_rad = joint.joint_angles_rad
            discontinuity_norm_rad = _qpos_discontinuity_norm_rad(
                candidate_qpos_rad,
                current_qpos_rad,
            )
            if discontinuity_norm_rad > self._discontinuity_threshold_rad:
                hold_qpos_rad = current_qpos_rad
                if self._qpos_joint_count is not None and len(hold_qpos_rad) < self._qpos_joint_count:
                    hold_qpos_rad = hold_qpos_rad + (0.0,) * (self._qpos_joint_count - len(hold_qpos_rad))

                rejected_metadata = _build_rejected_metadata(
                    intent.metadata,
                    reason="target_discontinuous",
                    rejection_message=(
                        "candidate qpos exceeds the "
                        f"{self._discontinuity_threshold_label} "
                        f"{self._discontinuity_threshold_rad}"
                    ),
                    rejected_desired_endpoint_m=_resolve_rejected_desired_endpoint_m(
                        intent,
                        fallback_endpoint_m=desired_endpoint_m,
                    ),
                )
                rejected_metadata.update(
                    _metadata_with_qpos_diagnostics(
                        {},
                        qpos_before_ik_rad=current_qpos_rad,
                        ik_output_qpos_rad=candidate_qpos_rad,
                        qpos_discontinuity_norm_rad=discontinuity_norm_rad,
                    )
                )
                rejected_metadata["target_discontinuity_threshold_rad"] = self._discontinuity_threshold_rad
                rejected_metadata["target_discontinuity_threshold_label"] = self._discontinuity_threshold_label
                return MotionCommand(
                    timestamp_s=intent.timestamp_s,
                    target=None,
                    joint=JointCommand(joint_angles_rad=tuple(hold_qpos_rad)),
                    metadata=rejected_metadata,
                )
        else:
            discontinuity_norm_rad = None

        if self._qpos_joint_count is not None:
            joint_angles_rad = joint.joint_angles_rad
            if len(joint_angles_rad) > self._qpos_joint_count:
                raise ValueError("solver output is longer than the configured qpos joint count")

            if len(joint_angles_rad) < self._qpos_joint_count:
                joint = JointCommand(
                    joint_angles_rad=joint_angles_rad + (0.0,) * (self._qpos_joint_count - len(joint_angles_rad)),
                    joint_velocities_rad_s=joint.joint_velocities_rad_s,
                )

        return build_motion_command_from_target_command(
            timestamp_s=intent.timestamp_s,
            target_command=target,
            joint_command=joint,
            metadata=_metadata_with_qpos_diagnostics(
                {
                    **intent.metadata,
                    "target_discontinuity_threshold_rad": self._discontinuity_threshold_rad,
                    "target_discontinuity_threshold_label": self._discontinuity_threshold_label,
                },
                qpos_before_ik_rad=self._current_qpos_rad,
                ik_output_qpos_rad=joint.joint_angles_rad,
                qpos_discontinuity_norm_rad=discontinuity_norm_rad,
            ),
        )


__all__ = [
    "InputIntentMotionGenerator",
    "build_motion_command_from_input_intent",
    "build_motion_command_from_target_command",
    "TargetToJointMotionGenerator",
    "DEFAULT_TARGET_DISCONTINUITY_THRESHOLD_RAD",
]
