"""実験compositionの6軸を接続前に検証・freezeするversioned contract。

このmoduleはproduction runnerを起動せず、plugin identity、parameter owner、
role、evidence、command routeの整合だけをside effect前に確定する。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from xpotato_sim.schemas import EndpointVelocityCommand, JointPositionCommand


@dataclass(frozen=True, slots=True, order=True)
class VersionedIdentity:
    """catalog間で比較する論理名と正のcontract versionの組。"""

    name: str
    version: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("versioned identity name must not be empty")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("versioned identity version must be positive")

    @property
    def canonical_id(self) -> str:
        return f"{self.name}/v{self.version}"


ENDPOINT_POSITION_COMMAND_V1 = VersionedIdentity("endpoint_position_command", 1)
ENDPOINT_VELOCITY_COMMAND_V1 = VersionedIdentity("endpoint_velocity_command", 1)
JOINT_POSITION_COMMAND_V1 = VersionedIdentity("joint_position_command", 1)
JOINT_VELOCITY_COMMAND_V1 = VersionedIdentity("joint_velocity_command", 1)
LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1 = VersionedIdentity(
    "local_endpoint_velocity_to_joint_position", 1
)
ENDPOINT_DELTA_TO_JOINT_POSITION_V1 = VersionedIdentity(
    "endpoint_delta_to_joint_position", 1
)
REPLAY_COMMAND_TO_JOINT_POSITION_V1 = VersionedIdentity(
    "replay_command_to_joint_position", 1
)
NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1 = VersionedIdentity(
    "native_endpoint_velocity_passthrough", 1
)


@dataclass(frozen=True, slots=True)
class RobotCommandSemanticContract:
    """Robot command semanticを実行時のtyped command classへ対応付ける契約。"""

    identity: VersionedIdentity
    command_type: type

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VersionedIdentity):
            raise TypeError(
                "Robot command semantic contract identity must use VersionedIdentity"
            )
        if not isinstance(self.command_type, type):
            raise TypeError(
                "Robot command semantic contract command_type must be a type"
            )


ROBOT_COMMAND_SEMANTIC_CONTRACTS: Mapping[
    VersionedIdentity, RobotCommandSemanticContract
] = MappingProxyType(
    {
        JOINT_POSITION_COMMAND_V1: RobotCommandSemanticContract(
            JOINT_POSITION_COMMAND_V1,
            JointPositionCommand,
        ),
        ENDPOINT_VELOCITY_COMMAND_V1: RobotCommandSemanticContract(
            ENDPOINT_VELOCITY_COMMAND_V1,
            EndpointVelocityCommand,
        ),
    }
)


def robot_command_semantic_contract(
    identity: VersionedIdentity,
) -> RobotCommandSemanticContract:
    """既知semanticのtyped contractを返し、未対応identityはfail closedにする。"""

    if not isinstance(identity, VersionedIdentity):
        raise TypeError(
            "Robot command semantic contract lookup requires VersionedIdentity"
        )
    contract = ROBOT_COMMAND_SEMANTIC_CONTRACTS.get(identity)
    if contract is None:
        raise ValueError(
            "Robot command semantic has no executable typed command contract: "
            f"{identity.canonical_id!r}"
        )
    return contract


@runtime_checkable
class CommandRouteExecutionStrategy(Protocol):
    """選択済みrouteをproviderへbindするstrategyのidentity整合契約。"""

    route_identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    command_type: type

    def bind(self, provider: object) -> object: ...


@dataclass(frozen=True, slots=True, order=True)
class CommandSemanticsRoute:
    """Versioned control-to-backend command condition selected for composition."""

    identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    execution_strategy: CommandRouteExecutionStrategy = field(
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for name, value in (
            ("identity", self.identity),
            ("control_semantics_identity", self.control_semantics_identity),
            ("robot_command_semantics_identity", self.robot_command_semantics_identity),
        ):
            if not isinstance(value, VersionedIdentity):
                raise TypeError(f"command semantics route {name} must use VersionedIdentity")
        if not isinstance(self.execution_strategy, CommandRouteExecutionStrategy):
            raise TypeError(
                "command semantics route requires a typed execution strategy"
            )
        if (
            self.execution_strategy.route_identity != self.identity
            or self.execution_strategy.control_semantics_identity
            != self.control_semantics_identity
            or self.execution_strategy.robot_command_semantics_identity
            != self.robot_command_semantics_identity
        ):
            raise ValueError(
                "command semantics route/execution strategy identity mismatch"
            )
        semantic_contract = robot_command_semantic_contract(
            self.robot_command_semantics_identity
        )
        if self.execution_strategy.command_type is not semantic_contract.command_type:
            raise TypeError(
                "command semantics route/execution strategy command type mismatch"
            )


@dataclass(frozen=True, slots=True)
class PluginSelection:
    """1軸で要求されたplugin logical identityとcontract version。"""

    plugin_id: str
    contract_version: int

    def __post_init__(self) -> None:
        if not self.plugin_id:
            raise ValueError("plugin_id must not be empty")
        if type(self.contract_version) is not int or self.contract_version < 1:
            raise ValueError("contract_version must be positive")


class PluginAxis(str, Enum):
    """generic experiment compositionがfreezeする6つのownership軸。"""

    ROBOT_BUNDLE = "robot_bundle"
    ENVIRONMENT = "environment"
    CONTROL_MAPPING = "control_mapping"
    TASK = "task"
    EVALUATION = "evaluation"
    INPUT_SOURCE = "input_source"


@dataclass(frozen=True, slots=True)
class PluginParameterOwner:
    """parameter namespaceの所有axisとplugin selectionを表す。"""

    axis: PluginAxis
    selection: PluginSelection

    def __post_init__(self) -> None:
        if not isinstance(self.axis, PluginAxis):
            raise TypeError("plugin parameter owner axis must use PluginAxis")
        if not isinstance(self.selection, PluginSelection):
            raise TypeError("plugin parameter owner selection must use PluginSelection")

    @property
    def canonical_id(self) -> str:
        return (
            f"{self.axis.value}:{self.selection.plugin_id}"
            f"/v{self.selection.contract_version}"
        )


class EvidenceStatus(str, Enum):
    """canonical evidenceの生成段階または利用不能状態。"""

    REQUESTED = "requested"
    RESOLVED = "resolved"
    PREDICTED = "predicted"
    MEASURED = "measured"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CanonicalEvidence:
    """provenance付きevidence値とfailure semanticsを保持する契約。"""

    identity: VersionedIdentity
    status: EvidenceStatus
    value: object | None
    provenance: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.provenance:
            raise ValueError("canonical evidence provenance must not be empty")
        if self.status in (EvidenceStatus.UNAVAILABLE, EvidenceStatus.INVALID):
            if self.value is not None:
                raise ValueError(f"{self.status.value} evidence must not carry a value")
            if not self.reason:
                raise ValueError(f"{self.status.value} evidence requires a reason")
        elif self.status in (
            EvidenceStatus.RESOLVED,
            EvidenceStatus.PREDICTED,
            EvidenceStatus.MEASURED,
        ) and self.value is None:
            raise ValueError(f"{self.status.value} evidence requires a value")


class CanonicalEvidenceSet:
    """versioned identityごとに重複を拒否するread-only evidence lookup。"""

    def __init__(self, entries: tuple[CanonicalEvidence, ...]) -> None:
        values: dict[VersionedIdentity, CanonicalEvidence] = {}
        for entry in entries:
            if entry.identity in values:
                raise ValueError(
                    f"duplicate canonical evidence: {entry.identity.canonical_id!r}"
                )
            values[entry.identity] = entry
        self._values = values

    @property
    def identities(self) -> frozenset[VersionedIdentity]:
        return frozenset(self._values)

    def require(self, identity: VersionedIdentity) -> CanonicalEvidence:
        try:
            return self._values[identity]
        except KeyError as exc:
            raise ValueError(
                f"missing canonical evidence {identity.canonical_id!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class ParameterField:
    """plugin parameterの型・必須性・実験条件帰属を宣言する。"""

    name: str
    value_type: type
    required: bool = True
    condition_specific: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("parameter field name must not be empty")
        if type(self.required) is not bool:
            raise TypeError("parameter field required must be a bool")
        if type(self.condition_specific) is not bool:
            raise TypeError("parameter field condition_specific must be a bool")


@dataclass(frozen=True, slots=True)
class ParameterContract:
    """未知・不足・型不一致をcomposition時に拒否するparameter schema。"""

    fields: tuple[ParameterField, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fields, tuple):
            object.__setattr__(self, "fields", tuple(self.fields))
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("parameter contract field names must be unique")

    def validate(self, parameters: Mapping[str, object]) -> None:
        declared = {field.name: field for field in self.fields}
        unknown = tuple(sorted(set(parameters) - set(declared)))
        if unknown:
            raise ValueError(f"unknown plugin parameters: {unknown}")
        missing = tuple(
            field.name
            for field in self.fields
            if field.required and field.name not in parameters
        )
        if missing:
            raise ValueError(f"missing required plugin parameters: {missing}")
        for name, value in parameters.items():
            expected = declared[name].value_type
            if expected in (int, float) and isinstance(value, bool):
                raise ValueError(
                    f"plugin parameter {name!r} must be {expected.__name__}, "
                    "not bool"
                )
            if not _parameter_value_matches(expected, value):
                raise ValueError(
                    f"plugin parameter {name!r} must be {expected.__name__}, "
                    f"got {type(value).__name__}"
                )

    @property
    def condition_specific_field_names(self) -> frozenset[str]:
        return frozenset(
            field.name for field in self.fields if field.condition_specific
        )


def _parameter_value_matches(expected: type, value: object) -> bool:
    """Match frozen recursive JSON values against the generic parameter contract."""

    if expected is list:
        return isinstance(value, tuple)
    if expected is tuple:
        return isinstance(value, tuple)
    if expected is dict:
        return isinstance(value, Mapping)
    if expected is Mapping:
        return isinstance(value, Mapping)
    return isinstance(value, expected)


@dataclass(frozen=True, slots=True, order=True)
class SemanticRole:
    """environmentとRobot間で共有するversion非依存のsemantic role名。"""

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("semantic role name must not be empty")


@dataclass(frozen=True, slots=True)
class EnvironmentRole:
    """environment objectのkind、coordinate frame、unitを束ねるrole記述。"""

    role: SemanticRole
    object_kind: str
    frame: str
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, SemanticRole):
            raise TypeError("environment role must use SemanticRole")
        if not self.object_kind or not self.frame or not self.unit:
            raise ValueError("environment role kind, frame, and unit must not be empty")


ROLE_ATTRIBUTE_WILDCARD = "*"


@dataclass(frozen=True, slots=True, order=True)
class SemanticRoleRequirement:
    """Robot側へ要求するrole属性。明示的な ``*`` だけをwildcardとする。"""

    role: SemanticRole
    object_kind: str
    frame: str
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, SemanticRole):
            raise TypeError("semantic role requirement must use SemanticRole")
        if not self.object_kind or not self.frame or not self.unit:
            raise ValueError(
                "semantic role requirement kind, frame, and unit must not be empty; "
                "use '*' as an explicit wildcard"
            )

    def matches(self, descriptor: EnvironmentRole) -> bool:
        return self.role == descriptor.role and all(
            required == ROLE_ATTRIBUTE_WILDCARD or required == actual
            for required, actual in (
                (self.object_kind, descriptor.object_kind),
                (self.frame, descriptor.frame),
                (self.unit, descriptor.unit),
            )
        )


@runtime_checkable
class EnvironmentSceneProvider(Protocol):
    """sceneの生成とresetをenvironment ownerへ委譲するlifecycle境界。"""

    def compose_scene(self, parameters: Mapping[str, object]) -> object: ...

    def reset_scene(self, scene: object) -> None: ...


@runtime_checkable
class ControlMappingStrategy(Protocol):
    """Input Intentをcontrol semanticへ写像するMapping戦略境界。"""

    def map_input(self, input_intent: object, parameters: Mapping[str, object]) -> object: ...


class TaskTerminalClassification(str, Enum):
    """Taskが返す継続・成功・失敗・技術的無効の終端分類。"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TECHNICAL_INVALID = "technical_invalid"


@dataclass(frozen=True, slots=True)
class TaskTransition:
    """Task-owned transition後のstate、分類、canonical evidence。"""

    state: object
    classification: TaskTerminalClassification
    evidence: CanonicalEvidenceSet

    def __post_init__(self) -> None:
        if not isinstance(self.classification, TaskTerminalClassification):
            raise TypeError("task transition classification must be typed")
        if not isinstance(self.evidence, CanonicalEvidenceSet):
            raise TypeError("task transition evidence must use CanonicalEvidenceSet")


@runtime_checkable
class TaskExecutionBinding(Protocol):
    """frozen task contextへbind済みのpure transition境界。"""

    def initial_state(self) -> object: ...

    def advance(self, state: object, observation: object) -> TaskTransition: ...


@runtime_checkable
class TaskLifecycleStrategy(Protocol):
    """Task contextのbindと初期state生成を所有するlifecycle契約。"""

    def initial_state(self, parameters: Mapping[str, object]) -> object: ...

    def bind_context(
        self,
        context: object,
        parameters: Mapping[str, object],
    ) -> TaskExecutionBinding: ...


class EvidenceDisposition(str, Enum):
    """欠落・unavailable・invalid evidenceに対するmetric側の扱い。"""

    REJECT = "reject"
    PRODUCE_UNAVAILABLE = "produce_unavailable"
    PRODUCE_INVALID = "produce_invalid"


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """evidence failure categoryごとのfail-closed方針。"""

    missing: EvidenceDisposition = EvidenceDisposition.REJECT
    unavailable: EvidenceDisposition = EvidenceDisposition.REJECT
    invalid: EvidenceDisposition = EvidenceDisposition.REJECT


@dataclass(frozen=True, slots=True)
class MetricResult:
    """metric valueまたは理由付き非値をprovenanceとともに返す契約。"""

    metric_id: VersionedIdentity
    value: object | None
    status: EvidenceStatus
    provenance: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.provenance:
            raise ValueError("metric result provenance must not be empty")
        if self.status in (EvidenceStatus.UNAVAILABLE, EvidenceStatus.INVALID):
            if self.value is not None:
                raise ValueError(f"{self.status.value} metric must not carry a value")
            if not self.reason:
                raise ValueError(f"{self.status.value} metric requires a reason")
        elif self.value is None:
            raise ValueError(f"{self.status.value} metric requires a value")


@runtime_checkable
class MetricDerivationStrategy(Protocol):
    """frozen evidenceからmetricを導出し、runtimeを駆動しない評価境界。"""

    def derive(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
        *,
        provenance: str,
    ) -> MetricResult: ...


@dataclass(frozen=True, slots=True)
class EnvironmentPlugin:
    """scene、role、capability requirementを所有するEnvironment宣言。"""

    identity: VersionedIdentity
    scene_provider: EnvironmentSceneProvider
    roles: tuple[EnvironmentRole, ...]
    required_robot_capabilities: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    required_robot_roles: frozenset[SemanticRoleRequirement] = field(default_factory=frozenset)
    parameter_contract: ParameterContract = ParameterContract()
    produced_evidence: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    backend_scene_owner: str = "runtime"
    viewer_presentation_reference: str | None = None
    compatible_robot_bundles: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    compatible_backend_kinds: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.scene_provider, EnvironmentSceneProvider):
            raise TypeError("environment plugin requires a typed scene provider")
        if self.backend_scene_owner != "runtime":
            raise ValueError("environment backend scene composition owner must be runtime")
        roles = tuple(item.role for item in self.roles)
        if len(roles) != len(set(roles)):
            raise ValueError("environment plugin roles must be unique")
        if any(
            not isinstance(requirement, SemanticRoleRequirement)
            for requirement in self.required_robot_roles
        ):
            raise TypeError(
                "environment required robot roles must use SemanticRoleRequirement"
            )
        if any(
            not isinstance(identity, VersionedIdentity)
            for identity in self.compatible_robot_bundles
        ):
            raise TypeError(
                "environment compatible Robot Bundles must use VersionedIdentity"
            )


@dataclass(frozen=True, slots=True)
class ControlMappingPlugin:
    """Input Intent、command semantic、parameter contractを結ぶMapping宣言。

    route declarationを所有するが、provider binding後のexecution lifecycleは
    ``CommandRouteExecutionStrategy`` が所有する。
    """

    identity: VersionedIdentity
    strategy: ControlMappingStrategy
    accepted_input_sample_schemas: frozenset[VersionedIdentity]
    required_robot_capabilities: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    parameter_contract: ParameterContract = ParameterContract()
    produced_evidence: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    control_frame: str | None = None
    comparison_family_identity: VersionedIdentity | None = None
    mapping_semantics_identity: VersionedIdentity | None = None
    command_semantics_routes: frozenset[CommandSemanticsRoute] = field(
        default_factory=frozenset
    )
    parameter_normalizer: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None
    runtime_context_parameters: frozenset[str] = frozenset()
    runtime_parameter_normalizer: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None
    session_strategy_factory: Callable[[], ControlMappingStrategy] | None = None

    def __post_init__(self) -> None:
        if self.session_strategy_factory is not None and not callable(self.session_strategy_factory):
            raise TypeError("session_strategy_factory must be callable")
        if not isinstance(self.strategy, ControlMappingStrategy):
            raise TypeError("control mapping plugin requires a typed mapping strategy")
        schemas = frozenset(self.accepted_input_sample_schemas)
        if any(not isinstance(identity, VersionedIdentity) for identity in schemas):
            raise TypeError(
                "control mapping accepted input sample schemas must use VersionedIdentity"
            )
        object.__setattr__(self, "accepted_input_sample_schemas", schemas)
        if self.control_frame is not None and self.control_frame not in {"world", "tool"}:
            raise ValueError("control mapping control_frame must be 'world' or 'tool'")
        for name, identity in (
            ("comparison_family_identity", self.comparison_family_identity),
            ("mapping_semantics_identity", self.mapping_semantics_identity),
        ):
            if identity is not None and not isinstance(identity, VersionedIdentity):
                raise TypeError(f"{name} must use VersionedIdentity")
        if self.mapping_semantics_identity is not None:
            strategy_identity = getattr(self.strategy, "mapping_semantics_identity", None)
            if strategy_identity != self.mapping_semantics_identity:
                raise ValueError(
                    "mapping strategy semantic identity mismatch: "
                    "strategy and plugin must declare the same VersionedIdentity"
                )
        routes = frozenset(self.command_semantics_routes)
        if not routes:
            raise ValueError(
                "control mapping must declare at least one command semantics route"
            )
        route_identities = tuple(route.identity for route in routes)
        if len(route_identities) != len(set(route_identities)):
            raise ValueError("duplicate control mapping command semantics route identity")
        if self.mapping_semantics_identity is None:
            raise ValueError(
                "control mapping command semantics routes require mapping_semantics_identity"
            )
        for route in routes:
            if not isinstance(route, CommandSemanticsRoute):
                raise TypeError(
                    "control mapping command semantics routes must use CommandSemanticsRoute"
                )
            if route.control_semantics_identity != self.mapping_semantics_identity:
                raise ValueError(
                    "control mapping command route/control semantics identity mismatch"
                )
        object.__setattr__(self, "command_semantics_routes", routes)
        if self.parameter_normalizer is not None and not callable(self.parameter_normalizer):
            raise TypeError("control mapping parameter_normalizer must be callable")
        if isinstance(self.runtime_context_parameters, (str, bytes)):
            raise TypeError("runtime context parameters must be a collection of field names")
        context = frozenset(self.runtime_context_parameters)
        if any(not isinstance(name, str) or not name for name in context):
            raise TypeError("runtime context parameters must contain non-empty field names")
        if not context.issubset({item.name for item in self.parameter_contract.fields}):
            raise ValueError("runtime context must name declared mapping parameters")
        object.__setattr__(self, "runtime_context_parameters", context)
        if self.runtime_parameter_normalizer is not None and not callable(self.runtime_parameter_normalizer):
            raise TypeError("runtime_parameter_normalizer must be callable")

    def create_session_strategy(self) -> ControlMappingStrategy:
        """runtime単位のMappingを生成し、catalogのsingletonへ可変状態を残さない。"""
        if self.session_strategy_factory is None:
            return self.strategy
        strategy = self.session_strategy_factory()
        if strategy is self.strategy or not isinstance(strategy, ControlMappingStrategy):
            raise TypeError("mapping session factory must return a separate typed strategy")
        if getattr(strategy, "mapping_semantics_identity", None) != self.mapping_semantics_identity:
            raise ValueError("mapping session semantic identity mismatch")
        return strategy

    def resolve_command_semantics_route(
        self,
        identity: VersionedIdentity | None = None,
    ) -> CommandSemanticsRoute:
        if identity is None:
            if len(self.command_semantics_routes) != 1:
                raise ValueError(
                    "control mapping command semantics route selection is required"
                )
            return next(iter(self.command_semantics_routes))
        if not isinstance(identity, VersionedIdentity):
            raise TypeError("command semantics route selection must use VersionedIdentity")
        matches = tuple(
            route for route in self.command_semantics_routes if route.identity == identity
        )
        if not matches:
            supported = tuple(
                item.identity.canonical_id
                for item in sorted(self.command_semantics_routes)
            )
            raise ValueError(
                f"unsupported command semantics route {identity.canonical_id!r}; "
                f"supported={supported}"
            )
        return matches[0]

    def normalize_parameters(
        self,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Validate and deterministically normalize mapping parameters once."""

        if not isinstance(parameters, Mapping):
            raise TypeError("control mapping parameters must use a mapping")
        self.parameter_contract.validate(parameters)
        normalized = (
            self.parameter_normalizer(parameters)
            if self.parameter_normalizer is not None
            else dict(parameters)
        )
        if not isinstance(normalized, Mapping):
            raise TypeError("control mapping parameter_normalizer must return a mapping")
        self.parameter_contract.validate(normalized)
        return MappingProxyType(dict(sorted(normalized.items())))

    def normalize_runtime_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        """宣言された観測contextだけを後段供給可能にし、固定configは通常どおり検証する。"""
        if not self.runtime_context_parameters:
            return self.normalize_parameters(parameters)
        if not isinstance(parameters, Mapping):
            raise TypeError("control mapping parameters must use a mapping")
        contract = ParameterContract(tuple(
            replace(item, required=False) if item.name in self.runtime_context_parameters else item
            for item in self.parameter_contract.fields
        ))
        contract.validate(parameters)
        normalizer = self.runtime_parameter_normalizer or self.parameter_normalizer
        normalized = dict(parameters) if normalizer is None else normalizer(parameters)
        if not isinstance(normalized, Mapping):
            raise TypeError("runtime parameter normalizer must return a mapping")
        contract.validate(normalized)
        return MappingProxyType(dict(sorted(normalized.items())))

    def resolve_control_frame(self, parameters: Mapping[str, object]) -> str | None:
        """Resolve the evaluation control frame without executing the mapping.

        A static declaration remains authoritative when present.  A mapping whose
        frame is an explicit experiment condition may instead expose the
        condition-owned ``control_frame`` parameter.
        """

        self.parameter_contract.validate(parameters)
        parameter_frame = parameters.get("control_frame")
        if parameter_frame is not None:
            if not isinstance(parameter_frame, str) or parameter_frame not in {
                "world",
                "tool",
            }:
                raise ValueError("control_frame parameter must be 'world' or 'tool'")
            if self.control_frame is not None and self.control_frame != parameter_frame:
                raise ValueError(
                    "control mapping static/parameter control frame mismatch"
                )
            return parameter_frame
        return self.control_frame


@dataclass(frozen=True, slots=True)
class TaskPlugin:
    """task lifecycleとrequired evidence/roleを宣言するTask contract。"""

    identity: VersionedIdentity
    lifecycle: TaskLifecycleStrategy
    required_robot_capabilities: frozenset[VersionedIdentity]
    required_semantic_roles: frozenset[SemanticRoleRequirement]
    parameter_contract: ParameterContract
    task_event_identity: VersionedIdentity
    produced_evidence: frozenset[VersionedIdentity]
    compatible_robot_bundles: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    compatible_environments: frozenset[VersionedIdentity] = field(default_factory=frozenset)
    compatible_backend_kinds: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, TaskLifecycleStrategy):
            raise TypeError("task plugin requires a typed lifecycle strategy")
        if self.task_event_identity not in self.produced_evidence:
            raise ValueError("task event identity must be declared as produced evidence")
        if any(
            not isinstance(requirement, SemanticRoleRequirement)
            for requirement in self.required_semantic_roles
        ):
            raise TypeError("task required roles must use SemanticRoleRequirement")
        if any(
            not isinstance(identity, VersionedIdentity)
            for identity in self.compatible_robot_bundles
        ):
            raise TypeError("task compatible Robot Bundles must use VersionedIdentity")
        if any(
            not isinstance(identity, VersionedIdentity)
            for identity in self.compatible_environments
        ):
            raise TypeError("task compatible environments must use VersionedIdentity")

    def bind_context(
        self,
        context: object,
        parameters: Mapping[str, object],
    ) -> TaskExecutionBinding:
        """parameterを検証し、runner用のimmutable execution bindingを返す。"""

        self.parameter_contract.validate(parameters)
        binding = self.lifecycle.bind_context(context, parameters)
        if not isinstance(binding, TaskExecutionBinding):
            raise TypeError("task lifecycle returned an invalid execution binding")
        return binding


@dataclass(frozen=True, slots=True)
class EvaluationPlugin:
    """evidence policyとmetric derivationを所有するEvaluation contract。"""

    identity: VersionedIdentity
    metric_deriver: MetricDerivationStrategy
    required_evidence: frozenset[VersionedIdentity]
    evidence_policy: EvidencePolicy
    parameter_contract: ParameterContract
    provenance: str
    unit: str = "dimensionless"
    frame: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric_deriver, MetricDerivationStrategy):
            raise TypeError("evaluation plugin requires a typed metric derivation strategy")
        if not self.provenance:
            raise ValueError("evaluation plugin provenance must not be empty")
        if not self.unit:
            raise ValueError("evaluation plugin unit must not be empty")
        if self.frame is not None and not self.frame:
            raise ValueError("evaluation plugin frame must be non-empty or None")

    def derive_metric(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
    ) -> MetricResult:
        self.parameter_contract.validate(parameters)
        for identity in self.required_evidence:
            if identity not in evidence.identities:
                if self.evidence_policy.missing is EvidenceDisposition.REJECT:
                    raise ValueError(
                        f"missing evaluator evidence {identity.canonical_id!r}"
                    )
                status = (
                    EvidenceStatus.UNAVAILABLE
                    if self.evidence_policy.missing
                    is EvidenceDisposition.PRODUCE_UNAVAILABLE
                    else EvidenceStatus.INVALID
                )
                return MetricResult(
                    metric_id=self.identity,
                    value=None,
                    status=status,
                    provenance=self.provenance,
                    reason=f"missing required evidence {identity.canonical_id}",
                )
            entry = evidence.require(identity)
            if entry.status is EvidenceStatus.UNAVAILABLE:
                if self.evidence_policy.unavailable is EvidenceDisposition.REJECT:
                    raise ValueError(
                        f"unavailable evaluator evidence {identity.canonical_id!r}"
                    )
                return MetricResult(
                    metric_id=self.identity,
                    value=None,
                    status=(
                        EvidenceStatus.UNAVAILABLE
                        if self.evidence_policy.unavailable
                        is EvidenceDisposition.PRODUCE_UNAVAILABLE
                        else EvidenceStatus.INVALID
                    ),
                    provenance=self.provenance,
                    reason=f"required evidence unavailable: {identity.canonical_id}",
                )
            elif entry.status is EvidenceStatus.INVALID:
                if self.evidence_policy.invalid is EvidenceDisposition.REJECT:
                    raise ValueError(f"invalid evaluator evidence {identity.canonical_id!r}")
                return MetricResult(
                    metric_id=self.identity,
                    value=None,
                    status=(
                        EvidenceStatus.UNAVAILABLE
                        if self.evidence_policy.invalid
                        is EvidenceDisposition.PRODUCE_UNAVAILABLE
                        else EvidenceStatus.INVALID
                    ),
                    provenance=self.provenance,
                    reason=f"required evidence invalid: {identity.canonical_id}",
                )
        result = self.metric_deriver.derive(
            evidence,
            parameters,
            provenance=self.provenance,
        )
        if not isinstance(result, MetricResult):
            raise TypeError("evaluation strategy must return MetricResult")
        if result.metric_id != self.identity:
            raise ValueError(
                "evaluation metric identity mismatch: "
                f"expected {self.identity.canonical_id!r}, "
                f"got {result.metric_id.canonical_id!r}"
            )
        if result.provenance != self.provenance:
            raise ValueError(
                "evaluation metric provenance mismatch: "
                f"expected {self.provenance!r}, got {result.provenance!r}"
            )
        return result


__all__ = [
    "CanonicalEvidence",
    "CanonicalEvidenceSet",
    "CommandSemanticsRoute",
    "CommandRouteExecutionStrategy",
    "ControlMappingPlugin",
    "ControlMappingStrategy",
    "EnvironmentPlugin",
    "EnvironmentRole",
    "EnvironmentSceneProvider",
    "EvaluationPlugin",
    "EvidenceDisposition",
    "EvidencePolicy",
    "EvidenceStatus",
    "ENDPOINT_POSITION_COMMAND_V1",
    "ENDPOINT_VELOCITY_COMMAND_V1",
    "ENDPOINT_DELTA_TO_JOINT_POSITION_V1",
    "JOINT_POSITION_COMMAND_V1",
    "JOINT_VELOCITY_COMMAND_V1",
    "LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1",
    "MetricDerivationStrategy",
    "MetricResult",
    "NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1",
    "REPLAY_COMMAND_TO_JOINT_POSITION_V1",
    "ROBOT_COMMAND_SEMANTIC_CONTRACTS",
    "RobotCommandSemanticContract",
    "robot_command_semantic_contract",
    "ParameterContract",
    "ParameterField",
    "PluginAxis",
    "PluginParameterOwner",
    "PluginSelection",
    "ROLE_ATTRIBUTE_WILDCARD",
    "SemanticRole",
    "SemanticRoleRequirement",
    "TaskExecutionBinding",
    "TaskLifecycleStrategy",
    "TaskPlugin",
    "TaskTransition",
    "TaskTerminalClassification",
    "VersionedIdentity",
]
