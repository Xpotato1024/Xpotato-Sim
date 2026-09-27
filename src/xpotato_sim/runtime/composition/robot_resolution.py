"""Generic registry and Robot Profile/Runtime Plugin resolution primitives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Protocol, TypeVar

from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.composition.robot_plugin import RobotRuntimePlugin
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    VersionedIdentity,
)


class IdentifiedByProfileId(Protocol):
    @property
    def profile_id(self) -> str: ...


T = TypeVar("T", bound=IdentifiedByProfileId)


class ProfileIdRegistry(Protocol[T]):
    @property
    def ids(self) -> tuple[str, ...]: ...

    def resolve(self, profile_id: str) -> T: ...


class ImmutableRegistry(Generic[T]):
    """Deterministic generic registry keyed by ``profile_id``."""

    def __init__(self, entries: Iterable[T], *, kind: str) -> None:
        values: dict[str, T] = {}
        for entry in entries:
            if entry.profile_id in values:
                raise ValueError(
                    f"duplicate {kind} registration: {entry.profile_id!r}"
                )
            values[entry.profile_id] = entry
        self._kind = kind
        self._values: Mapping[str, T] = MappingProxyType(values)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._values)

    def resolve(self, profile_id: str) -> T:
        try:
            return self._values[profile_id]
        except KeyError as exc:
            raise ValueError(
                f"unknown {self._kind} ID {profile_id!r}; available: {self.ids}"
            ) from exc


@dataclass(frozen=True, slots=True)
class ResolvedRobotRuntime:
    profile: RobotProfile
    plugin: RobotRuntimePlugin


def validate_robot_profile_plugin_consistency(
    requested_profile_id: str,
    profile: RobotProfile,
    plugin: RobotRuntimePlugin,
) -> None:
    if profile.profile_id != requested_profile_id:
        raise ValueError(
            "robot profile registry identity mismatch: "
            f"requested {requested_profile_id!r}, got {profile.profile_id!r}"
        )
    if plugin.profile_id != requested_profile_id:
        raise ValueError(
            "robot runtime plugin registry identity mismatch: "
            f"requested {requested_profile_id!r}, got {plugin.profile_id!r}"
        )
    if plugin.profile.profile_id != requested_profile_id:
        raise ValueError(
            "robot runtime plugin profile identity mismatch: "
            f"requested {requested_profile_id!r}, got {plugin.profile.profile_id!r}"
        )
    if plugin.profile.profile_contract_version != profile.profile_contract_version:
        raise ValueError("robot profile/plugin profile contract version mismatch")
    if plugin.profile.model_contract_version != profile.model_contract_version:
        raise ValueError("robot profile/plugin model contract version mismatch")
    if plugin.profile != profile:
        raise ValueError("robot profile/plugin declarative contract mismatch")
    if plugin.profile is not profile:
        raise ValueError(
            "robot runtime plugin does not reference the registered profile object"
        )


def validate_production_robot_selection_consistency(
    selection: PluginSelection,
    *,
    bundle_identity: VersionedIdentity,
    profile: RobotProfile,
    plugin: RobotRuntimePlugin,
) -> None:
    """Validate one production Robot selection without restricting generic Bundles."""

    expected_identity = VersionedIdentity(
        selection.plugin_id,
        selection.contract_version,
    )
    if bundle_identity != expected_identity:
        raise ValueError(
            "production Robot selection/Bundle logical identity mismatch: "
            f"selection={expected_identity.canonical_id}, "
            f"bundle={bundle_identity.canonical_id}"
        )
    if profile.profile_id != selection.plugin_id:
        raise ValueError(
            "production Robot Bundle/Profile logical identity mismatch: "
            f"bundle={bundle_identity.canonical_id}, "
            f"profile={profile.profile_id}/v{profile.profile_contract_version}"
        )
    if plugin.profile_id != selection.plugin_id:
        raise ValueError(
            "production Robot Bundle/Runtime Plugin logical identity mismatch: "
            f"bundle={bundle_identity.canonical_id}, plugin={plugin.profile_id!r}"
        )
    if profile.profile_contract_version != selection.contract_version:
        raise ValueError(
            "production Robot Bundle/Profile logical version mismatch: "
            f"bundle={bundle_identity.canonical_id}, "
            f"profile={profile.profile_id}/v{profile.profile_contract_version}"
        )
    validate_robot_profile_plugin_consistency(
        selection.plugin_id,
        profile,
        plugin,
    )


def resolve_robot_runtime_from_registries(
    profile_id: str,
    *,
    profile_registry: ProfileIdRegistry[RobotProfile],
    plugin_registry: ProfileIdRegistry[RobotRuntimePlugin],
    robot_logical_version: int = 1,
) -> ResolvedRobotRuntime:
    if frozenset(profile_registry.ids) != frozenset(plugin_registry.ids):
        raise ValueError(
            "robot profile/runtime plugin registry ID mismatch: "
            f"profiles={profile_registry.ids}, plugins={plugin_registry.ids}"
        )
    profile = profile_registry.resolve(profile_id)
    plugin = plugin_registry.resolve(profile_id)
    if profile.profile_contract_version != robot_logical_version:
        raise ValueError(
            f"Robot Profile logical version mismatch for {profile_id!r}: "
            f"requested v{robot_logical_version}, "
            f"registered v{profile.profile_contract_version}"
        )
    validate_robot_profile_plugin_consistency(profile_id, profile, plugin)
    return ResolvedRobotRuntime(profile=profile, plugin=plugin)


__all__ = [
    "ImmutableRegistry",
    "ProfileIdRegistry",
    "ResolvedRobotRuntime",
    "resolve_robot_runtime_from_registries",
    "validate_production_robot_selection_consistency",
    "validate_robot_profile_plugin_consistency",
]
