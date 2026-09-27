"""6軸のgeneric experiment contractをside effect前に解決するcomposition root。

結果はreadiness/freeze用であり、production diagnostic runtimeやrunnerへの接続、
scene生成、Input Source開始、Robot command実行は行わない。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from xpotato_sim.runtime.experiment.contracts import (
    CommandSemanticsRoute,
    ControlMappingPlugin,
    EnvironmentPlugin,
    EnvironmentRole,
    EvaluationPlugin,
    PluginAxis,
    PluginParameterOwner,
    PluginSelection,
    ROLE_ATTRIBUTE_WILDCARD,
    SemanticRole,
    SemanticRoleRequirement,
    TaskPlugin,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.input_source import InputSourcePlugin
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry
from xpotato_sim.runtime.execution.command_routes import (
    CommandExecutionBinding,
    MappingRuntimeContextBinding,
    ResolvedCommandExecution,
)
from xpotato_sim.runtime.composition.robot_bundle import RobotBundle


def freeze_parameter_value(name: str, value: object) -> object:
    """Detach and recursively freeze one canonical JSON parameter value."""

    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return 0.0 if value == 0.0 else value
    if isinstance(value, (list, tuple)):
        return tuple(
            freeze_parameter_value(f"{name}[{index}]", item)
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{name} object keys must be strings")
            frozen[key] = freeze_parameter_value(f"{name}.{key}", item)
        return MappingProxyType(dict(sorted(frozen.items())))
    raise TypeError(
        f"{name} must be a canonical JSON value; got {type(value).__name__}"
    )


def parameter_value_to_document(value: object) -> object:
    """Convert an already frozen parameter value to detached JSON values."""

    if isinstance(value, Mapping):
        return {
            key: parameter_value_to_document(item)
            for key, item in sorted(value.items(), key=lambda pair: pair[0])
        }
    if isinstance(value, tuple):
        return [parameter_value_to_document(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PluginParameters:
    """owner identityとJSON-compatibleなfrozen parameter値の組。"""

    owner: PluginParameterOwner
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.owner, PluginParameterOwner):
            raise TypeError("plugin parameter owner must use PluginParameterOwner")
        if not isinstance(self.values, Mapping):
            raise TypeError("plugin parameter values must use a mapping")
        if any(not isinstance(name, str) or not name for name in self.values):
            raise TypeError("plugin parameter names must be non-empty strings")
        frozen = {
            name: freeze_parameter_value(f"plugin parameter {name!r}", value)
            for name, value in self.values.items()
        }
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(frozen.items()))))


@dataclass(frozen=True, slots=True)
class EvidenceProducerBinding:
    """解決済みproducerと、そのevidence identity/provenanceの固定binding。"""

    producer_axis: PluginAxis
    producer_identity: VersionedIdentity
    evidence_identity: VersionedIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.producer_axis, PluginAxis):
            raise TypeError("evidence producer axis must use PluginAxis")
        if not isinstance(self.producer_identity, VersionedIdentity):
            raise TypeError("evidence producer identity must use VersionedIdentity")
        if not isinstance(self.evidence_identity, VersionedIdentity):
            raise TypeError("evidence identity must use VersionedIdentity")


@dataclass(frozen=True, slots=True)
class ExperimentPluginManifest:
    """6軸のselectionとaxis-owned parameterを入力する未解決manifest。"""

    robot_bundle: PluginSelection
    environment: PluginSelection
    control_mapping: PluginSelection
    task: PluginSelection
    input_source: PluginSelection
    command_semantics_route: VersionedIdentity
    evaluators: tuple[PluginSelection, ...]
    parameters: tuple[PluginParameters, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.command_semantics_route, VersionedIdentity):
            raise TypeError("command_semantics_route must use VersionedIdentity")
        if not isinstance(self.evaluators, tuple):
            object.__setattr__(self, "evaluators", tuple(self.evaluators))
        if not isinstance(self.parameters, tuple):
            object.__setattr__(self, "parameters", tuple(self.parameters))
        evaluator_ids = tuple(selection.plugin_id for selection in self.evaluators)
        if len(evaluator_ids) != len(set(evaluator_ids)):
            raise ValueError("duplicate evaluator selection")
        parameter_owners = tuple(item.owner for item in self.parameters)
        if len(parameter_owners) != len(set(parameter_owners)):
            raise ValueError("duplicate plugin parameter owner")


@dataclass(frozen=True, slots=True)
class ExperimentPluginRegistries:
    """各axisのbounded registryをまとめるlookup-only dependency。"""

    robot_bundles: VersionedPluginRegistry[RobotBundle]
    environments: VersionedPluginRegistry[EnvironmentPlugin]
    control_mappings: VersionedPluginRegistry[ControlMappingPlugin]
    tasks: VersionedPluginRegistry[TaskPlugin]
    evaluators: VersionedPluginRegistry[EvaluationPlugin]
    input_sources: VersionedPluginRegistry[InputSourcePlugin]


@dataclass(frozen=True, slots=True)
class ResolvedExperimentComposition:
    """全validation後にfreezeされたsoftware-only readiness結果。

    production pluginを選べてもproduction runnerへの接続済みを意味せず、
    test fixtureのregistryも同じcontractを満たせば解決できる。
    """

    manifest: ExperimentPluginManifest
    robot_bundle: RobotBundle
    environment: EnvironmentPlugin
    control_mapping: ControlMappingPlugin
    task: TaskPlugin
    input_source: InputSourcePlugin
    resolved_input_sample_schema: VersionedIdentity
    resolved_mapping_input_sample_schema: VersionedIdentity
    resolved_command_semantics_route: CommandSemanticsRoute
    evaluators: tuple[EvaluationPlugin, ...]
    resolved_capabilities: frozenset[VersionedIdentity]
    resolved_roles: frozenset[SemanticRole]
    resolved_role_descriptors: tuple[EnvironmentRole, ...]
    available_evidence: frozenset[VersionedIdentity]
    evidence_producers: tuple[EvidenceProducerBinding, ...]

    @property
    def effective_mapping_input_sample_schema(self) -> VersionedIdentity:
        """Return the versioned representation consumed by the mapping strategy."""

        return self.resolved_mapping_input_sample_schema

    def evidence_producer(
        self, evidence_identity: VersionedIdentity
    ) -> EvidenceProducerBinding:
        matches = tuple(
            binding
            for binding in self.evidence_producers
            if binding.evidence_identity == evidence_identity
        )
        if not matches:
            raise ValueError(
                f"no canonical evidence producer for {evidence_identity.canonical_id!r}"
            )
        if len(matches) != 1:
            raise ValueError(
                f"ambiguous canonical evidence producer for "
                f"{evidence_identity.canonical_id!r}"
            )
        return matches[0]


def _parameters_by_owner(
    manifest: ExperimentPluginManifest,
) -> dict[PluginParameterOwner, Mapping[str, object]]:
    return {item.owner: item.values for item in manifest.parameters}


def _validate_compatibility(
    robot: RobotBundle,
    environment: EnvironmentPlugin,
    task: TaskPlugin,
) -> None:
    backend_kind = robot.profile.backend_kind
    if (
        environment.compatible_robot_bundles
        and robot.identity not in environment.compatible_robot_bundles
    ):
        raise ValueError("robot/environment compatibility mismatch")
    if (
        environment.compatible_backend_kinds
        and backend_kind not in environment.compatible_backend_kinds
    ):
        raise ValueError("robot backend/environment compatibility mismatch")
    if (
        task.compatible_robot_bundles
        and robot.identity not in task.compatible_robot_bundles
    ):
        raise ValueError("robot/task compatibility mismatch")
    if (
        task.compatible_environments
        and environment.identity not in task.compatible_environments
    ):
        raise ValueError("environment/task compatibility mismatch")
    if task.compatible_backend_kinds and backend_kind not in task.compatible_backend_kinds:
        raise ValueError("robot backend/task compatibility mismatch")


def resolve_command_semantics_route(
    mapping: ControlMappingPlugin,
    robot: RobotBundle,
    identity: VersionedIdentity | None,
) -> CommandSemanticsRoute:
    """Resolve one executable route and return its versioned declaration."""

    return resolve_command_execution(mapping, robot, identity).route


def resolve_command_execution(
    mapping: ControlMappingPlugin,
    robot: RobotBundle,
    identity: VersionedIdentity | None,
) -> ResolvedCommandExecution:
    """Bind one Mapping route to its matching Robot command provider."""

    route = mapping.resolve_command_semantics_route(identity)
    provider = robot.command_semantic_provider(
        route.robot_command_semantics_identity
    )
    binding = route.execution_strategy.bind(provider)
    if not isinstance(binding, CommandExecutionBinding):
        raise TypeError(
            "command route execution strategy must return a "
            "CommandExecutionBinding"
        )
    supplied_context = (
        binding.mapping_context_parameters
        if isinstance(binding, MappingRuntimeContextBinding)
        else frozenset()
    )
    if mapping.runtime_context_parameters != supplied_context:
        raise ValueError("Mapping/runtime route context declaration mismatch")
    return ResolvedCommandExecution(route=route, binding=binding)


def _validate_role_requirements(
    requirements: frozenset[SemanticRoleRequirement],
    descriptors_by_role: Mapping[SemanticRole, EnvironmentRole],
) -> None:
    for requirement in sorted(
        requirements,
        key=lambda item: (item.role.name, item.object_kind, item.frame, item.unit),
    ):
        descriptor = descriptors_by_role.get(requirement.role)
        if descriptor is None:
            raise ValueError(
                f"semantic role binding failure: missing {requirement.role.name!r}"
            )
        mismatches = tuple(
            name
            for name, expected, actual in (
                ("object kind", requirement.object_kind, descriptor.object_kind),
                ("frame", requirement.frame, descriptor.frame),
                ("unit", requirement.unit, descriptor.unit),
            )
            if expected != ROLE_ATTRIBUTE_WILDCARD and expected != actual
        )
        if mismatches:
            raise ValueError(
                f"semantic role compatibility mismatch for {requirement.role.name!r}: "
                f"{', '.join(mismatches)}"
            )


def _build_evidence_producer_bindings(
    producers: tuple[
        tuple[PluginAxis, VersionedIdentity, frozenset[VersionedIdentity]], ...
    ],
) -> tuple[EvidenceProducerBinding, ...]:
    by_evidence: dict[VersionedIdentity, EvidenceProducerBinding] = {}
    bindings: list[EvidenceProducerBinding] = []
    for axis, producer_identity, evidence_identities in producers:
        for evidence_identity in sorted(evidence_identities):
            binding = EvidenceProducerBinding(
                producer_axis=axis,
                producer_identity=producer_identity,
                evidence_identity=evidence_identity,
            )
            previous = by_evidence.get(evidence_identity)
            if previous is not None:
                raise ValueError(
                    "ambiguous canonical evidence producer for "
                    f"{evidence_identity.canonical_id!r}: "
                    f"{previous.producer_axis.value}:"
                    f"{previous.producer_identity.canonical_id}, "
                    f"{axis.value}:{producer_identity.canonical_id}"
                )
            by_evidence[evidence_identity] = binding
            bindings.append(binding)
    return tuple(bindings)


def compose_experiment(
    manifest: ExperimentPluginManifest,
    registries: ExperimentPluginRegistries,
) -> ResolvedExperimentComposition:
    """6軸を解決し、identity・parameter・role・routeを実行前に検証する。

    失敗はproviderのbind、scene生成、source start等のside effectより前に送出する。
    """

    robot = registries.robot_bundles.resolve(manifest.robot_bundle)
    environment = registries.environments.resolve(manifest.environment)
    mapping = registries.control_mappings.resolve(manifest.control_mapping)
    task = registries.tasks.resolve(manifest.task)
    input_source = registries.input_sources.resolve(manifest.input_source)
    evaluators = tuple(
        registries.evaluators.resolve(selection) for selection in manifest.evaluators
    )
    resolved_types = (
        (robot, RobotBundle, "Robot Bundle"),
        (environment, EnvironmentPlugin, "environment"),
        (mapping, ControlMappingPlugin, "control mapping"),
        (task, TaskPlugin, "task"),
        (input_source, InputSourcePlugin, "input source"),
        *((evaluator, EvaluationPlugin, "evaluation") for evaluator in evaluators),
    )
    for plugin, expected_type, registry_kind in resolved_types:
        if not isinstance(plugin, expected_type):
            raise ValueError(
                f"experiment registry-set type mismatch for {registry_kind}: "
                f"expected {expected_type.__name__}, got {type(plugin).__name__}"
            )

    selected_plugins = (
        (
            PluginParameterOwner(PluginAxis.ROBOT_BUNDLE, manifest.robot_bundle),
            robot,
        ),
        (
            PluginParameterOwner(PluginAxis.ENVIRONMENT, manifest.environment),
            environment,
        ),
        (
            PluginParameterOwner(
                PluginAxis.CONTROL_MAPPING, manifest.control_mapping
            ),
            mapping,
        ),
        (PluginParameterOwner(PluginAxis.TASK, manifest.task), task),
        (
            PluginParameterOwner(PluginAxis.INPUT_SOURCE, manifest.input_source),
            input_source,
        ),
        *(
            (
                PluginParameterOwner(PluginAxis.EVALUATION, selection),
                evaluator,
            )
            for selection, evaluator in zip(manifest.evaluators, evaluators, strict=True)
        ),
    )
    parameter_values = _parameters_by_owner(manifest)
    selected_owners = {owner for owner, _ in selected_plugins}
    unknown_parameter_owners = tuple(
        sorted(owner.canonical_id for owner in set(parameter_values) - selected_owners)
    )
    if unknown_parameter_owners:
        raise ValueError(
            f"parameters supplied for unselected plugins: {unknown_parameter_owners}"
        )
    for owner, plugin in selected_plugins:
        values = parameter_values.get(owner, {})
        if isinstance(plugin, ControlMappingPlugin):
            plugin.normalize_parameters(values)
        else:
            plugin.parameter_contract.validate(values)

    if not mapping.accepted_input_sample_schemas:
        raise ValueError(
            "control mapping must declare at least one accepted input sample schema"
        )
    effective_mapping_schema = input_source.effective_mapping_input_sample_schema
    if effective_mapping_schema not in mapping.accepted_input_sample_schemas:
        raise ValueError(
            "input sample schema compatibility mismatch: mapping input is "
            f"{effective_mapping_schema.canonical_id!r}, mapping accepts "
            f"{tuple(sorted(item.canonical_id for item in mapping.accepted_input_sample_schemas))!r}"
        )

    command_semantics_route = resolve_command_semantics_route(
        mapping,
        robot,
        manifest.command_semantics_route,
    )

    required_capabilities = (
        environment.required_robot_capabilities
        | mapping.required_robot_capabilities
        | task.required_robot_capabilities
    )
    for capability in required_capabilities:
        robot.provider(capability)

    robot_role_bindings = robot.semantic_role_bindings()
    robot_role_descriptors = tuple(
        EnvironmentRole(
            role=binding.role,
            object_kind=binding.object_kind,
            frame=binding.frame,
            unit=binding.unit,
        )
        for binding in robot_role_bindings
    )
    robot_roles = tuple(descriptor.role for descriptor in robot_role_descriptors)
    if len(robot_roles) != len(set(robot_roles)):
        raise ValueError("ambiguous robot semantic role provider")
    environment_roles = tuple(item.role for item in environment.roles)
    combined_roles = robot_roles + environment_roles
    if len(combined_roles) != len(set(combined_roles)):
        raise ValueError("ambiguous semantic role binding across robot and environment")
    resolved_role_descriptors = robot_role_descriptors + environment.roles
    descriptors_by_role = {
        descriptor.role: descriptor for descriptor in resolved_role_descriptors
    }
    resolved_roles = frozenset(descriptors_by_role)
    _validate_role_requirements(
        environment.required_robot_roles,
        {descriptor.role: descriptor for descriptor in robot_role_descriptors},
    )
    _validate_role_requirements(
        task.required_semantic_roles,
        descriptors_by_role,
    )

    _validate_compatibility(robot, environment, task)

    evidence_producers = _build_evidence_producer_bindings(
        (
            (PluginAxis.ROBOT_BUNDLE, robot.identity, robot.provided_evidence),
            (PluginAxis.ENVIRONMENT, environment.identity, environment.produced_evidence),
            (PluginAxis.CONTROL_MAPPING, mapping.identity, mapping.produced_evidence),
            (PluginAxis.TASK, task.identity, task.produced_evidence),
            (
                PluginAxis.INPUT_SOURCE,
                input_source.identity,
                input_source.produced_evidence,
            ),
        )
    )
    available_evidence = frozenset(
        binding.evidence_identity for binding in evidence_producers
    )
    for evaluator in evaluators:
        missing_evidence = tuple(
            sorted(
                identity.canonical_id
                for identity in evaluator.required_evidence
                if identity not in available_evidence
            )
        )
        if missing_evidence:
            raise ValueError(
                f"evaluator evidence requirement mismatch for "
                f"{evaluator.identity.canonical_id!r}: missing {missing_evidence}"
            )

    return ResolvedExperimentComposition(
        manifest=manifest,
        robot_bundle=robot,
        environment=environment,
        control_mapping=mapping,
        task=task,
        input_source=input_source,
        resolved_input_sample_schema=input_source.produced_sample_schema,
        resolved_mapping_input_sample_schema=effective_mapping_schema,
        resolved_command_semantics_route=command_semantics_route,
        evaluators=evaluators,
        resolved_capabilities=robot.provided_capabilities,
        resolved_roles=resolved_roles,
        resolved_role_descriptors=resolved_role_descriptors,
        available_evidence=available_evidence,
        evidence_producers=evidence_producers,
    )


__all__ = [
    "ExperimentPluginManifest",
    "ExperimentPluginRegistries",
    "EvidenceProducerBinding",
    "PluginParameters",
    "freeze_parameter_value",
    "parameter_value_to_document",
    "resolve_command_execution",
    "resolve_command_semantics_route",
    "ResolvedExperimentComposition",
    "compose_experiment",
]
