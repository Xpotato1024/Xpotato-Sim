from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator


import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import FastArmMuJoCoModelForwardKinematicsSolver
from xpotato_sim.motion import LocalEndpointMotionGenerator
from xpotato_sim.runtime.control.viewer_motion_policy import build_viewer_local_motion_metadata
from xpotato_sim.schemas import InputIntent, JointCommand


class _RecordingEndpointKinematics:
    def __init__(self) -> None:
        self.calls: list[tuple[float, ...]] = []

    def forward(self, qpos_rad):
        qpos = tuple(float(component) for component in qpos_rad)
        self.calls.append(qpos)
        return (qpos[0], qpos[1], qpos[2])


def _rotate_vector_by_quaternion_wxyz(
    vector: tuple[float, float, float],
    quaternion_wxyz: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    w, x, y, z = quaternion_wxyz
    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z

    rot00 = 1.0 - 2.0 * (yy + zz)
    rot01 = 2.0 * (xy - wz)
    rot02 = 2.0 * (xz + wy)
    rot10 = 2.0 * (xy + wz)
    rot11 = 1.0 - 2.0 * (xx + zz)
    rot12 = 2.0 * (yz - wx)
    rot20 = 2.0 * (xz - wy)
    rot21 = 2.0 * (yz + wx)
    rot22 = 1.0 - 2.0 * (xx + yy)

    vx, vy, vz = vector
    return (
        rot00 * vx + rot01 * vy + rot02 * vz,
        rot10 * vx + rot11 * vy + rot12 * vz,
        rot20 * vx + rot21 * vy + rot22 * vz,
    )


def _intent(
    *,
    axis_values: tuple[float, float, float],
    endpoint_velocity_m_s: tuple[float, float, float],
    dt_s: float,
) -> InputIntent:
    return InputIntent(
        source="viewer_keyboard",
        timestamp_s=1.0,
        values=axis_values,
        metadata={
            "intent_kind": "local_endpoint_velocity",
            "input_continuity": "continuous",
            "axis_values": axis_values,
            "endpoint_velocity_m_s": endpoint_velocity_m_s,
            "local_endpoint_speed_m_s": 0.1,
            "local_endpoint_max_delta_m": 0.03,
            "dt_s": dt_s,
        },
    )


def test_local_endpoint_motion_generator_uses_injected_endpoint_kinematics() -> None:
    endpoint_kinematics = _RecordingEndpointKinematics()
    generator = LocalEndpointMotionGenerator(
        endpoint_kinematics=endpoint_kinematics,
        endpoint_model="recording_endpoint_model",
    )
    generator.set_current_qpos_rad((0.0, -1.5707963267948966, 0.0, 0.0))

    command = generator.update(
        _intent(
            axis_values=(1.0, 0.0, 0.0),
            endpoint_velocity_m_s=(0.1, 0.0, 0.0),
            dt_s=1.0 / 60.0,
        ),
        dt_s=1.0 / 60.0,
    )

    assert endpoint_kinematics.calls
    assert command.metadata["endpoint_model"] == "recording_endpoint_model"
    assert command.metadata["local_motion_policy"] == "finite_difference_jacobian"
    assert command.metadata["control_frame"] == "world"
    assert command.metadata["requested_control_frame"] == "world"
    assert command.metadata["resolved_control_frame"] == "mujoco_world"
    assert command.metadata["control_frame_resolution_status"] == "world_passthrough"
    assert command.metadata["local_endpoint_velocity_frame"] == "world"
    assert command.metadata["resolved_world_endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert command.metadata["endpoint_velocity_frame"] == "mujoco_world"
    assert command.metadata["current_tip_position_m"] == pytest.approx((0.0, -1.5707963267948966, 0.0), abs=1e-12)


def test_local_endpoint_motion_generator_does_not_invent_a_pose_when_qpos_is_unavailable() -> None:
    generator = LocalEndpointMotionGenerator(
        endpoint_kinematics=_RecordingEndpointKinematics(),
        endpoint_model="recording_endpoint_model",
    )

    command = generator.update(
        _intent(
            axis_values=(0.0, 0.0, 0.0),
            endpoint_velocity_m_s=(0.0, 0.0, 0.0),
            dt_s=1.0 / 60.0,
        ),
        dt_s=1.0 / 60.0,
    )

    assert command.joint is None
    assert command.metadata["motion_status"] == "held"
    assert command.metadata["motion_rejection_reason"] == "current_qpos_unavailable"
    assert command.metadata["qpos_before_rad"] is None
    assert command.metadata["candidate_qpos_rad"] is None


def test_local_endpoint_motion_generator_matches_mujoco_tip_site_for_representative_qpos() -> None:
    solver = FastArmMuJoCoModelForwardKinematicsSolver()
    generator = LocalEndpointMotionGenerator(
        endpoint_kinematics=solver,
        endpoint_model="mujoco_model_aligned_tip_site",
    )
    qpos = (0.0, -1.5707963267948966, 0.0, 0.0)
    simulator = build_fast_arm_simulator()
    simulator.apply_qpos_command(JointCommand(joint_angles_rad=qpos))
    tip_site = extract_fast_arm_tip_site_endpoint_from_state(simulator.snapshot())

    generator.set_current_qpos_rad(qpos)
    command = generator.update(
        _intent(
            axis_values=(0.0, 0.0, 0.0),
            endpoint_velocity_m_s=(0.0, 0.0, 0.0),
            dt_s=1.0 / 60.0,
        ),
        dt_s=1.0 / 60.0,
    )

    assert solver.forward(qpos) == pytest.approx(tip_site.position_m, abs=1e-9)
    assert command.metadata["current_tip_position_m"] == pytest.approx(tip_site.position_m, abs=1e-9)
    assert command.metadata["endpoint_model"] == "mujoco_model_aligned_tip_site"
    assert command.metadata["endpoint_delta_achieved_m"] == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)


def test_local_endpoint_motion_generator_dt_scales_requested_endpoint_delta() -> None:
    generator = LocalEndpointMotionGenerator(
        endpoint_kinematics=FastArmMuJoCoModelForwardKinematicsSolver(),
        endpoint_model="mujoco_model_aligned_tip_site",
    )
    generator.set_current_qpos_rad((0.0, -1.5707963267948966, 0.0, 0.0))

    fast_command = generator.update(
        _intent(
            axis_values=(0.0, 1.0, 0.0),
            endpoint_velocity_m_s=(0.0, 0.1, 0.0),
            dt_s=1.0 / 60.0,
        ),
        dt_s=1.0 / 60.0,
    )
    slow_command = generator.update(
        _intent(
            axis_values=(0.0, 1.0, 0.0),
            endpoint_velocity_m_s=(0.0, 0.1, 0.0),
            dt_s=1.0 / 30.0,
        ),
        dt_s=1.0 / 30.0,
    )

    assert fast_command.metadata["endpoint_delta_requested_m"][1] == pytest.approx(1.0 / 600.0, abs=1e-12)
    assert slow_command.metadata["endpoint_delta_requested_m"][1] == pytest.approx(1.0 / 300.0, abs=1e-12)
    assert slow_command.metadata["endpoint_delta_requested_m"][1] == pytest.approx(
        fast_command.metadata["endpoint_delta_requested_m"][1] * 2.0,
        abs=1e-12,
    )


def test_viewer_local_motion_metadata_defaults_to_world_frame_without_rotation() -> None:
    metadata = build_viewer_local_motion_metadata(
        {
            "intent_kind": "local_endpoint_velocity",
            "input_continuity": "continuous",
            "axis_values": (1.0, 0.0, 0.0),
            "local_endpoint_speed_m_s": 0.1,
            "local_endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "control_frame": "world",
            "current_tip_orientation_wxyz": (0.0, 0.0, 0.0, 1.0),
        },
        dt_s=1.0 / 60.0,
    )

    assert metadata["control_frame"] == "world"
    assert metadata["local_endpoint_velocity_frame"] == "world"
    assert metadata["local_endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert metadata["resolved_world_endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert metadata["endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert metadata["endpoint_velocity_frame"] == "mujoco_world"
    assert metadata["endpoint_delta_m"] == pytest.approx((1.0 / 600.0, 0.0, 0.0), abs=1e-12)


def test_viewer_local_motion_metadata_rotates_tool_frame_velocity() -> None:
    quaternion_wxyz = (0.7071067811865476, 0.0, 0.0, 0.7071067811865476)
    metadata = build_viewer_local_motion_metadata(
        {
            "intent_kind": "local_endpoint_velocity",
            "input_continuity": "continuous",
            "axis_values": (1.0, 0.0, 0.0),
            "local_endpoint_speed_m_s": 0.1,
            "local_endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "control_frame": "tool",
            "current_tip_orientation_wxyz": quaternion_wxyz,
        },
        dt_s=1.0 / 60.0,
    )

    expected_world_velocity = _rotate_vector_by_quaternion_wxyz((0.1, 0.0, 0.0), quaternion_wxyz)
    assert metadata["control_frame"] == "tool"
    assert metadata["requested_control_frame"] == "tool"
    assert metadata["resolved_control_frame"] == "mujoco_world"
    assert metadata["control_frame_resolution_status"] == "tool_orientation_resolved"
    assert metadata["local_endpoint_velocity_frame"] == "tool"
    assert metadata["local_endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert metadata["resolved_world_endpoint_velocity_m_s"] == pytest.approx(expected_world_velocity, abs=1e-12)
    assert metadata["endpoint_velocity_m_s"] == pytest.approx(expected_world_velocity, abs=1e-12)
    assert metadata["endpoint_delta_m"] == pytest.approx(
        tuple(component / 60.0 for component in expected_world_velocity),
        abs=1e-12,
    )


@pytest.mark.parametrize(
    ("orientation", "reason"),
    [
        (None, "tip_orientation_missing"),
        ((1.0, 2.0, 3.0), "tip_orientation_shape_invalid"),
        ((float("nan"), 0.0, 0.0, 1.0), "tip_orientation_non_finite"),
        ((0.0, 0.0, 0.0, 0.0), "tip_orientation_zero_norm"),
        (7.0, "tip_orientation_shape_invalid"),
        ("invalid", "tip_orientation_shape_invalid"),
        (b"invalid", "tip_orientation_shape_invalid"),
        (object(), "tip_orientation_shape_invalid"),
    ],
)
def test_viewer_local_motion_metadata_holds_tool_resolution_failure(
    orientation: object,
    reason: str,
) -> None:
    metadata = build_viewer_local_motion_metadata(
        {
            "axis_values": (1.0, 0.0, 0.0),
            "local_endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "control_frame": "tool",
            "current_tip_orientation_wxyz": orientation,
            "endpoint_velocity_m_s": (0.2, 0.0, 0.0),
            "resolved_world_endpoint_velocity_m_s": (0.2, 0.0, 0.0),
            "endpoint_velocity_frame": "mujoco_world",
            "endpoint_delta_m": (0.003, 0.0, 0.0),
        },
        dt_s=1.0 / 60.0,
    )

    assert metadata["requested_control_frame"] == "tool"
    assert metadata["resolved_control_frame"] is None
    assert metadata["control_frame_resolution_status"] == "tool_orientation_unavailable"
    assert metadata["control_frame_resolution_reason"] == reason
    assert "resolved_world_endpoint_velocity_m_s" not in metadata
    assert "endpoint_velocity_m_s" not in metadata
    assert "endpoint_delta_m" not in metadata
    assert "endpoint_velocity_frame" not in metadata
    assert "current_tip_orientation_wxyz" not in metadata

    generator = LocalEndpointMotionGenerator(
        endpoint_kinematics=_RecordingEndpointKinematics(),
        endpoint_model="recording_endpoint_model",
    )
    current_qpos = (0.0, -1.5707963267948966, 0.0, 0.0)
    generator.set_current_qpos_rad(current_qpos)
    command = generator.update(
        InputIntent(
            source="viewer_keyboard",
            timestamp_s=1.0,
            values=(1.0, 0.0, 0.0),
            metadata=metadata,
        ),
        dt_s=1.0 / 60.0,
    )

    assert command.metadata["motion_status"] == "held"
    assert command.metadata["motion_rejection_reason"] == reason
    assert command.metadata["candidate_qpos_rad"] == current_qpos
    assert command.metadata["resolved_control_frame"] is None


def test_invalid_control_frame_defaults_to_world_explicitly() -> None:
    metadata = build_viewer_local_motion_metadata(
        {
            "local_endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "control_frame": "camera",
        },
        dt_s=1.0 / 60.0,
    )

    assert metadata["control_frame"] == "world"
    assert metadata["requested_control_frame"] == "world"
    assert metadata["resolved_control_frame"] == "mujoco_world"
    assert metadata["control_frame_resolution_status"] == "invalid_control_frame_defaulted"


def test_local_endpoint_motion_generator_scales_large_dt_boundary_motion() -> None:
    generator = LocalEndpointMotionGenerator(
        endpoint_kinematics=FastArmMuJoCoModelForwardKinematicsSolver(),
        endpoint_model="mujoco_model_aligned_tip_site",
    )
    generator.set_current_qpos_rad((0.0, -1.5707963267948966, 0.0, 0.0))

    command = generator.update(
        _intent(
            axis_values=(1.0, 0.0, 0.0),
            endpoint_velocity_m_s=(0.1, 0.0, 0.0),
            dt_s=1.0,
        ),
        dt_s=1.0,
    )

    assert command.metadata["motion_status"] == "scaled"
    assert command.metadata["endpoint_delta_requested_m"] == pytest.approx((0.01, 0.0, 0.0), abs=1e-12)
    assert command.metadata["motion_rejection_reason"] is None
