"""Robot登録内の同列モデル選択。形状・腕数・初期姿勢はRobot側が所有する。"""
from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.runtime.execution.coordinated import CoordinatedMotionProvider
from xpotato_sim.runtime.composition.viewer_robot_declaration import ViewerRobotDeclaration
from xpotato_sim.schemas import MuJoCoState


class ModelViewerResources(Protocol):
    declaration: ViewerRobotDeclaration
    @property
    def metadata(self) -> Mapping[str, object]: ...
    def write_public_tree(self, root: Path) -> tuple[Path, ...]: ...


class ModelMotionProvider(CoordinatedMotionProvider, Protocol):
    def transport_state(self, *, frame_index: int, metadata: Mapping[str, object]) -> MuJoCoState: ...


@dataclass(frozen=True, slots=True)
class RobotModelInstance:
    provider: ModelMotionProvider
    viewer: ModelViewerResources


@dataclass(frozen=True, slots=True)
class RobotModelRegistration:
    """実行factoryは固定Plugin宣言だけから取得し、設定のimport pathは受け付けない。"""
    identity: VersionedIdentity
    endpoint_ids: tuple[str, ...]
    joint_names: tuple[str, ...]
    build_instance: Callable[[], RobotModelInstance]
    configuration_json: str = "{}"

    @property
    def configuration_sha256(self) -> str:
        return sha256(self.configuration_json.encode("utf-8")).hexdigest()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VersionedIdentity):
            raise TypeError("model identity must be versioned")
        for label, names in (("endpoints", self.endpoint_ids), ("joints", self.joint_names)):
            if type(names) is not tuple or not names or any(type(n) is not str or not n for n in names) or len(set(names)) != len(names):
                raise ValueError(f"model {label} must be nonempty distinct names")
        if type(self.configuration_json) is not str:
            raise TypeError("model configuration must be canonical JSON text")
        parsed = json.loads(self.configuration_json)
        if type(parsed) is not dict or json.dumps(parsed, sort_keys=True, separators=(",", ":"), allow_nan=False) != self.configuration_json:
            raise ValueError("model configuration must be a canonical JSON object")
        if not callable(self.build_instance):
            raise TypeError("model factory must be callable")

    def build(self) -> RobotModelInstance:
        value = self.build_instance()
        if type(value) is not RobotModelInstance:
            raise TypeError("model factory returned an invalid instance")
        snapshot = value.provider.snapshot()
        if value.provider.endpoint_ids != self.endpoint_ids or snapshot.joint_names != self.joint_names:
            raise ValueError("model provider identity/order mismatch")
        if value.viewer.declaration.joint_names != self.joint_names or value.viewer.declaration.qpos_dimension != len(self.joint_names):
            raise ValueError("model viewer/provider joint mismatch")
        if value.viewer.metadata.get("model_sha256") != snapshot.model_sha256:
            raise ValueError("model viewer/provider artifact digest mismatch")
        return value
