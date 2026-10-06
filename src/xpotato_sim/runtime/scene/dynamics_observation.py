"""全worldのdynamic観測。native state/actuator/constraintを同一時刻から読む。"""
import mujoco
import numpy as np
from dataclasses import dataclass
from math import isfinite
from xpotato_sim.mujoco_backend.contact_geometry import read_contact_geometry
from xpotato_sim.mujoco_backend.contact_wrench import read_contact_wrench


@dataclass(frozen=True, slots=True)
class _DynamicsObservationPlan:
    """固定scene/modelのnative addressと役割。可変の観測値は含めない。"""
    model: object
    scene: object
    settings: object
    arms: tuple
    objects: tuple[tuple[str, str, int], ...]
    geoms: tuple[tuple[str, str, str] | None, ...]
    joints: tuple[tuple[str, int, int, int], ...]
    identity: tuple[str, str]

    @classmethod
    def prepare(cls, model, scene, settings, arms):
        roles = {name:("object", oid) for oid,name in scene.object_geoms}
        roles.update({c.geom_name:("tool", c.endpoint_id) for c in scene.colliders})
        roles.update({"support__"+p.plane_id:("support", p.plane_id)
                      for p in scene.manifest.world.support_planes})
        geoms = []
        for gid in range(model.ngeom):
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, gid)
            geoms.append(None if name not in roles else (name, *roles[name]))
        objects = []
        for obj in scene.manifest.objects:
            bid = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "object__"+obj.instance_id))
            if bid < 0:
                raise ValueError("missing dynamic observation object binding")
            objects.append((obj.instance_id, obj.motion_type, bid))
        joints = tuple(binding for arm in arms for binding in zip(
            arm.joint_names, arm.qpos_addresses, arm.dof_addresses, arm.actuator_ids, strict=True))
        return cls(model, scene, settings, arms, tuple(objects), tuple(geoms), joints,
                   (scene.manifest.digest, settings.digest))

    def matches(self, model, scene, settings, arms):
        return self.model is model and self.scene is scene and self.settings is settings and self.arms is arms


def observe_dynamics(model,data,*,scene,model_sha256,settings,arms,frame_index,identity=None,plan=None):
    """力を推定せずsolverから取得し、制御targetと実際のqpos/qvelを分離する。"""
    if (data.warning.number > 0).any():
        raise ValueError("dynamic observation has solver warning")
    if plan is None:
        plan = _DynamicsObservationPlan.prepare(model, scene, settings, arms)
    elif not plan.matches(model, scene, settings, arms):
        raise ValueError("dynamic observation plan/model mismatch")
    objects=[]
    for oid,motion_type,bid in plan.objects:
        velocity=np.zeros(6);mujoco.mj_objectVelocity(model,data,mujoco.mjtObj.mjOBJ_BODY,bid,velocity,0)
        position, orientation = data.xpos[bid].tolist(), data.xquat[bid].tolist()
        if not all(isfinite(v) for v in (*position, *orientation, *velocity)):
            raise ValueError("nonfinite dynamic object observation")
        objects.append({"instance_id":oid,"motion_type":motion_type,
            "position_m":position,"orientation_wxyz":orientation,
            "linear_velocity_world_m_s":velocity[3:].tolist(),"angular_velocity_world_rad_s":velocity[:3].tolist()})
    contacts=[]
    for i in range(data.ncon):
        c=data.contact[i]
        ga, gb = int(c.geom1), int(c.geom2)
        # flex等の非rigid contactは負ID。旧名前解決と同じく未対応の役割は観測対象外。
        if not (0 <= ga < len(plan.geoms) and 0 <= gb < len(plan.geoms)):
            continue
        a, b = plan.geoms[ga], plan.geoms[gb]
        if a is None or b is None:
            continue
        g=read_contact_geometry(c)
        active=int(c.efc_address)>=0
        w=read_contact_wrench(model,data,i,g.frame_world) if active else None
        contacts.append({"geom1":a[0],"geom2":b[0],"role1":{"kind":a[1],"id":a[2]},"role2":{"kind":b[1],"id":b[2]},
            "point_world_m":g.point_world_m,"normal_geom1_to_geom2_world":g.normal_world,
            "distance_m":g.distance_m,"penetration_m":g.penetration_m,
            "status":"measured" if active else "measurement_unavailable",
            "force_on_geom2_world_n":None if w is None else w.force_on_geom2_world_n,
            "torque_on_geom2_world_nm":None if w is None else w.torque_on_geom2_world_nm})
    if len(contacts)>4096:
        raise ValueError("contact observation budget exceeded")
    joints=[]
    for name,qa,va,aid in plan.joints:
        values = tuple(float(v) for v in (data.ctrl[aid], data.qpos[qa], data.qvel[va], data.actuator_force[aid]))
        if not all(isfinite(v) for v in values):
            raise ValueError("nonfinite dynamic joint observation")
        joints.append({"name":name,"target_rad":values[0],"position_rad":values[1],
            "velocity_rad_s":values[2],"actuator_force_nm":values[3]})
    time, gravity = float(data.time), model.opt.gravity.tolist()
    if not all(isfinite(v) for v in (time, *gravity)):
        raise ValueError("nonfinite dynamic world observation")
    scene_digest, settings_digest = plan.identity if identity is None else identity
    result={"schema_version":"scene-dynamics-observation/v1","model_sha256":model_sha256,
        "scene_digest":scene_digest,"settings_digest":settings_digest,"frame_index":frame_index,
        "simulation_time_s":time,"semantics":"coordinated_actuator_servo_dynamic/v1",
        "gravity_m_s2":gravity,"objects":objects,"joints":joints,"contacts":contacts}
    return result
