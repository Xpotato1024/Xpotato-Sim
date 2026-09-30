"""固定/可動box sceneのstrictな値契約。配置と形状、表示と物理条件を分離する。"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite, sqrt
import re
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .world import WorldPhysics

MAX_OBJECTS = 32
MAX_SCENE_BYTES = 262144


def identifier(value: object) -> str:
    """生成MJCF名にも使えるlogical IDだけを許可し、path/importを受けない。"""
    if type(value) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", value):
        raise ValueError("invalid scene identifier")
    return value


def fields(value: object, expected: set[str], label: str) -> dict:
    """未知fieldを捨てず、nested objectもexactに検査する。"""
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label}: missing or unknown fields")
    return value


def number(value: object, *, positive: bool = False, nonnegative: bool = False) -> float:
    """bool、NaN、overflowを数値として受理しない。"""
    if type(value) not in (int, float):
        raise ValueError("finite number required")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("number overflow") from exc
    if not isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("number outside permitted range")
    return 0.0 if result == 0 else result


def vector(value: object, length: int, **kwargs) -> tuple[float, ...]:
    """JSON配列またはimmutable tupleの次元を固定する。"""
    if type(value) not in (list, tuple) or len(value) != length:
        raise ValueError("invalid vector dimension")
    return tuple(number(x, **kwargs) for x in value)


def canonical(value: object) -> bytes:
    """全実効設定をUTF-8 canonical JSONへ変換する。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def strict_json(document: bytes) -> dict:
    """bounded JSONから重複key、BOM、非有限数を拒否する。"""
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate JSON field")
            result[k] = v
        return result
    def invalid(value):
        raise ValueError("nonfinite JSON constant")
    if type(document) is not bytes or len(document) > MAX_SCENE_BYTES or document.startswith(b"\xef\xbb\xbf"):
        raise ValueError("bounded UTF-8 JSON required")
    try:
        value = json.loads(document.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid scene JSON") from exc
    if type(value) is not dict:
        raise ValueError("scene JSON object required")
    canonical(value)
    return value


@dataclass(frozen=True, slots=True)
class ObjectDefinition:
    """中心原点の均質box。世界位置・用途・固定条件を持たない。"""
    definition_id: str
    version: int
    half_extents_m: tuple[float, float, float]
    mass_kg: float
    friction: tuple[float, float, float]
    rgba: tuple[float, float, float, float]
    provenance: str

    def __post_init__(self):
        identifier(self.definition_id)
        if type(self.version) is not int or self.version < 1:
            raise ValueError("positive definition version required")
        object.__setattr__(self, "half_extents_m", vector(self.half_extents_m, 3, positive=True))
        object.__setattr__(self, "mass_kg", number(self.mass_kg, positive=True))
        object.__setattr__(self, "friction", vector(self.friction, 3, nonnegative=True))
        object.__setattr__(self, "rgba", vector(self.rgba, 4, nonnegative=True))
        if any(v > 1 for v in self.rgba):
            raise ValueError("RGBA outside [0,1]")
        if type(self.provenance) is not str or not self.provenance.strip() or len(self.provenance) > 512:
            raise ValueError("physical parameter provenance required")
        for value in self.diagonal_inertia_kg_m2:
            number(value, positive=True)

    @property
    def diagonal_inertia_kg_m2(self):
        """均質boxの重心まわり慣性。半寸法を全寸法として誤用しない。"""
        x, y, z = self.half_extents_m
        return tuple(self.mass_kg / 3 * v for v in (y*y+z*z, x*x+z*z, x*x+y*y))

    def to_document(self):
        """意味のある単位名で、質量/外観/表面を別fieldへ保存する。"""
        return {"definition_id": self.definition_id, "version": self.version,
            "geometry": {"type": "box", "half_extents_m": self.half_extents_m},
            "inertia": {"mode": "uniform_box", "mass_kg": self.mass_kg},
            "surface": dict(zip(("sliding_friction", "torsional_friction_m", "rolling_friction_m"), self.friction)),
            "appearance": {"rgba": self.rgba}, "provenance": self.provenance}

    @classmethod
    def from_document(cls, raw):
        """他の形状・慣性モデルをboxへ暗黙変換しない。"""
        raw = fields(raw, {"definition_id", "version", "geometry", "inertia", "surface", "appearance", "provenance"}, "definition")
        g = fields(raw["geometry"], {"type", "half_extents_m"}, "geometry")
        i = fields(raw["inertia"], {"mode", "mass_kg"}, "inertia")
        s = fields(raw["surface"], {"sliding_friction", "torsional_friction_m", "rolling_friction_m"}, "surface")
        a = fields(raw["appearance"], {"rgba"}, "appearance")
        if g["type"] != "box" or i["mode"] != "uniform_box":
            raise ValueError("only uniform rigid boxes are supported")
        return cls(raw["definition_id"], raw["version"], g["half_extents_m"], i["mass_kg"],
                   tuple(s[k] for k in ("sliding_friction", "torsional_friction_m", "rolling_friction_m")), a["rgba"], raw["provenance"])


@dataclass(frozen=True, slots=True)
class ObjectInstance:
    """world内の物体instance。fixed/v1を保持し、明示world/v2でdynamic初期速度を扱う。"""
    instance_id: str
    definition_id: str
    definition_version: int
    position_m: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float]
    motion_type: str = "fixed"
    initial_linear_velocity_m_s: tuple[float, float, float] = (0.,0.,0.)
    initial_angular_velocity_rad_s: tuple[float, float, float] = (0.,0.,0.)

    def __post_init__(self):
        identifier(self.instance_id); identifier(self.definition_id)
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ValueError("positive instance definition version required")
        object.__setattr__(self, "position_m", vector(self.position_m, 3))
        q = vector(self.orientation_wxyz, 4)
        if abs(sqrt(sum(v*v for v in q)) - 1) > 1e-9:
            raise ValueError("unit wxyz quaternion required")
        object.__setattr__(self, "orientation_wxyz", q)
        if self.motion_type not in ("fixed", "dynamic"):
            raise ValueError("explicit fixed/dynamic motion required")
        for key in ("initial_linear_velocity_m_s", "initial_angular_velocity_rad_s"):
            object.__setattr__(self,key,vector(getattr(self,key),3))
        if self.motion_type=="fixed" and any(self.initial_linear_velocity_m_s+self.initial_angular_velocity_rad_s):
            raise ValueError("fixed object cannot have initial velocity")

    @classmethod
    def motion_templates(cls):
        """editorの明示motion遷移を、同じObjectInstance初期値契約から公開する。"""
        return {"fixed": {}, "dynamic": {"initial_velocity": {"frame": "mujoco_world",
            "linear_m_s": list(cls.__dataclass_fields__["initial_linear_velocity_m_s"].default),
            "angular_rad_s": list(cls.__dataclass_fields__["initial_angular_velocity_rad_s"].default)}}}

    def to_document(self):
        """固定/座標系は省略せず出力する。"""
        return {"instance_id": self.instance_id, "definition": {"name": self.definition_id, "version": self.definition_version},
            "motion_type": self.motion_type, "pose": {"frame": "mujoco_world", "position_m": self.position_m,
            "orientation_wxyz": self.orientation_wxyz},
            **({} if self.motion_type=="fixed" else {"initial_velocity": {
                "frame":"mujoco_world","linear_m_s":self.initial_linear_velocity_m_s,
                "angular_rad_s":self.initial_angular_velocity_rad_s}})}

    @classmethod
    def from_document(cls, raw, *, allow_dynamic=False):
        """旧v1の意味は維持し、v2でだけdynamic初期状態を受理する。"""
        dynamic = type(raw) is dict and raw.get("motion_type")=="dynamic"
        extra = {"initial_velocity"} if dynamic and allow_dynamic else set()
        raw = fields(raw, {"instance_id", "definition", "motion_type", "pose"}|extra, "instance")
        d = fields(raw["definition"], {"name", "version"}, "definition reference")
        p = fields(raw["pose"], {"frame", "position_m", "orientation_wxyz"}, "pose")
        if p["frame"]!="mujoco_world" or (dynamic and not allow_dynamic):
            raise ValueError("diagnostic requires fixed world-frame instances; dynamic execution is separate")
        velocity = fields(raw["initial_velocity"],{"frame","linear_m_s","angular_rad_s"},"initial velocity") if dynamic else None
        if velocity is not None and velocity["frame"]!="mujoco_world":
            raise ValueError("world initial velocity required")
        return cls(raw["instance_id"],d["name"],d["version"],p["position_m"],p["orientation_wxyz"],raw["motion_type"],
            (0.,0.,0.) if velocity is None else velocity["linear_m_s"],
            (0.,0.,0.) if velocity is None else velocity["angular_rad_s"])


@dataclass(frozen=True, slots=True)
class ContactParameters:
    """MuJoCo接触数値条件。材料弾性率や実物ばね定数ではない。"""
    margin_m: float
    condim: int
    solref: tuple[float, float]
    solimp: tuple[float, float, float, float, float]
    initial_penetration_tolerance_m: float

    def __post_init__(self):
        object.__setattr__(self, "margin_m", number(self.margin_m, nonnegative=True))
        object.__setattr__(self, "initial_penetration_tolerance_m", number(self.initial_penetration_tolerance_m, nonnegative=True))
        if type(self.condim) is not int or self.condim not in (1,3,4,6):
            raise ValueError("invalid contact dimension")
        object.__setattr__(self, "solref", vector(self.solref, 2, positive=True))
        object.__setattr__(self, "solimp", vector(self.solimp, 5, positive=True))
        lo, hi, width, midpoint, power = self.solimp
        if not (0 < lo <= hi < 1 and 0 < midpoint < 1 and power >= 1):
            raise ValueError("invalid solimp")


@dataclass(frozen=True, slots=True)
class ObjectSceneManifest:
    """Task非依存の全実効scene定義。1物体も空sceneも同じ集合表現。"""
    scene_id: str
    definitions: tuple[ObjectDefinition, ...]
    objects: tuple[ObjectInstance, ...]
    contact: ContactParameters
    world: WorldPhysics | None = None

    def __post_init__(self):
        identifier(self.scene_id)
        from .world import WorldPhysics
        if self.world is not None and type(self.world) is not WorldPhysics:
            raise TypeError("typed WorldPhysics required")
        if type(self.contact) is not ContactParameters:
            raise TypeError("typed ContactParameters required")
        for values, cls in ((self.definitions, ObjectDefinition), (self.objects, ObjectInstance)):
            if type(values) is not tuple or len(values) > MAX_OBJECTS or any(type(v) is not cls for v in values):
                raise ValueError("typed bounded scene tuple required")
        if self.world is None and any(o.motion_type!="fixed" for o in self.objects):
            raise ValueError("dynamic instances require an explicit world/v2")
        ids = [(d.definition_id,d.version) for d in self.definitions]
        if len(set(ids)) != len(ids) or len({o.instance_id for o in self.objects}) != len(self.objects):
            raise ValueError("duplicate definition or instance ID")
        if any((o.definition_id,o.definition_version) not in ids for o in self.objects):
            raise ValueError("unknown object definition/version")
        object.__setattr__(self, "definitions", tuple(sorted(self.definitions,key=lambda d:(d.definition_id,d.version))))
        object.__setattr__(self, "objects", tuple(sorted(self.objects,key=lambda o:o.instance_id)))

    def definition_for(self, instance):
        """解決済み参照のみ利用する。"""
        return next(d for d in self.definitions if (d.definition_id,d.version)==(instance.definition_id,instance.definition_version))

    def to_document(self):
        """instance順序で意味が変わらないcanonical projection。"""
        return {"schema_version":"object-scene/v1" if self.world is None else "object-scene/v2", "scene_id":self.scene_id,
                "definitions":[d.to_document() for d in self.definitions],
                "objects":[o.to_document() for o in self.objects], "contact":asdict(self.contact),
                **({} if self.world is None else {"world":self.world.to_document()})}

    @property
    def digest(self):
        """表示色を含めたresolved scene条件digest。"""
        return sha256(canonical(self.to_document())).hexdigest()


def decode_object_scene(document: bytes) -> ObjectSceneManifest:
    """旧固定scene/v1と明示world/v2の完全展開JSONを区別する。"""
    from .world import WorldPhysics
    raw = strict_json(document)
    version = raw.get("schema_version")
    if version not in ("object-scene/v1","object-scene/v2"):
        raise ValueError("unsupported object scene schema")
    fields(raw,{"schema_version","scene_id","definitions","objects","contact"} | ({"world"} if version.endswith("/v2") else set()),"scene")
    for key in ("definitions","objects"):
        if type(raw[key]) is not list or len(raw[key])>MAX_OBJECTS:
            raise ValueError("bounded scene array required")
    c=fields(raw["contact"],{"margin_m","condim","solref","solimp","initial_penetration_tolerance_m"},"contact")
    return ObjectSceneManifest(raw["scene_id"],tuple(ObjectDefinition.from_document(d) for d in raw["definitions"]),
        tuple(ObjectInstance.from_document(o,allow_dynamic=version.endswith("/v2")) for o in raw["objects"]),
        ContactParameters(**c),None if version.endswith("/v1") else WorldPhysics.from_document(raw["world"]))
