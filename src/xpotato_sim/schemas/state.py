"""MuJoCo backendからtransport/viewerへ渡すstate snapshot schema。

MuJoCoがphysical stateのSoTであり、RenderStateはprojectionに限定される。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from xpotato_sim.schemas.types import QuaternionWXYZ, Vector3


@dataclass(frozen=True, slots=True)
class BodyTransform:
    """world frameのbody pose。positionはm、quaternion orderingはwxyz。"""

    name: str
    position_m: Vector3
    quaternion_wxyz: QuaternionWXYZ


@dataclass(frozen=True, slots=True)
class SiteTransform:
    """world frameのMuJoCo site pose。positionはm、quaternion orderingはwxyz。"""

    name: str
    position_m: Vector3
    quaternion_wxyz: QuaternionWXYZ


@dataclass(frozen=True, slots=True)
class MuJoCoState:
    """1 simulation snapshotのqposとworld transforms。

    ``qpos`` ordering/unitはloaded MuJoCo modelが所有し、consumerは並べ替えやFK再計算を
    行わない。
    """

    frame_index: int
    time_s: float
    qpos: tuple[float, ...] = ()
    qvel: tuple[float, ...] = ()
    bodies: tuple[BodyTransform, ...] = ()
    sites: tuple[SiteTransform, ...] = ()
    target_position_m: Vector3 | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RenderState:
    """MuJoCoStateを描画用に投影した値。独立したphysical state SoTではない。"""

    frame_index: int
    time_s: float
    metadata: Mapping[str, object] = field(default_factory=dict)


__all__ = ["BodyTransform", "MuJoCoState", "RenderState", "SiteTransform"]
