"""Sceneが所有するworld物理条件。積分器やRobot名はここに含めない。"""
from dataclasses import dataclass
from .objects import fields, identifier, vector


@dataclass(frozen=True, slots=True)
class SupportPlane:
    """無限支持面のpose・表面特性。見た目サイズは物理的な有限境界ではない。"""
    plane_id: str
    position_m: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float]
    friction: tuple[float, float, float]
    rgba: tuple[float, float, float, float]

    def __post_init__(self):
        identifier(self.plane_id)
        for key, size in (("position_m",3),("orientation_wxyz",4),("friction",3),("rgba",4)):
            object.__setattr__(self,key,vector(getattr(self,key),size))
        if abs(sum(v*v for v in self.orientation_wxyz)-1)>1e-9:
            raise ValueError("unit support plane quaternion required")
        if any(v<0 for v in self.friction) or any(not 0<=v<=1 for v in self.rgba):
            raise ValueError("invalid support plane surface")

    def to_document(self):
        return {"plane_id":self.plane_id,"position_m":self.position_m,
                "orientation_wxyz":self.orientation_wxyz,"friction":self.friction,"rgba":self.rgba}

    @classmethod
    def from_document(cls,raw):
        return cls(**fields(raw,{"plane_id","position_m","orientation_wxyz","friction","rgba"},"support plane"))


@dataclass(frozen=True, slots=True)
class WorldPhysics:
    """重力と明示支持面集合。空集合は床なしであり、Robotから床を補わない。"""
    gravity_m_s2: tuple[float, float, float]
    support_planes: tuple[SupportPlane, ...]

    def __post_init__(self):
        object.__setattr__(self,"gravity_m_s2",vector(self.gravity_m_s2,3))
        if (type(self.support_planes) is not tuple or len(self.support_planes)>8
                or any(type(p) is not SupportPlane for p in self.support_planes)
                or len({p.plane_id for p in self.support_planes})!=len(self.support_planes)):
            raise ValueError("bounded distinct support plane tuple required")
        object.__setattr__(self,"support_planes",tuple(sorted(self.support_planes,key=lambda p:p.plane_id)))

    def to_document(self):
        return {"frame":"mujoco_world","gravity_m_s2":self.gravity_m_s2,
                "support_planes":[p.to_document() for p in self.support_planes]}

    @classmethod
    def from_document(cls,raw):
        raw=fields(raw,{"frame","gravity_m_s2","support_planes"},"world")
        if raw["frame"]!="mujoco_world" or type(raw["support_planes"]) is not list:
            raise ValueError("explicit world frame and plane list required")
        return cls(raw["gravity_m_s2"],tuple(SupportPlane.from_document(p) for p in raw["support_planes"]))
