from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    TimestampSequencePolicy,
    assert_input_source_plugin_conforms,
)


def test_programmed_target_plugin_conforms() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("programmed_target").plugin
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=plugin,
            parameters={
                "steps": 2,
                "initial_position_m": (0.6, 0.0, 0.1),
                "preset": "sweep_x",
                "loop": False,
            },
            expected_frame_source="programmed_target",
            reads_per_instance=2,
            timestamp_sequence_policy=TimestampSequencePolicy.MONOTONIC_INDEXED,
        )
    )
