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
    "browser-smoke": (*UV, "python", "scripts/viewer/run_browser_viewer_smoke.py"),
    "loadcell-plot": (*UV, "python", "scripts/hardware/selfrionette/plot_loadcell_vectors.py"),
    "selfrionette-dry-run": (*UV, "python", "scripts/hardware/selfrionette/run_selfrionette_serial_dry_run.py"),
    "selfrionette-live": (*UV, "python", "scripts/hardware/selfrionette/run_live_selfrionette_runtime.py"),
    "fast-arm-motion-sanity": (*UV, "python", "scripts/diagnostics/fast_arm/run_fast_arm_endpoint_motion_sanity.py"),
}



def native_argv(argv: list[str], executable: str) -> list[str]:
    """Windowsのnpm batchをnative Nodeへ解決し、cmd.exeの引数展開を避ける。"""
    if Path(executable).suffix.lower() not in {".bat", ".cmd"}:
        return [executable, *argv[1:]]
    if argv[0] != "npm":
        raise RuntimeError(f"literal argvを保証できないbatch toolです: {executable}")
    directory = Path(executable).parent
    node = directory / "node.exe"
    node_executable = str(node) if node.is_file() else shutil.which("node")
    if node_executable is None or Path(node_executable).suffix.lower() in {".bat", ".cmd"}:
        raise RuntimeError("npmのnative Node executableが見つかりません")
    cli = directory / "node_modules/npm/bin/npm-cli.js"
    prefix_script = directory / "node_modules/npm/bin/npm-prefix.js"
    if prefix_script.is_file():
        # npm.cmdと同じglobal npm選択。operator argvはこの探索へ渡さない。
        prefix = subprocess.run(
            [node_executable, str(prefix_script)], cwd=ROOT, shell=False,
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        if prefix.returncode == 0:
            candidate = Path(prefix.stdout.strip()) / "node_modules/npm/bin/npm-cli.js"
            if candidate.is_file():
                cli = candidate
    if not cli.is_file():
        raise RuntimeError(f"npmのnative CLIが見つかりません: {cli}")
    return [node_executable, str(cli), *argv[1:]]


def main(command: str, arguments: list[str]) -> int:
    argv = [*COMMANDS[command], *arguments]
    executable = shutil.which(argv[0])
    if executable is None:
        print(f"必要なtoolが見つかりません: {argv[0]}", file=sys.stderr)
        return 1
    try:
        argv = native_argv(argv, executable)
        return subprocess.call(argv, cwd=ROOT, shell=False,
                               env={**os.environ, "PYTHONUTF8": "1"})
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit("登録済みrepository commandが必要です")
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
