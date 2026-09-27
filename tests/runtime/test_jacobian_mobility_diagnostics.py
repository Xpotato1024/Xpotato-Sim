from __future__ import annotations

import math

import numpy as np
import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.jacobian_mobility import build_delta_metrics, summarize_jacobian


def test_full_rank_metrics_are_deterministic() -> None:
    metrics = summarize_jacobian(np.eye(3))
    assert metrics.numeric_rank == 3
    assert metrics.effective_rank == 3
    assert metrics.singular_values == pytest.approx((1.0, 1.0, 1.0))
    assert metrics.condition_number == pytest.approx(1.0)
    assert metrics.row_norms == pytest.approx((1.0, 1.0, 1.0))
    assert metrics.column_norms == pytest.approx((1.0, 1.0, 1.0))
    assert metrics.manipulability == pytest.approx(1.0)


def test_rank_deficient_and_zero_row_metrics_use_infinity_semantics() -> None:
    metrics = summarize_jacobian(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)))
    assert metrics.numeric_rank == 2
    assert metrics.effective_rank == 2
    assert math.isinf(metrics.condition_number)
    assert metrics.manipulability == 0.0
    assert metrics.row_norms[2] == 0.0


def test_delta_metrics_handle_signed_progress_and_zero_direction() -> None:
    result = build_delta_metrics((1.0, 0.0, 0.0), (0.1,), (0.1,), (0.1, 0.0, 0.0), (0.2, 0.0, 0.0))
    assert result.signed_progress_m == pytest.approx(0.2)
    assert result.progress_ratio == pytest.approx(0.2)
    assert result.direction_cosine == pytest.approx(1.0)
    zero = build_delta_metrics((0.0, 0.0, 0.0), (0.1,), (0.1,), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert zero.progress_ratio is None
    assert zero.direction_cosine is None
    near_zero = build_delta_metrics((1.0, 0.0, 0.0), (0.1,), (0.1,), (0.0, 0.0, 0.0), (1e-15, 0.0, 0.0))
    assert near_zero.direction_cosine is None


def test_direction_cosine_tolerance_must_be_finite_and_positive() -> None:
    with pytest.raises(ValueError):
        build_delta_metrics((1.0, 0.0, 0.0), (0.1,), (0.1,), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), direction_cosine_tolerance=0.0)


def test_nonfinite_jacobian_is_rejected() -> None:
    with pytest.raises(ValueError):
        summarize_jacobian(((1.0, float("nan")), (0.0, 1.0), (0.0, 0.0)))
