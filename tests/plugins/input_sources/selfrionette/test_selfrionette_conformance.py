"""Production Selfrionette source conformance."""

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import LOADCELL_ENDPOINT_MAPPING_PLUGIN
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    TimestampSequencePolicy,
    assert_sample_schema_compatible,
    assert_input_source_plugin_conforms,
)


_VALID_LINE = "vector,1,1,2,3,4,5,6,7"
_SECOND_VALID_LINE = "vector,2,2,3,4,5,6,7,8"


def test_selfrionette_plugin_conforms_with_injected_lines() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    assert_sample_schema_compatible(plugin, LOADCELL_ENDPOINT_MAPPING_PLUGIN)
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=plugin,
            parameters={"lines": (_VALID_LINE, _SECOND_VALID_LINE)},
            expected_frame_source="selfrionette",
            expected_started_status="active",
            reads_per_instance=2,
            timestamp_sequence_policy=TimestampSequencePolicy.PRESERVED_REPLAY_ORDER,
            timestamp_sequence_validator=lambda frames: (
                None
                if tuple(frame.timestamp_s for frame in frames) == (0.001, 0.002)
                else (_ for _ in ()).throw(AssertionError("serial order changed"))
            ),
        )
    )
