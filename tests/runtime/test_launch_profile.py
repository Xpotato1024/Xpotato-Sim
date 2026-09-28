"""起動設定だけを解決し、取得・実行・送信を始めないことを確認する。"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from xpotato_sim.runtime.composition.launch_profile import (
    MAX_PROFILE_BYTES, decode_launch_profile, list_launch_profiles,
    load_launch_profile, override_launch_profile,
)
from xpotato_sim.runtime.experiment.input_source import ValidatedManagedInputSourceReader, ValidatedInputSourceReader

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "profiles/sim-gamepad.json"


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("profile validation must not start acquisition/process/network")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(ValidatedManagedInputSourceReader, "start", forbidden)
    monkeypatch.setattr(ValidatedInputSourceReader, "read_frame", forbidden)


def raw_profile():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def decode(raw):
    return decode_launch_profile(json.dumps(raw).encode(), source_path=SOURCE)


@pytest.mark.parametrize("name", ("sim-gamepad", "sim-keyboard", "replay-sweep"))
def test_profiles_resolve_without_execution(name):
    profile = load_launch_profile(name)
    assert profile.name == name and profile.workspace_path == ROOT
    assert profile.robot.plugin_id == "fast_arm"
    assert len(profile.configuration_sha256) == 64
    assert profile.to_dict()["resolved"]["physical_output"] == "disabled"
    assert profile.steps * profile.dt_s > 0
    assert decode_launch_profile(profile.document_json.encode(), source_path=profile.source_path) == profile


@pytest.mark.parametrize("path,value", [
    (("schema_version",), "v2"), (("mode",), "physical"),
    (("name",), "../escape"), (("workspace",), "missing-directory"),
    (("execution", "steps"), True), (("execution", "steps"), 0),
    (("execution", "steps"), 2**31), (("execution", "dt_s"), 0),
    (("execution", "interval_s"), -1), (("execution", "grace_period_s"), True),
    (("execution", "dt_s"), float("nan")), (("execution", "dt_s"), float("inf")),
    (("web", "host"), "0.0.0.0"), (("web", "host"), "example.com"),
    (("web", "port"), 0), (("web", "port"), 65536), (("web", "port"), True),
    (("web", "port"), 8766), (("web", "open_browser"), 1),
    (("robot", "name"), "missing"), (("robot", "version"), 999),
    (("input", "plugin", "version"), 999), (("input", "provider"), None),
    (("input", "provider"), "unknown/v1"), (("input", "preset"), "sweep_x"),
    (("mapping", "plugin", "name"), "replay_mapping"),
    (("mapping", "parameters"), {"typo": 1}),
    (("mapping", "parameters"), {"gamepad_deadzone": -0.1}),
])
def test_bad_profile_fails_before_execution(path, value):
    raw = raw_profile()
    target = raw
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises((ValueError, TypeError)):
        decode(raw)


@pytest.mark.parametrize("document", [
    b"{}", b"[]", b"{", b"\xff", b"\xef\xbb\xbf{}",
    b'{"name":"a","name":"b"}', b'{"nested":{"x":1,"x":2}}',
    b'{"number":NaN}', b'{"number":Infinity}', b" " * (MAX_PROFILE_BYTES + 1),
], ids=["empty-object", "array", "broken", "encoding", "bom", "duplicate", "nested-duplicate", "nan", "infinity", "oversize"])
def test_strict_json(document):
    with pytest.raises(ValueError):
        decode_launch_profile(document, source_path=SOURCE)


@pytest.mark.parametrize("section", (None, "web", "execution", "input", "robot", "mapping"))
def test_unknown_keys_are_rejected(section):
    raw = raw_profile()
    (raw if section is None else raw[section])["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        decode(raw)


def test_numeric_overflow_and_unknown_physical_permission():
    raw = raw_profile()
    document = json.dumps(raw).replace('"dt_s": 0.016666666666666666', '"dt_s": 1e999')
    with pytest.raises(ValueError):
        decode_launch_profile(document.encode(), source_path=SOURCE)
    raw["physical_permission"] = {"enabled": True}
    with pytest.raises(ValueError):
        decode(raw)


def test_external_path_is_relative_to_profile_not_cwd(tmp_path, monkeypatch):
    raw = raw_profile()
    import os
    raw["workspace"] = os.path.relpath(ROOT, tmp_path)
    path = tmp_path / "external.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.chdir(ROOT.parent)
    profile = load_launch_profile(path)
    assert profile.workspace_path == ROOT
    assert load_launch_profile("sim-keyboard").workspace_path == ROOT


def test_overrides_are_limited_revalidated_and_do_not_mutate_original():
    profile = load_launch_profile("sim-gamepad")
    changed = override_launch_profile(profile, web_port=5197, backend_port=8797, open_browser=False)
    assert (changed.web_port, changed.backend_port, changed.open_browser) == (5197, 8797, False)
    assert profile.web_port == 5173 and profile.open_browser
    assert changed.configuration_sha256 != profile.configuration_sha256
    with pytest.raises(ValueError):
        override_launch_profile(profile, backend_port=profile.web_port)
    assert profile.to_dict()["resolved"]["mapping_parameters"]["gamepad_deadzone"] == 0.1
    editable = profile.mapping_parameters
    editable["gamepad_deadzone"] = 0.9
    assert profile.mapping_parameters == {}


def test_parameter_changes_reach_the_existing_mapping_normalizer():
    raw = raw_profile()
    raw["mapping"]["parameters"] = {"gamepad_speed_m_s": 0.02, "gamepad_deadzone": 0.2}
    profile = decode(raw)
    assert profile.to_dict()["resolved"]["mapping_parameters"]["gamepad_speed_m_s"] == 0.02
    assert profile.to_dict()["resolved"]["mapping_parameters"]["gamepad_deadzone"] == 0.2


def test_profile_cli_and_errors(capsys):
    cli = importlib.import_module("xpotato_sim.cli.main")
    assert cli.main(["profile"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == sorted([*list_launch_profiles(), "fast-arm-bimanual-gamepad"])
    assert cli.main(["profile", "sim-keyboard"]) == 0
    assert json.loads(capsys.readouterr().out)["configuration"]["input"]["provider"] == "keyboard/v1"
    assert cli.main(["profile", "missing-profile"]) == 1
    assert "error" in capsys.readouterr().err


def test_world_xy_profile_resolves_axis_map_and_retains_legacy_profile():
    profile = load_launch_profile("sim-gamepad-world-xy")
    assert profile.mapping_parameters["gamepad_axis_map"] == {
        "axis_indices": [0, 1, 3], "axis_signs": [1, -1, -1],
    }
    assert profile.to_dict()["resolved"]["mapping_parameters"]["gamepad_axis_map"] == profile.mapping_parameters["gamepad_axis_map"]
    assert load_launch_profile("sim-gamepad").mapping_parameters == {}
    altered = json.loads(profile.document_json)
    altered["mapping"]["parameters"]["gamepad_axis_map"]["axis_signs"][0] = -1
    other = decode_launch_profile(json.dumps(altered).encode(), source_path=profile.source_path)
    assert profile.configuration_sha256 != other.configuration_sha256


@pytest.mark.parametrize("axis_map", [
    {"axis_indices": [0, 1, 3], "axis_signs": [1, 0, -1]},
    {"axis_indices": [0, 1, True], "axis_signs": [1, -1, -1]},
    {"axis_indices": [0, 1, 3]}, None,
])
def test_invalid_axis_map_profile_is_rejected_without_execution(axis_map):
    raw = raw_profile()
    raw["mapping"]["parameters"] = {"gamepad_axis_map": axis_map}
    with pytest.raises((ValueError, TypeError)):
        decode(raw)
