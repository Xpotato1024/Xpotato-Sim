from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_frontend_keyboard_capture_exposes_only_key_allowlist_semantics() -> None:
    source = _text("apps/mujoco-viewer/src/input/keyboardInput.ts")
    assert "ViewerKeyboardBindingAxis" not in source
    assert "ViewerKeyboardBindingDirection" not in source
    assert "DEFAULT_VIEWER_KEYBOARD_CAPTURE_KEYS" in source


def test_backend_viewer_source_has_no_control_mapping_import_or_algorithm() -> None:
    path = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "plugins"
        / "input_sources"
        / "viewer"
        / "source.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert all(not module.startswith("xpotato_sim.plugins.mappings") for module in imported_modules)
    assert "build_continuous_endpoint_velocity_intent" not in source
    assert "build_keyboard_continuous_velocity_intent" not in source


def test_mapping_does_not_read_legacy_viewer_control_summary() -> None:
    path = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "plugins"
        / "mappings"
        / "viewer_keyboard_gamepad_mapping"
        / "implementation.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value != "viewer_control_message", ast.unparse(node)


def test_input_source_registration_has_no_cross_axis_mapping_policy() -> None:
    source = _text(
        "src/xpotato_sim/plugins/input_sources/viewer/plugin.py"
    )
    assert "ControlMappingPlugin" not in source
    assert "VIEWER_CONTROL_MAPPING_PLUGIN" not in source
    assert "PluginSelection(" not in source
    assert "viewer_keyboard_gamepad_mapping" not in source
    policy = _text(
        "src/xpotato_sim/runtime/control/input_source_mapping_policy.py"
    )
    assert '"viewer": PluginSelection("viewer_keyboard_gamepad_mapping", 1)' in policy
