"""同じmodel/dataから全物体の幾何接触を観測する。力の推定は行わない。"""
from dataclasses import dataclass
from .objects import number, vector, identifier
import re


@dataclass(frozen=True, slots=True)
class SceneContact:
    """手先から物体へ向くworld法線と接触点。step間で永続するcontact IDは仮定しない。"""
    endpoint_id: str
    object_id: str
    tool_geom_name: str
    object_geom_name: str
    point_world_m: tuple[float, float, float]
    normal_world: tuple[float, float, float]
    distance_m: float
    penetration_m: float
    relation: str

    def __post_init__(self):
        """観測と派生distance分類の不整合を入口で拒否する。"""
        identifier(self.endpoint_id); identifier(self.object_id)
        if any(type(v) is not str or not v for v in (self.tool_geom_name,self.object_geom_name)):
            raise ValueError("contact geom names required")
        object.__setattr__(self,"point_world_m",vector(self.point_world_m,3))
        normal=vector(self.normal_world,3)
        if abs(sum(v*v for v in normal)-1)>1e-8:
            raise ValueError("unit contact normal required")
        object.__setattr__(self,"normal_world",normal)
        d=number(self.distance_m);p=number(self.penetration_m,nonnegative=True)
        object.__setattr__(self,"distance_m",d)
        object.__setattr__(self,"penetration_m",p)
        expected="penetrating" if d<0 else "touching" if d==0 else "near"
        if abs(p-max(0.,-d))>1e-12 or self.relation!=expected:
            raise ValueError("inconsistent contact classification")


@dataclass(frozen=True, slots=True)
class ObjectPose:
    """commandした初期配置ではなくnative dataから読んだ物体pose。"""
    instance_id: str
    position_m: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float]

    def __post_init__(self):
        """world poseの次元・単位quaternionを確認する。"""
        identifier(self.instance_id)
        object.__setattr__(self,"position_m",vector(self.position_m,3))
        q=vector(self.orientation_wxyz,4)
        if abs(sum(v*v for v in q)-1)>1e-8:
            raise ValueError("unit object quaternion required")
        object.__setattr__(self,"orientation_wxyz",q)


@dataclass(frozen=True, slots=True)
class SceneGeometryObservation:
    """geometry-only evidence。force zeroや押し返しを推定しない。"""
    scene_digest: str
    model_sha256: str
    frame_index: int
    simulation_time_s: float
    objects: tuple[ObjectPose, ...]
    contacts: tuple[SceneContact, ...]
    dynamic: bool = False

    def __post_init__(self):
        """geometry-only recordの型・上限・参照を検証する。"""
        if any(type(v) is not str or not re.fullmatch(r"[a-f0-9]{64}",v) for v in (self.scene_digest,self.model_sha256)):
            raise ValueError("scene/model digests required")
        if type(self.frame_index) is not int or self.frame_index<0:
            raise ValueError("invalid observation frame")
        object.__setattr__(self,"simulation_time_s",number(self.simulation_time_s,nonnegative=True))
        if type(self.objects) is not tuple or type(self.contacts) is not tuple or len(self.objects)>32 or len(self.contacts)>256:
            raise ValueError("bounded observation tuples required")
        if any(type(o) is not ObjectPose for o in self.objects) or any(type(c) is not SceneContact for c in self.contacts):
            raise ValueError("typed geometry observations required")
        ids={o.instance_id for o in self.objects}
        if len(ids)!=len(self.objects) or any(c.object_id not in ids for c in self.contacts):
            raise ValueError("invalid observation object binding")

    def to_document(self):
        """payload用projection。計測不能な力はnullと理由を保持する。"""
        return {"schema_version":"scene-contact-geometry/v2" if self.dynamic else "scene-contact-geometry/v1", "scene_digest":self.scene_digest,
            "model_sha256":self.model_sha256,"frame_index":self.frame_index,"simulation_time_s":self.simulation_time_s,
            "status":"observed", "scope":"tool_object_geometry", "force_status":"separate_dynamics_evidence" if self.dynamic else "not_evaluated_kinematic",
            "force_n":None, "objects":[{"instance_id":o.instance_id,"position_m":o.position_m,
                "orientation_wxyz":o.orientation_wxyz} for o in self.objects],
            "contacts":[{"endpoint_id":c.endpoint_id,"object_id":c.object_id,
                "tool_geom_name":c.tool_geom_name,"object_geom_name":c.object_geom_name,
                "point_world_m":c.point_world_m,"normal_world":c.normal_world,
                "distance_m":c.distance_m,"penetration_m":c.penetration_m,"relation":c.relation}
                for c in self.contacts]}
