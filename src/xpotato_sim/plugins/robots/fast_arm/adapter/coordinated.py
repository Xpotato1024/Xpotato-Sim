"""名前付きassemblyの共同位置更新。接触力学・実機安全性ではない運動学診断経路。"""
from __future__ import annotations
from collections.abc import Mapping
from threading import RLock
import mujoco
import numpy as np
from fast_arm_core.assembly import FastArmAssembly, resolve_assembly_addresses
from fast_arm_core.assembly_model import FastArmAssemblyModel, build_fast_arm_assembly_model
from xpotato_sim.motion import LocalEndpointMotionGenerator
from xpotato_sim.mujoco_backend.snapshot import _read_synchronized_mujoco_state, _SnapshotLayout
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
from xpotato_sim.runtime.scene.contracts import ComposedObjectScene
from xpotato_sim.runtime.scene.measurement import SceneGeometryObserver
from xpotato_sim.runtime.composition.robot_model import ModelStateSample
from .feasibility import parse_fast_arm_joint_limit_config, default_fast_arm_joint_limits_path


class _SnapshotKinematics:
    """同じsnapshotをコピーしたFK作業域。正本のdataには一切書き込まない。"""
    def __init__(self, model, base, addresses):
        self.model, self.addresses = model, addresses
        self.data = mujoco.MjData(model)
        self.load(base)

    def load(self, base):
        """呼出ごとに同じpre-stepから再読込みし、前の腕・試行を混入しない。"""
        mujoco.mj_copyData(self.data, self.model, base)

    def forward(self, qpos_rad):
        if len(qpos_rad) != len(self.addresses.qpos_addresses):
            raise ValueError("named arm qpos shape mismatch")
        self.data.qpos[list(self.addresses.qpos_addresses)] = qpos_rad
        # FKのsite位置だけが必要。接触solver/actuator計算は候補worldの積分側で行う。
        mujoco.mj_kinematics(self.model, self.data)
        return tuple(float(v) for v in self.data.site_xpos[self.addresses.tip_site_id])


class FastArmAssemblyMotionProvider:
    """全腕を準備して一回で公開する。元のactuator/限界を改変せず、mj_stepは呼ばない。"""
    execution_semantics = "coordinated_joint_position_kinematic/v1"

    def __init__(self, assembly: FastArmAssembly, *, built: FastArmAssemblyModel | None = None, object_scene: ComposedObjectScene | None = None) -> None:
        # 保存assembly診断はbareモデルを維持し、登録モデルは共通sceneを明示注入する。
        if built is not None and (type(built) is not FastArmAssemblyModel or built.assembly != assembly):
            raise ValueError("provider scene/assembly mismatch")
        self.built = build_fast_arm_assembly_model(assembly) if built is None else built
        self.assembly = assembly
        self.model = mujoco.MjModel.from_xml_string(self.built.xml.decode(), dict(self.built.assets))
        self.addresses = resolve_assembly_addresses(self.model, assembly)
        self._snapshot_layout = _SnapshotLayout.prepare(self.model)
        self.endpoint_ids = assembly.arm_ids
        self.limits = parse_fast_arm_joint_limit_config(default_fast_arm_joint_limits_path())
        self._data = mujoco.MjData(self.model)
        self._generation = 0
        self._snapshot_cache: CoordinatedSnapshot | None = None
        self._pending: tuple[PreparedCoordinatedStep, object] | None = None
        self._lock = RLock()
        self._scene_observer = None if object_scene is None else SceneGeometryObserver(self.model, object_scene, self.built.model_sha256)
        self.reset()
        # private作業域はmodel lifetimeで確保し、tickごとの大容量MjData確保を避ける。
        self._base_data = mujoco.MjData(self.model)
        self._planning_data = mujoco.MjData(self.model)
        self._candidate_data = mujoco.MjData(self.model)
        self._kinematics = {arm.arm_id: _SnapshotKinematics(self.model, self._data, arm)
                            for arm in self.addresses}

    @property
    def scene_manifest(self):
        """固定配置identityを公開し、Environmentが別モデルをresetしないようにする。"""
        return None if self._scene_observer is None else self._scene_observer.scene.manifest

    def _snapshot(self, data, generation) -> CoordinatedSnapshot:
        return CoordinatedSnapshot(self.built.model_sha256, generation, float(data.time),
            self.assembly.joint_names,
            tuple(float(data.qpos[i]) for arm in self.addresses for i in arm.qpos_addresses),
            tuple(EndpointObservation(arm.arm_id, tuple(float(v) for v in data.site_xpos[arm.tip_site_id]))
                  for arm in self.addresses), self.execution_semantics)

    def snapshot(self) -> CoordinatedSnapshot:
        with self._lock:
            # 全fieldがtuple/scalarのfrozen snapshotだけを同generationで共有する。
            if self._snapshot_cache is None:
                self._snapshot_cache = self._snapshot(self._data, self._generation)
            return self._snapshot_cache

    def transport_state(
        self, *, frame_index: int, metadata: Mapping[str, object]
    ) -> MuJoCoState:
        """同じMuJoCo dataを既存transport/viewer stateへ投影する。"""
        if type(frame_index) is not int or frame_index < 0:
            raise ValueError("frame_index must be a non-negative integer")
        if not isinstance(metadata, Mapping):
            raise TypeError("transport metadata must be a mapping")
        return self.sample(frame_index=frame_index,metadata=metadata).state

    def sample(self, *, frame_index: int, metadata: Mapping[str, object]) -> ModelStateSample:
        """Robot/接触/Viewerを同じlocked dataで観測し、別world・時刻の混入を防ぐ。"""
        if type(frame_index) is not int or frame_index < 0 or not isinstance(metadata, Mapping):
            raise ValueError("invalid sample frame/metadata")
        with self._lock:
            geometry = None if self._scene_observer is None else self._scene_observer.observe(self._data,frame_index=frame_index)
            state = _read_synchronized_mujoco_state(self.model,self._data,frame_index=frame_index,metadata=metadata,
                layout=self._snapshot_layout)
            return ModelStateSample(self.snapshot(),state,
                tuple(i for arm in self.addresses for i in arm.qpos_addresses),geometry,
                self._dynamics_observation(frame_index))

    def _dynamics_observation(self, frame_index):
        """kinematic経路はforceを作らない。"""
        return None

    def _check_data(self, data) -> None:
        qpos, qvel, ctrl, sites = data.qpos, data.qvel, data.ctrl, data.site_xpos
        if not (np.isfinite(qpos).all() and np.isfinite(qvel).all()
                and np.isfinite(ctrl).all() and np.isfinite(sites).all()):
            raise ValueError("nonfinite assembly state")
        if (data.warning.number > 0).any():
            raise ValueError("MuJoCo warning in candidate state")
        # 全native配列のfinite検査後、同じlive限界を直接照合する。
        # 違反一覧用DTOと二重のtuple/float変換を毎substepで生成しない。
        for arm in self.addresses:
            for index, limit in zip(arm.qpos_addresses, self.limits.joints, strict=True):
                if not limit.lower_rad <= float(qpos[index]) <= limit.upper_rad:
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
            self._initialize_data(data)
            mujoco.mj_forward(self.model, data)
            self._check_data(data)
            if self._scene_observer is not None:
                self._scene_observer.validate_initial(data)
            self._generation += 1
            self._data = data
            self._snapshot_cache = None

    def invalidate(self) -> None:
        with self._lock:
            self._pending = None
            self._snapshot_cache = None

    def numerical_condition(self):
        """実際のnative modelと実行policyの数値条件をcopyで公開する。"""
        from dataclasses import asdict
        with self._lock:
            return {
                "schema_version": "model-numerical-condition/v1",
                "execution_semantics": self.execution_semantics,
                "mujoco_version": mujoco.__version__,
                "joint_limits": asdict(self.limits),
                "native": {name: getattr(self.model, name).tolist() for name in (
                    "jnt_range", "jnt_limited", "actuator_ctrlrange", "actuator_forcerange",
                    "actuator_gainprm", "actuator_biasprm", "actuator_dynprm",
                    "dof_damping", "dof_armature", "body_mass", "body_inertia")},
                "options": {"timestep_s": float(self.model.opt.timestep),
                    "gravity_m_s2": self.model.opt.gravity.tolist(),
                    "integrator": int(self.model.opt.integrator), "solver": int(self.model.opt.solver),
                    "iterations": int(self.model.opt.iterations), "tolerance": float(self.model.opt.tolerance)},
                "controller": {"fd_epsilon_rad": DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
                    "damping": DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
                    "max_qpos_delta_norm_rad": DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
                    "max_endpoint_delta_per_tick_m": DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_DELTA_PER_TICK_M},
            }

    def trial_state(self):
        """全物体・actuator・warmstart等を含むnative integration stateを保存用に読む。"""
        with self._lock:
            spec = mujoco.mjtState.mjSTATE_INTEGRATION
            state = np.empty(mujoco.mj_stateSize(self.model, spec))
            mujoco.mj_getState(self.model, self._data, state, spec)
            return {"schema_version": "model-integration-state/v1",
                "model_sha256": self.built.model_sha256, "mujoco_version": mujoco.__version__,
                "state_spec": int(spec), "integration_state": state.tolist(),
                "time_s": float(self._data.time), "qpos": self._data.qpos.tolist(),
                "qvel": self._data.qvel.tolist(), "act": self._data.act.tolist(),
                "ctrl": self._data.ctrl.tolist()}

    def _initialize_data(self, data):
        """旧kinematicのhome/controlは変更しない。実行方式固有の初期化hook。"""

    def _planning_state(self, base):
        """全armに同じ計画snapshotを渡す。"""
        return base

    def _integrate_candidate(self, base, candidates, dt_s):
        """kinematic診断のqpos反映。dynamic積分とのdispatchは構築時だけ。"""
        candidate_data=self._candidate_data
        mujoco.mj_copyData(candidate_data,self.model,base)
        for arm in self.addresses:
            candidate_data.qpos[list(arm.qpos_addresses)]=candidates[arm.arm_id]
        candidate_data.time=float(base.time)+dt_s
        mujoco.mj_forward(self.model,candidate_data)
        return candidate_data

    def _arm_candidate(self, base, arm, command, dt_s, *, observed=None):
        velocity = np.asarray(command.velocity_m_s)
        if not np.isfinite(np.linalg.norm(velocity)):
            raise ValueError("velocity magnitude overflow")
        if command.control_frame == "tool":
            reference = base if observed is None else observed
            velocity = np.asarray(reference.site_xmat[arm.tip_site_id]).reshape(3, 3) @ velocity
        q = tuple(float(base.qpos[i]) for i in arm.qpos_addresses)
        if not np.any(velocity):
            # 中立でも物理積分は続ける。ゼロ増分のDLSだけを省き、servo targetを保持する。
            if self.limits.violations_for_qpos(q):
                raise ValueError(f"joint_limit_violation:{arm.arm_id}")
            return q
        kinematics = self._kinematics[arm.arm_id]
        kinematics.load(base)
        solver = LocalEndpointMotionGenerator(
            endpoint_kinematics=kinematics,
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
            base = self._base_data
            mujoco.mj_copyData(base, self.model, self._data)
            by_id = {c.endpoint_id: c for c in commands}
            planning = self._planning_state(base)
            candidates = {arm.arm_id: self._arm_candidate(planning, arm, by_id[arm.arm_id], dt_s, observed=base)
                          for arm in self.addresses}
            candidate_data = self._integrate_candidate(base, candidates, dt_s)
            if not np.isfinite(candidate_data.time) or candidate_data.time <= base.time:
                raise ValueError("invalid simulation time advance")
            self._check_data(candidate_data)
            predicted = self._snapshot(candidate_data, self._generation + 1)
            joint = JointPositionCommand(timestamp_s=predicted.simulation_time_s,
                                         joint_angles_rad=tuple(v for arm in self.addresses for v in candidates[arm.arm_id]))
            prepared = PreparedCoordinatedStep(before, joint, predicted, object())
            self._pending = (prepared, candidate_data)
            return prepared

    def commit(self, candidate: PreparedCoordinatedStep) -> CoordinatedSnapshot:
        with self._lock:
            pending, self._pending = self._pending, None
            if pending is None or pending[0] is not candidate or candidate.before != self._snapshot(self._data, self._generation):
                raise ValueError("foreign, consumed, or stale coordinated candidate")
            self._check_data(pending[1])
            if self._snapshot(pending[1], self._generation + 1) != candidate.predicted:
                raise ValueError("candidate modified after preparation")
            # 検査完了後にlive/candidateを交換。以後のprepareは旧liveを作業域にする。
            self._candidate_data, self._data = self._data, pending[1]
            self._generation += 1
            self._snapshot_cache = None
            return self.snapshot()
