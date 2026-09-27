"""Selfrionette device source with live and injected acquisition backends."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import Enum
from math import isfinite
from time import monotonic

from xpotato_sim.plugins.input_sources.selfrionette.protocol import (
    LoadcellNormalizationConfig,
    LoadcellNormalizedInputIntentConverter,
    NormalizedLoadcellInputIntent,
    RawLoadcellVectorRecord,
    SerialDiagnosticEvent,
    SerialAcquisitionError,
    MAX_LINES_PER_FRAME, MAX_LINE_BYTES, MAX_DIAGNOSTICS,
    SerialFrameParseError,
    SerialInputSource,
    normalize_loadcell_frame_for_mapping,
    parse_serial_frame_line,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceRuntimeDependencies,
)
from xpotato_sim.schemas import RawInputFrame

_SERIAL_IMPORT_ERROR = (
    "serial module is required for live Selfrionette mode. "
    "Install pyserial or use injected lines."
)


READ_TIMEOUT_S = 0.25
STALE_AFTER_MS = 250


class _LifecycleState(str, Enum):
    NEW = "new"
    STARTED = "started"
    START_FAILED = "start_failed"
    CLOSED = "closed"


class SelfrionetteInputSource:
    """readerの取得・health・exclusive lifecycleを所有する。

    constructionはI/Oなし。read失敗はsessionへラッチし、close -> startまで再取得しない。
    経過時間だけによるstaleは新しいvectorで復帰できる。thread-safeではない。
    """

    def __init__(
        self, *, port: str | None, baud_rate: int,
        injected_lines: tuple[str, ...] | None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._port = port
        self._baud_rate = baud_rate
        self._injected_lines = injected_lines
        self._clock = clock if clock is not None else (monotonic if injected_lines is None else lambda: 0.0)
        self._clock_kind = "injected" if clock is not None else (
            "host_monotonic" if injected_lines is None else "injected_static"
        )
        self._serial_port = None
        self._source: SerialInputSource | None = None
        self._state = _LifecycleState.NEW
        self._last_now: float | None = None
        self._started_at: float | None = None
        self._received_at: float | None = None
        self._deadline = 0.0
        self._failure: tuple[InputSourceHealthStatus, str] | None = None
        self._closed_diagnostics: tuple[SerialDiagnosticEvent, ...] = ()
        self._closed_diagnostics_dropped = 0

    def _now(self) -> float:
        try:
            value = self._clock()
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("clock must return a number")
            now = float(value)
            if not isfinite(now) or (self._last_now is not None and now < self._last_now):
                raise ValueError("clock must be finite and monotonic")
        except Exception as exc:
            raise SerialAcquisitionError("invalid_monotonic_clock") from exc
        self._last_now = now
        return now

    def _read_serial_line(self) -> str:
        remaining = self._deadline - self._now()
        if remaining <= 0.0:
            raise SerialAcquisitionError("read_timeout")
        self._serial_port.timeout = remaining
        raw = self._serial_port.read_until(b"\n", size=MAX_LINE_BYTES + 1)
        finished_at = self._now()
        if not isinstance(raw, bytes):
            raise SerialAcquisitionError("invalid_serial_bytes")
        if finished_at > self._deadline or not raw:
            raise SerialAcquisitionError("read_timeout")
        if len(raw) > MAX_LINE_BYTES:
            raise SerialFrameParseError(repr(raw[:128]), "line byte limit exceeded")
        # timeout途中のlineを完全なvectorと誤認せず、正常sampleとして保管・再送しない。
        if not raw.endswith(b"\n"):
            raise SerialFrameParseError(repr(raw[:128]), "incomplete serial line")
        return raw.decode("utf-8", errors="strict")

    def start(self) -> None:
        if self._state is _LifecycleState.STARTED:
            if self._failure is not None:
                raise SerialAcquisitionError("close_required_after_read_failure")
            return
        if self._state is _LifecycleState.START_FAILED:
            raise RuntimeError("Selfrionette input source must be closed after failed start before retry")
        self._last_now = None
        self._received_at = None
        self._failure = None
        self._closed_diagnostics = ()
        self._closed_diagnostics_dropped = 0
        try:
            self._started_at = self._now()
            if self._injected_lines is not None:
                self._source = SerialInputSource.from_lines(self._injected_lines)
            else:
                if self._port is None:
                    raise ValueError("port is required for live Selfrionette mode")
                try:
                    # optional dependencyは明示live startでのみimportする。
                    import serial  # type: ignore[import-not-found]
                except ModuleNotFoundError as exc:
                    raise RuntimeError(_SERIAL_IMPORT_ERROR) from exc
                self._serial_port = serial.Serial(
                    port=self._port, baudrate=self._baud_rate, timeout=READ_TIMEOUT_S,
                )
                self._source = SerialInputSource(self._read_serial_line)
        except BaseException:
            self._state = _LifecycleState.START_FAILED
            raise
        self._state = _LifecycleState.STARTED

    def read_frame(self) -> RawInputFrame:
        if self._source is None or self._state is not _LifecycleState.STARTED:
            raise RuntimeError("Selfrionette input source is not started")
        if self._failure is not None:
            raise SerialAcquisitionError("close_required_after_read_failure")
        try:
            self._deadline = self._now() + READ_TIMEOUT_S
            frame = self._source.read_frame()
            received_at = self._now()
            if received_at > self._deadline:
                raise SerialAcquisitionError("read_timeout")
        except StopIteration:
            self._failure = (InputSourceHealthStatus.DISCONNECTED, "end_of_stream")
            raise
        except OSError:
            self._failure = (InputSourceHealthStatus.DISCONNECTED, "serial_io_error")
            raise
        except SerialAcquisitionError as exc:
            status = (InputSourceHealthStatus.STALE if exc.reason in
                      {"read_timeout", "no_vector_line_budget"} else InputSourceHealthStatus.INVALID)
            self._failure = (status, exc.reason)
            raise
        except Exception:
            self._failure = (InputSourceHealthStatus.INVALID, "invalid_serial_frame")
            raise
        self._received_at = received_at
        return replace(frame, metadata={**frame.metadata, "acquisition_v1": {
            **self._acquisition_metadata(), "receipt_monotonic_s": received_at,
        }})

    def _acquisition_metadata(self) -> dict[str, object]:
        return {
            "policy": "selfrionette_acquisition/v1",
            "backend": "injected_lines" if self._injected_lines is not None else "serial",
            "clock_kind": self._clock_kind,
            "read_timeout_s": READ_TIMEOUT_S,
            "max_lines_per_frame": MAX_LINES_PER_FRAME,
            "max_line_bytes": MAX_LINE_BYTES,
            "max_diagnostics": MAX_DIAGNOSTICS,
            "stale_after_ms": STALE_AFTER_MS,
        }

    def current_health(self) -> InputSourceHealth:
        if self._state is not _LifecycleState.STARTED:
            reason = "start_failed" if self._state is _LifecycleState.START_FAILED else "not_started"
            return InputSourceHealth(InputSourceHealthStatus.DISCONNECTED, reason=reason, age_ms=0)
        try:
            now = self._now()
            elapsed_ms = None if self._received_at is None else (now - self._received_at) * 1000.0
            if elapsed_ms is not None and not isfinite(elapsed_ms):
                raise SerialAcquisitionError("invalid_monotonic_clock")
            age_ms = None if elapsed_ms is None else int(elapsed_ms)
        except Exception:
            self._failure = (InputSourceHealthStatus.INVALID, "invalid_monotonic_clock")
            return InputSourceHealth(InputSourceHealthStatus.INVALID,
                                     reason="invalid_monotonic_clock", age_ms=None)
        metadata = {"acquisition_v1": self._acquisition_metadata(),
                    "receipt_monotonic_s": self._received_at,
                    "diagnostics_dropped": self.diagnostics_dropped}
        if self._failure is not None:
            status, reason = self._failure
            return InputSourceHealth(status, reason=reason, age_ms=age_ms, metadata=metadata)
        reference = self._started_at if self._received_at is None else self._received_at
        if now - reference > STALE_AFTER_MS / 1000.0:
            return InputSourceHealth(InputSourceHealthStatus.STALE, age_ms=age_ms,
                                     reason="no_vector_received" if self._received_at is None else "sample_stale",
                                     metadata=metadata)
        status = InputSourceHealthStatus.INACTIVE if self._received_at is None else InputSourceHealthStatus.ACTIVE
        return InputSourceHealth(status, age_ms=age_ms, metadata=metadata)

    def close(self) -> None:
        # closeが失敗した場合も、その呼出しを成功とせず例外を伝播する。
        if self._serial_port is not None:
            try:
                self._serial_port.close()
            except BaseException:
                self._failure = (InputSourceHealthStatus.DISCONNECTED, "close_failed")
                raise
        if self._source is not None:
            self._closed_diagnostics = self._source.diagnostics
            self._closed_diagnostics_dropped = self._source.diagnostics_dropped
        self._serial_port = None
        self._source = None
        self._state = _LifecycleState.CLOSED

    @property
    def diagnostics(self) -> tuple[SerialDiagnosticEvent, ...]:
        return self._closed_diagnostics if self._source is None else self._source.diagnostics

    @property
    def diagnostics_dropped(self) -> int:
        return self._closed_diagnostics_dropped if self._source is None else self._source.diagnostics_dropped


def _validate_parameters(
    parameters: Mapping[str, object],
    *,
    runtime_dependencies: InputSourceRuntimeDependencies | None,
) -> tuple[str | None, int, tuple[str, ...] | None]:
    port = parameters.get("port")
    if port is not None and (
        not isinstance(port, str) or not port.strip()
    ):
        raise ValueError("port must be a non-empty string when provided")
    baud_rate = parameters.get("baud_rate", 115200)
    if type(baud_rate) is not int or baud_rate <= 0:
        raise ValueError("baud_rate must be positive")
    parameter_lines = parameters.get("lines")
    if parameter_lines is not None and not isinstance(parameter_lines, tuple):
        raise ValueError("Selfrionette injected lines must be a tuple")
    dependency_lines = (
        runtime_dependencies.line_source
        if runtime_dependencies is not None
        else None
    )
    selected_lines = dependency_lines if dependency_lines is not None else parameter_lines
    injected_lines = tuple(selected_lines) if selected_lines is not None else None
    if injected_lines is not None and any(
        not isinstance(line, str) for line in injected_lines
    ):
        raise ValueError("Selfrionette injected lines must contain strings")
    if port is not None and injected_lines is not None:
        raise ValueError("Selfrionette source cannot combine port and injected lines")
    if port is None and injected_lines is None:
        raise ValueError("port or injected lines are required for Selfrionette")
    return port, baud_rate, injected_lines


def build_reader(
    parameters: Mapping[str, object],
    *,
    runtime_dependencies: InputSourceRuntimeDependencies | None = None,
) -> SelfrionetteInputSource:
    """parameterを検証して未開始readerを返し、serial portは開かない。"""

    port, baud_rate, injected_lines = _validate_parameters(
        parameters,
        runtime_dependencies=runtime_dependencies,
    )
    return SelfrionetteInputSource(
        port=port,
        baud_rate=baud_rate,
        injected_lines=injected_lines,
        clock=runtime_dependencies.clock if runtime_dependencies is not None else None,
    )


__all__ = [
    "LoadcellNormalizationConfig",
    "LoadcellNormalizedInputIntentConverter",
    "NormalizedLoadcellInputIntent",
    "RawLoadcellVectorRecord",
    "SelfrionetteInputSource",
    "SerialDiagnosticEvent",
    "SerialAcquisitionError",
    "SerialFrameParseError",
    "SerialInputSource",
    "build_reader",
    "normalize_loadcell_frame_for_mapping",
    "parse_serial_frame_line",
]
