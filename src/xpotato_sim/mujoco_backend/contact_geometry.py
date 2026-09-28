"""MuJoCo contact配列の幾何読取り。力/Task/対象分類とは独立した共通primitive。"""
from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True, slots=True)
class ContactGeometry:
    """geom1からgeom2への法線とworld接触点。単位m、frameはrow-major。"""
    point_world_m: tuple[float, float, float]
    distance_m: float
    frame_world: tuple[float, ...]

    @property
    def normal_world(self):
        """contact frameの第一軸は法線。"""
        return self.frame_world[:3]

    @property
    def penetration_m(self):
        """接触marginと無関係の、形状間の負distanceだけを食い込みとする。"""
        return max(0.0, -self.distance_m)


def read_contact_geometry(contact: object) -> ContactGeometry:
    """一つのnative/fake contactから有限な幾何を取得する。force APIは呼ばない。"""
    def finite(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("finite contact number required")
        result = float(value)
        if not isfinite(result):
            raise ValueError("nonfinite contact geometry")
        return 0.0 if result == 0.0 else result

    def vector(value, n):
        if isinstance(value, (str, bytes, bytearray)) or not hasattr(value, "__len__") or len(value) != n:
            raise ValueError("contact vector dimension mismatch")
        values = tuple(finite(v) for v in value)
        if not all(isfinite(v) for v in values):
            raise ValueError("nonfinite contact geometry")
        return values
    point = vector(contact.pos, 3)
    frame = vector(contact.frame, 9)
    distance = finite(contact.dist)
    if not isfinite(distance) or abs(sqrt(sum(v*v for v in frame[:3]))-1.) > 1e-9:
        raise ValueError("invalid contact distance/normal")
    return ContactGeometry(point, distance, frame)
