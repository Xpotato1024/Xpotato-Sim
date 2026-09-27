from __future__ import annotations

import math

import pytest

from xpotato_sim.runtime.evaluation.endpoint_progress import calculate_endpoint_progress


def test_zero_request_is_not_requested_without_fabricated_metrics() -> None:
    result = calculate_endpoint_progress((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    assert result.status == "not_requested"
    assert result.requested_norm_m == 0.0
    assert result.measured_norm_m == 0.0
    assert result.signed_progress_m is None
    assert result.progress_ratio is None
    assert result.direction_cosine is None
    assert result.measurement_available is True


def test_missing_or_non_finite_measurement_is_explicitly_unavailable() -> None:
    missing = calculate_endpoint_progress((1.0, 0.0, 0.0), None)
    non_finite = calculate_endpoint_progress((1.0, 0.0, 0.0), (math.nan, 0.0, 0.0))

    assert missing.status == non_finite.status == "measurement_unavailable"
    assert missing.measurement_available is non_finite.measurement_available is False
    assert missing.progress_ratio is non_finite.progress_ratio is None
    assert missing.direction_cosine is non_finite.direction_cosine is None


@pytest.mark.parametrize(
    ("requested", "measured"),
    (
        (None, (0.0, 0.0, 0.0)),
        ((math.inf, 0.0, 0.0), (0.0, 0.0, 0.0)),
        ((1.0, 0.0), (0.0, 0.0, 0.0)),
        ((1.0, 0.0, 0.0), "invalid"),
        ((1.0, 0.0, 0.0), (0.0, 0.0)),
    ),
)
def test_missing_malformed_or_non_finite_vectors_are_unavailable(
    requested: object,
    measured: object,
) -> None:
    result = calculate_endpoint_progress(requested, measured)

    assert result.status == "measurement_unavailable"
    assert result.measurement_available is False
    assert result.signed_progress_m is None
    assert result.progress_ratio is None
    assert result.direction_cosine is None


def test_near_zero_measurement_is_insufficient_and_has_no_direction_cosine() -> None:
    result = calculate_endpoint_progress((1e-3, 0.0, 0.0), (1e-7, 0.0, 0.0))

    assert result.status == "insufficient_progress"
    assert result.signed_progress_m == pytest.approx(1e-7)
    assert result.progress_ratio == pytest.approx(1e-4)
    assert result.direction_cosine is None


def test_low_alignment_precedes_low_progress_ratio() -> None:
    result = calculate_endpoint_progress((1e-3, 0.0, 0.0), (1e-5, 1e-4, 0.0))

    assert result.progress_ratio == pytest.approx(0.01)
    assert result.direction_cosine == pytest.approx(0.09950371902099892)
    assert result.status == "misaligned"


def test_aligned_low_progress_is_insufficient() -> None:
    result = calculate_endpoint_progress((1e-3, 0.0, 0.0), (4e-4, 0.0, 0.0))

    assert result.direction_cosine == pytest.approx(1.0)
    assert result.progress_ratio == pytest.approx(0.4)
    assert result.status == "insufficient_progress"


def test_aligned_sufficient_progress_is_progressing() -> None:
    result = calculate_endpoint_progress((1e-3, 0.0, 0.0), (8e-4, 0.0, 0.0))

    assert result.direction_cosine == pytest.approx(1.0)
    assert result.progress_ratio == pytest.approx(0.8)
    assert result.status == "progressing"


def test_reverse_progress_retains_negative_sign_and_is_misaligned() -> None:
    result = calculate_endpoint_progress((1e-3, 0.0, 0.0), (-8e-4, 0.0, 0.0))

    assert result.signed_progress_m == pytest.approx(-8e-4)
    assert result.progress_ratio == pytest.approx(-0.8)
    assert result.direction_cosine == pytest.approx(-1.0)
    assert result.status == "misaligned"


def test_metadata_field_names_are_stable_and_inputs_are_not_mutated() -> None:
    requested = [1e-3, 0.0, 0.0]
    measured = [8e-4, 0.0, 0.0]
    metadata = calculate_endpoint_progress(requested, measured).to_metadata()

    assert requested == [1e-3, 0.0, 0.0]
    assert measured == [8e-4, 0.0, 0.0]
    assert tuple(metadata) == (
        "endpoint_progress_status",
        "endpoint_progress_signed_m",
        "endpoint_progress_ratio",
        "endpoint_progress_direction_cosine",
        "endpoint_progress_requested_norm_m",
        "endpoint_progress_measured_norm_m",
        "endpoint_progress_measurement_available",
    )


def test_invalid_threshold_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="norm tolerances must be non-negative"):
        calculate_endpoint_progress(
            (1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            request_norm_tolerance_m=-1.0,
        )
