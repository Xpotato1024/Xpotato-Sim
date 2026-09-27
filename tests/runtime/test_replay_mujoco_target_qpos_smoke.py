from __future__ import annotations

import asyncio

import pytest

from xpotato_sim.mujoco_backend import snapshot_mujoco_state
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.replay_mujoco_pipeline import build_replay_mujoco_pipeline
from xpotato_sim.schemas import JointCommand, MotionCommand, RawInputFrame
from xpotato_sim.transport import mujoco_state_to_payload


class _ReplayJointMotionGenerator:
    def __init__(self, joint_angles_rad: tuple[float, ...]) -> None:
        self._joint_angles_rad = joint_angles_rad

    def update(self, intent, dt_s):  # noqa: ANN001
        _ = dt_s
        return MotionCommand(
            timestamp_s=intent.timestamp_s,
            joint=JointCommand(joint_angles_rad=self._joint_angles_rad),
            metadata=dict(intent.metadata),
        )


def test_replay_dry_run_smoke_keeps_target_feedback_separate_from_qpos_update() -> None:
    target_position_m = (0.4, 0.5, 0.6)
    frame = RawInputFrame(
        source="replay",
        timestamp_s=0.25,
        metadata={
            "smoke": "R6-E-P4",
            "target_position_m": target_position_m,
        },
    )
    joint_angles_rad = (0.1, -0.2, 0.3, -0.4)
    pipeline = build_replay_mujoco_pipeline(
        frames=(frame,),
        config=RuntimeConfig(robot_profile_id="fast_arm"),
        loop=False,
        robot_bundle=resolve_robot_bundle("fast_arm"),
    )
    pipeline.motion_generator = _ReplayJointMotionGenerator(joint_angles_rad)

    state = asyncio.run(pipeline.run_once(dt_s=1.0 / 60.0))

    assert state.frame_index == 1
    assert state.target_position_m is None
    assert state.qpos[:4] == pytest.approx(joint_angles_rad)
    assert pipeline.simulator.last_command is not None
    assert pipeline.simulator.last_command.joint == JointCommand(joint_angles_rad=joint_angles_rad)
    assert pipeline.simulator.last_command.target is None

    smoke_state = snapshot_mujoco_state(
        pipeline.simulator.model,
        pipeline.simulator.data,
        frame_index=state.frame_index,
        target_position_m=target_position_m,
        metadata={"smoke": frame.metadata["smoke"]},
    )
    payload = mujoco_state_to_payload(smoke_state)

    assert payload["frame_index"] == 1
    assert payload["qpos"][:4] == pytest.approx(joint_angles_rad)
    assert payload["target_position_m"] == list(target_position_m)
