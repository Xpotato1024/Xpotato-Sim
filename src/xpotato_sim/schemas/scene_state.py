"""Robot部分状態と全sceneのqpos/qvelを区別するimmutable配列配置契約。"""
from dataclasses import dataclass

_WIDTHS={"free":(7,6),"ball":(4,3),"slide":(1,1),"hinge":(1,1)}


@dataclass(frozen=True, slots=True)
class SceneJointLayout:
    """joint種類とRobot/objectの役割、qpos/qvelそれぞれの先頭address。"""
    name: str
    kind: str
    role: str
    qpos_address: int
    qvel_address: int

    def __post_init__(self):
        if type(self.name) is not str or not self.name or self.kind not in _WIDTHS or self.role not in ("robot","object"):
            raise ValueError("invalid named scene joint")
        if any(type(v) is not int or v<0 for v in (self.qpos_address,self.qvel_address)):
            raise ValueError("nonnegative state addresses required")

    def to_document(self):
        return {"name":self.name,"kind":self.kind,"role":self.role,"qpos_address":self.qpos_address,"qvel_address":self.qvel_address}


@dataclass(frozen=True, slots=True)
class SceneStateLayout:
    """全scene座標を重複・未割当なく覆う宣言。Robot名の長さをnqと仮定しない。"""
    qpos_dimension: int
    qvel_dimension: int
    joints: tuple[SceneJointLayout, ...]

    def __post_init__(self):
        if any(type(n) is not int or not 1<=n<=4096 for n in (self.qpos_dimension,self.qvel_dimension)):
            raise ValueError("bounded positive scene dimension required")
        if type(self.joints) is not tuple or not self.joints or any(type(j) is not SceneJointLayout for j in self.joints):
            raise TypeError("typed joint layout tuple required")
        if len({j.name for j in self.joints})!=len(self.joints):
            raise ValueError("duplicate scene joint")
        q,v=[],[]
        for j in self.joints:
            qw,vw=_WIDTHS[j.kind]
            q.extend(range(j.qpos_address,j.qpos_address+qw));v.extend(range(j.qvel_address,j.qvel_address+vw))
        if sorted(q)!=list(range(self.qpos_dimension)) or sorted(v)!=list(range(self.qvel_dimension)):
            raise ValueError("scene layout must cover every coordinate exactly once")

    def to_document(self):
        return {"schema_version":"scene-state-layout/v1","qpos_dimension":self.qpos_dimension,
                "qvel_dimension":self.qvel_dimension,"joints":[j.to_document() for j in self.joints]}

    @classmethod
    def from_document(cls,raw):
        if type(raw) is not dict or set(raw)!={"schema_version","qpos_dimension","qvel_dimension","joints"} or raw["schema_version"]!="scene-state-layout/v1" or type(raw["joints"]) is not list:
            raise ValueError("invalid scene layout document")
        joints=[]
        for j in raw["joints"]:
            if type(j) is not dict or set(j)!={"name","kind","role","qpos_address","qvel_address"}:
                raise ValueError("invalid scene joint document")
            joints.append(SceneJointLayout(**j))
        return cls(raw["qpos_dimension"],raw["qvel_dimension"],tuple(joints))
