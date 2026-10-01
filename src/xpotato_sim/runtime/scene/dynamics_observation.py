"""全worldのdynamic観測。native state/actuator/constraintを同一時刻から読む。"""
import mujoco
import numpy as np
from xpotato_sim.mujoco_backend.contact_geometry import read_contact_geometry
from xpotato_sim.mujoco_backend.contact_wrench import read_contact_wrench


def observe_dynamics(model,data,*,scene,model_sha256,settings,arms,frame_index):
    """力を推定せずsolverから取得し、制御targetと実際のqpos/qvelを分離する。"""
    if any(int(w.number)>0 for w in data.warning):
        raise ValueError("dynamic observation has solver warning")
    objects=[]
    geom_roles={name:{"kind":"object","id":oid} for oid,name in scene.object_geoms}
    geom_roles.update({c.geom_name:{"kind":"tool","id":c.endpoint_id} for c in scene.colliders})
    geom_roles.update({"support__"+p.plane_id:{"kind":"support","id":p.plane_id} for p in scene.manifest.world.support_planes})
    for obj in scene.manifest.objects:
        bid=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,"object__"+obj.instance_id)
        velocity=np.zeros(6);mujoco.mj_objectVelocity(model,data,mujoco.mjtObj.mjOBJ_BODY,bid,velocity,0)
        objects.append({"instance_id":obj.instance_id,"motion_type":obj.motion_type,
            "position_m":data.xpos[bid].tolist(),"orientation_wxyz":data.xquat[bid].tolist(),
            "linear_velocity_world_m_s":velocity[3:].tolist(),"angular_velocity_world_rad_s":velocity[:3].tolist()})
    contacts=[]
    for i in range(data.ncon):
        c=data.contact[i]
        a=mujoco.mj_id2name(model,mujoco.mjtObj.mjOBJ_GEOM,int(c.geom1))
        b=mujoco.mj_id2name(model,mujoco.mjtObj.mjOBJ_GEOM,int(c.geom2))
        if a not in geom_roles or b not in geom_roles:
            continue
        g=read_contact_geometry(c)
        active=int(c.efc_address)>=0
        w=read_contact_wrench(model,data,i,g.frame_world) if active else None
        contacts.append({"geom1":a,"geom2":b,"role1":geom_roles[a],"role2":geom_roles[b],
            "point_world_m":g.point_world_m,"normal_geom1_to_geom2_world":g.normal_world,
            "distance_m":g.distance_m,"penetration_m":g.penetration_m,
            "status":"measured" if active else "measurement_unavailable",
            "force_on_geom2_world_n":None if w is None else w.force_on_geom2_world_n,
            "torque_on_geom2_world_nm":None if w is None else w.torque_on_geom2_world_nm})
    if len(contacts)>4096:
        raise ValueError("contact observation budget exceeded")
    joints=[]
    for arm in arms:
        for name,qa,va,aid in zip(arm.joint_names,arm.qpos_addresses,arm.dof_addresses,arm.actuator_ids,strict=True):
            joints.append({"name":name,"target_rad":float(data.ctrl[aid]),"position_rad":float(data.qpos[qa]),
                "velocity_rad_s":float(data.qvel[va]),"actuator_force_nm":float(data.actuator_force[aid])})
    result={"schema_version":"scene-dynamics-observation/v1","model_sha256":model_sha256,
        "scene_digest":scene.manifest.digest,"settings_digest":settings.digest,"frame_index":frame_index,
        "simulation_time_s":float(data.time),"semantics":"coordinated_actuator_servo_dynamic/v1",
        "gravity_m_s2":model.opt.gravity.tolist(),"objects":objects,"joints":joints,"contacts":contacts}
    import json
    json.dumps(result,allow_nan=False)
    return result
