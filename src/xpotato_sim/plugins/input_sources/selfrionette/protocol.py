"""Selfrionette 7-channel protocol, parsing, and intrinsic normalization."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import cast

from xpotato_sim.schemas import RawInputFrame


# wire仕様ではなく、取得処理と診断保持のsoftware保護policy。
MAX_LINES_PER_FRAME = 64
MAX_LINE_BYTES = 1024
MAX_DIAGNOSTICS = 64


class SerialAcquisitionError(RuntimeError):
    """正常sampleを取得できなかった有限取得の失敗。過去frameでは代用しない。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Selfrionette acquisition failed: {reason}")


@dataclass(frozen=True, slots=True)
class RawLoadcellVectorRecord:
    timestamp_ms: int
    channels: tuple[float, float, float, float, float, float, float]
    raw_line: str


@dataclass(frozen=True, slots=True)
class SerialDiagnosticEvent:
    prefix: str
    fields: tuple[str, ...]
    raw_line: str


@dataclass(frozen=True, slots=True)
class LoadcellNormalizationConfig:
    """Device-intrinsic calibration and sensor-domain saturation limits."""

    channel_count: int = 7
    scale: float = 1.0
    clamp_abs: float = 1.0

    def __post_init__(self) -> None:
        if self.channel_count != 7:
            raise ValueError("channel_count must be exactly 7")
        if self.scale <= 0.0:
            raise ValueError("scale must be positive")
        if self.clamp_abs <= 0.0:
            raise ValueError("clamp_abs must be positive")


@dataclass(frozen=True, slots=True)
class NormalizedLoadcellInputIntent:
    source: str
    timestamp_s: float
    values: tuple[float, float, float, float, float, float, float]
    active_channels: tuple[int, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


class SerialFrameParseError(ValueError):
    def __init__(self, line: str, reason: str) -> None:
        self.line = line
        self.reason = reason
        super().__init__(f"{reason}: {line!r}")


def _raise_parse_error(line: str, reason: str) -> None:
    raise SerialFrameParseError(line, reason)


def _parse_timestamp_ms(text: str, *, line: str) -> int:
    try:
        timestamp_ms = int(text)
    except ValueError as exc:
        raise SerialFrameParseError(line, "malformed timestamp") from exc

    if timestamp_ms < 0:
        raise SerialFrameParseError(line, "negative timestamp")

    return timestamp_ms


def _parse_channel_value(text: str, *, line: str, channel_index: int) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise SerialFrameParseError(line, f"malformed channel value at index {channel_index}") from exc

    if not isfinite(value):
        raise SerialFrameParseError(line, f"non-finite channel value at index {channel_index}")

    return value


def parse_serial_frame_line(line: str) -> RawLoadcellVectorRecord | SerialDiagnosticEvent:
    stripped_line = line.strip()
    if not stripped_line:
        _raise_parse_error(line, "empty line")

    parts = stripped_line.split(",")
    prefix = parts[0]

    if prefix == "vector":
        if len(parts) != 9:
            _raise_parse_error(line, "vector frame must contain exactly 9 fields")

        timestamp_ms = _parse_timestamp_ms(parts[1], line=line)
        channels = cast(
            tuple[float, float, float, float, float, float, float],
            tuple(
                _parse_channel_value(parts[index], line=line, channel_index=index - 2)
                for index in range(2, 9)
            ),
        )
        return RawLoadcellVectorRecord(
            timestamp_ms=timestamp_ms,
            channels=channels,
            raw_line=stripped_line,
        )

    if prefix in {"status", "warn"}:
        return SerialDiagnosticEvent(
            prefix=prefix,
            fields=tuple(parts[1:]),
            raw_line=stripped_line,
        )

    return SerialDiagnosticEvent(
        prefix=prefix,
        fields=tuple(parts[1:]),
        raw_line=stripped_line,
    )


def _iterable_to_line_reader(lines: Iterable[str] | Iterator[str]) -> Callable[[], str]:
    iterator = iter(lines)

    def read_line() -> str:
        return next(iterator)

    return read_line


class SerialInputSource:
    """Injected-line serial source that yields parsed loadcell vector frames.

    The source collects diagnostic frames locally and only surfaces vector
    records as RawInputFrame objects. It does not open a serial port.
    """

    def __init__(self, line_reader: Iterable[str] | Iterator[str] | Callable[[], str]) -> None:
        if callable(line_reader):
            self._read_line = line_reader
        else:
            self._read_line = _iterable_to_line_reader(line_reader)

        self._diagnostics: deque[SerialDiagnosticEvent] = deque(maxlen=MAX_DIAGNOSTICS)
        self.diagnostics_dropped = 0

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> "SerialInputSource":
        return cls(lines)

    @property
    def diagnostics(self) -> tuple[SerialDiagnosticEvent, ...]:
        return tuple(self._diagnostics)

    def _read_next_line(self) -> str:
        try:
            return self._read_line()
        except StopIteration as exc:
            raise StopIteration("SerialInputSource reached end of injected lines") from exc

    def _read_next_vector_record(self) -> RawLoadcellVectorRecord:
        for _ in range(MAX_LINES_PER_FRAME):
            line = self._read_next_line()
            if not isinstance(line, str):
                raise SerialFrameParseError(repr(line)[:128], "line must be a string")
            # 先に文字数を制限し、巨大文字列のencodeやerrorへの複製を避ける。
            if len(line) > MAX_LINE_BYTES or len(line.encode("utf-8")) > MAX_LINE_BYTES:
                raise SerialFrameParseError(line[:128], "line byte limit exceeded")
            record_or_event = parse_serial_frame_line(line)

            if isinstance(record_or_event, SerialDiagnosticEvent):
                if len(self._diagnostics) == MAX_DIAGNOSTICS:
                    self.diagnostics_dropped += 1
                self._diagnostics.append(record_or_event)
                continue

            return record_or_event
        raise SerialAcquisitionError("no_vector_line_budget")

    def read_frame(self) -> RawInputFrame:
        vector_record = self._read_next_vector_record()
        try:
            timestamp_s = float(vector_record.timestamp_ms) / 1000.0
        except OverflowError as exc:
            raise SerialFrameParseError(vector_record.raw_line, "timestamp out of range") from exc
        return RawInputFrame(
            source="selfrionette",
            timestamp_s=timestamp_s,
            values=vector_record.channels,
            metadata={
                "source_kind": "selfrionette",
                "timestamp_ms": vector_record.timestamp_ms,
                "raw_line": vector_record.raw_line,
            },
        )


def _coerce_loadcell_values(
    values: tuple[float, ...],
    *,
    expected_channel_count: int,
) -> tuple[float, float, float, float, float, float, float]:
    if len(values) != expected_channel_count:
        raise ValueError(f"loadcell vector must contain exactly {expected_channel_count} values")

    coerced_values = []
    for channel_index, raw_value in enumerate(values):
        if not isfinite(raw_value):
            raise ValueError(f"non-finite loadcell value at index {channel_index}")
        coerced_values.append(float(raw_value))

    return cast(
        tuple[float, float, float, float, float, float, float],
        tuple(coerced_values),
    )


def _normalize_channel_value(raw_value: float, config: LoadcellNormalizationConfig) -> float:
    normalized_value = raw_value / config.scale
    if normalized_value > config.clamp_abs:
        return config.clamp_abs
    if normalized_value < -config.clamp_abs:
        return -config.clamp_abs

    return normalized_value


class LoadcellNormalizedInputIntentConverter:
    """Convert raw 7ch loadcell values into a normalized intent."""

    def __init__(
        self,
        config: LoadcellNormalizationConfig | None = None,
        *,
        source: str = "selfrionette",
    ) -> None:
        self._config = LoadcellNormalizationConfig() if config is None else config
        self._source = source

    @property
    def config(self) -> LoadcellNormalizationConfig:
        return self._config

    def convert(self, frame: RawInputFrame | RawLoadcellVectorRecord) -> NormalizedLoadcellInputIntent:
        if isinstance(frame, RawInputFrame):
            source = frame.source
            timestamp_s = frame.timestamp_s
            raw_values = frame.values
            metadata = dict(frame.metadata)
        else:
            source = self._source
            timestamp_s = float(frame.timestamp_ms) / 1000.0
            raw_values = frame.channels
            metadata = {}

        values = _coerce_loadcell_values(raw_values, expected_channel_count=self._config.channel_count)
        normalized_values = tuple(
            _normalize_channel_value(raw_value, self._config)
            for raw_value in values
        )
        active_channels = tuple(
            channel_index
            for channel_index, normalized_value in enumerate(normalized_values)
            if normalized_value != 0.0
        )

        return NormalizedLoadcellInputIntent(
            source=source,
            timestamp_s=timestamp_s,
            values=cast(
                tuple[float, float, float, float, float, float, float],
                normalized_values,
            ),
            active_channels=active_channels,
            metadata=metadata,
        )


def normalize_loadcell_frame_for_mapping(
    frame: RawInputFrame,
) -> NormalizedLoadcellInputIntent:
    """Adapt one raw source frame at the explicit source-to-mapping boundary."""

    return LoadcellNormalizedInputIntentConverter().convert(frame)


__all__ = [
    "LoadcellNormalizationConfig",
    "LoadcellNormalizedInputIntentConverter",
    "normalize_loadcell_frame_for_mapping",
    "NormalizedLoadcellInputIntent",
    "RawLoadcellVectorRecord",
    "SerialDiagnosticEvent",
    "SerialFrameParseError",
    "SerialInputSource",
    "parse_serial_frame_line",
]
