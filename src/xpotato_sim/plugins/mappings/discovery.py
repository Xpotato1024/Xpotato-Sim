"""first-party Control Mappingのfixed ``plugin.py`` を読むbounded discovery。

declaration importだけを行い、Mapping algorithm実行やRobot provider bindingは行わない。
duplicate identityと不正なdeclarationはregistry完成前に拒否する。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from xpotato_sim.plugins.bounded_discovery import (
    BoundedPluginImportError,
    direct_child_package_names,
    import_fixed_entry_module,
)
from xpotato_sim.runtime.experiment.contracts import ControlMappingPlugin
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry


CONTROL_MAPPING_PLUGIN_ENTRY_MODULE = "plugin"
CONTROL_MAPPING_PLUGIN_ENTRY_SYMBOL = "CONTROL_MAPPING_PLUGIN"


class ControlMappingPluginDiscoveryError(RuntimeError):
    """Fail-closed Control Mapping Plugin discovery error."""


@dataclass(frozen=True, slots=True)
class ControlMappingDiscoveryRoot:
    """Control Mapping探索を許可するpackage namespace。"""
    namespace: ModuleType

    def __post_init__(self) -> None:
        if not hasattr(self.namespace, "__path__"):
            raise ValueError("control mapping discovery namespace must be a package")


def _load_plugin(
    root: ControlMappingDiscoveryRoot,
    package_name: str,
) -> ControlMappingPlugin:
    try:
        module = import_fixed_entry_module(
            root.namespace,
            package_name,
            entry_module=CONTROL_MAPPING_PLUGIN_ENTRY_MODULE,
            kind="Control Mapping Plugin",
        )
    except BoundedPluginImportError as exc:
        raise ControlMappingPluginDiscoveryError(str(exc)) from exc
    module_name = module.__name__
    if not hasattr(module, CONTROL_MAPPING_PLUGIN_ENTRY_SYMBOL):
        raise ControlMappingPluginDiscoveryError(
            "Control Mapping Plugin export is missing: "
            f"{module_name}.{CONTROL_MAPPING_PLUGIN_ENTRY_SYMBOL}"
        )
    plugin = getattr(module, CONTROL_MAPPING_PLUGIN_ENTRY_SYMBOL)
    if not isinstance(plugin, ControlMappingPlugin):
        raise ControlMappingPluginDiscoveryError(
            f"invalid Control Mapping Plugin type for {module_name}"
        )
    return plugin


def discover_control_mapping_plugins(
    root: ControlMappingDiscoveryRoot,
) -> VersionedPluginRegistry[ControlMappingPlugin]:
    """Discover direct packages only and fail on every broken candidate."""

    package_names = direct_child_package_names(root.namespace)
    plugins = tuple(
        _load_plugin(root, package_name) for package_name in package_names
    )
    try:
        registry = VersionedPluginRegistry(
            plugins, kind="control mapping plugin"
        )
    except ValueError as exc:
        raise ControlMappingPluginDiscoveryError(str(exc)) from exc
    for package_name, plugin in zip(package_names, plugins, strict=True):
        if plugin.identity.name != package_name:
            raise ControlMappingPluginDiscoveryError(
                "Control Mapping Plugin package/declaration identity mismatch: "
                f"package={package_name!r}, declared={plugin.identity.name!r}"
            )
    return registry


def discover_production_control_mapping_plugins(
) -> VersionedPluginRegistry[ControlMappingPlugin]:
    """Discover only the fixed first-party production namespace."""

    from xpotato_sim.plugins import mappings

    return discover_control_mapping_plugins(
        ControlMappingDiscoveryRoot(namespace=mappings)
    )


__all__ = [
    "CONTROL_MAPPING_PLUGIN_ENTRY_MODULE",
    "CONTROL_MAPPING_PLUGIN_ENTRY_SYMBOL",
    "ControlMappingDiscoveryRoot",
    "ControlMappingPluginDiscoveryError",
    "discover_control_mapping_plugins",
    "discover_production_control_mapping_plugins",
]
