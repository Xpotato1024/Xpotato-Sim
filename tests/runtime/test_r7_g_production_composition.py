from __future__ import annotations

from dataclasses import replace
from math import isfinite

import pytest

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
    resolve_production_experiment,
)
from xpotato_sim.runtime.evaluation.manifest import (
    EvaluationConditionPair,
    EvaluationReadinessError,
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
    build_evaluation_readiness,
    decode_evaluation_manifest,
    encode_evaluation_manifest,
    evaluation_manifest_digest,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    R7_G_ENVIRONMENT_SELECTION,
    R7_G_EVALUATOR_SELECTIONS,
    R7_G_FIXTURE_NONZERO_SAMPLE_COUNT,
    R7_G_INPUT_SOURCE_SELECTION,
    R7_G_MAPPING_SELECTION,
    R7_G_ROBOT_SELECTION,
    R7_G_TASK_SELECTION,
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.contracts import PluginAxis, PluginSelection
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachTaskContext,
)


EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity="Xpotato1024/Xpotato-Sim",
    software_revision_identity="test-revision:r7-g-production-fixture",
)
WORLD_MANIFEST_DIGEST = (
    "sha256:939d4bf909dec5cccf5ce48371931e7e6eecca0e8f4b5e3a9fcfbdbe7e29253e"
)
TOOL_MANIFEST_DIGEST = (
    "sha256:c877521fef2c46e89542055fb9564eb6d5d2017b7b5505f625ae49974c613ba0"
)
WORLD_RESOLVED_DIGEST = (
    "sha256:6372df327fd03cc55cc5107cf4d82f3c47913b232de990548c2fabbe583e38ce"
)
TOOL_RESOLVED_DIGEST = (
    "sha256:9f60f249450e47b9ed8ff6be860f1dbeb9f6ee77816e11aac7db7706cac370fb"
)
WORLD_FREEZE_DIGEST = (
    "sha256:594326c987d0e8f36a57f12e75bbea2a6ca082e8ca4b36807e1b09a97a206dda"
)
TOOL_FREEZE_DIGEST = (
    "sha256:8bfc897be091a7054e6f503eb82471e6d679f5b5598731fd28dcc56943141d5c"
)
PAIR_DIGEST = (
    "sha256:96baf08830c0f1d6f361aa26b964cd3231d130d133fc54ca2cdd0b5cef768e39"
)


def _pair() -> EvaluationConditionPair:
    return build_r7_g_free_space_manifest_pair(
        software_revision_identity=EXECUTION_IDENTITY.software_revision_identity
    )


def _mapping_parameters(manifest) -> dict[str, object]:
    for item in manifest.parameters:
        if item.owner.axis is PluginAxis.CONTROL_MAPPING:
            return dict(item.values)
    raise AssertionError("mapping parameters are required")


def _input_source_parameters(manifest) -> dict[str, object]:
    for item in manifest.parameters:
        if item.owner.axis is PluginAxis.INPUT_SOURCE:
            return dict(item.values)
    raise AssertionError("input source parameters are required")


def test_production_catalogs_resolve_all_six_axes_and_ordered_metrics() -> None:
    pair = _pair()
    resolved = resolve_production_experiment(pair.world.plugin_manifest)

    assert pair.world.robot_bundle == R7_G_ROBOT_SELECTION
    assert pair.world.environment == R7_G_ENVIRONMENT_SELECTION
    assert pair.world.input_source == R7_G_INPUT_SOURCE_SELECTION
    assert pair.world.control_mapping == R7_G_MAPPING_SELECTION
    assert pair.world.task == R7_G_TASK_SELECTION
    assert pair.world.evaluators == R7_G_EVALUATOR_SELECTIONS
    assert resolved.robot_bundle.identity.canonical_id == "fast_arm/v1"
    assert resolved.environment.identity.canonical_id == "free_space_environment/v1"
    assert resolved.input_source.identity.canonical_id == "analog_fixture/v1"
    assert resolved.control_mapping.identity.canonical_id == "analog_fixture_mapping/v1"
    assert resolved.task.identity.canonical_id == "endpoint_reach_task/v1"
    assert tuple(item.identity.name for item in resolved.evaluators) == (
        "success_within_timeout",
        "off_axis_drift",
        "completion_time",
        "final_endpoint_error",
    )
    assert tuple(
        binding.evidence_identity.name for binding in resolved.evidence_producers
    ) == (
        "endpoint_reach_measured_trajectory",
        "endpoint_reach_terminal_classification",
    )


def test_canonical_world_tool_pair_freezes_from_production_catalogs_only() -> None:
    pair = _pair()
    readiness = build_evaluation_condition_pair_readiness(
        pair,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )

    # repository identity以外の条件が旧goldenと同一であることを検証する。
    def old_manifest(manifest):
        return decode_evaluation_manifest(encode_evaluation_manifest(manifest).replace(
            b"Xpotato1024/Xpotato-Sim", b"Xpotato1024/Selfrionette-mujoco"))
    old_pair = replace(pair, world=old_manifest(pair.world), tool=old_manifest(pair.tool))
    old_ready = build_evaluation_condition_pair_readiness(old_pair, PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=replace(EXECUTION_IDENTITY, repository_identity="Xpotato1024/Selfrionette-mujoco"))
    assert evaluation_manifest_digest(old_pair.world) == 'sha256:416b85e1b70e27f3485aa58211e1ad5db1a4948d6f7c58983edcc044ac290f4f'
    assert evaluation_manifest_digest(old_pair.tool) == 'sha256:35352ae7fa550e9ca472941d81a4d89b070b84eae3c34629fd70b8b3a485a054'
    assert old_ready.world.freeze_record.resolved_identity_digest == 'sha256:40287ccca4f92e793efbba1a4daf996f205dac9362659cbba6a1150c0d9924aa'
    assert old_ready.tool.freeze_record.resolved_identity_digest == 'sha256:a69359d2c6bf83bdf9dac10134356c60f15bcabaa9949205a8e9446ce9bb1120'
    assert old_ready.world.freeze_identity == 'sha256:28960b4155a455fb13613b75270ffbe920a2bc328da0ef8ec696fad8389bf700'
    assert old_ready.tool.freeze_identity == 'sha256:e29343c50d9012823fe4322153d38d24021bbce3339ec78ff89ac83ac08b63c1'
    assert old_ready.pair_identity == 'sha256:800d09b16aac7b3c8519010df7fdeaec975bc59111cefd1bb3098b148e3e0247'

    assert evaluation_manifest_digest(pair.world) == WORLD_MANIFEST_DIGEST
    assert evaluation_manifest_digest(pair.tool) == TOOL_MANIFEST_DIGEST
    assert readiness.world.freeze_record.resolved_identity_digest == WORLD_RESOLVED_DIGEST
    assert readiness.tool.freeze_record.resolved_identity_digest == TOOL_RESOLVED_DIGEST
    assert readiness.world.freeze_identity == WORLD_FREEZE_DIGEST
    assert readiness.tool.freeze_identity == TOOL_FREEZE_DIGEST
    assert readiness.pair_identity == PAIR_DIGEST
    assert readiness.world.composition.control_mapping is (
        readiness.tool.composition.control_mapping
    )
    assert readiness.world.command_semantics_route == (
        readiness.tool.command_semantics_route
    )
    assert readiness.world.composition.environment is (
        readiness.tool.composition.environment
    )
    assert readiness.world.composition.task is readiness.tool.composition.task
    assert readiness.world.composition.evaluators == readiness.tool.composition.evaluators


def test_manifest_projection_has_one_upper_owner_and_only_frame_differs() -> None:
    pair = _pair()
    world = _mapping_parameters(pair.world)
    tool = _mapping_parameters(pair.tool)

    assert world["control_frame"] == "world"
    assert tool["control_frame"] == "tool"
    assert world["mapping_config"] == tool["mapping_config"]
    mapping_config = world["mapping_config"]
    assert mapping_config["speed_m_s"] == pair.world.gain  # type: ignore[index]
    assert mapping_config["deadzone"] == pair.world.deadzone  # type: ignore[index]
    assert mapping_config["max_delta_m"] == (  # type: ignore[index]
        pair.world.maximum_per_step_delta_m
    )
    assert "control_frame" not in mapping_config
    input_parameters = _input_source_parameters(pair.world)
    samples = input_parameters["samples"]
    timestamps = tuple(sample["timestamp_s"] for sample in samples)  # type: ignore[union-attr]
    assert timestamps == tuple(
        index * pair.world.cadence_s for index in range(len(samples))  # type: ignore[arg-type]
    )
    assert _input_source_parameters(pair.world) == _input_source_parameters(pair.tool)
    assert pair.world.gain == pair.tool.gain
    assert pair.world.deadzone == pair.tool.deadzone
    assert pair.world.cadence_s == pair.tool.cadence_s
    assert pair.world.target_world_position_m == pair.tool.target_world_position_m
    assert pair.world.initial_qpos_rad == pair.tool.initial_qpos_rad
    assert pair.world.initial_tip_position_m == pair.tool.initial_tip_position_m
    assert encode_evaluation_manifest(pair.world) == encode_evaluation_manifest(
        decode_evaluation_manifest(encode_evaluation_manifest(pair.world))
    )


def test_fixture_has_bounded_execution_budget_and_terminal_zero_hold() -> None:
    pair = _pair()
    parameters = _input_source_parameters(pair.world)
    samples = parameters["samples"]
    raw_values = tuple(sample["raw_values"] for sample in samples)  # type: ignore[union-attr]
    nonzero_count = sum(value != (0.0, 0.0, 0.0) for value in raw_values)
    nominal_budget_m = nonzero_count * pair.world.cadence_s * pair.world.gain

    assert nonzero_count == R7_G_FIXTURE_NONZERO_SAMPLE_COUNT
    assert nominal_budget_m >= (
        pair.world.initial_tip_to_target_distance_m - pair.world.target_tolerance_m
    )
    assert raw_values[0] == (0.0, 0.0, 0.0)
    assert raw_values[-1] == (0.0, 0.0, 0.0)
    assert all(
        isfinite(component) and -1.0 <= component <= 1.0
        for value in raw_values
        for component in value
    )

    source_plugin = PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES.input_sources.resolve(
        R7_G_INPUT_SOURCE_SELECTION
    )
    source = source_plugin.factory(parameters)
    frames = tuple(source.read_frame() for _ in range(len(samples) + 2))  # type: ignore[arg-type]
    assert frames[-3].values == (0.0, 0.0, 0.0)
    assert frames[-2].values == (0.0, 0.0, 0.0)
    assert frames[-1].values == (0.0, 0.0, 0.0)


def test_readiness_binds_upper_manifest_to_immutable_task_context() -> None:
    pair = _pair()
    readiness = build_evaluation_readiness(
        pair.world,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )

    context = readiness.task_execution_binding.context
    assert isinstance(context, EndpointReachTaskContext)
    assert context.initial_position_world_m == pair.world.initial_tip_position_m
    assert context.target_position_world_m == pair.world.target_world_position_m
    assert context.target_tolerance_m == pair.world.target_tolerance_m
    assert context.dwell_interval_s == pair.world.dwell_interval_s
    assert context.timeout_s == pair.world.timeout_s


def test_readiness_failure_precedes_input_source_factory_and_simulation() -> None:
    pair = _pair()
    calls = 0
    source = PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES.input_sources.resolve(
        R7_G_INPUT_SOURCE_SELECTION
    )

    def exploding_factory(parameters):
        nonlocal calls
        calls += 1
        raise AssertionError("Input Source factory must not run during readiness")

    registries = replace(
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        input_sources=type(PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES.input_sources)(
            (replace(source, factory=exploding_factory),),
            kind="input source plugin",
        ),
    )
    invalid = replace(
        pair.world,
        environment=PluginSelection("missing_environment", 1),
    )
    with pytest.raises(EvaluationReadinessError, match="unknown environment plugin"):
        build_evaluation_readiness(
            invalid,
            registries,
            execution_identity=EXECUTION_IDENTITY,
        )
    assert calls == 0


def test_world_tool_pair_rejects_non_frame_mapping_parameter_drift() -> None:
    pair = _pair()
    tool_parameters = []
    for item in pair.tool.parameters:
        if item.owner.axis is PluginAxis.CONTROL_MAPPING:
            values = dict(item.values)
            config = dict(values["mapping_config"])
            config["speed_m_s"] = 0.2
            values["mapping_config"] = config
            item = replace(item, values=values)
        tool_parameters.append(item)
    changed_tool = replace(pair.tool, parameters=tuple(tool_parameters))

    with pytest.raises(EvaluationReadinessError, match="condition-specific"):
        build_evaluation_condition_pair_readiness(
            EvaluationConditionPair(pair.world, changed_tool),
            PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
            execution_identity=EXECUTION_IDENTITY,
        )
