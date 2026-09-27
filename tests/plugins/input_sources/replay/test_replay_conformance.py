from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies
from xpotato_sim.schemas import RawInputFrame
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    TimestampSequencePolicy,
    assert_input_source_plugin_conforms,
)


def test_replay_plugin_conforms() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("replay").plugin
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=plugin,
            parameters={"metadata": {"fixture": "replay"}, "loop": True},
            expected_frame_source="replay",
            runtime_dependencies=InputSourceRuntimeDependencies(
                replay_frames=(
                    RawInputFrame(source="replay", timestamp_s=1.0, metadata={"order": 1}),
                    RawInputFrame(source="replay", timestamp_s=2.0, metadata={"order": 2}),
                )
            ),
            reads_per_instance=2,
            timestamp_sequence_policy=TimestampSequencePolicy.PRESERVED_REPLAY_ORDER,
            timestamp_sequence_validator=lambda frames: (
                None
                if tuple(frame.metadata["order"] for frame in frames) == (1, 2)
                else (_ for _ in ()).throw(AssertionError("replay order changed"))
            ),
        )
    )
