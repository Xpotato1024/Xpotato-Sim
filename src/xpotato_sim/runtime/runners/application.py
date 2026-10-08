"""LaunchProfileから既存publisherとWeb viewerをforegroundで一括起動する。"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from math import isfinite
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
import webbrowser

from xpotato_sim.runtime.composition.launch_profile import (
    LaunchProfile,
    decode_launch_profile,
    list_launch_profiles,
    load_launch_profile,
    override_launch_profile,
)
from xpotato_sim.runtime.application.owned_processes import OwnedApplicationWorkers, join_application_job
from xpotato_sim.runtime.runners.model_websocket_publisher import run_model_websocket_publisher
from xpotato_sim.runtime.runners.websocket_publisher import run_input_source_websocket_publisher

ApplicationProfile = LaunchProfile


def _host_for_url(host: str) -> str:
    return f"[{host}]" if ":" in host else host


# 設定型・名前解決は単腕/双腕で分岐させず、共通LaunchProfileへ委譲する。
list_application_profiles = list_launch_profiles
load_application_profile = load_launch_profile
override_application_profile = override_launch_profile
_decode_application_profile = decode_launch_profile


def application_url(profile: ApplicationProfile) -> str:
    host = _host_for_url(profile.host)
    provider = profile.provider_id
    query_values = {"websocketUrl": f"ws://{host}:{profile.backend_port}",
                    "inputProvider": provider or "none", "launchProfile": profile.name}
    if profile.model is not None:
        query_values["inputStartup"] = "scene"
    query = urlencode(query_values)
    return f"http://{host}:{profile.web_port}/apps/mujoco-viewer/?{query}"


def preflight_application(profile: ApplicationProfile) -> str:
    """設定・checkout・依存fileを検査する。port probeとprocess開始はしない。"""
    decoded = _decode_application_profile(
        profile.document_json.encode("utf-8"),
        source_path=profile.source_path,
    )
    if decoded != profile:
        raise ValueError("application profile changed after validation")
    if profile.model is not None:
        profile.build_model().viewer
    if profile.workspace_path != Path(__file__).resolve().parents[4]:
        raise ValueError("workspace differs from the running Python source checkout")
    node = shutil.which("node")
    if node is None:
        raise ValueError("Node.js is missing; install the supported Node.js before app startup")
    viewer = profile.workspace_path / "apps/mujoco-viewer"
    required = ("node_modules/vite/bin/vite.js", "node_modules/react/package.json", "node_modules/@mujoco/mujoco/package.json")
    if not all((viewer / path).is_file() for path in required):
        raise ValueError("viewer dependencies are missing; run npm --prefix apps/mujoco-viewer ci")
    return node


def _require_free_port(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"cannot bind {host}:{port}; another process may own the port") from exc


def _http_ready(profile: ApplicationProfile) -> bool:
    url = f"http://{_host_for_url(profile.host)}:{profile.web_port}/apps/mujoco-viewer/"
    try:
        # local readinessを環境のHTTP proxyへ送らない。
        with build_opener(ProxyHandler({})).open(url, timeout=0.4) as response:
            body = response.read(8192)
            return response.status == 200 and b"/apps/mujoco-viewer/src/main.tsx" in body
    except (OSError, TimeoutError):
        return False


def _wait_ready(predicate: Callable[[], bool], workers: OwnedApplicationWorkers,
                label: str, timeout_s: float) -> None:
    deadline = monotonic() + timeout_s
    while True:
        if any(process.poll() is not None for process in workers.processes):
            raise RuntimeError(f"{label} startup failed: a child process exited")
        if predicate():
            return
        if monotonic() >= deadline:
            raise RuntimeError(f"{label} startup timed out")
        sleep(0.05)


def _show_logs(directory: Path) -> None:
    for name in ("web", "backend"):
        path = directory / f"{name}.log"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 8192))
                text = stream.read().decode("utf-8", errors="replace")
            for line in text.splitlines():
                print(f"[{name}] {line}", flush=True)


def _read_snapshot(path: Path) -> ApplicationProfile:
    with path.open("rb") as stream:
        data = stream.read(524289)
    if len(data) > 524288:
        raise ValueError("application snapshot is too large")
    raw = json.loads(data.decode("utf-8"))
    if type(raw) is not dict or set(raw) != {"source_path", "configuration", "configuration_sha256"}:
        raise ValueError("invalid application snapshot")
    document = json.dumps(
        raw["configuration"],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    profile = _decode_application_profile(
        document,
        source_path=Path(raw["source_path"]),
    )
    if raw["configuration_sha256"] != profile.configuration_sha256:
        raise ValueError("application snapshot digest mismatch")
    return profile


def run_application(profile: ApplicationProfile, *, startup_check: bool = False,
                    startup_timeout_s: float = 30.0,
                    open_browser: Callable[[str], object] | None = None) -> int:
    """有限runtimeを監督し、片側終了・例外・Ctrl+Cで自分のworkerを閉じる。"""
    if not isfinite(startup_timeout_s) or startup_timeout_s <= 0:
        raise ValueError("startup timeout must be finite and positive")
    node = preflight_application(profile)
    _require_free_port(profile.host, profile.web_port)
    _require_free_port(profile.host, profile.backend_port)
    url = application_url(profile)
    mode_label = profile.mode
    print(f"[app] {profile.name} | {mode_label} | physical output: disabled", flush=True)
    print(f"[app] owner PID {os.getpid()}", flush=True)
    print(f"[app] profile SHA-256: {profile.configuration_sha256}", flush=True)
    print(f"[app] finite runtime: {profile.steps} steps / scheduled {profile.steps * profile.interval_s:g} s", flush=True)
    result = 1
    with tempfile.TemporaryDirectory(prefix="xpotato-sim-app-") as temporary:
        directory = Path(temporary)
        snapshot = directory / "profile.json"
        snapshot.write_text(json.dumps({"source_path": str(profile.source_path),
            "configuration": json.loads(profile.document_json),
            "configuration_sha256": profile.configuration_sha256}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                       "XPOTATO_SIM_LAUNCHER": "1", "NO_COLOR": "1", "BROWSER": "none"}
        # 親環境の一時資源を別sessionや単腕起動へ混入させない。
        environment.pop("XPOTATO_SIM_DYNAMIC_VIEWER_RESOURCE_ROOT", None)
        if profile.model is not None:
            bundle = profile.build_model().viewer
            resource_root = directory / "viewer-resources"
            resource_root.mkdir()
            bundle.write_public_tree(resource_root)
            environment["XPOTATO_SIM_DYNAMIC_VIEWER_RESOURCE_ROOT"] = str(resource_root)
        try:
            with OwnedApplicationWorkers() as workers:
                args = [sys.executable, "-u", "-m", "xpotato_sim.runtime.runners.application", "--snapshot", str(snapshot)]
                web = workers.start([*args, "--worker", "web"], cwd=profile.workspace_path,
                                    log_path=directory / "web.log", env=environment)
                def web_ready() -> bool:
                    try:
                        ready = json.loads((directory / "web-ready.json").read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        return False
                    return (type(ready) is dict and set(ready) == {"pid", "configuration_sha256"}
                            and type(ready["pid"]) is int and ready["pid"] > 0
                            and ready["configuration_sha256"] == profile.configuration_sha256
                            and _http_ready(profile))

                _wait_ready(web_ready, workers, "Web", startup_timeout_s)
                print(f"[web] ready; worker PID {web.pid}", flush=True)
                backend = workers.start([*args, "--worker", "backend"], cwd=profile.workspace_path,
                                        log_path=directory / "backend.log", env=environment)
                ready_path = directory / "backend-ready.json"

                def backend_ready() -> bool:
                    try:
                        ready = json.loads(ready_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        return False
                    return (type(ready) is dict and set(ready) == {"pid", "configuration_sha256"}
                            and type(ready["pid"]) is int and ready["pid"] > 0
                            and ready["configuration_sha256"] == profile.configuration_sha256)

                _wait_ready(backend_ready, workers, "Backend", startup_timeout_s)
                print(f"[backend] ready; worker PID {backend.pid}", flush=True)
                print(f"[app] {url}", flush=True)
                if startup_check:
                    print("[app] startup check complete; no WebSocket viewer was connected", flush=True)
                    result = 0
                else:
                    if profile.open_browser:
                        opened = (webbrowser.open if open_browser is None else open_browser)(url)
                        if opened is False:
                            print("[app] browser could not be opened; open the printed URL manually", flush=True)
                    print("[app] Ctrl+C: stop this session and both servers", flush=True)
                    while True:
                        if backend.poll() is not None:
                            result = backend.returncode
                            print(f"[backend] exited: {result}", flush=True)
                            break
                        if web.poll() is not None:
                            raise RuntimeError(f"Web server exited unexpectedly: {web.returncode}")
                        sleep(0.1)
        except KeyboardInterrupt:
            print("[app] operator interruption", flush=True)
            result = 130
        finally:
            _show_logs(directory)
    print("[app] session closed", flush=True)
    return result


def _worker(kind: str, snapshot: Path) -> int:
    # jobへの割当以前に子孫/通信を開始しない。親消失時のEOFでも開始しない。
    if sys.stdin.buffer.readline(16) != b"start\n":
        return 1
    join_application_job()
    profile = _read_snapshot(snapshot)
    node = preflight_application(profile)
    if kind == "backend":
        def ready() -> None:
            target = snapshot.parent / "backend-ready.json"
            pending = target.with_suffix(".tmp")
            pending.write_text(json.dumps({"pid": os.getpid(), "configuration_sha256": profile.configuration_sha256}), encoding="utf-8")
            pending.replace(target)

        if profile.model is not None:
            run_model_websocket_publisher(profile, on_ready=ready)
        else:
            run_input_source_websocket_publisher(input_source=profile.input_source.plugin_id,
                host=profile.host, port=profile.backend_port, steps=profile.steps, dt_s=profile.dt_s,
                interval_s=profile.interval_s, grace_period_s=profile.grace_period_s, preset=profile.preset,
                robot_profile_id=profile.robot.plugin_id, robot_logical_version=profile.robot.contract_version,
                control_mapping_selection=profile.mapping, control_mapping_parameters=profile.mapping_parameters,
                command_semantics_route_selection=profile.route, viewer_provider_id=profile.provider_id, on_ready=ready)
        return 0
    viewer = profile.workspace_path / "apps/mujoco-viewer"
    process = subprocess.Popen([node, str(viewer / "tooling/runApplicationViewer.mjs"),
        str(viewer / "vite.config.ts"), profile.host, str(profile.web_port),
        str(snapshot.parent / "web-ready.json"), profile.configuration_sha256],
        cwd=viewer, stdin=subprocess.DEVNULL, shell=False)
    try:
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    """定義済みworker専用の内部entry。任意実行commandは受け取らない。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("backend", "web"), required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupt)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, interrupt)
    try:
        return _worker(args.worker, args.snapshot)
    except KeyboardInterrupt:
        return 130
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"application worker: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
