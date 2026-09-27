"""有限な保存Gamepad入力を共同runtimeへ通す診断CLI。socket/serial/実送信なし。"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from fast_arm_core.assembly import FastArmAssembly, FastArmInstance
from xpotato_sim.runtime.composition.fast_arm_coordinated import FastArmCoordinatedGamepadRuntime
from xpotato_sim.schemas import coerce_viewer_control_message
from xpotato_sim.schemas.coordinated import number


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field:{key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"nonfinite JSON constant:{value}")


def run_document(raw: dict) -> dict:
    expected = {"schema", "assembly", "mapping_parameters", "side_to_arm", "epoch", "dt_s", "max_input_age_s", "samples"}
    if type(raw) is not dict or set(raw) != expected or raw["schema"] != "coordinated-gamepad-diagnostic/v1":
        raise ValueError("complete explicit diagnostic v1 document required")
    instances = raw["assembly"]
    if type(instances) is not list or not 1 <= len(instances) <= 2:
        raise ValueError("one or two explicit assembly instances required")
    spec = FastArmAssembly(tuple(FastArmInstance(**item) for item in instances))
    samples = raw["samples"]
    if type(samples) is not list or not 1 <= len(samples) <= 10000:
        raise ValueError("finite sample budget 1..10000 required")
    decoded=[]
    for sample in samples:
        if type(sample) is not dict or set(sample) != {"host_time_s", "message"}:
            raise ValueError("sample requires host_time_s and message")
        decoded.append((number(sample["host_time_s"], "host_time_s"),
                        None if sample["message"] is None else coerce_viewer_control_message(sample["message"])))
    now = [decoded[0][0]]
    app = FastArmCoordinatedGamepadRuntime(assembly=spec, mapping_parameters=raw["mapping_parameters"],
           side_to_arm=raw["side_to_arm"], epoch=raw["epoch"], dt_s=raw["dt_s"],
           max_input_age_s=raw["max_input_age_s"], clock=lambda: now[0])
    rows=[]
    try:
        for clock, message in decoded:
            now[0]=clock
            if message is not None:
                app.ingest(message)
            row=app.tick(epoch=raw["epoch"])
            rows.append(asdict(row))
            if row.state == "faulted":
                break
    finally:
        app.stop()
    return {"schema":"coordinated-gamepad-diagnostic-result/v1", "run_kind":"diagnostic",
            "physical_output":"disabled", "rows":rows, "state":app.runtime.state,
            "reason":app.runtime.reason, "model_sha256":app.runtime.provider.snapshot().model_sha256}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    args=parser.parse_args(argv)
    try:
        if args.document.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("diagnostic input exceeds 4 MiB")
        raw=json.loads(args.document.read_bytes().decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
        result=run_document(raw)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 1 if result["state"] == "faulted" else 0
    except (ValueError, TypeError, OSError) as exc:
        parser.exit(2, f"diagnostic error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
