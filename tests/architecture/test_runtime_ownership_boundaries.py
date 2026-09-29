from __future__ import annotations

import ast
from pathlib import Path

import xpotato_sim.runtime as runtime
import xpotato_sim.runtime.safety.collision_policy as collision_policy


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "src" / "xpotato_sim" / "runtime"
EXPECTED_MODULES = {
    "scene": {"composition", "contracts", "objects", "observation", "measurement", "task", "world", "world_composition", "dynamics_observation"},
    "composition": {
        "fast_arm_coordinated",
        "robot_model",
        "coordinated_input",
        "launch_profile",
        "config",
        "concrete_mujoco_pipeline",
        "production_experiment",
        "replay_mujoco_pipeline",
        "robot_bundle",
        "robot_plugin",
        "robot_profile",
        "robot_profile_metadata",
        "robot_resource",
        "robot_provider_adapters",
        "robot_resolution",
        "viewer_robot_declaration",
        "viewer_package_resource_manifest",
    },
    "contact": {
        "evidence",
        "log",
        "manifest",
        "presentation",
        "scene",
        "task_contract",
        "robot_view",
        "virtual_reaction_force",
    },
    "control": {
        "desired_endpoint_resolver",
        "endpoint_target_generator",
        "input_source_selection",
        "input_source_mapping_policy",
        "input_source_state",
        "input_step_diagnostics",
        "viewer_control_ingress",
        "viewer_motion_policy",
    },
    "evaluation": {
        "artifact",
        "endpoint_metrics",
        "endpoint_progress",
        "kinematics",
        "manifest",
        "r7_g_free_space",
    },
    "execution": {
        "physics",
        "coordinated",
        "command_routes",
        "input_step_loop",
        "input_source_adapters",
        "live_timing",
        "pipeline",
    },
    "experiment": {
        "composition",
        "contracts",
        "endpoint_reach_evidence",
        "input_source",
        "measured_state",
        "motion_log_recorder",
        "registry",
        "r7_g_e2e",
        "world_tool_runner",
    },
    "runners": {
        "coordinated_gamepad",
        "model_websocket_publisher",
        "application",
        "application_process",
        "fast_arm_input_runtime",
        "dry_run",
        "live_selfrionette",
        "live_viewer_smoke",
        "live_websocket_delivery",
        "offline_input_smoke",
        "r7_g_world_tool_experiment",
        "selfrionette_serial_dry_run",
        "signal_contact",
        "signal_contact_artifact",
        "websocket_publisher",
    },
    "output": {
        "coordinated",
        "fast_arm_adapter",
        "fast_arm_observation",
        "fast_arm_emulation",
        "lifecycle",
        "permission",
        "safety_gate",
        "trace",
        "transport_adapter",
    },
    "safety": {
        "evaluated_candidate",
        "collision_policy",
        "input_safety",
        "limit_resolution",
        "operator_validation",
        "physical_limits",
        "physical_safety_core",
        "qpos_feasibility",
        "trajectory_feasibility",
    },
}
RETIRED_FLAT_MODULES = (frozenset().union(*EXPECTED_MODULES.values()) - set(EXPECTED_MODULES)) | {
    "evaluation_manifest",
    "experiment_composition",
    "experiment_contracts",
    "experiment_registry",
    "live_loadcell",
    "live_loadcell_runtime_runner",
    "loadcell_serial_dry_run",
    "offline_input_runtime_smoke",
    "websocket_publisher_runner",
}


def _imports(path: Path) -> set[str]:
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
    return imported


def test_runtime_modules_have_one_responsibility_owner() -> None:
    assert {path.stem for path in RUNTIME_ROOT.glob("*.py")} == {"__init__"}
    assert {
        path.name
        for path in RUNTIME_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    } == set(EXPECTED_MODULES)
    for owner, expected_modules in EXPECTED_MODULES.items():
        actual_modules = {path.stem for path in (RUNTIME_ROOT / owner).glob("*.py")}
        assert actual_modules == {"__init__", *expected_modules}


def test_retired_flat_runtime_imports_have_no_repository_consumers() -> None:
    retired_imports = {f"xpotato_sim.runtime.{name}" for name in RETIRED_FLAT_MODULES}
    for root in (ROOT / "src", ROOT / "tests", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            assert _imports(path).isdisjoint(retired_imports), path.relative_to(ROOT)


def test_runtime_package_root_is_a_minimal_lazy_facade() -> None:
    assert set(runtime.__all__) == {
        "RuntimeConfig",
        "registered_robot_bundle_ids",
        "registered_robot_runtime_plugin_ids",
        "resolve_robot_bundle",
        "resolve_robot_runtime",
        "resolve_robot_runtime_plugin",
    }
    assert set(runtime._PUBLIC_EXPORTS) == set(runtime.__all__)


def test_collision_safety_exposes_one_canonical_context_type() -> None:
    assert collision_policy.__all__.count("CollisionContext") == 1
    assert "CollisionContextIdentity" not in collision_policy.__all__
    assert not hasattr(collision_policy, "CollisionContextIdentity")


def test_experiment_runner_has_one_owner_and_thin_entry_point() -> None:
    readme = (RUNTIME_ROOT / "README.md").read_text(encoding="utf-8")
    assert "experiment lifecycle / runnerは`experiment/`が所有する" in readme
    assert "`runners/`はthin entry point" in readme


def test_production_experiment_runtime_has_no_concrete_robot_or_test_fixture_import() -> None:
    for name in ("world_tool_runner.py", "motion_log_recorder.py"):
        source = (RUNTIME_ROOT / "experiment" / name).read_text(encoding="utf-8")
        for forbidden in (
            "xpotato_sim.plugins.robots.fast_arm",
            "FAST_ARM_",
            "tests.",
            "ExperimentPluginRegistries(",
        ):
            assert forbidden not in source, name
