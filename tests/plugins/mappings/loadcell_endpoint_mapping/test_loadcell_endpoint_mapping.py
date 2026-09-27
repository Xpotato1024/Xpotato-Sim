from __future__ import annotations

import math

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import NormalizedLoadcellInputIntent
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import (
    LoadcellEndpointMappingConfig,
    LoadcellEndpointMotionCommandConverter,
    build_motion_command_from_normalized_loadcell_intent,
)
from xpotato_sim.schemas import MotionCommand


def test_loadcell_endpoint_mapping_converts_to_desired_endpoint_and_preserves_metadata() -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=1.25,
        values=(0.2, -0.3, 0.4, 0.1, 0.0, 0.0, 0.0),
        active_channels=(0, 1, 2, 3),
        metadata={"origin": "unit", "sample_id": 7},
    )
    config = LoadcellEndpointMappingConfig(
        channel_axis_weights=(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ),
        max_delta_m=0.03,
        gain_m=0.01,
    )

    command = LoadcellEndpointMotionCommandConverter(config).convert(
        intent,
        current_tip_position_m=(0.4, 0.5, 0.6),
    )

    assert isinstance(command, MotionCommand)
    assert command.timestamp_s == pytest.approx(1.25)
    assert command.target is None
    assert command.joint is None
    assert command.metadata is not intent.metadata
    assert command.metadata["origin"] == "unit"
    assert command.metadata["sample_id"] == 7
    assert command.metadata["active_channels"] == (0, 1, 2, 3)
    assert command.metadata["current_tip_position_m"] == (0.4, 0.5, 0.6)
    assert command.metadata["endpoint_delta_m"] == pytest.approx((0.003, -0.003, 0.004))
    assert command.metadata["desired_endpoint_m"] == pytest.approx((0.403, 0.497, 0.604))
    assert "target_position_m" not in command.metadata


def test_default_loadcell_endpoint_mapping_is_safe_and_no_op() -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=2.0,
        values=(0.9, -0.8, 0.7, -0.6, 0.5, -0.4, 0.3),
        active_channels=(0, 2, 4, 6),
        metadata={"origin": "default"},
    )

    command = LoadcellEndpointMotionCommandConverter().convert(
        intent,
        current_tip_position_m=(0.1, 0.2, 0.3),
    )

    assert command.metadata["endpoint_delta_m"] == (0.0, 0.0, 0.0)
    assert command.metadata["desired_endpoint_m"] == (0.1, 0.2, 0.3)
    assert command.metadata["active_channels"] == (0, 2, 4, 6)
    assert "target_position_m" not in command.metadata


def test_loadcell_endpoint_mapping_clamps_endpoint_delta() -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=3.0,
        values=(0.05, -0.04, 0.1, 0.0, 0.0, 0.0, 0.0),
        active_channels=(0, 1, 2),
        metadata={"origin": "clamp"},
    )
    config = LoadcellEndpointMappingConfig(
        channel_axis_weights=(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ),
        max_delta_m=0.03,
        gain_m=1.0,
    )

    command = build_motion_command_from_normalized_loadcell_intent(
        intent,
        current_tip_position_m=(0.4, 0.5, 0.6),
        config=config,
    )

    assert command.metadata["endpoint_delta_m"] == pytest.approx((0.03, -0.03, 0.03))
    assert command.metadata["desired_endpoint_m"] == pytest.approx((0.43, 0.47, 0.63))
    assert "target_position_m" not in command.metadata


@pytest.mark.parametrize(
    "values",
    [
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
    ],
)
def test_loadcell_endpoint_mapping_rejects_wrong_channel_count(values: tuple[float, ...]) -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=4.0,
        values=values,  # type: ignore[arg-type]
        metadata={"origin": "bad-count"},
    )

    with pytest.raises(ValueError, match="exactly 7 values"):
        LoadcellEndpointMotionCommandConverter().convert(
            intent,
            current_tip_position_m=(0.1, 0.2, 0.3),
        )


@pytest.mark.parametrize(
    "current_tip_position_m",
    [
        (0.1, 0.2),
        (0.1, 0.2, 0.3, 0.4),
    ],
)
def test_loadcell_endpoint_mapping_rejects_wrong_current_tip_position_length(
    current_tip_position_m: tuple[float, ...],
) -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=5.0,
        values=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7),
        metadata={"origin": "bad-tip"},
    )

    with pytest.raises(ValueError, match="current_tip_position_m must contain exactly three values"):
        LoadcellEndpointMotionCommandConverter().convert(
            intent,
            current_tip_position_m=current_tip_position_m,
        )


@pytest.mark.parametrize(
    "config_kwargs, reason",
    [
        (
            {
                "channel_axis_weights": (
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                    (0.0, 0.0, 1.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                )
            },
            "exactly 7 channel weights",
        ),
        (
            {
                "channel_axis_weights": (
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                    (0.0, 0.0, 1.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0),
                )
            },
            "must contain exactly three values",
        ),
        (
            {
                "channel_axis_weights": (
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                    (0.0, 0.0, 1.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, math.nan),
                )
            },
            "must contain only finite values",
        ),
        (
            {"gain_m": -0.01},
            "gain_m must be non-negative",
        ),
        (
            {"max_delta_m": 0.0},
            "max_delta_m must be positive",
        ),
    ],
)
def test_loadcell_endpoint_mapping_rejects_invalid_mapping_config(
    config_kwargs: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        LoadcellEndpointMappingConfig(**config_kwargs)


def test_loadcell_endpoint_mapping_rejects_non_finite_input() -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=6.0,
        values=(0.1, math.nan, 0.3, 0.4, 0.5, 0.6, 0.7),
        metadata={"origin": "non-finite"},
    )

    with pytest.raises(ValueError, match="non-finite loadcell value"):
        LoadcellEndpointMotionCommandConverter().convert(
            intent,
            current_tip_position_m=(0.1, 0.2, 0.3),
        )


def test_loadcell_endpoint_mapping_rejects_non_finite_current_tip_position() -> None:
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=7.0,
        values=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7),
        metadata={"origin": "non-finite-tip"},
    )

    with pytest.raises(ValueError, match="current_tip_position_m must contain only finite values"):
        LoadcellEndpointMotionCommandConverter().convert(
            intent,
            current_tip_position_m=(0.1, math.inf, 0.3),
        )


def test_loadcell_endpoint_mapping_owns_operational_deadzone() -> None:
    config = LoadcellEndpointMappingConfig(
        channel_axis_weights=(
            (1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ),
        gain_m=1.0,
        max_delta_m=0.03,
        operational_deadzone=0.05,
    )
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=0.0,
        values=(0.049, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )

    command = LoadcellEndpointMotionCommandConverter(config).convert(
        intent,
        current_tip_position_m=(0.1, 0.2, 0.3),
    )

    assert command.metadata["endpoint_delta_m"] == pytest.approx((0.0, 0.0, 0.0))
    assert command.metadata["desired_endpoint_m"] == pytest.approx((0.1, 0.2, 0.3))
