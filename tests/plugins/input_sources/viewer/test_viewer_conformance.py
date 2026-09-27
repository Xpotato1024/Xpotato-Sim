from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    TimestampSequencePolicy,
    assert_input_source_plugin_conforms,
)


def test_viewer_plugin_conforms_without_browser_or_network() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("viewer").plugin
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=plugin,
            parameters={
                "metadata": {"fixture": "viewer"},
                "initial_endpoint_m": (0.6, 0.0, 0.1),
            },
            runtime_dependencies=InputSourceRuntimeDependencies(clock=lambda: 0.0),
            expected_frame_source="viewer",
            reads_per_instance=2,
            timestamp_sequence_policy=TimestampSequencePolicy.CONSTANT_TIMESTAMP,
        )
    )
