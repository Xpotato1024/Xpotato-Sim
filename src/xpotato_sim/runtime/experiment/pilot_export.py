"""検証済み有限trialからCSV・図を再生成する。失敗や不完全保存を黙って除外しない。"""
from __future__ import annotations

from collections import Counter
import csv
from itertools import islice
from math import isfinite
import os
from pathlib import Path
import stat

from xpotato_sim.runtime.experiment.trial_analysis import load_validated_trial
from xpotato_sim.runtime.experiment.pilot_session import MAX_RECORD_BYTES, load_session, validate_planned_trial
from xpotato_sim.runtime.scene.objects import canonical, fields, strict_json


def _target_spec(spring_object, target_force_n, window_start_s, window_end_s):
    values = (spring_object, target_force_n, window_start_s, window_end_s)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("誤差の再計算にはspring object、目標N、開始秒、終了秒をすべて明示してください")
    if type(spring_object) is not str or not spring_object or len(spring_object) > 128:
        raise ValueError("bounded spring object ID required")
    if any(type(value) not in (int, float) or not isfinite(value) for value in values[1:]):
        raise ValueError("finite target and analysis interval required")
    if target_force_n < 0 or not 0 <= window_start_s < window_end_s:
        raise ValueError("非負目標と0 <= 開始 < 終了の解析区間が必要です")
    return {"quantity": "spring_restoring_load_n", "sign": "minus_restoring_force_n",
        "object_id": spring_object, "target_n": float(target_force_n),
        "start_s": float(window_start_s), "end_s": float(window_end_s),
        "sampling": "post_control_commit_last_substep", "interval": "start_inclusive_end_exclusive"}


def _spring_series(validated):
    rows = []
    for row in validated.iter_rows(kind="tick"):
        dynamics = row["dynamics"]
        springs = [] if dynamics is None else dynamics.get("springs", [])
        for spring in springs:
            identity = row.get("input_identity") or {}
            oldest = identity.get("processed_oldest_receipt_s")
            rows.append({"tick": row["tick"], "simulation_time_s": row["simulation_time_s"],
                "host_monotonic_s": row["host_monotonic_s"],
                "tick_entry_minus_oldest_receipt_s": None if oldest is None else row["host_monotonic_s"] - oldest,
                "object_id": spring["instance_id"], "displacement_m": spring["displacement_m"],
                "velocity_m_s": spring["velocity_m_s"],
                "spring_restoring_load_n": -spring["restoring_force_n"],
                "spring_damping_load_n": -spring["damping_force_n"],
                "spring_axial_load_n": -spring["axial_force_n"]})
    return rows


def _force_errors(series, analysis, spec):
    """明示したばね復元荷重だけを評価する。接触力や入力N較正へ読み替えない。"""
    if spec is None:
        return None
    result = {"specification": spec, "status": "unavailable", "reason": None,
              "sample_count": 0, "mae_n": None, "bias_n": None, "max_overshoot_n": None}
    if analysis["metrics"]["simulation_time_s"] + 1e-9 < spec["end_s"]:
        result["reason"] = "trial_ended_before_analysis_interval"
        return result
    dt = analysis["condition"]["dt_s"]
    expected_ticks = [tick for tick in range(1, analysis["metrics"]["ticks"] + 1)
                      if spec["start_s"] <= tick * dt < spec["end_s"]]
    selected_ticks = set(expected_ticks)
    selected = [row for row in series if row["object_id"] == spec["object_id"] and row["tick"] in selected_ticks]
    if not expected_ticks or [row["tick"] for row in selected] != expected_ticks:
        result["reason"] = "missing_or_empty_analysis_samples"
        return result
    errors = [row["spring_restoring_load_n"] - spec["target_n"] for row in selected]
    result.update(status="computed", sample_count=len(errors),
        mae_n=sum(abs(value) for value in errors) / len(errors),
        bias_n=sum(errors) / len(errors), max_overshoot_n=max(0., max(errors)))
    return result


def _plot_series(series, output, title, spec):
    if not series:
        return
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True, layout="constrained")
    for object_id in sorted({row["object_id"] for row in series}):
        selected = [row for row in series if row["object_id"] == object_id]
        time = [row["simulation_time_s"] for row in selected]
        axes[0].plot(time, [1000 * row["displacement_m"] for row in selected], label=object_id)
        axes[1].plot(time, [row["spring_restoring_load_n"] for row in selected], label=f"{object_id}: kq")
        axes[1].plot(time, [row["spring_axial_load_n"] for row in selected], linestyle="--", label=f"{object_id}: kq + cv")
    if spec is not None:
        axes[1].axhline(spec["target_n"], color="black", linestyle=":", label=f"Target kq ({spec['object_id']})")
        for axis in axes:
            axis.axvspan(spec["start_s"], spec["end_s"], color="#cbd5e1", alpha=.3)
    axes[0].set(ylabel="Spring displacement (mm)", title=title)
    axes[1].set(xlabel="Simulation time (s)", ylabel="Spring load (N)")
    for axis in axes:
        axis.grid(alpha=.2)
        axis.legend(loc="best")
    fig.savefig(output, dpi=160)
    plt.close(fig)


def _session_links(path):
    if path is None:
        return None, {}
    session, digest = load_session(path)
    records = Path(path).resolve().parent / "records"
    if records.is_symlink() or (records.exists() and not records.is_dir()):
        raise ValueError("pilot records must be a regular directory")
    bound = len(session["trials"]) * 100
    files = sorted(islice(records.glob("slot-*-attempt-*.json"), bound + 1))
    if len(files) > bound:
        raise ValueError("pilot record bound exceeded")
    links, attempts = {}, set()
    for file in files:
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise ValueError("bounded regular pilot link required")
        with file.open("rb") as stream:
            record = fields(strict_json(stream.read(MAX_RECORD_BYTES + 1), max_bytes=MAX_RECORD_BYTES), {
                "schema_version", "session_sha256", "session_id", "participant_id", "slot",
                "repetition", "order_row", "condition_id", "attempt", "retry_reason", "trial_id",
                "epoch", "condition_sha256", "terminal_sha256", "trial_directory",
                "runner_stop_reason", "response",
            }, "pilot trial link")
        slot = record.get("slot")
        if (record.get("schema_version") != "pilot-trial-link/v1"
                or record.get("session_sha256") != digest or type(slot) is not int
                or not 1 <= slot <= len(session["trials"])
                or record.get("session_id") != session["session_id"]
                or record.get("participant_id") != session["participant_id"]):
            raise ValueError("pilot trial link identity mismatch")
        selected = session["trials"][slot - 1]
        if canonical({key: record[key] for key in selected}) != canonical(selected):
            raise ValueError("pilot trial link schedule mismatch")
        attempt = record["attempt"]
        if type(attempt) is not int or not 1 <= attempt <= 100:
            raise ValueError("pilot link attempt must be 1..100")
        retry_reason = record["retry_reason"]
        if attempt > 1:
            if type(retry_reason) is not str or not retry_reason.strip() or len(retry_reason) > 2048:
                raise ValueError("pilot retry requires a bounded reason")
        elif retry_reason is not None:
            raise ValueError("first pilot attempt cannot have a retry reason")
        if (slot, attempt) in attempts:
            raise ValueError("duplicate pilot slot attempt")
        if file.name != f"slot-{slot:03d}-attempt-{attempt:03d}.json":
            raise ValueError("pilot link filename differs from slot attempt")
        if type(record["trial_id"]) is not str or not record["trial_id"] or len(record["trial_id"]) > 1024:
            raise ValueError("bounded pilot trial identity required")
        if record["trial_id"] in links:
            raise ValueError("duplicate trial link")
        attempts.add((slot, attempt))
        links[record["trial_id"]] = record
    if any(attempt > 1 and (slot, attempt - 1) not in attempts for slot, attempt in attempts):
        raise ValueError("pilot retry requires the previous attempt record")
    return session, links


def export_results(*, result_root, output, session_path=None, spring_object=None,
                   target_force_n=None, window_start_s=None, window_end_s=None, plots=True):
    """全試行を1件ずつ検証し、不完全試行も一覧へ残す。出力先は新規directoryだけ。"""
    root = Path(result_root).resolve()
    if not root.is_dir():
        raise ValueError("既存result rootが必要です")
    spec = _target_spec(spring_object, target_force_n, window_start_s, window_end_s)
    session, links = _session_links(session_path)
    # 単一trialか、直属trial directoryの集合だけを読む。隠れた再帰探索を行わない。
    directories = [root] if (root / "start.json").exists() or (root / "terminal.json").exists() else sorted(p for p in root.iterdir() if p.is_dir())
    if not directories or len(directories) > 1000:
        raise ValueError("1..1000個のtrial directoryが必要です")
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "series").mkdir()
    if plots:
        (destination / "figures").mkdir()
    analyses, failures, table, seen = [], [], [], set()
    for index, directory in enumerate(directories, 1):
        try:
            trial = load_validated_trial(directory)
            analysis = trial.analysis()
            if analysis["trial_id"] in seen:
                raise ValueError("duplicate trial identity across directories")
            link = links.get(analysis["trial_id"])
            if link is not None and (link["condition_sha256"] != analysis["condition_sha256"]
                                    or link["epoch"] != analysis["epoch"]
                                    or link["terminal_sha256"] != analysis["terminal_sha256"]
                                    or link["runner_stop_reason"] != analysis["runner_stop_reason"]):
                raise ValueError("pilot link differs from verified trial")
            if link is not None:
                validate_planned_trial(session, slot=link["slot"], analysis=analysis)
            series = _spring_series(trial)
            errors = _force_errors(series, analysis, spec)
            analysis.update(source_directory=str(directory), pilot_link=link, force_error=errors)
            task = analysis["task_outcome"] or {}
            display = analysis["condition"].get("display_comparison")
            metrics = analysis["metrics"]
            passive = analysis["condition"]["schema_version"] == "trial-condition/v2"
            table_row = {"directory": directory.name, "integrity": "verified", "trial_id": analysis["trial_id"],
                "participant_id": None if link is None else link["participant_id"],
                "session_id": None if link is None else link["session_id"],
                "slot": None if link is None else link["slot"], "attempt": None if link is None else link["attempt"],
                "condition_id": None if link is None else link["condition_id"],
                "condition_sha256": analysis["condition_sha256"],
                "input_provider": None if passive else analysis["condition"]["input"]["provider"],
                "display_mode": None if display is None else display["presentation"]["mode"],
                "runner_stop_reason": analysis["runner_stop_reason"], "task_classification": task.get("classification"),
                "task_reason": task.get("reason"), "ticks": metrics["ticks"],
                "simulation_time_s": metrics["simulation_time_s"],
                "peak_spring_displacement_m": metrics["peak_spring_displacement_m"],
                "peak_all_contact_point_force_n": metrics["peak_contact_force_n"],
                "force_error_status": None if errors is None else errors["status"],
                "mae_n": None if errors is None else errors["mae_n"],
                "bias_n": None if errors is None else errors["bias_n"],
                "max_overshoot_n": None if errors is None else errors["max_overshoot_n"],
                "error": analysis["error"]}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            failure = {"directory": str(directory), "error": str(exc)}
            failures.append(failure)
            table.append({"directory": directory.name, "integrity": "invalid_or_incomplete", "error": str(exc)})
            continue
        # 出力障害はtrialの不正へ読み替えずexport自体を失敗にする。
        prefix = f"trial-{index:04d}"
        if series:
            with (destination / "series" / f"{prefix}.csv").open("x", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(series[0]))
                writer.writeheader(); writer.writerows(series)
            if plots:
                _plot_series(series, destination / "figures" / f"{prefix}.png", analysis["trial_id"], spec)
        seen.add(analysis["trial_id"])
        analyses.append(analysis)
        table.append(table_row)
    # JSONを最後に置き、CSVだけ残った中断exportを完了reportと誤認させない。
    columns = list(dict.fromkeys(key for row in table for key in row))
    with (destination / "trials.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader(); writer.writerows(table)
    linked_ids = {link["participant_id"] for link in links.values() if link["trial_id"] in seen}
    software_only = session is not None and session["usage"] == "software_validation"
    missing = sorted(set(links) - seen)
    unlinked = sorted(seen - set(links)) if session is not None else []
    linked_slots = {link["slot"] for link in links.values()}
    unattempted = [trial["slot"] for trial in session["trials"] if trial["slot"] not in linked_slots] if session is not None else []
    report = {"schema_version": "pilot-analysis/v1", "trial_count": len(directories),
        "verified_count": len(analyses), "invalid_or_incomplete_count": len(failures),
        "stop_reasons": dict(Counter(item["runner_stop_reason"] for item in analyses)),
        "participant_count": 0 if software_only else len(linked_ids),
        "software_fixture_id_count": len(linked_ids) if software_only else 0,
        "session": session, "missing_linked_trial_ids": missing,
        "unlinked_trial_ids": unlinked, "unattempted_slots": unattempted,
        "session_linkage_complete": None if session is None else not (missing or unlinked or unattempted),
        "target_specification": spec, "trials": analyses, "invalid_or_incomplete": failures,
        "interpretation": "task_successは保存されたTask分類です。観測時間満了を押圧成功と呼ばず、反復試行やsoftware fixtureを参加者数に数えません。kqは接触力・指先入力力とは別量です。"}
    data = canonical(report) + b"\n"
    pending = destination / "analysis.json.pending"
    with pending.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if pending.read_bytes() != data:
        raise OSError("analysis report read-back mismatch")
    os.link(pending, destination / "analysis.json")
    try:
        pending.unlink()
    except OSError:
        pass
    return report
