from __future__ import annotations

import json
import math

import mujoco
import pytest

import xpotato_sim.runtime.runners.dry_run as dry_run_module
from xpotato_sim.mujoco_backend import HeadlessMuJoCoSimulator
from xpotato_sim.runtime.runners.dry_run import run_replay_mujoco_dry_run
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from generic_qpos_test_doubles import RejectingGenericQposGuard


def _assert_endpoint_evaluation(payload: dict[str, object]) -> None:
    endpoint_evaluation = payload["endpoint_evaluation"]
    assert isinstance(endpoint_evaluation, dict)
    assert endpoint_evaluation["unit"] == "meter"
    assert endpoint_evaluation["desired_endpoint_coordinate_frame"] == "command-side endpoint frame"
    assert endpoint_evaluation["fk_endpoint_coordinate_frame"] == "solver-defined frame"
    assert endpoint_evaluation["site_endpoint_coordinate_frame"] == "MuJoCo world / scene frame"
    assert "diagnostic only" in endpoint_evaluation["frame_mismatch_note"]
    assert len(endpoint_evaluation["desired_endpoint_m"]) == 3
    assert len(endpoint_evaluation["qpos_like_joint_angles_rad"]) >= 2
    assert len(endpoint_evaluation["fk_endpoint_m"]) == 3
    assert len(endpoint_evaluation["site_endpoint_m"]) == 3
    assert len(endpoint_evaluation["desired_to_fk_error_vector_m"]) == 3
    assert len(endpoint_evaluation["desired_to_site_error_vector_m"]) == 3
    assert len(endpoint_evaluation["fk_to_site_error_vector_m"]) == 3


def test_run_replay_mujoco_dry_run_returns_single_payload_line() -> None:
    lines = run_replay_mujoco_dry_run(steps=1)

    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["version"] == 0
    assert payload["frame_index"] == 1
    assert payload["time_s"] > 0.0
    assert payload["qpos"][:4] != [0.0, 0.0, 0.0, 0.0]
    assert payload["target_position_m"] is None
    _assert_endpoint_evaluation(payload)


def test_run_replay_mujoco_dry_run_emits_ndjson_for_multiple_steps() -> None:
    lines = run_replay_mujoco_dry_run(steps=3)

    assert len(lines) == 3

    frame_indices = [json.loads(line)["frame_index"] for line in lines]
    assert frame_indices == [1, 2, 3]


def test_run_replay_mujoco_dry_run_keeps_canonical_bodies_and_sites() -> None:
    payload = json.loads(run_replay_mujoco_dry_run(steps=1)[0])

    assert any(site["name"] == "tip" for site in payload["sites"])
    assert any(body["name"] == "base_link" for body in payload["bodies"])


def test_run_replay_mujoco_dry_run_rejects_invalid_steps() -> None:
    with pytest.raises(ValueError, match="steps must be a positive integer"):
        run_replay_mujoco_dry_run(steps=0)

    with pytest.raises(ValueError, match="steps must be a positive integer"):
        run_replay_mujoco_dry_run(steps=-1)


def test_run_replay_mujoco_dry_run_writes_ndjson_output_file(tmp_path) -> None:
    output_path = tmp_path / "payload.ndjson"

    lines = run_replay_mujoco_dry_run(steps=2, output=output_path)

    file_lines = output_path.read_text(encoding="utf-8").splitlines()
    assert file_lines == lines
    assert len(file_lines) == 2

    payloads = [json.loads(line) for line in file_lines]
    assert [payload["version"] for payload in payloads] == [0, 0]
    assert [payload["frame_index"] for payload in payloads] == [1, 2]


def test_run_replay_mujoco_dry_run_sweep_x_preset_keeps_delta_and_feedback_separate() -> None:
    lines = run_replay_mujoco_dry_run(steps=3, preset="sweep_x")

    assert len(lines) == 3

    payloads = [json.loads(line) for line in lines]
    assert [payload["metadata"]["preset"] for payload in payloads] == ["sweep_x", "sweep_x", "sweep_x"]
    assert [payload["metadata"]["source_kind"] for payload in payloads] == ["programmed_target"] * 3
    assert [payload["metadata"]["trajectory_name"] for payload in payloads] == ["sweep_x"] * 3
    assert [payload["metadata"]["phase"] for payload in payloads] == ["initial_hold", "initial_hold", "initial_hold"]

    for payload in payloads:
        assert payload["metadata"]["desired_endpoint_m"] == payload["target_position_m"]
        assert len(payload["metadata"]["target_position_m"]) == 3
        assert len(payload["qpos"]) >= 4
        _assert_endpoint_evaluation(payload)


def test_run_replay_mujoco_dry_run_sweep_x_preset_remains_visual_smoke_compatibility_path() -> None:
    lines = run_replay_mujoco_dry_run(steps=1, preset="sweep_x")

    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["metadata"]["preset"] == "sweep_x"
    assert payload["metadata"]["source_kind"] == "programmed_target"
    assert payload["metadata"]["trajectory_name"] == "sweep_x"
    assert payload["metadata"]["phase"] == "initial_hold"
    assert payload["metadata"]["desired_endpoint_m"] == payload["target_position_m"]
    assert len(payload["qpos"]) >= 4
    _assert_endpoint_evaluation(payload)


def test_sweep_x_real_path_preserves_time_qpos_and_mujoco_warning_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_step = HeadlessMuJoCoSimulator.step
    observations: list[dict[str, object]] = []
    bad_qacc_warning = int(mujoco.mjtWarning.mjWARN_BADQACC)

    def observe_step(simulator: HeadlessMuJoCoSimulator, dt_s: float) -> None:
        observation = {
            "model_id_before": id(simulator.model),
            "data_id_before": id(simulator.data),
            "time_before": float(simulator.data.time),
            "warning_before": int(simulator.data.warning.number[bad_qacc_warning]),
        }
        original_step(simulator, dt_s)
        observation.update(
            {
                "model_id_after": id(simulator.model),
                "data_id_after": id(simulator.data),
                "time_after": float(simulator.data.time),
                "warning_after": int(simulator.data.warning.number[bad_qacc_warning]),
            }
        )
        observations.append(observation)

    monkeypatch.setattr(HeadlessMuJoCoSimulator, "step", observe_step)
    payloads = [
        json.loads(line)
        for line in run_replay_mujoco_dry_run(steps=30, dt_s=1.0 / 60.0, preset="sweep_x")
    ]

    assert len(payloads) == 30
    assert len(observations) == 30
    assert [payload["frame_index"] for payload in payloads] == list(range(1, 31))
    times = [payload["time_s"] for payload in payloads]
    assert all(isinstance(time_s, (int, float)) and math.isfinite(time_s) for time_s in times)
    assert all(previous < current for previous, current in zip(times, times[1:]))
    assert times[-1] == pytest.approx(30.0 / 60.0)

    qpos_frames = [payload["qpos"][:4] for payload in payloads]
    assert all(len(qpos) == 4 for qpos in qpos_frames)
    assert all(math.isfinite(value) for qpos in qpos_frames for value in qpos)
    assert all(-math.pi <= value <= math.pi for qpos in qpos_frames for value in qpos)
    assert len({tuple(round(value, 9) for value in qpos) for qpos in qpos_frames[3:18]}) > 3
    assert qpos_frames[18:] == [qpos_frames[18]] * 12

    metadata = [payload["metadata"] for payload in payloads]
    assert [entry["frame_index"] for entry in metadata] == list(range(21)) + [20] * 9
    assert [entry["phase"] for entry in metadata[3:9]] == ["move_positive_x"] * 6
    assert [entry["phase"] for entry in metadata[12:18]] == ["return_to_initial"] * 6
    assert qpos_frames[3] != qpos_frames[8]
    assert qpos_frames[8] != qpos_frames[17]

    assert {entry["model_id_before"] for entry in observations} == {observations[0]["model_id_before"]}
    assert {entry["data_id_before"] for entry in observations} == {observations[0]["data_id_before"]}
    assert all(entry["model_id_before"] == entry["model_id_after"] for entry in observations)
    assert all(entry["data_id_before"] == entry["data_id_after"] for entry in observations)
    assert all(entry["warning_before"] == entry["warning_after"] for entry in observations)
    assert observations[-1]["warning_after"] == 0


def test_run_replay_mujoco_dry_run_uses_typed_rejection_without_fast_arm_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_builder = dry_run_module.build_concrete_mujoco_pipeline

    def build_rejecting_pipeline(*args, **kwargs):  # noqa: ANN002, ANN003
        pipeline = original_builder(*args, **kwargs)
        pipeline.qpos_feasibility_guard = RejectingGenericQposGuard()
        return pipeline

    monkeypatch.setattr(dry_run_module, "build_concrete_mujoco_pipeline", build_rejecting_pipeline)

    payload = json.loads(run_replay_mujoco_dry_run(steps=1, preset="sweep_x")[0])

    assert payload["target_position_m"] is None
    assert payload.get("endpoint_evaluation") is None
    assert "qpos_feasibility_rejected" not in payload["metadata"]
    assert "qpos_rejection_reason" not in payload["metadata"]


def test_run_replay_mujoco_dry_run_rejects_unknown_preset() -> None:
    with pytest.raises(ValueError, match="unsupported dry-run preset"):
        run_replay_mujoco_dry_run(steps=1, preset="unknown")


def test_run_replay_mujoco_dry_run_rejects_preset_with_custom_frames() -> None:
    frame = RawInputFrame(source="replay", timestamp_s=0.0)

    with pytest.raises(ValueError, match="preset and custom frames are mutually exclusive"):
        run_replay_mujoco_dry_run(steps=1, preset="sweep_x", frames=(frame,))


def test_dry_run_profile_metadata_cannot_be_spoofed() -> None:
    frame = RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            "desired_endpoint_m": (0.6, 0.0, 0.1),
            "target_position_m": (0.6, 0.0, 0.1),
            "robot_profile_id": "spoofed",
            "model_contract_version": "spoofed/v9",
            "robot_joint_names": ("wrong",),
            "robot_qpos_dimension": 999,
        },
    )
    payload = json.loads(run_replay_mujoco_dry_run(steps=1, frames=(frame,))[0])

    assert payload["metadata"]["robot_profile_id"] == "fast_arm"
    assert payload["metadata"]["model_contract_version"] == FAST_ARM_ROBOT_PROFILE.model_contract_version
    assert payload["metadata"]["robot_joint_names"] == list(FAST_ARM_ROBOT_PROFILE.canonical_joint_names)
    assert payload["metadata"]["robot_qpos_dimension"] == 4
