"""正規化済みのDevice入力を保持する。校正・極性・filter・port取得は所有しない。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import re
from time import monotonic

from xpotato_sim.runtime.experiment.input_source import InputSourceHealth, InputSourceHealthStatus
from xpotato_sim.runtime.experiment.session_input import SessionInputMessage
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.schemas.coordinated import identifier, number, vector

INPUT_SCHEMA = "selfrionette-device-input/v1"
EVENT_SCHEMA = "selfrionette-bridge-event/v1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DEVICE_ID = re.compile(r"srn-[0-9a-f]{32}\Z")
EVENT_FIELDS = {"schema", "event", "bridge_revision", "host_clock", "host_received_us",
    "host_emitted_us", "sample_age_ms", "relative_backlog_ms", "source", "profile", "operator",
    "domain", "session", "sequence", "device_ms", "settings_generation", "tare_epoch",
    "quality_flags", "normalized", "command", "span_exceeded", "unsupported_direction", "active",
    "ready", "missing", "hardware_acceptance"}
BINDING_FIELDS = {"source", "profile", "operator", "domain", "settings_generation"}


def canonical(value):
    def plain(item):
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [plain(child) for child in item]
        return item
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def strict_json(message):
    if type(message) is not str or len(message.encode("utf-8")) > 65536:
        raise ValueError("bounded Device input JSON required")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate Device input field")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite Device input")
    try:
        return json.loads(message, object_pairs_hook=unique, parse_constant=invalid)
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Device input JSON") from exc


def fields(value, expected, label):
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label}: missing or unknown fields")
    return value


def integer(value, label, maximum=2**64-1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label}: bounded unsigned integer required")
    return value


def validate_binding(binding):
    fields(binding, BINDING_FIELDS, "device binding")
    source = fields(binding["source"], {"device_id", "firmware_version", "source_sha", "protocol"}, "source")
    if not isinstance(source["device_id"], str) or not DEVICE_ID.fullmatch(source["device_id"]):
        raise ValueError("stable srn device ID required")
    identifier(source["firmware_version"], "firmware version")
    if not isinstance(source["source_sha"], str) or not SHA.fullmatch(source["source_sha"]):
        raise ValueError("exact firmware source revision required")
    if type(source["protocol"]) is not int or source["protocol"] != 3:
        raise ValueError("quality-bearing Device Protocol 3 required")
    profile = fields(binding["profile"], {"record", "revision", "assembly_revision", "config_revision", "method"}, "profile")
    identifier(profile["record"], "profile record")
    for key in ("revision", "assembly_revision", "config_revision"):
        integer(profile[key], "profile " + key)
    identifier(profile["method"], "profile method")
    context = fields(binding["operator"], {"operator", "posture", "grip"}, "operator context")
    for key, value in context.items():
        identifier(value, key)
    if binding["domain"] != "normalized_input":
        raise ValueError("explicit normalized_input domain required; values are dimensionless")
    integer(binding["settings_generation"], "settings generation")


def validate_parameters(parameters):
    fields(parameters, {"bridge_revision", "bindings"}, "Device source parameters")
    if not isinstance(parameters["bridge_revision"], str) or not SHA.fullmatch(parameters["bridge_revision"]):
        raise ValueError("exact Device bridge revision required")
    bindings = parameters["bindings"]
    if not isinstance(bindings, Mapping) or not bindings or not set(bindings) <= {"left", "right"}:
        raise ValueError("one or two explicit role bindings required")
    for binding in bindings.values():
        validate_binding(binding)
    if len({b["source"]["device_id"] for b in bindings.values()}) != len(bindings):
        raise ValueError("each role requires a distinct stable device ID")
    # canonical byteを保持し、呼出側のdict変更を取り込まない。
    return strict_json(canonical(parameters))


@dataclass(frozen=True, slots=True)
class DeviceSample:
    role: str
    event_json: str

    def document(self):
        return json.loads(self.event_json)


class DeviceSessionSource:
    """最大2個体の最新値だけを保持し、両者の鮮度のうち古い側を採用する。"""
    requires_new_session = True
    def __init__(self, *, parameters, clock=monotonic):
        self.parameters = validate_parameters(parameters)
        self.bound_roles = frozenset(self.parameters["bindings"])
        self.clock = clock
        self.last_received_at_s = None
        self._samples = {}
        self._identity = {}
        self._updates = 0
        self._frame = RawInputFrame("selfrionette_device", 0., metadata={
            "source_kind": "selfrionette_device", "source_active": False,
            "stale_reason": "awaiting_device_input", "input_unit": "1"})
        self._started = False

    def start(self):
        self._started = True

    def close(self):
        self._samples.clear()
        self._identity.clear()
        self.last_received_at_s = None
        self._started = False
        self._frame = RawInputFrame("selfrionette_device", 0., metadata={
            "source_kind": "selfrionette_device", "source_active": False,
            "stale_reason": "device_input_closed", "input_unit": "1"})

    def current_health(self):
        if self.last_received_at_s is None:
            return InputSourceHealth(InputSourceHealthStatus.DISCONNECTED, reason="not_started", age_ms=0)
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE,
            age_ms=max(0, round((self.clock()-self.last_received_at_s)*1000)))

    def parse_message(self, message):
        raw = fields(strict_json(message), {"schema_version", "role", "source_age_s", "event"}, "Device input")
        if raw["schema_version"] != INPUT_SCHEMA or raw["role"] not in self.parameters["bindings"]:
            raise ValueError("unregistered Device schema or role")
        event = fields(raw["event"], EVENT_FIELDS, "Device bridge sample")
        if (event["schema"] != EVENT_SCHEMA or event["event"] != "sample"
                or event["bridge_revision"] != self.parameters["bridge_revision"]
                or event["host_clock"] != "bridge_process_elapsed_us"):
            raise ValueError("Device bridge identity or clock mismatch")
        expected = self.parameters["bindings"][raw["role"]]
        validate_binding({key: event[key] for key in BINDING_FIELDS})
        if any(event[key] != expected[key] for key in BINDING_FIELDS):
            raise ValueError("Device source/profile/context/role binding mismatch")
        for key in ("session", "sequence", "device_ms", "settings_generation", "tare_epoch",
                    "host_received_us", "host_emitted_us", "sample_age_ms", "relative_backlog_ms"):
            integer(event[key], key)
        if event["host_emitted_us"] < event["host_received_us"]:
            raise ValueError("Device host clock regressed")
        age = number(raw["source_age_s"], "Device source age")
        minimum_age = max(event["sample_age_ms"]/1000,
            (event["host_emitted_us"]-event["host_received_us"])/1e6 + event["relative_backlog_ms"]/1000)
        if age + 1e-9 < minimum_age:
            raise ValueError("Device source age was refreshed by the consumer")
        if (event["ready"] is not True or event["missing"] is not False
                or type(event["hardware_acceptance"]) is not bool):
            raise ValueError("Device profile/tare/neutral is unavailable")
        flags = event["quality_flags"]
        if type(flags) is not list or len(flags) != 7 or any(type(v) is not int or v != 1 for v in flags):
            raise ValueError("Device missing/held/saturated/rejected quality")
        for key in ("span_exceeded", "unsupported_direction", "active"):
            value = event[key]
            if type(value) is not list or len(value) != 7 or any(type(v) is not bool for v in value):
                raise ValueError("seven explicit Device channel flags required")
            if key != "active" and any(value):
                raise ValueError("Device span or supported direction exceeded")
        for key in ("normalized", "command"):
            values = vector(event[key], 7, key)
            if any(abs(v) > 1 for v in values):
                raise ValueError("Device normalized value exceeds its declared domain")
        source_session = f"{event['source']['device_id']}:{event['session']}:{event['tare_epoch']}"
        return SessionInputMessage(raw["role"], source_session, event["sequence"], event["device_ms"]/1000,
            DeviceSample(raw["role"], canonical(event)), age, producer_receipt_s=event["host_received_us"]/1e6)

    def ingest_session_message(self, message):
        if type(message) is not SessionInputMessage or type(message.payload) is not DeviceSample:
            raise TypeError("validated Device SessionInputMessage required")
        role = message.stream_id
        if role not in self.parameters["bindings"] or message.payload.role != role:
            raise ValueError("Device role mismatch")
        received = number(self.clock(), "Device receipt") - message.source_age_s
        if not isfinite(received) or received < 0:
            raise ValueError("Device receipt predates the host clock origin")
        previous = self._identity.get(role)
        identity = (message.session_id, message.sequence, message.timestamp_s, received, message.producer_receipt_s)
        if previous is not None and (identity[0] != previous[0] or identity[1] <= previous[1]
                or identity[2] < previous[2] or identity[4] < previous[4]):
            raise ValueError("Device session/epoch changed or source ordering regressed")
        self._identity[role] = identity
        self._samples[role] = (message.payload, received)
        self._updates += 1
        self._started = True
        if set(self._samples) != set(self.parameters["bindings"]):
            return
        self.last_received_at_s = min(value[1] for value in self._samples.values())
        documents = {key: {**value[0].document(), "received_at_s": value[1]}
                     for key, value in sorted(self._samples.items())}
        epoch = sha256(canonical({key: value[0] for key, value in sorted(self._identity.items())}).encode()).hexdigest()
        snapshot = {"schema_version": "selfrionette-device-snapshot/v1", "devices": documents,
            "source_epoch": epoch, "update_sequence": self._updates,
            "ordering_receipt_s": min(value[4] for value in self._identity.values()),
            "unit": "1", "source_active": True}
        self._frame = RawInputFrame("selfrionette_device",
            min(value[2] for value in self._identity.values()),
            tuple(v for item in documents.values() for v in item["command"]), metadata={
                "source_kind": "selfrionette_device", "source_active": True, "input_unit": "1",
                "selfrionette_device_input_v1": snapshot})

    def read_frame(self):
        return self._frame


def build_reader(parameters, *, runtime_dependencies=None):
    clock = monotonic if runtime_dependencies is None or runtime_dependencies.clock is None else runtime_dependencies.clock
    return DeviceSessionSource(parameters=parameters, clock=clock)
