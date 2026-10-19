"""stdioのみの明示software fixture。serialをimport/openしない。"""
import json
from pathlib import Path
import sys
import time

fixture = json.loads((Path(__file__).parent / "bridge-sample-v1.json").read_text())
origin = time.monotonic()
phase, scenario, config = "unconfigured", "normal", {}


def stamp():
    return int((time.monotonic() - origin) * 1e6)


def status(request_id):
    return {"schema": fixture["schema"], "event": "status", "bridge_revision": fixture["bridge_revision"],
        "host_clock": fixture["host_clock"], "host_emitted_us": stamp(), "request_id": request_id,
        "phase": phase, "ready": phase in {"applied", "streaming"},
        "session_neutral_valid": phase in {"neutral_ready", "applied", "streaming"},
        "source": fixture["source"] if phase not in {"unconfigured", "configured"} else None,
        "profile": fixture["profile"] if phase in {"applied", "streaming"} else None,
        "operator": fixture["operator"] if phase not in {"unconfigured", "configured"} else None,
        "diagnostics": None}


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    command = json.loads(line)
    action = command["action"]
    request_id = command["request_id"]
    if action == "configure":
        config = command["config"]
        scenario = config["port"]
        fixture["source"]["device_id"] = config["expected_device_id"]
        fixture["operator"] = {key: config[key] for key in ("operator", "posture", "grip")}
        phase = "configured"
    else:
        phase = {"connect": "connected", "tare": "awaiting_neutral", "neutral": "neutral_ready",
            "apply": "applied", "start": "streaming"}.get(action, phase)
    event = status(request_id)
    if scenario == "bad_revision":
        event["bridge_revision"] = "f" * 40
    emit(event)
    if scenario == "eof":
        break
    if action == "status" and scenario == "oversize":
        print("x" * 65537, flush=True)
    if action == "status" and scenario == "overflow":
        for _ in range(2000):
            emit(status(request_id))
    if action == "start":
        if scenario == "stall":
            time.sleep(10)
        event = {**fixture, "host_emitted_us": stamp(), "sample_age_ms": 2}
        event["host_received_us"] = event["host_emitted_us"] - 2000
        if scenario == "delayed_sample":
            time.sleep(.3)
        emit(event)
