"""Selfrionette injected-line reader contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import (
    SerialFrameParseError,
    SerialInputSource,
)
from xpotato_sim.schemas import RawInputFrame


FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "r7_a_lite_serial_frames"


def read_fixture_lines(name: str) -> list[str]:
    return FIXTURE_ROOT.joinpath(name).read_text(encoding="utf-8").splitlines()


def test_serial_input_source_from_lines_reads_first_valid_vector_record() -> None:
    source = SerialInputSource.from_lines(read_fixture_lines("minimal_valid.txt"))

    frame = source.read_frame()

    assert isinstance(frame, RawInputFrame)
    assert frame.source == "selfrionette"
    assert frame.timestamp_s == pytest.approx(2152.956)
    assert frame.values == pytest.approx((-37.67, 99.06, 137.60, 242.13, 277.34, 25.87, -18.67))
    assert frame.metadata["source_kind"] == "selfrionette"
    assert frame.metadata["timestamp_ms"] == 2_152_956
    assert frame.metadata["raw_line"] == "vector,2152956,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67"

    diagnostics = source.diagnostics
    assert [event.prefix for event in diagnostics] == ["status", "status", "status", "warn", "warn"]
    assert diagnostics[1].fields == ("calibration_command_received",)
    assert diagnostics[4].fields == ("calibration_spread", "4", "2501.0")


def test_serial_input_source_preserves_vector_timestamp_and_channels() -> None:
    line = "vector,2152956,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67"
    source = SerialInputSource([line])

    frame = source.read_frame()

    assert frame.timestamp_s == pytest.approx(2152.956)
    assert frame.values == pytest.approx((-37.67, 99.06, 137.60, 242.13, 277.34, 25.87, -18.67))


def test_serial_input_source_surfaces_malformed_vector_lines() -> None:
    source = SerialInputSource.from_lines(
        [
            "status,setup_start",
            "vector,2152956,-37.67,99.06,137.60,242.13,277.34,25.87",
        ]
    )

    with pytest.raises(SerialFrameParseError) as exc_info:
        source.read_frame()

    assert "exactly 9 fields" in exc_info.value.reason
    assert [event.fields for event in source.diagnostics] == [("setup_start",)]


def test_serial_input_source_uses_callable_reader_and_stops_deterministically() -> None:
    lines = iter(
        [
            "warn,ready_timeout,3",
            "vector,2152956,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67",
        ]
    )

    def read_line() -> str:
        return next(lines)

    source = SerialInputSource(read_line)

    frame = source.read_frame()

    assert isinstance(frame, RawInputFrame)
    assert source.diagnostics[0].fields == ("ready_timeout", "3")

    with pytest.raises(
        StopIteration,
        match="SerialInputSource reached end of injected lines",
    ):
        source.read_frame()


def test_serial_input_source_does_not_silence_known_malformed_vectors() -> None:
    source = SerialInputSource.from_lines(["vector,-1,-37.67,99.06,137.60,242.13,277.34,25.87,-18.67"])

    with pytest.raises(SerialFrameParseError) as exc_info:
        source.read_frame()

    assert "negative timestamp" in exc_info.value.reason
