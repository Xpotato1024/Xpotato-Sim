"""統一起動の事前拒否、provider拘束、owned worker cleanupを検証する。"""
from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile, override_launch_profile
from xpotato_sim.runtime.runners import application as app
from xpotato_sim.runtime.application.owned_processes import OwnedApplicationWorkers
from xpotato_sim.runtime.control.viewer_control_ingress import build_viewer_input_source, ingest_viewer_control_message
from xpotato_sim.plugins.input_sources.viewer import viewer_health

ROOT = Path(__file__).resolve().parents[2]


def test_check_has_no_network_process_or_browser(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("--check must not launch anything")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(app.webbrowser, "open", forbidden)
    # CIのPython jobにfrontend node_modulesは不要。依存file検査は別testで検証。
    monkeypatch.setattr(app, "preflight_application", lambda profile: "node")
    cli = importlib.import_module("xpotato_sim.cli.main")
    assert cli.main(["app", "--profile", "sim-gamepad", "--check", "--web-port", "5197", "--no-browser"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["configuration"]["web"]["port"] == 5197
    assert output["configuration"]["web"]["open_browser"] is False
    assert output["resolved"]["physical_output"] == "disabled"


def test_url_owns_endpoint_and_provider_without_silent_default():
    profile = override_launch_profile(load_launch_profile("sim-keyboard"), web_port=5197, backend_port=8797)
    url = urlparse(app.application_url(profile))
    assert url.port == 5197
    assert parse_qs(url.query)["websocketUrl"] == ["ws://127.0.0.1:8797"]
    assert parse_qs(url.query)["inputProvider"] == ["keyboard/v1"]
    assert parse_qs(urlparse(app.application_url(load_launch_profile("replay-sweep"))).query)["inputProvider"] == ["none"]


def test_missing_node_rejected_before_launch(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Node.js"):
        app.preflight_application(load_launch_profile("sim-gamepad"))


def test_busy_port_rejected_without_killing_listener(monkeypatch):
    profile = load_launch_profile("sim-gamepad")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        profile = override_launch_profile(profile, web_port=listener.getsockname()[1])
        monkeypatch.setattr(app, "preflight_application", lambda _: "node")
        monkeypatch.setattr(app, "OwnedApplicationWorkers", lambda: pytest.fail("spawn after busy port"))
        with pytest.raises(RuntimeError, match="cannot bind"):
            app.run_application(profile)
        assert listener.fileno() >= 0


def test_worker_requires_parent_start_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"")})())
    monkeypatch.setattr(app, "_read_snapshot", lambda _: pytest.fail("read before start gate"))
    assert app._worker("web", tmp_path / "missing.json") == 1


def test_snapshot_digest_is_rechecked(tmp_path):
    profile = load_launch_profile("sim-keyboard")
    raw = {"source_path": str(profile.source_path), "configuration": json.loads(profile.document_json),
           "configuration_sha256": profile.configuration_sha256}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert app._read_snapshot(path) == profile
    raw["configuration"]["execution"]["steps"] = 5
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        app._read_snapshot(path)


def test_viewer_provider_mismatch_invalidates_previous_input():
    source = build_viewer_input_source(clock=lambda: 1.0)
    gamepad = {"type": "viewer_control_message", "timestamp_s": 1.0, "source_kind": "gamepad",
               "gamepad": {"connected": True, "axes": [0.5, 0.0, 0.0], "buttons": []}}
    ingest_viewer_control_message(source, gamepad, expected_provider_id="gamepad/v1")
    assert viewer_health(source).status.value == "active"
    with pytest.raises(ValueError, match="provider does not match"):
        ingest_viewer_control_message(source, gamepad, expected_provider_id="keyboard/v1")
    assert viewer_health(source).status.value == "invalid"


def test_selected_mapping_and_provider_reach_canonical_publisher(monkeypatch):
    from xpotato_sim.runtime.runners import websocket_publisher as publisher
    profile = load_launch_profile("sim-gamepad")
    seen = []
    async def capture(**kwargs):
        seen.append(kwargs)
    monkeypatch.setattr(publisher, "_run_input_source_websocket_publisher_async", capture)
    publisher.run_input_source_websocket_publisher(input_source="viewer",
        control_mapping_selection=profile.mapping, control_mapping_parameters={"gamepad_speed_m_s": 0.02},
        command_semantics_route_selection=profile.route, viewer_provider_id="gamepad/v1")
    assert seen[0]["control_mapping_parameters"]["gamepad_speed_m_s"] == 0.02
    assert seen[0]["control_mapping_selection"] == profile.mapping
    assert seen[0]["viewer_provider_id"] == "gamepad/v1"


def _alive(pid):
    if os.name == "nt":
        import ctypes as c
        from ctypes import wintypes as w
        api = c.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.WaitForSingleObject.restype = w.DWORD
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 0x102
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        # Linux orphan zombieは終了済みとして扱う。
        stat = Path(f"/proc/{pid}/stat")
        return not stat.exists() or stat.read_text().split()[2] != "Z"
    except ProcessLookupError:
        return False


@pytest.mark.parametrize("fail", (False, True))
def test_owned_worker_and_descendant_cleanup_keeps_unrelated_process(tmp_path, fail):
    marker = tmp_path / "descendant.json"
    child_code = "import os,time,pathlib;pathlib.Path(" + repr(str(marker)) + ").write_text(str(os.getpid()));time.sleep(60)"
    worker_code = ("import sys,subprocess,time;from xpotato_sim.runtime.application.owned_processes import join_application_job;"
                   "assert sys.stdin.buffer.readline(16)==bytes([115,116,97,114,116,10]);join_application_job();"
                   "subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]);time.sleep(60)")
    sentinel = subprocess.Popen([sys._base_executable, "-c", "import time;time.sleep(60)"], stdin=subprocess.DEVNULL)
    descendant = None
    try:
        try:
            with OwnedApplicationWorkers() as workers:
                worker = workers.start([sys.executable, "-u", "-c", worker_code], cwd=ROOT,
                                       log_path=tmp_path / "worker.log", env=dict(os.environ))
                deadline = time.monotonic() + 15
                while not marker.exists() and time.monotonic() < deadline:
                    assert worker.poll() is None, (tmp_path / "worker.log").read_text()
                    time.sleep(0.02)
                assert marker.exists(), (tmp_path / "worker.log").read_text()
                descendant = int(marker.read_text())
                assert _alive(descendant)
                if fail:
                    raise ValueError("original failure")
        except ValueError as exc:
            assert fail and str(exc) == "original failure"
        deadline = time.monotonic() + 5
        while descendant is not None and _alive(descendant) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker.poll() is not None
        assert descendant is not None and not _alive(descendant)
        assert sentinel.poll() is None
        workers.close()
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5)
