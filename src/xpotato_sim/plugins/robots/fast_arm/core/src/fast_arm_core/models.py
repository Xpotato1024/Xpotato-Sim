"""FastArmの原型・左右単腕・双腕を同列のモデル定義として保持する。

胴体座標は+X前方、+Y左、+Z上。sourceの肩中心(0,0,0.7)を取付点へ写す。
写真で指定された30度の取付姿勢と、未実測の合成位置を混同しない。
"""
from __future__ import annotations
from dataclasses import dataclass
from math import cos, sin, radians
from .assembly import FastArmAssembly, FastArmInstance, finite_tuple

SOURCE_SHOULDER_CENTER_M = (0.0, 0.0, 0.7)
SOURCE_OUTWARD_NORMAL = (1.0, 0.0, 0.0)


def torso_shoulder_instance(*, arm_id: str, side: str,
                            shoulder_center_m: tuple[float, float, float],
                            cant_degrees: float = 30.0) -> FastArmInstance:
    """水平外向きから下へcantする肩を、肩中心を固定して配置する。

    原型の+X法線を左右へ振る基準姿勢Rz(+/-90)と、胴体Xまわりのcantを合成。
    左は原型、右はXZ鏡映。原型の負elbow角が両側で胴体前方へ曲がる配置になる。
    出力は既存assembly/v1の原点基準剛体変換であり、joint zeroや配線は変更しない。
    """
    if side not in ("left", "right"):
        raise ValueError("explicit left/right mounting side required")
    center = finite_tuple(shoulder_center_m, 3, "shoulder_center_m")
    angle, = finite_tuple((cant_degrees,), 1, "cant_degrees")
    if not 0 <= angle < 90:
        raise ValueError("cant_degrees must be in [0, 90)")
    direction = 1 if side == "left" else -1
    roll = -direction * radians(angle)
    yaw = direction * radians(90.0)
    cr, sr, cy, sy = cos(roll / 2), sin(roll / 2), cos(yaw / 2), sin(yaw / 2)
    # qx(roll) * qz(yaw)。sourceのbase内にあるRz(90)はここで二重適用しない。
    quaternion = (cr * cy, sr * cy, -sr * sy, cr * sy)
    # p_world = R * (p_source - shoulder_source) + shoulder_world
    z0 = SOURCE_SHOULDER_CENTER_M[2]
    translated = (center[0], center[1] + sin(roll) * z0,
                  center[2] - cos(roll) * z0)
    return FastArmInstance(arm_id, side == "right", translated, quaternion)


@dataclass(frozen=True, slots=True)
class FastArmModelDefinition:
    """腕数ではなく、選択したモデルの明示instance集合が構成の正本。"""
    name: str
    version: int
    assembly: FastArmAssembly
    placement_evidence: str


# 位置は従来の合成肩中心間隔0.8 m、高さ0.7 mを保持。実機寸法ではない。
_LEFT = torso_shoulder_instance(arm_id="left", side="left", shoulder_center_m=(0., .4, .7))
_RIGHT = torso_shoulder_instance(arm_id="right", side="right", shoulder_center_m=(0., -.4, .7))
FAST_ARM_MODEL_DEFINITIONS = (
    FastArmModelDefinition("single_original", 1,
        FastArmAssembly((FastArmInstance("arm", False, (0.,0.,0.), (1.,0.,0.,0.)),)),
        "canonical source frame; not an anatomical side"),
    FastArmModelDefinition("single_left", 1, FastArmAssembly((_LEFT,)),
        "30 degree photo constraint; synthetic unmeasured shoulder position"),
    FastArmModelDefinition("single_right", 1, FastArmAssembly((_RIGHT,)),
        "30 degree photo constraint; synthetic unmeasured shoulder position"),
    FastArmModelDefinition("bimanual", 1, FastArmAssembly((_LEFT, _RIGHT)),
        "30 degree photo constraint; synthetic unmeasured shoulder positions"),
)


def resolve_fast_arm_model(name: str, version: int = 1) -> FastArmModelDefinition:
    for definition in FAST_ARM_MODEL_DEFINITIONS:
        if definition.name == name and type(version) is int and definition.version == version:
            return definition
    raise ValueError(f"unknown FastArm model selection: {name!r}/v{version}")
