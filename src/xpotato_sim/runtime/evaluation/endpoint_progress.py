"""Measured endpoint progress evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from xpotato_sim.schemas.endpoint import EndpointProgressStatus

DEFAULT_REQUEST_NORM_TOLERANCE_M = 1e-12
DEFAULT_MEASURED_NORM_TOLERANCE_M = 1e-6
DEFAULT_MINIMUM_PROGRESS_RATIO = 0.5
DEFAULT_MINIMUM_DIRECTION_COSINE = 0.5


@dataclass(frozen=True, slots=True)
class EndpointProgressResult:
    status: EndpointProgressStatus
    signed_progress_m: float | None
    progress_ratio: float | None
    direction_cosine: float | None
    requested_norm_m: float | None
    measured_norm_m: float | None
    measurement_available: bool

    def to_metadata(self) -> dict[str, object]:
        return {
            "endpoint_progress_status": self.status,
            "endpoint_progress_signed_m": self.signed_progress_m,
            "endpoint_progress_ratio": self.progress_ratio,
            "endpoint_progress_direction_cosine": self.direction_cosine,
            "endpoint_progress_requested_norm_m": self.requested_norm_m,
            "endpoint_progress_measured_norm_m": self.measured_norm_m,
            "endpoint_progress_measurement_available": self.measurement_available,
        }


def _coerce_finite_vector3(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None

    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        return None
    if len(components) != 3 or not all(isfinite(component) for component in components):
        return None
    return components


def _norm(vector: Sequence[float]) -> float:
    return sqrt(sum(component * component for component in vector))


def _unavailable_result(*, requested_norm_m: float | None = None) -> EndpointProgressResult:
    return EndpointProgressResult(
        status="measurement_unavailable",
        signed_progress_m=None,
        progress_ratio=None,
        direction_cosine=None,
        requested_norm_m=requested_norm_m,
        measured_norm_m=None,
        measurement_available=False,
    )


def calculate_endpoint_progress(
    requested_delta_m: object,
    measured_delta_m: object | None,
    *,
    request_norm_tolerance_m: float = DEFAULT_REQUEST_NORM_TOLERANCE_M,
    measured_norm_tolerance_m: float = DEFAULT_MEASURED_NORM_TOLERANCE_M,
    minimum_progress_ratio: float = DEFAULT_MINIMUM_PROGRESS_RATIO,
    minimum_direction_cosine: float = DEFAULT_MINIMUM_DIRECTION_COSINE,
) -> EndpointProgressResult:
    thresholds = {
        "request_norm_tolerance_m": request_norm_tolerance_m,
        "measured_norm_tolerance_m": measured_norm_tolerance_m,
        "minimum_progress_ratio": minimum_progress_ratio,
        "minimum_direction_cosine": minimum_direction_cosine,
    }
    if not all(isfinite(value) for value in thresholds.values()):
        raise ValueError("endpoint progress thresholds must be finite")
    if request_norm_tolerance_m < 0.0 or measured_norm_tolerance_m < 0.0:
        raise ValueError("endpoint progress norm tolerances must be non-negative")
    if not -1.0 <= minimum_direction_cosine <= 1.0:
        raise ValueError("minimum_direction_cosine must be between -1 and 1")

    requested = _coerce_finite_vector3(requested_delta_m)
    if requested is None:
        return _unavailable_result()
    requested_norm_m = _norm(requested)

    if requested_norm_m <= request_norm_tolerance_m:
        measured_norm_m: float | None = None
        measurement_available = False
        if measured_delta_m is not None:
            measured = _coerce_finite_vector3(measured_delta_m)
            if measured is not None:
                measured_norm_m = _norm(measured)
                measurement_available = True
        return EndpointProgressResult(
            status="not_requested",
            signed_progress_m=None,
            progress_ratio=None,
            direction_cosine=None,
            requested_norm_m=requested_norm_m,
            measured_norm_m=measured_norm_m,
            measurement_available=measurement_available,
        )

    if measured_delta_m is None:
        return _unavailable_result(requested_norm_m=requested_norm_m)

    measured = _coerce_finite_vector3(measured_delta_m)
    if measured is None:
        return _unavailable_result(requested_norm_m=requested_norm_m)

    measured_norm_m = _norm(measured)
    requested_unit = tuple(component / requested_norm_m for component in requested)
    signed_progress_m = sum(
        measured[index] * requested_unit[index]
        for index in range(3)
    )
    progress_ratio = signed_progress_m / requested_norm_m

    if measured_norm_m <= measured_norm_tolerance_m:
        return EndpointProgressResult(
            status="insufficient_progress",
            signed_progress_m=signed_progress_m,
            progress_ratio=progress_ratio,
            direction_cosine=None,
            requested_norm_m=requested_norm_m,
            measured_norm_m=measured_norm_m,
            measurement_available=True,
        )

    direction_cosine = signed_progress_m / measured_norm_m
    if direction_cosine < minimum_direction_cosine:
        status: EndpointProgressStatus = "misaligned"
    elif progress_ratio < minimum_progress_ratio:
        status = "insufficient_progress"
    else:
        status = "progressing"

    return EndpointProgressResult(
        status=status,
        signed_progress_m=signed_progress_m,
        progress_ratio=progress_ratio,
        direction_cosine=direction_cosine,
        requested_norm_m=requested_norm_m,
        measured_norm_m=measured_norm_m,
        measurement_available=True,
    )


def endpoint_progress_metadata(
    requested_delta_m: object,
    measured_delta_m: object | None,
) -> Mapping[str, object]:
    return calculate_endpoint_progress(requested_delta_m, measured_delta_m).to_metadata()


__all__ = [
    "EndpointProgressResult",
    "EndpointProgressStatus",
    "calculate_endpoint_progress",
    "endpoint_progress_metadata",
]
