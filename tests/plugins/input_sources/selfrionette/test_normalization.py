"""Selfrionette intrinsic-normalization contract."""

from __future__ import annotations

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import (
    LoadcellNormalizationConfig,
    LoadcellNormalizedInputIntentConverter,
    NormalizedLoadcellInputIntent,
    RawLoadcellVectorRecord,
)
from xpotato_sim.schemas import RawInputFrame


def test_loadcell_normalization_converts_raw_input_frame_without_reordering_channels() -> None:
    frame = RawInputFrame(
        source="selfrionette",
        timestamp_s=2152.956,
        values=(-10.0, 0.6, 12.0, -15.0, 4.0, 0.0, 4.9),
        metadata={
            "source_kind": "selfrionette",
            "timestamp_ms": 2_152_956,
            "raw_line": "vector,2152956,-10.0,0.6,12.0,-15.0,4.0,0.0,4.9",
        },
    )
    config = LoadcellNormalizationConfig(scale=10.0, clamp_abs=1.0)
    converter = LoadcellNormalizedInputIntentConverter(config)

    intent = converter.convert(frame)

    assert isinstance(intent, NormalizedLoadcellInputIntent)
    assert intent.source == "selfrionette"
    assert intent.timestamp_s == pytest.approx(2152.956)
    assert intent.values == pytest.approx((-1.0, 0.06, 1.0, -1.0, 0.4, 0.0, 0.49))
    assert intent.active_channels == (0, 1, 2, 3, 4, 6)
    assert intent.metadata == frame.metadata
    assert intent.metadata is not frame.metadata


def test_selfrionette_normalization_does_not_apply_operational_deadzone() -> None:
    frame = RawInputFrame(
        source="selfrionette",
        timestamp_s=0.25,
        values=(0.01, -0.02, 0.049, -0.001, 0.051, 0.0, -0.049),
        metadata={"source_kind": "selfrionette"},
    )
    converter = LoadcellNormalizedInputIntentConverter(
        LoadcellNormalizationConfig(scale=1.0, clamp_abs=1.0)
    )

    intent = converter.convert(frame)

    assert intent.values == pytest.approx(
        (0.01, -0.02, 0.049, -0.001, 0.051, 0.0, -0.049)
    )
    assert intent.active_channels == (0, 1, 2, 3, 4, 6)


def test_loadcell_normalization_clamps_large_values_and_accepts_raw_records() -> None:
    record = RawLoadcellVectorRecord(
        timestamp_ms=1234,
        channels=(-2.5, -1.5, -1.0, 0.0, 1.0, 1.5, 2.5),
        raw_line="vector,1234,-2.5,-1.5,-1.0,0.0,1.0,1.5,2.5",
    )
    converter = LoadcellNormalizedInputIntentConverter(
        LoadcellNormalizationConfig(scale=1.0, clamp_abs=1.0)
    )

    intent = converter.convert(record)

    assert intent.source == "selfrionette"
    assert intent.timestamp_s == pytest.approx(1.234)
    assert intent.values == pytest.approx((-1.0, -1.0, -1.0, 0.0, 1.0, 1.0, 1.0))
    assert intent.active_channels == (0, 1, 2, 4, 5, 6)
    assert intent.metadata == {}


@pytest.mark.parametrize(
    "values",
    [
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
    ],
)
def test_loadcell_normalization_rejects_wrong_channel_count(values: tuple[float, ...]) -> None:
    frame = RawInputFrame(source="selfrionette", timestamp_s=0.0, values=values)

    with pytest.raises(ValueError, match="exactly 7 values"):
        LoadcellNormalizedInputIntentConverter().convert(frame)


def test_loadcell_normalization_rejects_non_finite_values() -> None:
    frame = RawInputFrame(
        source="selfrionette",
        timestamp_s=0.0,
        values=(1.0, float("nan"), 3.0, 4.0, 5.0, 6.0, 7.0),
    )

    with pytest.raises(ValueError, match="non-finite loadcell value"):
        LoadcellNormalizedInputIntentConverter().convert(frame)


@pytest.mark.parametrize(
    "config_kwargs, reason",
    [
        ({"scale": 0.0}, "scale must be positive"),
        ({"clamp_abs": 0.0}, "clamp_abs must be positive"),
        ({"channel_count": 8}, "channel_count must be exactly 7"),
    ],
)
def test_loadcell_normalization_rejects_invalid_config(
    config_kwargs: dict[str, float | int],
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        LoadcellNormalizationConfig(**config_kwargs)
