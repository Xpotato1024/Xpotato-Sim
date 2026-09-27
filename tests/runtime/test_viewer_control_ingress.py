from __future__ import annotations

import pytest

from xpotato_sim.runtime.control.viewer_control_ingress import (
    build_viewer_input_source,
    ingest_viewer_control_message,
)
from xpotato_sim.plugins.input_sources.viewer import viewer_health
from xpotato_sim.runtime.experiment.input_source import InputSourceHealthStatus
from xpotato_sim.schemas import ViewerControlMessageError, ViewerControlKeyboardMessage, ViewerControlMessage


def test_viewer_control_ingress_validates_json_before_source_update() -> None:
    source = build_viewer_input_source()

    with pytest.raises(ViewerControlMessageError, match="malformed JSON"):
        ingest_viewer_control_message(source, "{not json")

    assert source.last_control_message is None
    assert source.read_frame().metadata["source_active"] is False


def test_malformed_ingress_invalidates_active_source_and_recovers_on_valid_message() -> None:
    source = build_viewer_input_source(clock=lambda: 0.0)
    active = ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=1.0,
        source_kind="keyboard",
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=("KeyW",),
            key_state={"KeyW": True},
            focus_state="focused",
            zero_state=False,
        ),
    )
    ingest_viewer_control_message(source, active)

    with pytest.raises(ViewerControlMessageError, match="malformed JSON"):
        ingest_viewer_control_message(source, "{not json")

    invalid = source.read_frame()
    assert invalid.metadata["source_active"] is False
    assert invalid.metadata["source_health_status"] == "invalid"
    assert invalid.metadata["stale_reason"] == "Invalid viewer control message: malformed JSON"
    assert viewer_health(source).status is InputSourceHealthStatus.INVALID

    recovered = ingest_viewer_control_message(source, active)
    assert recovered.metadata["source_active"] is True
    assert "source_health_status" not in recovered.metadata
    assert viewer_health(source).status is InputSourceHealthStatus.ACTIVE


def test_provider_schema_failure_invalidates_source_before_source_object_update() -> None:
    source = build_viewer_input_source(clock=lambda: 0.0)
    with pytest.raises(ViewerControlMessageError, match="provider identity"):
        ingest_viewer_control_message(
            source,
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "provider_id": "gamepad/v1",
                "provider_schema": "viewer_gamepad_sample/v1",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
            },
        )
    source.read_frame()
    assert viewer_health(source).status is InputSourceHealthStatus.INVALID
