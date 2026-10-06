from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / "apps/mujoco-viewer/src/wasm-scene"


def test_viewer_pose_updates_use_only_native_display_kinematics() -> None:
    forbidden = re.compile(
        r"\b(?:mj_forward(?:Skip)?|mj_fwdPosition|mj_fwdVelocity|mj_fwdActuation|"
        r"mj_fwdAcceleration|mj_fwdConstraint|mj_collision|mj_makeConstraint|"
        r"mj_projectConstraint|mj_step[12]?)\s*\("
    )
    for path in VIEWER.glob("*.ts"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name

    renderer = (VIEWER / "mujocoSceneRenderer.ts").read_text(encoding="utf-8")
    assert "applyMujocoDisplayPose(mujocoApi, model, data, qpos)" in renderer
    assert "applyMujocoDisplayPose(mujocoApi, model, data, startupQpos)" in renderer
    assert "createMujocoDisplayOption(mujocoApi)" in renderer
    assert "data.qpos.set(" not in renderer
    helper = (VIEWER / "mujocoDisplayPose.ts").read_text(encoding="utf-8")
    assert "api.mj_fwdKinematics(model, data)" in helper
    assert "catch" not in helper


def test_viewer_uses_installed_mujoco_binding_types() -> None:
    declarations = (
        ROOT / "apps/mujoco-viewer/src/types/three-minimal.d.ts"
    ).read_text(encoding="utf-8")
    assert 'declare module "@mujoco/mujoco"' not in declarations
