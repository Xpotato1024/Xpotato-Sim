from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src" / "xpotato_sim"

FORBIDDEN_IMPORTS = {
    Path("plugins/input_sources"): [
        "xpotato_sim.plugins.mappings",
        "xpotato_sim.motion",
        "xpotato_sim.kinematics",
        "xpotato_sim.mujoco_backend",
        "xpotato_sim.transport",
    ],
    Path("plugins/mappings"): [
        "xpotato_sim.plugins.input_sources",
        "xpotato_sim.motion",
        "xpotato_sim.kinematics",
        "xpotato_sim.mujoco_backend",
        "xpotato_sim.transport",
    ],
    Path("motion"): [
        "xpotato_sim.plugins.input_sources",
        "xpotato_sim.plugins.mappings",
        "xpotato_sim.mujoco_backend",
        "xpotato_sim.transport",
        "xpotato_sim.runtime",
    ],
    Path("kinematics"): [
        "xpotato_sim.plugins.input_sources",
        "xpotato_sim.plugins.mappings",
        "xpotato_sim.mujoco_backend",
        "xpotato_sim.transport",
        "xpotato_sim.runtime",
    ],
    Path("mujoco_backend"): [
        "xpotato_sim.plugins.input_sources",
        "xpotato_sim.plugins.mappings",
        "xpotato_sim.motion",
        "xpotato_sim.transport",
        "xpotato_sim.runtime",
    ],
    Path("transport"): [
        "xpotato_sim.plugins.input_sources",
        "xpotato_sim.plugins.mappings",
        "xpotato_sim.motion",
        "xpotato_sim.kinematics",
        "xpotato_sim.mujoco_backend",
        "xpotato_sim.runtime",
    ],
}


def iter_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    return imports


def test_import_boundaries() -> None:
    violations: list[str] = []

    for layer_path, forbidden_prefixes in FORBIDDEN_IMPORTS.items():
        for path in (SRC_ROOT / layer_path).rglob("*.py"):
            for imported in iter_imports(path):
                for forbidden in forbidden_prefixes:
                    if imported.startswith(forbidden):
                        violations.append(
                            f"{path.relative_to(ROOT)} imports {imported}; "
                            f"forbidden prefix: {forbidden}"
                        )

    assert not violations, "\n".join(violations)
