"""native modelの名前/種別/addressを、Robotとobjectの明示役割へ投影する。"""
import mujoco
from xpotato_sim.schemas.scene_state import SceneJointLayout, SceneStateLayout


def resolve_scene_state_layout(model, robot_joint_names, object_joint_names):
    robot,objects=set(robot_joint_names),set(object_joint_names)
    if robot&objects:
        raise ValueError("ambiguous scene joint role")
    names=set();joints=[]
    kinds={0:"free",1:"ball",2:"slide",3:"hinge"}
    for i in range(model.njnt):
        name=mujoco.mj_id2name(model,mujoco.mjtObj.mjOBJ_JOINT,i)
        if name not in robot|objects:
            raise ValueError("unbound scene joint")
        names.add(name)
        joints.append(SceneJointLayout(name,kinds[int(model.jnt_type[i])],"robot" if name in robot else "object",int(model.jnt_qposadr[i]),int(model.jnt_dofadr[i])))
    if names!=robot|objects:
        raise ValueError("missing scene joint binding")
    return SceneStateLayout(int(model.nq),int(model.nv),tuple(joints))
