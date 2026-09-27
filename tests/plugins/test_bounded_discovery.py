from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

from xpotato_sim.plugins.mappings.discovery import (
    ControlMappingDiscoveryRoot,
    ControlMappingPluginDiscoveryError,
    discover_control_mapping_plugins,
)
from xpotato_sim.plugins.input_sources.discovery import (
    InputSourceDiscoveryRoot,
    InputSourcePluginDiscoveryError,
    discover_input_source_plugins,
)
from xpotato_sim.plugins.input_sources.catalog import (
    INPUT_SOURCE_CATALOG,
    InputSourceCatalog,
)
from xpotato_sim.plugins.mappings.catalog import CONTROL_MAPPING_REGISTRY
from xpotato_sim.runtime.experiment.contracts import PluginSelection


_INPUT_EXPORT = """
from tests.plugins.input_sources.fixtures.dummy_input_source import DUMMY_REGISTRATION
INPUT_SOURCE_PLUGIN = DUMMY_REGISTRATION
"""
_MAPPING_EXPORT = """
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from tests.runtime.test_experiment_plugin_composition import build_test_mapping
CONTROL_MAPPING_PLUGIN = build_test_mapping(
    identity=VersionedIdentity("test_dummy_mapping", 1)
)
"""


def _input_export(plugin_id: str) -> str:
    return f"""
from dataclasses import replace
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from tests.plugins.input_sources.fixtures.dummy_input_source import (
    DUMMY_PLUGIN,
    DUMMY_REGISTRATION,
)
INPUT_SOURCE_PLUGIN = replace(
    DUMMY_REGISTRATION,
    plugin=replace(DUMMY_PLUGIN, identity=VersionedIdentity("{plugin_id}", 1)),
    cli_aliases=("{plugin_id}",),
)
"""


def _mapping_export(plugin_id: str) -> str:
    return f"""
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from tests.runtime.test_experiment_plugin_composition import build_test_mapping
CONTROL_MAPPING_PLUGIN = build_test_mapping(
    identity=VersionedIdentity("{plugin_id}", 1)
)
"""


def _namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    packages: dict[str, str | None],
):
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for package_name, plugin_source in packages.items():
        package = root / package_name
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        if plugin_source is not None:
            (package / "plugin.py").write_text(plugin_source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return importlib.import_module(name)


def test_input_source_package_only_onboarding_and_production_separation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _namespace(
        tmp_path,
        monkeypatch,
        "test_input_source_plugins",
        {
            "_private": "raise RuntimeError('private package must not import')",
            "test_dummy_input_source": _INPUT_EXPORT,
        },
    )
    catalog = InputSourceCatalog(
        discover_input_source_plugins(InputSourceDiscoveryRoot(namespace))
    )

    assert catalog.ids == ("test_dummy_input_source",)
    assert catalog.resolve("test_dummy_input").plugin.identity.canonical_id == (
        "test_dummy_input_source/v1"
    )
    assert "test_dummy_input_source" not in INPUT_SOURCE_CATALOG.ids

    shutil.rmtree(tmp_path / "test_input_source_plugins" / "test_dummy_input_source")
    sys.modules.pop(
        "test_input_source_plugins.test_dummy_input_source.plugin", None
    )
    sys.modules.pop("test_input_source_plugins.test_dummy_input_source", None)
    importlib.invalidate_caches()
    removed_catalog = InputSourceCatalog(
        discover_input_source_plugins(InputSourceDiscoveryRoot(namespace))
    )
    with pytest.raises(ValueError, match="unknown input source plugin ID"):
        removed_catalog.resolve_plugin(
            PluginSelection("test_dummy_input_source", 1)
        )


def test_discovery_order_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_namespace = _namespace(
        tmp_path,
        monkeypatch,
        "ordered_input_plugins",
        {
            "z_input": _input_export("z_input"),
            "a_input": _input_export("a_input"),
        },
    )
    registrations = discover_input_source_plugins(
        InputSourceDiscoveryRoot(input_namespace)
    )
    assert tuple(item.plugin.identity.name for item in registrations) == (
        "a_input",
        "z_input",
    )

    mapping_namespace = _namespace(
        tmp_path,
        monkeypatch,
        "ordered_mapping_plugins",
        {
            "z_mapping": _mapping_export("z_mapping"),
            "a_mapping": _mapping_export("a_mapping"),
        },
    )
    registry = discover_control_mapping_plugins(
        ControlMappingDiscoveryRoot(mapping_namespace)
    )
    assert registry.ids == ("a_mapping", "z_mapping")


@pytest.mark.parametrize(
    ("name", "packages", "match"),
    (
        (
            "input_missing",
            {"missing_input": None},
            "entry point is missing",
        ),
        (
            "input_wrong_type",
            {"wrong_type": "INPUT_SOURCE_PLUGIN = object()"},
            "invalid Input Source Plugin registration type",
        ),
        (
            "input_broken",
            {"broken_input": "raise RuntimeError('broken input fixture')"},
            "import failed",
        ),
        (
            "input_mismatch",
            {"wrong_package": _INPUT_EXPORT},
            "package/declaration identity mismatch",
        ),
        (
            "input_duplicate",
            {"duplicate_a": _INPUT_EXPORT, "duplicate_b": _INPUT_EXPORT},
            "duplicate input source plugin registration",
        ),
    ),
)
def test_input_source_discovery_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    packages: dict[str, str | None],
    match: str,
) -> None:
    namespace = _namespace(tmp_path, monkeypatch, name, packages)
    with pytest.raises(InputSourcePluginDiscoveryError, match=match):
        discover_input_source_plugins(InputSourceDiscoveryRoot(namespace))


def test_control_mapping_package_only_onboarding_and_production_separation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _namespace(
        tmp_path,
        monkeypatch,
        "test_control_mapping_plugins",
        {
            "_private": "raise RuntimeError('private package must not import')",
            "test_dummy_mapping": _MAPPING_EXPORT,
        },
    )
    registry = discover_control_mapping_plugins(
        ControlMappingDiscoveryRoot(namespace)
    )

    assert registry.ids == ("test_dummy_mapping",)
    assert registry.resolve(
        PluginSelection("test_dummy_mapping", 1)
    ).identity.canonical_id == "test_dummy_mapping/v1"
    assert "test_dummy_mapping" not in CONTROL_MAPPING_REGISTRY.ids


@pytest.mark.parametrize(
    ("name", "packages", "match"),
    (
        (
            "mapping_missing",
            {"missing_mapping": None},
            "entry point is missing",
        ),
        (
            "mapping_wrong_type",
            {"wrong_type": "CONTROL_MAPPING_PLUGIN = object()"},
            "invalid Control Mapping Plugin type",
        ),
        (
            "mapping_broken",
            {"broken_mapping": "raise RuntimeError('broken mapping fixture')"},
            "import failed",
        ),
        (
            "mapping_mismatch",
            {"wrong_package": _MAPPING_EXPORT},
            "package/declaration identity mismatch",
        ),
        (
            "mapping_duplicate",
            {"duplicate_a": _MAPPING_EXPORT, "duplicate_b": _MAPPING_EXPORT},
            "duplicate control mapping plugin registration",
        ),
    ),
)
def test_control_mapping_discovery_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    packages: dict[str, str | None],
    match: str,
) -> None:
    namespace = _namespace(tmp_path, monkeypatch, name, packages)
    with pytest.raises(ControlMappingPluginDiscoveryError, match=match):
        discover_control_mapping_plugins(ControlMappingDiscoveryRoot(namespace))
