"""native solver接触wrenchの共通読取り。Task/object分類や反力集約を持たない。"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ContactWrench:
    """接触frameのwrenchと、geom2へ働くworld wrench。単位N/Nm。"""
    local: tuple[float, ...]
    force_on_geom2_world_n: tuple[float, float, float]
    torque_on_geom2_world_nm: tuple[float, float, float]


def read_contact_wrench(model, data, contact_index, frame_world):
    import mujoco
    import numpy as np
    if type(contact_index) is not int or not 0<=contact_index<int(data.ncon):
        raise ValueError("invalid contact index")
    if int(data.contact[contact_index].efc_address)<0:
        raise ValueError("contact has no active solver constraint")
    buffer=np.zeros(6,dtype=np.float64)
    result=mujoco.mj_contactForce(model,data,contact_index,buffer)
    raw=buffer if result is None else result
    if len(raw)!=6 or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in raw):
        raise ValueError("invalid contact wrench components")
    local=tuple(float(v) for v in raw)
    frame=np.asarray(frame_world,dtype=float).reshape(3,3)
    if not np.allclose(frame@frame.T,np.eye(3),rtol=0,atol=1e-8) or np.linalg.det(frame)<0:
        raise ValueError("invalid orthonormal contact frame")
    force=frame.T@np.asarray(local[:3]); torque=frame.T@np.asarray(local[3:])
    if not all(isfinite(v) for v in (*local,*force,*torque)):
        raise ValueError("nonfinite contact wrench")
    return ContactWrench(local,tuple(map(float,force)),tuple(map(float,torque)))
