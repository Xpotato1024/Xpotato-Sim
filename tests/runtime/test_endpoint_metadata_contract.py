from __future__ import annotations

from pathlib import Path

from xpotato_sim.schemas.endpoint import (
    ControlFrameResolutionStatus,
    EndpointMetadata,
    EndpointProgressStatus,
)
from xpotato_sim.runtime.evaluation.endpoint_progress import calculate_endpoint_progress
from xpotato_sim.runtime.control.viewer_motion_policy import build_viewer_local_motion_metadata


def test_endpoint_metadata_contract_contains_canonical_and_compatibility_fields() -> None:
    annotations = EndpointMetadata.__annotations__
    for field in (
        "requested_control_frame",
        "control_frame",
        "resolved_world_endpoint_velocity_m_s",
        "endpoint_velocity_m_s",
        "endpoint_delta_requested_m",
        "endpoint_delta_m",
        "endpoint_delta_achieved_m",
        "actual_tip_delta_m",
    ):
        assert field in annotations


def test_current_tip_position_provenance_is_explicitly_overloaded_compatibility_metadata() -> None:
    document = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "contracts"
        / "endpoint-metadata-vocabulary.md"
    ).read_text(encoding="utf-8")

    assert "overloaded compatibility field" in document
    assert "stateful viewer command endpoint anchor" in document
    assert "caller-supplied endpoint anchor" in document
    assert "MuJoCo physical measurementではない" in document
    assert "actual_tip_delta_m" in document


def test_control_frame_and_progress_vocabularies_are_closed() -> None:
    assert ControlFrameResolutionStatus.__args__ == (
        "world_passthrough",
        "tool_orientation_resolved",
        "tool_orientation_unavailable",
        "invalid_control_frame_defaulted",
    )
    assert EndpointProgressStatus.__args__ == (
        "not_requested",
        "measurement_unavailable",
        "insufficient_progress",
        "misaligned",
        "progressing",
    )


def test_canonical_velocity_precedes_alias_and_failure_clears_stale_values() -> None:
    resolved = build_viewer_local_motion_metadata(
        {
            "control_frame": "world",
            "local_endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "resolved_world_endpoint_velocity_m_s": (0.2, 0.0, 0.0),
            "endpoint_velocity_m_s": (0.9, 0.0, 0.0),
        },
        dt_s=1.0 / 60.0,
    )
    assert resolved["resolved_world_endpoint_velocity_m_s"] == (0.2, 0.0, 0.0)
    assert resolved["endpoint_velocity_m_s"] == (0.2, 0.0, 0.0)

    unavailable = build_viewer_local_motion_metadata(
        {
            "control_frame": "tool",
            "local_endpoint_velocity_m_s": (0.1, 0.0, 0.0),
            "resolved_world_endpoint_velocity_m_s": (0.2, 0.0, 0.0),
            "endpoint_velocity_m_s": (0.9, 0.0, 0.0),
        },
        dt_s=1.0 / 60.0,
    )
    assert unavailable["control_frame_resolution_status"] == "tool_orientation_unavailable"
    assert "resolved_world_endpoint_velocity_m_s" not in unavailable
    assert "endpoint_velocity_m_s" not in unavailable
    assert "endpoint_delta_m" not in unavailable


def test_motion_status_does_not_imply_measured_progress() -> None:
    progress = calculate_endpoint_progress((0.1, 0.0, 0.0), (0.0, 0.0, 0.0))

    assert progress.status == "insufficient_progress"
    assert progress.status != "progressing"
