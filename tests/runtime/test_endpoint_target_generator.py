from __future__ import annotations

import pytest

from xpotato_sim.runtime.control.endpoint_target_generator import (
    EndpointTargetGeneratorConfig,
    EndpointTargetGeneratorInput,
    EndpointTargetGeneratorState,
    endpoint_target_generation_result_to_metadata,
    generate_endpoint_target,
)


def _config(**overrides: object) -> EndpointTargetGeneratorConfig:
    values = {
        "gain_m_per_s": 0.2,
        "deadzone": 0.05,
        "max_step_m": 0.01,
        "workspace_min_m": (-0.1, -0.1, -0.1),
        "workspace_max_m": (0.1, 0.1, 0.1),
        "smoothing_alpha": 1.0,
    }
    values.update(overrides)
    return EndpointTargetGeneratorConfig(**values)  # type: ignore[arg-type]


def _input(**overrides: object) -> EndpointTargetGeneratorInput:
    values = {
        "current_tip_position_m": (0.01, 0.02, 0.03),
        "input_vector": (1.0, 0.0, 0.0),
        "dt_s": 0.05,
        "control_frame": "world",
    }
    values.update(overrides)
    return EndpointTargetGeneratorInput(**values)  # type: ignore[arg-type]


def _state(**overrides: object) -> EndpointTargetGeneratorState:
    values = {
        "previous_desired_endpoint_m": (0.0, 0.0, 0.0),
        "last_valid_target_position_m": (0.0, 0.0, 0.0),
        "previous_rejected": False,
    }
    values.update(overrides)
    return EndpointTargetGeneratorState(**values)  # type: ignore[arg-type]


def test_initializes_from_current_tip_position_m() -> None:
    result = generate_endpoint_target(
        _config(),
        _state(previous_desired_endpoint_m=None, last_valid_target_position_m=None),
        _input(current_tip_position_m=(0.4, 0.5, 0.6)),
    )

    assert result.desired_endpoint_m == (0.4, 0.5, 0.6)
    assert result.target_delta_m == (0.0, 0.0, 0.0)
    assert result.target_generation_status == "initialized"
    assert result.target_generation_reason == "initial_current_tip"
    assert result.last_valid_target_position_m == (0.4, 0.5, 0.6)


def test_deadzone_holds_previous_target() -> None:
    result = generate_endpoint_target(
        _config(deadzone=0.1),
        _state(previous_desired_endpoint_m=(0.2, 0.0, 0.0), last_valid_target_position_m=(0.19, 0.0, 0.0)),
        _input(input_vector=(0.03, 0.04, 0.0)),
    )

    assert result.desired_endpoint_m == (0.2, 0.0, 0.0)
    assert result.target_delta_m == (0.0, 0.0, 0.0)
    assert result.held is True
    assert result.target_generation_status == "held"
    assert result.target_generation_reason == "deadzone"
    assert result.last_valid_target_position_m == (0.19, 0.0, 0.0)


def test_normal_input_advances_previous_target_by_gain_dt() -> None:
    result = generate_endpoint_target(
        _config(gain_m_per_s=0.2, max_step_m=0.1),
        _state(previous_desired_endpoint_m=(0.0, 0.0, 0.0)),
        _input(input_vector=(0.5, 0.0, 0.0), dt_s=0.5),
    )

    assert result.desired_endpoint_m == pytest.approx((0.05, 0.0, 0.0), abs=1e-9)
    assert result.target_delta_m == pytest.approx((0.05, 0.0, 0.0), abs=1e-9)
    assert result.target_generation_status == "moved"
    assert result.target_generation_reason == "input_motion"


def test_input_magnitude_over_one_is_normalized_deterministically() -> None:
    result = generate_endpoint_target(
        _config(gain_m_per_s=0.2, max_step_m=0.1),
        _state(previous_desired_endpoint_m=(0.0, 0.0, 0.0)),
        _input(input_vector=(2.0, 0.0, 0.0), dt_s=0.5),
    )

    assert result.desired_endpoint_m == pytest.approx((0.1, 0.0, 0.0), abs=1e-9)
    assert result.target_delta_m == pytest.approx((0.1, 0.0, 0.0), abs=1e-9)


def test_max_step_clamps_delta() -> None:
    result = generate_endpoint_target(
        _config(gain_m_per_s=1.0, max_step_m=0.02),
        _state(previous_desired_endpoint_m=(0.0, 0.0, 0.0)),
        _input(input_vector=(1.0, 0.0, 0.0), dt_s=1.0),
    )

    assert result.desired_endpoint_m == pytest.approx((0.02, 0.0, 0.0), abs=1e-9)
    assert result.target_delta_m == pytest.approx((0.02, 0.0, 0.0), abs=1e-9)
    assert result.clamped is True
    assert result.target_generation_status == "clamped"
    assert result.target_generation_reason == "max_step"


def test_workspace_projection_clamps_candidate_into_bounds() -> None:
    result = generate_endpoint_target(
        _config(gain_m_per_s=1.0, max_step_m=1.0, workspace_max_m=(0.05, 0.05, 0.05)),
        _state(previous_desired_endpoint_m=(0.04, 0.0, 0.0)),
        _input(input_vector=(1.0, 1.0, 0.0), dt_s=0.1),
    )

    assert result.desired_endpoint_m == pytest.approx((0.05, 0.05, 0.0), abs=1e-9)
    assert result.target_delta_m == pytest.approx((0.01, 0.05, 0.0), abs=1e-9)
    assert result.projected is True
    assert result.target_generation_status == "projected"
    assert result.target_generation_reason == "workspace_projection"


def test_previous_rejection_holds_last_valid_target() -> None:
    result = generate_endpoint_target(
        _config(),
        _state(
            previous_desired_endpoint_m=(0.08, 0.0, 0.0),
            last_valid_target_position_m=(0.03, 0.0, 0.0),
            previous_rejected=True,
        ),
        _input(input_vector=(1.0, 0.0, 0.0), current_tip_position_m=(0.01, 0.0, 0.0)),
    )

    assert result.desired_endpoint_m == (0.03, 0.0, 0.0)
    assert result.target_delta_m == (0.0, 0.0, 0.0)
    assert result.held is True
    assert result.target_generation_status == "held_after_rejection"
    assert result.target_generation_reason == "previous_rejection"


def test_previous_rejection_falls_back_to_current_tip_when_last_valid_missing() -> None:
    result = generate_endpoint_target(
        _config(),
        _state(last_valid_target_position_m=None, previous_rejected=True),
        _input(current_tip_position_m=(0.01, 0.02, 0.03)),
    )

    assert result.desired_endpoint_m == (0.01, 0.02, 0.03)
    assert result.last_valid_target_position_m == (0.01, 0.02, 0.03)


def test_invalid_dt_raises_value_error() -> None:
    with pytest.raises(ValueError, match="dt_s must be positive"):
        generate_endpoint_target(_config(), _state(), _input(dt_s=0.0))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"gain_m_per_s": -0.1}, "gain_m_per_s must be non-negative"),
        ({"deadzone": -0.1}, "deadzone must be non-negative"),
        ({"max_step_m": 0.0}, "max_step_m must be positive"),
        ({"smoothing_alpha": 1.1}, "smoothing_alpha must be between"),
        ({"workspace_min_m": (1.0, 0.0, 0.0), "workspace_max_m": (0.0, 0.0, 0.0)}, "workspace_min_m must be <="),
    ],
)
def test_invalid_config_raises_value_error(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        generate_endpoint_target(_config(**overrides), _state(), _input())


def test_metadata_helper_preserves_desired_endpoint_and_status_fields() -> None:
    result = generate_endpoint_target(
        _config(gain_m_per_s=0.2, max_step_m=0.1),
        _state(previous_desired_endpoint_m=(0.0, 0.0, 0.0)),
        _input(input_vector=(0.0, 0.0, 1.0), dt_s=0.5),
    )

    metadata = endpoint_target_generation_result_to_metadata(result)

    assert metadata["desired_endpoint_m"] == pytest.approx((0.0, 0.0, 0.1), abs=1e-9)
    assert metadata["target_generation_status"] == "moved"
    assert metadata["target_generation_reason"] == "input_motion"
    assert metadata["target_delta_m"] == pytest.approx((0.0, 0.0, 0.1), abs=1e-9)
    assert metadata["target_generation_clamped"] is False
    assert metadata["target_generation_projected"] is False
    assert metadata["target_generation_held"] is False
    assert metadata["last_valid_target_position_m"] == pytest.approx((0.0, 0.0, 0.1), abs=1e-9)
    assert "target_position_m" not in metadata


def test_repeated_small_inputs_do_not_accumulate_beyond_workspace_bounds() -> None:
    config = _config(gain_m_per_s=0.2, max_step_m=0.01, workspace_max_m=(0.03, 0.1, 0.1))
    state = _state(previous_desired_endpoint_m=(0.0, 0.0, 0.0), last_valid_target_position_m=(0.0, 0.0, 0.0))
    result = None

    for _ in range(20):
        result = generate_endpoint_target(config, state, _input(input_vector=(1.0, 0.0, 0.0), dt_s=0.05))
        state = EndpointTargetGeneratorState(
            previous_desired_endpoint_m=result.desired_endpoint_m,
            last_valid_target_position_m=result.last_valid_target_position_m,
        )

    assert result is not None
    assert result.desired_endpoint_m == pytest.approx((0.03, 0.0, 0.0), abs=1e-9)
    assert result.projected is True
    assert result.target_generation_status == "projected"


def test_smoothing_alpha_scales_motion_before_max_step() -> None:
    result = generate_endpoint_target(
        _config(gain_m_per_s=0.2, max_step_m=0.1, smoothing_alpha=0.5),
        _state(previous_desired_endpoint_m=(0.0, 0.0, 0.0)),
        _input(input_vector=(1.0, 0.0, 0.0), dt_s=0.5),
    )

    assert result.desired_endpoint_m == pytest.approx((0.05, 0.0, 0.0), abs=1e-9)
