from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    TimestampSequencePolicy,
    assert_input_source_plugin_conforms,
)


def test_noop_plugin_conforms() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("noop").plugin
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=plugin,
            parameters={"metadata": {"fixture": "noop"}},
            expected_frame_source="noop",
            reads_per_instance=2,
            timestamp_sequence_policy=TimestampSequencePolicy.CONSTANT_TIMESTAMP,
        )
    )
