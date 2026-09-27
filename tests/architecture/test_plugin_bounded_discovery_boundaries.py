from __future__ import annotations

import ast
from pathlib import Path

from xpotato_sim.plugins import (
    environments,
    evaluations,
    input_sources,
    mappings,
    robots,
    tasks,
)
from xpotato_sim.plugins.bounded_discovery import direct_child_package_names
from xpotato_sim.plugins.environments.catalog import ENVIRONMENT_REGISTRY
from xpotato_sim.plugins.evaluations.catalog import EVALUATION_REGISTRY
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.catalog import CONTROL_MAPPING_REGISTRY
from xpotato_sim.plugins.tasks.catalog import TASK_REGISTRY


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "xpotato_sim"
PLUGINS = SRC / "plugins"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_discoverable_axes_use_fixed_package_entry_points() -> None:
    assert (PLUGINS / "robots" / "fast_arm" / "plugin.py").is_file()
    for plugin_id in INPUT_SOURCE_CATALOG.ids:
        entry = PLUGINS / "input_sources" / plugin_id / "plugin.py"
        assert entry.is_file(), plugin_id
        assert "INPUT_SOURCE_PLUGIN" in entry.read_text(encoding="utf-8")
    for plugin_id in CONTROL_MAPPING_REGISTRY.ids:
        entry = PLUGINS / "mappings" / plugin_id / "plugin.py"
        assert entry.is_file(), plugin_id
        assert "CONTROL_MAPPING_PLUGIN" in entry.read_text(encoding="utf-8")
    for axis, registry, symbol in (
        ("environments", ENVIRONMENT_REGISTRY, "ENVIRONMENT_PLUGIN"),
        ("tasks", TASK_REGISTRY, "TASK_PLUGIN"),
        ("evaluations", EVALUATION_REGISTRY, "EVALUATION_PLUGIN"),
    ):
        for plugin_id in registry.ids:
            entry = PLUGINS / axis / plugin_id / "plugin.py"
            assert entry.is_file(), plugin_id
            assert symbol in entry.read_text(encoding="utf-8")


def test_plugin_readme_coverage_follows_bounded_discovery_candidates() -> None:
    assert (PLUGINS / "README.md").is_file()
    for axis in (
        "robots",
        "input_sources",
        "mappings",
        "environments",
        "tasks",
        "evaluations",
    ):
        assert (PLUGINS / axis / "README.md").is_file(), axis

    for namespace in (
        robots,
        input_sources,
        mappings,
        environments,
        tasks,
        evaluations,
    ):
        axis_root = PLUGINS / namespace.__name__.rsplit(".", 1)[-1]
        for package_name in direct_child_package_names(namespace):
            readme = axis_root / package_name / "README.md"
            assert readme.is_file(), (
                "discoverable plugin package requires README.md: "
                f"{readme.relative_to(ROOT)}"
            )


def test_catalogs_and_generic_registration_do_not_list_concrete_plugins() -> None:
    guarded = (
        PLUGINS / "input_sources" / "catalog.py",
        PLUGINS / "mappings" / "catalog.py",
        PLUGINS / "environments" / "catalog.py",
        PLUGINS / "tasks" / "catalog.py",
        PLUGINS / "evaluations" / "catalog.py",
        PLUGINS / "input_sources" / "registration.py",
    )
    concrete_ids = (
        *INPUT_SOURCE_CATALOG.ids,
        *CONTROL_MAPPING_REGISTRY.ids,
        *ENVIRONMENT_REGISTRY.ids,
        *TASK_REGISTRY.ids,
        *EVALUATION_REGISTRY.ids,
    )
    for path in guarded:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not set(concrete_ids) & string_literals, path
        allowed_axis_infrastructure = {
            "xpotato_sim.plugins.input_sources.discovery",
            "xpotato_sim.plugins.input_sources.registration",
            "xpotato_sim.plugins.mappings.discovery",
            "xpotato_sim.plugins.environments.discovery",
            "xpotato_sim.plugins.tasks.discovery",
            "xpotato_sim.plugins.evaluations.discovery",
        }
        assert not any(
            (
                module.startswith("xpotato_sim.plugins.input_sources.")
                or module.startswith("xpotato_sim.plugins.mappings.")
                or module.startswith("xpotato_sim.plugins.environments.")
                or module.startswith("xpotato_sim.plugins.tasks.")
                or module.startswith("xpotato_sim.plugins.evaluations.")
            )
            and module not in allowed_axis_infrastructure
            for module in _imports(path)
        ), path


def test_mapping_root_compatibility_exports_are_retired() -> None:
    compatibility = (PLUGINS / "mappings" / "__init__.py").read_text(
        encoding="utf-8"
    )
    discovery = (PLUGINS / "mappings" / "discovery.py").read_text(
        encoding="utf-8"
    )
    catalog = (PLUGINS / "mappings" / "catalog.py").read_text(encoding="utf-8")

    assert "_PUBLIC_EXPORTS" not in compatibility
    assert "__getattr__" not in compatibility
    assert "__all__: list[str] = []" in compatibility
    for production_source in (discovery, catalog):
        assert "_PUBLIC_EXPORTS" not in production_source
        assert "mappings.__all__" not in production_source
        assert "REPLAY_CONTROL_MAPPING_PLUGIN" not in production_source


def test_discovery_is_bounded_and_axis_validation_stays_typed() -> None:
    helper = (PLUGINS / "bounded_discovery.py").read_text(encoding="utf-8")
    assert "pkgutil.iter_modules(namespace.__path__)" in helper
    assert "item.ispkg" in helper
    assert 'item.name.startswith("_")' in helper
    assert "sorted(" in helper
    assert "importlib.import_module(module_name)" in helper
    assert "entry_points" not in helper
    assert "sys.path" not in helper

    input_discovery = (
        PLUGINS / "input_sources" / "discovery.py"
    ).read_text(encoding="utf-8")
    mapping_discovery = (
        PLUGINS / "mappings" / "discovery.py"
    ).read_text(encoding="utf-8")
    assert 'INPUT_SOURCE_PLUGIN_ENTRY_MODULE = "plugin"' in input_discovery
    assert (
        'INPUT_SOURCE_PLUGIN_ENTRY_SYMBOL = "INPUT_SOURCE_PLUGIN"'
        in input_discovery
    )
    assert 'CONTROL_MAPPING_PLUGIN_ENTRY_MODULE = "plugin"' in mapping_discovery
    assert (
        'CONTROL_MAPPING_PLUGIN_ENTRY_SYMBOL = "CONTROL_MAPPING_PLUGIN"'
        in mapping_discovery
    )
    assert "isinstance(registration, InputSourcePluginRegistration)" in (
        input_discovery
    )
    assert "isinstance(plugin, ControlMappingPlugin)" in mapping_discovery


def test_mapping_implementation_is_package_owned_without_cross_plugin_imports() -> None:
    mapping_root = PLUGINS / "mappings"
    plugin_ids = set(CONTROL_MAPPING_REGISTRY.ids)
    for plugin_id in sorted(plugin_ids):
        package = mapping_root / plugin_id
        assert (package / "implementation.py").is_file()
        for path in package.rglob("*.py"):
            for module in _imports(path):
                for other in plugin_ids - {plugin_id}:
                    assert not module.startswith(
                        f"xpotato_sim.plugins.mappings.{other}"
                    ), f"{path}:{module}"

    shared = mapping_root / "_continuous_endpoint_velocity.py"
    assert shared.is_file()
    shared_text = shared.read_text(encoding="utf-8")
    assert not any(plugin_id in shared_text for plugin_id in plugin_ids)


def test_environment_task_evaluation_use_axis_local_bounded_infrastructure() -> None:
    expected = {
        "environments": ("contact_cube_environment", "free_space_environment"),
        "tasks": ("contact_press_hold_task", "endpoint_reach_task"),
        "evaluations": (
            "completion_time",
            "contact_outcome",
            "final_endpoint_error",
            "off_axis_drift",
            "success_within_timeout",
        ),
    }
    for axis, plugin_ids in expected.items():
        namespace = PLUGINS / axis
        assert (namespace / "catalog.py").is_file()
        assert (namespace / "discovery.py").is_file()
        assert not (namespace / "registration.py").exists()
        assert tuple(
            sorted(
                path.name
                for path in namespace.iterdir()
                if path.is_dir() and not path.name.startswith("_")
            )
        ) == plugin_ids


def test_generic_experiment_layer_has_no_concrete_plugin_imports() -> None:
    violations: list[str] = []
    for path in (SRC / "runtime" / "experiment").rglob("*.py"):
        for module in _imports(path):
            if module.startswith("xpotato_sim.plugins.input_sources.") or (
                module.startswith("xpotato_sim.plugins.mappings.")
            ):
                violations.append(f"{path}:{module}")
    assert not violations
