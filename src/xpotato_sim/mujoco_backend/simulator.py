from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.mujoco_backend.model_info import inspect_mujoco_model
from xpotato_sim.mujoco_backend.model_loader import (
    ModelResourceBundle,
    load_mujoco_model,
    load_mujoco_model_from_xml_resources,
    reset_mujoco_data_to_initial_state,
)
from xpotato_sim.mujoco_backend.snapshot import snapshot_mujoco_state
from xpotato_sim.schemas import JointCommand, JointPositionCommand
from xpotato_sim.schemas import MotionCommand, MuJoCoState


@dataclass(slots=True)
class HeadlessMuJoCoSimulator:
    model: object
    data: object
    model_path: Path
    _frame_index: int = 0
    _last_dt_s: float | None = None
    _last_command: MotionCommand | None = None
    _last_joint_position_command: JointPositionCommand | None = None
    _pending_joint_position_command: JointPositionCommand | None = None
    initial_keyframe_name: str | None = None
    _command_group: tuple[tuple[int, ...], tuple[int, ...]] | None = None

    @classmethod
    def from_model_path(
        cls,
        model_path: str | Path | ModelResourceBundle,
        *,
        initial_keyframe_name: str | None = None,
    ) -> "HeadlessMuJoCoSimulator":
        bundle = load_mujoco_model(
            model_path,
            initial_keyframe_name=initial_keyframe_name,
        )
        return cls(
            model=bundle.model,
            data=bundle.data,
            model_path=bundle.model_path,
            initial_keyframe_name=initial_keyframe_name,
        )

    @classmethod
    def from_xml_resources(
        cls,
        model_xml: bytes,
        *,
        assets: dict[str, bytes],
        logical_model_path: str | Path,
        initial_keyframe_name: str | None = None,
    ) -> "HeadlessMuJoCoSimulator":
        bundle = load_mujoco_model_from_xml_resources(
            model_xml,
            assets=assets,
            logical_model_path=logical_model_path,
            initial_keyframe_name=initial_keyframe_name,
        )
        return cls(
            model=bundle.model,
            data=bundle.data,
            model_path=bundle.model_path,
            initial_keyframe_name=initial_keyframe_name,
        )

    def apply_joint_position_command(
        self, command: JointPositionCommand
    ) -> None:
        if not isinstance(command, JointPositionCommand):
            raise TypeError(
                "joint-position backend requires JointPositionCommand"
            )
        self._last_command = None
        self._last_joint_position_command = command
        self._pending_joint_position_command = command

    def record_motion_command_envelope(
        self, command: MotionCommand
    ) -> None:
        if not isinstance(command, MotionCommand):
            raise TypeError(
                "motion diagnostics boundary requires MotionCommand"
            )
        self._last_command = command

    def apply_qpos_command(self, joint_command: JointCommand) -> None:
        """qpos command を直接受け取り、backend state に反映する。"""

        self._apply_joint_command(joint_command)

    def reset(self) -> None:
        reset_mujoco_data_to_initial_state(
            self.model,
            self.data,
            model_path=self.model_path,
            initial_keyframe_name=self.initial_keyframe_name,
        )
        self._frame_index = 0
        self._last_dt_s = None
        self._last_command = None
        self._last_joint_position_command = None
        self._pending_joint_position_command = None

    @property
    def last_command(self) -> MotionCommand | None:
        return self._last_command

    @property
    def last_joint_position_command(
        self,
    ) -> JointPositionCommand | None:
        return self._last_joint_position_command

    @property
    def last_dt_s(self) -> float | None:
        return self._last_dt_s

    def _import_mujoco(self) -> object:
        import mujoco

        return mujoco

    def bind_joint_position_group(self, joint_names: tuple[str, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """sceneの一部へ指令する場合だけ、名前順をscalar qpos/dof addressへ固定する。"""
        if type(joint_names) is not tuple or not joint_names or len(set(joint_names)) != len(joint_names):
            raise ValueError("joint command group requires unique explicit names")
        mujoco = self._import_mujoco()
        qpos, dofs = [], []
        for name in joint_names:
            if type(name) is not str or not name:
                raise ValueError("joint command group names must be nonempty strings")
            index = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name))
            if index < 0 or int(self.model.jnt_type[index]) not in (
                int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)
            ):
                raise ValueError("joint command group requires known scalar joints")
            qpos.append(int(self.model.jnt_qposadr[index]))
            dofs.append(int(self.model.jnt_dofadr[index]))
        group = (tuple(qpos), tuple(dofs))
        if self._command_group is not None and self._command_group != group:
            raise ValueError("joint command group cannot change after binding")
        if self._frame_index or self._pending_joint_position_command is not None:
            raise ValueError("joint command group must be bound before execution")
        self._command_group = group
        return group

    def _resolve_joint_qpos_addresses(self) -> tuple[int, ...]:
        if self._command_group is not None:
            return self._command_group[0]
        mujoco = self._import_mujoco()
        joint_names = inspect_mujoco_model(self.model).joint_names

        qpos_addresses: list[int] = []
        for joint_name in joint_names:
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            if joint_id < 0:
                raise ValueError(f"unknown joint name in model: {joint_name}")

            qpos_address = int(self.model.jnt_qposadr[joint_id])
            qpos_addresses.append(qpos_address)

        return tuple(qpos_addresses)

    def _apply_joint_command(self, joint_command: JointCommand) -> None:
        if joint_command.joint_velocities_rad_s:
            raise ValueError("joint velocities are not supported in this backend step")

        joint_angles = tuple(float(value) for value in joint_command.joint_angles_rad)
        qpos_addresses = self._resolve_joint_qpos_addresses()

        if joint_angles and len(joint_angles) != len(qpos_addresses):
            raise ValueError(
                "joint command length does not match model qpos contract: "
                f"expected {len(qpos_addresses)}, got {len(joint_angles)}"
            )

        if not joint_angles:
            return

        for qpos_address, angle in zip(qpos_addresses, joint_angles, strict=True):
            self.data.qpos[qpos_address] = angle

        # A joint-position command replaces the position state.  Retaining the
        # velocity from the previous MuJoCo step would integrate a stale
        # qpos/qvel pair on the next step and can drive the model into
        # BADQACC recovery.  This backend has no joint-velocity command
        # contract, so a direct qpos application starts from zero velocity.
        if self._command_group is None:
            self.data.qvel[:] = 0.0
        else:
            # 非指令対象（cube freejoint等）の運動状態を消さない。
            for dof in self._command_group[1]:
                self.data.qvel[dof] = 0.0
        self._import_mujoco().mj_forward(self.model, self.data)

    def step(self, dt_s: float) -> None:
        mujoco = self._import_mujoco()

        if dt_s <= 0.0:
            raise ValueError("dt_s must be positive")

        if self._pending_joint_position_command is not None:
            self._apply_joint_command(
                JointCommand(
                    joint_angles_rad=(
                        self._pending_joint_position_command.joint_angles_rad
                    )
                )
            )

        self.model.opt.timestep = dt_s
        mujoco.mj_step(self.model, self.data)

        if self._pending_joint_position_command is not None:
            self._apply_joint_command(
                JointCommand(
                    joint_angles_rad=(
                        self._pending_joint_position_command.joint_angles_rad
                    )
                )
            )

        self._last_dt_s = dt_s
        self._frame_index += 1

    def snapshot(self) -> MuJoCoState:
        return snapshot_mujoco_state(
            self.model,
            self.data,
            frame_index=self._frame_index,
        )
