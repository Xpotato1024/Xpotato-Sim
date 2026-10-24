"""明示されたRust bridge processを有界JSONLで所有する。serial/校正はDevice側に置く。"""
from __future__ import annotations

from collections import deque
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Thread
from time import get_clock_info, monotonic

from xpotato_sim.runtime.composition.input_sessions import resolve_source_session
from xpotato_sim.plugins.input_sources.selfrionette_device.source import (
    EVENT_SCHEMA, INPUT_SCHEMA, canonical, fields, integer, strict_json, validate_parameters,
)

COMMAND_SCHEMA = "selfrionette-bridge-command/v1"
CONFIG_SCHEMA = "selfrionette-workbench-connection/v1"
MAX_LINE = 65536
MAX_COMMAND = 8192
MAX_EVENTS = 64
OPERATOR_PHASES = {"connect": "configured", "tare": "connected",
    "neutral": "awaiting_neutral", "apply": "neutral_ready"}
PHASES = {"unconfigured", "configured", "connected", "awaiting_neutral", "capturing_neutral",
    "neutral_ready", "applied", "streaming", "stopped", "failed"}


def load_connection_config(path):
    """起動時に明示したlocal fileだけ。browserから実行file/port/pathを受け取らない。"""
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_LINE + 1)
    raw = fields(strict_json(data.decode("utf-8")), {"schema_version", "executable", "roles"}, "bridge connection")
    if raw["schema_version"] != CONFIG_SCHEMA:
        raise ValueError("unsupported bridge connection schema")
    executable = Path(raw["executable"])
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("explicit existing absolute Device CLI executable required")
    roles = raw["roles"]
    if type(roles) is not dict or not roles or not set(roles) <= {"left", "right"}:
        raise ValueError("one or two explicit local role connections required")
    for value in roles.values():
        fields(value, {"port", "profile_path"}, "role connection")
        if type(value["port"]) is not str or not 1 <= len(value["port"]) <= 256 or "\x00" in value["port"]:
            raise ValueError("bounded explicit serial port required")
        profile = Path(value["profile_path"])
        if not profile.is_absolute() or not profile.is_file():
            raise ValueError("explicit existing absolute calibration profile required")
    if len({item["port"] for item in roles.values()}) != len(roles):
        raise ValueError("one serial port cannot serve both roles")
    return raw


class BridgeProcess:
    """最大64 eventと1 command。owner threadはpipe読書きや校正待機で停止しない。"""
    def __init__(self, executable, *, role, expected_revision, config, clock=monotonic,
                 process_factory=subprocess.Popen):
        self.role, self.expected_revision, self.clock = role, expected_revision, clock
        # hostの送信/受信2点の分解能とproducerの整数microsecond丸めを過去側へ確保する。
        # 時計原点を切り替えず、Source/TrialRunnerが使うmonotonicとの対応を維持する。
        resolution = get_clock_info("monotonic").resolution
        if not isfinite(resolution) or resolution <= 0:
            raise ValueError("finite positive host clock resolution required")
        self._clock_uncertainty_s = 2 * resolution + 1e-6
        if self._clock_uncertainty_s >= .2:
            raise ValueError("host clock resolution exceeds Device handshake budget")
        self._events, self._commands = Queue(MAX_EVENTS), Queue(1)
        self._failure = None
        self._closed = False
        self._request_id = 0
        self._pending = None
        self._clock_offset = None
        self._last_emitted = None
        self._status = {"phase": "unconfigured", "ready": False, "session_neutral_valid": False}
        self._stderr = deque(maxlen=8)
        self.received_samples = 0
        self._last_status_request = 0.
        if len(canonical(config).encode("utf-8")) > MAX_COMMAND - 256:
            raise ValueError("Device bridge config exceeds command bound")
        self._process = process_factory([str(executable), "bridge"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=8192, shell=False)
        self._threads = [Thread(target=self._read, daemon=True, name=f"device-{role}-events"),
            Thread(target=self._write, daemon=True, name=f"device-{role}-commands"),
            Thread(target=self._read_errors, daemon=True, name=f"device-{role}-errors")]
        try:
            for thread in self._threads:
                thread.start()
            self.command("configure", config=config)
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while not self._closed:
                raw = self._process.stdout.readline(MAX_LINE + 1)
                received_at = self.clock()
                if not raw:
                    raise EOFError("Device bridge closed its event stream")
                if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
                    raise ValueError("Device bridge line exceeds 64 KiB or is incomplete")
                self._events.put_nowait((raw.decode("utf-8"), received_at))
        except Exception as exc:
            if not self._closed:
                self._failure = "Device bridge event queue overflow" if isinstance(exc, Full) else str(exc)

    def _write(self):
        try:
            while True:
                item = self._commands.get()
                if item is None:
                    self._process.stdin.close()
                    return
                pending = memoryview(item)
                while pending:
                    written = self._process.stdin.write(pending)
                    if not written:
                        raise EOFError("Device bridge command pipe stopped accepting bytes")
                    pending = pending[written:]
                self._process.stdin.flush()
        except Exception as exc:
            if not self._closed:
                self._failure = f"Device bridge command write failed: {exc}"

    def _read_errors(self):
        try:
            while not self._closed:
                raw = self._process.stderr.read1(1024)
                if not raw:
                    return
                self._stderr.append(raw.decode("utf-8", errors="replace"))
        except Exception:
            return

    def command(self, action, **parameters):
        if self._closed or self._failure:
            raise RuntimeError(self._failure or "Device bridge is closed")
        if self._pending is not None:
            raise ValueError("Device bridge operation is pending")
        self._request_id += 1
        if self._request_id > 2**53 - 1:
            raise ValueError("Device bridge request bound reached; prepare a new process")
        document = (canonical({"schema": COMMAND_SCHEMA, "request_id": self._request_id,
            "action": action, **parameters}) + "\n").encode("utf-8")
        if len(document) > MAX_COMMAND:
            raise ValueError("Device bridge command exceeds 8 KiB")
        self._pending = (self._request_id, action, self.clock())
        if action == "status":
            self._last_status_request = self._pending[2]
        try:
            self._commands.put_nowait(document)
        except Full as exc:
            self._failure = "Device bridge command queue overflow"
            raise RuntimeError(self._failure) from exc

    def state(self):
        return {**self._status, "phase": "failed" if self._failure else self._status["phase"],
            "ready": bool(self._status.get("ready") and not self._closed and not self._failure
                          and self._clock_offset is not None and self._pending is None),
            "pending": None if self._pending is None else self._pending[1],
            "clock_synchronized": self._clock_offset is not None,
            "error": self._failure, "received_samples": self.received_samples,
            "queued_events": self._events.qsize(), "queue_limit": MAX_EVENTS}

    def poll(self):
        if self._closed:
            return ()
        if self._failure:
            raise RuntimeError(self._failure)
        if self._pending and self.clock() - self._pending[2] > 15:
            raise TimeoutError("Device bridge operation deadline exceeded")
        samples = []
        for _ in range(MAX_EVENTS):
            try:
                raw, received_at = self._events.get_nowait()
            except Empty:
                break
            event = strict_json(raw)
            if (type(event) is not dict or event.get("schema") != EVENT_SCHEMA
                    or event.get("bridge_revision") != self.expected_revision
                    or event.get("host_clock") != "bridge_process_elapsed_us"):
                raise ValueError("Device bridge schema/revision/clock mismatch")
            emitted = integer(event.get("host_emitted_us"), "bridge emission")
            if self._last_emitted is not None and emitted < self._last_emitted:
                raise ValueError("Device bridge clock regressed")
            self._last_emitted = emitted
            kind = event.get("event")
            if kind == "error":
                raise RuntimeError("Device bridge rejected operation: " + str(event.get("reason", "unknown"))[:2048])
            if kind == "status":
                request_id = integer(event.get("request_id"), "bridge request ID", maximum=self._request_id)
                phase = event.get("phase")
                if phase not in PHASES or type(event.get("ready")) is not bool or type(event.get("session_neutral_valid")) is not bool:
                    raise ValueError("invalid Device bridge status")
                self._status = {key: event.get(key) for key in ("phase", "ready", "session_neutral_valid", "source", "profile", "operator", "diagnostics")}
                if phase == "failed":
                    raise RuntimeError("Device bridge reported failed state")
                if self._pending is not None and request_id == self._pending[0]:
                    _, action, sent_at = self._pending
                    self._pending = None
                    if action == "status" and self._clock_offset is None:
                        # 分解能誤差を含むRTT上限を検査し、offsetも同じ余白だけ過去側へ置く。
                        # 初期pipe滞留を受信時刻の差分で消さない。
                        if not (0 <= received_at - sent_at
                                and received_at - sent_at + self._clock_uncertainty_s <= .2):
                            raise TimeoutError("Device bridge clock handshake exceeded 200 ms")
                        self._clock_offset = sent_at - emitted / 1e6 - self._clock_uncertainty_s
                    elif action == "configure":
                        self.command("status")
            elif kind == "sample":
                if self._clock_offset is None:
                    raise ValueError("Device sample predates clock handshake")
                producer_receipt = self._clock_offset + integer(event.get("host_received_us"), "bridge receipt") / 1e6
                producer_emission = self._clock_offset + emitted / 1e6
                backlog = integer(event.get("relative_backlog_ms"), "Device backlog") / 1000
                if producer_receipt > producer_emission or producer_emission > received_at + 1e-9:
                    raise ValueError("Device bridge receipt/emission is in the future")
                age = max(received_at - producer_receipt + backlog,
                    integer(event.get("sample_age_ms"), "Device age") / 1000 + received_at - producer_emission)
                message = canonical({"schema_version": INPUT_SCHEMA, "role": self.role,
                    "source_age_s": age, "event": event})
                samples.append((message, received_at))
                self.received_samples += 1
            else:
                raise ValueError("unknown Device bridge event")
        if (self._status["phase"] == "applied" and not self._status.get("ready")
                and self._pending is None and self.clock() - self._last_status_request >= .1):
            self.command("status")
        return tuple(samples)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._status = {"phase": "stopped", "ready": False, "session_neutral_valid": False}
        self._pending = None
        try:
            self._commands.put_nowait(None)
        except Full:
            pass
        for timeout, action in ((.15, None), (.15, self._process.terminate), (.15, self._process.kill)):
            try:
                if action is not None and self._process.poll() is None:
                    action()
                self._process.wait(timeout=timeout)
                break
            except subprocess.TimeoutExpired:
                continue
        if self._process.poll() is None:
            raise RuntimeError("Device bridge process termination could not be confirmed")
        # 終了後はpipe writeが解除されるので、reader/writerを有限待機で回収できる。
        try:
            self._commands.get_nowait()
        except Empty:
            pass
        self._commands.put_nowait(None)
        for thread in self._threads:
            if thread.ident is not None:
                thread.join(.1)
        for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
            pipe.close()


class DeviceBridgeGroup:
    """左右IDとprofileを凍結条件から結び、operator操作をroleごとに照合する。"""
    def __init__(self, profile, connection, *, process_type=BridgeProcess, clock=monotonic):
        self.parameters = validate_parameters(profile.input_parameters)
        self.processes = {}
        self.error = None
        self.clock = clock
        if connection is None or set(connection["roles"]) != set(self.parameters["bindings"]):
            raise ValueError("Device profile requires matching explicit --device-config role connections")
        try:
            for role, expected in sorted(self.parameters["bindings"].items()):
                local = connection["roles"][role]
                self.processes[role] = process_type(connection["executable"], role=role,
                    expected_revision=self.parameters["bridge_revision"], clock=clock, config={
                        **local, "expected_device_id": expected["source"]["device_id"],
                        **expected["operator"], "method": expected["profile"]["method"]})
        except BaseException:
            self.close()
            raise

    def state(self):
        roles = {role: {**process.state(), "expected": self.parameters["bindings"][role]}
                 for role, process in self.processes.items()}
        return {"transport": "selfrionette-bridge/v1", "roles": roles, "error": self.error,
            "ready": bool(roles and not self.error and all(item["phase"] == "applied" and item["ready"] for item in roles.values()))}

    def operate(self, role, action, confirmed, observed_phase):
        if role not in self.processes or confirmed is not True or action not in OPERATOR_PHASES:
            raise ValueError("explicit confirmed Device operator action and role required")
        process = self.processes[role]
        state = process.state()
        if (state["pending"] is not None or not state["clock_synchronized"]
                or state["phase"] != observed_phase or observed_phase != OPERATOR_PHASES[action]):
            raise ValueError("Device state changed; inspect the current preparation step")
        process.command(action, confirmed=True)

    def start(self):
        if not self.state()["ready"]:
            raise ValueError("both Device roles require fresh tare, neutral and applied profile")
        for process in self.processes.values():
            process.command("start")

    def poll(self):
        try:
            result = []
            for process in self.processes.values():
                result.extend(process.poll())
            for role, process in self.processes.items():
                state = process.state()
                if state.get("ready") and (state.get("session_neutral_valid") is not True
                        or any(state.get(key) is None for key in ("source", "profile", "operator"))):
                    raise ValueError("ready Device input requires complete profile/neutral provenance")
                for key in ("source", "profile", "operator"):
                    if state.get(key) is not None and state[key] != self.parameters["bindings"][role][key]:
                        raise ValueError("Device source/profile/operator differs from the frozen trial binding")
            return tuple(sorted(result, key=lambda value: value[1]))
        except Exception as exc:
            self.error = str(exc)
            self.close()
            raise

    def close(self):
        errors = []
        for process in self.processes.values():
            try:
                process.close()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            self.error = "; ".join(errors)
            raise RuntimeError(self.error)


def create_application_input(profile, connection):
    binding = resolve_source_session(profile.input_source, profile.provider_id)
    if binding.application_transport is None:
        return None
    factories = {"selfrionette-bridge/v1": DeviceBridgeGroup}
    return factories[binding.application_transport](profile, connection)
