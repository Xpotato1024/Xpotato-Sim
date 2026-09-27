"""production Taskをexact logical identityで解決するcatalog。"""

from xpotato_sim.plugins.tasks.discovery import discover_production_task_plugins
from xpotato_sim.runtime.experiment.contracts import PluginSelection, TaskPlugin


TASK_REGISTRY = discover_production_task_plugins()
TASK_PLUGINS: tuple[TaskPlugin, ...] = TASK_REGISTRY.entries


def resolve_task_plugin(selection: PluginSelection) -> TaskPlugin:
    """lifecycleを開始せずproduction Taskを1件解決する。"""

    return TASK_REGISTRY.resolve(selection)


__all__ = ["TASK_PLUGINS", "TASK_REGISTRY", "resolve_task_plugin"]
