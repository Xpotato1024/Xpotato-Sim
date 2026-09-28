"""bounded discovery結果を唯一のproduction Robot catalogへ投影する。

logical identityをBundle/Profile/Runtime Pluginへroutingし、未知identityやversion不一致を
fallbackせず拒否する。provider lifecycleやsimulator開始は行わない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from xpotato_sim.plugins.robots.discovery import (
    RobotPluginRegistry,
    discover_production_robot_plugins,
)
from xpotato_sim.plugins.robots.registration import RobotPluginRegistration
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry
from xpotato_sim.runtime.composition.robot_bundle import RobotBundle
from xpotato_sim.runtime.composition.robot_plugin import RobotRuntimePlugin
from xpotato_sim.runtime.composition.robot_resolution import (
    ProfileIdRegistry,
    ResolvedRobotRuntime,
    resolve_robot_runtime_from_registries,
    validate_production_robot_selection_consistency,
)


@dataclass(frozen=True, slots=True)
class RobotCatalog:
    """Robot registrationをidentity一意で保持するread-only resolver。"""

    registrations: RobotPluginRegistry
    bundles: VersionedPluginRegistry[RobotBundle] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bundles",
            VersionedPluginRegistry(
                tuple(
                    registration.bundle
                    for registration in self.registrations.entries
                ),
                kind="Robot Bundle",
            ),
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return self.registrations.ids

    def resolve_registration(
        self, selection: PluginSelection
    ) -> RobotPluginRegistration:
        return self.registrations.resolve(
            selection.plugin_id,
            robot_logical_version=selection.contract_version,
        )

    def resolve_model(self, robot: PluginSelection, model: PluginSelection):
        """単腕・双腕を区別せず、Robot登録内の同じモデル型から解決する。"""
        registration = self.resolve_registration(robot)
        for candidate in registration.models:
            if candidate.identity.name == model.plugin_id and candidate.identity.version == model.contract_version:
                return candidate
        raise ValueError(f"unknown registered robot model: {robot.plugin_id}/{model.plugin_id}/v{model.contract_version}")

    def resolve_bundle(self, selection: PluginSelection) -> RobotBundle:
        registration = self.resolve_registration(selection)
        bundle = self.bundles.resolve(selection)
        if registration.bundle is not bundle:
            raise ValueError("Robot Plugin registration/Bundle catalog identity mismatch")
        return bundle

    def resolve_profile(self, selection: PluginSelection) -> RobotProfile:
        self._require_id(selection.plugin_id, kind="robot profile")
        return self.resolve_bundle(selection).profile

    def resolve_runtime_plugin(
        self, selection: PluginSelection
    ) -> RobotRuntimePlugin:
        self._require_id(selection.plugin_id, kind="robot runtime plugin")
        return self.resolve_bundle(selection).runtime_plugin

    def resolve_runtime(self, selection: PluginSelection) -> ResolvedRobotRuntime:
        self._require_id(selection.plugin_id, kind="robot profile")
        bundle = self.resolve_bundle(selection)
        validate_production_robot_selection_consistency(
            selection,
            bundle_identity=bundle.identity,
            profile=bundle.profile,
            plugin=bundle.runtime_plugin,
        )
        return ResolvedRobotRuntime(
            profile=bundle.profile,
            plugin=bundle.runtime_plugin,
        )

    def _require_id(self, robot_id: str, *, kind: str) -> None:
        if robot_id not in self.ids:
            raise ValueError(
                f"unknown {kind} ID {robot_id!r}; available: {self.ids}"
            )


ROBOT_PLUGIN_REGISTRY: RobotPluginRegistry = discover_production_robot_plugins()
ROBOT_CATALOG = RobotCatalog(ROBOT_PLUGIN_REGISTRY)
ROBOT_BUNDLE_REGISTRY: VersionedPluginRegistry[RobotBundle] = ROBOT_CATALOG.bundles


def _selection(
    robot_id: str,
    *,
    robot_logical_version: int,
    contract_version: int | None = None,
) -> PluginSelection:
    if contract_version is not None:
        if robot_logical_version != 1 and robot_logical_version != contract_version:
            raise ValueError("conflicting robot logical version arguments")
        robot_logical_version = contract_version
    return PluginSelection(robot_id, robot_logical_version)


def resolve_robot_plugin_registration(
    robot_id: str, *, robot_logical_version: int = 1
) -> RobotPluginRegistration:
    """production catalogからexact identity/versionのregistrationを解決する。"""
    return ROBOT_CATALOG.resolve_registration(
        PluginSelection(robot_id, robot_logical_version)
    )


def registered_robot_plugin_ids() -> tuple[str, ...]:
    """登録済みRobot Plugin canonical IDをdeterministic順で返す。"""
    return ROBOT_CATALOG.ids


def resolve_robot_bundle(
    bundle_id: str,
    *,
    robot_logical_version: int = 1,
    contract_version: int | None = None,
) -> RobotBundle:
    """Robot selectionに属するgeneric Bundleを解決し、runtimeを開始しない。"""
    return ROBOT_CATALOG.resolve_bundle(
        _selection(
            bundle_id,
            robot_logical_version=robot_logical_version,
            contract_version=contract_version,
        )
    )


def registered_robot_bundle_ids() -> tuple[str, ...]:
    """catalogが提供するRobot Bundle IDをdeterministic順で返す。"""
    return ROBOT_CATALOG.ids


class _RobotProfileProjectionRegistry:
    """Read-only v1 projection over the single concrete Robot Bundle catalog."""

    @property
    def ids(self) -> tuple[str, ...]:
        return ROBOT_CATALOG.ids

    def resolve(self, profile_id: str) -> RobotProfile:
        return resolve_robot_profile(profile_id)


class _RobotRuntimePluginProjectionRegistry:
    """Read-only v1 projection over the single concrete Robot Bundle catalog."""

    @property
    def ids(self) -> tuple[str, ...]:
        return ROBOT_CATALOG.ids

    def resolve(self, profile_id: str) -> RobotRuntimePlugin:
        return resolve_robot_runtime_plugin(profile_id)


ROBOT_PROFILE_REGISTRY: ProfileIdRegistry[RobotProfile] = (
    _RobotProfileProjectionRegistry()
)
ROBOT_RUNTIME_PLUGIN_REGISTRY: ProfileIdRegistry[RobotRuntimePlugin] = (
    _RobotRuntimePluginProjectionRegistry()
)


def resolve_robot_profile(
    profile_id: str, *, robot_logical_version: int = 1
) -> RobotProfile:
    """compatibility用Profile projectionを同じRobot registrationから解決する。"""
    return ROBOT_CATALOG.resolve_profile(
        PluginSelection(profile_id, robot_logical_version)
    )


def registered_robot_profile_ids() -> tuple[str, ...]:
    """Profile compatibility surfaceのregistered IDを返す。"""
    return ROBOT_CATALOG.ids


def resolve_robot_runtime_plugin(
    profile_id: str, *, robot_logical_version: int = 1
) -> RobotRuntimePlugin:
    """Robot-owned Runtime Plugin projectionをexact IDで解決する。"""
    return ROBOT_CATALOG.resolve_runtime_plugin(
        PluginSelection(profile_id, robot_logical_version)
    )


def registered_robot_runtime_plugin_ids() -> tuple[str, ...]:
    """Runtime Plugin projectionのregistered IDを返す。"""
    return ROBOT_CATALOG.ids


def resolve_robot_runtime(
    profile_id: str,
    *,
    robot_logical_version: int = 1,
    profile_registry: ProfileIdRegistry[RobotProfile] | None = None,
    plugin_registry: ProfileIdRegistry[RobotRuntimePlugin] | None = None,
) -> ResolvedRobotRuntime:
    """legacy registry injectionを含むruntime lookupをcanonical selectionへroutingする。"""
    if profile_registry is None and plugin_registry is None:
        return ROBOT_CATALOG.resolve_runtime(
            PluginSelection(profile_id, robot_logical_version)
        )
    return resolve_robot_runtime_from_registries(
        profile_id,
        profile_registry=(
            ROBOT_PROFILE_REGISTRY if profile_registry is None else profile_registry
        ),
        plugin_registry=(
            ROBOT_RUNTIME_PLUGIN_REGISTRY
            if plugin_registry is None
            else plugin_registry
        ),
        robot_logical_version=robot_logical_version,
    )


__all__ = [
    "ROBOT_BUNDLE_REGISTRY",
    "ROBOT_CATALOG",
    "ROBOT_PLUGIN_REGISTRY",
    "ROBOT_PROFILE_REGISTRY",
    "ROBOT_RUNTIME_PLUGIN_REGISTRY",
    "RobotCatalog",
    "registered_robot_bundle_ids",
    "registered_robot_plugin_ids",
    "registered_robot_profile_ids",
    "registered_robot_runtime_plugin_ids",
    "resolve_robot_bundle",
    "resolve_robot_plugin_registration",
    "resolve_robot_profile",
    "resolve_robot_runtime",
    "resolve_robot_runtime_plugin",
]
