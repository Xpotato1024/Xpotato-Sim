"""接続・制御FIFO・描画frame配信とworker停止監督を所有する。"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
from time import monotonic
import webbrowser
from urllib.request import ProxyHandler, build_opener

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile, repository_workspace
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, descriptors

from xpotato_sim.runtime.application.workbench_control import ACTIVE, WorkbenchControl, decode_request, profile_catalog
from xpotato_sim.runtime.application.workbench_client import run_headless_client

WORKBENCH_ENTRY_MODULE = "xpotato_sim.runtime.runners.workbench"


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
        if value["type"] == "frame" and not value.get("required"):
            self.frame = value
        else:
            self.frame = None
            self.control.put_nowait(value)
        self.wake.set()

    def invalidate_frames(self):
        """Drop queued displays on STOP/fault, retaining ordered control replies."""
        self.frame = None
        retained = asyncio.Queue(maxsize=self.control.maxsize)
        while not self.control.empty():
            value = self.control.get_nowait()
            if value["type"] != "frame":
                retained.put_nowait(value)
        self.control = retained
        if not self.control.empty():
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
    trial_deadline = None
    finalizing_deadline = None
    fault_reported = False
    headless_task = None
    origin = f"http://127.0.0.1:{config['web_port']}"

    def enqueue_stop(command):
        nonlocal allowed_assets, latest_frame, prepare_deadline
        if command is None:
            return
        allowed_assets, latest_frame, prepare_deadline = set(), None, None
        for peer in peers:
            peer.invalidate_frames()
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
        nonlocal worker, latest_frame, allowed_assets, prepare_deadline, trial_deadline, finalizing_deadline
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
                        if event["state"]["phase"] == "finalizing" and finalizing_deadline is None:
                            finalizing_deadline = monotonic() + 4
                        elif event["state"]["phase"] in {"terminal", "recording_failed", "ready", "unselected"}:
                            trial_deadline = finalizing_deadline = None
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
                        if command["op"] == "start":
                            budget = (control.state.get("applied_condition") or {}).get("limits", {}).get("wall_s", config["wall_s"])
                            trial_deadline = monotonic() + budget + 4
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
        args = [sys.executable, "-m", WORKBENCH_ENTRY_MODULE, "--worker-config", str(directory / "worker.json")]
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
                if not fault_reported and (worker_process.poll() is not None or (control.stop_deadline and now >= control.stop_deadline) or (prepare_deadline and now >= prepare_deadline) or (trial_deadline and now >= trial_deadline) or (finalizing_deadline and now >= finalizing_deadline)):
                    fault_reported = True
                    control.dead = True
                    control.state.update(phase="stopping", error="実行worker終了または停止監督期限。所有processの終了を確認中")
                    control.renderer_epoch = None
                    allowed_assets = set()
                    latest_frame = None
                    for peer in peers:
                        peer.invalidate_frames()
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

