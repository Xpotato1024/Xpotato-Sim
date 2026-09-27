from __future__ import annotations

import ast
from pathlib import Path

import xpotato_sim as xpotato_sim_package
import xpotato_sim.kinematics as kinematics
import xpotato_sim.motion as motion
import xpotato_sim.mujoco_backend as mujoco_backend
import xpotato_sim.runtime as runtime
import xpotato_sim.schemas as schemas
import xpotato_sim.transport as transport


ROOT = Path(__file__).resolve().parents[2]
DOUBLE_PATH = ROOT / "tests" / "support" / "kinematics_solver_doubles.py"
MIGRATED_GENERIC_TESTS = (
    ROOT / "tests" / "motion" / "test_target_to_joint_motion_generator.py",
    ROOT / "tests" / "runtime" / "test_endpoint_metrics.py",
    ROOT / "tests" / "runtime" / "test_kinematic_evaluation.py",
)
FORBIDDEN_DOUBLE_IMPORTS = {
    "xpotato_sim.kinematics.fk",
    "xpotato_sim.kinematics.ik",
    "xpotato_sim.plugins.robots.fast_arm.kinematics",
    "xpotato_sim.mujoco_backend",
    "xpotato_sim.runtime",
}
DOUBLE_EXPORT_NAMES = {
    "FixedForwardKinematicsSolver",
    "FailingForwardKinematicsSolver",
    "FixedInverseKinematicsSolver",
    "FailingInverseKinematicsSolver",
    "SeedSensitiveInverseKinematicsSolver",
}
PRODUCTION_PACKAGES = (
    xpotato_sim_package,
    kinematics,
    motion,
    mujoco_backend,
    runtime,
    schemas,
    transport,
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def _called_names(tree: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.append(node.func.attr)
    return tuple(names)


def test_solver_doubles_are_production_and_robot_agnostic() -> None:
    source = DOUBLE_PATH.read_text(encoding="utf-8")
    tree = _parse(DOUBLE_PATH)
    imported_modules = _imported_modules(tree)

    assert "Planar" not in source
    assert "fast_arm" not in source.lower()
    assert "mujoco" not in source.lower()
    assert "viewer" not in source.lower()
    assert "runtime" not in source.lower()
    assert not any(module in FORBIDDEN_DOUBLE_IMPORTS for module in imported_modules)
    assert not any(module.startswith("apps.") for module in imported_modules)


def test_solver_doubles_use_no_dynamic_import_or_filesystem_discovery() -> None:
    tree = _parse(DOUBLE_PATH)
    called_names = set(_called_names(tree))
    imported_modules = set(_imported_modules(tree))

    assert not (imported_modules & {"importlib", "pathlib"})
    assert not (called_names & {"__import__", "import_module", "find_spec", "glob", "rglob", "walk"})


def test_solver_doubles_are_not_exported_by_production_packages() -> None:
    for module in PRODUCTION_PACKAGES:
        exported = set(getattr(module, "__all__", ()))
        assert not exported & DOUBLE_EXPORT_NAMES
        assert not any(hasattr(module, name) for name in DOUBLE_EXPORT_NAMES)


def test_migrated_generic_tests_have_no_planar_solver_dependency() -> None:
    for path in MIGRATED_GENERIC_TESTS:
        source = path.read_text(encoding="utf-8")
        tree = _parse(path)
        imported_modules = _imported_modules(tree)

        assert "PlanarChainForwardKinematicsSolver" not in source
        assert "PlanarTwoLinkInverseKinematicsSolver" not in source
        assert "xpotato_sim.kinematics.fk" not in imported_modules
        assert "xpotato_sim.kinematics.ik" not in imported_modules


def test_planar_solver_implementation_exports_and_production_consumers_are_retired() -> None:
    retired_names = {
        "PlanarChainForwardKinematicsSolver",
        "PlanarTwoLinkInverseKinematicsSolver",
    }
    production_sources = tuple((ROOT / "src" / "xpotato_sim").rglob("*.py"))

    assert not retired_names & set(kinematics.__all__)
    assert not any(hasattr(kinematics, name) for name in retired_names)
    for path in production_sources:
        source = path.read_text(encoding="utf-8")
        assert not any(name in source for name in retired_names), path
