from __future__ import annotations

import ast
import json
from pathlib import Path

from xpotato_sim.runtime.runners.dry_run import run_replay_mujoco_dry_run


ROOT = Path(__file__).resolve().parents[2]
DRY_RUN_MODULE = ROOT / "src" / "xpotato_sim" / "runtime" / "runners" / "dry_run.py"


def _assert_endpoint_evaluation(payload: dict[str, object]) -> None:
    endpoint_evaluation = payload["endpoint_evaluation"]
    assert isinstance(endpoint_evaluation, dict)
    assert endpoint_evaluation["unit"] == "meter"
    assert endpoint_evaluation["desired_endpoint_coordinate_frame"] == "command-side endpoint frame"
    assert endpoint_evaluation["fk_endpoint_coordinate_frame"] == "solver-defined frame"
    assert endpoint_evaluation["site_endpoint_coordinate_frame"] == "MuJoCo world / scene frame"
    assert len(endpoint_evaluation["desired_endpoint_m"]) == 3
    assert len(endpoint_evaluation["qpos_like_joint_angles_rad"]) >= 2


def test_dry_run_sweep_x_uses_programmed_input_source_metadata() -> None:
    payload = json.loads(run_replay_mujoco_dry_run(steps=1, preset="sweep_x")[0])

    assert payload["metadata"]["source_kind"] == "programmed_target"
    assert payload["metadata"]["trajectory_name"] == "sweep_x"
    assert payload["metadata"]["phase"] == "initial_hold"
    assert payload["metadata"]["preset"] == "sweep_x"
    assert payload["metadata"]["desired_endpoint_m"] == payload["target_position_m"]
    _assert_endpoint_evaluation(payload)


def test_dry_run_module_uses_programmed_input_source_and_not_noop_motion_generator() -> None:
    source_text = DRY_RUN_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(DRY_RUN_MODULE))

    assert "build_sweep_x_input_source" in source_text
    assert "NoOpMotionGenerator" not in source_text

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "xpotato_sim.plugins.input_sources.programmed_target"
        ):
            imported_names.update(alias.name for alias in node.names)

    assert "build_sweep_x_input_source" in imported_names
