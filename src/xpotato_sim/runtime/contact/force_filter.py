"""worldやTaskから独立した力ベクトルfilter。旧contactと新sceneで数式を共有する。"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class ForceFilterParameters(Protocol):
    """入口で検証済みのSI parameterだけを要求する。"""
    deadband_n: float
    smoothing_window_samples: int
    low_pass_time_constant_s: float
    rate_limit_n_per_s: float | None
    magnitude_clamp_n: float | None


def _magnitude(value: Sequence[float]) -> float:
    return math.hypot(value[0], value[1], value[2])


def _clean_vector(value: Sequence[float]) -> tuple[float, float, float]:
    if len(value) != 3 or not all(math.isfinite(v) for v in value):
        raise ValueError("force vector must be finite")
    return tuple(0.0 if v == 0.0 else float(v) for v in value)


_ZERO3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class FilteredForce:
    """原力を上書きせず、実際の加工を返す。"""
    force: tuple[float, float, float]
    filtered: bool
    deadbanded: bool
    rate_limited: bool
    clamped: bool


class ForceVectorFilter:
    """有界windowを持つ既存filter。clock・epoch・品質のownerはcaller。"""
    def __init__(self):
        self._window: list[tuple[float, float, float]] = []
        self._low_pass: tuple[float, float, float] | None = None
        self._last_output: tuple[float, float, float] | None = None

    def reset(self):
        self._window.clear()
        self._low_pass = self._last_output = None

    def apply(self, raw_output: tuple[float, float, float], *,
              config: ForceFilterParameters, sample_delta: float | None) -> FilteredForce:
        """deadband→平均→LPF→rate limit→clampの順序・seed規則を維持する。"""
        magnitude = _magnitude(raw_output)
        if not math.isfinite(magnitude):
            raise ValueError("raw contact force magnitude is invalid")
        deadbanded = magnitude > 0.0 and magnitude <= config.deadband_n
        filtered_input = _ZERO3 if magnitude <= config.deadband_n else raw_output
        self._window.append(filtered_input)
        if len(self._window) > config.smoothing_window_samples:
            del self._window[0]
        smoothed = tuple(
            math.fsum(sample_value[axis] for sample_value in self._window)
            / len(self._window)
            for axis in range(3)
        )
        filtered = (
            config.smoothing_window_samples > 1
            or config.low_pass_time_constant_s > 0.0
        )

        time_constant = config.low_pass_time_constant_s
        if time_constant == 0.0:
            low_pass = _clean_vector(smoothed)
        elif self._low_pass is None or sample_delta is None:
            low_pass = _clean_vector(smoothed)
        else:
            alpha = 1.0 - math.exp(-sample_delta / time_constant)
            low_pass = _clean_vector(
                tuple(
                    self._low_pass[axis]
                    + alpha * (smoothed[axis] - self._low_pass[axis])
                    for axis in range(3)
                )
            )
        self._low_pass = low_pass
        output = low_pass
        rate_limited = False
        rate_limit = config.rate_limit_n_per_s
        if (
            rate_limit is not None
            and self._last_output is not None
            and sample_delta is not None
        ):
            delta = tuple(output[axis] - self._last_output[axis] for axis in range(3))
            delta_magnitude = _magnitude(delta)
            permitted_delta = rate_limit * sample_delta
            if not math.isfinite(permitted_delta):
                raise ValueError("rate-limit interval is invalid")
            if delta_magnitude > permitted_delta and delta_magnitude > 0.0:
                scale = permitted_delta / delta_magnitude
                output = _clean_vector(
                    tuple(
                        self._last_output[axis] + delta[axis] * scale
                        for axis in range(3)
                    )
                )
                rate_limited = True

        clamped = False
        maximum = config.magnitude_clamp_n
        output_magnitude = _magnitude(output)
        if not math.isfinite(output_magnitude):
            raise ValueError("filtered force magnitude is invalid")
        if maximum is not None and output_magnitude > maximum:
            scale = maximum / output_magnitude
            output = _clean_vector(
                tuple(component * scale for component in output)
            )
            clamped = True
        output = _clean_vector(output)
        self._last_output = output
        return FilteredForce(output, filtered, deadbanded, rate_limited, clamped)
