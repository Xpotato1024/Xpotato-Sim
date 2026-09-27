from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PERMISSION_PATH = ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "permission.py"
TRACE_PATH = ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "trace.py"
LIFECYCLE_PATH = ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "lifecycle.py"
SAFETY_GATE_PATH = ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "safety_gate.py"
FAST_ARM_RUNTIME_PATH = ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "fast_arm_adapter.py"
FAST_ARM_PLUGIN_PATH = (
    ROOT / "src" / "xpotato_sim" / "plugins" / "robots" / "fast_arm" / "adapter" / "physical_output.py"
)
TRANSPORT_ADAPTER_PATH = (
    ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "transport_adapter.py"
)
OUTPUT_PACKAGE_PATH = ROOT / "src" / "xpotato_sim" / "runtime" / "output" / "__init__.py"
FORBIDDEN_IMPORT_ROOTS = {
    "mujoco",
    "osc4py3",
    "pythonosc",
    "serial",
    "socket",
    "usb",
}
FORBIDDEN_SELF_RIONETTE_IMPORTS = (
    "xpotato_sim.plugins.robots",
    "xpotato_sim.runtime.execution",
    "xpotato_sim.runtime.runners",
    "xpotato_sim.transport",
)


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_permission_evaluator_has_no_transport_or_hardware_imports() -> None:
    tree = ast.parse(
        PERMISSION_PATH.read_text(encoding="utf-8"),
        filename=str(PERMISSION_PATH),
    )
    imported = _imported_modules(tree)
    assert {
        module.split(".", maxsplit=1)[0]
        for module in imported
    }.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
    assert all(
        not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_SELF_RIONETTE_IMPORTS
        )
        for module in imported
    )

    calls = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint({"connect", "send", "write"})


def test_trace_owner_has_no_transport_or_hardware_imports() -> None:
    tree = ast.parse(TRACE_PATH.read_text(encoding="utf-8"), filename=str(TRACE_PATH))
    imported = _imported_modules(tree)
    assert {
        module.split(".", maxsplit=1)[0]
        for module in imported
    }.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
    assert all(
        not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_SELF_RIONETTE_IMPORTS
        )
        for module in imported
    )


def test_lifecycle_owner_has_no_transport_or_hardware_imports() -> None:
    tree = ast.parse(
        LIFECYCLE_PATH.read_text(encoding="utf-8"),
        filename=str(LIFECYCLE_PATH),
    )
    imported = _imported_modules(tree)
    assert {
        module.split(".", maxsplit=1)[0]
        for module in imported
    }.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
    assert all(
        not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_SELF_RIONETTE_IMPORTS
        )
        for module in imported
    )


def test_safety_gate_uses_the_canonical_p5_evaluator_without_transport() -> None:
    tree = ast.parse(
        SAFETY_GATE_PATH.read_text(encoding="utf-8"),
        filename=str(SAFETY_GATE_PATH),
    )
    imported = _imported_modules(tree)
    assert {
        module.split(".", maxsplit=1)[0]
        for module in imported
    }.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
    assert all(
        not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_SELF_RIONETTE_IMPORTS
        )
        for module in imported
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "evaluate_physical_safety"
        for node in ast.walk(tree)
    )


def test_transport_adapter_is_the_output_composition_transport_owner() -> None:
    tree = ast.parse(
        TRANSPORT_ADAPTER_PATH.read_text(encoding="utf-8"),
        filename=str(TRANSPORT_ADAPTER_PATH),
    )
    imported = _imported_modules(tree)
    assert "xpotato_sim.transport.endpoint" in imported
    assert "xpotato_sim.transport.osc" in imported
    assert "xpotato_sim.transport.udp" in imported
    assert all(
        not module.startswith("xpotato_sim.plugins.robots")
        for module in imported
    )

    package_tree = ast.parse(
        OUTPUT_PACKAGE_PATH.read_text(encoding="utf-8"),
        filename=str(OUTPUT_PACKAGE_PATH),
    )
    package_imports = _imported_modules(package_tree)
    assert all("transport_adapter" not in module for module in package_imports)


def test_fast_arm_mapping_and_runtime_output_composition_keep_layer_ownership() -> None:
    plugin_tree = ast.parse(
        FAST_ARM_PLUGIN_PATH.read_text(encoding="utf-8"),
        filename=str(FAST_ARM_PLUGIN_PATH),
    )
    plugin_imports = _imported_modules(plugin_tree)
    assert "xpotato_sim.schemas.command" in plugin_imports
    assert all(
        not module.startswith((
            "xpotato_sim.runtime",
            "xpotato_sim.transport",
        ))
        for module in plugin_imports
    )

    runtime_tree = ast.parse(
        FAST_ARM_RUNTIME_PATH.read_text(encoding="utf-8"),
        filename=str(FAST_ARM_RUNTIME_PATH),
    )
    runtime_imports = _imported_modules(runtime_tree)
    assert "xpotato_sim.plugins.robots.fast_arm.adapter.physical_output" in runtime_imports
    assert "xpotato_sim.runtime.output.transport_adapter" in runtime_imports
    assert "xpotato_sim.runtime.output.lifecycle" in runtime_imports
    assert "xpotato_sim.runtime.output.safety_gate" in runtime_imports
    assert "xpotato_sim.transport.osc" in runtime_imports



def test_no_io_fast_arm_owners_cannot_import_transmission_or_authorization():
    """P3の新ownerはpure byte処理に限定し、実機sessionや許可を構築させない。"""
    for name in ("fast_arm_observation.py", "fast_arm_emulation.py"):
        path = FAST_ARM_RUNTIME_PATH.with_name(name)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = _imported_modules(tree)
        assert {item.split(".")[0] for item in modules}.isdisjoint(FORBIDDEN_IMPORT_ROOTS | {"threading", "asyncio"})
        assert all(not item.startswith((
            "xpotato_sim.transport.udp", "xpotato_sim.runtime.output.fast_arm_adapter",
            "xpotato_sim.runtime.output.permission", "xpotato_sim.runtime.output.safety_gate",
            "xpotato_sim.runtime.output.transport_adapter", "xpotato_sim.runtime.safety",
        )) for item in modules)
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)}
        assert calls.isdisjoint({"send", "sendto", "connect", "bind", "arm", "dispatch", "submit_physical"})
