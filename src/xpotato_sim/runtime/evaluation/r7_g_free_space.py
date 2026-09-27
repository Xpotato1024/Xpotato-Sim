"""R7-G free-spaceのsingle-target execution-candidate manifest fixture。

production catalogだけでworld/tool conditionをfreezeし、+Y targetへ向かうbounded
analog sample列と終端zero holdを宣言する。4-target pilot designの代替ではなく、
readiness中にInput Source開始、MuJoCo model load/step、到達可能性の観測は行わない。
"""

from __future__ import annotations

from dataclasses import replace

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    InitialStateContractProvider,
    RESET_INITIAL_STATE_V1,
)
from xpotato_sim.runtime.evaluation.manifest import (
    EVALUATION_MANIFEST_CONTRACT_VERSION,
    EVALUATION_MANIFEST_SCHEMA_VERSION,
    EvaluationConditionPair,
    EvaluationManifest,
)
from xpotato_sim.runtime.experiment.composition import PluginParameters
from xpotato_sim.runtime.experiment.contracts import (
    LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    VersionedIdentity,
)


R7_G_ROBOT_SELECTION = PluginSelection("fast_arm", 1)
R7_G_ENVIRONMENT_SELECTION = PluginSelection("free_space_environment", 1)
R7_G_INPUT_SOURCE_SELECTION = PluginSelection("analog_fixture", 1)
R7_G_MAPPING_SELECTION = PluginSelection("analog_fixture_mapping", 1)
R7_G_TASK_SELECTION = PluginSelection("endpoint_reach_task", 1)
R7_G_EVALUATOR_SELECTIONS = (
    PluginSelection("success_within_timeout", 1),
    PluginSelection("off_axis_drift", 1),
    PluginSelection("completion_time", 1),
    PluginSelection("final_endpoint_error", 1),
)

R7_G_FIXTURE_NONZERO_SAMPLE_COUNT = 50
_FIXTURE_RAW_VALUES = (
    ((0.0, 0.0, 0.0),)
    + ((0.0, 1.0, 0.0),) * R7_G_FIXTURE_NONZERO_SAMPLE_COUNT
    + ((0.0, 0.0, 0.0),)
)

_MAPPING_SHAPE_CONFIG = {
    "centers": (0.0, 0.0, 0.0),
    "half_ranges": (1.0, 1.0, 1.0),
    "channel_axis_weights": (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ),
    "signs": (1, 1, 1),
    "scales": (1.0, 1.0, 1.0),
}


def _versioned_contract(value: str) -> VersionedIdentity:
    name, separator, version = value.rpartition("/v")
    if not separator or not name or not version.isdigit():
        raise ValueError(f"invalid versioned contract identity: {value!r}")
    return VersionedIdentity(name, int(version))


def _fixture_samples(cadence_s: float) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "timestamp_s": index * cadence_s,
            "raw_values": raw_values,
            "active": True,
            "stale_reason": None,
        }
        for index, raw_values in enumerate(_FIXTURE_RAW_VALUES)
    )


def _plugin_parameters(
    control_frame: str,
    *,
    gain: float,
    deadzone: float,
    cadence_s: float,
    maximum_per_step_delta_m: float,
) -> tuple[PluginParameters, ...]:
    mapping_config = {
        **_MAPPING_SHAPE_CONFIG,
        "deadzone": deadzone,
        "speed_m_s": gain,
        "max_delta_m": maximum_per_step_delta_m,
        "source_kind": R7_G_INPUT_SOURCE_SELECTION.plugin_id,
    }
    return (
        PluginParameters(
            PluginParameterOwner(
                PluginAxis.INPUT_SOURCE,
                R7_G_INPUT_SOURCE_SELECTION,
            ),
            {"samples": _fixture_samples(cadence_s)},
        ),
        PluginParameters(
            PluginParameterOwner(
                PluginAxis.CONTROL_MAPPING,
                R7_G_MAPPING_SELECTION,
            ),
            {
                "mapping_config": mapping_config,
                "control_frame": control_frame,
            },
        ),
    )


def build_r7_g_free_space_manifest_pair(
    *,
    software_revision_identity: str,
) -> EvaluationConditionPair:
    """production selectionだけからsingle-target world/tool pairを構築する。"""

    bundle = PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES.robot_bundles.resolve(
        R7_G_ROBOT_SELECTION
    )
    initial_state_provider = bundle.provider(RESET_INITIAL_STATE_V1)
    if not isinstance(initial_state_provider, InitialStateContractProvider):
        raise TypeError("selected Robot Bundle lacks an initial-state contract provider")
    initial_state = initial_state_provider.initial_state_contract()
    profile = bundle.profile
    target_offset_m = 0.1
    gain = 0.1
    deadzone = 0.05
    cadence_s = 0.02
    maximum_per_step_delta_m = 0.01
    target = (
        initial_state.tip_position_m[0],
        initial_state.tip_position_m[1] + target_offset_m,
        initial_state.tip_position_m[2],
    )
    base = EvaluationManifest(
        schema_version=EVALUATION_MANIFEST_SCHEMA_VERSION,
        contract_version=EVALUATION_MANIFEST_CONTRACT_VERSION,
        repository_identity="Xpotato1024/Xpotato-Sim",
        software_revision_identity=software_revision_identity,
        robot_bundle=R7_G_ROBOT_SELECTION,
        robot_profile_identity=VersionedIdentity(
            profile.profile_id, profile.profile_contract_version
        ),
        runtime_plugin_identity=VersionedIdentity(
            bundle.runtime_plugin.profile_id, profile.profile_contract_version
        ),
        model_contract_identity=_versioned_contract(profile.model_contract_version),
        initial_state_contract_identity=initial_state.identity,
        environment=R7_G_ENVIRONMENT_SELECTION,
        control_mapping=R7_G_MAPPING_SELECTION,
        task=R7_G_TASK_SELECTION,
        input_source=R7_G_INPUT_SOURCE_SELECTION,
        command_semantics_route_identity=(
            LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
        ),
        evaluators=R7_G_EVALUATOR_SELECTIONS,
        parameters=_plugin_parameters(
            "world",
            gain=gain,
            deadzone=deadzone,
            cadence_s=cadence_s,
            maximum_per_step_delta_m=maximum_per_step_delta_m,
        ),
        initial_keyframe_name=profile.initial_keyframe_name,
        initial_qpos_rad=initial_state.qpos_rad,
        initial_tip_position_m=initial_state.tip_position_m,
        initial_tip_frame=initial_state.frame,
        initial_tip_unit=initial_state.position_unit,
        initial_tool_orientation_wxyz=initial_state.tool_orientation_wxyz,
        initial_tool_orientation_frame=initial_state.frame,
        initial_tool_orientation_unit=initial_state.orientation_unit,
        initial_tool_orientation_order=initial_state.quaternion_order,
        target_family="free-space-point-reach",
        target_identity="r7-g-free-space-y-positive-100mm-v1",
        target_world_position_m=target,
        initial_tip_to_target_distance_m=target_offset_m,
        target_tolerance_m=0.01,
        dwell_interval_s=0.2,
        timeout_s=5.0,
        input_source_identity=R7_G_INPUT_SOURCE_SELECTION.plugin_id,
        fixture_identity="r7-g-free-space-analog-y-execution-smoke-v1",
        normalized_input_range=(-1.0, 1.0),
        gain=gain,
        deadzone=deadzone,
        cadence_s=cadence_s,
        maximum_per_step_delta_m=maximum_per_step_delta_m,
        requested_control_frame="world",
        condition_id="world",
        condition_order=0,
        task_order=0,
        deterministic_seed=495,
        camera_identity="r7-g-fixed-camera-v1",
        visual_feedback_identity="r7-g-endpoint-feedback-v1",
        presentation_identity="r7-g-free-space-presentation-v1",
    )
    tool = replace(
        base,
        requested_control_frame="tool",
        condition_id="tool",
        condition_order=1,
        parameters=_plugin_parameters(
            "tool",
            gain=base.gain,
            deadzone=base.deadzone,
            cadence_s=base.cadence_s,
            maximum_per_step_delta_m=base.maximum_per_step_delta_m,
        ),
    )
    return EvaluationConditionPair(world=base, tool=tool)


__all__ = [
    "R7_G_ENVIRONMENT_SELECTION",
    "R7_G_EVALUATOR_SELECTIONS",
    "R7_G_FIXTURE_NONZERO_SAMPLE_COUNT",
    "R7_G_INPUT_SOURCE_SELECTION",
    "R7_G_MAPPING_SELECTION",
    "R7_G_ROBOT_SELECTION",
    "R7_G_TASK_SELECTION",
    "build_r7_g_free_space_manifest_pair",
]
