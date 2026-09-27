from __future__ import annotations

import re

import pytest

from xpotato_sim.schemas import (
    ViewerControlGamepadButtonMessage,
    ViewerControlGamepadMessage,
    ViewerControlKeyboardMessage,
    ViewerControlMessage,
    ViewerControlMessageError,
    coerce_viewer_control_message,
    parse_viewer_control_message_json,
)


def test_parse_viewer_control_message_json_accepts_keyboard_payload() -> None:
    payload = parse_viewer_control_message_json(
        """
        {
          "type": "viewer_control_message",
          "timestamp_s": 1.25,
          "source_kind": "keyboard",
          "sequence": 7,
          "keyboard": {
            "active_key_codes": ["KeyW", "KeyA"],
            "key_state": {"KeyW": true, "KeyA": false},
            "focus_state": "focused",
            "zero_state": false
          },
          "metadata": {
            "origin": "ui",
            "nested": {"kept": true}
          }
        }
        """
    )

    assert payload == ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=1.25,
        source_kind="keyboard",
        sequence=7,
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=("KeyW", "KeyA"),
            key_state={"KeyW": True, "KeyA": False},
            focus_state="focused",
            zero_state=False,
        ),
        gamepad=None,
        metadata={"origin": "ui", "nested": {"kept": True}},
    )


def test_parse_viewer_control_message_json_accepts_gamepad_payload() -> None:
    payload = parse_viewer_control_message_json(
        """
        {
          "type": "viewer_control_message",
          "timestamp_s": 2.5,
          "source_kind": "gamepad",
          "gamepad": {
            "connected": true,
            "axes": [0.0, -0.5],
            "buttons": [
              {"pressed": true, "value": 0.75},
              {"pressed": false}
            ],
            "stale": false,
            "zero_state": true
          },
          "metadata": {}
        }
        """
    )

    assert payload == ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=2.5,
        source_kind="gamepad",
        sequence=None,
        keyboard=None,
        gamepad=ViewerControlGamepadMessage(
            connected=True,
            axes=(0.0, -0.5),
            buttons=(
                ViewerControlGamepadButtonMessage(pressed=True, value=0.75),
                ViewerControlGamepadButtonMessage(pressed=False, value=None),
            ),
            stale=False,
            zero_state=True,
        ),
        metadata={},
    )
    assert payload.gamepad is not None
    assert payload.gamepad.index is None
    assert payload.gamepad.id is None


def test_parse_viewer_control_message_json_rejects_malformed_payload() -> None:
    with pytest.raises(ViewerControlMessageError, match="malformed JSON"):
        parse_viewer_control_message_json("{not json")


def test_parse_viewer_control_message_json_rejects_unknown_source_kind() -> None:
    with pytest.raises(ViewerControlMessageError, match="source_kind must be 'keyboard' or 'gamepad'"):
        parse_viewer_control_message_json(
            """
            {
              "type": "viewer_control_message",
              "timestamp_s": 1.0,
              "source_kind": "touch",
              "metadata": {}
            }
            """
        )


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
                "gamepad": {
                    "connected": True,
                    "axes": [],
                    "buttons": [],
                },
                "metadata": {},
            },
            "gamepad payload is not allowed when source_kind is 'keyboard'",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
                "gamepad": {
                    "connected": True,
                    "axes": [],
                    "buttons": [],
                },
                "metadata": {},
            },
            "keyboard payload is not allowed when source_kind is 'gamepad'",
        ),
    ],
)
def test_parse_viewer_control_message_json_rejects_mixed_source_payloads(
    payload: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(ViewerControlMessageError, match=expected_message):
        coerce_viewer_control_message(payload)


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": "1.0",
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
                "metadata": {},
            },
            "timestamp_s must be a finite number",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "sequence": "7",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
                "metadata": {},
            },
            "sequence must be an integer",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": "KeyW",
                    "key_state": {"KeyW": True},
                },
                "metadata": {},
            },
            "keyboard.active_key_codes must be an array of strings",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "gamepad": {
                    "index": 0,
                    "id": "Controller",
                    "connected": "yes",
                    "axes": [0.0],
                    "buttons": [True],
                },
                "metadata": {},
            },
            "gamepad.connected must be a boolean",
        ),
    ],
)
def test_parse_viewer_control_message_json_rejects_wrong_field_types(
    payload: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(ViewerControlMessageError, match=expected_message):
        coerce_viewer_control_message(payload)


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "metadata": {},
            },
            "keyboard payload is required",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "metadata": {},
            },
            "gamepad payload is required",
        ),
    ],
)
def test_parse_viewer_control_message_json_requires_source_specific_payload(
    payload: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(ViewerControlMessageError, match=expected_message):
        coerce_viewer_control_message(payload)


def test_parse_viewer_control_message_json_rejects_unknown_top_level_field() -> None:
    with pytest.raises(ViewerControlMessageError, match="contains unknown fields"):
        coerce_viewer_control_message(
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
                "metadata": {},
                "unexpected": True,
            }
        )


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "sequence": None,
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                },
                "metadata": {},
            },
            "viewer control message.sequence must be an integer",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                    "focus_state": None,
                },
                "metadata": {},
            },
            "keyboard.focus_state must be 'focused' or 'blurred'",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                    "zero_state": None,
                },
                "metadata": {},
            },
            "keyboard.zero_state must be a boolean",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "gamepad": {
                    "index": None,
                    "connected": True,
                    "axes": [0.0],
                    "buttons": [{"pressed": True}],
                },
                "metadata": {},
            },
            "gamepad.index must be an integer",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "gamepad": {
                    "id": None,
                    "connected": True,
                    "axes": [0.0],
                    "buttons": [{"pressed": True}],
                },
                "metadata": {},
            },
            "gamepad.id must be a string",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "gamepad": {
                    "connected": True,
                    "axes": [0.0],
                    "buttons": [{"pressed": True, "value": None}],
                },
                "metadata": {},
            },
            "gamepad.buttons[0].value must be a finite number",
        ),
    ],
)
def test_parse_viewer_control_message_json_rejects_explicit_null_optional_fields(
    payload: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(ViewerControlMessageError, match=re.escape(expected_message)):
        coerce_viewer_control_message(payload)


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "keyboard",
                "keyboard": {
                    "active_key_codes": ["KeyW"],
                    "key_state": {"KeyW": True},
                    "unexpected": True,
                },
                "metadata": {},
            },
            "keyboard contains unknown fields",
        ),
        (
            {
                "type": "viewer_control_message",
                "timestamp_s": 1.0,
                "source_kind": "gamepad",
                "gamepad": {
                    "connected": True,
                    "axes": [0.0],
                    "buttons": [{"pressed": True, "unexpected": True}],
                },
                "metadata": {},
            },
            r"gamepad\.buttons\[0\] contains unknown fields",
        ),
    ],
)
def test_parse_viewer_control_message_json_rejects_unknown_nested_fields(
    payload: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(ViewerControlMessageError, match=expected_message):
        coerce_viewer_control_message(payload)


@pytest.mark.parametrize(
    ("provider_fields", "expected_message"),
    [
        ({"provider_id": "keyboard/v1"}, "provider_id and provider_schema must be supplied together"),
        (
            {"provider_id": "gamepad/v1", "provider_schema": "viewer_gamepad_sample/v1"},
            "provider identity/schema does not match source_kind",
        ),
        (
            {"provider_id": "keyboard/v1", "provider_schema": "viewer_unknown_sample/v1"},
            "provider_schema is unknown",
        ),
    ],
)
def test_provider_identity_and_schema_mismatch_fails_closed(
    provider_fields: dict[str, str], expected_message: str
) -> None:
    payload = {
        "type": "viewer_control_message",
        "timestamp_s": 1.0,
        "source_kind": "keyboard",
        "keyboard": {"active_key_codes": ["KeyW"], "key_state": {"KeyW": True}},
        "metadata": {},
        **provider_fields,
    }
    with pytest.raises(ViewerControlMessageError, match=re.escape(expected_message)):
        coerce_viewer_control_message(payload)


def test_provider_identity_extension_is_backward_compatible() -> None:
    payload = coerce_viewer_control_message(
        {
            "type": "viewer_control_message",
            "timestamp_s": 1.0,
            "source_kind": "keyboard",
            "provider_id": "keyboard/v1",
            "provider_schema": "viewer_keyboard_sample/v1",
            "keyboard": {"active_key_codes": [], "key_state": {}},
            "metadata": {},
        }
    )
    assert payload.provider_id == "keyboard/v1"
    assert payload.provider_schema == "viewer_keyboard_sample/v1"
