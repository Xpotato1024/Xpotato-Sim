"""C4 guards for final Input Source public compatibility retirement."""

from __future__ import annotations

import ast
from pathlib import Path

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.catalog import (
    CONTROL_MAPPING_PLUGINS,
    CONTROL_MAPPING_REGISTRY,
)
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.runtime.experiment.input_source import InputSourceMode
from xpotato_sim.runtime.control.input_source_mapping_policy import (
    default_control_mapping_selection,
)


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "xpotato_sim"
OLD_PACKAGE_PREFIXES = (
    "xpotato_sim.input_sources",
    "xpotato_sim.input_interpreters",
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return tuple(modules)


def _uses_old_package(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in OLD_PACKAGE_PREFIXES
    )


def test_old_package_directories_and_compatibility_wrappers_do_not_exist() -> None:
    assert not (SRC / "input_sources").exists()
    assert not (SRC / "input_interpreters").exists()
    assert not (ROOT / "scripts" / "compatibility").exists()


def test_old_package_imports_are_completely_forbidden() -> None:
    violations: list[str] = []
    for root in (ROOT / "src", ROOT / "scripts", ROOT / "tests"):
        for path in root.rglob("*.py"):
            for module in _imported_modules(path):
                if _uses_old_package(module):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert not violations, "\n".join(violations)


def test_legacy_runtime_pipeline_is_not_defined_or_exported() -> None:
    pipeline = SRC / "runtime" / "execution" / "pipeline.py"
    runtime_root = SRC / "runtime" / "__init__.py"
    pipeline_classes = {
        node.name for node in _parse(pipeline).body if isinstance(node, ast.ClassDef)
    }
    assert pipeline_classes == {"ControlMappedRuntimePipeline"}
    assert "RuntimePipeline" not in runtime_root.read_text(encoding="utf-8")


def test_no_production_compatibility_registry_or_mapping_facade_exists() -> None:
    production_source = "\n".join(
        path.read_text(encoding="utf-8") for path in SRC.rglob("*.py")
    )
    assert "InputSourceDescriptor" not in production_source
    assert "compatibility_execution_adapter" not in production_source


def test_selfrionette_runner_requires_an_explicit_versioned_mapping() -> None:
    runner = SRC / "runtime" / "runners" / "selfrionette_serial_dry_run.py"
    tree = _parse(runner)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "run_selfrionette_serial_dry_run_smoke"
    )
    keyword_defaults = dict(
        zip(
            (argument.arg for argument in function.args.kwonlyargs),
            function.args.kw_defaults,
            strict=True,
        )
    )
    assert keyword_defaults["mapping_plugin"] is None
    source = runner.read_text(encoding="utf-8")
    assert 'PluginSelection("loadcell_endpoint_mapping", 1)' in source
    assert "mapping_plugin=resolve_control_mapping_plugin(" in source
    assert "if mapping_plugin is None" not in source
    assert "LoadcellEndpointMotionCommandConverter" not in source


def test_current_operator_docs_do_not_reference_retired_compatibility_surfaces() -> None:
    forbidden = (
        "scripts/compatibility/",
        "C4まで残",
        "C4までのpublic compatibility",
        "compatibility package exists",
        "legacy RuntimePipeline public compatibility",
    )
    violations: list[str] = []
    for root in (
        ROOT / "README.md",
        ROOT / "docs" / "architecture",
        ROOT / "docs" / "contracts",
        ROOT / "docs" / "operations",
    ):
        paths = (root,) if root.is_file() else root.rglob("*.md")
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for phrase in forbidden:
                if phrase in text:
                    violations.append(f"{path.relative_to(ROOT)}:{phrase}")
    assert not violations, "\n".join(violations)


def test_current_cli_docs_match_command_specific_input_source_choices() -> None:
    unified_cli = (ROOT / "docs" / "operations" / "unified-cli.md").read_text(
        encoding="utf-8"
    )
    runtime_dry_run = (
        ROOT / "docs" / "operations" / "runtime-dry-run.md"
    ).read_text(encoding="utf-8")

    replay_choices = "`programmed_target` / `replay` / `noop`"
    viewer_choices = "`programmed_target` / `replay` / `noop` / `viewer`"
    assert replay_choices in unified_cli
    assert viewer_choices in unified_cli
    assert "`replay --input-source viewer`は受理しない" in runtime_dry_run
    assert "--robot" in unified_cli


def test_catalog_and_mapping_identities_remain_canonical() -> None:
    assert INPUT_SOURCE_CATALOG.ids == (
        "analog_fixture",
        "noop",
        "programmed_target",
        "replay",
        "selfrionette",
        "viewer",
    )
    assert tuple(
        registration.plugin.identity.canonical_id
        for registration in INPUT_SOURCE_CATALOG.registrations
    ) == (
        "analog_fixture/v1",
        "noop/v1",
        "programmed_target/v1",
        "replay/v1",
        "selfrionette/v1",
        "viewer/v1",
    )
    assert INPUT_SOURCE_CATALOG.aliases == (
        "analog_fixture",
        "noop",
        "programmed_target",
        "replay",
        "selfrionette",
        "viewer",
    )
    assert CONTROL_MAPPING_REGISTRY.ids == (
        "analog_fixture_mapping",
        "loadcell_endpoint_mapping",
        "replay_mapping",
        "viewer_keyboard_gamepad_mapping",
    )
    assert tuple(plugin.identity.canonical_id for plugin in CONTROL_MAPPING_PLUGINS) == (
        "analog_fixture_mapping/v1",
        "loadcell_endpoint_mapping/v1",
        "replay_mapping/v1",
        "viewer_keyboard_gamepad_mapping/v1",
    )


def test_source_contracts_and_explicit_mappings_are_preserved() -> None:
    expected = {
        "programmed_target": (
            VersionedIdentity("programmed_target_sample", 1),
            InputSourceMode.OFFLINE,
            VersionedIdentity("target_metadata_input_execution", 1),
            VersionedIdentity("replay_raw_input_frame", 1),
            "replay_mapping/v1",
        ),
        "replay": (
            VersionedIdentity("replay_raw_input_frame", 1),
            InputSourceMode.REPLAY,
            VersionedIdentity("replay_compatibility_input_execution", 1),
            VersionedIdentity("replay_raw_input_frame", 1),
            "replay_mapping/v1",
        ),
        "noop": (
            VersionedIdentity("noop_sample", 1),
            InputSourceMode.OFFLINE,
            VersionedIdentity("replay_compatibility_input_execution", 1),
            VersionedIdentity("replay_raw_input_frame", 1),
            "replay_mapping/v1",
        ),
        "viewer": (
            VersionedIdentity("viewer_control_sample", 1),
            InputSourceMode.VIEWER_BRIDGE,
            VersionedIdentity("viewer_local_endpoint_input_execution", 1),
            VersionedIdentity("viewer_control_sample", 1),
            "viewer_keyboard_gamepad_mapping/v1",
        ),
        "selfrionette": (
            VersionedIdentity("loadcell_vector_sample", 1),
            InputSourceMode.LIVE,
            VersionedIdentity("loadcell_input_execution", 1),
            VersionedIdentity("loadcell_normalized_input_intent", 1),
            "loadcell_endpoint_mapping/v1",
        ),
        "analog_fixture": (
            VersionedIdentity("analog_fixture_sample", 1),
            InputSourceMode.REPLAY,
            VersionedIdentity("analog_fixture_input_execution", 1),
            VersionedIdentity("analog_fixture_sample", 1),
            "analog_fixture_mapping/v1",
        ),
    }
    actual = {
        registration.plugin.identity.name: (
            registration.plugin.produced_sample_schema,
            registration.plugin.mode,
            registration.execution_adapter.identity,
            registration.plugin.effective_mapping_input_sample_schema,
            (
                f"{default_control_mapping_selection(registration.plugin.identity.name).plugin_id}/"
                f"v{default_control_mapping_selection(registration.plugin.identity.name).contract_version}"
            ),
        )
        for registration in INPUT_SOURCE_CATALOG.registrations
    }
    assert actual == expected
