"""Robotの出力XMLへEnvironment所有の固定物体を合成する型付き境界。"""
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from types import MappingProxyType
from xpotato_sim.runtime.scene.objects import ObjectSceneManifest, identifier
from xpotato_sim.runtime.execution.physics import DynamicsSettings


@dataclass(frozen=True, slots=True)
class ToolColliderBinding:
    """Robotが名前を決める。Environmentはjoint名や左右命名を推測しない。"""
    endpoint_id: str
    geom_name: str
    friction: tuple[float, float, float]

    def __post_init__(self):
        identifier(self.endpoint_id)
        if type(self.geom_name) is not str or not self.geom_name:
            raise ValueError("named Robot collider required")
        from .objects import vector
        object.__setattr__(self, "friction", vector(self.friction, 3, nonnegative=True))


@dataclass(frozen=True, slots=True)
class ObjectSceneBuildRequest:
    """Task条件を含まない、pure scene合成への完全入力。"""
    model_xml: bytes
    model_assets: Mapping[str, bytes]
    manifest: ObjectSceneManifest
    colliders: tuple[ToolColliderBinding, ...]
    dynamics: DynamicsSettings | None = None

    def __post_init__(self):
        """呼出側の可変asset辞書や無効なbindingを保持しない。"""
        if type(self.model_xml) is not bytes or not self.model_xml or self.model_xml.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 model XML bytes required")
        if not isinstance(self.model_assets, Mapping) or any(type(k) is not str or type(v) is not bytes for k,v in self.model_assets.items()):
            raise TypeError("named model asset bytes required")
        if type(self.manifest) is not ObjectSceneManifest or type(self.colliders) is not tuple or not self.colliders or any(type(c) is not ToolColliderBinding for c in self.colliders):
            raise TypeError("typed manifest and collider bindings required")
        if self.dynamics is not None and type(self.dynamics) is not DynamicsSettings:
            raise TypeError("typed dynamics settings required")
        if self.dynamics is not None and self.manifest.world is None:
            raise ValueError("dynamic execution requires explicit world/v2")
        if self.dynamics is None and any(o.motion_type=="dynamic" for o in self.manifest.objects):
            raise ValueError("dynamic object requires dynamic execution")
        object.__setattr__(self,"model_assets",MappingProxyType(dict(self.model_assets)))


@dataclass(frozen=True, slots=True)
class ComposedObjectScene:
    """最終XMLと観測用の一意なrole binding。"""
    xml: bytes
    manifest: ObjectSceneManifest
    colliders: tuple[ToolColliderBinding, ...]
    object_geoms: tuple[tuple[str, str], ...]
    dynamic_execution: bool = False

    def __post_init__(self):
        """Environmentが宣言したinstanceと観測用geomの対応を完全に照合する。"""
        if type(self.dynamic_execution) is not bool:
            raise TypeError("explicit execution mode flag required")
        if type(self.xml) is not bytes or not self.xml or type(self.manifest) is not ObjectSceneManifest:
            raise TypeError("typed composed scene required")
        if type(self.colliders) is not tuple or any(type(c) is not ToolColliderBinding for c in self.colliders):
            raise TypeError("typed collider tuple required")
        if type(self.object_geoms) is not tuple or any(type(p) is not tuple or len(p)!=2 or any(type(v) is not str or not v for v in p) for p in self.object_geoms):
            raise ValueError("named object geom bindings required")
        object_ids = [p[0] for p in self.object_geoms]
        geom_names = [p[1] for p in self.object_geoms]
        if len(set(object_ids))!=len(object_ids) or set(object_ids)!={o.instance_id for o in self.manifest.objects}:
            raise ValueError("composed object identity mismatch")
        if len(set(geom_names))!=len(geom_names) or set(geom_names)&{c.geom_name for c in self.colliders}:
            raise ValueError("ambiguous composed scene geom binding")


@runtime_checkable
class ObjectSceneProvider(Protocol):
    """既存Environment軸に追加するscene選択能力。新しいplugin軸は作らない。"""
    def resolve_parameters(self, parameters: dict) -> ObjectSceneManifest: ...
    def compose_scene(self, parameters: dict) -> ComposedObjectScene: ...
    def reset_scene(self, scene: object) -> None: ...


@runtime_checkable
class SceneResetTarget(Protocol):
    """Environment resetが受け付ける、scene identityを公開した生きたstate owner。"""
    @property
    def scene_manifest(self) -> ObjectSceneManifest | None: ...
    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ModelScenePlan:
    """composition rootが検証したEnvironmentを、Robot model factoryへ渡す。"""
    manifest: ObjectSceneManifest
    provider: ObjectSceneProvider
    collision_profile: str
    dynamics: DynamicsSettings | None = None

    def __post_init__(self):
        """入口で型とcollision選択を検証し、callback風の任意設定を許可しない。"""
        if type(self.manifest) is not ObjectSceneManifest or not isinstance(self.provider,ObjectSceneProvider):
            raise TypeError("typed scene manifest/provider required")
        if type(self.collision_profile) is not str or not self.collision_profile:
            raise ValueError("explicit collision profile required")
        if self.dynamics is not None and type(self.dynamics) is not DynamicsSettings:
            raise TypeError("typed dynamics settings required")
        if self.dynamics is not None and self.manifest.world is None:
            raise ValueError("dynamic execution requires explicit world/v2")
        if self.dynamics is None and any(o.motion_type=="dynamic" for o in self.manifest.objects):
            raise ValueError("dynamic object cannot be silently frozen by kinematic execution")

    def compose(self, model_xml: bytes, model_assets: Mapping[str, bytes], colliders: tuple[ToolColliderBinding, ...]) -> ComposedObjectScene:
        """trusted Environment entryへtyped requestを渡す。"""
        result = self.provider.compose_scene({"request":ObjectSceneBuildRequest(model_xml,model_assets,self.manifest,colliders,self.dynamics)})
        if type(result) is not ComposedObjectScene or result.manifest != self.manifest or result.colliders != colliders or result.dynamic_execution != (self.dynamics is not None):
            raise ValueError("Environment returned a different scene binding")
        return result
