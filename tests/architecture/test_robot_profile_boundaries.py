from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_generic_runtime_files_do_not_import_fast_arm_implementation() -> None:
    runtime = ROOT / "src" / "xpotato_sim" / "runtime"
    for name in (
        "composition/config.py",
        "execution/pipeline.py",
        "composition/replay_mujoco_pipeline.py",
        "safety/qpos_feasibility.py",
        "composition/robot_plugin.py",
    ):
        source = (runtime / name).read_text(encoding="utf-8")
        assert "fast_arm" not in source.lower(), name
        assert "FastArm" not in source, name
        if name.endswith("replay_mujoco_pipeline.py"):
            assert "resolve_robot_runtime" not in source, name

    simulator_source = (
        ROOT / "src" / "xpotato_sim" / "mujoco_backend" / "simulator.py"
    ).read_text(encoding="utf-8")
    assert "xpotato_sim.plugins.robots.fast_arm.adapter.profile" not in simulator_source
    assert "default_fast_arm_scene_path" not in simulator_source
    assert "FAST_ARM_ROBOT_PROFILE.initial_keyframe_name" not in simulator_source


def test_generic_viewer_renderer_and_qpos_sync_do_not_embed_fast_arm() -> None:
    viewer = ROOT / "apps" / "mujoco-viewer" / "src" / "wasm-scene"
    for name in ("mujocoSceneRenderer.ts", "mujocoQposSync.ts", "visualStyles.ts"):
        source = (viewer / name).read_text(encoding="utf-8")
        assert "fast_arm" not in source.lower(), name


def test_profile_registries_do_not_use_arbitrary_dynamic_imports() -> None:
    paths = (
        ROOT / "src" / "xpotato_sim" / "plugins" / "robots" / "catalog.py",
        ROOT / "apps" / "mujoco-viewer" / "src" / "robot-profiles" / "registry.ts",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "importlib" not in source
        assert "__import__" not in source
        assert "import(" not in source


def test_runtime_package_root_exports_resolvers_not_plugin_classes() -> None:
    import xpotato_sim.runtime as runtime

    assert "RobotRuntimePlugin" not in runtime.__all__
    assert "ResolvedRobotRuntime" not in runtime.__all__
    assert "resolve_robot_runtime" in runtime.__all__
    assert "resolve_robot_runtime_plugin" in runtime.__all__
    assert "FastArmRuntimePlugin" not in runtime.__all__
    assert not hasattr(runtime, "FastArmRuntimePlugin")


def test_viewer_profile_does_not_claim_an_unused_mesh_fallback_contract() -> None:
    profile_sources = (
        ROOT / "apps" / "mujoco-viewer" / "src" / "robot-profiles" / "types.ts",
        ROOT / "apps" / "mujoco-viewer" / "src" / "robot-profiles" / "fastArm.ts",
    )
    for path in profile_sources:
        assert "meshFallbackUrls" not in path.read_text(encoding="utf-8")

    contract = (
        ROOT / "docs" / "contracts" / "robot-profile-runtime-viewer-profile.md"
    ).read_text(encoding="utf-8")
    normalized_contract = " ".join(contract.split())
    assert "selects Option B" in normalized_contract
    assert "does not declare an unused fallback mapping" in normalized_contract


def test_fast_arm_viewer_registry_is_a_single_sot_compatibility_facade() -> None:
    viewer_root = ROOT / "apps" / "mujoco-viewer" / "src"
    fast_arm_facade = (viewer_root / "robot-profiles" / "fastArm.ts").read_text(
        encoding="utf-8"
    )
    registry = (viewer_root / "robot-profiles" / "registry.ts").read_text(
        encoding="utf-8"
    )
    app = (viewer_root / "app" / "ProductViewerApp.tsx").read_text(
        encoding="utf-8"
    )

    assert "viewer-profile.json" in fast_arm_facade
    for duplicated_field in (
        "modelContractVersion",
        "initialKeyframeName",
        "jointNames",
        "qposDimension",
        "bodyVisualStyles",
    ):
        assert duplicated_field not in fast_arm_facade
    assert '"fast_arm"' not in registry
    assert '"fast_arm"' not in app
    assert "loadViewerRobotProfileFromPayload" in (
        viewer_root / "wasm-scene" / "mujocoSceneRenderer.ts"
    ).read_text(encoding="utf-8")
