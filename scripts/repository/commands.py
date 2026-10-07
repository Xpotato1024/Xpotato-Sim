"""justの引数を既存CLIへshellを経由せず転送する。option/defaultは実装本体が所有する。"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
UV = ("uv", "run", "--no-sync", "--no-env-file", "--offline")
CLI = (*UV, "xpotato-sim")
NPM = ("npm", "--prefix", "apps/mujoco-viewer", "run")
COMMANDS = {
    "app": (*CLI, "app"),
    "profile": (*CLI, "profile"),
    "replay": (*CLI, "replay"),
    "viewer-publisher": (*CLI, "viewer"),
    "trial": (*CLI, "trial"),
    "test": (*UV, "pytest", "tests"),
    "lint": (*UV, "ruff", "check", "src", "tests", "scripts", ".github"),
    "typecheck": (*UV, "mypy"),
    "launcher-typecheck": (*UV, "mypy", "scripts/workbench_local.py"),
    "compile": (*UV, "python", "-m", "compileall", "src", "tests", "scripts"),
    "docs-check": (*UV, "python", "scripts/repository/validate_markdown_docs.py"),
    "github-body-check": (*UV, "python", "scripts/repository/validate_github_body_structure.py"),
    "viewer-test": (*NPM, "test"),
    "viewer-typecheck": (*NPM, "typecheck"),
    "viewer-build": (*NPM, "build"),
    "viewer-smoke": (*UV, "python", "scripts/viewer/run_live_viewer_smoke.py"),
    "selfrionette-dry-run": (*UV, "python", "scripts/hardware/selfrionette/run_selfrionette_serial_dry_run.py"),
    "fast-arm-motion-sanity": (*UV, "python", "scripts/diagnostics/fast_arm/run_fast_arm_endpoint_motion_sanity.py"),
}


def main(command: str, arguments: list[str]) -> int:
    argv = [*COMMANDS[command], *arguments]
    executable = shutil.which(argv[0])
    if executable is None:
        print(f"必要なtoolが見つかりません: {argv[0]}", file=sys.stderr)
        return 1
    argv[0] = executable
    try:
        return subprocess.call(argv, cwd=ROOT, shell=False,
                               env={**os.environ, "PYTHONUTF8": "1"})
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit("登録済みrepository commandが必要です")
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
