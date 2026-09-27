from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xpotato_sim.runtime.composition.robot_profile import (
    CoordinateUnitContract,
    EndpointReference,
    RobotProfile,
)
from xpotato_sim.runtime.composition.robot_provider_adapters import NamedKeyframeInitialStateProvider
from xpotato_sim.plugins.mappings._command_routes import (
    joint_position_command_route,
)
from xpotato_sim.runtime.execution.command_routes import (
    JointPositionCommandExecutionBinding,
    JointPositionCommandRouteExecutionStrategy,
    NativeEndpointVelocityCommandRouteExecutionStrategy,
    ResolvedCommandExecution,
)
from xpotato_sim.runtime.experiment.composition import (
    EvidenceProducerBinding,
    ExperimentPluginManifest,
    ExperimentPluginRegistries,
    PluginParameters,
    compose_experiment,
    resolve_command_execution,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    CanonicalEvidenceSet,
    CommandSemanticsRoute,
    ControlMappingPlugin,
    ENDPOINT_VELOCITY_COMMAND_V1,
    EnvironmentPlugin,
    EnvironmentRole,
    EvaluationPlugin,
    EvidenceDisposition,
    EvidencePolicy,
    EvidenceStatus,
    JOINT_POSITION_COMMAND_V1,
    LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
    MetricResult,
    ParameterContract,
    ParameterField,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    ROLE_ATTRIBUTE_WILDCARD,
    SemanticRole,
    SemanticRoleRequirement,
    TaskPlugin,
    TaskTerminalClassification,
    TaskTransition,
    VersionedIdentity,
    NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1,
)
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry
from tests.support.input_source_plugin_doubles import (
    CONFORMANCE_INPUT_SOURCE,
    CONFORMANCE_SAMPLE_SCHEMA,
    build_conformance_input_source,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import FAST_ARM_RUNTIME_PLUGIN
from xpotato_sim.runtime.composition.robot_bundle import (
    CAPABILITY_PROVIDER_TYPES,
    CONTACT_EVIDENCE_V1,
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    InitialStateContract,
    ProviderAssemblyBinding,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    ROBOT_TOOL_ENDPOINT_ROLE,
    SCENE_ROLE_BINDING_V1,
    CapabilityProviderBinding,
    RobotCommandSemanticProviderBinding,
    RobotBundle,
)
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.schemas import (
    EndpointVelocityCommand,
    JointPositionCommand,
    MotionCommand,
)
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import (
    build_concrete_mujoco_pipeline,
)


TARGET_ROLE = SemanticRole("environment.target_object")
TASK_TERMINAL_EVIDENCE = VersionedIdentity("task.terminal_classification", 1)
UNKNOWN_EVIDENCE = VersionedIdentity("evidence.not_produced", 1)
EVALUATOR_IDENTITY = VersionedIdentity("dummy_success_evaluator", 1)
ROBOT_TOOL_REQUIREMENT = SemanticRoleRequirement(
    ROBOT_TOOL_ENDPOINT_ROLE,
    object_kind="robot_endpoint",
    frame="dummy world",
    unit="meter",
)
TARGET_REQUIREMENT = SemanticRoleRequirement(
    TARGET_ROLE,
    object_kind="target",
    frame="world",
    unit="meter",
)


@dataclass(frozen=True)
class _DummyRuntimePlugin:
    profile: RobotProfile

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    def validate_model(self, model: object) -> None:
        return None

    def build_inverse_kinematics(self):
        raise NotImplementedError

    def build_forward_kinematics(self):
        raise NotImplementedError

    def build_target_motion_generator(self, **kwargs):
        return ("target-motion", kwargs)

    def build_local_endpoint_motion_generator(self):
        return "local-motion"

    def build_qpos_feasibility_guard(self, **kwargs):
        return ("qpos-guard", kwargs)

    def endpoint_position_from_state(self, state):
        return (0.0, 0.0, 0.0)

    def endpoint_orientation_from_state(self, state):
        return (1.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class _EndpointPoseProvider:
    assembly_binding: ProviderAssemblyBinding
    capability_identity = ENDPOINT_POSE_V1

    def observe_endpoint_pose(self, state):
        return object()


@dataclass(frozen=True)
class _EndpointCommandProvider:
    assembly_binding: ProviderAssemblyBinding
    capability_identity = ENDPOINT_COMMAND_V1

    def build_target_motion_generator(self, **kwargs):
        return object()

    def build_local_endpoint_motion_generator(self):
        return object()


@dataclass(frozen=True)
class _CommandSemanticProvider:
    assembly_binding: ProviderAssemblyBinding
    command_semantics_identity: VersionedIdentity
    command_type: type

    def execute(self, command, *, backend):
        _ = command
        _ = backend


@dataclass(frozen=True)
class _QposFeasibilityProvider:
    assembly_binding: ProviderAssemblyBinding
    capability_identity = QPOS_FEASIBILITY_V1

    def build_guard(self, **kwargs):
        return object()


@dataclass(frozen=True)
class _SceneRoleProvider:
    assembly_binding: ProviderAssemblyBinding
    capability_identity = SCENE_ROLE_BINDING_V1

    def semantic_role_bindings(self):
        from xpotato_sim.runtime.composition.robot_bundle import SemanticRoleBinding

        return (
            SemanticRoleBinding(
                role=ROBOT_TOOL_ENDPOINT_ROLE,
                backend_kind="dummy",
                target_kind="site",
                target_id="endpoint",
                object_kind="robot_endpoint",
                frame="dummy world",
                unit="meter",
            ),
        )


class _SceneProvider:
    def compose_scene(self, parameters):
        return dict(parameters)

    def reset_scene(self, scene):
        return None


class _MappingStrategy:
    mapping_semantics_identity = VersionedIdentity("dummy_mapping_semantics", 1)

    def map_input(self, input_intent, parameters):
        return (input_intent, dict(parameters))


class _TaskLifecycle:
    def initial_state(self, parameters):
        return {"phase": "running", **parameters}

    def bind_context(self, context, parameters):
        return _TaskExecutionBinding(context, dict(parameters))


@dataclass(frozen=True)
class _TaskExecutionBinding:
    context: object
    parameters: dict[str, object]

    def initial_state(self):
        return {"phase": "running", **self.parameters}

    def advance(self, state, observation):
        evidence = CanonicalEvidenceSet(
            (
                CanonicalEvidence(
                    identity=TASK_TERMINAL_EVIDENCE,
                    status=EvidenceStatus.MEASURED,
                    value=TaskTerminalClassification.SUCCESS.value,
                    provenance="dummy-task",
                ),
            )
        )
        return TaskTransition(state, TaskTerminalClassification.SUCCESS, evidence)


@dataclass(frozen=True)
class _SuccessMetric:
    metric_id: VersionedIdentity = EVALUATOR_IDENTITY
    provenance_override: str | None = None

    def derive(self, evidence, parameters, *, provenance):
        terminal = evidence.require(TASK_TERMINAL_EVIDENCE)
        return MetricResult(
            metric_id=self.metric_id,
            value=terminal.value == TaskTerminalClassification.SUCCESS.value,
            status=EvidenceStatus.MEASURED,
            provenance=self.provenance_override or provenance,
        )


def _dummy_profile() -> RobotProfile:
    return RobotProfile(
        profile_id="dummy_robot",
        profile_contract_version=1,
        model_contract_version="dummy-model/v1",
        backend_kind="dummy",
        mujoco_model_asset=Path("dummy.xml"),
        canonical_joint_names=("joint",),
        qpos_dimension=1,
        qvel_dimension=1,
        initial_keyframe_name="neutral",
        endpoint=EndpointReference(site_name="endpoint", body_name=None),
        joint_limit_config_asset=None,
        coordinate_units=CoordinateUnitContract(
            position_unit="meter",
            angle_unit="rad",
            coordinate_frame="dummy world",
            quaternion_order="wxyz",
        ),
        viewer_profile_id="dummy_robot",
        supported_capabilities=frozenset(),
    )


def _dummy_bundle(
    *,
    identity: VersionedIdentity = VersionedIdentity("dummy_robot_bundle", 1),
    include_endpoint_pose: bool = True,
    duplicate_endpoint_pose: bool = False,
    parameter_contract: ParameterContract = ParameterContract(),
    initial_state_contract: InitialStateContract | None = None,
    command_semantics: frozenset[VersionedIdentity] = frozenset(
        {JOINT_POSITION_COMMAND_V1}
    ),
) -> RobotBundle:
    profile = _dummy_profile()
    plugin = _DummyRuntimePlugin(profile)
    contract = initial_state_contract or InitialStateContract(
        identity=VersionedIdentity("dummy_initial_state", 1),
        source_kind="named_keyframe",
        source_id=profile.initial_keyframe_name,
        qpos_rad=(0.0,),
        tip_position_m=(0.0, 0.0, 0.0),
        tool_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        frame=profile.coordinate_units.coordinate_frame,
        position_unit=profile.coordinate_units.position_unit,
        orientation_unit="unit_quaternion",
        quaternion_order=profile.coordinate_units.quaternion_order,
    )
    providers = [
        CapabilityProviderBinding(
            RESET_INITIAL_STATE_V1,
            NamedKeyframeInitialStateProvider(
                profile,
                contract,
                robot_identity=identity,
            ),
        ),
        CapabilityProviderBinding(
            ENDPOINT_COMMAND_V1,
            _EndpointCommandProvider(ProviderAssemblyBinding(identity, plugin)),
        ),
        CapabilityProviderBinding(
            QPOS_FEASIBILITY_V1,
            _QposFeasibilityProvider(ProviderAssemblyBinding(identity, plugin)),
        ),
        CapabilityProviderBinding(
            SCENE_ROLE_BINDING_V1,
            _SceneRoleProvider(ProviderAssemblyBinding(identity, profile)),
        ),
    ]
    if include_endpoint_pose:
        providers.append(
            CapabilityProviderBinding(
                ENDPOINT_POSE_V1,
                _EndpointPoseProvider(ProviderAssemblyBinding(identity, plugin)),
            )
        )
    if duplicate_endpoint_pose:
        providers.append(
            CapabilityProviderBinding(
                ENDPOINT_POSE_V1,
                _EndpointPoseProvider(ProviderAssemblyBinding(identity, plugin)),
            )
        )
    return RobotBundle(
        identity=identity,
        profile=profile,
        runtime_plugin=plugin,
        capability_providers=tuple(providers),
        command_semantic_providers=tuple(
            RobotCommandSemanticProviderBinding(
                semantic_identity,
                _CommandSemanticProvider(
                    ProviderAssemblyBinding(identity, plugin),
                    semantic_identity,
                    (
                        EndpointVelocityCommand
                        if semantic_identity == ENDPOINT_VELOCITY_COMMAND_V1
                        else JointPositionCommand
                    ),
                ),
            )
            for semantic_identity in sorted(command_semantics)
        ),
        parameter_contract=parameter_contract,
    )


class _NoOpPublisher:
    async def publish(self, state) -> None:  # noqa: ANN001
        _ = state


def test_generic_experiment_bundle_identity_can_differ_from_profile_identity() -> None:
    bundle = _dummy_bundle()

    assert bundle.identity == VersionedIdentity("dummy_robot_bundle", 1)
    assert bundle.profile.profile_id == "dummy_robot"


def _assert_production_builder_rejects_prebound_execution(
    execution: ResolvedCommandExecution,
) -> None:
    with pytest.raises(TypeError, match="resolved_command_execution"):
        build_concrete_mujoco_pipeline(
            publisher=_NoOpPublisher(),
            **{"resolved_command_execution": execution},
        )


def test_production_builder_rejects_other_robot_prebound_provider() -> None:
    robot_a = _dummy_bundle(
        identity=VersionedIdentity("dummy_robot_a", 1)
    )
    robot_b = _dummy_bundle(
        identity=VersionedIdentity("dummy_robot_b", 1)
    )
    mapping = _mapping()
    foreign_execution = resolve_command_execution(mapping, robot_b, None)

    assert (
        foreign_execution.binding.provider
        is not robot_a.command_semantic_provider(JOINT_POSITION_COMMAND_V1)
    )
    _assert_production_builder_rejects_prebound_execution(foreign_execution)


def test_production_builder_rejects_stale_logical_version_execution() -> None:
    current_robot = _dummy_bundle(
        identity=VersionedIdentity("dummy_versioned_robot", 1)
    )
    stale_robot = _dummy_bundle(
        identity=VersionedIdentity("dummy_versioned_robot", 2)
    )
    mapping = _mapping()
    stale_execution = resolve_command_execution(mapping, stale_robot, None)

    assert (
        stale_execution.binding.provider.assembly_binding.robot_identity
        != current_robot.identity
    )
    _assert_production_builder_rejects_prebound_execution(stale_execution)


def test_production_builder_rejects_same_identity_custom_strategy_execution() -> None:
    robot = _dummy_bundle()
    canonical_route = _mapping().resolve_command_semantics_route()

    class _CustomJointPositionStrategy:
        route_identity = canonical_route.identity
        control_semantics_identity = canonical_route.control_semantics_identity
        robot_command_semantics_identity = (
            canonical_route.robot_command_semantics_identity
        )
        command_type = JointPositionCommand

        def bind(self, provider):  # noqa: ANN001, ANN201
            return JointPositionCommandExecutionBinding(
                route_identity=self.route_identity,
                control_semantics_identity=self.control_semantics_identity,
                robot_command_semantics_identity=(
                    self.robot_command_semantics_identity
                ),
                provider=provider,
            )

    custom_route = replace(
        canonical_route,
        execution_strategy=_CustomJointPositionStrategy(),
    )
    custom_execution = ResolvedCommandExecution(
        route=custom_route,
        binding=custom_route.execution_strategy.bind(
            robot.command_semantic_provider(JOINT_POSITION_COMMAND_V1)
        ),
    )

    assert (
        custom_execution.route.execution_strategy
        is not canonical_route.execution_strategy
    )
    _assert_production_builder_rejects_prebound_execution(custom_execution)


def _environment(
    *,
    identity: VersionedIdentity = VersionedIdentity("dummy_environment", 1),
    roles: tuple[EnvironmentRole, ...] | None = None,
    required_robot_roles: frozenset[SemanticRoleRequirement] = frozenset(
        {ROBOT_TOOL_REQUIREMENT}
    ),
    produced_evidence: frozenset[VersionedIdentity] = frozenset(),
    compatible_robot_bundles: frozenset[VersionedIdentity] = frozenset(
        {VersionedIdentity("dummy_robot_bundle", 1)}
    ),
    parameter_contract: ParameterContract = ParameterContract(
        (ParameterField("target_x", float),)
    ),
) -> EnvironmentPlugin:
    role_descriptors = (
        (EnvironmentRole(TARGET_ROLE, "target", "world", "meter"),)
        if roles is None
        else roles
    )
    return EnvironmentPlugin(
        identity=identity,
        scene_provider=_SceneProvider(),
        roles=role_descriptors,
        required_robot_capabilities=frozenset({SCENE_ROLE_BINDING_V1}),
        required_robot_roles=required_robot_roles,
        parameter_contract=parameter_contract,
        produced_evidence=produced_evidence,
        compatible_robot_bundles=compatible_robot_bundles,
        compatible_backend_kinds=frozenset({"dummy"}),
    )


def _mapping(
    *,
    identity: VersionedIdentity = VersionedIdentity("dummy_mapping", 1),
    produced_evidence: frozenset[VersionedIdentity] = frozenset(),
) -> ControlMappingPlugin:
    return ControlMappingPlugin(
        identity=identity,
        strategy=_MappingStrategy(),
        accepted_input_sample_schemas=frozenset({CONFORMANCE_SAMPLE_SCHEMA}),
        required_robot_capabilities=frozenset({ENDPOINT_COMMAND_V1}),
        produced_evidence=produced_evidence,
        comparison_family_identity=VersionedIdentity("dummy_mapping_family", 1),
        mapping_semantics_identity=VersionedIdentity("dummy_mapping_semantics", 1),
        command_semantics_routes=frozenset(
            {
                joint_position_command_route(
                    route_identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
                    control_semantics_identity=VersionedIdentity(
                        "dummy_mapping_semantics", 1
                    ),
                )
            }
        ),
    )


def _task(
    *,
    identity: VersionedIdentity = VersionedIdentity("dummy_reach_task", 1),
    produced_evidence: frozenset[VersionedIdentity] = frozenset(
        {TASK_TERMINAL_EVIDENCE}
    ),
    required_semantic_roles: frozenset[SemanticRoleRequirement] = frozenset(
        {ROBOT_TOOL_REQUIREMENT, TARGET_REQUIREMENT}
    ),
    compatible_robot_bundles: frozenset[VersionedIdentity] = frozenset(
        {VersionedIdentity("dummy_robot_bundle", 1)}
    ),
    compatible_environments: frozenset[VersionedIdentity] = frozenset(
        {VersionedIdentity("dummy_environment", 1)}
    ),
    parameter_contract: ParameterContract = ParameterContract(),
) -> TaskPlugin:
    return TaskPlugin(
        identity=identity,
        lifecycle=_TaskLifecycle(),
        required_robot_capabilities=frozenset({ENDPOINT_POSE_V1}),
        required_semantic_roles=required_semantic_roles,
        parameter_contract=parameter_contract,
        task_event_identity=TASK_TERMINAL_EVIDENCE,
        produced_evidence=produced_evidence,
        compatible_robot_bundles=compatible_robot_bundles,
        compatible_environments=compatible_environments,
        compatible_backend_kinds=frozenset({"dummy"}),
    )


def _evaluator(
    *,
    identity: VersionedIdentity = EVALUATOR_IDENTITY,
    required: VersionedIdentity = TASK_TERMINAL_EVIDENCE,
    metric_deriver: _SuccessMetric | None = None,
) -> EvaluationPlugin:
    return EvaluationPlugin(
        identity=identity,
        metric_deriver=metric_deriver or _SuccessMetric(metric_id=identity),
        required_evidence=frozenset({required}),
        evidence_policy=EvidencePolicy(),
        parameter_contract=ParameterContract(),
        provenance=f"{identity.canonical_id}:deterministic",
    )


def _registries(
    *,
    bundle: RobotBundle | None = None,
    environment: EnvironmentPlugin | None = None,
    mapping: ControlMappingPlugin | None = None,
    task: TaskPlugin | None = None,
    evaluator: EvaluationPlugin | None = None,
    input_source=None,
) -> ExperimentPluginRegistries:
    return ExperimentPluginRegistries(
        robot_bundles=VersionedPluginRegistry(
            (bundle or _dummy_bundle(),), kind="Robot Bundle"
        ),
        environments=VersionedPluginRegistry(
            (environment or _environment(),), kind="environment plugin"
        ),
        control_mappings=VersionedPluginRegistry(
            (mapping or _mapping(),), kind="mapping plugin"
        ),
        tasks=VersionedPluginRegistry((task or _task(),), kind="task plugin"),
        evaluators=VersionedPluginRegistry(
            (evaluator or _evaluator(),), kind="evaluation plugin"
        ),
        input_sources=VersionedPluginRegistry(
            (input_source or build_conformance_input_source(),),
            kind="input source plugin",
        ),
    )


def _manifest(**overrides) -> ExperimentPluginManifest:
    values = {
        "robot_bundle": PluginSelection("dummy_robot_bundle", 1),
        "environment": PluginSelection("dummy_environment", 1),
        "control_mapping": PluginSelection("dummy_mapping", 1),
        "task": PluginSelection("dummy_reach_task", 1),
        "input_source": PluginSelection(CONFORMANCE_INPUT_SOURCE.name, 1),
        "command_semantics_route": LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
        "evaluators": (PluginSelection("dummy_success_evaluator", 1),),
        "parameters": (
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.ENVIRONMENT,
                    PluginSelection("dummy_environment", 1),
                ),
                {"target_x": 0.2},
            ),
        ),
    }
    values.update(overrides)
    return ExperimentPluginManifest(**values)


# Public test builders are shared by the generic input-source contract tests.
# Keeping these builders here avoids importing another test module's private
# helpers while preserving the existing composition fixture ownership.
def build_test_manifest(**overrides) -> ExperimentPluginManifest:
    return _manifest(**overrides)


def build_test_registries(
    *,
    bundle: RobotBundle | None = None,
    environment: EnvironmentPlugin | None = None,
    mapping: ControlMappingPlugin | None = None,
    task: TaskPlugin | None = None,
    evaluator: EvaluationPlugin | None = None,
    input_source=None,
) -> ExperimentPluginRegistries:
    return _registries(
        bundle=bundle,
        environment=environment,
        mapping=mapping,
        task=task,
        evaluator=evaluator,
        input_source=input_source,
    )


def build_test_mapping(**overrides) -> ControlMappingPlugin:
    return _mapping(**overrides)


def build_test_task(**overrides) -> TaskPlugin:
    return _task(**overrides)


def test_non_fast_arm_conformance_composition_resolves_all_axes_before_startup() -> None:
    resolved = compose_experiment(_manifest(), _registries())

    assert resolved.robot_bundle.identity.canonical_id == "dummy_robot_bundle/v1"
    assert resolved.environment.identity.canonical_id == "dummy_environment/v1"
    assert resolved.control_mapping.identity.canonical_id == "dummy_mapping/v1"
    assert resolved.task.identity.canonical_id == "dummy_reach_task/v1"
    assert tuple(item.identity.canonical_id for item in resolved.evaluators) == (
        "dummy_success_evaluator/v1",
    )
    assert ENDPOINT_POSE_V1 in resolved.resolved_capabilities
    assert resolved.resolved_roles == frozenset({ROBOT_TOOL_ENDPOINT_ROLE, TARGET_ROLE})
    assert TARGET_REQUIREMENT.matches(
        next(
            descriptor
            for descriptor in resolved.resolved_role_descriptors
            if descriptor.role == TARGET_ROLE
        )
    )
    assert resolved.evidence_producer(TASK_TERMINAL_EVIDENCE) == EvidenceProducerBinding(
        producer_axis=PluginAxis.TASK,
        producer_identity=VersionedIdentity("dummy_reach_task", 1),
        evidence_identity=TASK_TERMINAL_EVIDENCE,
    )
    reset = resolved.robot_bundle.provider(RESET_INITIAL_STATE_V1)
    assert reset.resolve_initial_state().source_id == "neutral"


def test_discovered_logical_v2_fixture_uses_plugin_selection_in_resolved_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib
    import sys

    from xpotato_sim.plugins.robots.discovery import (
        RobotDiscoveryRoot,
        discover_robot_plugins,
    )

    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "robot_plugins"
    for name in tuple(sys.modules):
        if name == "test_robot_plugins" or name.startswith("test_robot_plugins."):
            sys.modules.pop(name)
    monkeypatch.syspath_prepend(str(fixture_root))
    namespace = importlib.import_module("test_robot_plugins")
    registration = discover_robot_plugins(
        RobotDiscoveryRoot(
            namespace=namespace,
            repository_root=fixture_root,
            asset_roots=(fixture_root / "assets" / "mujoco",),
            configuration_roots=(fixture_root / "configs",),
        )
    ).resolve("fixture_bot", robot_logical_version=2)
    bundle = registration.bundle
    robot_requirement = SemanticRoleRequirement(
        ROBOT_TOOL_ENDPOINT_ROLE,
        object_kind="robot_endpoint",
        frame=ROLE_ATTRIBUTE_WILDCARD,
        unit="meter",
    )
    environment = replace(
        _environment(
            required_robot_roles=frozenset({robot_requirement}),
            compatible_robot_bundles=frozenset({bundle.identity}),
        ),
        compatible_backend_kinds=frozenset({"mujoco"}),
    )
    task = replace(
        _task(
            required_semantic_roles=frozenset(
                {robot_requirement, TARGET_REQUIREMENT}
            ),
            compatible_robot_bundles=frozenset({bundle.identity}),
        ),
        compatible_backend_kinds=frozenset({"mujoco"}),
    )
    resolved = compose_experiment(
        _manifest(robot_bundle=PluginSelection("fixture_bot", 2)),
        _registries(bundle=bundle, environment=environment, task=task),
    )

    assert resolved.robot_bundle is bundle
    assert resolved.robot_bundle.identity == VersionedIdentity("fixture_bot", 2)
    reset = resolved.robot_bundle.provider(RESET_INITIAL_STATE_V1)
    assert reset.resolve_initial_state().source_id == "home"


def test_parameter_owners_with_same_raw_id_remain_axis_scoped() -> None:
    shared_identity = VersionedIdentity("shared_plugin_id", 1)
    environment = _environment(identity=shared_identity)
    task = _task(
        identity=shared_identity,
        compatible_environments=frozenset({shared_identity}),
        parameter_contract=ParameterContract((ParameterField("timeout_s", float),)),
    )
    manifest = _manifest(
        environment=PluginSelection("shared_plugin_id", 1),
        task=PluginSelection("shared_plugin_id", 1),
        parameters=(
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.ENVIRONMENT,
                    PluginSelection("shared_plugin_id", 1),
                ),
                {"target_x": 0.2},
            ),
            PluginParameters(
                PluginParameterOwner(
                    PluginAxis.TASK,
                    PluginSelection("shared_plugin_id", 1),
                ),
                {"timeout_s": 5.0},
            ),
        ),
    )

    resolved = compose_experiment(
        manifest,
        _registries(environment=environment, task=task),
    )

    assert resolved.environment is environment
    assert resolved.task is task


@pytest.mark.parametrize(
    "owner",
    (
        PluginParameterOwner(
            PluginAxis.ENVIRONMENT,
            PluginSelection("not-selected", 1),
        ),
        PluginParameterOwner(
            PluginAxis.ENVIRONMENT,
            PluginSelection("dummy_environment", 2),
        ),
        PluginParameterOwner(
            PluginAxis.TASK,
            PluginSelection("dummy_environment", 1),
        ),
    ),
)
def test_parameter_owner_must_exactly_match_selected_axis_id_and_version(
    owner: PluginParameterOwner,
) -> None:
    with pytest.raises(ValueError, match="parameters supplied for unselected plugins"):
        compose_experiment(
            _manifest(parameters=(PluginParameters(owner, {}),)),
            _registries(),
        )


def test_versioned_registries_reject_unknown_duplicate_and_version_mismatch() -> None:
    registry = VersionedPluginRegistry((_mapping(),), kind="mapping plugin")

    with pytest.raises(ValueError, match="unknown mapping plugin ID"):
        registry.resolve(PluginSelection("unknown", 1))
    with pytest.raises(ValueError, match="contract version mismatch"):
        registry.resolve(PluginSelection("dummy_mapping", 2))
    with pytest.raises(ValueError, match="duplicate mapping plugin registration"):
        VersionedPluginRegistry((_mapping(), _mapping()), kind="mapping plugin")


def test_composition_rejects_registry_set_type_mismatch() -> None:
    registries = _registries()
    mismatched = ExperimentPluginRegistries(
        robot_bundles=registries.robot_bundles,
        environments=registries.environments,
        control_mappings=VersionedPluginRegistry((_task(),), kind="mapping plugin"),
        tasks=registries.tasks,
        evaluators=registries.evaluators,
        input_sources=registries.input_sources,
    )

    with pytest.raises(ValueError, match="registry-set type mismatch for control mapping"):
        compose_experiment(
            _manifest(control_mapping=PluginSelection("dummy_reach_task", 1)),
            mismatched,
        )


def test_composition_rejects_missing_capability_without_fallback() -> None:
    with pytest.raises(ValueError, match="unsupported Robot Bundle capability 'endpoint_pose/v1'"):
        compose_experiment(
            _manifest(),
            _registries(bundle=_dummy_bundle(include_endpoint_pose=False)),
        )


def test_composition_rejects_semantic_role_binding_failure() -> None:
    with pytest.raises(ValueError, match="semantic role binding failure"):
        compose_experiment(
            _manifest(),
            _registries(environment=_environment(roles=())),
        )


def test_environment_robot_role_requirement_cannot_be_satisfied_by_environment() -> None:
    environment = _environment(
        required_robot_roles=frozenset({TARGET_REQUIREMENT})
    )

    with pytest.raises(ValueError, match="semantic role binding failure"):
        compose_experiment(_manifest(), _registries(environment=environment))


def test_environment_rejects_duplicate_semantic_role() -> None:
    descriptor = EnvironmentRole(TARGET_ROLE, "target", "world", "meter")

    with pytest.raises(ValueError, match="environment plugin roles must be unique"):
        _environment(roles=(descriptor, descriptor))


def test_composition_rejects_duplicate_role_across_robot_and_environment() -> None:
    environment = _environment(
        roles=(
            EnvironmentRole(
                ROBOT_TOOL_ENDPOINT_ROLE,
                "robot_endpoint",
                "dummy world",
                "meter",
            ),
            EnvironmentRole(TARGET_ROLE, "target", "world", "meter"),
        )
    )

    with pytest.raises(ValueError, match="ambiguous semantic role binding"):
        compose_experiment(_manifest(), _registries(environment=environment))


@pytest.mark.parametrize(
    ("requirement", "mismatch"),
    (
        (
            SemanticRoleRequirement(TARGET_ROLE, "obstacle", "world", "meter"),
            "object kind",
        ),
        (
            SemanticRoleRequirement(TARGET_ROLE, "target", "tool", "meter"),
            "frame",
        ),
        (
            SemanticRoleRequirement(TARGET_ROLE, "target", "world", "millimeter"),
            "unit",
        ),
    ),
)
def test_composition_rejects_typed_semantic_role_mismatch(
    requirement: SemanticRoleRequirement,
    mismatch: str,
) -> None:
    task = _task(
        required_semantic_roles=frozenset({ROBOT_TOOL_REQUIREMENT, requirement})
    )

    with pytest.raises(ValueError, match=rf"semantic role compatibility mismatch.*{mismatch}"):
        compose_experiment(_manifest(), _registries(task=task))


def test_semantic_role_wildcards_are_explicit_and_accepted() -> None:
    wildcard_target = SemanticRoleRequirement(
        TARGET_ROLE,
        object_kind=ROLE_ATTRIBUTE_WILDCARD,
        frame=ROLE_ATTRIBUTE_WILDCARD,
        unit=ROLE_ATTRIBUTE_WILDCARD,
    )
    task = _task(
        required_semantic_roles=frozenset(
            {ROBOT_TOOL_REQUIREMENT, wildcard_target}
        )
    )

    resolved = compose_experiment(_manifest(), _registries(task=task))

    assert TARGET_ROLE in resolved.resolved_roles


def test_composition_rejects_evaluator_evidence_requirement_mismatch() -> None:
    with pytest.raises(ValueError, match="evaluator evidence requirement mismatch"):
        compose_experiment(
            _manifest(),
            _registries(evaluator=_evaluator(required=UNKNOWN_EVIDENCE)),
        )


def test_composition_rejects_ambiguous_evidence_producer() -> None:
    mapping = _mapping(produced_evidence=frozenset({TASK_TERMINAL_EVIDENCE}))

    with pytest.raises(ValueError, match="ambiguous canonical evidence producer"):
        compose_experiment(_manifest(), _registries(mapping=mapping))


def test_robot_bundle_rejects_ambiguous_provider_and_has_no_unsupported_default() -> None:
    with pytest.raises(ValueError, match="ambiguous Robot Bundle capability provider"):
        _dummy_bundle(duplicate_endpoint_pose=True)

    with pytest.raises(
        ValueError,
        match="unsupported Robot Bundle capability 'contact_evidence/v1'",
    ):
        _dummy_bundle().provider(CONTACT_EVIDENCE_V1)


def test_capability_provider_contract_mapping_is_immutable_and_typed() -> None:
    with pytest.raises(TypeError):
        CAPABILITY_PROVIDER_TYPES[ENDPOINT_POSE_V1] = (  # type: ignore[index]
            _EndpointCommandProvider
        )
    with pytest.raises(TypeError, match="does not satisfy EndpointPoseProvider"):
        CapabilityProviderBinding(
            ENDPOINT_POSE_V1,
            _EndpointCommandProvider(
                ProviderAssemblyBinding(VersionedIdentity("dummy", 1), object())
            ),
        )
    with pytest.raises(ValueError, match="unknown capability identity"):
        CapabilityProviderBinding(
            VersionedIdentity("not_registered", 1),
            _EndpointPoseProvider(
                ProviderAssemblyBinding(VersionedIdentity("dummy", 1), object())
            ),
        )


def test_composition_rejects_robot_environment_task_compatibility_mismatch() -> None:
    incompatible = EnvironmentPlugin(
        identity=VersionedIdentity("dummy_environment", 1),
        scene_provider=_SceneProvider(),
        roles=(EnvironmentRole(TARGET_ROLE, "target", "world", "meter"),),
        required_robot_capabilities=frozenset({SCENE_ROLE_BINDING_V1}),
        required_robot_roles=frozenset({ROBOT_TOOL_REQUIREMENT}),
        parameter_contract=ParameterContract((ParameterField("target_x", float),)),
        compatible_backend_kinds=frozenset({"not-dummy"}),
    )
    with pytest.raises(ValueError, match="robot backend/environment compatibility mismatch"):
        compose_experiment(_manifest(), _registries(environment=incompatible))


def test_exact_versioned_cross_plugin_compatibility_accepts_v1() -> None:
    resolved = compose_experiment(_manifest(), _registries())

    assert resolved.robot_bundle.identity == VersionedIdentity("dummy_robot_bundle", 1)
    assert resolved.environment.identity == VersionedIdentity("dummy_environment", 1)


def test_command_semantic_identities_are_versioned_and_distinct() -> None:
    from xpotato_sim.runtime.experiment.contracts import (
        ENDPOINT_POSITION_COMMAND_V1,
        JOINT_VELOCITY_COMMAND_V1,
    )

    identities = {
        ENDPOINT_POSITION_COMMAND_V1,
        ENDPOINT_VELOCITY_COMMAND_V1,
        JOINT_POSITION_COMMAND_V1,
        JOINT_VELOCITY_COMMAND_V1,
    }
    assert {item.canonical_id for item in identities} == {
        "endpoint_position_command/v1",
        "endpoint_velocity_command/v1",
        "joint_position_command/v1",
        "joint_velocity_command/v1",
    }


def test_native_endpoint_velocity_route_composes_with_test_only_robot() -> None:
    native_route = CommandSemanticsRoute(
        identity=NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1,
        control_semantics_identity=VersionedIdentity("dummy_mapping_semantics", 1),
        robot_command_semantics_identity=ENDPOINT_VELOCITY_COMMAND_V1,
        execution_strategy=NativeEndpointVelocityCommandRouteExecutionStrategy(
            route_identity=NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1,
            control_semantics_identity=VersionedIdentity(
                "dummy_mapping_semantics", 1
            ),
            robot_command_semantics_identity=ENDPOINT_VELOCITY_COMMAND_V1,
        ),
    )
    mapping = replace(
        _mapping(),
        command_semantics_routes=frozenset({native_route}),
    )
    robot = _dummy_bundle(
        command_semantics=frozenset({ENDPOINT_VELOCITY_COMMAND_V1})
    )

    resolved = compose_experiment(
        _manifest(
            command_semantics_route=NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1
        ),
        _registries(mapping=mapping, bundle=robot),
    )

    assert resolved.resolved_command_semantics_route == native_route


def test_native_endpoint_velocity_route_rejects_joint_position_only_robot() -> None:
    native_route = CommandSemanticsRoute(
        identity=NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1,
        control_semantics_identity=VersionedIdentity("dummy_mapping_semantics", 1),
        robot_command_semantics_identity=ENDPOINT_VELOCITY_COMMAND_V1,
        execution_strategy=NativeEndpointVelocityCommandRouteExecutionStrategy(
            route_identity=NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1,
            control_semantics_identity=VersionedIdentity(
                "dummy_mapping_semantics", 1
            ),
            robot_command_semantics_identity=ENDPOINT_VELOCITY_COMMAND_V1,
        ),
    )
    mapping = replace(
        _mapping(),
        command_semantics_routes=frozenset({native_route}),
    )

    with pytest.raises(
        ValueError,
        match="mapping/Robot command semantics compatibility mismatch",
    ):
        compose_experiment(
            _manifest(
                command_semantics_route=NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1
            ),
            _registries(mapping=mapping),
        )


def test_robot_bundle_rejects_command_semantic_without_provider() -> None:
    with pytest.raises(
        ValueError,
        match="bind at least one executable command semantic provider",
    ):
        _dummy_bundle(command_semantics=frozenset())


def test_joint_position_route_rejects_broad_motion_command_provider() -> None:
    bundle = _dummy_bundle()
    provider_binding = bundle.command_semantic_providers[0]
    with pytest.raises(
        TypeError,
        match="command type mismatch",
    ):
        RobotCommandSemanticProviderBinding(
            provider_binding.identity,
            replace(
                provider_binding.provider,
                command_type=MotionCommand,
            ),
        )


def test_robot_bundle_rejects_endpoint_velocity_semantic_with_joint_position_type() -> None:
    bundle = _dummy_bundle()
    provider_binding = bundle.command_semantic_providers[0]
    with pytest.raises(
        TypeError,
        match="command type mismatch",
    ):
        RobotCommandSemanticProviderBinding(
            ENDPOINT_VELOCITY_COMMAND_V1,
            replace(
                provider_binding.provider,
                command_semantics_identity=ENDPOINT_VELOCITY_COMMAND_V1,
                command_type=JointPositionCommand,
            ),
        )


def test_selected_route_and_execution_binding_mismatch_fails_closed() -> None:
    control_semantics = VersionedIdentity("dummy_mapping_semantics", 1)

    @dataclass(frozen=True)
    class _MismatchedBindingStrategy:
        route_identity = LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
        control_semantics_identity = control_semantics
        robot_command_semantics_identity = JOINT_POSITION_COMMAND_V1
        command_type = JointPositionCommand

        def bind(self, provider):
            valid = JointPositionCommandRouteExecutionStrategy(
                route_identity=self.route_identity,
                control_semantics_identity=self.control_semantics_identity,
                robot_command_semantics_identity=(
                    self.robot_command_semantics_identity
                ),
            ).bind(provider)
            assert isinstance(valid, JointPositionCommandExecutionBinding)
            return replace(
                valid,
                route_identity=VersionedIdentity("wrong_execution_route", 1),
            )

    route = CommandSemanticsRoute(
        identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
        control_semantics_identity=control_semantics,
        robot_command_semantics_identity=JOINT_POSITION_COMMAND_V1,
        execution_strategy=_MismatchedBindingStrategy(),
    )
    mapping = replace(
        _mapping(),
        command_semantics_routes=frozenset({route}),
    )

    with pytest.raises(
        ValueError,
        match="selected command route and execution binding identity mismatch",
    ):
        compose_experiment(_manifest(), _registries(mapping=mapping))


def test_command_route_rejects_strategy_command_type_mismatch() -> None:
    control_semantics = VersionedIdentity("dummy_mapping_semantics", 1)

    @dataclass(frozen=True)
    class _WrongTypeStrategy:
        route_identity = LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
        control_semantics_identity = control_semantics
        robot_command_semantics_identity = JOINT_POSITION_COMMAND_V1
        command_type = EndpointVelocityCommand

        def bind(self, provider):
            _ = provider
            raise AssertionError("invalid strategy must fail before binding")

    with pytest.raises(
        TypeError,
        match="execution strategy command type mismatch",
    ):
        CommandSemanticsRoute(
            identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
            control_semantics_identity=control_semantics,
            robot_command_semantics_identity=JOINT_POSITION_COMMAND_V1,
            execution_strategy=_WrongTypeStrategy(),
        )


def test_resolved_execution_rejects_binding_command_type_mismatch() -> None:
    control_semantics = VersionedIdentity("dummy_mapping_semantics", 1)

    @dataclass(frozen=True)
    class _WrongBinding:
        route_identity = LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1
        control_semantics_identity = control_semantics
        robot_command_semantics_identity = JOINT_POSITION_COMMAND_V1
        command_type = EndpointVelocityCommand
        requires_motion_generator = True

        def execute(self, intent, **kwargs):
            _ = intent
            _ = kwargs
            raise AssertionError("invalid binding must fail before execution")

    route = CommandSemanticsRoute(
        identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
        control_semantics_identity=control_semantics,
        robot_command_semantics_identity=JOINT_POSITION_COMMAND_V1,
        execution_strategy=JointPositionCommandRouteExecutionStrategy(
            route_identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
            control_semantics_identity=control_semantics,
            robot_command_semantics_identity=JOINT_POSITION_COMMAND_V1,
        ),
    )

    with pytest.raises(
        TypeError,
        match="execution binding command type mismatch",
    ):
        ResolvedCommandExecution(route, _WrongBinding())


def test_cross_plugin_compatibility_rejects_robot_bundle_version_mismatch() -> None:
    bundle_v2 = _dummy_bundle(
        identity=VersionedIdentity("dummy_robot_bundle", 2)
    )
    task = _task(
        compatible_robot_bundles=frozenset(
            {VersionedIdentity("dummy_robot_bundle", 2)}
        )
    )

    with pytest.raises(ValueError, match="robot/environment compatibility mismatch"):
        compose_experiment(
            _manifest(robot_bundle=PluginSelection("dummy_robot_bundle", 2)),
            _registries(bundle=bundle_v2, task=task),
        )


def test_task_robot_compatibility_rejects_robot_bundle_version_mismatch() -> None:
    bundle_v2 = _dummy_bundle(
        identity=VersionedIdentity("dummy_robot_bundle", 2)
    )
    environment = _environment(
        compatible_robot_bundles=frozenset(
            {VersionedIdentity("dummy_robot_bundle", 2)}
        )
    )

    with pytest.raises(ValueError, match="robot/task compatibility mismatch"):
        compose_experiment(
            _manifest(robot_bundle=PluginSelection("dummy_robot_bundle", 2)),
            _registries(bundle=bundle_v2, environment=environment),
        )


def test_cross_plugin_compatibility_rejects_environment_version_mismatch() -> None:
    environment_v2 = _environment(
        identity=VersionedIdentity("dummy_environment", 2)
    )

    with pytest.raises(ValueError, match="environment/task compatibility mismatch"):
        compose_experiment(
            _manifest(
                environment=PluginSelection("dummy_environment", 2),
                parameters=(
                    PluginParameters(
                        PluginParameterOwner(
                            PluginAxis.ENVIRONMENT,
                            PluginSelection("dummy_environment", 2),
                        ),
                        {"target_x": 0.2},
                    ),
                ),
            ),
            _registries(environment=environment_v2),
        )


def test_unspecified_cross_plugin_compatibility_remains_generic() -> None:
    environment = _environment(compatible_robot_bundles=frozenset())
    task = _task(
        compatible_robot_bundles=frozenset(),
        compatible_environments=frozenset(),
    )

    resolved = compose_experiment(
        _manifest(),
        _registries(environment=environment, task=task),
    )

    assert resolved.environment is environment
    assert resolved.task is task


def test_evidence_statuses_remain_distinct_and_metric_is_deterministic() -> None:
    evaluator = _evaluator()
    evidence = CanonicalEvidenceSet(
        (
            CanonicalEvidence(
                identity=TASK_TERMINAL_EVIDENCE,
                status=EvidenceStatus.MEASURED,
                value=TaskTerminalClassification.SUCCESS.value,
                provenance="dummy_task/v1:terminal",
            ),
        )
    )

    first = evaluator.derive_metric(evidence, {})
    second = evaluator.derive_metric(evidence, {})
    assert first == second
    assert first.value is True
    assert first.provenance == "dummy_success_evaluator/v1:deterministic"

    with pytest.raises(ValueError, match="unavailable evidence must not carry a value"):
        CanonicalEvidence(
            identity=TASK_TERMINAL_EVIDENCE,
            status=EvidenceStatus.UNAVAILABLE,
            value=False,
            provenance="dummy",
            reason="not observed",
        )



@pytest.mark.parametrize(
    "status",
    (EvidenceStatus.UNAVAILABLE, EvidenceStatus.INVALID),
)
def test_metric_result_unavailable_and_invalid_invariants(
    status: EvidenceStatus,
) -> None:
    with pytest.raises(ValueError, match=rf"{status.value} metric must not carry a value"):
        MetricResult(
            metric_id=EVALUATOR_IDENTITY,
            value=False,
            status=status,
            provenance="dummy_success_evaluator/v1:deterministic",
            reason="not usable",
        )
    with pytest.raises(ValueError, match=rf"{status.value} metric requires a reason"):
        MetricResult(
            metric_id=EVALUATOR_IDENTITY,
            value=None,
            status=status,
            provenance="dummy_success_evaluator/v1:deterministic",
        )


def test_evaluator_rejects_strategy_metric_identity_mismatch() -> None:
    evaluator = _evaluator(
        metric_deriver=_SuccessMetric(
            metric_id=VersionedIdentity("wrong_metric", 1)
        )
    )
    evidence = CanonicalEvidenceSet(
        (
            CanonicalEvidence(
                identity=TASK_TERMINAL_EVIDENCE,
                status=EvidenceStatus.MEASURED,
                value=TaskTerminalClassification.SUCCESS.value,
                provenance="dummy_task/v1:terminal",
            ),
        )
    )

    with pytest.raises(ValueError, match="evaluation metric identity mismatch"):
        evaluator.derive_metric(evidence, {})


def test_evaluator_rejects_strategy_provenance_mismatch() -> None:
    evaluator = _evaluator(
        metric_deriver=_SuccessMetric(provenance_override="wrong provenance")
    )
    evidence = CanonicalEvidenceSet(
        (
            CanonicalEvidence(
                identity=TASK_TERMINAL_EVIDENCE,
                status=EvidenceStatus.MEASURED,
                value=TaskTerminalClassification.SUCCESS.value,
                provenance="dummy_task/v1:terminal",
            ),
        )
    )

    with pytest.raises(ValueError, match="evaluation metric provenance mismatch"):
        evaluator.derive_metric(evidence, {})


def test_evaluator_applies_declared_missing_policy_without_inventing_a_metric() -> None:
    evaluator = EvaluationPlugin(
        identity=VersionedIdentity("policy_evaluator", 1),
        metric_deriver=_SuccessMetric(),
        required_evidence=frozenset({TASK_TERMINAL_EVIDENCE}),
        evidence_policy=EvidencePolicy(
            missing=EvidenceDisposition.PRODUCE_UNAVAILABLE
        ),
        parameter_contract=ParameterContract(),
        provenance="policy_evaluator/v1:deterministic",
    )

    result = evaluator.derive_metric(CanonicalEvidenceSet(()), {})

    assert result.status is EvidenceStatus.UNAVAILABLE
    assert result.value is None
    assert result.reason == "missing required evidence task.terminal_classification/v1"


def test_fast_arm_bundle_reuses_existing_profile_plugin_and_home_contract() -> None:
    bundle = resolve_robot_bundle("fast_arm", contract_version=1)

    assert bundle.runtime_plugin is FAST_ARM_RUNTIME_PLUGIN
    assert bundle.profile is FAST_ARM_RUNTIME_PLUGIN.profile
    assert bundle.provider(RESET_INITIAL_STATE_V1).resolve_initial_state().source_id == "home"
    assert bundle.provided_capabilities == frozenset(
        {
            RESET_INITIAL_STATE_V1,
            ENDPOINT_POSE_V1,
            ENDPOINT_COMMAND_V1,
            QPOS_FEASIBILITY_V1,
            SCENE_ROLE_BINDING_V1,
        }
    )
    assert CONTACT_EVIDENCE_V1 not in bundle.provided_capabilities
    assert bundle.supported_command_semantics == frozenset(
        {JOINT_POSITION_COMMAND_V1}
    )
    assert ENDPOINT_VELOCITY_COMMAND_V1 not in bundle.supported_command_semantics


def test_manifest_rejects_duplicate_evaluator_and_unknown_parameter_owner() -> None:
    with pytest.raises(ValueError, match="duplicate evaluator selection"):
        _manifest(
            evaluators=(
                PluginSelection("dummy_success_evaluator", 1),
                PluginSelection("dummy_success_evaluator", 1),
            )
        )
    with pytest.raises(ValueError, match="parameters supplied for unselected plugins"):
        compose_experiment(
            _manifest(
                parameters=(
                    PluginParameters(
                        PluginParameterOwner(
                            PluginAxis.ENVIRONMENT,
                            PluginSelection("dummy_environment", 1),
                        ),
                        {"target_x": 0.2},
                    ),
                    PluginParameters(
                        PluginParameterOwner(
                            PluginAxis.TASK,
                            PluginSelection("not-selected", 1),
                        ),
                        {},
                    ),
                )
            ),
            _registries(),
        )
