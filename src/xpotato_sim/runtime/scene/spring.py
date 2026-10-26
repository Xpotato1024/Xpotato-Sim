"""固定基部に対する一自由度ばね。solver接触条件やcommand gainから独立する。"""
from dataclasses import dataclass, asdict
from math import sqrt
from .objects import fields, number, vector


@dataclass(frozen=True, slots=True)
class SpringAxis:
    """axisは物体基準姿勢の局所押込方向。変位の正方向を押込とする。"""
    axis: tuple[float, float, float]
    stiffness_n_m: float
    damping_n_s_m: float
    range_m: tuple[float, float]
    initial_displacement_m: float
    initial_velocity_m_s: float

    def __post_init__(self):
        object.__setattr__(self, "axis", vector(self.axis, 3))
        if abs(sqrt(sum(v*v for v in self.axis))-1) > 1e-9:
            raise ValueError("unit spring axis required")
        for key in ("stiffness_n_m", "damping_n_s_m"):
            object.__setattr__(self, key, number(getattr(self, key), positive=key=="stiffness_n_m", nonnegative=True))
        object.__setattr__(self, "range_m", vector(self.range_m, 2))
        lo, hi = self.range_m
        if lo != 0 or hi <= lo:
            raise ValueError("compression spring range must start at zero")
        for key in ("initial_displacement_m", "initial_velocity_m_s"):
            object.__setattr__(self, key, number(getattr(self, key)))
        if not lo <= self.initial_displacement_m <= hi:
            raise ValueError("initial spring displacement outside range")

    def to_document(self):
        return asdict(self)

    @classmethod
    def from_document(cls, raw):
        return cls(**fields(raw, set(cls.__dataclass_fields__), "spring"))


def observe_springs(model, data, manifest):
    """同じMjDataからSI値と解析的復元/減衰力を読む。接触原力とは別値。"""
    import mujoco
    result = []
    for obj in manifest.objects:
        if obj.spring is None:
            continue
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "object__"+obj.instance_id+"__spring")
        if jid < 0:
            raise ValueError("missing spring joint")
        q = float(data.qpos[model.jnt_qposadr[jid]])
        v = float(data.qvel[model.jnt_dofadr[jid]])
        result.append({"instance_id": obj.instance_id, "displacement_m": q, "velocity_m_s": v,
            "restoring_force_n": -obj.spring.stiffness_n_m*q,
            "damping_force_n": -obj.spring.damping_n_s_m*v,
            "axial_force_n": -obj.spring.stiffness_n_m*q-obj.spring.damping_n_s_m*v})
    return result
