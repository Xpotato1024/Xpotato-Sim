"""完了した有限試行の参照連鎖を検査し、保存した数値だけから集計する。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isclose
from pathlib import Path
import re
import stat

from xpotato_sim.runtime.scene.objects import canonical, number, strict_json, vector

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_CONTINUOUS_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 4 * 1024 * 1024
_IDENTITY = ("trial_id", "epoch", "condition_sha256", "software_revision")
_STOP_REASONS = {"simulation_budget", "task_success", "task_failure", "technical_invalid",
                 "wall_timeout", "input_wait_timeout", "operator_abort"}


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid artifact digest")
    return value


def _read(root, name, bound):
    path = root / name
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > bound:
            raise ValueError(f"bounded regular artifact required: {name}")
        with path.open("rb") as stream:
            data = stream.read(bound + 1)
    except OSError as exc:
        raise ValueError(f"artifact unavailable: {name}") from exc
    if len(data) > bound:
        raise ValueError(f"artifact exceeds byte bound: {name}")
    return data


def _reference(root, ref, expected, *, bound=MAX_ARTIFACT_BYTES, continuous=False):
    keys = {"file", "sha256", "bytes"} if continuous else {"file", "sha256"}
    if type(ref) is not dict or set(ref) != keys or ref["file"] != expected:
        raise ValueError(f"invalid artifact reference: {expected}")
    data = _read(root, expected, bound)
    if sha256(data).hexdigest() != _digest(ref["sha256"]):
        raise ValueError(f"artifact digest mismatch: {expected}")
    if continuous and (type(ref["bytes"]) is not int or ref["bytes"] != len(data)):
        raise ValueError("continuous artifact byte count mismatch")
    return data if continuous else strict_json(data, max_bytes=bound)


def _schema(document, expected):
    if type(document) is not dict or document.get("schema_version") != expected:
        raise ValueError(f"unsupported artifact schema: {expected}")
    return document


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"nonnegative integer required: {label}")
    return value


def _same_identity(value, identity):
    if any(value.get(key) != identity[key] for key in _IDENTITY):
        raise ValueError("artifact identity mismatch")


def _same_time(actual, expected, label):
    actual = number(actual, nonnegative=True)
    if not isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-9):
        raise ValueError(f"artifact timestamp mismatch: {label}")
    return actual


def _state(document, model_digest, *, unavailable=False):
    if unavailable and document.get("schema_version") == "unavailable-trial-state/v1":
        if type(document.get("reason")) is not str or not document["reason"]:
            raise ValueError("unavailable final state requires reason")
        return None
    _schema(document, "model-integration-state/v1")
    if document["model_sha256"] != model_digest:
        raise ValueError("state model identity mismatch")
    for name in ("qpos", "qvel", "act", "ctrl", "integration_state"):
        if type(document[name]) is not list:
            raise ValueError("state numeric array required")
        vector(document[name], len(document[name]))
    _integer(document["state_spec"], "state_spec")
    number(document["time_s"], nonnegative=True)
    return document


def _task(value, identity):
    if value is None:
        return
    if type(value) is not dict or value.get("classification") not in {
        "running", "success", "failure", "technical_invalid"
    }:
        raise ValueError("invalid task classification")
    if "epoch" in value and value["epoch"] != identity["epoch"]:
        raise ValueError("task epoch identity mismatch")


@dataclass(frozen=True, slots=True)
class ValidatedTrial:
    """検証時の不変bytesを保持する。解析後のfile変更や返却dict変更を持ち越さない。"""
    _analysis_json: bytes
    _continuous_bytes: bytes

    def analysis(self):
        """条件・来歴・停止理由を欠落させない独立した集計dictを返す。"""
        return json.loads(self._analysis_json)

    def iter_rows(self, *, kind=None):
        """検証済みsnapshotを列挙する。diskを再読込せず、rowの変更は他のreaderへ影響しない。"""
        if kind not in {None, "input", "tick", "terminal"}:
            raise ValueError("unknown continuous row kind")
        # bytes.splitlinesによる全行copyを避け、最大1行だけをdecodeする。
        import io
        for line in io.BytesIO(self._continuous_bytes):
            row = json.loads(line)
            if kind is None or row["kind"] == kind:
                yield row


def _analyze(root):
    terminal_bytes = _read(root, "terminal.json", MAX_ARTIFACT_BYTES)
    terminal = _schema(strict_json(terminal_bytes, max_bytes=MAX_ARTIFACT_BYTES), "trial-terminal/v1")
    if terminal.get("recording") != "complete":
        raise ValueError("completed trial artifact required")
    for key in _IDENTITY:
        if type(terminal.get(key)) is not str or not terminal[key] or len(terminal[key]) > 1024:
            raise ValueError("bounded artifact identity required")
    _digest(terminal["condition_sha256"])
    reason = terminal["runner_stop_reason"]
    if reason not in _STOP_REASONS:
        raise ValueError("unknown terminal stop reason")
    _task(terminal["task_outcome"], terminal)
    if reason == "task_success" and (terminal["task_outcome"] or {}).get("classification") != "success":
        raise ValueError("terminal success contradicts task classification")
    if terminal["error"] is not None and type(terminal["error"]) is not str:
        raise ValueError("invalid terminal error")
    ticks = _integer(terminal["ticks"], "ticks")
    start = _schema(_reference(root, terminal["start_ref"], "start.json"), "trial-start/v1")
    _same_identity(start, terminal)
    saved = _reference(root, start["condition_ref"], "condition.json")
    condition = saved["condition"]
    if type(condition) is not dict or condition.get("schema_version") not in ("trial-condition/v1","trial-condition/v2"):
        raise ValueError("unsupported trial condition version")
    passive = condition["schema_version"] == "trial-condition/v2"
    if passive:
        if any(condition.get(k) is not None for k in ("robot","model","input","mapping","task","evaluation")):
            raise ValueError("passive trial must not invent Robot/Source/Task identity")
        if condition.get("route") != "passive_scene/v1":
            raise ValueError("passive trial route mismatch")
        if condition.get("scene_digest") != sha256(canonical(condition["scene"])).hexdigest():
            raise ValueError("passive scene digest mismatch")
    digest = sha256(canonical(condition)).hexdigest()
    if digest != terminal["condition_sha256"] or digest != saved["condition_sha256"]:
        raise ValueError("condition semantic digest mismatch")
    if type(start["input_provenance"]) is not dict or not start["input_provenance"]:
        raise ValueError("explicit input provenance required")
    origin = saved["origin_profile"]
    if type(origin) is not dict or not isinstance(origin.get("configuration"), dict):
        raise ValueError("origin profile artifact required")
    if sha256(canonical(origin["configuration"])).hexdigest() != _digest(origin["configuration_sha256"]):
        raise ValueError("origin profile digest mismatch")
    model_digest = _digest(condition["model_sha256"])
    initial = _state(_reference(root, start["initial_state_ref"], "initial-state.json"), model_digest)
    final = _state(_reference(root, terminal["final_state_ref"], "final-state.json"),
                   model_digest, unavailable=reason == "technical_invalid")
    dt = number(condition["dt_s"], positive=True)
    _same_time(terminal["simulation_time_s"], ticks * dt, "terminal simulation")
    started = number(start["started_monotonic_s"], nonnegative=True)
    accepting = number(terminal["input_accepting_monotonic_s"], nonnegative=True)
    stopped = number(terminal["terminal_monotonic_s"], nonnegative=True)
    if not started <= accepting <= stopped:
        raise ValueError("terminal/start timestamp ordering mismatch")
    if ticks > _integer(condition["limits"]["max_ticks"], "max_ticks"):
        raise ValueError("committed ticks exceed frozen limit")
    if final is not None:
        _same_time(final["time_s"], initial["time_s"] + ticks * dt, "final state")
        if any(len(final[key]) != len(initial[key]) for key in ("qpos", "qvel", "act", "ctrl", "integration_state")):
            raise ValueError("initial/final state dimensions mismatch")
    data = _reference(root, terminal["continuous_ref"], "samples.jsonl",
                      bound=MAX_CONTINUOUS_BYTES, continuous=True)
    metrics = _continuous_metrics(data, terminal, condition, initial, final, accepting, stopped)
    analysis = {"schema_version": "trial-analysis/v1", **{key: terminal[key] for key in _IDENTITY},
                "terminal_sha256": sha256(terminal_bytes).hexdigest(),
                "simulation_time_s": terminal["simulation_time_s"],
                "condition": condition, "input_provenance": start["input_provenance"],
                "origin_profile": origin, "runner_stop_reason": reason,
                "error": terminal["error"], "task_outcome": terminal["task_outcome"],
                "metrics": {**metrics, "simulation_time_s": terminal["simulation_time_s"],
                            "wall_elapsed_s": stopped - started,
                            "input_preparation_s": accepting - started}}
    return ValidatedTrial(canonical(analysis), data)


def _continuous_metrics(data, terminal, condition, initial, final, accepting, stopped):
    import io
    ticks, inputs, missing = 0, 0, 0
    peak_q = peak_f = None
    last_tick = end = None
    kind_times = {}
    sites_previous, site_paths, springs = {}, {}, {}
    scene = condition["scene"]
    passive = condition["schema_version"] == "trial-condition/v2"
    if scene is not None and (type(scene) is not dict or scene.get("schema_version") not in {"object-scene/v1", "object-scene/v2", "object-scene/v3", "object-scene/v4"}):
        raise ValueError("unsupported scene artifact schema")
    scene_digest = None if scene is None else sha256(canonical(scene)).hexdigest()
    settings = condition.get("dynamics")
    settings_digest = None if settings is None else sha256(canonical(settings)).hexdigest()
    endpoints = set(condition.get("side_to_endpoint",{}).values())
    objects = set() if scene is None else {obj["instance_id"] for obj in scene["objects"]}
    dynamic_scene = scene is not None and scene["schema_version"] in ("object-scene/v2","object-scene/v3","object-scene/v4")
    supports = {item["plane_id"] for item in scene["world"]["support_planes"]} if dynamic_scene else set()
    spring_ids = {obj["instance_id"] for obj in scene["objects"] if obj["motion_type"] == "spring"} if dynamic_scene else set()
    targets = set() if condition["force_signal"] is None else set(condition["force_signal"]["target_object_ids"])
    pairs = {(endpoint, target): {"endpoint_id": endpoint, "object_id": target,
              "peak_resultant_force_n": None, "peak_contact_point_force_n": None,
              "samples_with_contact": 0, "unavailable_contact_points": 0}
             for endpoint in sorted(endpoints) for target in sorted(targets)}
    for line in io.BytesIO(data):
        if len(line) > MAX_ROW_BYTES or not line.endswith(b"\n"):
            raise ValueError("bounded newline-terminated continuous row required")
        row = _schema(strict_json(line, max_bytes=MAX_ROW_BYTES), "trial-continuous/v1")
        _same_identity(row, terminal)
        if end is not None:
            raise ValueError("continuous rows after terminal")
        kind = row["kind"]
        if kind not in {"input", "tick", "terminal"}:
            raise ValueError("unknown continuous row kind")
        host = number(row["host_monotonic_s"], nonnegative=True)
        # tick時刻はadvance入口、input時刻はcallback内でも取得されるため種類別に検査する。
        if not accepting <= host <= stopped or host < kind_times.get(kind, accepting):
            raise ValueError("continuous host timestamp ordering mismatch")
        kind_times[kind] = host
        index = _integer(row["tick"], "continuous tick")
        if kind == "input":
            if index != ticks or type(row["message"]) is not dict:
                raise ValueError("continuous input tick/message mismatch")
            receipt = number(row["received_at_s"], nonnegative=True)
            if not accepting <= receipt <= host:
                raise ValueError("continuous input receipt timestamp mismatch")
            inputs += 1
            continue
        _task(row["task"], terminal)
        if kind == "terminal":
            if (index != ticks or ticks != terminal["ticks"] or host != stopped
                    or row["task"] != terminal["task_outcome"]
                    or row["runner_stop_reason"] != terminal["runner_stop_reason"]
                    or row["error"] != terminal["error"]):
                raise ValueError("continuous terminal marker mismatch")
            end = row
            continue
        ticks += 1
        if index != ticks:
            raise ValueError("nonsequential committed ticks")
        _same_time(row["simulation_time_s"], initial["time_s"] + ticks * condition["dt_s"], "tick simulation")
        if "signals" in row and type(row["signals"]) is not dict:
            raise ValueError("optional recorded signals must be an object")
        if passive:
            checkpoint = row["signals"].get("scene_state_checkpoint_v1")
            if type(checkpoint) is not dict or checkpoint.get("schema_version") != "model-integration-state/v1":
                raise ValueError("passive trial requires a complete integration checkpoint")
            if checkpoint.get("model_sha256") != condition["model_sha256"] or checkpoint.get("mujoco_version") != initial["mujoco_version"]:
                raise ValueError("passive checkpoint model/engine mismatch")
            if checkpoint.get("qpos") != row["qpos"] or checkpoint.get("qvel") != row["qvel"]:
                raise ValueError("passive checkpoint differs from committed state")
            _same_time(checkpoint["time_s"], row["simulation_time_s"], "passive checkpoint")
            if checkpoint.get("state_spec") != initial["state_spec"]:
                raise ValueError("passive checkpoint state specification mismatch")
            if (type(checkpoint.get("integration_state")) is not list
                    or len(checkpoint["integration_state"]) != len(initial["integration_state"])):
                raise ValueError("incomplete passive integration state")
            vector(checkpoint["integration_state"],len(initial["integration_state"]))
        for name in ("qpos", "qvel"):
            vector(row[name], len(initial[name]))
        names = set()
        for site in row["sites"]:
            name = site["name"]
            if type(name) is not str or not name or name in names:
                raise ValueError("distinct recorded site names required")
            names.add(name)
            position = vector(site["position_m"], 3)
            vector(site["quaternion_wxyz"], 4)
            previous = sites_previous.get(name)
            site_paths[name] = site_paths.get(name, 0.) + (0. if previous is None else hypot(*(a-b for a,b in zip(position, previous))))
            sites_previous[name] = position
        if last_tick is not None and names != {site["name"] for site in last_tick["sites"]}:
            raise ValueError("recorded site set changed")
        last_tick = row
        dynamics = row["dynamics"]
        if dynamics is None:
            missing += 1
            continue
        _schema(dynamics, "scene-dynamics-observation/v1")
        if (dynamics["model_sha256"] != condition["model_sha256"]
                or dynamics["scene_digest"] != scene_digest or dynamics["settings_digest"] != settings_digest):
            raise ValueError("dynamics condition identity mismatch")
        _same_time(dynamics["simulation_time_s"], row["simulation_time_s"], "dynamics simulation")
        observed_springs = set()
        for spring in dynamics.get("springs", []):
            oid = spring["instance_id"]
            if oid not in spring_ids or oid in observed_springs:
                raise ValueError("spring object identity mismatch")
            observed_springs.add(oid)
            for key in ("velocity_m_s", "restoring_force_n", "damping_force_n"):
                number(spring[key])
            displacement = abs(number(spring["displacement_m"]))
            force = abs(number(spring["axial_force_n"]))
            peak_q = max(peak_q or 0., displacement)
            entry = springs.setdefault(oid, {"peak_absolute_displacement_m": 0., "peak_absolute_axial_force_n": 0.})
            entry["peak_absolute_displacement_m"] = max(entry["peak_absolute_displacement_m"], displacement)
            entry["peak_absolute_axial_force_n"] = max(entry["peak_absolute_axial_force_n"], force)
        if observed_springs != spring_ids:
            raise ValueError("missing spring object measurement")
        tick_pairs = {}
        for contact in dynamics["contacts"]:
            roles = [contact["role1"], contact["role2"]]
            for role in roles:
                allowed = {"tool": endpoints, "object": objects, "support": supports}.get(role["kind"])
                if allowed is None or role["id"] not in allowed:
                    raise ValueError("contact role identity mismatch")
            pair = None
            sign = 1.
            if roles[0]["kind"] == "tool" and roles[1]["kind"] == "object":
                pair, sign = (roles[0]["id"], roles[1]["id"]), -1.
            elif roles[1]["kind"] == "tool" and roles[0]["kind"] == "object":
                pair = (roles[1]["id"], roles[0]["id"])
            entry = pairs.get(pair)
            if entry is not None:
                aggregate = tick_pairs.setdefault(pair, {"vector": [0.,0.,0.], "complete": True})
            force = contact["force_on_geom2_world_n"]
            if force is None:
                if contact["status"] != "measurement_unavailable":
                    raise ValueError("contact status/measurement mismatch")
                missing += 1
                if entry is not None:
                    entry["unavailable_contact_points"] += 1
                    aggregate["complete"] = False
                continue
            if contact["status"] != "measured":
                raise ValueError("contact status/measurement mismatch")
            force = vector(force, 3)
            magnitude = hypot(*force)
            peak_f = max(peak_f or 0., magnitude)
            if entry is not None:
                entry["peak_contact_point_force_n"] = max(entry["peak_contact_point_force_n"] or 0., magnitude)
                aggregate["vector"] = [a + sign*b for a,b in zip(aggregate["vector"], force)]
        for pair, aggregate in tick_pairs.items():
            entry = pairs[pair]
            entry["samples_with_contact"] += 1
            if aggregate["complete"]:
                entry["peak_resultant_force_n"] = max(entry["peak_resultant_force_n"] or 0., hypot(*aggregate["vector"]))
    if end is None:
        raise ValueError("continuous terminal row missing")
    if final is not None and last_tick is not None:
        if any(last_tick[name] != final[name] for name in ("qpos", "qvel")):
            raise ValueError("final state differs from last committed tick")
    return {"ticks": ticks, "input_samples": inputs, "peak_spring_displacement_m": peak_q,
            "peak_contact_force_n": peak_f, "missing_measurements": missing,
            "springs": springs, "target_contacts": list(pairs.values()),
            "site_path_length_between_recorded_ticks_m": site_paths,
            "observation_phase": "post_control_commit_last_substep",
            "contact_peak_scope": "all_recorded_contact_points"}


def load_validated_trial(directory):
    """上限付きsnapshot全体を検証する。失敗/中断も有効な保存済み試行として返す。"""
    try:
        return _analyze(Path(directory))
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError(f"malformed trial artifact: {exc}") from exc


def analyze_trial(directory):
    """CLI等から使う条件付き集計。課題成績や実機受入の閾値は追加しない。"""
    return load_validated_trial(directory).analysis()
