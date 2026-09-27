"""Endpoint metadata wire vocabulary."""

from __future__ import annotations

from typing import Literal, TypedDict

from xpotato_sim.schemas.types import QuaternionWXYZ, Vector3

EndpointControlFrame = Literal["world", "tool"]
ResolvedEndpointFrame = Literal["mujoco_world"]
EndpointVelocityFrame = Literal["mujoco_world"]

ControlFrameResolutionStatus = Literal[
    "world_passthrough",
    "tool_orientation_resolved",
    "tool_orientation_unavailable",
    "invalid_control_frame_defaulted",
]

MotionStatus = Literal["accepted", "scaled", "held"]
EndpointProgressStatus = Literal[
    "not_requested",
    "measurement_unavailable",
    "insufficient_progress",
    "misaligned",
    "progressing",
]


class EndpointMetadata(TypedDict, total=False):
    """Typed vocabulary for the additive endpoint metadata contract.

    The runtime still carries a dict because payload v0 is intentionally open.
    This type describes known fields without making unknown legacy metadata
    invalid or changing serialization.
    """

    # Command intent and endpoint lifecycle.
    desired_endpoint_m: Vector3
    current_tip_position_m: Vector3
    ik_target_endpoint_m: Vector3
    target_position_m: Vector3
    target_rejected: bool
    target_rejection_reason: str | None

    # Frame intent and runtime resolution.
    control_frame: EndpointControlFrame
    requested_control_frame: EndpointControlFrame
    resolved_control_frame: ResolvedEndpointFrame | None
    control_frame_resolution_status: ControlFrameResolutionStatus
    control_frame_resolution_reason: str | None
    local_endpoint_velocity_m_s: Vector3
    resolved_world_endpoint_velocity_m_s: Vector3
    endpoint_velocity_m_s: Vector3
    endpoint_velocity_frame: EndpointVelocityFrame

    # Policy prediction versus MuJoCo measurement.
    endpoint_delta_m: Vector3
    endpoint_delta_requested_m: Vector3
    endpoint_delta_achieved_m: Vector3
    actual_tip_delta_m: Vector3

    # Independent outcome axes.
    motion_status: MotionStatus
    motion_rejection_reason: str | None
    endpoint_progress_status: EndpointProgressStatus
    endpoint_progress_signed_m: float | None
    endpoint_progress_ratio: float | None
    endpoint_progress_direction_cosine: float | None
    endpoint_progress_requested_norm_m: float | None
    endpoint_progress_measured_norm_m: float | None
    endpoint_progress_measurement_available: bool

    # Optional solver-local diagnostic and orientation metadata.
    current_tip_orientation_wxyz: QuaternionWXYZ
    candidate_qpos_rad: tuple[float, ...]
    qpos_before_rad: tuple[float, ...]


__all__ = [
    "ControlFrameResolutionStatus",
    "EndpointControlFrame",
    "EndpointMetadata",
    "EndpointProgressStatus",
    "EndpointVelocityFrame",
    "MotionStatus",
    "ResolvedEndpointFrame",
]
