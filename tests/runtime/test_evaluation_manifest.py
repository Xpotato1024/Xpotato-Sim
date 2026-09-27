from __future__ import annotations

import json
from dataclasses import replace

import pytest

from xpotato_sim.plugins.robots.catalog import (
    resolve_robot_bundle as resolve_robot_bundle_from_catalog,
)
from xpotato_sim.runtime.evaluation.manifest import (
    EVALUATION_MANIFEST_SCHEMA_VERSION,
    EvaluationConditionPair,
    EvaluationManifest,
    EvaluationManifestDecodeError,
    EvaluationManifestError,
    EvaluationReadinessError,
    ReadinessStatus,
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
    build_evaluation_readiness,
    decode_evaluation_manifest,
    encode_evaluation_manifest,
    evaluation_manifest_digest,
    verify_freeze_identity,
)
from xpotato_sim.runtime.experiment.composition import (
    ExperimentPluginRegistries,
    PluginParameters,
)
from xpotato_sim.runtime.experiment.contracts import (
    ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
    EnvironmentRole,
    ControlMappingPlugin,
    LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
    ParameterContract,
    ParameterField,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    VersionedIdentity,
)
from xpotato_sim.plugins.mappings._command_routes import (
    joint_position_command_route,
)
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry
from tests.support.input_source_plugin_doubles import CONFORMANCE_SAMPLE_SCHEMA
from xpotato_sim.runtime.composition.robot_bundle import CONTACT_EVIDENCE_V1, InitialStateContract
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.initial_state import (
    FAST_ARM_INITIAL_STATE_CONTRACT,
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
)
from tests.runtime.test_experiment_plugin_composition import (
    TARGET_REQUIREMENT,
    TARGET_ROLE,
    _environment,
    _evaluator,
    _dummy_bundle,
    _mapping,
    _registries,
    _task,
)


BASELINE_FAST_ARM_MANIFEST_DIGEST = (
    "sha256:1c0f145f98fe7a05d8af2308f677794690b0ed2d7b690b8d4024336cd58f436a"
)
BASELINE_FAST_ARM_RESOLVED_IDENTITY_DIGEST = (
    "sha256:f525e8d6ddd89b6a10647014defcdf86ea967716d1eed07d3f197986ba34b07e"
)
BASELINE_FAST_ARM_FREEZE_DIGEST = (
    "sha256:818e18bbcf617a44c2d9bbe60ab92c70194c79f5dbbbc2826bf8b37e6a08fec1"
)


def _environment_parameters(selection: PluginSelection) -> PluginParameters:
    return PluginParameters(
        PluginParameterOwner(PluginAxis.ENVIRONMENT, selection),
        {"target_x": 0.2},
    )


EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity="Xpotato1024/Xpotato-Sim",
    software_revision_identity="test-revision:abc123",
)


def _manifest(**overrides: object) -> EvaluationManifest:
    environment = PluginSelection("dummy_environment", 1)
    values: dict[str, object] = {
        "schema_version": EVALUATION_MANIFEST_SCHEMA_VERSION,
        "contract_version": 3,
        "repository_identity": "Xpotato1024/Xpotato-Sim",
        "software_revision_identity": EXECUTION_IDENTITY.software_revision_identity,
        "robot_bundle": PluginSelection("dummy_robot_bundle", 1),
        "robot_profile_identity": VersionedIdentity("dummy_robot", 1),
        "runtime_plugin_identity": VersionedIdentity("dummy_robot", 1),
        "model_contract_identity": VersionedIdentity("dummy-model", 1),
        "initial_state_contract_identity": VersionedIdentity(
            "dummy_initial_state", 1
        ),
        "environment": environment,
        "control_mapping": PluginSelection("dummy_mapping", 1),
        "task": PluginSelection("dummy_reach_task", 1),
        "input_source": PluginSelection("conformance_input_source", 1),
        "command_semantics_route_identity": (
            LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
        ),
        "evaluators": (PluginSelection("dummy_success_evaluator", 1),),
        "parameters": (_environment_parameters(environment),),
        "initial_keyframe_name": "neutral",
        "initial_qpos_rad": (0.0,),
        "initial_tip_position_m": (0.0, 0.0, 0.0),
        "initial_tip_frame": "dummy world",
        "initial_tip_unit": "meter",
        "initial_tool_orientation_wxyz": (1.0, 0.0, 0.0, 0.0),
        "initial_tool_orientation_frame": "dummy world",
        "initial_tool_orientation_unit": "unit_quaternion",
        "initial_tool_orientation_order": "wxyz",
        "target_family": "point",
        "target_identity": "target-1",
        "target_world_position_m": (0.1, 0.0, 0.0),
        "initial_tip_to_target_distance_m": 0.1,
        "target_tolerance_m": 0.001,
        "dwell_interval_s": 0.1,
        "timeout_s": 1.0,
        "input_source_identity": "analog",
        "fixture_identity": "analog-fixture/v1",
        "normalized_input_range": (-1.0, 1.0),
        "gain": 0.1,
        "deadzone": 0.0,
        "cadence_s": 0.01,
        "maximum_per_step_delta_m": 0.01,
        "requested_control_frame": "world",
        "condition_id": "world",
        "condition_order": 0,
        "task_order": 0,
        "deterministic_seed": 7,
        "camera_identity": "camera/v1",
        "visual_feedback_identity": "feedback/v1",
        "presentation_identity": "presentation/v1",
    }
    values.update(overrides)
    return EvaluationManifest(**values)


def _readiness_registries(
    *,
    mapping=None,
    environment=None,
    task=None,
    evaluator=None,
    bundle=None,
) -> ExperimentPluginRegistries:
    return _registries(
        mapping=mapping or _mapping(),
        environment=environment,
        task=task,
        evaluator=evaluator,
        bundle=bundle,
    )


def _build_readiness(
    manifest: EvaluationManifest,
    registries: ExperimentPluginRegistries,
    *,
    execution_identity: SoftwareExecutionIdentity = EXECUTION_IDENTITY,
):
    return build_evaluation_readiness(
        manifest,
        registries,
        execution_identity=execution_identity,
    )


def _build_pair_readiness(
    pair: EvaluationConditionPair,
    registries: ExperimentPluginRegistries,
    *,
    execution_identity: SoftwareExecutionIdentity = EXECUTION_IDENTITY,
):
    return build_evaluation_condition_pair_readiness(
        pair,
        registries,
        execution_identity=execution_identity,
    )


def _reverse_document(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _reverse_document(item)
            for key, item in reversed(tuple(value.items()))
        }
    if isinstance(value, list):
        return [_reverse_document(item) for item in value]
    return value


def test_canonical_round_trip_and_field_insertion_order_are_stable() -> None:
    manifest = _manifest()
    canonical = encode_evaluation_manifest(manifest)
    reordered = json.dumps(
        _reverse_document(manifest.to_document()),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert decode_evaluation_manifest(canonical) == manifest
    assert encode_evaluation_manifest(decode_evaluation_manifest(reordered)) == canonical


def test_command_semantics_are_preserved_in_manifest_readiness_and_freeze() -> None:
    manifest = _manifest()
    readiness = _build_readiness(
        manifest,
        _readiness_registries(mapping=replace(_mapping(), control_frame="world")),
    )

    assert manifest.to_document()["command_semantics_route_identity"] == {
        "name": "local_endpoint_velocity_to_joint_position",
        "version": 1,
    }
    assert readiness.command_semantics_route.identity == (
        LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
    )
    resolved = readiness.freeze_record.canonical_resolved_identity_bytes
    assert b"local_endpoint_velocity_to_joint_position" in resolved
    assert b"dummy_mapping_semantics" in resolved
    assert b"joint_position_command" in resolved


def test_command_route_difference_changes_resolved_and_freeze_identity() -> None:
    baseline_mapping = replace(_mapping(), control_frame="world")
    control_semantics = baseline_mapping.mapping_semantics_identity
    assert control_semantics is not None
    alternate_route = joint_position_command_route(
        route_identity=ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
        control_semantics_identity=control_semantics,
    )
    alternate_mapping = replace(
        baseline_mapping,
        command_semantics_routes=frozenset({alternate_route}),
    )
    baseline = _build_readiness(
        _manifest(),
        _readiness_registries(mapping=baseline_mapping),
    )
    alternate = _build_readiness(
        _manifest(
            command_semantics_route_identity=ENDPOINT_DELTA_TO_JOINT_POSITION_V1
        ),
        _readiness_registries(mapping=alternate_mapping),
    )

    assert baseline.manifest_digest != alternate.manifest_digest
    assert baseline.resolved_identity_digest != alternate.resolved_identity_digest
    assert baseline.freeze_identity != alternate.freeze_identity
    assert (
        b"endpoint_delta_to_joint_position"
        in alternate.freeze_record.canonical_resolved_identity_bytes
    )


def test_one_semantic_field_changes_the_manifest_digest() -> None:
    from xpotato_sim.runtime.evaluation.manifest import evaluation_manifest_digest

    assert evaluation_manifest_digest(_manifest()) != evaluation_manifest_digest(
        _manifest(gain=0.2)
    )


def test_unknown_missing_and_duplicate_fields_are_rejected() -> None:
    document = _manifest().to_document()
    document["unknown_field"] = True
    with pytest.raises(EvaluationManifestDecodeError, match="unknown fields"):
        decode_evaluation_manifest(document)

    document = _manifest().to_document()
    del document["gain"]
    with pytest.raises(EvaluationManifestDecodeError, match="missing fields"):
        decode_evaluation_manifest(document)

    canonical = encode_evaluation_manifest(_manifest()).decode("utf-8")
    duplicate = canonical.replace('"cadence_s":0.01,', '"cadence_s":0.01,"cadence_s":0.01,', 1)
    with pytest.raises(EvaluationManifestDecodeError, match="duplicate field"):
        decode_evaluation_manifest(duplicate)

    document = _manifest().to_document()
    document["parameters"][0]["owner"]["axis"] = "invalid-axis"  # type: ignore[index]
    with pytest.raises(EvaluationManifestDecodeError, match="valid plugin axis"):
        decode_evaluation_manifest(document)


@pytest.mark.parametrize(
    "change",
    (
        {"gain": True},
        {"cadence_s": float("nan")},
        {"maximum_per_step_delta_m": 0.0},
        {"deadzone": 1.1},
        {"normalized_input_range": (0.5, 0.5)},
    ),
)
def test_strict_scalar_and_range_validation(change: dict[str, object]) -> None:
    with pytest.raises(EvaluationManifestError):
        _manifest(**change)

    document = _manifest().to_document()
    document.update(change)
    if isinstance(change.get("cadence_s"), float) and change["cadence_s"] != change["cadence_s"]:
        with pytest.raises(EvaluationManifestDecodeError, match="non-finite"):
            decode_evaluation_manifest(json.dumps(document, allow_nan=True))
    else:
        with pytest.raises(EvaluationManifestError):
            decode_evaluation_manifest(document)


@pytest.mark.parametrize(
    "change",
    (
        {"target_tolerance_m": 0.1},
        {"dwell_interval_s": 1.1},
        {"cadence_s": 1.1},
    ),
)
def test_cross_field_timing_and_target_relations_fail_closed(
    change: dict[str, object],
) -> None:
    with pytest.raises(EvaluationManifestError):
        _manifest(**change)


def test_recursive_parameter_values_reject_non_canonical_python_values() -> None:
    environment = PluginSelection("dummy_environment", 1)
    owner = PluginParameterOwner(PluginAxis.ENVIRONMENT, environment)
    with pytest.raises(TypeError, match="object keys must be strings"):
        PluginParameters(owner, {"nested": {1: "invalid"}})
    with pytest.raises(ValueError, match="finite"):
        PluginParameters(owner, {"nested": [float("inf")]})
    with pytest.raises(TypeError, match="canonical JSON"):
        PluginParameters(owner, {"nested": {"bad"}})
    with pytest.raises(TypeError, match="canonical JSON"):
        PluginParameters(owner, {"nested": object()})


def test_malformed_version_identity_and_pose_dimensions_fail_closed() -> None:
    with pytest.raises(EvaluationManifestError, match="schema version"):
        _manifest(schema_version="evaluation-manifest/v1")
    with pytest.raises((EvaluationManifestError, ValueError), match="empty"):
        _manifest(robot_profile_identity=VersionedIdentity("", 1))
    with pytest.raises(EvaluationManifestError, match="exactly 3"):
        _manifest(initial_tip_position_m=(0.0, 0.0))
    with pytest.raises(EvaluationManifestError, match="unit quaternion"):
        _manifest(initial_tool_orientation_wxyz=(2.0, 0.0, 0.0, 0.0))
    with pytest.raises(EvaluationManifestError, match="distance identity mismatch"):
        _manifest(initial_tip_to_target_distance_m=0.2)


def test_duplicate_evaluator_and_parameter_owner_are_rejected() -> None:
    evaluator = PluginSelection("dummy_success_evaluator", 1)
    with pytest.raises(EvaluationManifestError, match="duplicate evaluator"):
        _manifest(evaluators=(evaluator, evaluator))

    environment = PluginSelection("dummy_environment", 1)
    parameters = (
        _environment_parameters(environment),
        _environment_parameters(environment),
    )
    with pytest.raises(EvaluationManifestError, match="duplicate plugin parameter owner"):
        _manifest(parameters=parameters)


def test_manifest_and_parameter_inputs_are_detached_from_external_mutation() -> None:
    parameter_values = {"target_x": 0.2}
    environment = PluginSelection("dummy_environment", 1)
    parameter = PluginParameters(
        PluginParameterOwner(PluginAxis.ENVIRONMENT, environment), parameter_values
    )
    manifest = _manifest(parameters=(parameter,))
    readiness = _build_readiness(
        manifest,
        _readiness_registries(mapping=replace(_mapping(), control_frame="world")),
    )
    before_document = manifest.to_document()
    before_bytes = encode_evaluation_manifest(manifest)
    before_digest = evaluation_manifest_digest(manifest)
    before_freeze = readiness.freeze_identity

    parameter_values["target_x"] = 9.0
    assert manifest.parameters[0].values["target_x"] == 0.2
    assert manifest.to_document() == before_document
    assert encode_evaluation_manifest(manifest) == before_bytes
    assert evaluation_manifest_digest(manifest) == before_digest
    assert readiness.freeze_identity == before_freeze
    verify_freeze_identity(readiness.freeze_record, manifest, readiness)
    with pytest.raises(TypeError):
        manifest.parameters[0].values["target_x"] = 9.0  # type: ignore[index]

    source_document = manifest.to_document()
    decoded = decode_evaluation_manifest(source_document)
    decoded_before_document = decoded.to_document()
    decoded_before_bytes = encode_evaluation_manifest(decoded)
    decoded_before_digest = evaluation_manifest_digest(decoded)
    source_document["target_world_position_m"][0] = 9.0  # type: ignore[index]
    source_document["parameters"][0]["values"]["target_x"] = 8.0  # type: ignore[index]

    assert decoded.parameters[0].values["target_x"] == 0.2
    assert decoded.to_document() == decoded_before_document
    assert encode_evaluation_manifest(decoded) == decoded_before_bytes
    assert evaluation_manifest_digest(decoded) == decoded_before_digest
    with pytest.raises(TypeError):
        decoded.parameters[0].values["target_x"] = 9.0  # type: ignore[index]

    nested_source = {"array": [1, {"label": "stable"}]}
    nested_manifest = _manifest(
        parameters=(
            PluginParameters(
                PluginParameterOwner(PluginAxis.ENVIRONMENT, environment),
                {"target_x": 0.2, "nested": nested_source},
            ),
        )
    )
    nested_before = nested_manifest.to_document()
    nested_bytes = encode_evaluation_manifest(nested_manifest)
    assert encode_evaluation_manifest(decode_evaluation_manifest(nested_bytes)) == nested_bytes
    nested_source["array"][1]["label"] = "changed"  # type: ignore[index]
    assert nested_manifest.to_document() == nested_before
    with pytest.raises(TypeError):
        nested_manifest.parameters[0].values["nested"]["array"] = ()  # type: ignore[index]


def test_generic_non_fast_arm_readiness_is_software_only_and_ready() -> None:
    manifest = _manifest()
    readiness = _build_readiness(
        manifest,
        _readiness_registries(
            mapping=replace(_mapping(), control_frame="world")
        ),
    )

    assert readiness.readiness_status is ReadinessStatus.READY
    assert readiness.composition.robot_bundle.identity == VersionedIdentity(
        "dummy_robot_bundle", 1
    )
    assert readiness.resolved_capability_identities
    assert readiness.resolved_semantic_role_descriptors
    assert readiness.evidence_producers
    assert readiness.frozen_digest.startswith("sha256:")
    assert readiness.software_execution_identity == EXECUTION_IDENTITY


@pytest.mark.parametrize(
    "execution_identity",
    (
        SoftwareExecutionIdentity(
            "OtherOwner/Xpotato-Sim", "test-revision:abc123"
        ),
        SoftwareExecutionIdentity(
            "Xpotato1024/Xpotato-Sim", "test-revision:other"
        ),
    ),
)
def test_readiness_rejects_actual_software_identity_mismatch(
    execution_identity: SoftwareExecutionIdentity,
) -> None:
    with pytest.raises(EvaluationReadinessError, match="software identity"):
        _build_readiness(
            _manifest(),
            _readiness_registries(mapping=replace(_mapping(), control_frame="world")),
            execution_identity=execution_identity,
        )


def test_software_revision_identity_requires_an_explicit_stable_scheme() -> None:
    with pytest.raises(EvaluationManifestError, match="explicit stable scheme"):
        _manifest(software_revision_identity="abc123")
    with pytest.raises(EvaluationManifestError, match="explicit stable scheme"):
        SoftwareExecutionIdentity(
            "Xpotato1024/Xpotato-Sim", "git-sha1:ABCDEF"
        )


def test_actual_revision_change_changes_resolved_and_freeze_identity() -> None:
    registries = _readiness_registries(mapping=replace(_mapping(), control_frame="world"))
    first = _build_readiness(_manifest(), registries)
    changed_manifest = _manifest(software_revision_identity="test-revision:other")
    changed = _build_readiness(
        changed_manifest,
        registries,
        execution_identity=SoftwareExecutionIdentity(
            "Xpotato1024/Xpotato-Sim", "test-revision:other"
        ),
    )
    assert changed.resolved_identity != first.resolved_identity
    assert changed.freeze_identity != first.freeze_identity


def _condition_pair() -> tuple[EvaluationConditionPair, ExperimentPluginRegistries]:
    condition_parameter_contract = ParameterContract(
        (ParameterField("frame_scale", float, condition_specific=True),)
    )
    world_mapping = replace(
        _mapping(identity=VersionedIdentity("world_mapping", 1)),
        control_frame="world",
        parameter_contract=condition_parameter_contract,
    )
    tool_mapping = replace(
        _mapping(identity=VersionedIdentity("tool_mapping", 1)),
        control_frame="tool",
        parameter_contract=condition_parameter_contract,
    )
    base = _readiness_registries(mapping=world_mapping)
    registries = ExperimentPluginRegistries(
        robot_bundles=base.robot_bundles,
        environments=base.environments,
        control_mappings=VersionedPluginRegistry(
            (world_mapping, tool_mapping), kind="mapping plugin"
        ),
        tasks=base.tasks,
        evaluators=base.evaluators,
        input_sources=base.input_sources,
    )
    world_selection = PluginSelection("world_mapping", 1)
    tool_selection = PluginSelection("tool_mapping", 1)
    environment = PluginSelection("dummy_environment", 1)
    world = _manifest(
        control_mapping=world_selection,
        parameters=(
            _environment_parameters(environment),
            PluginParameters(
                PluginParameterOwner(PluginAxis.CONTROL_MAPPING, world_selection),
                {"frame_scale": 1.0},
            ),
        ),
    )
    tool = replace(
        world,
        control_mapping=tool_selection,
        requested_control_frame="tool",
        condition_id="tool",
        condition_order=1,
        parameters=(
            _environment_parameters(environment),
            PluginParameters(
                PluginParameterOwner(PluginAxis.CONTROL_MAPPING, tool_selection),
                {"frame_scale": 2.0},
            ),
        ),
    )
    return EvaluationConditionPair(world, tool), registries


def test_valid_world_tool_pair_freezes_both_conditions() -> None:
    pair, registries = _condition_pair()
    readiness = _build_pair_readiness(pair, registries)

    assert readiness.world.readiness_status is ReadinessStatus.READY
    assert readiness.tool.readiness_status is ReadinessStatus.READY
    assert readiness.world.freeze_identity != readiness.tool.freeze_identity
    assert readiness.pair_identity.startswith("sha256:")
    assert b"dummy_mapping_family" in readiness.world.freeze_record.canonical_resolved_identity_bytes


@pytest.mark.parametrize(
    "family_identity",
    (
        VersionedIdentity("other_mapping_family", 1),
        VersionedIdentity("dummy_mapping_family", 2),
    ),
)
def test_world_tool_pair_rejects_unrelated_or_version_mismatched_mapping_family(
    family_identity: VersionedIdentity,
) -> None:
    pair, registries = _condition_pair()
    world_mapping = replace(
        _mapping(identity=VersionedIdentity("world_mapping", 1)),
        control_frame="world",
        comparison_family_identity=family_identity,
        parameter_contract=registries.control_mappings.resolve(
            pair.world.control_mapping
        ).parameter_contract,
    )
    tool_mapping = replace(
        _mapping(identity=VersionedIdentity("tool_mapping", 1)),
        control_frame="tool",
        comparison_family_identity=VersionedIdentity("dummy_mapping_family", 1),
        parameter_contract=world_mapping.parameter_contract,
    )
    mismatched_registries = replace(
        registries,
        control_mappings=VersionedPluginRegistry(
            (world_mapping, tool_mapping), kind="mapping plugin"
        ),
    )
    with pytest.raises(EvaluationReadinessError, match="same comparison family"):
        _build_pair_readiness(pair, mismatched_registries)


def test_world_tool_pair_rejects_swapped_mapping_frames() -> None:
    pair, registries = _condition_pair()
    world_mapping = replace(
        _mapping(identity=VersionedIdentity("world_mapping", 1)),
        control_frame="tool",
        parameter_contract=registries.control_mappings.resolve(
            pair.world.control_mapping
        ).parameter_contract,
    )
    tool_mapping = replace(
        _mapping(identity=VersionedIdentity("tool_mapping", 1)),
        control_frame="world",
        parameter_contract=world_mapping.parameter_contract,
    )
    swapped_registries = replace(
        registries,
        control_mappings=VersionedPluginRegistry(
            (world_mapping, tool_mapping), kind="mapping plugin"
        ),
    )
    with pytest.raises(EvaluationReadinessError, match="frame mismatch"):
        _build_pair_readiness(pair, swapped_registries)


def test_mapping_strategy_semantic_identity_cannot_reuse_a_family_incorrectly() -> None:
    class _OtherStrategy:
        mapping_semantics_identity = VersionedIdentity("other_mapping_semantics", 1)

        def map_input(self, input_intent, parameters):
            return (input_intent, parameters)

    with pytest.raises(ValueError, match="semantic identity"):
        ControlMappingPlugin(
            identity=VersionedIdentity("other_mapping", 1),
            strategy=_OtherStrategy(),
            accepted_input_sample_schemas=frozenset({CONFORMANCE_SAMPLE_SCHEMA}),
            comparison_family_identity=VersionedIdentity("dummy_mapping_family", 1),
            mapping_semantics_identity=VersionedIdentity("dummy_mapping_semantics", 1),
        )


@pytest.mark.parametrize(
    "change",
    (
        {"software_revision_identity": "test-revision:other"},
        {"gain": 0.2},
        {"target_world_position_m": (0.2, 0.0, 0.0), "initial_tip_to_target_distance_m": 0.2},
        {"initial_qpos_rad": (0.1,)},
        {"camera_identity": "other-camera/v1"},
    ),
)
def test_world_tool_pair_rejects_non_allowed_condition_differences(
    change: dict[str, object],
) -> None:
    pair, _ = _condition_pair()
    with pytest.raises(EvaluationManifestError, match="shared invariant mismatch"):
        EvaluationConditionPair(pair.world, replace(pair.tool, **change))


def test_world_tool_pair_requires_explicit_condition_specific_mapping_parameters() -> None:
    pair, registries = _condition_pair()
    unmarked = ParameterContract((ParameterField("frame_scale", float),))
    world_mapping = replace(
        _mapping(identity=VersionedIdentity("world_mapping", 1)),
        control_frame="world",
        parameter_contract=unmarked,
    )
    tool_mapping = replace(
        _mapping(identity=VersionedIdentity("tool_mapping", 1)),
        control_frame="tool",
        parameter_contract=unmarked,
    )
    registries = replace(
        registries,
        control_mappings=VersionedPluginRegistry(
            (world_mapping, tool_mapping), kind="mapping plugin"
        ),
    )

    with pytest.raises(EvaluationReadinessError, match="condition-specific"):
        _build_pair_readiness(pair, registries)


def test_mapping_selection_and_requested_frame_mismatch_is_rejected() -> None:
    manifest = _manifest()
    wrong_mapping = replace(_mapping(), control_frame="tool")
    with pytest.raises(EvaluationReadinessError, match="mapping.*frame mismatch"):
        _build_readiness(
            manifest,
            _readiness_registries(mapping=wrong_mapping),
        )


@pytest.mark.parametrize(
    "manifest_change",
    (
        {"initial_qpos_rad": (0.5,)},
        {
            "initial_tip_position_m": (0.01, 0.0, 0.0),
            "target_world_position_m": (0.11, 0.0, 0.0),
            "initial_tip_to_target_distance_m": 0.1,
        },
        {"initial_tool_orientation_wxyz": (0.0, 1.0, 0.0, 0.0)},
        {"initial_keyframe_name": "other-neutral"},
        {
            "initial_state_contract_identity": VersionedIdentity(
                "dummy_initial_state", 2
            )
        },
    ),
)
def test_readiness_rejects_canonical_initial_state_mismatch(
    manifest_change: dict[str, object],
) -> None:
    with pytest.raises(EvaluationReadinessError, match="initial"):
        _build_readiness(
            _manifest(**manifest_change),
            _readiness_registries(mapping=replace(_mapping(), control_frame="world")),
        )


def test_readiness_rejects_provider_initial_state_contract_value_mismatch() -> None:
    provider_contract = InitialStateContract(
        identity=VersionedIdentity("dummy_initial_state", 1),
        source_kind="named_keyframe",
        source_id="neutral",
        qpos_rad=(0.5,),
        tip_position_m=(0.0, 0.0, 0.0),
        tool_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        frame="dummy world",
        position_unit="meter",
        orientation_unit="unit_quaternion",
        quaternion_order="wxyz",
    )
    with pytest.raises(EvaluationReadinessError, match="initial qpos"):
        _build_readiness(
            _manifest(),
            _readiness_registries(
                mapping=replace(_mapping(), control_frame="world"),
                bundle=_dummy_bundle(initial_state_contract=provider_contract),
            ),
        )


@pytest.mark.parametrize(
    "manifest_change",
    (
        {"control_mapping": PluginSelection("unknown_mapping", 1)},
        {"control_mapping": PluginSelection("dummy_mapping", 2)},
        {"robot_profile_identity": VersionedIdentity("other_robot", 1)},
        {"runtime_plugin_identity": VersionedIdentity("other_robot", 1)},
        {"model_contract_identity": VersionedIdentity("other-model", 1)},
        {"initial_keyframe_name": "not-neutral"},
        {"initial_qpos_rad": (0.0, 0.0)},
    ),
)
def test_readiness_rejects_identity_capability_and_neutral_state_mismatch(
    manifest_change: dict[str, object],
) -> None:
    manifest = _manifest(**manifest_change)
    with pytest.raises(EvaluationReadinessError):
        _build_readiness(
            manifest,
            _readiness_registries(
                mapping=replace(_mapping(), control_frame="world")
            ),
        )


def test_readiness_rejects_missing_capability_role_and_evidence() -> None:
    manifest = _manifest()
    mapping = replace(_mapping(), control_frame="world")
    missing_capability_environment = replace(
        _environment(),
        required_robot_capabilities=frozenset({CONTACT_EVIDENCE_V1}),
    )
    with pytest.raises(EvaluationReadinessError, match="unsupported Robot Bundle capability"):
        _build_readiness(
            manifest,
            _readiness_registries(
                mapping=mapping, environment=missing_capability_environment
            ),
        )

    with pytest.raises(EvaluationReadinessError, match="semantic role binding"):
        _build_readiness(
            manifest,
            _readiness_registries(mapping=mapping, environment=replace(_environment(), roles=())),
        )

    unknown_evidence_evaluator = _evaluator(
        required=VersionedIdentity("not-produced", 1)
    )
    with pytest.raises(EvaluationReadinessError, match="evidence requirement"):
        _build_readiness(
            manifest,
            _readiness_registries(mapping=mapping, evaluator=unknown_evidence_evaluator),
        )


def test_freeze_identity_detects_manifest_and_resolved_identity_changes() -> None:
    manifest = _manifest()
    readiness = _build_readiness(
        manifest,
        _readiness_registries(
            mapping=replace(_mapping(), control_frame="world")
        ),
    )
    with pytest.raises(EvaluationReadinessError, match="manifest value changed"):
        verify_freeze_identity(
            readiness.freeze_record,
            replace(manifest, software_revision_identity="test-revision:changed"),
            readiness,
        )

    tampered = replace(
        readiness,
        evidence_producers=(),
        resolved_semantic_role_descriptors=(),
    )
    with pytest.raises(EvaluationReadinessError, match="resolved readiness identity"):
        verify_freeze_identity(readiness.freeze_record, manifest, tampered)

    with pytest.raises(EvaluationReadinessError, match="resolved readiness identity"):
        verify_freeze_identity(
            readiness.freeze_record,
            manifest,
            replace(
                readiness,
                initial_state_verification_identity="sha256:" + "0" * 64,
            ),
        )

    with pytest.raises(EvaluationReadinessError, match="resolved readiness identity"):
        verify_freeze_identity(
            readiness.freeze_record,
            manifest,
            replace(
                readiness,
                software_execution_identity=SoftwareExecutionIdentity(
                    "Xpotato1024/Xpotato-Sim", "test-revision:changed"
                ),
            ),
        )

    with pytest.raises(EvaluationReadinessError, match="resolved readiness identity"):
        verify_freeze_identity(
            readiness.freeze_record,
            manifest,
            replace(
                readiness,
                composition=replace(
                    readiness.composition,
                    control_mapping=replace(
                        readiness.composition.control_mapping,
                        control_frame="tool",
                    ),
                ),
            ),
        )


def test_fast_arm_profile_plugin_and_model_identity_regression() -> None:
    fast_environment_identity = VersionedIdentity("fast_environment", 1)
    fast_task_identity = VersionedIdentity("fast_task", 1)
    fast_mapping_identity = VersionedIdentity("fast_world_mapping", 1)
    fast_evaluator_identity = VersionedIdentity("fast_evaluator", 1)
    fast_environment = replace(
        _environment(identity=fast_environment_identity),
        roles=(EnvironmentRole(TARGET_ROLE, "target", "world", "meter"),),
        required_robot_roles=frozenset(),
        compatible_robot_bundles=frozenset({VersionedIdentity("fast_arm", 1)}),
        compatible_backend_kinds=frozenset({"mujoco"}),
    )
    fast_task = replace(
        _task(identity=fast_task_identity),
        required_semantic_roles=frozenset({TARGET_REQUIREMENT}),
        compatible_robot_bundles=frozenset({VersionedIdentity("fast_arm", 1)}),
        compatible_environments=frozenset({fast_environment_identity}),
        compatible_backend_kinds=frozenset({"mujoco"}),
    )
    fast_mapping = replace(
        _mapping(identity=fast_mapping_identity), control_frame="world"
    )
    fast_evaluator = replace(
        _evaluator(identity=fast_evaluator_identity)
    )
    bundle = resolve_robot_bundle_from_catalog("fast_arm")
    registries = _readiness_registries(
        bundle=bundle,
        environment=fast_environment,
        mapping=fast_mapping,
        task=fast_task,
        evaluator=fast_evaluator,
    )
    manifest = _manifest(
        robot_bundle=PluginSelection("fast_arm", 1),
        robot_profile_identity=VersionedIdentity("fast_arm", 1),
        runtime_plugin_identity=VersionedIdentity("fast_arm", 1),
        model_contract_identity=VersionedIdentity("fast_arm-mujoco-model", 1),
        initial_state_contract_identity=FAST_ARM_INITIAL_STATE_CONTRACT.identity,
    )
    manifest = replace(
        manifest,
        environment=PluginSelection(fast_environment_identity.name, 1),
        control_mapping=PluginSelection(fast_mapping_identity.name, 1),
        task=PluginSelection(fast_task_identity.name, 1),
        evaluators=(PluginSelection(fast_evaluator_identity.name, 1),),
        parameters=(
            _environment_parameters(
                PluginSelection(fast_environment_identity.name, 1)
            ),
        ),
        initial_keyframe_name=FAST_ARM_ROBOT_PROFILE.initial_keyframe_name,
        initial_qpos_rad=FAST_ARM_INITIAL_STATE_QPOS_RAD,
        initial_tip_position_m=FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
        initial_tip_frame=FAST_ARM_ROBOT_PROFILE.coordinate_units.coordinate_frame,
        initial_tip_unit=FAST_ARM_ROBOT_PROFILE.coordinate_units.position_unit,
        initial_tool_orientation_wxyz=FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
        initial_tool_orientation_frame=FAST_ARM_ROBOT_PROFILE.coordinate_units.coordinate_frame,
        initial_tool_orientation_unit=FAST_ARM_INITIAL_STATE_CONTRACT.orientation_unit,
        target_world_position_m=(
            FAST_ARM_INITIAL_STATE_TIP_POSITION_M[0] + 0.1,
            FAST_ARM_INITIAL_STATE_TIP_POSITION_M[1],
            FAST_ARM_INITIAL_STATE_TIP_POSITION_M[2],
        ),
        initial_tip_to_target_distance_m=0.1,
    )

    readiness = _build_readiness(manifest, registries)
    # 旧名を持つ記録は、改名前と同じdigestへ解決されることも固定する。
    old_manifest = decode_evaluation_manifest(
        encode_evaluation_manifest(manifest).replace(b"Xpotato1024/Xpotato-Sim", b"Xpotato1024/Selfrionette-mujoco")
    )
    old_readiness = _build_readiness(old_manifest, registries,
        execution_identity=replace(EXECUTION_IDENTITY, repository_identity="Xpotato1024/Selfrionette-mujoco"))
    assert old_readiness.freeze_record.manifest_digest == 'sha256:2125da9cf092fcd33bb57df5cbd1fdf129b054fd2eba3b4f5d01872493a6caeb'
    assert old_readiness.resolved_identity == 'sha256:5c184f295fc6c8fe5371dfbfd0c85b65467360b784c4a9490a983ec9197fc8f3'
    assert old_readiness.freeze_identity == 'sha256:7b7d77a441539026aec1eee0642236fdb1b85f876262815c8f20d0ac3521ab06'
    # Golden values include the evaluation-manifest/v3 command semantics condition.
    assert readiness.freeze_record.manifest_digest == (
        BASELINE_FAST_ARM_MANIFEST_DIGEST
    )
    assert readiness.resolved_identity == (
        BASELINE_FAST_ARM_RESOLVED_IDENTITY_DIGEST
    )
    assert readiness.freeze_identity == BASELINE_FAST_ARM_FREEZE_DIGEST

    assert readiness.composition.robot_bundle is bundle
    assert readiness.robot_profile_identity == VersionedIdentity("fast_arm", 1)
    assert readiness.model_contract_identity == VersionedIdentity(
        "fast_arm-mujoco-model", 1
    )
    assert readiness.freeze_record.canonical_manifest_bytes == (
        encode_evaluation_manifest(manifest)
    )
    assert (
        b"xpotato_sim.plugins"
        not in readiness.freeze_record.canonical_resolved_identity_bytes
    )
    assert (
        b"fast_arm_bundle.py"
        not in readiness.freeze_record.canonical_resolved_identity_bytes
    )
