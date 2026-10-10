"""Device採用境界のfail-closed、argv、終了code。serialを使わない。"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("device_launcher", ROOT / "scripts/hardware/selfrionette/run_device_serial_tool.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize("command", ["legacy-monitor", "legacy-measure"])
@pytest.mark.parametrize("status", [0, 17, 130])
@pytest.mark.parametrize("powershell", [False, True])
def test_boundary_preserves_literals_root_and_exit(monkeypatch, tmp_path, command, status, powershell):
    executable = tmp_path / "日本語 space" / "selfrionettectl.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv("SELFRIONETTECTL", str(executable))
    probe, seen = [], []
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kw: probe.append((args, kw)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(launcher.subprocess, "call", lambda args, **kw: seen.append((args, kw)) or status)
    arguments = ["-Port", "日本語 space;&", "-SendText", "", "-Calibrate:False", 'a"b', "%COMSPEC%"]
    forwarded = ["--powershell-json", base64.b64encode(json.dumps(arguments).encode()).decode()] if powershell else arguments
    assert launcher.main([command, *forwarded]) == status
    assert probe[0][0] == [str(executable), command, "--help"]
    assert seen == [([str(executable), command, *(["--powershell-args"] if powershell else []), *arguments], {"cwd": launcher.ROOT, "shell": False})]


@pytest.mark.parametrize("configured", [None, "relative.exe", "missing.exe", "bad.cmd", "bad.ps1"])
def test_unprepared_device_never_runs_or_falls_back(monkeypatch, tmp_path, configured, capsys):
    if configured is None:
        monkeypatch.delenv("SELFRIONETTECTL", raising=False)
    elif configured == "relative.exe":
        monkeypatch.setenv("SELFRIONETTECTL", configured)
    else:
        path = tmp_path / configured
        if configured != "missing.exe":
            path.touch()
        monkeypatch.setenv("SELFRIONETTECTL", str(path))
    def forbidden(*args, **kwargs):
        raise AssertionError("unprepared Device must not execute")
    monkeypatch.setattr(launcher.subprocess, "run", forbidden)
    monkeypatch.setattr(launcher.subprocess, "call", forbidden)
    assert launcher.main(["legacy-monitor", "--help"]) == 1
    assert "SELFRIONETTECTL" in capsys.readouterr().err


def test_old_binary_fails_capability_probe_and_interruption_is_130(monkeypatch, tmp_path, capsys):
    executable = tmp_path / "selfrionettectl.exe"
    executable.touch()
    monkeypatch.setenv("SELFRIONETTECTL", str(executable))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=2, stderr="unknown command"))
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **kw: pytest.fail("unsupported command must not run"))
    assert launcher.main(["legacy-measure", "--port", "explicit"]) == 1
    assert "対応していません" in capsys.readouterr().err
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(launcher.subprocess, "run", interrupt)
    assert launcher.main(["legacy-measure"]) == 130


@pytest.mark.parametrize("arguments", [[], ["info"], ["legacy-monitor", "--powershell-json", "!"],
                                       ["legacy-monitor", "--powershell-json", "e30="],
                                       ["legacy-monitor", "--powershell-json", "WzFd"]])
def test_invalid_transport_envelope_fails_before_any_device(monkeypatch, arguments):
    monkeypatch.delenv("SELFRIONETTECTL", raising=False)
    assert launcher.main(arguments) == 2
