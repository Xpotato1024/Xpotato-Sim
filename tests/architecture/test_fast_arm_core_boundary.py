from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter import bundle as adapter_bundle
from xpotato_sim.plugins.robots.fast_arm.adapter import kinematics as adapter_kinematics


ROOT = Path(__file__).resolve().parents[2]
FAST_ARM_ROOT = ROOT / "src/xpotato_sim/plugins/robots/fast_arm"
CORE_PROJECT = FAST_ARM_ROOT / "core"
CORE_SOURCE = CORE_PROJECT / "src/fast_arm_core"


def _imports(path: Path) -> set[str]:
    imported: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_fast_arm_core_is_an_independent_distribution() -> None:
    project = tomllib.loads((CORE_PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    root_project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "fast_arm_core"
    assert project["tool"]["setuptools"]["include-package-data"] is False
    assert root_project["tool"]["uv"]["sources"]["fast-arm-core"] == {
        "workspace": True
    }
    assert "fast-arm-core" in root_project["project"]["dependencies"]
    package_find = root_project["tool"]["setuptools"]["packages"]["find"]
    assert root_project["tool"]["setuptools"]["include-package-data"] is False
    assert package_find["namespaces"] is False
    assert "xpotato_sim.plugins.robots.fast_arm.core*" in package_find["exclude"]
    assert not (CORE_PROJECT / "__init__.py").exists()
    assert (ROOT / "MANIFEST.in").read_text(encoding="utf-8").strip() == (
        "prune src/xpotato_sim/plugins/robots/fast_arm/core"
    )


def test_fast_arm_core_has_no_selfrionette_dependency() -> None:
    violations = {
        str(path.relative_to(ROOT)): sorted(
            name for name in _imports(path) if name == "xpotato_sim" or name.startswith("xpotato_sim.")
        )
        for path in CORE_SOURCE.rglob("*.py")
        if any(
            name == "xpotato_sim" or name.startswith("xpotato_sim.")
            for name in _imports(path)
        )
    }
    assert violations == {}


def test_plugin_entrypoint_only_assembles_the_adapter() -> None:
    imports = _imports(FAST_ARM_ROOT / "plugin.py")
    concrete_imports = {
        name
        for name in imports
        if name.startswith("xpotato_sim.plugins.robots.fast_arm")
    }
    assert concrete_imports
    assert all(
        name.startswith("xpotato_sim.plugins.robots.fast_arm.adapter")
        for name in concrete_imports
    )


def test_generic_resource_manifest_tooling_contains_no_fast_arm_inventory() -> None:
    generic_sources = (
        ROOT
        / "src/xpotato_sim/runtime/composition/viewer_package_resource_manifest.py"
    ).read_text(encoding="utf-8") + (
        ROOT / "apps/mujoco-viewer/tooling/viewerPackageResources.ts"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "fast_arm",
        "BaseLink.stl",
        "SholderLink1.stl",
        "resources/model/arm.xml",
        "adapter/resources",
    ):
        assert forbidden not in generic_sources

    manifests = tuple(
        (ROOT / "src").rglob("viewer-resource-bindings.json")
    )
    assert manifests == (
        FAST_ARM_ROOT / "adapter/resources/viewer-resource-bindings.json",
    )


def test_initial_state_modules_describe_core_ownership_and_adapter_projection() -> None:
    adapter_source = (FAST_ARM_ROOT / "adapter/initial_state.py").read_text(
        encoding="utf-8"
    )
    core_source = (
        CORE_SOURCE / "reference/initial_state.py"
    ).read_text(encoding="utf-8")
    assert "Selfrionette projection of the core-owned" in adapter_source
    assert "Core-owned fast_arm initial-state reference" in core_source
    assert "Canonical fast_arm initial-state" not in adapter_source
    assert "simulator-independent initial-state" not in core_source


def test_adapter_modules_own_public_objects() -> None:
    assert adapter_bundle.FAST_ARM_ROBOT_BUNDLE is not None
    assert adapter_bundle.__name__ == "xpotato_sim.plugins.robots.fast_arm.adapter.bundle"
    assert adapter_kinematics.__name__ == "xpotato_sim.plugins.robots.fast_arm.adapter.kinematics"


@pytest.mark.parametrize(
    "module_suffix",
    (
        "bundle",
        "endpoint",
        "feasibility",
        "initial_state",
        "kinematics",
        "model_contract",
        "profile",
        "runtime",
        "viewer",
        "diagnostics.endpoint_motion_sanity",
        "diagnostics.jacobian_mobility",
        "diagnostics.neutral_initial_pose",
    ),
)
def test_retired_modules_are_absent_and_adapter_owner_imports(
    module_suffix: str,
) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"xpotato_sim.plugins.robots.fast_arm.{module_suffix}")
    adapter = importlib.import_module(f"xpotato_sim.plugins.robots.fast_arm.adapter.{module_suffix}")
    assert adapter.__all__
    assert not (FAST_ARM_ROOT / (module_suffix.replace(".", "/") + ".py")).exists()


def test_fast_arm_test_ownership_directories_have_real_tests_and_no_placeholders() -> None:
    ownership_roots = (
        ROOT / "tests/plugins/robots/fast_arm/core",
        ROOT / "tests/plugins/robots/fast_arm/adapter",
        ROOT / "tests/integration/fast_arm",
    )
    for directory in ownership_roots:
        assert directory.is_dir()
        assert tuple(directory.rglob("test_*.py")), directory
        assert not tuple(directory.rglob(".gitkeep")), directory
        assert not any(
            path.name == "README.md" and not path.read_text(encoding="utf-8").strip()
            for path in directory.rglob("README.md")
        )


def test_fast_arm_core_test_tree_has_no_selfrionette_imports() -> None:
    violations: list[str] = []
    for path in (ROOT / "tests/plugins/robots/fast_arm/core").rglob("*.py"):
        if any(
            name == "xpotato_sim" or name.startswith("xpotato_sim.")
            for name in _imports(path)
        ):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations
    production_test_files = tuple(
        path
        for path in FAST_ARM_ROOT.rglob("*.py")
        if path.name.startswith("test_")
        or path.name == "conftest.py"
        or "tests" in path.relative_to(FAST_ARM_ROOT).parts
    )
    production_test_directories = tuple(
        path
        for path in FAST_ARM_ROOT.rglob("*")
        if path.is_dir() and path.name == "tests"
    )
    assert not production_test_files
    assert not production_test_directories


def test_fast_arm_specific_tests_leave_old_paths_and_generic_owners_in_place() -> None:
    old_paths = (
        ROOT / "tests/assets/test_fast_arm_assets.py",
        ROOT / "tests/kinematics/test_fast_arm_endpoint.py",
        ROOT / "tests/runtime/test_fast_arm_endpoint_diagnostic_logging.py",
        ROOT / "tests/runtime/test_fast_arm_endpoint_motion_sanity.py",
        ROOT / "tests/runtime/test_fast_arm_endpoint_trajectory_diagnostics.py",
        ROOT / "tests/runtime/test_fast_arm_endpoint_trajectory_export.py",
        ROOT / "tests/runtime/test_fast_arm_fk_site_consistency.py",
        ROOT / "tests/runtime/test_fast_arm_ik_fk_sanity.py",
        ROOT / "tests/runtime/test_fast_arm_initial_tip_workspace_diagnostics.py",
        ROOT / "tests/runtime/test_fast_arm_jacobian_mobility_diagnostics.py",
        ROOT / "tests/runtime/test_fast_arm_joint_axis_mapping_diagnostics.py",
        ROOT / "tests/runtime/test_fast_arm_joint_limits.py",
        ROOT / "tests/runtime/test_fast_arm_local_jacobian_dof_allocation.py",
        ROOT / "tests/runtime/test_fast_arm_plugin_catalog.py",
        ROOT / "tests/runtime/test_fast_arm_solver_mujoco_frame_alignment.py",
        ROOT / "tests/runtime/test_fast_arm_viewer_endpoint_workspace_diagnostics.py",
        ROOT / "tests/runtime/test_neutral_initial_pose.py",
        ROOT / "tests/robots/fast_arm_conformance_case.py",
    )
    assert all(not path.exists() for path in old_paths)
    assert (ROOT / "tests/architecture/test_kinematics_test_double_boundaries.py").is_file()
    assert (ROOT / "tests/kinematics/test_inverse_kinematics_solver.py").is_file()
    assert (ROOT / "tests/runtime/test_concrete_mujoco_pipeline.py").is_file()
    assert (ROOT / "tests/support/test_robot_runtime_plugin_conformance.py").is_file()
    assert (ROOT / "apps/mujoco-viewer/tests/robotProfileRegistry.test.ts").is_file()
