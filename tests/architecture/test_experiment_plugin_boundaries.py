from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src" / "xpotato_sim" / "runtime"


def test_generic_experiment_contracts_do_not_import_robot_specific_implementations() -> None:
    generic_files = (
        "experiment/contracts.py",
        "experiment/input_source.py",
        "experiment/registry.py",
        "experiment/composition.py",
        "composition/robot_bundle.py",
        "composition/robot_provider_adapters.py",
    )
    forbidden = (
        "fast_arm",
        "sholder_joint",
        "elbow_joint",
        "FastArm",
        "tip\"",
        "mujoco.mj",
    )
    for name in generic_files:
        source = (RUNTIME / name).read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{name} contains robot-specific marker {marker!r}"


def test_task_and_evaluation_contracts_do_not_import_solver_or_viewer_layers() -> None:
    source = (RUNTIME / "experiment" / "contracts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert not any(
        marker in imported
        for imported in imports
        for marker in ("fast_arm", "kinematics", "mujoco_backend", "viewer")
    )


def test_generic_input_source_contract_does_not_import_concrete_layers() -> None:
    source = (RUNTIME / "experiment" / "input_source.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert not any(
        marker in imported
        for imported in imports
        for marker in (
            "fast_arm",
            "task",
            "evaluation",
            "viewer",
            "serial",
            "mujoco_backend",
        )
    )
    assert "importlib" not in source


def test_viewer_remains_outside_task_contact_and_metric_ownership() -> None:
    viewer = ROOT / "apps" / "mujoco-viewer" / "src"
    for path in viewer.rglob("*.ts*"):
        source = path.read_text(encoding="utf-8")
        assert "TaskLifecycleStrategy" not in source
        assert "MetricDerivationStrategy" not in source
        assert "ContactEvidenceProvider" not in source


def test_environment_task_evaluation_plugins_do_not_cross_import_concrete_axes() -> None:
    plugin_root = ROOT / "src" / "xpotato_sim" / "plugins"
    forbidden = {
        "environments": ("plugins.tasks.", "plugins.evaluations.", "fast_arm"),
        "tasks": ("plugins.environments.", "plugins.evaluations.", "fast_arm"),
        "evaluations": ("plugins.environments.", "plugins.tasks.", "fast_arm"),
    }
    for axis, markers in forbidden.items():
        for path in (plugin_root / axis).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in markers:
                assert marker not in source, f"{path}:{marker}"


def test_runtime_public_surface_is_minimal_and_catalog_aware() -> None:
    import xpotato_sim.runtime as runtime

    assert set(runtime.__all__) == {
        "RuntimeConfig",
        "registered_robot_bundle_ids",
        "registered_robot_runtime_plugin_ids",
        "resolve_robot_bundle",
        "resolve_robot_runtime",
        "resolve_robot_runtime_plugin",
    }
    assert "RobotBundle" not in runtime.__all__
    assert "compose_experiment" not in runtime.__all__
    assert "FAST_ARM_ROBOT_BUNDLE" not in runtime.__all__
    assert not hasattr(runtime, "FAST_ARM_ROBOT_BUNDLE")
