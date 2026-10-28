"""Tracked simulation conditions and one-time legacy conversion stay explicit."""

from copy import deepcopy
from pathlib import Path
import json
import sys

import pytest

from scripts.repository.convert_workbench_condition import convert_document, main
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition
from xpotato_sim.runtime.scene.objects import canonical


ROOT = Path(__file__).resolve().parents[2]
CONDITIONS = ROOT / "profiles" / "experiments" / "simulation-comparison"


@pytest.mark.parametrize("name,provider,mode", [
    ("gamepad-no-force-cue", "gamepad/v1", "no_force_cue"),
    ("gamepad-force-cue", "gamepad/v1", "force_cue"),
    ("selfrionette-fixture-no-force-cue", "selfrionette-device/v1", "no_force_cue"),
    ("selfrionette-fixture-force-cue", "selfrionette-device/v1", "force_cue"),
])
def test_migrated_tracked_condition(name, provider, mode):
    profile, _, document = resolve_condition((CONDITIONS / f"{name}.json").read_bytes())
    assert document["preset_id"] == "tasks/research-spring"
    assert profile.provider_id == provider
    assert document["display_comparison"]["mode"] == mode


@pytest.mark.parametrize("provider,old_id", [
    ("gamepad/v1", "research-spring-g"),
    ("selfrionette-device/v1", "research-spring-s"),
    ("gamepad/v1", "research-spring"),
])
def test_converter_preserves_condition_but_replaces_old_id(provider, old_id):
    current = preset_condition("tasks/research-spring", input_provider=provider)
    old = deepcopy(current)
    old["preset_id"] = old_id
    assert resolve_condition(convert_document(canonical(old)))[2] == current


@pytest.mark.parametrize("new_id,old_id", [
    ("tasks/dynamic-cube-drop", "shared-dynamic-cube-drop"),
    ("tasks/dynamic-cube-push", "shared-dynamic-cube-push"),
    ("tasks/dynamic-fixed-contact", "shared-dynamic-fixed-contact"),
])
def test_converter_accepts_previous_shared_task_ids(new_id, old_id):
    current = preset_condition(new_id)
    old = deepcopy(current)
    old["preset_id"] = old_id
    assert resolve_condition(convert_document(canonical(old)))[2] == current


def test_converter_rejects_unknown_and_nonconvertible_without_fallback():
    current = preset_condition("tasks/research-spring")
    with pytest.raises(ValueError, match="変換対象外"):
        convert_document(canonical(current))
    old = deepcopy(current)
    old["preset_id"] = "research-spring-g"
    old["configuration"]["model"]["name"] = "bimanual"
    with pytest.raises(ValueError, match="自動変換できません"):
        convert_document(canonical(old))


def test_cli_never_overwrites_source_or_existing_output(tmp_path, monkeypatch):
    old = preset_condition("tasks/research-spring")
    old["preset_id"] = "research-spring-g"
    source = tmp_path / "saved-old.json"
    output = tmp_path / "converted.json"
    source.write_bytes(canonical(old))
    before = source.read_bytes()
    monkeypatch.setattr(sys, "argv", ["convert_workbench_condition", str(source), str(output)])
    assert main() == 0
    assert source.read_bytes() == before
    assert json.loads(output.read_bytes())["preset_id"] == "tasks/research-spring"
    with pytest.raises(FileExistsError):
        main()
    assert source.read_bytes() == before
