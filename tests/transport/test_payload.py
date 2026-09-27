from __future__ import annotations

import json
from pathlib import Path

from xpotato_sim.schemas import BodyTransform, MuJoCoState, SiteTransform
from xpotato_sim.transport import TRANSPORT_PAYLOAD_VERSION, mujoco_state_to_payload


def test_mujoco_state_to_payload_returns_json_compatible_payload() -> None:
    metadata = {"source": "test", "flags": ["a", "b"]}
    state = MuJoCoState(
        frame_index=1,
        time_s=0.5,
        qpos=(1.0, 2.0, 3.0),
        qvel=(4.0, 5.0),
        bodies=(
            BodyTransform(
                name="base_link",
                position_m=(0.0, 0.0, 0.0),
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        ),
        sites=(
            SiteTransform(
                name="tip",
                position_m=(0.1, 0.2, 0.3),
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        ),
        target_position_m=None,
        metadata=metadata,
    )

    payload = mujoco_state_to_payload(state)

    assert isinstance(payload, dict)
    assert payload["version"] == TRANSPORT_PAYLOAD_VERSION == 0
    assert payload["frame_index"] == 1
    assert payload["time_s"] == 0.5
    assert payload["qpos"] == [1.0, 2.0, 3.0]
    assert payload["qvel"] == [4.0, 5.0]
    assert isinstance(payload["qpos"], list)
    assert isinstance(payload["qvel"], list)
    assert isinstance(payload["bodies"], list)
    assert isinstance(payload["sites"], list)
    assert payload["bodies"] == [
        {
            "name": "base_link",
            "position_m": [0.0, 0.0, 0.0],
            "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        }
    ]
    assert payload["sites"] == [
        {
            "name": "tip",
            "position_m": [0.1, 0.2, 0.3],
            "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        }
    ]
    assert payload["target_position_m"] is None
    assert "endpoint_evaluation" not in payload
    assert payload["metadata"] == metadata
    assert payload["metadata"] is not metadata

    json.dumps(payload)


def test_mujoco_state_to_payload_converts_target_position_tuple_to_list() -> None:
    state = MuJoCoState(
        frame_index=2,
        time_s=1.25,
        target_position_m=(0.4, 0.5, 0.6),
    )

    payload = mujoco_state_to_payload(state)

    assert payload["target_position_m"] == [0.4, 0.5, 0.6]
    assert isinstance(payload["target_position_m"], list)
    assert "endpoint_evaluation" not in payload


def test_mujoco_state_to_payload_keeps_target_feedback_separate_from_metadata() -> None:
    state = MuJoCoState(
        frame_index=3,
        time_s=2.0,
        target_position_m=(1.0, 2.0, 3.0),
        metadata={
            "preset": "sweep_x",
            "target_delta_m": [0.1, 0.0, 0.0],
            "desired_endpoint_m": [1.1, 2.0, 3.0],
        },
    )

    payload = mujoco_state_to_payload(state)

    assert payload["target_position_m"] == [1.0, 2.0, 3.0]
    assert payload["metadata"]["preset"] == "sweep_x"
    assert payload["metadata"]["target_delta_m"] == [0.1, 0.0, 0.0]
    assert payload["metadata"]["desired_endpoint_m"] == [1.1, 2.0, 3.0]


def test_mujoco_state_to_payload_lifts_endpoint_evaluation_out_of_metadata() -> None:
    endpoint_evaluation = {
        "desired_endpoint_m": [0.6, 0.0, 0.1],
        "qpos_like_joint_angles_rad": [0.1, -0.2, 0.0, 0.0],
        "fk_endpoint_m": [0.55, 0.0, 0.08],
        "site_endpoint_m": [0.62, 0.0, 0.7],
        "desired_to_fk_error_vector_m": [-0.05, 0.0, -0.02],
        "desired_to_site_error_vector_m": [0.02, 0.0, 0.6],
        "fk_to_site_error_vector_m": [0.07, 0.0, 0.62],
        "desired_to_fk_error_norm_m": 0.05385164807134504,
        "desired_to_site_error_norm_m": 0.6003332407921454,
        "fk_to_site_error_norm_m": 0.6239447641967053,
        "unit": "meter",
        "desired_endpoint_coordinate_frame": "command-side endpoint frame",
        "fk_endpoint_coordinate_frame": "solver-defined frame",
        "site_endpoint_coordinate_frame": "MuJoCo world / scene frame",
        "frame_mismatch_note": "diagnostic only; FK and site endpoints are not transformed or auto-aligned",
    }
    state = MuJoCoState(
        frame_index=4,
        time_s=3.0,
        metadata={
            "preset": "sweep_x",
            "endpoint_evaluation": endpoint_evaluation,
        },
    )

    payload = mujoco_state_to_payload(state)

    assert payload["endpoint_evaluation"] == endpoint_evaluation
    assert "endpoint_evaluation" not in payload["metadata"]
    assert payload["metadata"] == {"preset": "sweep_x"}


def test_transport_payload_contract_is_utf8_without_bom_or_mojibake_marker() -> None:
    path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "transport-payload.md"
    raw_bytes = path.read_bytes()
    assert not raw_bytes.startswith(b"\xef\xbb\xbf")

    text = raw_bytes.decode("utf-8")
    assert "縺" not in text
