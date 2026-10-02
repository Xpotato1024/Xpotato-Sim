"""待機型Workbench。通信の所有者とMuJoCoの単一process所有者を分離する。"""
from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict, deque
from dataclasses import asdict
import hmac
import json
from math import isfinite
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
from time import monotonic
import webbrowser
from urllib.request import ProxyHandler, build_opener

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from xpotato_sim.runtime.composition.launch_profile import list_launch_profiles, load_launch_profile, repository_workspace
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits, resolve_trial_profile
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition, descriptors, condition_diff, MAX_BYTES
from xpotato_sim.runtime.runners.application_process import OwnedApplicationWorkers, join_application_job
from xpotato_sim.runtime.runners.workbench_metrics import process_memory
from xpotato_sim.transport import mujoco_state_to_payload
from xpotato_sim.schemas import parse_viewer_control_message_json

ACTIVE = {"waiting_input", "running", "finalizing"}
INPUT_ACTIVE = {"waiting_input", "running"}
INPUT_FINISHED = {"terminal", "recording_failed"}
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def validate_late_input(message):
    """終了済み同ticketの有効なGamepadだけを棄却する。不正入力は正常化しない。"""
    if not isinstance(message, str) or len(message.encode("utf-8")) > 65536:
        raise ValueError("bounded Gamepad message required")
    parsed = parse_viewer_control_message_json(message)
    if (parsed.gamepad is None or parsed.gamepad.stale or not parsed.gamepad.connected
            or parsed.source_kind != "gamepad" or parsed.sequence is None
            or not isinstance(parsed.metadata.get("viewer_provider_session_id"), str)
            or not parsed.metadata["viewer_provider_session_id"]):
        raise ValueError("unavailable or invalid late Gamepad input")


def decode_request(raw):
    """外部入力はJSON object、有限値、既知fieldだけ。例外は接続内で応答する。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise ValueError("要求は64 KiB以内のJSON文字列です")
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError("重複field")
            obj[k] = v
        return obj
    def invalid(value):
        raise ValueError("非有限値は禁止です")
    def finite_float(value):
        number = float(value)
        if not isfinite(number):
            invalid(value)
        return number
    obj = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite_float)
    if type(obj) is not dict or not isinstance(obj.get("op"), str):
        raise ValueError("opを持つobjectが必要です")
    return obj


def profile_catalog():
    result = []
    for name in list_launch_profiles():
        try:
            p = load_launch_profile(name)
            if p.model is None:
                raise ValueError("旧形式v1はWorkbench対象外です。既存のapp/replay経路で利用してください")
            p = resolve_trial_profile(p)
            result.append({"id": name, "available": True, "reason": None,
                "model": p.model.plugin_id, "dt_s": p.dt_s, "ticks": p.steps})
        except Exception as exc:
            result.append({"id": name, "available": False, "reason": str(exc)})
    return result


class WorkbenchControl:
    """制御revision、connection所有権、bounded履歴。physicsは所有しない。"""
    def __init__(self, catalog, capability, *, require_renderer=True, launcher_limits=None):
        self.catalog = catalog
        self.capability = capability
        self.require_renderer = require_renderer
        self.owner = None
        self.revision = 0
        self.generation = 0
        self.busy = None
        self.busy_operation = None
        self.history = OrderedDict()
        self.results = deque(maxlen=32)
        self.state = {"phase": "unselected", "ticket": None, "ticks": 0, "profile_id": None,
                      "result": None, "error": None}
        self.renderer_epoch = None
        self.stop_id = None
        self.stop_deadline = None
        self.dead = False
        self.next_condition = None
        self.launcher_limits = launcher_limits or {}

    def complete(self, rid, error=None):
        if rid in self.history:
            fingerprint, response = self.history[rid]
            self.history[rid] = (fingerprint, {**response, "completed": True, "error": error})

    def supervise_stop(self, rid=None):
        """STOP固有IDだけで監督する。通常status/prepare完了は期限を解除しない。"""
        if self.stop_id is not None:
            return None
        if self.busy != rid:
            self.complete(self.busy, "停止要求により取り消しました")
        self.generation += 1
        self.stop_id = rid or "stop-" + secrets.token_hex(16)
        self.stop_deadline = monotonic() + 2
        self.busy, self.busy_operation = self.stop_id, "stop"
        self.renderer_epoch = None
        return {"op": "stop", "id": self.stop_id, "generation": self.generation}

    def worker_event(self, event):
        if self.dead or event.get("generation") != self.generation:
            return False
        if self.stop_id is not None:
            if (event.get("id") != self.stop_id or event.get("completed_op") != "stop"
                    or event.get("error") or event["state"]["phase"] not in {"unselected", "terminal", "recording_failed", "faulted"}):
                return False
            self.stop_id = self.stop_deadline = None
        self.update(event)
        return True

    def status(self):
        return {"type": "status", **self.state, "revision": self.revision,
                "generation": self.generation, "busy": self.busy, "profiles": self.catalog,
                "busy_operation": self.busy_operation,
                "results": list(self.results), "renderer_ready": self.renderer_epoch is not None,
                "history_size": len(self.history), "history_limit": 128}

    def authorize(self, client, request):
        capability = request.get("capability")
        if not isinstance(capability, str) or not hmac.compare_digest(capability, self.capability):
            raise ValueError("制御資格がありません")
        if self.owner != client:
            raise ValueError("この接続は制御所有者ではありません")

    def command(self, client, r):
        op = r["op"]
        if op == "status":
            if set(r) != {"op"}:
                raise ValueError("未知field")
            return self.status(), None
        if op == "claim":
            if set(r) != {"op", "capability"}:
                raise ValueError("未知field")
            value = r["capability"]
            if not isinstance(value, str) or not hmac.compare_digest(value, self.capability):
                raise ValueError("制御資格がありません")
            if self.owner not in (None, client) or self.state["phase"] in ACTIVE or self.busy:
                raise ValueError("別の接続または試行が所有しています")
            self.owner = client
            return {"type": "claimed"}, None
        self.authorize(client, r)
        if op in {"edit", "clone", "export", "import", "diff"}:
            expected = {"op", "capability", "revision", "ticket"}
            if "request_id" in r:
                expected.add("request_id")
                if not isinstance(r["request_id"], str) or not ID.fullmatch(r["request_id"]):
                    raise ValueError("有効なeditor要求IDが必要です")
            correlation = {"request_id": r["request_id"]} if "request_id" in r else {}
            if op in {"edit", "import", "diff"}:
                expected.add("condition")
            if op == "clone":
                expected.add("profile_id")
            if set(r) != expected or type(r["revision"]) is not int or r["revision"] != self.revision or r["ticket"] != self.state["ticket"]:
                raise ValueError("未知fieldまたは旧revision/ticketです")
            if self.busy or self.state["phase"] not in {"unselected", "ready", "terminal"}:
                raise ValueError("条件編集は停止中・記録確定後だけです")
            if op == "diff":
                if self.next_condition is None:
                    raise ValueError("次条件がありません")
                return {"type": "condition_diff", **correlation, "changes": condition_diff(self.state.get("applied_condition") or self.next_condition, r["condition"])}, None
            if op == "export":
                if self.next_condition is None:
                    raise ValueError("次条件がありません")
            else:
                if op == "clone":
                    if r["profile_id"] not in list_launch_profiles():
                        raise ValueError("cloneは登録preset IDだけです。server pathは受け付けません")
                    selected = load_launch_profile(r["profile_id"])
                    value = preset_condition(r["profile_id"], TrialLimits(self.launcher_limits.get("ticks") or selected.steps,
                        self.launcher_limits.get("input_wait_s", 5), self.launcher_limits.get("wall_s", 360),
                        self.launcher_limits.get("prepare_s", 30)))
                else:
                    value = r["condition"]
                if op == "import":
                    if type(value) is not str:
                        raise ValueError("importはJSON文字列です。server pathは受け付けません")
                    value = value.encode("utf-8")
                self.next_condition = resolve_condition(value)[2]
                self.revision += 1
            return {"type": "edited_condition", **correlation, "condition": self.next_condition,
                "descriptors": descriptors(self.next_condition), "revision": self.revision,
                "generation": self.generation, "ticket": self.state["ticket"]}, None
        if op == "input":
            if set(r) != {"op", "capability", "ticket", "message"}:
                raise ValueError("未知field")
            if r["ticket"] is None or r["ticket"] != self.state["ticket"]:
                raise ValueError("旧epochまたは実行前の入力です")
            if not isinstance(r["message"], str):
                raise ValueError("入力messageは文字列です")
            if self.state.get("fixture_mode"):
                raise ValueError("明示fixture実行ではbrowser入力を受け付けません")
            if ((not self.busy and self.state["phase"] in INPUT_FINISHED)
                    or (self.stop_id is not None and self.state["phase"] in INPUT_ACTIVE | INPUT_FINISHED)):
                validate_late_input(r["message"])
                return self.status(), None
            if self.busy or self.state["phase"] not in INPUT_ACTIVE:
                raise ValueError("旧epochまたは実行前の入力です")
            return None, {"op": op, "ticket": r["ticket"], "message": r["message"], "received_at_s": monotonic()}
        fields = {"op", "capability", "id", "revision", "ticket"}
        if op == "prepare":
            fields.add("profile_id")
            if "condition" in r:
                fields.add("condition")
        if set(r) != fields or op not in {"prepare", "start", "retry", "stop", "renderer_ready"}:
            raise ValueError("未知operationまたはfieldです")
        rid = r["id"]
        if not isinstance(rid, str) or not ID.fullmatch(rid):
            raise ValueError("有効な要求IDが必要です")
        fingerprint = json.dumps({k: v for k, v in r.items() if k != "capability"}, sort_keys=True)
        if rid in self.history:
            old, response = self.history[rid]
            if old != fingerprint:
                raise ValueError("同一要求IDの内容変更は禁止です")
            return response, None
        if type(r["revision"]) is not int or r["revision"] != self.revision:
            raise ValueError("旧revisionです。状態を再取得してください")
        if r["ticket"] != self.state["ticket"]:
            raise ValueError("旧trial ticketです")
        phase = self.state["phase"]
        if op == "stop" and self.stop_id is not None:
            raise ValueError("停止要求は受付済みです")
        if op != "stop" and self.busy:
            raise ValueError("処理中です")
        if op == "prepare":
            if phase not in {"unselected", "ready", "terminal", "faulted", "recording_failed"}:
                raise ValueError("停止中の正常なownerだけが準備できます")
            if not any(p["id"] == r["profile_id"] and p["available"] for p in self.catalog):
                raise ValueError("利用可能な登録profile IDが必要です")
            if "condition" in r:
                _, _, normalized = resolve_condition(r["condition"])
                if normalized["preset_id"] != r["profile_id"]:
                    raise ValueError("preset/condition ID不一致")
                r = {**r, "condition": normalized}
            self.generation += 1
            self.renderer_epoch = None
        elif op == "retry":
            if phase != "terminal":
                raise ValueError("保存済み終端だけが再試行できます")
            self.renderer_epoch = None
        elif op == "renderer_ready":
            if phase != "ready" or r["ticket"] is None:
                raise ValueError("ready試行の初期scene ACKが必要です")
            self.renderer_epoch = r["ticket"]["epoch"]
        elif op == "start":
            if phase != "ready" or (self.require_renderer and self.renderer_epoch != r["ticket"]["epoch"]):
                raise ValueError("初期scene・shader準備完了後に開始してください")
        elif op == "stop" and not self.busy and phase not in ACTIVE | {"ready"}:
            raise ValueError("停止対象がありません")
        self.revision += 1
        response = {"type": "accepted", "id": rid, "revision": self.revision,
                    "completed": op == "renderer_ready"}
        self.history[rid] = (fingerprint, response)
        while len(self.history) > 128:
            self.history.popitem(last=False)
        if op == "renderer_ready":
            return response, None
        if op == "stop":
            return response, self.supervise_stop(rid)
        self.busy = rid
        self.busy_operation = op
        command = {k: v for k, v in r.items() if k != "capability"}
        command["generation"] = self.generation
        return response, command

    def update(self, event):
        self.state.update(event["state"])
        if event.get("id") == self.busy:
            self.busy = None
            self.busy_operation = None
        rid = event.get("id")
        self.complete(rid, event.get("error"))
        result = self.state.get("result")
        if result and not any(x["trial_id"] == result["trial_id"] for x in self.results):
            self.results.append(result)


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
    def state():
        return {"phase": runner.status, "ticket": None if runner.ticket is None else asdict(runner.ticket),
            "ticks": runner.tick_count, "profile_id": None if profile is None else profile.name,
            "simulation_time_s": runner.tick_count * profile.dt_s if profile else 0.0,
            "result": None if runner.result is None else runner.result.to_document(), "error": runner.error,
            "condition": None if runner.condition is None else runner.condition.to_document(),
            "applied_condition": applied_condition,
            "worker_pid": os.getpid(),
            "native_builds": retired_builds + runner.model_build_count, "native_live": int(runner.viewer_resources is not None),
            "python_heap": tracemalloc.get_traced_memory()[0] if tracemalloc.is_tracing() else None, **process_memory()}
    def send_state(ws, rid=None, error=None, completed_op=None, operation=None):
        ws.send(json.dumps({"type": "worker_status", "id": rid, "state": state(),
                            "error": error, "completed_op": completed_op, "operation": operation,
                            "generation": generation, "assets": assets}, allow_nan=False))
    try:
        with connect(url, proxy=None, max_size=2**20, max_queue=8) as ws:
            ws.send(json.dumps({"op": "worker", "capability": os.environ.pop("XPOTATO_WORKBENCH_WORKER_KEY")}))
            send_state(ws)
            while True:
                try:
                    raw = ws.recv(timeout=max(0.001, min(0.02, next_tick - monotonic())))
                except TimeoutError:
                    raw = None
                if raw is not None:
                    cmd = json.loads(raw)
                    op = cmd["op"]
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
                            fixture_start, fixture_index = monotonic(), 0
                            next_tick = fixture_start
                        elif op == "input":
                            if fixture is not None:
                                raise ValueError("明示fixture実行ではbrowser入力を受け付けません")
                            if runner.ticket is None or cmd["ticket"] != asdict(runner.ticket):
                                raise ValueError("旧ticket")
                            if runner.status in INPUT_FINISHED:
                                validate_late_input(cmd["message"])
                                send_state(ws, cmd.get("id"))
                            else:
                                runner.ingest(runner.ticket, cmd["message"], received_at_s=cmd["received_at_s"])
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
                now = monotonic()
                if runner.status in ACTIVE and now >= next_tick:
                    old_phase = runner.status
                    try:
                        if fixture is not None:
                            while fixture_index < len(fixture.samples) and fixture_start + fixture.samples[fixture_index][0] <= now:
                                offset, message = fixture.samples[fixture_index]
                                runner.ingest(runner.ticket, message, received_at_s=fixture_start + offset)
                                fixture_index += 1
                        runner.advance(runner.ticket)
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


class Peer:
    """制御FIFOと最新frame slotを分離。遅いclientは切断し結果は再照会する。"""
    def __init__(self, ws):
        self.ws = ws
        self.control = asyncio.Queue(maxsize=32)
        self.frame = None
        self.wake = asyncio.Event()
        self.closing = False

    def put(self, value):
        if self.closing:
            return
        if value["type"] == "frame":
            self.frame = value
        else:
            self.control.put_nowait(value)
        self.wake.set()

    async def send(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while not self.control.empty():
                await asyncio.wait_for(self.ws.send(json.dumps(self.control.get_nowait(), allow_nan=False)), 2)
            if self.frame is not None:
                value, self.frame = self.frame, None
                await asyncio.wait_for(self.ws.send(json.dumps(value, allow_nan=False)), 2)


def prepare_watchdog_deadline(command, config, now):
    """受付・正規化済みprepareの実効期限へ、親監督の終了猶予だけを加える。"""
    budget = command.get("condition", {}).get("limits", {}).get("prepare_s", config["prepare_s"])
    return now + budget + 2


async def serve_workbench(config, workers, directory, *, open_browser=False, startup_check=False, capability=None, web_process=None):
    control = WorkbenchControl(profile_catalog(), capability or secrets.token_urlsafe(32), require_renderer=not config.get("run_once"), launcher_limits=config)
    control.state["preselected_profile"] = config.get("profile")
    control.state["fixture_mode"] = bool(config.get("fixture"))
    control.state["initial_condition"] = config.get("condition")
    if control.state["initial_condition"] is None and config.get("profile") and not config.get("run_once"):
        selected = load_launch_profile(config["profile"])
        control.state["initial_condition"] = preset_condition(config["profile"], TrialLimits(config.get("ticks") or selected.steps,
            config["input_wait_s"], config["wall_s"], config["prepare_s"]))
    control.next_condition = control.state["initial_condition"]
    control.state["initial_descriptors"] = [] if control.next_condition is None else descriptors(control.next_condition)
    worker_key = secrets.token_urlsafe(32)
    peers = set()
    worker = None
    worker_process = None
    latest_frame = None
    allowed_assets = set()
    pending = asyncio.Queue(maxsize=32)
    urgent_stop = asyncio.Queue(maxsize=1)
    commands_ready = asyncio.Event()
    prepare_deadline = None
    fault_reported = False
    headless_task = None
    origin = f"http://127.0.0.1:{config['web_port']}"

    def enqueue_stop(command):
        nonlocal allowed_assets, latest_frame, prepare_deadline
        if command is None:
            return
        allowed_assets, latest_frame, prepare_deadline = set(), None, None
        for peer in peers:
            peer.frame = None
        while not pending.empty():
            cancelled = pending.get_nowait()
            control.complete(cancelled.get("id"), "停止要求により未実行のまま取り消しました")
        urgent_stop.put_nowait(command)
        commands_ready.set()

    def broadcast(value):
        for peer in tuple(peers):
            try:
                peer.put(value)
            except asyncio.QueueFull:
                peer.closing = True
                asyncio.create_task(peer.ws.close(1013, "制御応答の受領が遅延しています"))

    async def process_request(connection, request):
        if request.headers.get("Host") != f"127.0.0.1:{config['port']}":
            return connection.respond(403, "Host rejected")
        request_origin = request.headers.get("Origin")
        if request_origin is not None and request_origin != origin:
            return connection.respond(403, "Origin rejected")
        if request.path != "/control":
            path = request.path.split("?", 1)[0]
            if path.startswith("/") and path[1:] in allowed_assets:
                target = (Path(config["asset_root"]) / path[1:]).resolve()
                if target.is_relative_to(Path(config["asset_root"]).resolve()) and target.is_file():
                    response = connection.respond(200, "")
                    response.body = target.read_bytes()
                    del response.headers["Content-Length"]
                    response.headers["Content-Length"] = str(len(response.body))
                    response.headers["Cache-Control"] = "no-store"
                    return response
            return connection.respond(404, "Not found")
        return None

    async def handler(ws):
        nonlocal worker, latest_frame, allowed_assets, prepare_deadline
        peer = Peer(ws)
        sender = None
        is_worker = False
        try:
            first = decode_request(await asyncio.wait_for(ws.recv(), 5))
            if first.get("op") == "worker":
                if worker is not None or not hmac.compare_digest(str(first.get("capability", "")), worker_key):
                    await ws.close(1008)
                    return
                is_worker = True
                worker = ws
                async def dispatch():
                    while True:
                        await commands_ready.wait()
                        command = urgent_stop.get_nowait() if not urgent_stop.empty() else pending.get_nowait()
                        if urgent_stop.empty() and pending.empty():
                            commands_ready.clear()
                        await ws.send(json.dumps(command, allow_nan=False))
                sender = asyncio.create_task(dispatch())
                async for raw in ws:
                    event = json.loads(raw)
                    if fault_reported:
                        continue
                    if event["type"] == "frame":
                        if control.stop_id is None and allowed_assets and event["generation"] == control.generation and event["ticket"] == control.state["ticket"]:
                            latest_frame = event
                            broadcast(event)
                    else:
                        if not control.worker_event(event):
                            continue
                        allowed_assets = set(event["assets"])
                        if control.busy is None:
                            prepare_deadline = None
                        if event.get("id"):
                            broadcast({"type": "completed", "id": event.get("id"), "error": event.get("error")})
                        elif event.get("error"):
                            broadcast({"type": "rejected", "op": event.get("operation"), "error": event["error"]})
                        broadcast(control.status())
                return
            if len(peers) >= 8:
                await ws.close(1013)
                return
            peers.add(peer)
            sender = asyncio.create_task(peer.send())
            while True:
                request = {}
                try:
                    request = first if first is not None else decode_request(await ws.recv())
                    first = None
                    if request["op"] == "result":
                        if set(request) != {"op", "trial_id"} or not isinstance(request["trial_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", request["trial_id"]):
                            raise ValueError("有効なtrial IDが必要です")
                        path = Path(config["result_root"]) / request["trial_id"] / "terminal.json"
                        peer.put({"type": "result", "record": json.loads(path.read_text(encoding="utf-8"))})
                        continue
                    if request["op"] == "stop" and urgent_stop.full():
                        raise ValueError("停止要求は受付済みです")
                    if request["op"] not in {"stop", "status", "claim", "result"} and pending.full():
                        raise ValueError("制御queueが満杯です")
                    if request["op"] not in {"status", "claim", "result"} and (worker is None or fault_reported):
                        raise ValueError("実行workerが利用できません")
                    response, command = control.command(peer, request)
                    if command:
                        if command["op"] == "prepare":
                            allowed_assets = set()
                            latest_frame = None
                            prepare_deadline = prepare_watchdog_deadline(command, config, monotonic())
                        if command["op"] == "stop":
                            enqueue_stop(command)
                        else:
                            pending.put_nowait(command)
                        commands_ready.set()
                    if response:
                        peer.put(response)
                    if response and response["type"] == "accepted":
                        broadcast(control.status())
                    if request["op"] == "status" and latest_frame:
                        peer.put(latest_frame)
                except (ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
                    peer.put({"type": "rejected", "request_id": request.get("request_id"), "error": str(exc)[:500]})
        except ConnectionClosed:
            # 切断はfinallyで所有権と停止監督へ反映し、正常closeをhandler障害にしない。
            pass
        finally:
            peers.discard(peer)
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            if is_worker:
                worker = None
                if not fault_reported:
                    enqueue_stop(control.supervise_stop())
            if control.owner is peer:
                control.owner = None
                control.renderer_epoch = None
                if control.state["phase"] in ACTIVE or control.busy:
                    enqueue_stop(control.supervise_stop())

    async with serve(handler, "127.0.0.1", config["port"], process_request=process_request,
                     max_size=2**20, max_queue=8, open_timeout=5, ping_interval=10, ping_timeout=10):
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "XPOTATO_WORKBENCH_WORKER_KEY": worker_key}
        args = [sys.executable, "-m", __name__, "--worker-config", str(directory / "worker.json")]
        worker_process = workers.start(args, cwd=repository_workspace(), log_path=directory / "worker.log", env=env)
        url = f"{origin}/apps/mujoco-viewer/?workbench={config['port']}#capability={control.capability}"
        # capabilityはURL fragmentでbrowserへ直接渡す。標準出力・fileへ保存しない。
        if open_browser:
            webbrowser.open(url)
        print(f"Workbench: {origin}/apps/mujoco-viewer/?workbench={config['port']} (閲覧接続)", flush=True)
        print("制御資格付き画面は --open-browser で開きます。", flush=True)
        ready_at = monotonic()
        def web_ready():
            try:
                with build_opener(ProxyHandler({})).open(f"{origin}/apps/mujoco-viewer/", timeout=0.3) as response:
                    return response.status == 200 and b'id="app"' in response.read(8192)
            except OSError:
                return False
        try:
            while True:
                now = monotonic()
                if config.get("run_once") and worker is not None and headless_task is None:
                    headless_task = asyncio.create_task(run_headless_client(config, control.capability))
                if headless_task is not None and headless_task.done():
                    return await headless_task
                if not fault_reported and (worker_process.poll() is not None or (control.stop_deadline and now >= control.stop_deadline) or (prepare_deadline and now >= prepare_deadline)):
                    fault_reported = True
                    control.dead = True
                    control.state.update(phase="stopping", error="実行worker終了または停止監督期限。所有processの終了を確認中")
                    control.renderer_epoch = None
                    allowed_assets = set()
                    latest_frame = None
                    for peer in peers:
                        peer.frame = None
                    broadcast(control.status())
                    # venv redirectorだけをkillしない。job/process group全体を閉じる。
                    await asyncio.to_thread(workers.close)
                    control.state.update(phase="faulted", error="所有workerの停止完了。結果確定は保存記録で確認してください")
                    rid, control.busy = control.busy, None
                    control.busy_operation = None
                    control.complete(rid, control.state["error"])
                    broadcast({"type": "completed", "id": rid, "error": control.state["error"]})
                    broadcast(control.status())
                    control.stop_deadline = prepare_deadline = None
                    if startup_check or config.get("run_once"):
                        return 1
                if web_process is not None and web_process.poll() is not None:
                    raise RuntimeError("Web workerが終了しました")
                if startup_check and worker is not None and await asyncio.to_thread(web_ready):
                    return 0
                if startup_check and now - ready_at > 30:
                    raise TimeoutError("Workbench起動確認の期限を超えました")
                await asyncio.sleep(0.05)
        finally:
            if headless_task is not None:
                headless_task.cancel()
                await asyncio.gather(headless_task, return_exceptions=True)
            if worker:
                try:
                    await asyncio.wait_for(worker.send(json.dumps({"op": "close"})), 1)
                except Exception:
                    pass
                deadline = monotonic() + 2
                while worker_process.poll() is None and monotonic() < deadline:
                    await asyncio.sleep(0.02)


async def run_headless_client(config, capability):
    """CLIの明示Start。GUIと同じprotocol/service/runnerを通り、Viewer ACKだけを要求しない。"""
    from websockets.asyncio.client import connect
    async with connect(f"ws://127.0.0.1:{config['port']}/control", proxy=None, max_size=2**20) as ws:
        await ws.send(json.dumps({"op": "claim", "capability": capability}))
        await ws.send(json.dumps({"op": "status"}))
        selected = started = False
        async for raw in ws:
            event = json.loads(raw)
            if event.get("type") == "rejected" or (event.get("type") == "completed" and event.get("error")):
                raise RuntimeError(event["error"])
            if event.get("type") != "status" or event.get("busy"):
                continue
            if event["phase"] in {"faulted", "recording_failed"}:
                print(json.dumps(event.get("result") or {"error": event.get("error")}, ensure_ascii=False))
                return 1
            if event["phase"] == "terminal":
                result = event["result"]
                print(json.dumps(result, ensure_ascii=False))
                return int(result["recording"] != "complete" or result["runner_stop_reason"] not in {"simulation_budget", "task_success"})
            if event["phase"] == "unselected" and not selected:
                selected = True
                op, extra = "prepare", {"profile_id": config["profile"]}
                if config.get("condition"):
                    extra["condition"] = config["condition"]
            elif event["phase"] == "ready" and not started:
                started = True
                op, extra = "start", {}
            else:
                continue
            await ws.send(json.dumps({"op": op, "capability": capability, "id": "headless-" + op,
                "revision": event["revision"], "ticket": event["ticket"], **extra}))
    raise RuntimeError("headless制御接続が終了しました")


def run_workbench(args):
    workspace = repository_workspace()
    condition = None
    if getattr(args, "condition", None):
        if args.profile or args.ticks or any(getattr(args, key) is not None for key in ("input_wait_s", "wall_s", "prepare_s")):
            raise ValueError("conditionはprofile/ticks/期限optionと排他です（予算は条件に保存されます）")
        with args.condition.open("rb") as stream:
            _, _, condition = resolve_condition(stream.read(MAX_BYTES + 1))
        args.profile = condition["preset_id"]
    for key, default in (("input_wait_s", 5.), ("wall_s", 360.), ("prepare_s", 30.)):
        if getattr(args, key) is None:
            setattr(args, key, default)
    TrialLimits(args.ticks or 1, args.input_wait_s, args.wall_s, args.prepare_s)
    if args.profile and args.profile not in list_launch_profiles():
        raise ValueError("登録profile IDだけを指定できます")
    args.result_root = args.result_root.resolve()
    if not args.temporary_root.is_absolute() or not args.temporary_root.is_dir():
        raise ValueError("既存の絶対temporary rootが必要です")
    if args.backend_port == args.web_port:
        raise ValueError("Web/control portは分離してください")
    if args.run_once and (not args.profile or not args.fixture or args.startup_check or args.open_browser):
        raise ValueError("run-onceは明示profile/fixtureが必要で、startup-check/open-browserとは排他です")
    capability = sys.stdin.readline().strip() if args.control_stdin else None
    if capability is not None and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", capability):
        raise ValueError("32..128文字の一時制御資格が必要です")
    with tempfile.TemporaryDirectory(prefix="workbench-", dir=args.temporary_root) as temp:
        directory = Path(temp)
        asset_root = directory / "assets"
        asset_root.mkdir()
        config = {"port": args.backend_port, "web_port": args.web_port,
            "result_root": str(args.result_root), "asset_root": str(asset_root),
            "software_revision": args.software_revision, "ticks": args.ticks,
            "input_wait_s": args.input_wait_s, "wall_s": args.wall_s, "prepare_s": args.prepare_s,
            "fixture": None if args.fixture is None else str(args.fixture.resolve()), "profile": args.profile}
        config["web_dist"] = None if args.web_dist is None else str(args.web_dist.resolve())
        config["run_once"] = args.run_once
        config["condition"] = condition
        config["diagnostic_memory"] = args.diagnostic_memory
        (directory / "worker.json").write_text(json.dumps(config), encoding="utf-8")
        with OwnedApplicationWorkers() as web_workers, OwnedApplicationWorkers() as workers:
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "XPOTATO_SIM_LAUNCHER": "1", "XPOTATO_WORKBENCH_PORT": str(args.backend_port),
                "XPOTATO_VITE_CACHE": str(directory / "vite-cache")}
            env.pop("XPOTATO_SIM_DYNAMIC_VIEWER_RESOURCE_ROOT", None)
            web_process = None if args.run_once else web_workers.start(
                [sys.executable, "-m", __name__, "--web-config", str(directory / "worker.json")],
                cwd=workspace, log_path=directory / "web.log", env=env)
            try:
                return asyncio.run(serve_workbench(config, workers, directory,
                    open_browser=args.open_browser, startup_check=args.startup_check, capability=capability, web_process=web_process))
            except Exception:
                for name in ("web.log", "worker.log"):
                    path = directory / name
                    if path.exists():
                        print(path.read_bytes()[-8192:].decode("utf-8", errors="replace"), file=sys.stderr)
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--worker-config", type=Path)
    modes.add_argument("--web-config", type=Path)
    args = parser.parse_args()
    if sys.stdin.buffer.readline() != b"start\n":
        raise SystemExit("worker gate rejected")
    join_application_job()
    config = json.loads((args.worker_config or args.web_config).read_text(encoding="utf-8"))
    if args.web_config:
        if config.get("web_dist"):
            from xpotato_sim.runtime.runners.workbench_web import run_static_viewer
            run_static_viewer(Path(config["web_dist"]), config["web_port"], config["port"])
            raise SystemExit(0)
        import subprocess
        node = shutil.which("node")
        viewer = repository_workspace() / "apps/mujoco-viewer"
        raise SystemExit(subprocess.call([node, str(viewer / "node_modules/vite/bin/vite.js"),
            "--config", str(viewer / "vite.config.ts"), "--configLoader", "runner", "--host", "127.0.0.1", "--port", str(config["web_port"]), "--strictPort"], cwd=viewer))
    else:
        execution_worker(f"ws://127.0.0.1:{config['port']}/control", config)
