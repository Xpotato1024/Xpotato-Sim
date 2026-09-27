"""first-party Environmentのfixed ``plugin.py`` を読むbounded discovery。"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from xpotato_sim.plugins.bounded_discovery import (
    BoundedPluginImportError,
    direct_child_package_names,
    import_fixed_entry_module,
)
from xpotato_sim.runtime.experiment.contracts import EnvironmentPlugin
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry


ENVIRONMENT_PLUGIN_ENTRY_MODULE = "plugin"
ENVIRONMENT_PLUGIN_ENTRY_SYMBOL = "ENVIRONMENT_PLUGIN"


class EnvironmentPluginDiscoveryError(RuntimeError):
    """Environment Plugin discoveryのfail-closed error。"""


@dataclass(frozen=True, slots=True)
class EnvironmentDiscoveryRoot:
    """Environment探索を許可するpackage namespace。"""

    namespace: ModuleType

    def __post_init__(self) -> None:
        if not hasattr(self.namespace, "__path__"):
            raise ValueError("environment discovery namespace must be a package")


def _load_plugin(
    root: EnvironmentDiscoveryRoot,
    package_name: str,
) -> EnvironmentPlugin:
    try:
        module = import_fixed_entry_module(
            root.namespace,
            package_name,
            entry_module=ENVIRONMENT_PLUGIN_ENTRY_MODULE,
            kind="Environment Plugin",
        )
    except BoundedPluginImportError as exc:
        raise EnvironmentPluginDiscoveryError(str(exc)) from exc
    module_name = module.__name__
    if not hasattr(module, ENVIRONMENT_PLUGIN_ENTRY_SYMBOL):
        raise EnvironmentPluginDiscoveryError(
            "Environment Plugin export is missing: "
            f"{module_name}.{ENVIRONMENT_PLUGIN_ENTRY_SYMBOL}"
        )
    plugin = getattr(module, ENVIRONMENT_PLUGIN_ENTRY_SYMBOL)
    if not isinstance(plugin, EnvironmentPlugin):
        raise EnvironmentPluginDiscoveryError(
            f"invalid Environment Plugin type for {module_name}"
        )
    return plugin


def discover_environment_plugins(
    root: EnvironmentDiscoveryRoot,
) -> VersionedPluginRegistry[EnvironmentPlugin]:
    """public direct childだけを探索し、壊れたcandidateをすべて拒否する。"""

    package_names = direct_child_package_names(root.namespace)
    plugins = tuple(_load_plugin(root, name) for name in package_names)
    try:
        registry = VersionedPluginRegistry(plugins, kind="environment plugin")
    except ValueError as exc:
        raise EnvironmentPluginDiscoveryError(str(exc)) from exc
    for package_name, plugin in zip(package_names, plugins, strict=True):
        if plugin.identity.name != package_name:
            raise EnvironmentPluginDiscoveryError(
                "Environment Plugin package/declaration identity mismatch: "
                f"package={package_name!r}, declared={plugin.identity.name!r}"
            )
    return registry


def discover_production_environment_plugins(
) -> VersionedPluginRegistry[EnvironmentPlugin]:
    """固定されたfirst-party production namespaceだけを探索する。"""

    from xpotato_sim.plugins import environments

    return discover_environment_plugins(
        EnvironmentDiscoveryRoot(namespace=environments)
    )


__all__ = [
    "ENVIRONMENT_PLUGIN_ENTRY_MODULE",
    "ENVIRONMENT_PLUGIN_ENTRY_SYMBOL",
    "EnvironmentDiscoveryRoot",
    "EnvironmentPluginDiscoveryError",
    "discover_environment_plugins",
    "discover_production_environment_plugins",
]
