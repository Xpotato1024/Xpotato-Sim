"""同じassembly/共同commitを、元のactuator servoとmj_stepへ接続する。"""
import mujoco
import numpy as np
from xpotato_sim.runtime.execution.physics import DynamicsSettings, DYNAMIC_EXECUTION
from .coordinated import FastArmAssemblyMotionProvider
from xpotato_sim.mujoco_backend.state_layout import resolve_scene_state_layout


class FastArmDynamicMotionProvider(FastArmAssemblyMotionProvider):
    """未公開候補worldを積分し、全arm成功後だけ原子的に公開するsimulation provider。"""
    execution_semantics = DYNAMIC_EXECUTION

    def __init__(self, assembly, *, built, object_scene, settings):
        if type(settings) is not DynamicsSettings or object_scene.manifest.world is None or not object_scene.dynamic_execution:
            raise TypeError("explicit world and DynamicsSettings required")
        self.settings=settings
        super().__init__(assembly,built=built,object_scene=object_scene)
        self.state_layout=resolve_scene_state_layout(self.model,self.assembly.joint_names,
            tuple("object__"+o.instance_id+"__free" for o in object_scene.manifest.objects if o.motion_type=="dynamic"))
        if abs(float(self.model.opt.timestep)-settings.physics_dt_s)>1e-15:
            raise ValueError("compiled timestep differs from execution configuration")

    def _initialize_data(self,data):
        """position-servo targetをhomeの関節位置へ合わせる。原本home/gainは変更しない。"""
        for arm in self.addresses:
            for aid,jid,qa in zip(arm.actuator_ids,arm.joint_ids,arm.qpos_addresses,strict=True):
                if (int(self.model.actuator_dyntype[aid])!=0 or int(self.model.actuator_gaintype[aid])!=0
                        or int(self.model.actuator_biastype[aid])!=1
                        or self.model.actuator_gainprm[aid,0]<=0
                        or self.model.actuator_biasprm[aid,1]!=-self.model.actuator_gainprm[aid,0]
                        or self.model.actuator_biasprm[aid,2]>=0
                        or not np.array_equal(self.model.actuator_gear[aid], [1.,0.,0.,0.,0.,0.])):
                    raise ValueError("selected Robot actuator is not the declared position servo")
                target=float(data.qpos[qa])
                if not self.model.actuator_ctrlrange[aid,0] <= target <= self.model.actuator_ctrlrange[aid,1]:
                    raise ValueError("home is outside servo ctrlrange")
                data.ctrl[aid]=target

    def _dynamics_observation(self,frame_index):
        from xpotato_sim.runtime.scene.dynamics_observation import observe_dynamics
        return observe_dynamics(self.model,self._data,scene=self._scene_observer.scene,model_sha256=self.built.model_sha256,
            settings=self.settings,arms=self.addresses,frame_index=frame_index)

    def _planning_state(self,base):
        """保持中のcommand targetをseedとする。中立でmeasured poseへ追従し続けて沈下させない。"""
        planning=self._planning_data
        mujoco.mj_copyData(planning,self.model,base)
        for arm in self.addresses:
            planning.qpos[list(arm.qpos_addresses)]=base.ctrl[list(arm.actuator_ids)]
        mujoco.mj_kinematics(self.model,planning)
        return planning

    def _check_data(self,data):
        super()._check_data(data)
        if not np.all(np.isfinite(data.qacc)) or not np.all(np.isfinite(data.actuator_force)):
            raise ValueError("nonfinite dynamic state")
        for arm in self.addresses:
            if np.max(np.abs(data.qvel[list(arm.dof_addresses)]))>self.settings.max_joint_speed_rad_s:
                raise ValueError("dynamic joint speed budget exceeded")
            error=data.ctrl[list(arm.actuator_ids)]-data.qpos[list(arm.qpos_addresses)]
            if np.max(np.abs(error))>self.settings.max_tracking_error_rad:
                raise ValueError("dynamic tracking error budget exceeded")

    def _integrate_candidate(self,base,candidates,dt_s):
        substeps=self.settings.substeps(dt_s)
        data=self._candidate_data
        mujoco.mj_copyData(data,self.model,base)
        for arm in self.addresses:
            targets=np.asarray(candidates[arm.arm_id])
            bounds=self.model.actuator_ctrlrange[list(arm.actuator_ids)]
            if np.any(targets<bounds[:,0]) or np.any(targets>bounds[:,1]):
                raise ValueError("dynamic target outside actuator ctrlrange")
            data.ctrl[list(arm.actuator_ids)]=targets
        for _ in range(substeps):
            old_time=float(data.time)
            mujoco.mj_step(self.model,data)
            if not data.time>old_time:
                raise ValueError("physics step did not advance time")
            self._check_data(data)
        # mj_stepのqpos積分後のpose/contact/forceを同一現在時刻にそろえる。
        mujoco.mj_forward(self.model,data)
        self._check_data(data)
        return data
