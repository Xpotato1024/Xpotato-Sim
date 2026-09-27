"""first-party Taskのfixed ``plugin.py`` を読むbounded discovery。"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from xpotato_sim.plugins.bounded_discovery import (
    BoundedPluginImportError,
    direct_child_package_names,
    import_fixed_entry_module,
)
from xpotato_sim.runtime.experiment.contracts import TaskPlugin
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry


TASK_PLUGIN_ENTRY_MODULE = "plugin"
TASK_PLUGIN_ENTRY_SYMBOL = "TASK_PLUGIN"


class TaskPluginDiscoveryError(RuntimeError):
    """Task Plugin discoveryのfail-closed error。"""


@dataclass(frozen=True, slots=True)
class TaskDiscoveryRoot:
    """Task探索を許可するpackage namespace。"""

    namespace: ModuleType

    def __post_init__(self) -> None:
        if not hasattr(self.namespace, "__path__"):
            raise ValueError("task discovery namespace must be a package")


def _load_plugin(root: TaskDiscoveryRoot, package_name: str) -> TaskPlugin:
    try:
        module = import_fixed_entry_module(
            root.namespace,
            package_name,
            entry_module=TASK_PLUGIN_ENTRY_MODULE,
            kind="Task Plugin",
        )
    except BoundedPluginImportError as exc:
        raise TaskPluginDiscoveryError(str(exc)) from exc
    module_name = module.__name__
    if not hasattr(module, TASK_PLUGIN_ENTRY_SYMBOL):
        raise TaskPluginDiscoveryError(
            f"Task Plugin export is missing: {module_name}.{TASK_PLUGIN_ENTRY_SYMBOL}"
        )
    plugin = getattr(module, TASK_PLUGIN_ENTRY_SYMBOL)
    if not isinstance(plugin, TaskPlugin):
        raise TaskPluginDiscoveryError(
            f"invalid Task Plugin type for {module_name}"
        )
    return plugin


def discover_task_plugins(
    root: TaskDiscoveryRoot,
) -> VersionedPluginRegistry[TaskPlugin]:
    """public direct childだけを探索し、壊れたcandidateをすべて拒否する。"""

    package_names = direct_child_package_names(root.namespace)
    plugins = tuple(_load_plugin(root, name) for name in package_names)
    try:
        registry = VersionedPluginRegistry(plugins, kind="task plugin")
    except ValueError as exc:
        raise TaskPluginDiscoveryError(str(exc)) from exc
    for package_name, plugin in zip(package_names, plugins, strict=True):
        if plugin.identity.name != package_name:
            raise TaskPluginDiscoveryError(
                "Task Plugin package/declaration identity mismatch: "
                f"package={package_name!r}, declared={plugin.identity.name!r}"
            )
    return registry


def discover_production_task_plugins() -> VersionedPluginRegistry[TaskPlugin]:
    """固定されたfirst-party production namespaceだけを探索する。"""

    from xpotato_sim.plugins import tasks

    return discover_task_plugins(TaskDiscoveryRoot(namespace=tasks))


__all__ = [
    "TASK_PLUGIN_ENTRY_MODULE",
    "TASK_PLUGIN_ENTRY_SYMBOL",
    "TaskDiscoveryRoot",
    "TaskPluginDiscoveryError",
    "discover_task_plugins",
    "discover_production_task_plugins",
]
