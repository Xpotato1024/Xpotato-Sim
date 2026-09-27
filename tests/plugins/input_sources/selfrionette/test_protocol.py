"""Selfrionette seven-channel protocol parser contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import (
    RawLoadcellVectorRecord,
    SerialDiagnosticEvent,
    SerialFrameParseError,
    parse_serial_frame_line,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "r7_a_lite_serial_frames"


def read_fixture_lines(name: str) -> list[str]:
    return FIXTURE_ROOT.joinpath(name).read_text(encoding="utf-8").splitlines()


def test_minimal_valid_fixture_parses_vector_and_diagnostics() -> None:
    lines = read_fixture_lines("minimal_valid.txt")

    status_setup = parse_serial_frame_line(lines[0])
    status_calibration = parse_serial_frame_line(lines[1])
    status_calibration_end = parse_serial_frame_line(lines[2])
    warn_ready_timeout = parse_serial_frame_line(lines[3])
    warn_calibration_spread = parse_serial_frame_line(lines[4])
    vector = parse_serial_frame_line(lines[5])

    assert isinstance(status_setup, SerialDiagnosticEvent)
    assert status_setup.prefix == "status"
    assert status_setup.fields == ("setup_start",)

    assert isinstance(status_calibration, SerialDiagnosticEvent)
    assert status_calibration.prefix == "status"
    assert status_calibration.fields == ("calibration_command_received",)

    assert isinstance(status_calibration_end, SerialDiagnosticEvent)
    assert status_calibration_end.prefix == "status"
    assert status_calibration_end.fields == ("calibration_channel_end", "0", "818")

    assert isinstance(warn_ready_timeout, SerialDiagnosticEvent)
    assert warn_ready_timeout.prefix == "warn"
    assert warn_ready_timeout.fields == ("ready_timeout", "3")

    assert isinstance(warn_calibration_spread, SerialDiagnosticEvent)
    assert warn_calibration_spread.prefix == "warn"
    assert warn_calibration_spread.fields == ("calibration_spread", "4", "2501.0")

    assert isinstance(vector, RawLoadcellVectorRecord)
    assert vector.timestamp_ms == 2_152_956
    assert vector.channels == pytest.approx(
        (-37.67, 99.06, 137.60, 242.13, 277.34, 25.87, -18.67)
    )


def test_status_line_is_diagnostic_not_vector() -> None:
    record = parse_serial_frame_line("status,calibration_command_received")

    assert isinstance(record, SerialDiagnosticEvent)
    assert record.prefix == "status"
    assert record.fields == ("calibration_command_received",)


def test_warn_line_is_diagnostic_not_vector() -> None:
    record = parse_serial_frame_line("warn,calibration_spread,4,2501.0")

    assert isinstance(record, SerialDiagnosticEvent)
    assert record.prefix == "warn"
    assert record.fields == ("calibration_spread", "4", "2501.0")


def test_unknown_prefix_is_surfaces_as_diagnostic_event() -> None:
    record = parse_serial_frame_line("foo,bar,baz")

    assert isinstance(record, SerialDiagnosticEvent)
    assert record.prefix == "foo"
    assert record.fields == ("bar", "baz")


@pytest.mark.parametrize(
    ("line", "reason_fragment"),
    [
        ("vector,2152956,-37.67,99.06,137.60,242.13,277.34,25.87", "exactly 9 fields"),
        (
            "vector,2152956,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67,1.0",
            "exactly 9 fields",
        ),
        (
            "vector,2152956,-37.67,99.06,abc,242.13,277.34,25.87,-18.67",
            "malformed channel value",
        ),
        (
            "vector,2152956,-37.67,99.06,nan,242.13,277.34,25.87,-18.67",
            "non-finite channel value",
        ),
        (
            "vector,2152956,-37.67,99.06,inf,242.13,277.34,25.87,-18.67",
            "non-finite channel value",
        ),
        (
            "vector,2152956,-37.67,99.06,-inf,242.13,277.34,25.87,-18.67",
            "non-finite channel value",
        ),
        (
            "vector,not-a-number,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67",
            "malformed timestamp",
        ),
        (
            "vector,-1,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67",
            "negative timestamp",
        ),
    ],
)
def test_malformed_vector_lines_are_rejected(line: str, reason_fragment: str) -> None:
    with pytest.raises(SerialFrameParseError) as exc_info:
        parse_serial_frame_line(line)

    assert reason_fragment in exc_info.value.reason


def test_empty_line_is_rejected() -> None:
    with pytest.raises(SerialFrameParseError) as exc_info:
        parse_serial_frame_line("")

    assert exc_info.value.reason == "empty line"


def test_negative_timestamp_is_rejected() -> None:
    with pytest.raises(SerialFrameParseError) as exc_info:
        parse_serial_frame_line("vector,-1,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67")

    assert "negative timestamp" in exc_info.value.reason


def test_malformed_fixture_lines_are_rejected() -> None:
    for line in read_fixture_lines("malformed.txt"):
        with pytest.raises(SerialFrameParseError):
            parse_serial_frame_line(line)
