"""WorkbenchのCLIとworker/web process起動を所有する。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from xpotato_sim.runtime.composition.launch_profile import list_launch_profiles, repository_workspace
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.experiment.edited_condition import resolve_condition, MAX_BYTES
from xpotato_sim.runtime.runners.application_process import OwnedApplicationWorkers, join_application_job

from xpotato_sim.runtime.application import workbench_service, workbench_worker

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
    if not args.run_once and not getattr(args, "dev_server", False):
        from xpotato_sim.runtime.runners.workbench_web import verify_build_identity
        args.web_dist = (args.web_dist or workspace / "apps/mujoco-viewer/dist").resolve()
        verify_build_identity(args.web_dist, workspace)
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
                [sys.executable, "-m", workbench_service.WORKBENCH_ENTRY_MODULE, "--web-config", str(directory / "worker.json")],
                cwd=workspace, log_path=directory / "web.log", env=env)
            try:
                return asyncio.run(workbench_service.serve_workbench(config, workers, directory,
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
        workbench_worker.execution_worker(f"ws://127.0.0.1:{config['port']}/control", config)
