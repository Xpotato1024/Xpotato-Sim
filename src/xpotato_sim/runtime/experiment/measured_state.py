"""runtimeのactual measurementをfrozen initial-stateへ照合する。"""

from __future__ import annotations

import math
from collections.abc import Sequence


RUNTIME_MEASUREMENT_ABS_TOLERANCE = 1e-9


def _finite_components(values: Sequence[float]) -> tuple[float, ...] | None:
    try:
        result: list[float] = []
        for value in values:
            if isinstance(value, bool):
                return None
            converted = float(value)
            if not math.isfinite(converted):
                return None
            result.append(converted)
    except (TypeError, ValueError, OverflowError):
        return None
    return tuple(result)


def runtime_measurement_matches_frozen(
    actual: Sequence[float],
    expected: Sequence[float],
    *,
    allow_quaternion_sign_equivalence: bool = False,
) -> bool:
    """有限なruntime measurementをfrozen valueへruntime境界で照合する。

    canonical manifest、schema、logのself-validation toleranceとは分離する。
    quaternionの符号同値性はorientation measurementの呼び出し側だけが明示する。
    """

    actual_values = _finite_components(actual)
    expected_values = _finite_components(expected)
    if actual_values is None or expected_values is None:
        return False

    def matches(candidate: Sequence[float]) -> bool:
        return len(actual_values) == len(candidate) and all(
            math.isclose(
                left,
                right,
                rel_tol=0.0,
                abs_tol=RUNTIME_MEASUREMENT_ABS_TOLERANCE,
            )
            for left, right in zip(actual_values, candidate, strict=True)
        )

    if matches(expected_values):
        return True
    return allow_quaternion_sign_equivalence and matches(
        tuple(-value for value in expected_values)
    )


__all__ = [
    "RUNTIME_MEASUREMENT_ABS_TOLERANCE",
    "runtime_measurement_matches_frozen",
]
