from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERIC_SUPPORT = ROOT / "tests" / "support" / "robot_runtime_plugin_conformance.py"
ROBOT_CASE = ROOT / "tests" / "plugins" / "robots" / "fast_arm" / "adapter" / "conformance_case.py"
CASE_REGISTRY = ROOT / "tests" / "robots" / "robot_runtime_plugin_conformance_cases.py"


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


def test_generic_harness_is_robot_agnostic_and_viewer_free() -> None:
    source = GENERIC_SUPPORT.read_text(encoding="utf-8")
    tree = _parse(GENERIC_SUPPORT)

    assert "fast_arm" not in source.lower()
    assert not any(module.startswith("xpotato_sim.robots") for module in _imported_modules(tree))
    assert not any(module.endswith("fast_arm_plugin") for module in _imported_modules(tree))
    assert not any(name.startswith("FAST_ARM") for name in _called_names(tree))
    assert "mujoco-viewer" not in source
    assert "three.js" not in source.lower()


def test_robot_specific_expected_values_are_owned_by_the_robot_case() -> None:
    generic_tree = _parse(GENERIC_SUPPORT)
    case_tree = _parse(ROBOT_CASE)

    generic_case_constructors = [
        node
        for node in ast.walk(generic_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {
            "KnownForwardKinematicsCase",
            "InverseKinematicsRoundTripCase",
            "MuJoCoEndpointConsistencyCase",
        }
    ]
    case_constructors = [
        node
        for node in ast.walk(case_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {
            "KnownForwardKinematicsCase",
            "InverseKinematicsRoundTripCase",
            "MuJoCoEndpointConsistencyCase",
        }
    ]

    assert generic_case_constructors == []
    assert len(case_constructors) == 6


def test_production_source_does_not_import_test_support() -> None:
    violations: list[str] = []
    for path in (ROOT / "src" / "xpotato_sim").rglob("*.py"):
        for module in _imported_modules(_parse(path)):
            if module == "tests" or module.startswith("tests."):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not violations, "\n".join(violations)


def test_test_conformance_support_is_not_a_runtime_export() -> None:
    import xpotato_sim.runtime as runtime

    assert not any("Conformance" in name for name in runtime.__all__)
    assert not hasattr(runtime, "RobotRuntimePluginConformanceCase")


def test_conformance_code_uses_no_arbitrary_dynamic_imports() -> None:
    paths = (GENERIC_SUPPORT, ROBOT_CASE, CASE_REGISTRY)
    violations: list[str] = []
    for path in paths:
        tree = _parse(path)
        modules = _imported_modules(tree)
        if "importlib" in modules or any(module.startswith("importlib.") for module in modules):
            violations.append(f"{path.relative_to(ROOT)} imports importlib")
        if "__import__" in _called_names(tree):
            violations.append(f"{path.relative_to(ROOT)} calls __import__")
    assert not violations, "\n".join(violations)
