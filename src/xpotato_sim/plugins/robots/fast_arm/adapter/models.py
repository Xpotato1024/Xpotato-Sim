"""Robot Pluginへ原型・左単腕・右単腕・双腕を同じ型で登録する。"""
from functools import partial
from dataclasses import replace
from fast_arm_core.assembly_model import fast_arm_model_digest, build_fast_arm_assembly_model
from .tool_collision import add_tool_colliders
from xpotato_sim.runtime.scene.contracts import ModelScenePlan
import json
from fast_arm_core.models import FAST_ARM_MODEL_DEFINITIONS, FastArmModelDefinition
from xpotato_sim.runtime.composition.robot_model import RobotModelRegistration, RobotModelInstance
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from .coordinated import FastArmAssemblyMotionProvider
from .dynamic import FastArmDynamicMotionProvider
from .assembly_scene import build_fast_arm_assembly_scene
from .resources import FAST_ARM_SCENE_RESOURCE
from .assembly_viewer import build_fast_arm_assembly_viewer_bundle


def _build(definition: FastArmModelDefinition, *, scene_plan: ModelScenePlan | None = None) -> RobotModelInstance:
    built = (build_fast_arm_assembly_model(definition.assembly)
             if scene_plan is not None and scene_plan.manifest.world is not None
             else build_fast_arm_assembly_scene(definition.assembly))
    scene = None
    if scene_plan is not None:
        built, colliders = add_tool_colliders(built, scene_plan.collision_profile)
        scene = scene_plan.compose(built.xml, built.assets, colliders)
        built = replace(built, xml=scene.xml, model_sha256=fast_arm_model_digest(scene.xml,built.assets))
    provider = (FastArmAssemblyMotionProvider(definition.assembly,built=built,object_scene=scene)
        if scene_plan is None or scene_plan.dynamics is None else
        FastArmDynamicMotionProvider(definition.assembly,built=built,object_scene=scene,settings=scene_plan.dynamics))
    viewer = build_fast_arm_assembly_viewer_bundle(
        definition.assembly, profile_id="fast_arm_" + definition.name, built=provider.built,
        scene_layout=provider.state_layout if isinstance(provider,FastArmDynamicMotionProvider) else None)
    return RobotModelInstance(provider, viewer)


FAST_ARM_MODELS = tuple(
    RobotModelRegistration(VersionedIdentity(d.name, d.version), d.assembly.arm_ids,
                           d.assembly.joint_names, partial(_build, d),
                           json.dumps({"assembly": d.assembly.to_dict(), "placement_evidence": d.placement_evidence,
                                       "base_scene_resource": FAST_ARM_SCENE_RESOURCE.logical_identifier},
                                      sort_keys=True, separators=(",", ":"), allow_nan=False))
    for d in FAST_ARM_MODEL_DEFINITIONS
)
