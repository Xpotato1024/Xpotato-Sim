"""Scene物理条件と分離した、明示的な数値積分・servo実行設定。"""
from dataclasses import asdict, dataclass
from math import isclose, isfinite
from xpotato_sim.runtime.scene.objects import canonical, fields, number
from hashlib import sha256

DYNAMIC_EXECUTION = "coordinated_actuator_servo_dynamic/v1"


@dataclass(frozen=True, slots=True)
class DynamicsSettings:
    """Robot固有gainを上書きせず、stepと診断用の有界条件を固定する。"""
    physics_dt_s: float
    integrator: str
    solver: str
    iterations: int
    tolerance: float
    max_joint_speed_rad_s: float
    max_tracking_error_rad: float

    def __post_init__(self):
        for key in ("physics_dt_s","tolerance","max_joint_speed_rad_s","max_tracking_error_rad"):
            object.__setattr__(self,key,number(getattr(self,key),positive=True))
        if self.integrator not in ("implicitfast","Euler") or self.solver!="Newton":
            raise ValueError("unsupported explicit integrator/solver")
        if type(self.iterations) is not int or not 1<=self.iterations<=1000:
            raise ValueError("bounded positive solver iterations required")

    def substeps(self, control_dt_s):
        ratio=number(control_dt_s,positive=True)/self.physics_dt_s
        if not isfinite(ratio):
            raise ValueError("nonfinite physics/control period ratio")
        rounded=round(ratio)
        if not 1<=rounded<=1000 or not isclose(ratio,rounded,rel_tol=0,abs_tol=1e-9):
            raise ValueError("control period must be an integer multiple of physics timestep")
        return rounded

    def to_document(self):
        return {"schema_version":"dynamics-settings/v1","semantics":DYNAMIC_EXECUTION,**asdict(self)}

    @property
    def digest(self):
        return sha256(canonical(self.to_document())).hexdigest()

    @classmethod
    def from_document(cls,raw):
        fields(raw,{"schema_version","semantics","physics_dt_s","integrator","solver","iterations","tolerance","max_joint_speed_rad_s","max_tracking_error_rad"},"dynamics")
        if raw["schema_version"]!="dynamics-settings/v1" or raw["semantics"]!=DYNAMIC_EXECUTION:
            raise ValueError("unsupported dynamics settings schema/semantics")
        return cls(**{k:v for k,v in raw.items() if k not in ("schema_version","semantics")})
