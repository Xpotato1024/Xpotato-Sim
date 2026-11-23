"""Environmentが所有する登録済み柔軟体格子の型付き設定。MuJoCoへ材料計算を再実装しない。"""
from __future__ import annotations

from dataclasses import dataclass
from math import prod
import xml.etree.ElementTree as ET

from .objects import fields, identifier, number, vector


def _ints(value, size, minimum=0):
    if type(value) is not list or len(value) != size:
        raise ValueError("bounded flex integer sequence required")
    if any(type(v) is not int or v < minimum or v > 4096 for v in value):
        raise ValueError("invalid flex grid integer")
    return tuple(value)


def _nums(value, length):
    return tuple(vector(value, length))


def _attr(values):
    return " ".join(format(float(x), ".17g") for x in values)


@dataclass(frozen=True, slots=True)
class FlexGrid:
    """個々のflexの物理条件と初期構成。world/solverはEnvironmentではなくExecutionが所有。"""

    flex_id: str
    dim: int
    count: tuple[int, int, int]
    spacing_m: tuple[float, float, float]
    position_m: tuple[float, float, float]
    euler_deg: tuple[float, float, float]
    radius_m: float
    mass_kg: float
    rgba: tuple[float, float, float, float]
    pin_gridrange: tuple[int, ...]
    contact_condim: int
    contact_solref: tuple[float, float]
    contact_solimp: tuple[float, float, float]
    young_pa: float
    poisson: float
    thickness_m: float | None
    elastic2d: str | None
    damping: float
    yield_strain: tuple[float, ...] | None
    provenance: str

    def __post_init__(self):
        identifier(self.flex_id)
        if type(self.dim) is not int or self.dim not in (2, 3):
            raise ValueError("only 2D/3D grid flex is supported")
        count = self.count
        if type(count) is not tuple or len(count) != 3 or any(type(v) is not int or v < 1 or v > 32 for v in count):
            raise ValueError("invalid bounded flex grid count")
        if count[0] < 2 or count[1] < 2 or (self.dim == 3 and count[2] < 2) or (self.dim == 2 and count[2] != 1):
            raise ValueError("flex grid dimension/count mismatch")
        if prod(count) > 1024:
            raise ValueError("flex grid vertex budget exceeded")
        for key in ("spacing_m", "position_m", "euler_deg"):
            object.__setattr__(self, key, vector(getattr(self, key), 3, positive=(key == "spacing_m")))
        for key in ("radius_m", "mass_kg", "young_pa"):
            object.__setattr__(self, key, number(getattr(self, key), positive=True))
        object.__setattr__(self, "rgba", vector(self.rgba, 4, nonnegative=True))
        if any(c > 1 for c in self.rgba):
            raise ValueError("flex RGBA out of range")
        pin_tuple_width = 4 if self.dim == 2 else 6
        if (type(self.pin_gridrange) is not tuple or not self.pin_gridrange
                or len(self.pin_gridrange) % pin_tuple_width):
            raise ValueError("explicit dimensional pin ranges required")
        if any(type(x) is not int or x < 0 or x > 4096 for x in self.pin_gridrange):
            raise ValueError("invalid pin gridrange")
        if self.contact_condim not in (1, 3):
            raise ValueError("invalid flex contact dimension")
        object.__setattr__(self, "contact_solref", vector(self.contact_solref, 2, positive=True))
        object.__setattr__(self, "contact_solimp", vector(self.contact_solimp, 3, positive=True))
        if not 0 < self.contact_solimp[0] <= self.contact_solimp[1] < 1:
            raise ValueError("invalid flex solimp")
        object.__setattr__(self, "poisson", number(self.poisson, nonnegative=True))
        object.__setattr__(self, "damping", number(self.damping, nonnegative=True))
        if self.poisson >= .5:
            raise ValueError("Poisson ratio must be below 0.5")
        if self.dim == 2:
            if self.thickness_m is None or self.elastic2d not in ("bend", "stretch", "both"):
                raise ValueError("2D flex requires thickness and bending/stretching model")
            object.__setattr__(self, "thickness_m", number(self.thickness_m, positive=True))
        elif self.thickness_m is not None or self.elastic2d is not None:
            raise ValueError("3D flex has no 2D thickness or mode")
        if self.yield_strain is not None:
            if type(self.yield_strain) is not tuple or len(self.yield_strain) != (2 if self.dim == 2 and self.elastic2d == "both" else 1):
                raise ValueError("flex yield dimension mismatch")
            object.__setattr__(self, "yield_strain", vector(self.yield_strain, len(self.yield_strain), positive=True))
        if type(self.provenance) is not str or not self.provenance.strip():
            raise ValueError("flex provenance required")

    @classmethod
    def from_document(cls, value):
        raw = fields(value, {"schema_version", "flex_id", "grid", "pose", "contact", "elasticity", "surface", "provenance"}, "flex resource")
        if raw["schema_version"] != "flex-grid/v1":
            raise ValueError("unsupported flex resource version")
        g = fields(raw["grid"], {"dim", "count", "spacing_m", "radius_m", "mass_kg", "pin_gridrange"}, "flex grid")
        p = fields(raw["pose"], {"frame", "position_m", "euler_deg"}, "flex pose")
        if p["frame"] != "mujoco_world":
            raise ValueError("flex pose must use world frame")
        c = fields(raw["contact"], {"condim", "solref", "solimp"}, "flex contact")
        e = fields(raw["elasticity"], {"young_pa", "poisson", "thickness_m", "elastic2d", "damping", "yield_strain"}, "flex elasticity")
        s = fields(raw["surface"], {"rgba"}, "flex surface")
        ys = e["yield_strain"]
        if ys is not None and type(ys) is not list:
            raise ValueError("flex yield must be a list or null")
        pins = g["pin_gridrange"]
        if type(pins) is not list:
            raise ValueError("flex pins must be a list")
        return cls(raw["flex_id"], g["dim"], _ints(g["count"], 3, 1), _nums(g["spacing_m"], 3),
                   _nums(p["position_m"], 3), _nums(p["euler_deg"], 3), g["radius_m"], g["mass_kg"],
                   _nums(s["rgba"], 4), tuple(pins), c["condim"], _nums(c["solref"], 2),
                   _nums(c["solimp"], 3), e["young_pa"], e["poisson"], e["thickness_m"], e["elastic2d"],
                   e["damping"], None if ys is None else tuple(ys), raw["provenance"])

    def to_document(self):
        return {"schema_version": "flex-grid/v1", "flex_id": self.flex_id,
                "grid": {"dim": self.dim, "count": list(self.count), "spacing_m": self.spacing_m,
                         "radius_m": self.radius_m, "mass_kg": self.mass_kg,
                         "pin_gridrange": list(self.pin_gridrange)},
                "pose": {"frame": "mujoco_world", "position_m": self.position_m, "euler_deg": self.euler_deg},
                "contact": {"condim": self.contact_condim, "solref": self.contact_solref,
                            "solimp": self.contact_solimp},
                "elasticity": {"young_pa": self.young_pa, "poisson": self.poisson,
                               "thickness_m": self.thickness_m, "elastic2d": self.elastic2d,
                               "damping": self.damping, "yield_strain": self.yield_strain},
                "surface": {"rgba": self.rgba}, "provenance": self.provenance}

    def add_to(self, world: ET.Element) -> None:
        """MJCFを唯一のMuJoCo solverへ渡す。"""
        fc = ET.SubElement(world, "flexcomp",
            {"name": "flex__"+self.flex_id, "type": "grid", "dim": str(self.dim),
             "count": " ".join(str(v) for v in self.count), "spacing": _attr(self.spacing_m),
             "pos": _attr(self.position_m), "euler": _attr(self.euler_deg),
             "radius": str(self.radius_m), "mass": str(self.mass_kg), "rgba": _attr(self.rgba)})
        ET.SubElement(fc, "pin", {"gridrange": " ".join(str(v) for v in self.pin_gridrange)})
        ET.SubElement(fc, "contact", {"condim": str(self.contact_condim),
            "solref": _attr(self.contact_solref), "solimp": _attr(self.contact_solimp),
            "selfcollide": "none"})
        elasticity = {"young": str(self.young_pa), "poisson": str(self.poisson),
                      "damping": str(self.damping)}
        if self.thickness_m is not None:
            elasticity.update(thickness=str(self.thickness_m), elastic2d=self.elastic2d)
        if self.yield_strain is not None:
            elasticity["yield"] = _attr(self.yield_strain)
        ET.SubElement(fc, "elasticity", elasticity)
