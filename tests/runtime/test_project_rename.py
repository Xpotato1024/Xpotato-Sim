"""基盤名の移行が装置ID・旧設定の記録を壊さないことを検証する。"""
from hashlib import sha256
from importlib.metadata import distribution
import importlib.util
import json
from pathlib import Path

import pytest
import xpotato_sim
from xpotato_sim.runtime.composition.launch_profile import (
    LAUNCH_PROFILE_SCHEMA, LEGACY_LAUNCH_PROFILE_SCHEMA, MODEL_LAUNCH_PROFILE_SCHEMA, SCENE_LAUNCH_PROFILE_SCHEMA, decode_launch_profile,
)
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG

ROOT = Path(__file__).resolve().parents[2]


def test_distribution_and_console_scripts_use_new_namespace():
    installed = distribution("xpotato-sim")
    scripts = {item.name: item.value for item in installed.entry_points}
    assert scripts["xpotato-sim"] == "xpotato_sim.cli:main"
    assert scripts["xpotato-sim-r7-g-e2e"] == "xpotato_sim.runtime.experiment.r7_g_e2e:main"
    assert Path(xpotato_sim.__file__).parent.name == "xpotato_sim"
    assert importlib.util.find_spec("selfrionette") is None


@pytest.mark.parametrize("path", sorted((ROOT / "profiles").glob("*.json")), ids=lambda p: p.stem)
def test_shipped_profile_schema_and_legacy_configuration_digest(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] in (LAUNCH_PROFILE_SCHEMA, MODEL_LAUNCH_PROFILE_SCHEMA, SCENE_LAUNCH_PROFILE_SCHEMA)
    current = decode_launch_profile(json.dumps(raw).encode(), source_path=path)
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert current.document_json == canonical
    assert current.configuration_sha256 == sha256(canonical.encode()).hexdigest()
    assert current.to_dict()["resolved"]["physical_output"] == "disabled"
    if raw["schema_version"] in (MODEL_LAUNCH_PROFILE_SCHEMA, SCENE_LAUNCH_PROFILE_SCHEMA):
        # v2/v3を旧schemaへ単純置換し、モデル/scene選択を黙って落とす移行は拒否する。
        assert current.model is not None
        old_schemas = (LAUNCH_PROFILE_SCHEMA, LEGACY_LAUNCH_PROFILE_SCHEMA) + ((MODEL_LAUNCH_PROFILE_SCHEMA,) if raw["schema_version"] == SCENE_LAUNCH_PROFILE_SCHEMA else ())
        for old_schema in old_schemas:
            old = {**raw, "schema_version": old_schema}
            with pytest.raises(ValueError, match="unknown fields"):
                decode_launch_profile(json.dumps(old).encode(), source_path=path)
    else:
        raw["schema_version"] = LEGACY_LAUNCH_PROFILE_SCHEMA
        legacy = decode_launch_profile(json.dumps(raw).encode(), source_path=path)
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert legacy.document_json == canonical
        assert legacy.configuration_sha256 == sha256(canonical.encode()).hexdigest()
        assert legacy.to_dict()["resolved"] == current.to_dict()["resolved"]
        assert legacy.to_dict()["resolved"]["physical_output"] == "disabled"
    raw["schema_version"] = "xpotato-sim-launch-profile/v999"
    with pytest.raises(ValueError, match="schema_version"):
        decode_launch_profile(json.dumps(raw).encode(), source_path=path)


def test_selfrionette_remains_a_device_identity():
    plugin = INPUT_SOURCE_CATALOG.resolve("selfrionette").plugin
    assert plugin.identity.name == "selfrionette"
    assert plugin.identity.canonical_id == "selfrionette/v1"
