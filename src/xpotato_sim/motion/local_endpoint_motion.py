from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Protocol

import numpy as np

from xpotato_sim.schemas import JointCommand, MotionCommand, InputIntent

_DEFAULT_FD_EPSILON_RAD = 1e-4
_DEFAULT_DAMPING = 1e-3
_DEFAULT_MAX_QPOS_DELTA_NORM_RAD = 0.2
_DEFAULT_MAX_ENDPOINT_DELTA_PER_TICK_M = 0.01
_DEFAULT_ENDPOINT_MODEL = "mujoco_model_aligned_tip_site"


def _coerce_vector3(name: str, value: object) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    for index, component in enumerate(components):
        if not isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {index}")

    return components


def _coerce_qpos(name: str, value: Sequence[float] | None) -> tuple[float, ...] | None:
    if value is None:
        return None

    qpos = tuple(float(component) for component in value)
    if not qpos:
        return None

    for index, component in enumerate(qpos):
        if not isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {index}")

    return qpos


def _vector_norm(vector: Sequence[float]) -> float:
    return sqrt(sum(float(component) * float(component) for component in vector))


def _scale_vector(vector: tuple[float, float, float], *, limit: float) -> tuple[float, float, float]:
    norm = _vector_norm(vector)
    if norm == 0.0 or norm <= limit:
        return vector

    scale = limit / norm
    return tuple(component * scale for component in vector)


def _resolve_vector3_from_intent(
    intent: InputIntent,
    *,
    key: str,
) -> tuple[float, float, float] | None:
    value = intent.metadata.get(key)
    if value is None:
        return None

    return _coerce_vector3(key, value)


def _resolve_control_frame(intent: InputIntent) -> str:
    control_frame = intent.metadata.get("control_frame", "world")
    if not isinstance(control_frame, str):
        return "world"

    normalized_control_frame = control_frame.strip().lower()
    if normalized_control_frame in {"world", "tool"}:
        return normalized_control_frame

    return "world"


def _resolve_axis_values(intent: InputIntent) -> tuple[float, float, float]:
    axis_values = intent.metadata.get("axis_values")
    if axis_values is not None:
        return _coerce_vector3("axis_values", axis_values)

    values = tuple(float(component) for component in intent.values[:3])
    if len(values) != 3:
        return (0.0, 0.0, 0.0)
    return values


class EndpointKinematics(Protocol):
    def forward(self, qpos_rad: Sequence[float]) -> tuple[float, float, float]:
        ...


def _finite_difference_jacobian(
    qpos_rad: tuple[float, ...],
    *,
    endpoint_kinematics: EndpointKinematics,
    epsilon_rad: float,
) -> np.ndarray:
    base = np.asarray(endpoint_kinematics.forward(qpos_rad), dtype=np.float64)
    jacobian = np.zeros((3, len(qpos_rad)), dtype=np.float64)

    for joint_index in range(len(qpos_rad)):
        perturbed = list(qpos_rad)
        perturbed[joint_index] += epsilon_rad
        perturbed_endpoint = np.asarray(endpoint_kinematics.forward(tuple(perturbed)), dtype=np.float64)
        jacobian[:, joint_index] = (perturbed_endpoint - base) / epsilon_rad

    return jacobian


@dataclass(frozen=True, slots=True)
class LocalEndpointMotionResult:
    motion_command: MotionCommand
    motion_status: str
    motion_rejection_reason: str | None
    qpos_before_rad: tuple[float, ...]
    candidate_qpos_rad: tuple[float, ...]
    qpos_delta_norm_rad: float
    endpoint_delta_requested_m: tuple[float, float, float]
    endpoint_delta_achieved_m: tuple[float, float, float]


class LocalEndpointMotionGenerator:
    def __init__(
        self,
        *,
        endpoint_kinematics: EndpointKinematics,
        endpoint_model: str = _DEFAULT_ENDPOINT_MODEL,
        fd_epsilon_rad: float = _DEFAULT_FD_EPSILON_RAD,
        damping: float = _DEFAULT_DAMPING,
        max_qpos_delta_norm_rad: float = _DEFAULT_MAX_QPOS_DELTA_NORM_RAD,
        max_endpoint_delta_per_tick_m: float = _DEFAULT_MAX_ENDPOINT_DELTA_PER_TICK_M,
    ) -> None:
        if not endpoint_model:
            raise ValueError("endpoint_model must be a non-empty string")
        if not isfinite(fd_epsilon_rad) or fd_epsilon_rad <= 0.0:
            raise ValueError("fd_epsilon_rad must be finite and positive")
        if not isfinite(damping) or damping < 0.0:
            raise ValueError("damping must be finite and non-negative")
        if not isfinite(max_qpos_delta_norm_rad) or max_qpos_delta_norm_rad <= 0.0:
            raise ValueError("max_qpos_delta_norm_rad must be finite and positive")
        if not isfinite(max_endpoint_delta_per_tick_m) or max_endpoint_delta_per_tick_m <= 0.0:
            raise ValueError("max_endpoint_delta_per_tick_m must be finite and positive")

        self._endpoint_kinematics = endpoint_kinematics
        self._endpoint_model = endpoint_model
        self._fd_epsilon_rad = float(fd_epsilon_rad)
        self._damping = float(damping)
        self._max_qpos_delta_norm_rad = float(max_qpos_delta_norm_rad)
        self._max_endpoint_delta_per_tick_m = float(max_endpoint_delta_per_tick_m)
        self._current_qpos_rad: tuple[float, ...] | None = None

    def set_current_qpos_rad(self, current_qpos_rad: Sequence[float] | None) -> None:
        self._current_qpos_rad = _coerce_qpos("current_qpos_rad", current_qpos_rad)

    def _build_holding_command(
        self,
        *,
        intent: InputIntent,
        reason: str,
        qpos_before_rad: tuple[float, ...],
        endpoint_delta_requested_m: tuple[float, float, float],
        motion_status: str = "held",
    ) -> MotionCommand:
        qpos_before = tuple(qpos_before_rad)
        current_tip_position_m = self._endpoint_kinematics.forward(qpos_before)
        desired_endpoint_m = tuple(
            current_tip_position_m[index] + endpoint_delta_requested_m[index]
            for index in range(3)
        )
        metadata = {
            **dict(intent.metadata),
            "local_motion_policy": "finite_difference_jacobian",
            "endpoint_model": self._endpoint_model,
            "motion_status": motion_status,
            "motion_rejection_reason": reason,
            "qpos_before_rad": qpos_before,
            "candidate_qpos_rad": qpos_before,
            "qpos_delta_norm_rad": 0.0,
            "qpos_delta_cap_rad": self._max_qpos_delta_norm_rad,
            "dt_s": float(intent.metadata.get("dt_s", 0.0) or 0.0),
            "current_tip_position_m": current_tip_position_m,
            "desired_endpoint_m": desired_endpoint_m,
            "endpoint_delta_requested_m": endpoint_delta_requested_m,
            "endpoint_delta_m": endpoint_delta_requested_m,
            "endpoint_delta_achieved_m": (0.0, 0.0, 0.0),
        }
        return MotionCommand(
            timestamp_s=intent.timestamp_s,
            joint=JointCommand(joint_angles_rad=qpos_before),
            metadata=metadata,
        )

    def update(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        """既存velocity入力をdtで積分する。metadata labelは方式を変更しない。"""
        return self._update(intent, dt_s, delta_input=False)

    def update_delta(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        """明示world位置増分を既存policyへ通し、要求と制約後の値を分離する。"""
        if isinstance(dt_s, bool) or not isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError("dt_s must be finite and positive")
        if intent.metadata.get("control_frame") != "world":
            raise ValueError("endpoint delta input requires explicit world frame")
        requested = _resolve_vector3_from_intent(intent, key="endpoint_delta_m")
        if requested is None:
            raise ValueError("endpoint delta input requires endpoint_delta_m")
        command = self._update(intent, dt_s, delta_input=True)
        metadata = dict(command.metadata)
        for key in (
            "axis_values", "local_endpoint_speed_m_s", "local_endpoint_velocity_frame",
            "local_endpoint_velocity_m_s", "resolved_world_endpoint_velocity_m_s",
            "endpoint_velocity_m_s", "endpoint_velocity_frame",
        ):
            metadata.pop(key, None)
        metadata.update({
            "intent_kind": "local_endpoint_delta",
            "motion_input_semantics": "endpoint_delta_per_sample/v1",
            "mapped_endpoint_delta_m": requested,
            "motion_policy_v1": {
                "identity": "local_endpoint_dls_bounds/v1",
                "endpoint_model": self._endpoint_model,
                "max_endpoint_delta_norm_m": self._max_endpoint_delta_per_tick_m,
                "max_qpos_delta_norm_rad": self._max_qpos_delta_norm_rad,
                "fd_epsilon_rad": self._fd_epsilon_rad,
                "damping": self._damping,
            },
        })
        return MotionCommand(timestamp_s=command.timestamp_s, target=command.target,
                             joint=command.joint, metadata=metadata)

    def _update(self, intent: InputIntent, dt_s: float, *, delta_input: bool) -> MotionCommand:
        if self._current_qpos_rad is None:
            return MotionCommand(
                timestamp_s=intent.timestamp_s,
                metadata={
                    **dict(intent.metadata),
                    "local_motion_policy": "finite_difference_jacobian",
                    "endpoint_model": self._endpoint_model,
                    "motion_status": "held",
                    "motion_rejection_reason": "current_qpos_unavailable",
                    "qpos_before_rad": None,
                    "candidate_qpos_rad": None,
                    "qpos_delta_norm_rad": 0.0,
                    "qpos_delta_cap_rad": self._max_qpos_delta_norm_rad,
                    "dt_s": float(intent.metadata.get("dt_s", 0.0) or 0.0),
                    "endpoint_delta_requested_m": (0.0, 0.0, 0.0),
                    "endpoint_delta_m": (0.0, 0.0, 0.0),
                    "endpoint_delta_achieved_m": (0.0, 0.0, 0.0),
                },
            )

        current_qpos_rad = self._current_qpos_rad
        if len(current_qpos_rad) != 4:
            return self._build_holding_command(
                intent=intent,
                reason="current_qpos_unsupported_shape",
                qpos_before_rad=current_qpos_rad,
                endpoint_delta_requested_m=(0.0, 0.0, 0.0),
            )

        if delta_input:
            axis_values = (0.0, 0.0, 0.0)
            control_frame = "world"
            local_endpoint_speed_m_s = 0.0
            local_endpoint_velocity_m_s = (0.0, 0.0, 0.0)
            endpoint_velocity_m_s = (0.0, 0.0, 0.0)
        else:
            axis_values = _resolve_axis_values(intent)
            control_frame = _resolve_control_frame(intent)
            if (
                control_frame == "tool"
                and intent.metadata.get("control_frame_resolution_status") != "tool_orientation_resolved"
            ):
                return self._build_holding_command(
                    intent=intent,
                    reason=str(intent.metadata.get("control_frame_resolution_reason", "tool_orientation_unavailable")),
                    qpos_before_rad=current_qpos_rad,
                    endpoint_delta_requested_m=(0.0, 0.0, 0.0),
                )
            local_endpoint_speed_m_s = float(intent.metadata.get("local_endpoint_speed_m_s", 0.0) or 0.0)
            local_endpoint_velocity_m_s = _resolve_vector3_from_intent(intent, key="local_endpoint_velocity_m_s")
            endpoint_velocity_m_s = _resolve_vector3_from_intent(intent, key="resolved_world_endpoint_velocity_m_s")
            if endpoint_velocity_m_s is None:
                endpoint_velocity_m_s = _resolve_vector3_from_intent(intent, key="endpoint_velocity_m_s")
            if local_endpoint_velocity_m_s is None:
                local_endpoint_velocity_m_s = endpoint_velocity_m_s
            if local_endpoint_velocity_m_s is None:
                local_endpoint_velocity_m_s = tuple(component * local_endpoint_speed_m_s for component in axis_values)
            if endpoint_velocity_m_s is None:
                endpoint_velocity_m_s = local_endpoint_velocity_m_s
        if delta_input:
            # 位置増分はsampleごとのworld変位であり、dtを掛ける速度入力ではない。
            if intent.metadata.get("control_frame") != "world":
                raise ValueError("endpoint delta input requires explicit world frame")
            raw_requested_endpoint_delta_m = _resolve_vector3_from_intent(intent, key="endpoint_delta_m")
            if raw_requested_endpoint_delta_m is None:
                raise ValueError("endpoint delta input requires endpoint_delta_m")
        else:
            raw_requested_endpoint_delta_m = tuple(component * dt_s for component in endpoint_velocity_m_s)
        requested_endpoint_delta_m = _scale_vector(
            raw_requested_endpoint_delta_m,
            limit=self._max_endpoint_delta_per_tick_m,
        )

        current_tip_position_m = self._endpoint_kinematics.forward(current_qpos_rad)
        desired_endpoint_m = tuple(
            current_tip_position_m[index] + requested_endpoint_delta_m[index]
            for index in range(3)
        )

        try:
            jacobian = _finite_difference_jacobian(
                current_qpos_rad,
                endpoint_kinematics=self._endpoint_kinematics,
                epsilon_rad=self._fd_epsilon_rad,
            )
            jj_t = jacobian @ jacobian.T
            damping_matrix = np.eye(3, dtype=np.float64) * self._damping
            delta_q = jacobian.T @ np.linalg.solve(jj_t + damping_matrix, np.asarray(requested_endpoint_delta_m, dtype=np.float64))
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            return self._build_holding_command(
                intent=intent,
                reason="local_jacobian_unavailable",
                qpos_before_rad=current_qpos_rad,
                endpoint_delta_requested_m=requested_endpoint_delta_m,
            )

        if not np.all(np.isfinite(delta_q)):
            return self._build_holding_command(
                intent=intent,
                reason="local_candidate_non_finite",
                qpos_before_rad=current_qpos_rad,
                endpoint_delta_requested_m=requested_endpoint_delta_m,
            )

        qpos_delta_norm_rad = float(np.linalg.norm(delta_q))
        motion_status = "accepted"
        motion_rejection_reason: str | None = None
        if requested_endpoint_delta_m != raw_requested_endpoint_delta_m:
            motion_status = "scaled"
        if qpos_delta_norm_rad > self._max_qpos_delta_norm_rad:
            scale = self._max_qpos_delta_norm_rad / qpos_delta_norm_rad
            delta_q = delta_q * scale
            qpos_delta_norm_rad = float(np.linalg.norm(delta_q))
            motion_status = "scaled"

        candidate_qpos_rad = tuple(float(component) for component in (np.asarray(current_qpos_rad, dtype=np.float64) + delta_q))
        if not all(isfinite(component) for component in candidate_qpos_rad):
            return self._build_holding_command(
                intent=intent,
                reason="local_candidate_non_finite",
                qpos_before_rad=current_qpos_rad,
                endpoint_delta_requested_m=requested_endpoint_delta_m,
            )

        candidate_endpoint_m = self._endpoint_kinematics.forward(candidate_qpos_rad)
        endpoint_delta_achieved_m = tuple(
            candidate_endpoint_m[index] - current_tip_position_m[index]
            for index in range(3)
        )

        metadata = {
            **dict(intent.metadata),
            "local_motion_policy": "finite_difference_jacobian",
            "endpoint_model": self._endpoint_model,
            "motion_status": motion_status,
            "motion_rejection_reason": motion_rejection_reason,
            "control_frame": control_frame,
            "requested_control_frame": intent.metadata.get("requested_control_frame", control_frame),
            "resolved_control_frame": intent.metadata.get(
                "resolved_control_frame",
                "mujoco_world" if control_frame == "world" else None,
            ),
            "control_frame_resolution_status": intent.metadata.get(
                "control_frame_resolution_status",
                "world_passthrough" if control_frame == "world" else "tool_orientation_unavailable",
            ),
            "local_endpoint_velocity_frame": intent.metadata.get("local_endpoint_velocity_frame", control_frame),
            "local_endpoint_velocity_m_s": local_endpoint_velocity_m_s,
            "resolved_world_endpoint_velocity_m_s": endpoint_velocity_m_s,
            "qpos_before_rad": current_qpos_rad,
            "candidate_qpos_rad": candidate_qpos_rad,
            "qpos_delta_norm_rad": qpos_delta_norm_rad,
            "qpos_delta_cap_rad": self._max_qpos_delta_norm_rad,
            "dt_s": float(dt_s),
            "axis_values": axis_values,
            "local_endpoint_speed_m_s": local_endpoint_speed_m_s,
            "endpoint_velocity_m_s": endpoint_velocity_m_s,
            "endpoint_velocity_frame": "mujoco_world",
            "endpoint_delta_requested_m": requested_endpoint_delta_m,
            "endpoint_delta_m": requested_endpoint_delta_m,
            "endpoint_delta_achieved_m": endpoint_delta_achieved_m,
            "current_tip_position_m": current_tip_position_m,
            "desired_endpoint_m": desired_endpoint_m,
        }

        return MotionCommand(
            timestamp_s=intent.timestamp_s,
            joint=JointCommand(joint_angles_rad=candidate_qpos_rad),
            metadata=metadata,
        )


__all__ = [
    "EndpointKinematics",
    "LocalEndpointMotionGenerator",
    "LocalEndpointMotionResult",
]
