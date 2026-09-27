"""first-party Evaluationのfixed ``plugin.py`` を読むbounded discovery。"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from xpotato_sim.plugins.bounded_discovery import (
    BoundedPluginImportError,
    direct_child_package_names,
    import_fixed_entry_module,
)
from xpotato_sim.runtime.experiment.contracts import EvaluationPlugin
from xpotato_sim.runtime.experiment.registry import VersionedPluginRegistry


EVALUATION_PLUGIN_ENTRY_MODULE = "plugin"
EVALUATION_PLUGIN_ENTRY_SYMBOL = "EVALUATION_PLUGIN"


class EvaluationPluginDiscoveryError(RuntimeError):
    """Evaluation Plugin discoveryのfail-closed error。"""


@dataclass(frozen=True, slots=True)
class EvaluationDiscoveryRoot:
    """Evaluation探索を許可するpackage namespace。"""

    namespace: ModuleType

    def __post_init__(self) -> None:
        if not hasattr(self.namespace, "__path__"):
            raise ValueError("evaluation discovery namespace must be a package")


def _load_plugin(
    root: EvaluationDiscoveryRoot,
    package_name: str,
) -> EvaluationPlugin:
    try:
        module = import_fixed_entry_module(
            root.namespace,
            package_name,
            entry_module=EVALUATION_PLUGIN_ENTRY_MODULE,
            kind="Evaluation Plugin",
        )
    except BoundedPluginImportError as exc:
        raise EvaluationPluginDiscoveryError(str(exc)) from exc
    module_name = module.__name__
    if not hasattr(module, EVALUATION_PLUGIN_ENTRY_SYMBOL):
        raise EvaluationPluginDiscoveryError(
            "Evaluation Plugin export is missing: "
            f"{module_name}.{EVALUATION_PLUGIN_ENTRY_SYMBOL}"
        )
    plugin = getattr(module, EVALUATION_PLUGIN_ENTRY_SYMBOL)
    if not isinstance(plugin, EvaluationPlugin):
        raise EvaluationPluginDiscoveryError(
            f"invalid Evaluation Plugin type for {module_name}"
        )
    return plugin


def discover_evaluation_plugins(
    root: EvaluationDiscoveryRoot,
) -> VersionedPluginRegistry[EvaluationPlugin]:
    """public direct childだけを探索し、壊れたcandidateをすべて拒否する。"""

    package_names = direct_child_package_names(root.namespace)
    plugins = tuple(_load_plugin(root, name) for name in package_names)
    try:
        registry = VersionedPluginRegistry(plugins, kind="evaluation plugin")
    except ValueError as exc:
        raise EvaluationPluginDiscoveryError(str(exc)) from exc
    for package_name, plugin in zip(package_names, plugins, strict=True):
        if plugin.identity.name != package_name:
            raise EvaluationPluginDiscoveryError(
                "Evaluation Plugin package/declaration identity mismatch: "
                f"package={package_name!r}, declared={plugin.identity.name!r}"
            )
    return registry


def discover_production_evaluation_plugins(
) -> VersionedPluginRegistry[EvaluationPlugin]:
    """固定されたfirst-party production namespaceだけを探索する。"""

    from xpotato_sim.plugins import evaluations

    return discover_evaluation_plugins(
        EvaluationDiscoveryRoot(namespace=evaluations)
    )


__all__ = [
    "EVALUATION_PLUGIN_ENTRY_MODULE",
    "EVALUATION_PLUGIN_ENTRY_SYMBOL",
    "EvaluationDiscoveryRoot",
    "EvaluationPluginDiscoveryError",
    "discover_evaluation_plugins",
    "discover_production_evaluation_plugins",
]
