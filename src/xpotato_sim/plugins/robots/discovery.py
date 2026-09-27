"""repository配下だけを走査するfirst-party Robot Plugin bounded discovery。

import対象を明示root直下のfixed ``plugin.py`` に限定し、duplicate identity、
malformed registration、resource ownership違反をcatalog作成前に拒否する。
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType

from xpotato_sim.plugins.robots.registration import RobotPluginRegistration


ROBOT_PLUGIN_ENTRY_MODULE = "plugin"
ROBOT_PLUGIN_ENTRY_SYMBOL = "ROBOT_PLUGIN"


class RobotPluginDiscoveryError(RuntimeError):
    """Fail-closed discovery error with candidate package context."""


@dataclass(frozen=True, slots=True)
class RobotDiscoveryRoot:
    """探索を許可するfilesystem root、package、resource rootの対応。"""
    namespace: ModuleType
    repository_root: Path
    asset_roots: tuple[Path, ...]
    configuration_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        if not hasattr(self.namespace, "__path__"):
            raise ValueError("robot discovery namespace must be a package")
        if not self.asset_roots or not self.configuration_roots:
            raise ValueError("robot discovery roots must be explicit")


class RobotPluginRegistry:
    """discovery済みregistrationをcanonical IDで保持するread-only registry。"""
    def __init__(self, entries: Iterable[RobotPluginRegistration]) -> None:
        values: dict[str, RobotPluginRegistration] = {}
        for entry in entries:
            robot_id = entry.identity.name
            if robot_id in values:
                raise ValueError(f"duplicate Robot Plugin registration: {robot_id!r}")
            values[robot_id] = entry
        self._values: Mapping[str, RobotPluginRegistration] = MappingProxyType(
            dict(sorted(values.items()))
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._values)

    @property
    def entries(self) -> tuple[RobotPluginRegistration, ...]:
        return tuple(self._values.values())

    def resolve(
        self, robot_id: str, *, robot_logical_version: int = 1
    ) -> RobotPluginRegistration:
        try:
            registration = self._values[robot_id]
        except KeyError as exc:
            raise ValueError(
                f"unknown Robot Plugin ID {robot_id!r}; available: {self.ids}"
            ) from exc
        if registration.identity.version != robot_logical_version:
            raise ValueError(
                f"Robot Plugin logical version mismatch for {robot_id!r}: "
                f"requested v{robot_logical_version}, "
                f"registered v{registration.identity.version}"
            )
        return registration

    def canonical_identity_bytes(self) -> bytes:
        documents = [
            json.loads(self._values[robot_id].canonical_identity_bytes())
            for robot_id in self.ids
        ]
        return json.dumps(
            documents,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


def _candidate_package_names(root: RobotDiscoveryRoot) -> tuple[str, ...]:
    candidates = (
        item.name
        for item in pkgutil.iter_modules(root.namespace.__path__)
        if item.ispkg and not item.name.startswith("_")
    )
    return tuple(sorted(candidates))


def _load_registration(
    root: RobotDiscoveryRoot, package_name: str
) -> RobotPluginRegistration:
    module_name = (
        f"{root.namespace.__name__}.{package_name}.{ROBOT_PLUGIN_ENTRY_MODULE}"
    )
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise RobotPluginDiscoveryError(
                f"Robot Plugin entry point is missing: {module_name}"
            ) from exc
        raise RobotPluginDiscoveryError(
            f"Robot Plugin import failed for {module_name}: {exc}"
        ) from exc
    except Exception as exc:
        raise RobotPluginDiscoveryError(
            f"Robot Plugin import failed for {module_name}: {exc}"
        ) from exc
    if not hasattr(module, ROBOT_PLUGIN_ENTRY_SYMBOL):
        raise RobotPluginDiscoveryError(
            f"Robot Plugin export is missing: {module_name}.{ROBOT_PLUGIN_ENTRY_SYMBOL}"
        )
    registration = getattr(module, ROBOT_PLUGIN_ENTRY_SYMBOL)
    if not isinstance(registration, RobotPluginRegistration):
        raise RobotPluginDiscoveryError(
            f"invalid Robot Plugin registration type for {module_name}"
        )
    if registration.identity.name != package_name:
        raise RobotPluginDiscoveryError(
            "Robot Plugin package/declaration identity mismatch: "
            f"package={package_name!r}, declared={registration.identity.name!r}"
        )
    try:
        registration.validate_resources(
            root.repository_root,
            asset_roots=root.asset_roots,
            configuration_roots=root.configuration_roots,
        )
    except (TypeError, ValueError) as exc:
        raise RobotPluginDiscoveryError(
            f"Robot Plugin resource validation failed for {package_name!r}: {exc}"
        ) from exc
    return registration


def discover_robot_plugins(root: RobotDiscoveryRoot) -> RobotPluginRegistry:
    """Discover direct packages only and fail on every broken candidate."""

    registrations = tuple(
        _load_registration(root, package_name)
        for package_name in _candidate_package_names(root)
    )
    try:
        return RobotPluginRegistry(registrations)
    except ValueError as exc:
        raise RobotPluginDiscoveryError(str(exc)) from exc


def discover_production_robot_plugins() -> RobotPluginRegistry:
    """Use the fixed first-party production namespace and repository roots."""

    from xpotato_sim.plugins import robots

    repository_root = Path(__file__).resolve().parents[3]
    return discover_robot_plugins(
        RobotDiscoveryRoot(
            namespace=robots,
            repository_root=repository_root,
            asset_roots=(repository_root / "assets" / "mujoco",),
            configuration_roots=(repository_root / "configs",),
        )
    )


__all__ = [
    "ROBOT_PLUGIN_ENTRY_MODULE",
    "ROBOT_PLUGIN_ENTRY_SYMBOL",
    "RobotDiscoveryRoot",
    "RobotPluginDiscoveryError",
    "RobotPluginRegistry",
    "discover_production_robot_plugins",
    "discover_robot_plugins",
]
