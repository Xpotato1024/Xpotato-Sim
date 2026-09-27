from __future__ import annotations

import ast
from pathlib import Path

import xpotato_sim.schemas as schemas


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "src" / "xpotato_sim" / "schemas"
DOMAIN_MODULES = {
    "coordinated": set(),
    "command": {"types"},
    "endpoint": {"types"},
    "experiment_log": {"endpoint"},
    "input": {"types"},
    "state": {"types"},
    "types": set(),
    "viewer_control": set(),
    "viewer_input": {"viewer_control"},
}
RETIRED_MODULES = {
    "continuous_endpoint_velocity",
    "endpoint_metadata",
    "experiment_motion_log",
    "input_frame",
    "input_intent",
    "joint_command",
    "motion_command",
    "mujoco_state",
    "render_state",
    "target_command",
    "viewer_control_message",
}


def _direct_schema_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        prefix = "xpotato_sim.schemas."
        if node.module.startswith(prefix):
            dependencies.add(node.module.removeprefix(prefix).split(".", 1)[0])
    return dependencies


def test_schema_files_are_grouped_by_wire_domain() -> None:
    assert {path.stem for path in SCHEMA_ROOT.glob("*.py")} == {
        "__init__",
        *DOMAIN_MODULES,
    }
    assert all(not (SCHEMA_ROOT / f"{name}.py").exists() for name in RETIRED_MODULES)


def test_schema_domain_dependencies_are_one_way_and_explicit() -> None:
    for module_name, allowed_dependencies in DOMAIN_MODULES.items():
        dependencies = _direct_schema_imports(SCHEMA_ROOT / f"{module_name}.py")
        assert dependencies <= allowed_dependencies, (
            f"{module_name} imports unexpected schema domains: "
            f"{sorted(dependencies - allowed_dependencies)}"
        )


def test_repository_has_no_import_consumers_of_retired_schema_modules() -> None:
    retired_imports = {f"xpotato_sim.schemas.{name}" for name in RETIRED_MODULES}
    for root in (ROOT / "src", ROOT / "tests", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            }
            imported.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            assert imported.isdisjoint(retired_imports), path.relative_to(ROOT)


def test_schema_package_public_surface_is_explicit_and_canonical() -> None:
    assert len(schemas.__all__) == len(set(schemas.__all__))
    assert all(hasattr(schemas, name) for name in schemas.__all__)
    assert schemas.RawInputFrame.__module__ == "xpotato_sim.schemas.input"
    assert schemas.InputIntent.__module__ == "xpotato_sim.schemas.input"
    assert schemas.MotionCommand.__module__ == "xpotato_sim.schemas.command"
    assert schemas.MuJoCoState.__module__ == "xpotato_sim.schemas.state"
    assert schemas.EndpointMetadata.__module__ == "xpotato_sim.schemas.endpoint"
    assert schemas.ViewerControlMessage.__module__ == "xpotato_sim.schemas.viewer_control"
    assert schemas.ConfigurationRecord.__module__ == "xpotato_sim.schemas.experiment_log"
