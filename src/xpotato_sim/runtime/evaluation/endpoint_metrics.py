from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from xpotato_sim.kinematics import ForwardKinematicsSolver
from xpotato_sim.mujoco_backend.endpoint_extraction import RuntimeMuJoCoEndpointEvaluation
from xpotato_sim.runtime.evaluation.kinematics import RuntimeForwardKinematicsEvaluation, evaluate_fk_endpoint_from_qpos
from xpotato_sim.runtime.composition.robot_bundle import EndpointPoseObservation, EndpointPoseProvider
from xpotato_sim.schemas import MotionCommand, MuJoCoState, Vector3
from xpotato_sim.transport.base import StatePublisher

_METRICS_UNIT = "meter"
_DESIRED_ENDPOINT_COORDINATE_FRAME = "command-side endpoint frame"
_FK_ENDPOINT_COORDINATE_FRAME = "solver-defined frame"
_SITE_ENDPOINT_COORDINATE_FRAME = "MuJoCo world / scene frame"
_FRAME_MISMATCH_NOTE = "diagnostic only; FK and site endpoints are not transformed or auto-aligned"

_EndpointEvaluationObservation = EndpointPoseObservation | RuntimeMuJoCoEndpointEvaluation


def _coerce_vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    return components


def _coerce_joint_angles(name: str, values: Sequence[float]) -> tuple[float, ...]:
    joint_angles_rad = tuple(float(value) for value in values)
    if not joint_angles_rad:
        raise ValueError(f"{name} must contain at least one joint angle")

    return joint_angles_rad


def compute_vector_error_m(*, start_m: Sequence[float], end_m: Sequence[float]) -> Vector3:
    start_vector_m = _coerce_vector3("start_m", start_m)
    end_vector_m = _coerce_vector3("end_m", end_m)
    return tuple(
        end_component - start_component
        for start_component, end_component in zip(start_vector_m, end_vector_m, strict=True)
    )


def compute_error_norm_m(vector_m: Sequence[float]) -> float:
    coerced_vector_m = _coerce_vector3("vector_m", vector_m)
    return math.sqrt(sum(component * component for component in coerced_vector_m))


@dataclass(frozen=True, slots=True)
class RuntimeEndpointEvaluationMetrics:
    """Endpoint metrics with legacy ``site_*`` payload names preserved."""

    desired_endpoint_m: Vector3
    qpos_like_joint_angles_rad: tuple[float, ...]
    fk_endpoint_m: Vector3
    site_endpoint_m: Vector3
    fk_evaluation: RuntimeForwardKinematicsEvaluation
    site_evaluation: _EndpointEvaluationObservation
    desired_to_fk_error_vector_m: Vector3
    desired_to_site_error_vector_m: Vector3
    fk_to_site_error_vector_m: Vector3
    desired_to_fk_error_norm_m: float
    desired_to_site_error_norm_m: float
    fk_to_site_error_norm_m: float
    unit: str = _METRICS_UNIT
    desired_endpoint_coordinate_frame: str = _DESIRED_ENDPOINT_COORDINATE_FRAME
    fk_endpoint_coordinate_frame: str = _FK_ENDPOINT_COORDINATE_FRAME
    site_endpoint_coordinate_frame: str = _SITE_ENDPOINT_COORDINATE_FRAME
    frame_mismatch_note: str = _FRAME_MISMATCH_NOTE


def runtime_endpoint_evaluation_metrics_to_payload(metrics: RuntimeEndpointEvaluationMetrics) -> dict[str, object]:
    return {
        "desired_endpoint_m": list(metrics.desired_endpoint_m),
        "qpos_like_joint_angles_rad": list(metrics.qpos_like_joint_angles_rad),
        "fk_endpoint_m": list(metrics.fk_endpoint_m),
        "site_endpoint_m": list(metrics.site_endpoint_m),
        "desired_to_fk_error_vector_m": list(metrics.desired_to_fk_error_vector_m),
        "desired_to_site_error_vector_m": list(metrics.desired_to_site_error_vector_m),
        "fk_to_site_error_vector_m": list(metrics.fk_to_site_error_vector_m),
        "desired_to_fk_error_norm_m": metrics.desired_to_fk_error_norm_m,
        "desired_to_site_error_norm_m": metrics.desired_to_site_error_norm_m,
        "fk_to_site_error_norm_m": metrics.fk_to_site_error_norm_m,
        "unit": metrics.unit,
        "desired_endpoint_coordinate_frame": metrics.desired_endpoint_coordinate_frame,
        "fk_endpoint_coordinate_frame": metrics.fk_endpoint_coordinate_frame,
        "site_endpoint_coordinate_frame": metrics.site_endpoint_coordinate_frame,
        "frame_mismatch_note": metrics.frame_mismatch_note,
    }


def _require_meter_unit(name: str, unit: str) -> None:
    if unit != _METRICS_UNIT:
        raise ValueError(f"{name} must use meter units")


def build_runtime_endpoint_evaluation_metrics(
    *,
    desired_endpoint_m: Sequence[float] | None,
    fk_evaluation: RuntimeForwardKinematicsEvaluation | None,
    site_evaluation: _EndpointEvaluationObservation | None,
    qpos_like_joint_angles_rad: Sequence[float] | None = None,
) -> RuntimeEndpointEvaluationMetrics:
    if desired_endpoint_m is None:
        raise ValueError("desired_endpoint_m is required")
    if fk_evaluation is None:
        raise ValueError("fk_evaluation is required")
    if site_evaluation is None:
        raise ValueError("site_evaluation is required")

    _require_meter_unit("fk_evaluation.unit", fk_evaluation.unit)
    _require_meter_unit(
        "site_evaluation.unit",
        getattr(site_evaluation, "unit", _METRICS_UNIT),
    )
    if site_evaluation.position_m is None:
        raise ValueError("site_evaluation.position_m is required")

    desired_endpoint_vector_m = _coerce_vector3("desired_endpoint_m", desired_endpoint_m)
    fk_endpoint_vector_m = _coerce_vector3("fk_evaluation.endpoint_m", fk_evaluation.endpoint_m)
    site_endpoint_vector_m = _coerce_vector3("site_evaluation.position_m", site_evaluation.position_m)

    if qpos_like_joint_angles_rad is None:
        qpos_like_joint_angles_rad = fk_evaluation.input_joint_angles_rad

    qpos_like_joint_angles_tuple_rad = _coerce_joint_angles(
        "qpos_like_joint_angles_rad",
        qpos_like_joint_angles_rad,
    )

    desired_to_fk_error_vector_m = compute_vector_error_m(
        start_m=desired_endpoint_vector_m,
        end_m=fk_endpoint_vector_m,
    )
    desired_to_site_error_vector_m = compute_vector_error_m(
        start_m=desired_endpoint_vector_m,
        end_m=site_endpoint_vector_m,
    )
    fk_to_site_error_vector_m = compute_vector_error_m(
        start_m=fk_endpoint_vector_m,
        end_m=site_endpoint_vector_m,
    )

    return RuntimeEndpointEvaluationMetrics(
        desired_endpoint_m=desired_endpoint_vector_m,
        qpos_like_joint_angles_rad=qpos_like_joint_angles_tuple_rad,
        fk_endpoint_m=fk_endpoint_vector_m,
        site_endpoint_m=site_endpoint_vector_m,
        fk_evaluation=fk_evaluation,
        site_evaluation=site_evaluation,
        desired_to_fk_error_vector_m=desired_to_fk_error_vector_m,
        desired_to_site_error_vector_m=desired_to_site_error_vector_m,
        fk_to_site_error_vector_m=fk_to_site_error_vector_m,
        desired_to_fk_error_norm_m=compute_error_norm_m(desired_to_fk_error_vector_m),
        desired_to_site_error_norm_m=compute_error_norm_m(desired_to_site_error_vector_m),
        fk_to_site_error_norm_m=compute_error_norm_m(fk_to_site_error_vector_m),
    )


def _resolve_desired_endpoint_m(
    *,
    motion_command: MotionCommand | None,
    state: MuJoCoState,
) -> Sequence[float] | None:
    if motion_command is not None:
        target = motion_command.target
        if target is not None:
            desired_endpoint_m = getattr(target, "desired_endpoint_m", None)
            if desired_endpoint_m is not None:
                return desired_endpoint_m

        desired_endpoint_m = motion_command.metadata.get("desired_endpoint_m")
        if desired_endpoint_m is not None:
            return desired_endpoint_m

        desired_endpoint_m = motion_command.metadata.get("target_position_m")
        if desired_endpoint_m is not None:
            return desired_endpoint_m

    # `target_position_m` is compatibility fallback only; prefer command-side
    # desired endpoint metadata before falling back to viewer feedback.
    desired_endpoint_m = state.metadata.get("desired_endpoint_m")
    if desired_endpoint_m is not None:
        return desired_endpoint_m

    desired_endpoint_m = state.metadata.get("target_position_m")
    if desired_endpoint_m is not None:
        return desired_endpoint_m

    return state.target_position_m


def build_runtime_endpoint_evaluation_payload(
    *,
    desired_endpoint_m: Sequence[float] | None,
    fk_evaluation: RuntimeForwardKinematicsEvaluation | None,
    site_evaluation: _EndpointEvaluationObservation | None,
    qpos_like_joint_angles_rad: Sequence[float] | None = None,
) -> dict[str, object] | None:
    try:
        metrics = build_runtime_endpoint_evaluation_metrics(
            desired_endpoint_m=desired_endpoint_m,
            fk_evaluation=fk_evaluation,
            site_evaluation=site_evaluation,
            qpos_like_joint_angles_rad=qpos_like_joint_angles_rad,
        )
    except ValueError:
        return None

    return runtime_endpoint_evaluation_metrics_to_payload(metrics)


def build_runtime_endpoint_evaluation_payload_from_state(
    *,
    state: MuJoCoState,
    motion_command: MotionCommand | None,
    fk_solver: ForwardKinematicsSolver,
    endpoint_pose_provider: EndpointPoseProvider,
    solver_joint_count: int | None = None,
) -> dict[str, object] | None:
    if motion_command is None or motion_command.joint is None:
        return None

    desired_endpoint_m = _resolve_desired_endpoint_m(motion_command=motion_command, state=state)
    if desired_endpoint_m is None:
        return None

    try:
        fk_evaluation = evaluate_fk_endpoint_from_qpos(
            fk_solver,
            motion_command.joint.joint_angles_rad,
            solver_joint_count=solver_joint_count,
        )
        site_evaluation = endpoint_pose_provider.observe_endpoint_pose(state)
    except ValueError:
        return None

    return build_runtime_endpoint_evaluation_payload(
        desired_endpoint_m=desired_endpoint_m,
        fk_evaluation=fk_evaluation,
        site_evaluation=site_evaluation,
        qpos_like_joint_angles_rad=motion_command.joint.joint_angles_rad,
    )


class _CommandSource(Protocol):
    last_command: MotionCommand | None


@dataclass(slots=True)
class EndpointEvaluationStatePublisher:
    publisher: StatePublisher
    simulator: _CommandSource
    fk_solver: ForwardKinematicsSolver
    endpoint_pose_provider: EndpointPoseProvider
    solver_joint_count: int | None = None

    def _annotate_state(self, state: MuJoCoState) -> MuJoCoState:
        if "endpoint_evaluation" in state.metadata:
            return state

        endpoint_evaluation = build_runtime_endpoint_evaluation_payload_from_state(
            state=state,
            motion_command=self.simulator.last_command,
            fk_solver=self.fk_solver,
            endpoint_pose_provider=self.endpoint_pose_provider,
            solver_joint_count=self.solver_joint_count,
        )
        if endpoint_evaluation is None:
            return state

        metadata = dict(state.metadata)
        metadata["endpoint_evaluation"] = endpoint_evaluation
        return replace(state, metadata=metadata)

    async def publish(self, state: MuJoCoState) -> None:
        await self.publisher.publish(self._annotate_state(state))


def build_endpoint_evaluation_state_publisher(
    publisher: StatePublisher,
    *,
    simulator: _CommandSource,
    fk_solver: ForwardKinematicsSolver,
    endpoint_pose_provider: EndpointPoseProvider,
    initial_state: MuJoCoState,
    solver_joint_count: int | None = None,
) -> EndpointEvaluationStatePublisher:
    validate_endpoint_pose_provider_state(
        endpoint_pose_provider=endpoint_pose_provider,
        state=initial_state,
    )
    return EndpointEvaluationStatePublisher(
        publisher=publisher,
        simulator=simulator,
        fk_solver=fk_solver,
        endpoint_pose_provider=endpoint_pose_provider,
        solver_joint_count=solver_joint_count,
    )


def validate_endpoint_pose_provider_state(
    *,
    endpoint_pose_provider: EndpointPoseProvider,
    state: MuJoCoState,
) -> EndpointPoseObservation:
    try:
        observation = endpoint_pose_provider.observe_endpoint_pose(state)
    except (TypeError, ValueError) as exc:
        raise ValueError("endpoint pose provider failed to resolve the selected endpoint") from exc
    if observation.position_m is None:
        raise ValueError("endpoint pose provider did not resolve an endpoint position")
    _coerce_vector3("endpoint pose provider position_m", observation.position_m)
    return observation


__all__ = [
    "EndpointEvaluationStatePublisher",
    "RuntimeEndpointEvaluationMetrics",
    "build_endpoint_evaluation_state_publisher",
    "build_runtime_endpoint_evaluation_payload",
    "build_runtime_endpoint_evaluation_payload_from_state",
    "build_runtime_endpoint_evaluation_metrics",
    "compute_error_norm_m",
    "compute_vector_error_m",
    "runtime_endpoint_evaluation_metrics_to_payload",
]
