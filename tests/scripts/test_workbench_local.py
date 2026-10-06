"""ローカル設定、準備/起動分離、build保全とdotenvの実process境界。"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/workbench_local.py"
SPEC = importlib.util.spec_from_file_location("workbench_local", SCRIPT)
assert SPEC and SPEC.loader
local = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = local
SPEC.loader.exec_module(local)


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "repository 日本語 space"
    root.mkdir()
    env = {
        "XPOTATO_TEMP_ROOT": "../temporary 日本語 space",
        "XPOTATO_RESULT_ROOT": "../results",
        "XPOTATO_WEB_DIST": "../temporary 日本語 space/viewer-dist",
        "XPOTATO_OPEN_BROWSER": "false", "XPOTATO_WEB_PORT": "5173",
        "XPOTATO_BACKEND_PORT": "8766", "XPOTATO_PYTHON": "3.12",
    }
    return root, env


def test_relative_paths(settings):
    root, env = settings
    config = local.Config.read(root, env)
    assert config.temp == root.parent / "temporary 日本語 space"
    assert not config.browser


@pytest.mark.parametrize("key,value", [
    ("XPOTATO_WEB_PORT", "0"), ("XPOTATO_WEB_PORT", "65536"),
    ("XPOTATO_WEB_PORT", "1.5"), ("XPOTATO_WEB_PORT", "secret"),
    ("XPOTATO_OPEN_BROWSER", "yes"), ("XPOTATO_PYTHON", "3.14"),
    ("XPOTATO_TEMP_ROOT", ""), ("XPOTATO_BACKEND_PORT", "5173"),
    ("XPOTATO_WEB_POT", "super-secret"),
])
def test_invalid_settings_never_echo_values(settings, key, value):
    root, env = settings
    env[key] = value
    with pytest.raises(ValueError) as caught:
        local.Config.read(root, env)
    assert "super-secret" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("dist", [".", "..", "src", "../results", "../results/child", "../temporary 日本語 space"])
def test_dangerous_output_overlap(settings, dist):
    root, env = settings
    env["XPOTATO_WEB_DIST"] = dist
    with pytest.raises(ValueError, match="重なり"):
        local.Config.read(root, env)


def test_init_never_overwrites(settings):
    root, _ = settings
    (root / ".env").write_bytes(b"existing-secret\n")
    local.init(root)
    assert (root / ".env").read_bytes() == b"existing-secret\n"


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_os_defaults(settings, platform):
    root, _ = settings
    defaults = local.defaults(root, {"LOCALAPPDATA": str(root.parent / "app data"),
                                    "XDG_DATA_HOME": str(root.parent / "xdg data")}, platform)
    assert "XPOTATO_SOFTWARE_REVISION" not in defaults
    assert local.Config.read(root, defaults).temp.is_absolute()
    if platform == "win32":
        assert "app data" in defaults["XPOTATO_RESULT_ROOT"]
    if platform == "linux":
        assert "xdg data" in defaults["XPOTATO_RESULT_ROOT"]
    if platform == "darwin":
        assert "Library/Application Support" in defaults["XPOTATO_RESULT_ROOT"]


def test_unowned_output_preserved(settings, monkeypatch):
    root, env = settings
    config = local.Config.read(root, env)
    config.dist.mkdir(parents=True)
    sentinel = config.dist / "saved-result.txt"
    sentinel.write_text("do not delete")
    monkeypatch.setattr(local, "execute", lambda *a, **kw: pytest.fail("unowned build must reject before executing"))
    with pytest.raises(ValueError, match="所有"):
        local.build(root, config)
    assert sentinel.read_text() == "do not delete"


@pytest.mark.parametrize("failure", ["command", "verification", "rename", "none"])
def test_staged_build_preserves_previous(settings, monkeypatch, failure):
    root, env = settings
    config = local.Config.read(root, env)
    vite = root / "apps/mujoco-viewer/node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    web_setup_fixture(root)
    config.dist.mkdir(parents=True)
    (config.dist / local.OWNER).write_text(json.dumps(local.owner(root)))
    (config.dist / "previous").write_text("normal build")
    monkeypatch.setattr(local, "tool", lambda name: name)

    def build(argv, **kwargs):
        stage = Path(argv[argv.index("--outDir") + 1])
        stage.mkdir()
        (stage / "new").write_text("new build")
        if failure == "command":
            raise ValueError("injected build failure")

    def verify(*args):
        if failure == "verification":
            raise ValueError("injected invalid build")

    original = Path.rename
    def rename(path, target):
        if failure == "rename" and path.name == "dist":
            raise OSError("injected cross-volume/locked destination")
        return original(path, target)

    monkeypatch.setattr(local, "execute", build)
    monkeypatch.setattr(local, "verify_build", verify)
    monkeypatch.setattr(Path, "rename", rename)
    if failure == "none":
        local.build(root, config)
        assert (config.dist / "new").read_text() == "new build"
        assert not (config.dist / "previous").exists()
    else:
        with pytest.raises(ValueError):
            local.build(root, config)
        assert (config.dist / "previous").read_text() == "normal build"
    assert sorted(p.name for p in config.temp.iterdir()) == ["viewer-dist"]


@pytest.mark.parametrize("dev", [False, True])
def test_run_uses_only_prepared_python_and_formal_cli(settings, monkeypatch, dev):
    root, env = settings
    config = local.Config.read(root, env)
    config.temp.mkdir()
    config.results.mkdir()
    vite = root / "apps/mujoco-viewer/node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    web_setup_fixture(root)
    checks = []
    monkeypatch.setattr(local, "prepared", lambda *a: checks.append("prepared"))
    monkeypatch.setattr(local, "verify_build", lambda *a: checks.append("build"))
    monkeypatch.setattr(local, "ports", lambda *a: None)
    monkeypatch.setattr(local, "revision", lambda *a: "head-dirty-digest")
    monkeypatch.setattr(local, "tool", lambda name: name)
    monkeypatch.setattr(local, "setup", lambda *a: pytest.fail("run must not setup"))
    monkeypatch.setattr(local, "build", lambda *a: pytest.fail("run must not build"))
    calls = []
    class Process:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def wait(self, **kwargs):
            return 0
    monkeypatch.setattr(local.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or Process())
    assert local.launch(root, config, dev=dev) == 0
    argv, kwargs = calls[0]
    assert argv[:4] == [str(local.venv_python(root)), "-c", local.CLI_ENTRY, "workbench"]
    assert "head-dirty-digest" in argv
    assert "--open-browser" not in argv
    assert "--profile" not in argv and "--run-once" not in argv
    assert ("--dev-server" in argv) == dev
    assert ("--web-dist" in argv) != dev
    assert checks == (["prepared"] if dev else ["prepared", "build"])
    assert kwargs["cwd"] == root
    assert not kwargs.get("shell")


def test_formal_cli_entry_runs_as_a_process():
    result = subprocess.run([sys.executable, "-c", local.CLI_ENTRY, "workbench", "--help"],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    assert "--temporary-root" in result.stdout
    assert "--startup-check" in result.stdout


def test_interrupt_waits_for_formal_cli_cleanup(settings, monkeypatch):
    root, env = settings
    config = local.Config.read(root, env)
    config.temp.mkdir()
    config.results.mkdir()
    monkeypatch.setattr(local, "prepared", lambda *args: None)
    monkeypatch.setattr(local, "verify_build", lambda *args: None)
    monkeypatch.setattr(local, "ports", lambda *args: None)
    monkeypatch.setattr(local, "revision", lambda *args: "source")
    waits = []
    class Process:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def wait(self, timeout=None):
            waits.append(timeout)
            if timeout is None:
                raise KeyboardInterrupt
            return 130
    monkeypatch.setattr(local.subprocess, "Popen", lambda *args, **kwargs: Process())
    assert local.launch(root, config) == 130
    assert waits == [None, 10]


@pytest.mark.parametrize("selection,version", [("3.12", "3.14.1"), ("3.12.12", "3.12.13")])
def test_python_mismatch_rejects_before_environment_sync(settings, monkeypatch, selection, version):
    root, env = settings
    env["XPOTATO_PYTHON"] = selection
    config = local.Config.read(root, env)
    monkeypatch.setattr(local, "python_version", lambda *args: version)
    monkeypatch.setattr(local, "execute", lambda *args, **kwargs: pytest.fail("must not synchronize"))
    with pytest.raises(ValueError, match="just setup"):
        local.prepared(root, config)


def test_missing_build_rejected_without_application(settings, monkeypatch):
    root, env = settings
    config = local.Config.read(root, env)
    config.temp.mkdir()
    config.results.mkdir()
    verifier = root / "src/xpotato_sim/runtime/runners/workbench_web.py"
    verifier.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT.parents[1] / "src/xpotato_sim/runtime/runners/workbench_web.py", verifier)
    monkeypatch.setattr(local, "prepared", lambda *a: None)
    monkeypatch.setattr(local.subprocess, "Popen", lambda *a, **kw: pytest.fail("app must not start"))
    with pytest.raises(ValueError, match="just build"):
        local.launch(root, config)


def test_environment_check_cannot_sync_or_recreate(settings, monkeypatch):
    root, env = settings
    config = local.Config.read(root, env)
    for path in (root / "pyproject.toml", root / "uv.lock", root / "src/xpotato_sim/plugins/robots/fast_arm/core/pyproject.toml"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    marker = root / ".venv/workbench-local.json"
    marker.parent.mkdir()
    (marker.parent / ".lock").touch()
    marker.write_text(json.dumps(local.fingerprint(root, config)))
    config.temp.mkdir()
    monkeypatch.setattr(local, "python_version", lambda *a: "3.12.13")
    monkeypatch.setattr(local, "tool", lambda name: name)
    calls = []
    monkeypatch.setattr(local, "execute", lambda argv, **kw: calls.append(argv))
    local.prepared(root, config)
    assert {"--check", "--locked", "--offline", "--no-python-downloads"} <= set(calls[0])
    assert str(local.venv_python(root)) in calls[0]
    (root / "uv.lock").write_text("changed")
    with pytest.raises(ValueError, match="just setup"):
        local.prepared(root, config)
    assert len(calls) == 1
    assert list(config.temp.iterdir()) == []


def test_dirty_identity_includes_untracked_source(settings, monkeypatch):
    root, _ = settings
    (root / "new-source.py").write_text("first")
    monkeypatch.setattr(local, "tool", lambda name: name)
    outputs = {"rev-parse": b"a" * 40 + b"\n", "diff": b"", "ls-files": b"new-source.py\0"}
    monkeypatch.setattr(local.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, outputs[argv[3]]))
    first = local.revision(root)
    assert first.startswith("a" * 40 + "-dirty-")
    (root / "new-source.py").write_text("second")
    assert first != local.revision(root)
    outputs["ls-files"] = b""
    assert local.revision(root) == "a" * 40
    outputs["diff"] = b"tracked patch"
    assert local.revision(root).startswith("a" * 40 + "-dirty-")


def test_occupied_loopback_port(settings):
    root, env = settings
    with socket.socket() as sock:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        env["XPOTATO_WEB_PORT"] = str(sock.getsockname()[1])
        config = local.Config.read(root, env)
        with pytest.raises(ValueError, match="port"):
            local.ports(config)


def test_listening_port_cannot_be_reused(settings):
    root, env = settings
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        env["XPOTATO_WEB_PORT"] = str(sock.getsockname()[1])
        with pytest.raises(ValueError, match="port"):
            local.ports(local.Config.read(root, env))


def test_dotenv_real_uv_root_only_and_no_secret_output(settings, monkeypatch):
    root, env = settings
    for key in list(os.environ):
        if key.startswith("XPOTATO_"):
            monkeypatch.delenv(key)
    (root.parent / ".env").write_text("XPOTATO_PARENT_SECRET=should-not-load\n")
    (root / ".env").write_text("\n".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in env.items()) + "\nPRIVATE_TOKEN=secret\n", encoding="utf-8")
    loaded = local.load_env(root)
    assert loaded == env
    script = root / "scripts/workbench_local.py"
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    result = subprocess.run([sys.executable, "-B", str(script), "doctor"], cwd=root.parent, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "secret" not in result.stdout + result.stderr
    assert "just setup" in result.stdout
    assert not (root / ".venv").exists()
    (root / ".env").write_text('XPOTATO_TEMP_ROOT="super-secret', encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(script), "doctor"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "super-secret" not in result.stdout + result.stderr


WEB_INPUTS = ("package.json", "package-lock.json", "node_modules/.package-lock.json")


def web_setup_fixture(root):
    app = root / "apps/mujoco-viewer"
    for name in WEB_INPUTS:
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    value = {"schema": "workbench-local-web/v1", "workspace": str(root.resolve())}
    value.update({name: hashlib.sha256((app / name).read_bytes()).hexdigest() for name in WEB_INPUTS})
    (app / "node_modules/.workbench-local-dependencies.json").write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("changed", WEB_INPUTS)
def test_build_rejects_changed_web_dependencies_before_vite(settings, monkeypatch, changed):
    root, env = settings
    config = local.Config.read(root, env)
    vite = root / "apps/mujoco-viewer/node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    web_setup_fixture(root)
    config.dist.mkdir(parents=True)
    (config.dist / local.OWNER).write_text(json.dumps(local.owner(root)))
    sentinel = config.dist / "previous"
    sentinel.write_bytes(b"keep normal build")
    (root / "apps/mujoco-viewer" / changed).write_text("changed", encoding="utf-8")
    monkeypatch.setattr(local, "tool", lambda name: name)
    monkeypatch.setattr(local, "execute", lambda *a, **k: pytest.fail("stale dependencies reached Vite"))
    with pytest.raises(ValueError, match="just setup"):
        local.build(root, config)
    assert sentinel.read_bytes() == b"keep normal build"


@pytest.mark.parametrize("command", ["init", "setup", "build", "run", "dev", "doctor"])
@pytest.mark.parametrize("exit_code", [0, 17, 130])
def test_actual_just_recipes_preserve_child_status(tmp_path, command, exit_code):
    just = shutil.which("just")
    assert just, "Install the documented rust-just utility before running development tests"
    recipe = tmp_path / "justfile"
    shutil.copyfile(SCRIPT.parents[1] / "justfile", recipe)
    bin_dir = tmp_path / "bin 日本語 space"
    bin_dir.mkdir()
    if sys.platform == "win32":
        fake_uv = bin_dir / "uv.cmd"
        fake_uv.write_bytes(f"@echo off\r\necho LOCAL_RECIPE_PROBE\r\nexit /b {exit_code}\r\n".encode())
    else:
        fake_uv = bin_dir / "uv"
        fake_uv.write_text(f"#!/bin/sh\necho LOCAL_RECIPE_PROBE\nexit {exit_code}\n", encoding="utf-8")
        fake_uv.chmod(0o700)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
    result = subprocess.run([just, "--justfile", str(recipe), command], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert "LOCAL_RECIPE_PROBE" in result.stdout
    assert result.returncode == exit_code, result.stderr
    assert not (tmp_path / ".env").exists()
