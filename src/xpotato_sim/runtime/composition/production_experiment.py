"""application composition rootへproduction 6軸catalogを公開する。

concrete plugin identityの中央listは持たず、各axisのbounded discoveryがcatalogを
構成する。このmoduleではlifecycleやexternal I/Oを開始しない。
"""

from xpotato_sim.plugins.environments.catalog import ENVIRONMENT_REGISTRY
from xpotato_sim.plugins.evaluations.catalog import EVALUATION_REGISTRY
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.catalog import CONTROL_MAPPING_REGISTRY
from xpotato_sim.plugins.robots.catalog import ROBOT_BUNDLE_REGISTRY
from xpotato_sim.plugins.tasks.catalog import TASK_REGISTRY
from xpotato_sim.runtime.experiment.composition import (
    ExperimentPluginManifest,
    ExperimentPluginRegistries,
    ResolvedExperimentComposition,
    compose_experiment,
)


PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES = ExperimentPluginRegistries(
    robot_bundles=ROBOT_BUNDLE_REGISTRY,
    environments=ENVIRONMENT_REGISTRY,
    control_mappings=CONTROL_MAPPING_REGISTRY,
    tasks=TASK_REGISTRY,
    evaluators=EVALUATION_REGISTRY,
    input_sources=INPUT_SOURCE_CATALOG.registry,
)


def resolve_production_experiment(
    manifest: ExperimentPluginManifest,
) -> ResolvedExperimentComposition:
    """lifecycleを開始せずproduction 6軸をすべて解決する。"""

    return compose_experiment(manifest, PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES)


__all__ = [
    "PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES",
    "resolve_production_experiment",
]
