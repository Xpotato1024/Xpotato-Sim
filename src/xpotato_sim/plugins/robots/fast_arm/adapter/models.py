"""Robot Pluginへ原型・左単腕・右単腕・双腕を同じ型で登録する。"""
from functools import partial
import json
from fast_arm_core.models import FAST_ARM_MODEL_DEFINITIONS, FastArmModelDefinition
from xpotato_sim.runtime.composition.robot_model import RobotModelRegistration, RobotModelInstance
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from .coordinated import FastArmAssemblyMotionProvider
from .assembly_scene import build_fast_arm_assembly_scene
from .resources import FAST_ARM_SCENE_RESOURCE
from .assembly_viewer import build_fast_arm_assembly_viewer_bundle


def _build(definition: FastArmModelDefinition) -> RobotModelInstance:
    built = build_fast_arm_assembly_scene(definition.assembly)
    provider = FastArmAssemblyMotionProvider(definition.assembly, built=built)
    viewer = build_fast_arm_assembly_viewer_bundle(
        definition.assembly, profile_id="fast_arm_" + definition.name, built=provider.built)
    return RobotModelInstance(provider, viewer)


FAST_ARM_MODELS = tuple(
    RobotModelRegistration(VersionedIdentity(d.name, d.version), d.assembly.arm_ids,
                           d.assembly.joint_names, partial(_build, d),
                           json.dumps({"assembly": d.assembly.to_dict(), "placement_evidence": d.placement_evidence,
                                       "base_scene_resource": FAST_ARM_SCENE_RESOURCE.logical_identifier},
                                      sort_keys=True, separators=(",", ":"), allow_nan=False))
    for d in FAST_ARM_MODEL_DEFINITIONS
)
