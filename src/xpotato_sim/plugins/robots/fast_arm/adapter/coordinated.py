"""名前付きassemblyの共同位置更新。接触力学・実機安全性ではない運動学診断経路。"""
from __future__ import annotations
from collections.abc import Mapping
from threading import RLock
import mujoco
import numpy as np
from fast_arm_core.assembly import FastArmAssembly, resolve_assembly_addresses
from fast_arm_core.assembly_model import FastArmAssemblyModel, build_fast_arm_assembly_model
from xpotato_sim.motion import LocalEndpointMotionGenerator
from xpotato_sim.mujoco_backend import snapshot_mujoco_state
from xpotato_sim.schemas import InputIntent, MuJoCoState
from xpotato_sim.schemas.command import JointPositionCommand
from xpotato_sim.schemas.coordinated import CoordinatedSnapshot, EndpointObservation, EndpointVelocity, number
from xpotato_sim.runtime.execution.coordinated import PreparedCoordinatedStep
from xpotato_sim.runtime.control.viewer_motion_policy import (
    DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_DELTA_PER_TICK_M,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
)
from .feasibility import parse_fast_arm_joint_limit_config, default_fast_arm_joint_limits_path


class _SnapshotKinematics:
    """同じsnapshotをコピーしたFK作業域。正本のdataには一切書き込まない。"""
    def __init__(self, model, base, addresses):
        self.model, self.addresses = model, addresses
        self.data = mujoco.MjData(model)
        mujoco.mj_copyData(self.data, model, base)

    def forward(self, qpos_rad):
        if len(qpos_rad) != len(self.addresses.qpos_addresses):
            raise ValueError("named arm qpos shape mismatch")
        self.data.qpos[list(self.addresses.qpos_addresses)] = qpos_rad
        mujoco.mj_forward(self.model, self.data)
        return tuple(float(v) for v in self.data.site_xpos[self.addresses.tip_site_id])


class FastArmAssemblyMotionProvider:
    """全腕を準備して一回で公開する。元のactuator/限界を改変せず、mj_stepは呼ばない。"""
    execution_semantics = "coordinated_joint_position_kinematic/v1"

    def __init__(self, assembly: FastArmAssembly, *, built: FastArmAssemblyModel | None = None) -> None:
        # 保存assembly診断はbareモデルを維持し、登録モデルは共通sceneを明示注入する。
        if built is not None and (type(built) is not FastArmAssemblyModel or built.assembly != assembly):
            raise ValueError("provider scene/assembly mismatch")
        self.built = build_fast_arm_assembly_model(assembly) if built is None else built
        self.assembly = assembly
        self.model = mujoco.MjModel.from_xml_string(self.built.xml.decode(), dict(self.built.assets))
        self.addresses = resolve_assembly_addresses(self.model, assembly)
        self.endpoint_ids = assembly.arm_ids
        self.limits = parse_fast_arm_joint_limit_config(default_fast_arm_joint_limits_path())
        self._data = mujoco.MjData(self.model)
        self._generation = 0
        self._pending: tuple[PreparedCoordinatedStep, object] | None = None
        self._lock = RLock()
        self.reset()

    def _snapshot(self, data, generation) -> CoordinatedSnapshot:
        return CoordinatedSnapshot(self.built.model_sha256, generation, float(data.time),
            self.assembly.joint_names,
            tuple(float(data.qpos[i]) for arm in self.addresses for i in arm.qpos_addresses),
            tuple(EndpointObservation(arm.arm_id, tuple(float(v) for v in data.site_xpos[arm.tip_site_id]))
                  for arm in self.addresses), self.execution_semantics)

    def snapshot(self) -> CoordinatedSnapshot:
        with self._lock:
            return self._snapshot(self._data, self._generation)

    def transport_state(
        self, *, frame_index: int, metadata: Mapping[str, object]
    ) -> MuJoCoState:
        """同じMuJoCo dataを既存transport/viewer stateへ投影する。"""
        if type(frame_index) is not int or frame_index < 0:
            raise ValueError("frame_index must be a non-negative integer")
        if not isinstance(metadata, Mapping):
            raise TypeError("transport metadata must be a mapping")
        with self._lock:
            return snapshot_mujoco_state(
                self.model,
                self._data,
                frame_index=frame_index,
                metadata=metadata,
            )

    def _check_data(self, data) -> None:
        if not all(np.all(np.isfinite(a)) for a in (data.qpos, data.qvel, data.ctrl, data.site_xpos)):
            raise ValueError("nonfinite assembly state")
        if any(int(w.number) > 0 for w in data.warning):
            raise ValueError("MuJoCo warning in candidate state")
        for arm in self.addresses:
            q = tuple(float(data.qpos[i]) for i in arm.qpos_addresses)
            if self.limits.violations_for_qpos(q):
                raise ValueError(f"joint_limit_violation:{arm.arm_id}")

    def preflight(self) -> bool:
        with self._lock:
            self._check_data(self._data)
            # source modelは接触用collision geometryを持たない。物理allowは生成しない。
            return True

    def reset(self) -> None:
        with self._lock:
            self._pending = None
            data = mujoco.MjData(self.model)
            key = int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "home"))
            if key < 0:
                raise ValueError("assembly home keyframe is unavailable")
            mujoco.mj_resetDataKeyframe(self.model, data, key)
            mujoco.mj_forward(self.model, data)
            self._check_data(data)
            self._generation += 1
            self._data = data

    def invalidate(self) -> None:
        with self._lock:
            self._pending = None

    def _arm_candidate(self, base, arm, command, dt_s):
        velocity = np.asarray(command.velocity_m_s)
        if not np.isfinite(np.linalg.norm(velocity)):
            raise ValueError("velocity magnitude overflow")
        if command.control_frame == "tool":
            velocity = np.asarray(base.site_xmat[arm.tip_site_id]).reshape(3, 3) @ velocity
        q = tuple(float(base.qpos[i]) for i in arm.qpos_addresses)
        solver = LocalEndpointMotionGenerator(
            endpoint_kinematics=_SnapshotKinematics(self.model, base, arm),
            endpoint_model="mujoco_named_assembly_tip",
            fd_epsilon_rad=DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
            damping=DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
            max_qpos_delta_norm_rad=DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
            max_endpoint_delta_per_tick_m=DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_DELTA_PER_TICK_M,
        )
        solver.set_current_qpos_rad(q)
        intent = InputIntent(source="coordinated", timestamp_s=float(base.time), values=command.velocity_m_s,
            metadata={"control_frame": "world", "endpoint_velocity_m_s": tuple(float(v) for v in velocity),
                      "resolved_world_endpoint_velocity_m_s": tuple(float(v) for v in velocity)})
        result = solver.update(intent, dt_s)
        if result.joint is None or result.metadata.get("motion_status") not in ("accepted", "scaled"):
            raise ValueError(f"candidate_rejected:{arm.arm_id}:{result.metadata.get('motion_rejection_reason')}")
        candidate = result.joint.joint_angles_rad
        if self.limits.violations_for_qpos(candidate):
            raise ValueError(f"joint_limit_violation:{arm.arm_id}")
        return candidate

    def prepare(self, commands: tuple[EndpointVelocity, ...], dt_s: float) -> PreparedCoordinatedStep:
        with self._lock:
            self._pending = None
            number(dt_s, "dt_s", positive=True)
            if (type(commands) is not tuple or any(type(c) is not EndpointVelocity for c in commands)
                    or len(commands) != len(self.endpoint_ids)
                    or {c.endpoint_id for c in commands} != set(self.endpoint_ids)):
                raise ValueError("commands must cover all named endpoints exactly once")
            self._check_data(self._data)
            before = self.snapshot()
            base = mujoco.MjData(self.model)
            mujoco.mj_copyData(base, self.model, self._data)
            by_id = {c.endpoint_id: c for c in commands}
            candidates = {arm.arm_id: self._arm_candidate(base, arm, by_id[arm.arm_id], dt_s)
                          for arm in self.addresses}
            candidate_data = mujoco.MjData(self.model)
            mujoco.mj_copyData(candidate_data, self.model, base)
            for arm in self.addresses:
                candidate_data.qpos[list(arm.qpos_addresses)] = candidates[arm.arm_id]
            candidate_data.time = float(base.time) + dt_s
            if not np.isfinite(candidate_data.time) or candidate_data.time <= base.time:
                raise ValueError("invalid simulation time advance")
            mujoco.mj_forward(self.model, candidate_data)
            self._check_data(candidate_data)
            predicted = self._snapshot(candidate_data, self._generation + 1)
            joint = JointPositionCommand(timestamp_s=predicted.simulation_time_s,
                                         joint_angles_rad=predicted.joint_positions_rad)
            prepared = PreparedCoordinatedStep(before, joint, predicted, object())
            self._pending = (prepared, candidate_data)
            return prepared

    def commit(self, candidate: PreparedCoordinatedStep) -> CoordinatedSnapshot:
        with self._lock:
            pending, self._pending = self._pending, None
            if pending is None or pending[0] is not candidate or candidate.before != self.snapshot():
                raise ValueError("foreign, consumed, or stale coordinated candidate")
            self._check_data(pending[1])
            if self._snapshot(pending[1], self._generation + 1) != candidate.predicted:
                raise ValueError("candidate modified after preparation")
            self._data = pending[1]
            self._generation += 1
            return self.snapshot()
