from __future__ import annotations

import ast
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "xpotato_sim"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return tuple(names)


def test_generic_contracts_do_not_import_catalog_or_concrete_plugins() -> None:
    paths = (
        SRC / "runtime" / "composition" / "robot_plugin.py",
        SRC / "runtime" / "composition" / "robot_bundle.py",
        SRC / "runtime" / "composition" / "robot_provider_adapters.py",
        SRC / "runtime" / "composition" / "robot_resolution.py",
        SRC / "runtime" / "experiment" / "contracts.py",
        SRC / "runtime" / "experiment" / "registry.py",
        SRC / "runtime" / "experiment" / "composition.py",
        SRC / "runtime" / "evaluation" / "manifest.py",
    )
    for path in paths:
        imported = _imports(path)
        assert not any(name.startswith("xpotato_sim.plugins") for name in imported), path
        assert not any("fast_arm" in name for name in imported), path


def test_domain_layers_do_not_reverse_depend_on_assembly_or_manifest() -> None:
    forbidden = (
        "xpotato_sim.plugins.robots.catalog",
        "xpotato_sim.runtime.composition.robot_bundle",
        "xpotato_sim.runtime.evaluation.manifest",
    )
    paths = tuple((SRC / "kinematics").rglob("*.py"))
    paths += tuple((SRC / "motion").rglob("*.py"))
    paths += tuple((SRC / "mujoco_backend").rglob("*.py"))
    for path in paths:
        imported = _imports(path)
        assert not any(
            name.startswith(prefix)
            for name in imported
            for prefix in forbidden
        ), path


def test_generic_plugin_axes_do_not_embed_fast_arm_names_or_solver_types() -> None:
    forbidden = (
        "fast_arm",
        "sholder_joint",
        "elbow_joint",
        "FastArm",
        "tip\"",
        "geom",
    )
    for directory in ("environments", "mappings", "tasks", "evaluations"):
        for path in (SRC / "plugins" / directory).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in forbidden:
                assert marker not in source, f"{path}: {marker}"


def test_runtime_execution_edges_use_typed_providers_not_broad_plugins() -> None:
    input_loop_source = (SRC / "runtime" / "execution" / "input_step_loop.py").read_text(
        encoding="utf-8"
    )
    offline_smoke_source = (
        SRC / "runtime" / "runners" / "offline_input_smoke.py"
    ).read_text(encoding="utf-8")
    for source in (input_loop_source, offline_smoke_source):
        assert "ResolvedRobotRuntime" not in source
        assert "resolve_robot_runtime" not in source
        assert "plugin.endpoint_position_from_state(" not in source
        assert "plugin.endpoint_orientation_from_state(" not in source
        assert "plugin.build_qpos_feasibility_guard(" not in source
        assert "plugin.build_target_motion_generator(" not in source
        assert "observe_endpoint_pose(" in source

    assert "resolved_robot_runtime" not in input_loop_source
    assert "endpoint_pose_provider: EndpointPoseProvider" in input_loop_source
    assert (
        "endpoint_command_provider: EndpointCommandProvider" in input_loop_source
    )
    assert (
        "qpos_feasibility_provider: QposFeasibilityProvider" in input_loop_source
    )


def test_catalog_and_bundle_do_not_introduce_defaults_or_dynamic_discovery() -> None:
    paths = (
        SRC / "plugins" / "robots" / "catalog.py",
        SRC / "plugins" / "__init__.py",
        SRC / "plugins" / "robots" / "fast_arm" / "bundle.py",
        SRC / "runtime" / "composition" / "robot_provider_adapters.py",
        SRC / "runtime" / "composition" / "robot_resolution.py",
    )
    forbidden = (
        "DefaultRobot",
        "DEFAULT_ROBOT_BUNDLE",
        "importlib",
        "__import__",
        "entry_points",
        "pkgutil",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{path}: {marker}"


def test_bounded_discovery_has_one_production_owner_and_fixed_entry_point() -> None:
    discovery = (SRC / "plugins" / "robots" / "discovery.py").read_text(encoding="utf-8")
    catalog = (SRC / "plugins" / "robots" / "catalog.py").read_text(encoding="utf-8")

    assert 'ROBOT_PLUGIN_ENTRY_MODULE = "plugin"' in discovery
    assert 'ROBOT_PLUGIN_ENTRY_SYMBOL = "ROBOT_PLUGIN"' in discovery
    assert "entry_points" not in discovery
    assert "RuntimeConfig" not in discovery
    assert "importlib.import_module(module_name)" in discovery
    assert "root.namespace.__name__" in discovery
    assert "xpotato_sim.plugins.robots.fast_arm" not in catalog
    assert "fast_arm" not in catalog

    discovery_importers = {
        path.relative_to(SRC)
        for path in SRC.rglob("*.py")
        if any(
            imported == "xpotato_sim.plugins.robots.discovery"
            for imported in _imports(path)
        )
    }
    assert discovery_importers == {Path("plugins/robots/catalog.py")}


def test_test_discovery_root_and_fixture_names_do_not_enter_production_sources() -> None:
    production_sources = tuple(SRC.rglob("*.py")) + tuple(
        (ROOT / "apps" / "mujoco-viewer" / "src").rglob("*.ts")
    ) + tuple((ROOT / "apps" / "mujoco-viewer" / "src").rglob("*.tsx"))
    for path in production_sources:
        source = path.read_text(encoding="utf-8")
        assert "test_robot_plugins" not in source, path
        assert "fixture_bot" not in source, path

    fixture_root = ROOT / "tests" / "fixtures" / "robot_plugins"
    assert (fixture_root / "test_robot_plugins" / "fixture_bot" / "plugin.py").is_file()
    assert not (SRC / "selfrionette_test_plugins").exists()


def test_runtime_generic_exports_are_catalog_free_until_resolver_access() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    for resolver_name in ("resolve_robot_runtime", "resolve_robot_bundle"):
        command = (
            "import sys; import xpotato_sim.runtime as runtime; "
            "assert 'xpotato_sim.plugins.robots.catalog' not in sys.modules; "
            "assert 'xpotato_sim.plugins.robots.fast_arm.plugin' not in sys.modules; "
            "from xpotato_sim.runtime.composition.robot_resolution import "
            "ResolvedRobotRuntime as direct_runtime; "
            "from xpotato_sim.runtime.composition.robot_bundle import RobotBundle as direct_bundle; "
            "from xpotato_sim.runtime.experiment.contracts import "
            "VersionedIdentity as direct_identity; "
            "assert direct_runtime.__module__ == 'xpotato_sim.runtime.composition.robot_resolution'; "
            "assert direct_bundle.__module__ == 'xpotato_sim.runtime.composition.robot_bundle'; "
            "assert direct_identity.__module__ == 'xpotato_sim.runtime.experiment.contracts'; "
            "assert not hasattr(runtime, 'ResolvedRobotRuntime'); "
            "assert not hasattr(runtime, 'RobotBundle'); "
            "assert not hasattr(runtime, 'VersionedIdentity'); "
            "assert 'xpotato_sim.plugins.robots.catalog' not in sys.modules; "
            "assert 'xpotato_sim.plugins.robots.fast_arm.plugin' not in sys.modules; "
            f"getattr(runtime, {resolver_name!r}); "
            "assert 'xpotato_sim.plugins.robots.catalog' in sys.modules; "
            "assert 'xpotato_sim.plugins.robots.fast_arm.plugin' in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_runtime_public_exports_have_one_explicit_owner_and_preserve_identity() -> None:
    import xpotato_sim.runtime as runtime

    assert set(runtime._PUBLIC_EXPORTS) == set(runtime.__all__)
    source = (SRC / "runtime" / "__init__.py").read_text(encoding="utf-8")
    assert "_PUBLIC_EXPORT_MODULES" not in source
    assert "hasattr(" not in source

    for public_name, (
        module_name,
        attribute_name,
    ) in runtime._PUBLIC_EXPORTS.items():
        owner = import_module(module_name)
        assert getattr(runtime, public_name) is getattr(
            owner, attribute_name
        ), public_name
