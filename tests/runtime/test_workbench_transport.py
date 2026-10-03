"""実WebSocketで制御ownerとprivate workerの停止競合を検査する（physicsなし）。"""
import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect

from xpotato_sim.runtime.application.workbench_service import serve_workbench
from xpotato_sim.runtime.runners.workbench_web import build_asset_allowlist


class Worker:
    def __init__(self):
        self.connected = asyncio.Event()
        self.commands = asyncio.Queue()
        self.returncode = None
        self.close_count = 0

    def start(self, args, *, env, **kwargs):
        self.key = env["XPOTATO_WORKBENCH_WORKER_KEY"]
        self.task = asyncio.create_task(self.run())
        return self

    def poll(self):
        return self.returncode

    def close(self):
        self.close_count += 1
        self.returncode = 1

    async def run(self):
        async with connect(self.url, proxy=None) as ws:
            self.ws = ws
            await ws.send(json.dumps({"op": "worker", "capability": self.key}))
            await self.status(0, "unselected")
            self.connected.set()
            async for raw in ws:
                value = json.loads(raw)
                if value["op"] == "close":
                    self.returncode = 0
                    return
                await self.commands.put(value)

    async def status(self, generation, phase, **extra):
        await self.ws.send(json.dumps({"type": "worker_status", "generation": generation,
            "state": {"phase": phase}, "assets": [], **extra}))


async def until(ws, predicate):
    async with asyncio.timeout(5):
        while True:
            event = json.loads(await ws.recv())
            if predicate(event):
                return event


def test_parent_reclaims_finalizing_worker_without_operator_stop(tmp_path,monkeypatch):
    now=[10.]
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_service.monotonic",lambda:now[0])
    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1",0));port=reservation.getsockname()[1]
        worker=Worker();worker.url=f"ws://127.0.0.1:{port}/control"
        service=asyncio.create_task(serve_workbench({"port":port,"web_port":port+1,"prepare_s":10,
            "result_root":str(tmp_path),"asset_root":str(tmp_path)},worker,tmp_path,capability="test-capability"))
        try:
            await asyncio.wait_for(worker.connected.wait(),5)
            async with connect(worker.url,proxy=None) as viewer:
                await viewer.send('{"op":"status"}')
                await until(viewer,lambda e:e["type"]=="status")
                await worker.status(0,"finalizing")
                await until(viewer,lambda e:e["type"]=="status" and e["phase"]=="finalizing")
                now[0]=13.9;await asyncio.sleep(.1)
                assert worker.close_count==0
                now[0]=14.
                state=await until(viewer,lambda e:e["type"]=="status" and e["phase"]=="faulted")
                assert worker.close_count==1
                assert state.get("result") is None and not state["renderer_ready"]
        finally:
            service.cancel();await asyncio.gather(service,return_exceptions=True)
            worker.task.cancel();await asyncio.gather(worker.task,return_exceptions=True)
    asyncio.run(scenario())


def test_terminal_input_and_worker_rejection_preserve_authoritative_reason(tmp_path):
    from test_workbench_late_input import neutral

    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        worker = Worker()
        worker.url = f"ws://127.0.0.1:{port}/control"
        service = asyncio.create_task(serve_workbench({"port": port, "web_port": port + 1,
            "prepare_s": 10, "result_root": str(tmp_path), "asset_root": str(tmp_path)},
            worker, tmp_path, capability="test-capability"))
        try:
            await asyncio.wait_for(worker.connected.wait(), 5)
            async with connect(worker.url, proxy=None) as owner:
                await owner.send('{"op":"claim","capability":"test-capability"}')
                await until(owner, lambda e: e["type"] == "claimed")
                ticket = {"trial_id": "one", "epoch": "one", "condition_sha256": "digest"}
                result = {"trial_id": "one", "runner_stop_reason": "technical_invalid", "error": "original stale"}
                state = {"phase": "terminal", "ticket": ticket, "error": None, "result": result}
                await worker.status(0, "terminal", state=state)
                await until(owner, lambda e: e.get("phase") == "terminal")
                await owner.send(json.dumps({"op": "input", "capability": "test-capability",
                                            "ticket": ticket, "message": neutral()}))
                status = await until(owner, lambda e: e["type"] == "status")
                assert status["result"] == result and worker.commands.empty()
                # 親に既にdispatchされた旧要求のerrorはrunnerの原因を上書きしない。
                await worker.status(0, "terminal", state=state, error="旧ticket", operation="input")
                assert (await until(owner, lambda e: e["type"] == "rejected"))["error"] == "旧ticket"
                status = await until(owner, lambda e: e["type"] == "status")
                assert status["error"] is None and status["result"] == result
                assert status["results"] == [result]
                await owner.send(json.dumps({"op": "input", "capability": "test-capability",
                    "ticket": {**ticket, "epoch": "old"}, "message": neutral()}))
                await until(owner, lambda e: e["type"] == "rejected")
                assert worker.commands.empty()
        finally:
            worker.returncode = 1
            service.cancel()
            await asyncio.gather(service, return_exceptions=True)
            if hasattr(worker, "task"):
                worker.task.cancel()
                await asyncio.gather(worker.task, return_exceptions=True)

    asyncio.run(scenario())


def test_malformed_import_rejects_without_owner_disconnect_and_stop_remains_usable(tmp_path,monkeypatch):
    from xpotato_sim.runtime.experiment.edited_condition import preset_condition
    now=[10.]
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_service.monotonic",lambda:now[0])
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_control.monotonic",lambda:now[0])
    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1",0));port=reservation.getsockname()[1]
        worker=Worker();worker.url=f"ws://127.0.0.1:{port}/control"
        service=asyncio.create_task(serve_workbench({"port":port,"web_port":port+1,"prepare_s":.01,
            "result_root":str(tmp_path),"asset_root":str(tmp_path)},worker,tmp_path,capability="test-capability"))
        try:
            await asyncio.wait_for(worker.connected.wait(),5)
            async with connect(worker.url,proxy=None) as owner:
                await owner.send(json.dumps({"op":"claim","capability":"test-capability"}))
                await until(owner,lambda e:e["type"]=="claimed")
                for value in (None,[],"invalid"):
                    d=preset_condition("dynamic-cube-drop");d["environment"]["parameters"]=value
                    await owner.send(json.dumps({"op":"import","request_id":"bad-import","condition":json.dumps(d),
                        "capability":"test-capability","revision":0,"ticket":None}))
                    rejected=await until(owner,lambda e:e["type"]=="rejected")
                    assert rejected["request_id"]=="bad-import" and "AttributeError" not in rejected["error"]
                await owner.send('{"op":"status"}')
                state=await until(owner,lambda e:e["type"]=="status")
                assert state["revision"]==0 and state["phase"]=="unselected"
                d=preset_condition("dynamic-cube-drop");d["limits"]["prepare_s"]=17.
                await owner.send(json.dumps({"op":"prepare","id":"valid-after-bad","profile_id":"dynamic-cube-drop",
                    "capability":"test-capability","revision":0,"ticket":None,"condition":d}))
                assert (await until(owner,lambda e:e["type"]=="accepted"))["id"]=="valid-after-bad"
                prepared=await asyncio.wait_for(worker.commands.get(),5)
                assert prepared["op"]=="prepare" and prepared["condition"]["limits"]["prepare_s"]==17.
                # configの0.01 s+猶予を超えても、編集条件17 sの親監督がworkerを終了しない。
                now[0]=13.;await asyncio.sleep(.15)
                await owner.send('{"op":"status"}')
                assert (await until(owner,lambda e:e["type"]=="status"))["busy"]=="valid-after-bad"
                assert worker.close_count==0
                await owner.send(json.dumps({"op":"stop","id":"stop-after-bad","capability":"test-capability",
                    "revision":1,"ticket":None}))
                assert (await until(owner,lambda e:e["type"]=="accepted"))["id"]=="stop-after-bad"
                assert (await asyncio.wait_for(worker.commands.get(),5))["op"]=="stop"
        finally:
            worker.returncode=1;service.cancel();await asyncio.gather(service,return_exceptions=True)
            if hasattr(worker,"task"):
                worker.task.cancel();await asyncio.gather(worker.task,return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("disconnect", [False, True])
def test_stop_during_prepare_survives_old_completion_and_status(tmp_path, monkeypatch, disconnect):
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_service.profile_catalog",
                        lambda: [{"id": "known", "available": True}])

    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        worker = Worker()
        worker.url = f"ws://127.0.0.1:{port}/control"
        config = {"port": port, "web_port": port + 1, "prepare_s": 10,
                  "result_root": str(tmp_path), "asset_root": str(tmp_path)}
        service = asyncio.create_task(serve_workbench(config, worker, tmp_path, capability="test-capability"))
        try:
            await asyncio.wait_for(worker.connected.wait(), 5)
            async with connect(worker.url, proxy=None) as owner:
                await owner.send(json.dumps({"op": "claim", "capability": "test-capability"}))
                await until(owner, lambda e: e["type"] == "claimed")
                await owner.send(json.dumps({"op": "prepare", "profile_id": "known", "capability": "test-capability",
                    "id": "prepare", "revision": 0, "ticket": None}))
                prepare = await asyncio.wait_for(worker.commands.get(), 5)
                await until(owner, lambda e: e["type"] == "accepted")
                if disconnect:
                    await owner.close()
                else:
                    await owner.send(json.dumps({"op": "stop", "capability": "test-capability", "id": "stop",
                        "revision": 1, "ticket": None}))
                stop = await asyncio.wait_for(worker.commands.get(), 5)
                assert stop["op"] == "stop" and stop["id"] != prepare["id"]
                await worker.status(prepare["generation"], "ready", id="prepare")
                await worker.status(stop["generation"], "ready")
                async with connect(worker.url, proxy=None) as observer:
                    await observer.send('{"op":"status"}')
                    current = await until(observer, lambda e: e["type"] == "status")
                    assert current["busy"] == stop["id"]
                    fault = await until(observer, lambda e: e.get("phase") == "faulted")
                    assert fault["busy"] is None
                    assert worker.close_count == 1
                    # 死亡後の遅延readyは状態を復活させない。
                    await worker.status(stop["generation"], "ready")
                    await observer.send('{"op":"status"}')
                    assert (await until(observer, lambda e: e["type"] == "status"))["phase"] == "faulted"
                    assert worker.close_count == 1
        finally:
            worker.returncode = 1
            service.cancel()
            await asyncio.gather(service, return_exceptions=True)
            if hasattr(worker, "task"):
                worker.task.cancel()
                await asyncio.gather(worker.task, return_exceptions=True)

    asyncio.run(scenario())


def test_static_mode_requires_real_referenced_build_assets(tmp_path):
    index = tmp_path / "apps/mujoco-viewer/index.html"
    index.parent.mkdir(parents=True)
    index.write_text('<div id="app"></div>', encoding="utf-8")
    with pytest.raises(ValueError, match="module"):
        build_asset_allowlist(tmp_path)
    index.write_text('<script type="module" src="/assets/app.js"></script>', encoding="utf-8")
    with pytest.raises(ValueError, match="参照資産"):
        build_asset_allowlist(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    with pytest.raises(ValueError, match="WASM"):
        build_asset_allowlist(tmp_path)
    (assets / "mujoco.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    assert "/assets/app.js" in build_asset_allowlist(tmp_path)


def test_build_identity_rejects_missing_old_source_and_tampered_output(tmp_path):
    from hashlib import sha256
    from xpotato_sim.runtime.runners.workbench_web import verify_build_identity
    workspace = tmp_path / "source"
    app = workspace / "apps/mujoco-viewer"
    app.mkdir(parents=True)
    (app/"src").mkdir();(app/"tooling").mkdir()
    names = ["index.html", "package-lock.json", "package.json", "src/main.ts", "tooling/test.ts", "vite.config.ts"]
    for name in names:(app/name).write_bytes(name.encode())
    root = tmp_path/"dist"
    root.mkdir()
    with pytest.raises(ValueError,match="固定buildがありません"):verify_build_identity(root, workspace)
    (root/"apps/mujoco-viewer").mkdir(parents=True)
    (root/"apps/mujoco-viewer/index.html").write_text('<script type="module" src="/assets/app.js"></script>')
    (root/"assets").mkdir()
    (root/"assets/app.js").write_bytes(b"export {};")
    (root/"assets/mujoco.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    source = sha256(b"".join(name.encode()+b"\0"+name.encode()+b"\0" for name in names)).hexdigest()
    identity = {"schema_version":"workbench-build/v1","source_sha256":source,
        "assets":{p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}}
    (root/"workbench-build.json").write_text(json.dumps(identity))
    verify_build_identity(root, workspace)
    (app/"src/main.ts").write_bytes(b"changed source")
    with pytest.raises(ValueError,match="現在source/lock"):verify_build_identity(root, workspace)
    (app/"src/main.ts").write_bytes(b"src/main.ts")
    (root/"assets/app.js").write_bytes(b"tampered")
    with pytest.raises(ValueError,match="asset bytes"):verify_build_identity(root, workspace)


def test_untrusted_peer_cannot_stop_owner_and_private_frame_exceeds_command_cap(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_service.profile_catalog", lambda: [])

    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        worker = Worker()
        worker.url = f"ws://127.0.0.1:{port}/control"
        service = asyncio.create_task(serve_workbench({"port": port, "web_port": port + 1, "prepare_s": 10,
            "result_root": str(tmp_path), "asset_root": str(tmp_path)}, worker, tmp_path, capability="test-capability"))
        try:
            await asyncio.wait_for(worker.connected.wait(), 5)
            async with connect(worker.url, proxy=None) as owner, connect(worker.url, proxy=None) as observer:
                await owner.send('{"op":"claim","capability":"test-capability"}')
                await until(owner, lambda e: e["type"] == "claimed")
                ticket = {"trial_id": "trial", "epoch": "epoch", "condition_sha256": "digest"}
                await worker.status(0, "running", state={"phase": "running", "ticket": ticket,
                    "ticks": 7, "simulation_time_s": .14}, assets=["dummy"])
                await until(owner, lambda e: e.get("phase") == "running")
                await observer.send('{"op":"status"}')
                await until(observer, lambda e: e["type"] == "status")
                for raw in ('{', '[]', '{"op":"status","n":1e999}',
                            '{"op":"unknown"}', ' ' * 65537,
                            '{"op":"stop","capability":"test-capability"}',
                            '{"op":"result","trial_id":"../outside"}'):
                    await observer.send(raw)
                    await until(observer, lambda e: e["type"] == "rejected")
                await observer.send('{"op":"status"}')
                status = await until(observer, lambda e: e["type"] == "status")
                assert (status["phase"], status["ticks"], status["simulation_time_s"]) == ("running", 7, .14)
                assert worker.commands.empty()
                payload = "x" * 70000
                await worker.ws.send(json.dumps({"type": "frame", "generation": 0, "ticket": ticket, "payload": payload}))
                assert (await until(observer, lambda e: e["type"] == "frame"))["payload"] == payload
                await worker.status(0, "terminal")
                await until(owner, lambda e: e.get("phase") == "terminal")
        finally:
            worker.returncode = 1
            service.cancel()
            await asyncio.gather(service, return_exceptions=True)
            if hasattr(worker, "task"):
                worker.task.cancel()
                await asyncio.gather(worker.task, return_exceptions=True)

    asyncio.run(scenario())
    assert not any("connection handler failed" in record.getMessage() for record in caplog.records)


def test_run_once_prepare_error_finishes_without_hanging(tmp_path, monkeypatch):
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_service.profile_catalog",
                        lambda: [{"id": "known", "available": True}])

    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        worker = Worker()
        worker.url = f"ws://127.0.0.1:{port}/control"
        service = asyncio.create_task(serve_workbench({"port": port, "web_port": port + 1, "prepare_s": 10,
            "run_once": True, "profile": "known", "result_root": str(tmp_path), "asset_root": str(tmp_path)},
            worker, tmp_path, capability="test-capability"))
        try:
            command = await asyncio.wait_for(worker.commands.get(), 5)
            assert command["op"] == "prepare"
            await worker.status(command["generation"], "unselected", id=command["id"], error="failed build")
            with pytest.raises(RuntimeError, match="failed build"):
                await asyncio.wait_for(service, 5)
        finally:
            service.cancel()
            await asyncio.gather(service, return_exceptions=True)
            if hasattr(worker, "task"):
                worker.task.cancel()
                await asyncio.gather(worker.task, return_exceptions=True)

    asyncio.run(scenario())


def test_slow_browser_keeps_required_terminal_then_retry_frames_in_order():
    from xpotato_sim.runtime.application.workbench_service import Peer
    async def scenario():
        entered, release, complete = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []
        class SlowWire:
            async def send(self, raw):
                entered.set()
                await release.wait()
                sent.append(json.loads(raw))
                if len(sent) == 5: complete.set()
        peer = Peer(SlowWire())
        terminal = {"type": "status", "phase": "terminal", "ticket": "old"}
        final_frame = {"type": "frame", "required": True, "ticket": "old"}
        ready = {"type": "status", "phase": "ready", "ticket": "new"}
        first_frame = {"type": "frame", "required": True, "ticket": "new"}
        latest = {"type": "frame", "ticket": "new", "tick": 2}
        peer.put(terminal)
        sender = asyncio.create_task(peer.send())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            for value in (final_frame, ready, first_frame, {**latest, "tick": 1}, latest): peer.put(value)
            release.set()
            await asyncio.wait_for(complete.wait(), 2)
            assert sent == [terminal, final_frame, ready, first_frame, latest]
        finally:
            release.set(); sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
    asyncio.run(scenario())


def test_browser_required_frames_share_bounded_control_fifo():
    from xpotato_sim.runtime.application.workbench_service import Peer
    async def scenario():
        peer = Peer(None)
        for i in range(32): peer.put({"type": "frame", "required": True, "tick": i})
        with pytest.raises(asyncio.QueueFull): peer.put({"type": "frame", "required": True, "tick": 32})
    asyncio.run(scenario())


def test_browser_stop_invalidates_required_frames_but_keeps_control_replies():
    from xpotato_sim.runtime.application.workbench_service import Peer
    async def scenario():
        peer = Peer(None)
        status = {"type": "status", "generation": 1}
        completed = {"type": "completed", "id": "request"}
        peer.put(status)
        peer.put({"type": "frame", "required": True, "generation": 1})
        peer.put(completed)
        peer.put({"type": "frame", "generation": 1, "tick": 2})
        peer.invalidate_frames()
        assert peer.frame is None
        assert [peer.control.get_nowait(), peer.control.get_nowait()] == [status, completed]
        assert peer.control.empty()
    asyncio.run(scenario())
