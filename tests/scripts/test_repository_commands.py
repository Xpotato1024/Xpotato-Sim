"""新just入口のargv境界とnative終了codeをWindows/Linuxで検証する。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/repository/commands.py"
spec = importlib.util.spec_from_file_location("repository_commands", SCRIPT)
commands = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commands)


def test_all_recipe_targets_exist():
    for argv in commands.COMMANDS.values():
        if argv[:len(commands.UV)] == commands.UV and argv[len(commands.UV)] == "python":
            target = argv[len(commands.UV) + 1]
            if target != "-m":
                assert (ROOT / target).is_file()
    package = json.loads((ROOT / "apps/mujoco-viewer/package.json").read_text())
    for argv in commands.COMMANDS.values():
        if argv[:len(commands.NPM)] == commands.NPM:
            assert argv[-1] in package["scripts"]


def test_dispatch_preserves_argv_and_status(monkeypatch):
    seen = []
    arguments = ["--output", "日本語 space/a.json", "a;b", "$value", '"quote"', "", "--flag"]
    monkeypatch.setattr(commands.shutil, "which", lambda name: "/absolute/tool")
    monkeypatch.setattr(commands.subprocess, "call", lambda argv, **kw: seen.append((argv, kw)) or 17)
    assert commands.main("replay", arguments) == 17
    argv, kwargs = seen[0]
    assert argv == ["/absolute/tool", *commands.COMMANDS["replay"][1:], *arguments]
    env = kwargs.pop("env")
    assert env["PYTHONUTF8"] == "1"
    assert kwargs == {"cwd": commands.ROOT, "shell": False}


@pytest.mark.parametrize("recipe", list(commands.COMMANDS))
@pytest.mark.parametrize("status", [0, 17, 130])
def test_actual_just_new_recipes_forward_literal_arguments(tmp_path, recipe, status):
    just = shutil.which("just")
    assert just, "Install documented rust-just before running development tests"
    shutil.copyfile(ROOT / "justfile", tmp_path / "justfile")
    target = tmp_path / "scripts/repository/commands.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "import json\n"
        "def main(command, arguments):\n"
        "    print(json.dumps([command, arguments], ensure_ascii=True))\n"
        f"    return {status}\n",
        encoding="utf-8",
    )
    arguments = ["日本語 space", "a;b", "$value", '"quote"', "", "--flag"]
    env = dict(os.environ)
    # bootstrapのPythonを選ぶuvは本物。root .envを読み込まないことも検査する。
    (tmp_path / ".env").write_text("UV_PYTHON=missing-python\n", encoding="utf-8")
    result = subprocess.run(
        [just, "--justfile", str(tmp_path / "justfile"), recipe, *arguments],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == status, result.stderr
    assert json.loads(result.stdout.strip()) == [recipe, arguments], result.stdout


def test_missing_tool_and_interruption(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda name: None)
    assert commands.main("profile", []) == 1
    assert "uv" in capsys.readouterr().err
    monkeypatch.setattr(commands.shutil, "which", lambda name: name)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(commands.subprocess, "call", interrupt)
    assert commands.main("profile", []) == 130


def test_real_python_child_uses_utf8_when_parent_disables_it(monkeypatch, capfd):
    import sys
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setitem(commands.COMMANDS, "profile", (
        sys.executable, "-c", "import sys; assert sys.flags.utf8_mode == 1; print(chr(0x279c))",
    ))
    assert commands.main("profile", []) == 0
    assert capfd.readouterr().out.strip() == "\u279c"
