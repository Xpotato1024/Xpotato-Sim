from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import xpotato_sim.runtime as runtime
from xpotato_sim.plugins.robots.catalog import (
    resolve_robot_bundle,
    resolve_robot_profile,
    resolve_robot_runtime_plugin,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import FAST_ARM_ROBOT_BUNDLE
from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "xpotato_sim"
FAST_ARM_PLUGIN_ROOT = SRC / "plugins" / "robots" / "fast_arm"
FAST_ARM_PACKAGE = "xpotato_sim.plugins.robots.fast_arm"
FAST_ARM_PHYSICAL_OUTPUT_MODULE = f"{FAST_ARM_PACKAGE}.adapter.physical_output"
FAST_ARM_RUNTIME_COMPOSITION_OWNER = SRC / "runtime" / "output" / "fast_arm_adapter.py"
FAST_ARM_RUNTIME_COMPOSITION_IMPORTS = frozenset(
    {
        FAST_ARM_PHYSICAL_OUTPUT_MODULE,
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.FAST_ARM_JOINT_POSITION_SEMANTICS",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.FastArmJointWireCommand",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.FastArmOutputMapping",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.build_fast_arm_joint_wire_command",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.parse_fast_arm_router_observation",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.router_observation_matches",
    }
)
FAST_ARM_NO_IO_COMPOSITION_IMPORTS = {
    SRC / "runtime" / "composition" / "fast_arm_coordinated.py": frozenset({
        f"{FAST_ARM_PACKAGE}.adapter.coordinated",
        f"{FAST_ARM_PACKAGE}.adapter.coordinated.FastArmAssemblyMotionProvider",
    }),
    SRC / "runtime" / "output" / "fast_arm_observation.py": frozenset({
        FAST_ARM_PHYSICAL_OUTPUT_MODULE,
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.FastArmJointWireCommand",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.parse_fast_arm_router_observation",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.router_observation_matches",
    }),
    SRC / "runtime" / "output" / "fast_arm_emulation.py": frozenset({
        FAST_ARM_PHYSICAL_OUTPUT_MODULE,
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.FastArmJointWireCommand",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.FastArmOutputMapping",
        f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.build_fast_arm_joint_wire_command",
    }),
}
REMOVED_MODULE_PATHS = (
    SRC / "robot_profile.py",
    SRC / "viewer_robot_declaration.py",
    SRC / "loadcell_serial.py",
    SRC / "robot_registry.py",
    SRC / "robots" / "fast_arm.py",
    SRC / "kinematics" / "fk.py",
    SRC / "kinematics" / "ik.py",
    SRC / "kinematics" / "fast_arm_endpoint.py",
    SRC / "mujoco_backend" / "fast_arm_compat.py",
    SRC / "runtime" / "default_robot_providers.py",
    SRC / "runtime" / "fast_arm_bundle.py",
    SRC / "runtime" / "fast_arm_joint_limits.py",
    SRC / "runtime" / "fast_arm_plugin.py",
    SRC / "runtime" / "robot_bundle_registry.py",
    SRC / "runtime" / "robot_plugin_registry.py",
    SRC / "runtime" / "endpoint_motion_sanity.py",
    SRC / "runtime" / "jacobian_mobility_diagnostics.py",
    SRC / "runtime" / "neutral_initial_pose.py",
    SRC / "runtime" / "mujoco_pipeline.py",
)
REMOVED_IMPORT_MODULES = frozenset(
    str(path.relative_to(SRC.parent)).removesuffix(".py").replace("\\", ".").replace("/", ".")
    for path in REMOVED_MODULE_PATHS
)


def _package_for_path(path: Path) -> str | None:
    try:
        relative = path.relative_to(SRC.parent).with_suffix("")
    except ValueError:
        return None
    package = ".".join(relative.parent.parts)
    return package or None


def _imports_from_source(source: str, *, filename: str, package: str | None) -> set[str]:
    tree = ast.parse(source, filename=filename)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                if package is None:
                    continue
                module = resolve_name(f"{'.' * node.level}{module}", package)
            if module:
                imported.add(module)
                imported.update(
                    f"{module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
    return imported


def _imports(path: Path) -> set[str]:
    return _imports_from_source(
        path.read_text(encoding="utf-8"),
        filename=str(path),
        package=_package_for_path(path),
    )


def _is_permitted_concrete_fast_arm_import(path: Path, imported: str) -> bool:
    if imported != FAST_ARM_PACKAGE and not imported.startswith(f"{FAST_ARM_PACKAGE}."):
        return True
    return (
        path == FAST_ARM_RUNTIME_COMPOSITION_OWNER
        and imported in FAST_ARM_RUNTIME_COMPOSITION_IMPORTS
    ) or imported in FAST_ARM_NO_IO_COMPOSITION_IMPORTS.get(path, frozenset())


def test_package_for_path_uses_containing_package_for_modules_and_initializers() -> None:
    assert _package_for_path(SRC / "runtime" / "foo.py") == "xpotato_sim.runtime"
    assert _package_for_path(SRC / "plugins" / "robots" / "__init__.py") == (
        "xpotato_sim.plugins.robots"
    )


def test_relative_imports_resolve_from_a_normal_module_package() -> None:
    package = _package_for_path(SRC / "runtime" / "foo.py")
    imported = _imports_from_source(
        "from ..plugins.robots import fast_arm\n",
        filename="src/xpotato_sim/runtime/foo.py",
        package=package,
    )
    assert "xpotato_sim.plugins.robots.fast_arm" in imported


def test_relative_imports_resolve_from_a_package_initializer() -> None:
    package = _package_for_path(SRC / "plugins" / "robots" / "__init__.py")
    imported = _imports_from_source(
        "from . import fast_arm\n",
        filename="src/xpotato_sim/plugins/robots/__init__.py",
        package=package,
    )
    assert "xpotato_sim.plugins.robots.fast_arm" in imported


def test_relative_removed_compatibility_import_is_detected() -> None:
    package = _package_for_path(SRC / "runtime" / "__init__.py")
    imported = _imports_from_source(
        "from . import fast_arm_plugin\n",
        filename="src/xpotato_sim/runtime/__init__.py",
        package=package,
    )
    assert "xpotato_sim.runtime.fast_arm_plugin" in imported
    assert imported & REMOVED_IMPORT_MODULES


def test_fast_arm_runtime_composition_import_exception_is_exact() -> None:
    owner = FAST_ARM_RUNTIME_COMPOSITION_OWNER
    assert _is_permitted_concrete_fast_arm_import(owner, FAST_ARM_PHYSICAL_OUTPUT_MODULE)
    for imported in FAST_ARM_RUNTIME_COMPOSITION_IMPORTS - {FAST_ARM_PHYSICAL_OUTPUT_MODULE}:
        assert _is_permitted_concrete_fast_arm_import(owner, imported)

    assert not _is_permitted_concrete_fast_arm_import(owner, FAST_ARM_PACKAGE)
    assert not _is_permitted_concrete_fast_arm_import(
        owner, f"{FAST_ARM_PACKAGE}.adapter.bundle"
    )
    assert not _is_permitted_concrete_fast_arm_import(
        SRC / "runtime" / "output" / "transport_adapter.py", FAST_ARM_PHYSICAL_OUTPUT_MODULE
    )


def test_concrete_fast_arm_imports_are_limited_to_plugin_and_exact_runtime_owner() -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.is_relative_to(FAST_ARM_PLUGIN_ROOT):
            continue
        for imported in sorted(_imports(path)):
            if not _is_permitted_concrete_fast_arm_import(path, imported):
                violations.append(f"{path.relative_to(ROOT)}: {imported}")
    assert not violations, "concrete fast_arm import outside plugin owner:\n" + "\n".join(
        violations
    )


def test_plugin_discovery_does_not_eagerly_load_diagnostics() -> None:
    code = (
        "import sys;sys.path.insert(0, 'src');"
        "from xpotato_sim.plugins.robots.discovery import discover_production_robot_plugins;"
        "discover_production_robot_plugins();"
        "assert not any('.diagnostics' in name for name in sys.modules), "
        "sorted(name for name in sys.modules if '.diagnostics' in name)"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_removed_facade_and_compatibility_modules_cannot_reappear() -> None:
    assert all(not path.exists() for path in REMOVED_MODULE_PATHS)


def test_package_root_and_robot_namespace_keep_canonical_ownership() -> None:
    assert {path.name for path in SRC.glob("*.py")} == {"__init__.py"}
    assert not tuple((SRC / "robots").glob("*.py"))
    assert (SRC / "runtime" / "composition" / "robot_profile.py").is_file()
    assert (SRC / "runtime" / "composition" / "viewer_robot_declaration.py").is_file()
    assert (
        SRC / "plugins" / "input_sources" / "selfrionette" / "protocol.py"
    ).is_file()


def test_repository_has_no_import_consumers_of_removed_modules() -> None:
    roots = (SRC, ROOT / "tests", ROOT / "scripts")
    for root in roots:
        for path in root.rglob("*.py"):
            assert _imports(path).isdisjoint(REMOVED_IMPORT_MODULES), path.relative_to(ROOT)


def test_production_source_contains_no_test_double_modules_or_test_imports() -> None:
    assert not tuple(SRC.rglob("stubs.py"))
    for path in SRC.rglob("*.py"):
        assert not any(name == "tests" or name.startswith("tests.") for name in _imports(path))


def test_generic_kinematics_and_mujoco_packages_are_robot_independent() -> None:
    assert {path.name for path in (SRC / "kinematics").glob("*.py")} == {"__init__.py", "base.py"}
    for path in (SRC / "mujoco_backend").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        strings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not any("FAST_ARM" in name for name in names), path.relative_to(ROOT)
        assert not any("default_fast_arm" in value for value in strings), path.relative_to(ROOT)


def test_diagnostic_scripts_import_plugin_owned_entrypoints() -> None:
    for path in (ROOT / "scripts" / "diagnostics" / "fast_arm").glob("run_fast_arm_*diagnostic*.py"):
        imported = _imports(path)
        assert any(
            name.startswith("xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics")
            for name in imported
        ), path.relative_to(ROOT)
        assert not any(name.startswith("xpotato_sim.runtime.endpoint_motion_sanity") for name in imported)


def test_public_packages_export_no_test_doubles_or_fast_arm_generic_symbols() -> None:
    for module_name in (
        "xpotato_sim.kinematics",
        "xpotato_sim.motion",
        "xpotato_sim.mujoco_backend",
        "xpotato_sim.transport",
        "xpotato_sim.runtime",
    ):
        module = importlib.import_module(module_name)
        assert not any(name.startswith(("NoOp", "Zero", "Static")) for name in module.__all__)
    assert set(runtime._PUBLIC_EXPORTS) == set(runtime.__all__)
    assert not any("FastArm" in name for name in importlib.import_module("xpotato_sim.kinematics").__all__)
    assert not any("FAST_ARM" in name for name in importlib.import_module("xpotato_sim.mujoco_backend").__all__)


def test_runtime_root_retains_only_deliberate_catalog_apis() -> None:
    retained_catalog_apis = {
        name
        for name, (owner, _) in runtime._PUBLIC_EXPORTS.items()
        if owner == "xpotato_sim.plugins.robots.catalog"
    }
    assert retained_catalog_apis == {
        "registered_robot_bundle_ids",
        "registered_robot_runtime_plugin_ids",
        "resolve_robot_bundle",
        "resolve_robot_runtime",
        "resolve_robot_runtime_plugin",
    }
    assert "resolve_robot_profile" not in runtime.__all__


def test_catalog_registration_bundle_profile_and_runtime_identity_is_unchanged() -> None:
    bundle = resolve_robot_bundle("fast_arm")
    assert bundle is FAST_ARM_ROBOT_BUNDLE
    assert ROBOT_PLUGIN.bundle is bundle
    assert resolve_robot_profile("fast_arm") is bundle.profile
    assert resolve_robot_runtime_plugin("fast_arm") is bundle.runtime_plugin



def test_no_io_concrete_import_exceptions_are_exact_and_not_transitive():
    """新ownerでも許可したpure symbols以外を機種依存importにしない。"""
    for owner, allowed in FAST_ARM_NO_IO_COMPOSITION_IMPORTS.items():
        for imported in allowed:
            assert _is_permitted_concrete_fast_arm_import(owner, imported)
        assert not _is_permitted_concrete_fast_arm_import(owner, FAST_ARM_PACKAGE)
        assert not _is_permitted_concrete_fast_arm_import(owner, f"{FAST_ARM_PACKAGE}.adapter.bundle")
        assert not _is_permitted_concrete_fast_arm_import(owner, f"{FAST_ARM_PHYSICAL_OUTPUT_MODULE}.unknown")
        assert not _is_permitted_concrete_fast_arm_import(owner.with_name("other.py"), FAST_ARM_PHYSICAL_OUTPUT_MODULE)
