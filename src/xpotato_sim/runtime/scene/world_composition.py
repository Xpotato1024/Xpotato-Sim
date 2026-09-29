"""Environment worldとExecution数値条件のMJCF投影。Robot形状や制御は所有しない。"""
import xml.etree.ElementTree as ET
import mujoco
import numpy as np
from .objects import ObjectSceneManifest
from xpotato_sim.runtime.execution.physics import DynamicsSettings


def values(seq):
    return " ".join(format(float(v),".17g") for v in seq)


def configure_world(tree, manifest: ObjectSceneManifest, settings: DynamicsSettings | None):
    """新worldにはRobot所有floorを混入せず、global optionを一回だけ確定する。"""
    if manifest.world is None or (settings is not None and type(settings) is not DynamicsSettings):
        raise ValueError("explicit world and typed integration settings required")
    if tree.find("option") is not None:
        raise ValueError("world/Execution cannot silently override a Robot global option")
    options={"gravity":values(manifest.world.gravity_m_s2)}
    if settings is not None:
        options.update(timestep=str(settings.physics_dt_s),integrator=settings.integrator,
            solver=settings.solver,iterations=str(settings.iterations),tolerance=str(settings.tolerance),cone="elliptic")
    ET.SubElement(tree,"option",options)
    cp=manifest.contact
    world=tree.find("worldbody")
    if world is None:
        raise ValueError("worldbody required")
    if any(g.get("type")=="plane" for g in world.findall("geom")):
        raise ValueError("explicit world cannot inherit unselected support planes")
    ET.SubElement(world,"light",{"directional":"true","pos":"0 0 2","dir":"0 0 -1"})
    for plane in manifest.world.support_planes:
        ET.SubElement(world,"geom",{"name":"support__"+plane.plane_id,"type":"plane", "size":"3 3 0.1",
            "pos":values(plane.position_m),"quat":values(plane.orientation_wxyz),"rgba":values(plane.rgba),
            "friction":values(plane.friction),"contype":"1","conaffinity":"1","group":"0",
            "condim":str(cp.condim),"solref":values(cp.solref),"solimp":values(cp.solimp),"margin":str(cp.margin_m)})


def finalize_home(tree, source_xml, assets, manifest):
    """sourceの名前付き初期状態とobject初期pose/速度から完全なscene homeを作る。"""
    source=mujoco.MjModel.from_xml_string(source_xml.decode(),dict(assets))
    data=mujoco.MjData(source)
    key=mujoco.mj_name2id(source,mujoco.mjtObj.mjOBJ_KEY,"home")
    if key<0:
        raise ValueError("Robot source home keyframe required")
    mujoco.mj_resetDataKeyframe(source,data,key)
    # 新worldのRobotはbare MJCF。includeに別keyframeを残して黙って上書きしない。
    if tree.findall("include"):
        raise ValueError("explicit world requires composed Robot XML, not an implicit base scene")
    for node in tree.findall("keyframe"):
        tree.remove(node)
    model=mujoco.MjModel.from_xml_string(ET.tostring(tree,encoding="unicode"),dict(assets))
    qpos=model.qpos0.copy(); qvel=np.zeros(model.nv); ctrl=np.zeros(model.nu)
    widths={0:(7,6),1:(4,3),2:(1,1),3:(1,1)}
    for jid in range(source.njnt):
        name=mujoco.mj_id2name(source,mujoco.mjtObj.mjOBJ_JOINT,jid)
        new=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_JOINT,name)
        if new<0 or model.jnt_type[new]!=source.jnt_type[jid]:
            raise ValueError("Robot joint disappeared during scene composition")
        qw,vw=widths[int(source.jnt_type[jid])]
        a,b=int(source.jnt_qposadr[jid]),int(model.jnt_qposadr[new]); qpos[b:b+qw]=data.qpos[a:a+qw]
        a,b=int(source.jnt_dofadr[jid]),int(model.jnt_dofadr[new]); qvel[b:b+vw]=data.qvel[a:a+vw]
    for aid in range(source.nu):
        name=mujoco.mj_id2name(source,mujoco.mjtObj.mjOBJ_ACTUATOR,aid)
        new=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_ACTUATOR,name)
        if new<0:
            raise ValueError("Robot actuator disappeared during scene composition")
        ctrl[new]=data.ctrl[aid]
    for obj in manifest.objects:
        if obj.motion_type!="dynamic":
            continue
        jid=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_JOINT,"object__"+obj.instance_id+"__free")
        qa,va=int(model.jnt_qposadr[jid]),int(model.jnt_dofadr[jid])
        qpos[qa:qa+7]=(*obj.position_m,*obj.orientation_wxyz)
        qvel[va:va+3]=obj.initial_linear_velocity_m_s
        rotation=np.zeros(9); mujoco.mju_quat2Mat(rotation,np.asarray(obj.orientation_wxyz))
        # MuJoCo freejointは並進world/回転body。manifestのworld角速度をここで一回だけ変換。
        qvel[va+3:va+6]=rotation.reshape(3,3).T@np.asarray(obj.initial_angular_velocity_rad_s)
    key=ET.SubElement(ET.SubElement(tree,"keyframe"),"key",{"name":"home","qpos":values(qpos),"qvel":values(qvel),"ctrl":values(ctrl)})
    return tree
