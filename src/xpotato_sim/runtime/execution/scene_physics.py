"""Scene単独の数値条件。Robot servoと物理solverのパラメータを混同しない。"""
from dataclasses import dataclass
from math import isclose, isfinite
from hashlib import sha256
from xpotato_sim.runtime.scene.objects import canonical, fields, number


@dataclass(frozen=True, slots=True)
class ScenePhysicsSettings:
    physics_dt_s: float
    integrator: str
    solver: str
    iterations: int
    tolerance: float
    speculative: bool

    def __post_init__(self):
        object.__setattr__(self,"physics_dt_s",number(self.physics_dt_s,positive=True))
        object.__setattr__(self,"tolerance",number(self.tolerance,positive=True))
        if self.integrator not in ("Euler","implicitfast","discrete") or self.solver not in ("Newton","CG"):
            raise ValueError("unsupported Scene physical integrator or solver")
        if type(self.iterations) is not int or not 1 <= self.iterations <= 1000:
            raise ValueError("bounded solver iterations required")
        if type(self.speculative) is not bool:
            raise ValueError("speculative must be explicit true/false")

    def substeps(self, control_dt_s):
        ratio=number(control_dt_s,positive=True)/self.physics_dt_s
        rounded=round(ratio)
        if not isfinite(ratio) or not 1 <= rounded <= 1000 or not isclose(ratio,rounded,abs_tol=1e-9,rel_tol=0):
            raise ValueError("scene/control cadence must be an integer number of physics steps")
        return rounded

    def to_document(self):
        return {"schema_version":"scene-physics/v1","physics_dt_s":self.physics_dt_s,
                "integrator":self.integrator,"solver":self.solver,"iterations":self.iterations,
                "tolerance":self.tolerance,"speculative":self.speculative}

    @property
    def digest(self):
        return sha256(canonical(self.to_document())).hexdigest()

    @classmethod
    def from_document(cls,raw):
        raw=fields(raw,{"schema_version","physics_dt_s","integrator","solver","iterations","tolerance","speculative"},"scene physics")
        if raw["schema_version"]!="scene-physics/v1":
            raise ValueError("unknown scene physics schema")
        return cls(**{k:v for k,v in raw.items() if k!="schema_version"})
