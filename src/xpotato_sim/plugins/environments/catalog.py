"""production Environmentをexact logical identityで解決するcatalog。"""

from xpotato_sim.plugins.environments.discovery import (
    discover_production_environment_plugins,
)
from xpotato_sim.runtime.experiment.contracts import EnvironmentPlugin, PluginSelection


ENVIRONMENT_REGISTRY = discover_production_environment_plugins()
ENVIRONMENT_PLUGINS: tuple[EnvironmentPlugin, ...] = ENVIRONMENT_REGISTRY.entries


def resolve_environment_plugin(selection: PluginSelection) -> EnvironmentPlugin:
    """sceneをcomposeせずproduction Environmentを1件解決する。"""

    return ENVIRONMENT_REGISTRY.resolve(selection)


__all__ = [
    "ENVIRONMENT_PLUGINS",
    "ENVIRONMENT_REGISTRY",
    "resolve_environment_plugin",
]
