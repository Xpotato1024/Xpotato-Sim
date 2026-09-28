"""world/物体sceneのpackage-owned資源と既存Environment entry。"""
from importlib.resources import files
from xpotato_sim.runtime.scene.objects import decode_object_scene, strict_json, canonical, fields, identifier, ObjectDefinition
from xpotato_sim.runtime.scene.contracts import ObjectSceneBuildRequest, ObjectSceneProvider, SceneResetTarget
from xpotato_sim.runtime.scene.composition import compose_object_scene
from xpotato_sim.runtime.experiment.contracts import EnvironmentPlugin, EnvironmentRole, SemanticRole, ParameterContract, ParameterField, VersionedIdentity


class ConfiguredObjectSceneProvider:
    """選択はpureなJSON展開、構築はtyped requestで別操作とする。"""
    def resolve_parameters(self, parameters):
        if type(parameters) is not dict:
            raise ValueError("Environment parameters must be an object")
        if set(parameters)=={"scene"}:
            return decode_object_scene(canonical(parameters["scene"]))
        fields(parameters,{"preset"},"Environment parameters")
        name=identifier(parameters["preset"])
        resource_root=files(__package__).joinpath("resources")
        path=resource_root.joinpath("scenes",name+".json")
        if not path.is_file():
            raise ValueError("unknown object scene preset")
        doc=strict_json(path.read_bytes())
        version=doc.get("schema_version")
        fields(doc,{"schema_version","scene_id","definition_resources","objects","contact"} | ({"world"} if version=="object-scene-preset/v2" else set()),"scene preset")
        if doc.pop("schema_version") not in ("object-scene-preset/v1","object-scene-preset/v2"):
            raise ValueError("unsupported scene preset")
        refs=doc.pop("definition_resources")
        if type(refs) is not list or len(refs)>32:
            raise ValueError("bounded resource references required")
        definitions=[]
        for ref in refs:
            identifier(ref)
            asset=resource_root.joinpath("objects",ref+".json")
            if not asset.is_file():
                raise ValueError("unknown object resource")
            definitions.append(ObjectDefinition.from_document(strict_json(asset.read_bytes())).to_document())
        doc.update(schema_version="object-scene/v2" if version.endswith("/v2") else "object-scene/v1",definitions=definitions)
        return decode_object_scene(canonical(doc))

    def compose_scene(self, parameters):
        fields(parameters,{"request"},"scene build")
        return compose_object_scene(parameters["request"])

    def reset_scene(self, scene):
        """同じlive ownerのresetへ委譲する。immutable planをreset済みと偽装しない。"""
        if not isinstance(scene,SceneResetTarget) or scene.scene_manifest is None:
            raise TypeError("live object-scene reset target required")
        # Robot、全物体、contact/warm-startは一つのMjData resetで戻す。Taskはruntimeの別owner。
        scene.reset()


ENVIRONMENT_PLUGIN=EnvironmentPlugin(
    identity=VersionedIdentity("object_scene_environment",1),scene_provider=ConfiguredObjectSceneProvider(),
    roles=(EnvironmentRole(SemanticRole("environment.objects"),"scene_objects","mujoco_world","meter"),),
    parameter_contract=ParameterContract((ParameterField("request",ObjectSceneBuildRequest,condition_specific=True),)),
    produced_evidence=frozenset(),compatible_backend_kinds=frozenset({"mujoco"}),
)
