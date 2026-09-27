from __future__ import annotations

import math

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.neutral_initial_pose import (
    validate_candidate_qpos,
)


@pytest.mark.parametrize(
    ("value", "message"),
    (
        ((0.0, 0.0), "length mismatch"),
        ((0.0, False, 0.0, 0.0), "bool is not numeric"),
        ((0.0, math.nan, 0.0, 0.0), "must be finite"),
        ((0.0, math.inf, 0.0, 0.0), "must be finite"),
        ("0 0 0 0", "numeric sequence"),
    ),
)
def test_candidate_validation_rejects_invalid_values(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_candidate_qpos(value, expected_length=4)
