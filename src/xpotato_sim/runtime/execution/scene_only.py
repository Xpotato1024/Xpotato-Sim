"""通常TrialRunnerに接続する、受動SceneのMuJoCo model/provider。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Mapping

import mujoco
import numpy as np

from xpotato_sim.mujoco_backend.compatibility import validate_solver_compatibility
from xpotato_sim.mujoco_backend.snapshot import _SnapshotLayout, _read_synchronized_mujoco_state
from xpotato_sim.runtime.scene.contracts import ModelScenePlan

EMPTY_MODEL = b'<mujoco model="scene_only"><worldbody/></mujoco>'


@dataclass(frozen=True, slots=True)
class SceneSnapshot:
    model_sha256: str


@dataclass(frozen=True, slots=True)
class SceneOnlyModelInstance:
    provider: SceneNativeProvider
    viewer: None = None


class SceneNativeProvider:
    """唯一のnative MuJoCo state owner。Robotのservoや架空の関節を作らない。"""

    def __init__(self, scene: ModelScenePlan):
        if scene.dynamics is None:
            raise ValueError("scene-only requires numerical integration settings")
        self.scene = scene
        composed = scene.compose(EMPTY_MODEL, {}, (), scene_only=True)
        self.xml = composed.xml
        self.model_sha256 = sha256(self.xml).hexdigest()
        self.model = mujoco.MjModel.from_xml_string(self.xml.decode("utf-8"))
        validate_solver_compatibility(self.model)
        self.data = mujoco.MjData(self.model)
        self._snapshot_layout = _SnapshotLayout.prepare(self.model)
        self._home = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "home")
        if self._home < 0:
            raise ValueError("scene model must contain a resolved home keyframe")
        self.reset()

    def _check(self):
        if any(not np.isfinite(a).all() for a in (self.data.qpos, self.data.qvel, self.data.plastic)):
            raise ValueError("nonfinite Scene-native state")
        if np.any(self.data.warning.number):
            raise ValueError("MuJoCo reported a Scene solver warning")

    def reset(self):
        mujoco.mj_resetDataKeyframe(self.model, self.data, self._home)
        mujoco.mj_forward(self.model, self.data)
        self._check()

    def step(self, control_dt_s: float):
        count = self.scene.dynamics.substeps(control_dt_s)
        mujoco.mj_step(self.model, self.data, nstep=count)
        self._check()

    def snapshot(self):
        return SceneSnapshot(self.model_sha256)

    def numerical_condition(self):
        o=self.model.opt
        return {"schema_version":"scene-native-condition/v1",
                "mujoco_version":mujoco.__version__,
                "model_sha256":self.model_sha256,
                "nq":self.model.nq,"nv":self.model.nv,"nflex":self.model.nflex,
                "jacobian_sparse":bool(mujoco.mj_isSparse(self.model)),
                "physics":{"timestep_s":float(o.timestep),"integrator":int(o.integrator),
                           "solver":int(o.solver),"iterations":int(o.iterations),
                           "tolerance":float(o.tolerance),"enableflags":int(o.enableflags)},
                "scene_digest":self.scene.manifest.digest}

    def trial_state(self):
        spec=mujoco.mjtState.mjSTATE_INTEGRATION
        values=np.empty(mujoco.mj_stateSize(self.model,spec))
        mujoco.mj_getState(self.model,self.data,values,spec)
        return {"schema_version":"model-integration-state/v1",
                "model_sha256":self.model_sha256,
                "mujoco_version":mujoco.__version__,"state_spec":int(spec),
                "integration_state":values.tolist(),"time_s":float(self.data.time),
                "qpos":self.data.qpos.tolist(),"qvel":self.data.qvel.tolist(),
                "act":self.data.act.tolist(),"ctrl":self.data.ctrl.tolist()}

    def sample(self, frame_index: int, metadata: Mapping[str,object]):
        self._check()
        content=dict(metadata)
        content["model_sha256"]=self.model_sha256
        content["scene_digest"]=self.scene.manifest.digest
        content["scene_only"]=True
        content["mujoco_version"]=mujoco.__version__
        # A full integration checkpoint is needed to replay plastic deformation without re-integrating.
        content["scene_state_checkpoint_v1"]=self.trial_state()
        return _read_synchronized_mujoco_state(self.model,self.data,
            frame_index=frame_index,metadata=content,layout=self._snapshot_layout)


def build_scene_only_model(scene: ModelScenePlan) -> SceneOnlyModelInstance:
    if type(scene) is not ModelScenePlan:
        raise TypeError("typed Scene plan required")
    from xpotato_sim.runtime.execution.scene_physics import ScenePhysicsSettings
    if type(scene.dynamics) is not ScenePhysicsSettings:
        raise TypeError("passive Scene physics settings required")
    return SceneOnlyModelInstance(SceneNativeProvider(scene))


class SceneOnlyExecution:
    """ModelExecutionと同じTrialRunnerの寿命・観測・記録を使う受動動作。"""

    passive = True

    def __init__(self, profile, *, clock, instance=None):
        self.profile=profile
        self.instance=profile.build_model() if instance is None else instance
        self.clock=clock
        self.tick_count=0
        self.state="running"
        self.reason=None
        self.epoch=None
        self.task_state=None
        self.task_view=None

    def reset(self, epoch):
        self.instance.provider.reset()
        self.epoch=epoch
        self.tick_count=0
        self.state="running"
        self.reason=None

    def tick(self, *, freshness_barrier=None):
        if freshness_barrier is not None:
            raise ValueError("passive scene cannot consume source input")
        if self.state=="running":
            self.instance.provider.step(self.profile.dt_s)
            self.tick_count+=1
        return self.tick_count

    def sample(self, frame_index, *, advance_task=False, budget_exhausted=False, stopped_reason=None):
        return self.instance.provider.sample(frame_index,metadata={
            "physical_output":"disabled","motion_status":self.state,
            "scene_only_presentation_v1":{"schema_version":"scene-only-presentation/v1",
                 "epoch":self.epoch,"tick":self.tick_count,"state":self.state,
                 "reason":self.reason}})

    def stop(self):
        self.state="stopped"
        self.reason=self.reason or "stopped"
