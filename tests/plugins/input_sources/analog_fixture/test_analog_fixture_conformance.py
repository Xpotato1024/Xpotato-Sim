from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.runtime.experiment.input_source import InputSourceHealthStatus
from tests.plugins.input_sources.contract.conformance import (
    InputSourceConformanceCase,
    InputSourceHealthTransitionCase,
    TimestampSequencePolicy,
    assert_input_source_plugin_conforms,
)


def _assert_analog_metadata(metadata: object) -> None:
    assert isinstance(metadata, dict) or hasattr(metadata, "get")
    assert metadata.get("source_kind") == "analog_fixture"  # type: ignore[union-attr]
    assert "source_active" in metadata  # type: ignore[operator]
    assert "stale_reason" in metadata  # type: ignore[operator]


def test_analog_fixture_plugin_conforms_from_deterministic_sample() -> None:
    plugin = INPUT_SOURCE_CATALOG.resolve("analog_fixture").plugin
    assert_input_source_plugin_conforms(
        InputSourceConformanceCase(
            plugin=plugin,
            parameters={
                "samples": (
                    {
                        "timestamp_s": 1.0,
                        "raw_values": (512, 612, 312, 512, 512, 512, 512),
                        "active": True,
                        "stale_reason": None,
                    },
                )
            },
            expected_frame_source="analog_fixture",
            reads_per_instance=2,
            timestamp_sequence_policy=TimestampSequencePolicy.TERMINAL_HOLD,
            frame_validator=lambda frame: (
                (_ for _ in ()).throw(AssertionError("unexpected source"))
                if frame.metadata.get("source_kind") != "analog_fixture"
                else None
            ),
            metadata_validator=_assert_analog_metadata,
            health_transition_cases=(
                InputSourceHealthTransitionCase(
                    parameters={
                        "samples": (
                            {
                                "timestamp_s": 1.0,
                                "raw_values": (512,) * 7,
                                "active": False,
                                "stale_reason": None,
                            },
                        )
                    },
                    expected_initial_status=InputSourceHealthStatus.ACTIVE,
                    expected_after_read_status=InputSourceHealthStatus.INACTIVE,
                ),
                InputSourceHealthTransitionCase(
                    parameters={
                        "samples": (
                            {
                                "timestamp_s": 1.0,
                                "raw_values": (512,) * 7,
                                "active": False,
                                "stale_reason": "fixture_stale",
                            },
                        )
                    },
                    expected_initial_status=InputSourceHealthStatus.ACTIVE,
                    expected_after_read_status=InputSourceHealthStatus.STALE,
                    expected_after_read_reason="fixture_stale",
                ),
            ),
        )
    )
