from __future__ import annotations

import json
from dataclasses import replace

from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.keyboard import build_keyboard_motion_command
from xpotato_sim.runtime.runners.offline_input_smoke import run_offline_input_runtime_stepping_smoke


def test_r7_b_input_driven_payload_smoke_roundtrips_keyboard_payload_and_feedback_target() -> None:
    expected_desired_endpoint_m = (0.1 + 0.1 / 60.0, 0.0, 0.3)
    command = build_keyboard_motion_command(
        ("KeyD",),
        current_tip_position_m=(0.1, 0.0, 0.3),
        timestamp_s=0.5,
    )
    command = replace(
        command,
        metadata={
            **command.metadata,
            "desired_endpoint_m": expected_desired_endpoint_m,
            "target_position_m": (0.24, 0.5, 0.75),
        },
    )

    result = run_offline_input_runtime_stepping_smoke(command, initial_qpos=(0.0, 0.0, 0.0, 0.0))

    assert isinstance(result.payload, dict)
    assert result.payload["metadata"]["desired_endpoint_m"] == expected_desired_endpoint_m
    assert result.payload["metadata"]["target_position_m"] == (0.24, 0.5, 0.75)
    assert result.payload["target_position_m"] == [0.24, 0.5, 0.75]

    roundtrip_payload = json.loads(json.dumps(result.payload))
    assert roundtrip_payload["metadata"]["desired_endpoint_m"] == list(expected_desired_endpoint_m)
    assert roundtrip_payload["metadata"]["target_position_m"] == [0.24, 0.5, 0.75]
    assert roundtrip_payload["target_position_m"] == [0.24, 0.5, 0.75]

    if result.endpoint_evaluation is None:
        assert "endpoint_evaluation" not in result.payload
        assert "endpoint_evaluation" not in roundtrip_payload
    else:
        assert result.payload["endpoint_evaluation"] == result.endpoint_evaluation
        assert roundtrip_payload["endpoint_evaluation"] == result.endpoint_evaluation
        assert result.payload["endpoint_evaluation"]["desired_endpoint_m"] == list(expected_desired_endpoint_m)
