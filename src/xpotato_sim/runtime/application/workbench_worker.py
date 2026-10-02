"""単一processのmain threadで既存TrialRunnerを駆動する。"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
from time import monotonic

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition
from xpotato_sim.runtime.application.workbench_metrics import process_memory
from xpotato_sim.transport import mujoco_state_to_payload

from xpotato_sim.runtime.application.workbench_control import ACTIVE, INPUT_ACTIVE, INPUT_FINISHED, validate_late_input


def execution_worker(url, config):
    """このprocessのmain threadだけがTrialRunner/ModelExecutionを変更する。"""
    from websockets.sync.client import connect
    from xpotato_sim.runtime.experiment.trial_fixture import load_trial_fixture
    import tracemalloc
    if config.get("diagnostic_memory"):
        tracemalloc.start(1)
    runner = TrialRunner(result_root=Path(config["result_root"]), software_revision=config["software_revision"])
    fixture = load_trial_fixture(Path(config["fixture"])) if config.get("fixture") else None
    profile = None
    generation = 0
    assets = []
    prepared_assets = ()
    prepared_viewer = None
    applied_condition = None
    fixture_start = None
    fixture_index = 0
    next_tick = monotonic()
    last_frame_key = None
    last_status = 0.0
    retired_builds = 0
    deferred_raw = None
    input_diagnostics = {"batch_size": 0, "batch_limit_hit": False, "last_receipt_s": None,
                         "last_ingest_s": None, "processed_sequence": None, "tick_duration_s": None}
    def state():
        return {"phase": runner.status, "ticket": None if runner.ticket is None else asdict(runner.ticket),
            "ticks": runner.tick_count, "profile_id": None if profile is None else profile.name,
            "simulation_time_s": runner.tick_count * profile.dt_s if profile else 0.0,
            "result": None if runner.result is None else runner.result.to_document(), "error": runner.error,
            "condition": None if runner.condition is None else runner.condition.to_document(),
            "applied_condition": applied_condition,
            "worker_pid": os.getpid(),
            "input_diagnostics": {**input_diagnostics, **runner.processed_input_diagnostics},
            "native_builds": retired_builds + runner.model_build_count, "native_live": int(runner.viewer_resources is not None),
            "python_heap": tracemalloc.get_traced_memory()[0] if tracemalloc.is_tracing() else None, **process_memory()}
    def send_state(ws, rid=None, error=None, completed_op=None, operation=None):
        ws.send(json.dumps({"type": "worker_status", "id": rid, "state": state(),
                            "error": error, "completed_op": completed_op, "operation": operation,
                            "generation": generation, "assets": assets}, allow_nan=False))
    def pending_input():
        """advance入口で到着分を再確認する。STOPは次iterationの先頭へ戻す。"""
        nonlocal deferred_raw
        if deferred_raw is not None:
            return None
        samples = []
        while len(samples) < 64:
            try:
                raw = ws.recv(timeout=0)
            except TimeoutError:
                break
            cmd = json.loads(raw)
            if (cmd["op"] != "input" or cmd.get("ticket") != asdict(runner.ticket)
                    or cmd.get("id") is not None):
                deferred_raw = raw
                if cmd["op"] in {"stop", "close"}:
                    return None
                break
            samples.append((cmd["message"], cmd["received_at_s"]))
        if samples:
            input_diagnostics.update(batch_size=len(samples), batch_limit_hit=len(samples) == 64,
                last_receipt_s=samples[-1][1], last_ingest_s=monotonic())
        return tuple(samples)
    try:
        with connect(url, proxy=None, max_size=2**20, max_queue=64) as ws:
            ws.send(json.dumps({"op": "worker", "capability": os.environ.pop("XPOTATO_WORKBENCH_WORKER_KEY")}))
            send_state(ws)
            while True:
                try:
                    if deferred_raw is not None:
                        raw, deferred_raw = deferred_raw, None
                    else:
                        raw = ws.recv(timeout=max(0.001, min(0.02, next_tick - monotonic())))
                except TimeoutError:
                    raw = None
                if raw is not None:
                    cmd = json.loads(raw)
                    op = cmd["op"]
                    batch = [cmd] if op == "input" else []
                    # 既に受信済みの入力をtickより先に消費。STOPはbatchを積分せず優先する。
                    if (batch and runner.status in INPUT_ACTIVE | INPUT_FINISHED and runner.ticket is not None
                            and cmd.get("ticket") == asdict(runner.ticket) and cmd.get("id") is None):
                        while len(batch) < 64:
                            try:
                                following = ws.recv(timeout=0)
                            except TimeoutError:
                                break
                            candidate = json.loads(following)
                            if candidate["op"] in {"stop", "close"}:
                                cmd, op, batch = candidate, candidate["op"], []
                                break
                            if (candidate["op"] != "input" or candidate.get("ticket") != cmd["ticket"]
                                    or candidate.get("id") is not None):
                                deferred_raw = following
                                break
                            batch.append(candidate)
                        input_diagnostics.update(batch_size=len(batch), batch_limit_hit=len(batch) == 64)
                    if op == "close":
                        break
                    try:
                        if op == "prepare":
                            generation = cmd["generation"]
                            assets = []
                            prepared_assets, prepared_viewer = (), None
                            if runner.status in {"faulted", "recording_failed"}:
                                retired_builds += runner.model_build_count
                                runner.close()
                                runner = TrialRunner(result_root=Path(config["result_root"]), software_revision=config["software_revision"])
                            if "condition" in cmd:
                                profile, limits, candidate = resolve_condition(cmd["condition"])
                            else:
                                profile = load_launch_profile(cmd["profile_id"])
                                limits = TrialLimits(config.get("ticks") or profile.steps,
                                    config["input_wait_s"], config["wall_s"], config["prepare_s"])
                                candidate = preset_condition(cmd["profile_id"], limits)
                            runner.prepare(profile, limits)
                            applied_condition = candidate
                            generation = cmd["generation"]
                            asset_root = Path(config["asset_root"])
                            # 単一世代のみ公開。準備中は親が旧allowlistを停止する。
                            if asset_root.is_dir() and asset_root.parent == Path(config["asset_root"]).resolve().parent:
                                shutil.rmtree(asset_root)
                            asset_root.mkdir()
                            files = runner.viewer_resources.write_public_tree(asset_root)
                            prepared_assets = tuple(p.relative_to(asset_root).as_posix() for p in files)
                            prepared_viewer = runner.viewer_resources
                            assets = list(prepared_assets)
                        elif op == "retry":
                            runner.retry()
                            # 再試行は状態だけを初期化し、同じ準備済みモデルの資源を再利用する。
                            if not prepared_assets or runner.viewer_resources is not prepared_viewer:
                                raise RuntimeError("retry model resources differ from prepared assets")
                            assets = list(prepared_assets)
                        elif op == "start":
                            runner.start(runner.ticket, input_provenance=fixture.identity() if fixture else {"source": "workbench-gamepad/v1"})
                            input_diagnostics.update(batch_size=0, batch_limit_hit=False, last_receipt_s=None,
                                last_ingest_s=None, processed_sequence=None, tick_duration_s=None)
                            fixture_start, fixture_index = monotonic(), 0
                            next_tick = fixture_start
                        elif op == "input":
                            if fixture is not None:
                                raise ValueError("明示fixture実行ではbrowser入力を受け付けません")
                            if runner.ticket is None or cmd["ticket"] != asdict(runner.ticket):
                                raise ValueError("旧ticket")
                            if runner.status in INPUT_FINISHED:
                                for item in batch:
                                    if item["ticket"] != asdict(runner.ticket):
                                        raise ValueError("旧ticket")
                                    validate_late_input(item["message"])
                                send_state(ws, cmd.get("id"))
                            else:
                                if any(item["ticket"] != asdict(runner.ticket) for item in batch):
                                    raise ValueError("旧ticket")
                                input_diagnostics["last_receipt_s"] = batch[-1]["received_at_s"]
                                input_diagnostics["last_ingest_s"] = monotonic()
                                runner.ingest_batch(runner.ticket,
                                    tuple((item["message"], item["received_at_s"]) for item in batch),
                                    check_freshness=False)
                                input_diagnostics["processed_sequence"] = runner.processed_input_sequence
                        elif op == "stop":
                            generation = cmd["generation"]
                            if runner.status in ACTIVE:
                                runner.abort(runner.ticket)
                            elif runner.status in {"ready", "unselected"}:
                                runner.discard_prepared()
                                prepared_assets, prepared_viewer = (), None
                            assets = []
                        else:
                            raise ValueError("未知worker operation")
                        if op != "input":
                            send_state(ws, cmd.get("id"), completed_op=op)
                    except Exception as exc:
                        if op in {"prepare", "retry"}:
                            assets = []
                        if op == "prepare" and runner.status == "ready":
                            runner.discard_prepared()
                            assets = []
                            profile = None
                        send_state(ws, cmd.get("id"), str(exc), operation=op)
                    if len(batch) == 64 and runner.status in ACTIVE:
                        runner.advance(runner.ticket, pending_input=lambda: None)
                        if runner.status not in ACTIVE:
                            send_state(ws)
                        continue
                now = monotonic()
                if runner.status in ACTIVE and now >= next_tick:
                    old_phase = runner.status
                    try:
                        if fixture is not None:
                            while fixture_index < len(fixture.samples) and fixture_start + fixture.samples[fixture_index][0] <= now:
                                offset, message = fixture.samples[fixture_index]
                                runner.ingest(runner.ticket, message, received_at_s=fixture_start + offset)
                                fixture_index += 1
                        tick_started = monotonic()
                        runner.advance(runner.ticket, pending_input=None if fixture is not None else pending_input)
                        input_diagnostics["tick_duration_s"] = monotonic() - tick_started
                    except Exception as exc:
                        send_state(ws, error=str(exc))
                    next_tick = max(next_tick + profile.dt_s, now)
                    if runner.status != old_phase or now - last_status >= 0.5:
                        send_state(ws)
                        last_status = now
                if assets and runner.status in {"ready", "waiting_input", "running", "terminal"} and runner.ticket and runner.viewer_resources is not None:
                    frame_key = (generation, runner.ticket, runner.status, runner.tick_count)
                    if frame_key == last_frame_key:
                        continue
                    sample = runner.snapshot()
                    ws.send(json.dumps({"type": "frame", "generation": generation,
                        "ticket": asdict(runner.ticket), "payload": mujoco_state_to_payload(sample)}, allow_nan=False))
                    last_frame_key = frame_key
    finally:
        runner.close()

