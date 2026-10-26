"""有限試行内の連続記録。欠測はnullのまま保存し、表示用の丸め値を使わない。"""
from hashlib import sha256
import json
import os
from xpotato_sim.runtime.scene.objects import canonical


class ContinuousRecord:
    """既存trial directoryに一つの有界JSONLを置く。demoはこのownerを作らない。"""
    def __init__(self, recorder, identity):
        self.recorder, self.identity = recorder, identity
        self.path = recorder.directory / "samples.jsonl.pending"
        self.size = 0
        self._failed = None
        self.digest = sha256()
        with self.path.open("xb"):
            pass

    def append(self, kind, **values):
        try:
            if self._failed is not None:
                raise OSError(self._failed)
            line = canonical({"schema_version": "trial-continuous/v1", **self.identity,
                "kind": kind, **values}) + b"\n"
            if self.size+len(line) > 64*1024*1024:
                raise OSError("continuous record 64 MiB bound exceeded")
            with self.path.open("ab") as stream:
                if stream.write(line) != len(line):
                    raise OSError("incomplete continuous write")
            self.size += len(line)
            self.digest.update(line)
        except Exception as exc:
            self._failed = str(exc)
            raise

    def finish(self):
        if self._failed is not None:
            raise OSError(self._failed)
        with self.path.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
            actual = sha256()
            while block := stream.read(65536):
                actual.update(block)
        if actual.digest() != self.digest.digest():
            raise OSError("continuous record read-back mismatch")
        target = self.path.with_name("samples.jsonl")
        os.link(self.path, target)
        self.path.unlink()
        return {"file": target.name, "sha256": actual.hexdigest(), "bytes": self.size}


def comparison_metrics(directory):
    """完了markerとhashを照合し、保存したtickから比較指標を再計算する。"""
    from pathlib import Path
    root = Path(directory)
    terminal = json.loads((root/"terminal.json").read_bytes())
    if terminal.get("recording") != "complete":
        raise ValueError("completed trial required")
    ref = terminal["continuous_ref"]
    if ref["file"] != "samples.jsonl":
        raise ValueError("invalid continuous reference")
    document = (root/ref["file"]).read_bytes()
    if len(document) != ref["bytes"] or sha256(document).hexdigest() != ref["sha256"]:
        raise ValueError("continuous digest mismatch")
    ticks, peak_q, peak_f, missing = 0, None, None, 0
    for line in document.splitlines():
        row = json.loads(line)
        if any(row[k] != terminal[k] for k in ("trial_id", "epoch", "condition_sha256")):
            raise ValueError("continuous identity mismatch")
        if row["kind"] != "tick":
            continue
        ticks += 1
        if row["tick"] != ticks:
            raise ValueError("nonsequential committed ticks")
        dynamics = row["dynamics"]
        if dynamics is None:
            missing += 1
            continue
        for s in dynamics.get("springs", []):
            peak_q = max(peak_q or 0, abs(s["displacement_m"]))
        for c in dynamics["contacts"]:
            f = c["force_on_geom2_world_n"]
            if f is None:
                missing += 1
            else:
                peak_f = max(peak_f or 0, sum(v*v for v in f)**.5)
    if ticks != terminal["ticks"]:
        raise ValueError("missing committed ticks")
    return {"ticks": ticks, "peak_spring_displacement_m": peak_q,
        "peak_contact_force_n": peak_f, "missing_measurements": missing,
        "runner_stop_reason": terminal["runner_stop_reason"], "task_outcome": terminal["task_outcome"]}
