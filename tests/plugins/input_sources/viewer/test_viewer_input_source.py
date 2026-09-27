from __future__ import annotations

import pytest

from xpotato_sim.plugins.input_sources.viewer import (
    DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS,
    DEFAULT_VIEWER_SAFE_ENDPOINT_M,
    ViewerInputSource,
    viewer_health,
)
from xpotato_sim.runtime.experiment.input_source import InputSourceHealthStatus
from xpotato_sim.schemas import (
    ViewerControlGamepadButtonMessage,
    ViewerControlGamepadMessage,
    ViewerControlKeyboardMessage,
    ViewerControlMessage,
)


class FakeClock:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)
def keyboard_message(
    timestamp_s: float,
    *key_codes: str,
    focus_state: str = "focused",
    zero_state: bool | None = None,
    control_frame: str = "world",
) -> ViewerControlMessage:
    return ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=timestamp_s,
        source_kind="keyboard",
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=key_codes,
            key_state={key_code: True for key_code in key_codes},
            focus_state=focus_state,  # type: ignore[arg-type]
            zero_state=not key_codes if zero_state is None else zero_state,
        ),
        metadata={"control_frame": control_frame},
    )


def gamepad_message(
    timestamp_s: float,
    axes: tuple[float, ...],
    *,
    buttons: tuple[bool, ...] = (),
    connected: bool = True,
    stale: bool = False,
    zero_state: bool = False,
    control_frame: str = "world",
    raw_axes: tuple[float, ...] | None = None,
) -> ViewerControlMessage:
    return ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=timestamp_s,
        source_kind="gamepad",
        gamepad=ViewerControlGamepadMessage(
            connected=connected,
            index=0,
            id="pad-1",
            raw_axes=raw_axes,
            axes=axes,
            buttons=tuple(
                ViewerControlGamepadButtonMessage(pressed=pressed, value=float(pressed))
                for pressed in buttons
            ),
            stale=stale,
            zero_state=zero_state,
        ),
        metadata={"control_frame": control_frame},
    )


def test_viewer_source_frame_does_not_project_mapping_parameters() -> None:
    frame = ViewerInputSource(clock=lambda: 0.0).read_frame()
    assert "viewer_mapping_parameters" not in frame.metadata


def test_viewer_input_source_emits_raw_canonical_sample_before_ingest() -> None:
    source = ViewerInputSource(clock=lambda: 0.0)

    frame = source.read_frame()

    assert frame.source == "viewer"
    assert frame.metadata["source_kind"] == "viewer"
    assert frame.metadata["source_active"] is False
    assert frame.metadata["command_age_ms"] == 0
    assert frame.metadata["stale_reason"] == "source_inactive"
    assert frame.values == ()
    assert frame.buttons == ()
    assert "axis_values" not in frame.metadata
    assert "endpoint_velocity_m_s" not in frame.metadata


def test_viewer_source_health_covers_disconnect_and_stale_timeout() -> None:
    clock = FakeClock((30.0, 30.0))
    source = ViewerInputSource(clock=clock, timeout_ms=250)
    source.ingest_control_message(
        gamepad_message(4.0, (0.8, 0.0, 0.0), connected=False, zero_state=True)
    )
    disconnected_frame = source.read_frame()
    assert disconnected_frame.metadata["source_active"] is False
    assert disconnected_frame.metadata["stale_reason"] == "gamepad_inactive"
    assert viewer_health(source).status is InputSourceHealthStatus.DISCONNECTED

    raw_source = ViewerInputSource(clock=lambda: 30.0, timeout_ms=250)
    raw_disconnected = raw_source.ingest_control_message(
        gamepad_message(
            4.1,
            (0.0, 0.0, 0.0),
            raw_axes=(0.8, 0.0, 0.0),
            connected=False,
            zero_state=False,
        )
    )
    assert raw_disconnected.metadata["source_active"] is False
    assert raw_disconnected.metadata["viewer_input_sample"]["gamepad"]["raw_axes"] == (
        0.8,
        0.0,
        0.0,
    )
    assert viewer_health(raw_source).status is InputSourceHealthStatus.DISCONNECTED

    source = ViewerInputSource(clock=FakeClock((40.0, 40.301)), timeout_ms=250)
    source.ingest_control_message(keyboard_message(5.0, "KeyW"))
    stale_frame = source.read_frame()
    assert stale_frame.metadata["source_active"] is False
    assert stale_frame.metadata["command_age_ms"] == 301
    assert stale_frame.metadata["stale_reason"] == "command_age_ms_exceeded_timeout_250"


def test_viewer_source_invalid_provider_is_reported_as_invalid_health() -> None:
    source = ViewerInputSource(clock=lambda: 0.0)
    with pytest.raises(ValueError, match="provider"):
        source.ingest_control_message(
            ViewerControlMessage(
                type="viewer_control_message",
                timestamp_s=1.0,
                source_kind="keyboard",
                provider_id="gamepad/v1",
                provider_schema="viewer_gamepad_sample/v1",
                keyboard=ViewerControlKeyboardMessage(
                    active_key_codes=("KeyW",),
                    key_state={"KeyW": True},
                    focus_state="focused",
                    zero_state=False,
                ),
            )
        )
    source.read_frame()
    assert viewer_health(source).status is InputSourceHealthStatus.INVALID


def test_viewer_source_rejects_malformed_provider_identity_without_fallback() -> None:
    source = ViewerInputSource(clock=lambda: 0.0)
    with pytest.raises(ValueError, match="provider"):
        source.ingest_control_message(
            ViewerControlMessage(
                type="viewer_control_message",
                timestamp_s=1.0,
                source_kind="keyboard",
                provider_id="gamepad/v1",
                provider_schema="viewer_gamepad_sample/v1",
                keyboard=ViewerControlKeyboardMessage(
                    active_key_codes=("KeyW",),
                    key_state={"KeyW": True},
                    focus_state="focused",
                    zero_state=False,
                ),
            )
        )


def test_viewer_source_endpoint_accessor_is_only_compatibility_state() -> None:
    source = ViewerInputSource(clock=lambda: 0.0)
    source.rebase_current_endpoint_m((0.2, 0.3, 0.4))
    assert source.current_endpoint_m == (0.2, 0.3, 0.4)
    frame = source.ingest_control_message(keyboard_message(6.0, "Space"))
    assert "desired_endpoint_m" not in frame.metadata
    assert "current_tip_position_m" not in frame.metadata
