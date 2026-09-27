from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from xpotato_sim.plugins.input_sources.analog_fixture import (
    AnalogFixtureSample,
    parse_analog_fixture_sample,
)


def test_analog_fixture_parser_preserves_strict_sample_representation() -> None:
    sample = parse_analog_fixture_sample(
        {
            "timestamp_s": 1,
            "raw_values": [1, 2.5, -3],
            "active": False,
            "stale_reason": "recorded_stale",
        }
    )

    assert sample == AnalogFixtureSample(
        timestamp_s=1.0,
        raw_values=(1.0, 2.5, -3.0),
        active=False,
        stale_reason="recorded_stale",
    )
    with pytest.raises(FrozenInstanceError):
        sample.active = True  # type: ignore[misc]


@pytest.mark.parametrize(
    "value, message",
    (
        (
            {
                "timestamp_s": 1.0,
                "raw_values": [1.0],
                "active": True,
            },
            "fields must be exactly",
        ),
        (
            {
                "timestamp_s": float("nan"),
                "raw_values": [1.0],
                "active": True,
                "stale_reason": None,
            },
            "timestamp_s must be finite",
        ),
        (
            {
                "timestamp_s": 1.0,
                "raw_values": [],
                "active": True,
                "stale_reason": None,
            },
            "raw_values must be a non-empty sequence",
        ),
        (
            {
                "timestamp_s": 1.0,
                "raw_values": [1.0],
                "active": True,
                "stale_reason": "stale",
            },
            "active fixture sample cannot be stale",
        ),
    ),
)
def test_analog_fixture_parser_rejects_invalid_samples(
    value: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_analog_fixture_sample(value)
