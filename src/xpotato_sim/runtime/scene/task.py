"""幾何接触の有限観測Taskに渡すbackend非依存context。"""
from dataclasses import dataclass
from .objects import ObjectSceneManifest, identifier
from .observation import SceneGeometryObservation


@dataclass(frozen=True, slots=True)
class GeometryTaskContext:
    """対象sceneと入力sessionを固定する。modelの私有dataや力を持たない。"""
    manifest: ObjectSceneManifest
    endpoint_ids: tuple[str, ...]
    epoch: str

    def __post_init__(self):
        """観測対象とsessionの対応を未定義値で作らない。"""
        if type(self.manifest) is not ObjectSceneManifest:
            raise TypeError("typed scene manifest required")
        if type(self.endpoint_ids) is not tuple or not self.endpoint_ids or len(set(self.endpoint_ids)) != len(self.endpoint_ids):
            raise ValueError("distinct endpoint IDs required")
        for endpoint in self.endpoint_ids:
            identifier(endpoint)
        if type(self.epoch) is not str or not self.epoch or len(self.epoch) > 256:
            raise ValueError("bounded task epoch required")


@dataclass(frozen=True, slots=True)
class GeometryTaskObservation:
    """同じscene frameの観測とruntime側の停止理由。"""
    geometry: SceneGeometryObservation
    stopped_reason: str | None = None
    budget_exhausted: bool = False

    def __post_init__(self):
        """終了理由と観測の型を検査する。"""
        if type(self.geometry) is not SceneGeometryObservation:
            raise TypeError("typed scene geometry required")
        if type(self.budget_exhausted) is not bool:
            raise TypeError("budget_exhausted must be boolean")
        if self.stopped_reason is not None and (type(self.stopped_reason) is not str or not self.stopped_reason):
            raise ValueError("nonempty stop reason required")
