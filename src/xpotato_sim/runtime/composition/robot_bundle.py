"""Robot logical identityからtyped capability providerを組み立てる契約。

generic Bundleのvalidation/assemblyを所有し、production Robotのcatalog selection
やruntime lifecycle開始は所有しない。validation failureはprovider side effect前に返す。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from xpotato_sim.motion.base import MotionGenerator
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    ParameterContract,
    SemanticRole,
    VersionedIdentity,
    robot_command_semantic_contract,
)
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityGuard
from xpotato_sim.runtime.composition.robot_plugin import RobotRuntimePlugin
from xpotato_sim.runtime.composition.robot_resolution import (
    validate_robot_profile_plugin_consistency,
)
from xpotato_sim.schemas import MuJoCoState, RobotCommand


RESET_INITIAL_STATE_V1 = VersionedIdentity("reset_initial_state", 1)
ENDPOINT_POSE_V1 = VersionedIdentity("endpoint_pose", 1)
ENDPOINT_COMMAND_V1 = VersionedIdentity("endpoint_command", 1)
QPOS_FEASIBILITY_V1 = VersionedIdentity("qpos_feasibility", 1)
SCENE_ROLE_BINDING_V1 = VersionedIdentity("scene_role_binding", 1)
CONTACT_EVIDENCE_V1 = VersionedIdentity("contact_evidence", 1)

ROBOT_TOOL_ENDPOINT_ROLE = SemanticRole("robot.tool_endpoint")


@dataclass(frozen=True, slots=True)
class InitialStateReference:
    """初期姿勢のlogical identityとRobot-owned qpos orderingを固定する参照。"""

    source_kind: str
    source_id: str

    def __post_init__(self) -> None:
        if not self.source_kind or not self.source_id:
            raise ValueError("initial state source kind and ID must not be empty")


@dataclass(frozen=True, slots=True)
class InitialStateContract:
    """Versioned, model-free canonical values for one Robot Bundle initial state."""

    identity: VersionedIdentity
    source_kind: str
    source_id: str
    qpos_rad: tuple[float, ...]
    tip_position_m: tuple[float, float, float]
    tool_orientation_wxyz: tuple[float, float, float, float]
    frame: str
    position_unit: str
    orientation_unit: str
    quaternion_order: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VersionedIdentity):
            raise TypeError("initial state contract identity must use VersionedIdentity")
        for name, value in (
            ("source_kind", self.source_kind),
            ("source_id", self.source_id),
            ("frame", self.frame),
            ("position_unit", self.position_unit),
            ("orientation_unit", self.orientation_unit),
            ("quaternion_order", self.quaternion_order),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"initial state contract {name} must not be empty")

        def finite_tuple(name: str, value: object, length: int | None = None) -> tuple[float, ...]:
            if not isinstance(value, (tuple, list)):
                raise TypeError(f"initial state contract {name} must use a tuple")
            if length is not None and len(value) != length:
                raise ValueError(
                    f"initial state contract {name} must contain exactly {length} values"
                )
            result: list[float] = []
            for index, component in enumerate(value):
                if isinstance(component, bool) or not isinstance(component, (int, float)):
                    raise TypeError(
                        f"initial state contract {name}[{index}] must be numeric"
                    )
                number = float(component)
                if not math.isfinite(number):
                    raise ValueError(
                        f"initial state contract {name}[{index}] must be finite"
                    )
                result.append(0.0 if number == 0.0 else number)
            if not result:
                raise ValueError(f"initial state contract {name} must not be empty")
            return tuple(result)

        qpos = finite_tuple("qpos_rad", self.qpos_rad)
        tip = finite_tuple("tip_position_m", self.tip_position_m, length=3)
        orientation = finite_tuple(
            "tool_orientation_wxyz", self.tool_orientation_wxyz, length=4
        )
        norm = math.sqrt(sum(component * component for component in orientation))
        if abs(norm - 1.0) > 1e-12:
            raise ValueError("initial state contract tool orientation must be a unit quaternion")
        object.__setattr__(self, "qpos_rad", qpos)
        object.__setattr__(self, "tip_position_m", tip)  # type: ignore[arg-type]
        object.__setattr__(self, "tool_orientation_wxyz", orientation)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class EndpointPoseObservation:
    """Robot providerが観測したworld frameのendpoint pose。位置unitはm。"""

    position_m: tuple[float, float, float] | None
    quaternion_wxyz: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class SemanticRoleBinding:
    """environment semantic roleをRobot-owned model objectへ束縛する宣言。"""

    role: SemanticRole
    backend_kind: str
    target_kind: str
    target_id: str
    object_kind: str
    frame: str
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, SemanticRole):
            raise TypeError("semantic role binding must use SemanticRole")
        if any(
            not value
            for value in (
                self.backend_kind,
                self.target_kind,
                self.target_id,
                self.object_kind,
                self.frame,
                self.unit,
            )
        ):
            raise ValueError("semantic role binding fields must not be empty")


@runtime_checkable
class ResetInitialStateProvider(Protocol):
    """reset時の初期state参照を提供するlifecycle capability。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    def resolve_initial_state(self) -> InitialStateReference: ...


@runtime_checkable
class InitialStateContractProvider(Protocol):
    """初期qpos contractを名前解決するprovider境界。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    def initial_state_contract(self) -> InitialStateContract: ...


@runtime_checkable
class EndpointPoseProvider(Protocol):
    """simulation stateからendpoint poseを観測するRobot-owned境界。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    def observe_endpoint_pose(self, state: MuJoCoState) -> EndpointPoseObservation: ...


@runtime_checkable
class EndpointCommandProvider(Protocol):
    """endpoint control intentをtyped Robot commandへ変換するprovider境界。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    def build_target_motion_generator(
        self,
        *,
        seed_joint_angles_rad: tuple[float, ...] | None,
        discontinuity_threshold_rad: float | None,
        discontinuity_threshold_label: str,
    ) -> MotionGenerator: ...

    def build_local_endpoint_motion_generator(self) -> MotionGenerator: ...


@runtime_checkable
class QposFeasibilityProvider(Protocol):
    """候補qposをside effectなしにaccept/rejectする安全境界。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    def build_guard(
        self, *, model: object, config_path: str | Path | None
    ) -> QposFeasibilityGuard: ...


@runtime_checkable
class SceneRoleBindingProvider(Protocol):
    """semantic roleをRobot model内のcanonical objectへ解決する境界。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    def semantic_role_bindings(self) -> tuple[SemanticRoleBinding, ...]: ...


@runtime_checkable
class ContactEvidenceProvider(Protocol):
    """runtime stateからcontact evidenceを抽出するRobot-owned境界。"""

    capability_identity: VersionedIdentity
    assembly_binding: ProviderAssemblyBinding

    @property
    def evidence_identities(self) -> frozenset[VersionedIdentity]: ...

    def observe_contact_evidence(
        self, state: MuJoCoState
    ) -> tuple[CanonicalEvidence, ...]: ...


@runtime_checkable
class RobotCommandSemanticProvider(Protocol):
    """1つのtyped Robot command semanticを実行可能にするprovider契約。"""

    command_semantics_identity: VersionedIdentity
    command_type: type
    assembly_binding: ProviderAssemblyBinding

    def execute(self, command: RobotCommand, *, backend: object) -> None: ...


CAPABILITY_PROVIDER_TYPES: Mapping[VersionedIdentity, type] = MappingProxyType(
    {
        RESET_INITIAL_STATE_V1: ResetInitialStateProvider,
        ENDPOINT_POSE_V1: EndpointPoseProvider,
        ENDPOINT_COMMAND_V1: EndpointCommandProvider,
        QPOS_FEASIBILITY_V1: QposFeasibilityProvider,
        SCENE_ROLE_BINDING_V1: SceneRoleBindingProvider,
        CONTACT_EVIDENCE_V1: ContactEvidenceProvider,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderAssemblyBinding:
    """Canonical Robot Bundle identity and Profile/Runtime Plugin owner."""

    robot_identity: VersionedIdentity
    owner: object

    def __post_init__(self) -> None:
        if not isinstance(self.robot_identity, VersionedIdentity):
            raise TypeError("provider assembly binding must use VersionedIdentity")
        if self.owner is None:
            raise TypeError("provider assembly binding owner must not be None")


@dataclass(frozen=True, slots=True)
class CapabilityProviderBinding:
    """capability identityとassembly対象providerを対応付ける宣言。"""

    identity: VersionedIdentity
    provider: object

    def __post_init__(self) -> None:
        expected_type = CAPABILITY_PROVIDER_TYPES.get(self.identity)
        if expected_type is None:
            raise ValueError(f"unknown capability identity {self.identity.canonical_id!r}")
        if not isinstance(self.provider, expected_type):
            raise TypeError(
                f"provider for {self.identity.canonical_id!r} does not satisfy "
                f"{expected_type.__name__}"
            )
        if getattr(self.provider, "capability_identity", None) != self.identity:
            raise ValueError(
                f"provider capability identity mismatch for {self.identity.canonical_id!r}"
            )
        if not isinstance(
            getattr(self.provider, "assembly_binding", None),
            ProviderAssemblyBinding,
        ):
            raise TypeError(
                f"provider for {self.identity.canonical_id!r} does not declare "
                "a ProviderAssemblyBinding"
            )


@dataclass(frozen=True, slots=True)
class RobotCommandSemanticProviderBinding:
    """command semantic identityとtyped providerを対応付ける宣言。"""

    identity: VersionedIdentity
    provider: RobotCommandSemanticProvider

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VersionedIdentity):
            raise TypeError("Robot command semantic identity must use VersionedIdentity")
        if not isinstance(self.provider, RobotCommandSemanticProvider):
            raise TypeError(
                "Robot command semantic provider must satisfy "
                "RobotCommandSemanticProvider"
            )
        if self.provider.command_semantics_identity != self.identity:
            raise ValueError(
                "Robot command semantic provider identity mismatch for "
                f"{self.identity.canonical_id!r}"
            )
        if not isinstance(self.provider.command_type, type):
            raise TypeError("Robot command semantic provider command_type must be a type")
        semantic_contract = robot_command_semantic_contract(self.identity)
        if self.provider.command_type is not semantic_contract.command_type:
            raise TypeError(
                "Robot command semantic provider command type mismatch for "
                f"{self.identity.canonical_id!r}"
            )
        if not isinstance(self.provider.assembly_binding, ProviderAssemblyBinding):
            raise TypeError(
                "Robot command semantic provider must declare a "
                "ProviderAssemblyBinding"
            )


@dataclass(frozen=True, slots=True)
class RobotBundle:
    """1つのRobot logical identityに属するprovider集合。

    profile/runtime pluginはこのBundleを参照できるが、Bundle自身はproduction
    selection、simulator起動、command送信を行わない。
    """

    identity: VersionedIdentity
    profile: RobotProfile
    runtime_plugin: RobotRuntimePlugin
    capability_providers: tuple[CapabilityProviderBinding, ...]
    command_semantic_providers: tuple[RobotCommandSemanticProviderBinding, ...]
    parameter_contract: ParameterContract = ParameterContract()

    def __post_init__(self) -> None:
        validate_robot_profile_plugin_consistency(
            self.profile.profile_id, self.profile, self.runtime_plugin
        )
        identities = tuple(binding.identity for binding in self.capability_providers)
        if len(identities) != len(set(identities)):
            raise ValueError("ambiguous Robot Bundle capability provider registration")
        command_semantic_identities = tuple(
            binding.identity for binding in self.command_semantic_providers
        )
        if not command_semantic_identities:
            raise ValueError(
                "Robot Bundle must bind at least one executable command semantic provider"
            )
        if len(command_semantic_identities) != len(set(command_semantic_identities)):
            raise ValueError(
                "ambiguous Robot Bundle command semantic provider registration"
            )
        for binding in (
            *self.capability_providers,
            *self.command_semantic_providers,
        ):
            assembly = binding.provider.assembly_binding
            if assembly.robot_identity != self.identity:
                raise ValueError(
                    "Robot Bundle/provider logical identity mismatch for "
                    f"{binding.identity.canonical_id!r}"
                )
            if assembly.owner is not self.profile and assembly.owner is not self.runtime_plugin:
                raise ValueError(
                    "Robot Bundle provider is not bound to the canonical Profile or "
                    f"Runtime Plugin for {binding.identity.canonical_id!r}"
                )

    @property
    def supported_command_semantics(self) -> frozenset[VersionedIdentity]:
        return frozenset(
            binding.identity for binding in self.command_semantic_providers
        )

    @property
    def provided_capabilities(self) -> frozenset[VersionedIdentity]:
        return frozenset(binding.identity for binding in self.capability_providers)

    def provider(self, identity: VersionedIdentity) -> object:
        providers = tuple(
            binding.provider
            for binding in self.capability_providers
            if binding.identity == identity
        )
        if not providers:
            provided_ids = tuple(
                item.canonical_id for item in sorted(self.provided_capabilities)
            )
            raise ValueError(
                f"unsupported Robot Bundle capability {identity.canonical_id!r}; "
                f"provided={provided_ids}"
            )
        if len(providers) != 1:
            raise ValueError(
                f"ambiguous Robot Bundle capability provider {identity.canonical_id!r}"
            )
        return providers[0]

    def command_semantic_provider(
        self, identity: VersionedIdentity
    ) -> RobotCommandSemanticProvider:
        providers = tuple(
            binding.provider
            for binding in self.command_semantic_providers
            if binding.identity == identity
        )
        if not providers:
            supported_ids = tuple(
                item.canonical_id for item in sorted(self.supported_command_semantics)
            )
            raise ValueError(
                "mapping/Robot command semantics compatibility mismatch: "
                f"required={identity.canonical_id!r}, supported={supported_ids}"
            )
        if len(providers) != 1:
            raise ValueError(
                f"ambiguous Robot command semantic provider {identity.canonical_id!r}"
            )
        return providers[0]

    def semantic_role_bindings(self) -> tuple[SemanticRoleBinding, ...]:
        provider = self.provider(SCENE_ROLE_BINDING_V1)
        assert isinstance(provider, SceneRoleBindingProvider)
        return provider.semantic_role_bindings()

    @property
    def provided_evidence(self) -> frozenset[VersionedIdentity]:
        if CONTACT_EVIDENCE_V1 not in self.provided_capabilities:
            return frozenset()
        provider = self.provider(CONTACT_EVIDENCE_V1)
        assert isinstance(provider, ContactEvidenceProvider)
        return provider.evidence_identities


__all__ = [
    "CAPABILITY_PROVIDER_TYPES",
    "CONTACT_EVIDENCE_V1",
    "CapabilityProviderBinding",
    "ContactEvidenceProvider",
    "ENDPOINT_COMMAND_V1",
    "ENDPOINT_POSE_V1",
    "EndpointCommandProvider",
    "EndpointPoseObservation",
    "EndpointPoseProvider",
    "InitialStateContract",
    "InitialStateContractProvider",
    "InitialStateReference",
    "QPOS_FEASIBILITY_V1",
    "QposFeasibilityProvider",
    "ProviderAssemblyBinding",
    "RobotCommandSemanticProvider",
    "RobotCommandSemanticProviderBinding",
    "RESET_INITIAL_STATE_V1",
    "ROBOT_TOOL_ENDPOINT_ROLE",
    "ResetInitialStateProvider",
    "RobotBundle",
    "SCENE_ROLE_BINDING_V1",
    "SceneRoleBindingProvider",
    "SemanticRoleBinding",
]
