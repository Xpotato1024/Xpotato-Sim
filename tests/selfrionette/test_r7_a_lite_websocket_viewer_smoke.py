from __future__ import annotations

import json
from pathlib import Path

from xpotato_sim.runtime.runners.selfrionette_serial_dry_run import (
    run_selfrionette_serial_dry_run_smoke as _run_canonical_selfrionette_smoke,
)
from xpotato_sim.plugins.input_sources.selfrionette import LoadcellNormalizationConfig
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import build_r7_a_lite_smoke_endpoint_mapping_config
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.schemas import MuJoCoState
from xpotato_sim.transport import mujoco_state_to_payload


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "r7_a_lite_serial_frames"


def _run_resolved_selfrionette_smoke(*args, **kwargs):
    kwargs["mapping_plugin"] = resolve_control_mapping_plugin(
        PluginSelection("loadcell_endpoint_mapping", 1)
    )
    return _run_canonical_selfrionette_smoke(*args, **kwargs)


def read_fixture_lines(name: str) -> list[str]:
    return FIXTURE_ROOT.joinpath(name).read_text(encoding="utf-8").splitlines()


def test_r7_a_lite_websocket_viewer_smoke_preserves_command_metadata_without_endpoint_evaluation() -> None:
    result = _run_resolved_selfrionette_smoke(
        read_fixture_lines("minimal_valid.txt"),
        max_vectors=1,
        normalization_config=LoadcellNormalizationConfig(
            scale=100000.0,
            clamp_abs=1.0,
        ),
        endpoint_config=build_r7_a_lite_smoke_endpoint_mapping_config(
            gain_m=1.0,
            max_delta_m=0.03,
        ),
        current_tip_position_m=(0.25, 0.5, 0.75),
    )

    assert result.motion_command is not None
    assert result.normalized_intent is not None

    state = MuJoCoState(
        frame_index=1,
        time_s=result.motion_command.timestamp_s,
        target_position_m=None,
        metadata=dict(result.motion_command.metadata),
    )

    payload = mujoco_state_to_payload(state)
    payload_json = json.dumps(payload)
    parsed_payload = json.loads(payload_json)

    assert payload["target_position_m"] is None
    assert "endpoint_evaluation" not in payload
    assert payload["metadata"]["desired_endpoint_m"] == result.motion_command.metadata["desired_endpoint_m"]
    assert payload["metadata"]["endpoint_delta_m"] == result.motion_command.metadata["endpoint_delta_m"]
    assert payload["metadata"]["active_channels"] == result.motion_command.metadata["active_channels"]
    assert payload["metadata"]["current_tip_position_m"] == result.motion_command.metadata["current_tip_position_m"]

    assert parsed_payload["metadata"]["desired_endpoint_m"] == list(result.motion_command.metadata["desired_endpoint_m"])
    assert parsed_payload["metadata"]["endpoint_delta_m"] == list(result.motion_command.metadata["endpoint_delta_m"])
    assert parsed_payload["metadata"]["active_channels"] == list(result.motion_command.metadata["active_channels"])
    assert parsed_payload["metadata"]["current_tip_position_m"] == list(result.motion_command.metadata["current_tip_position_m"])
    assert parsed_payload["target_position_m"] is None
    assert "endpoint_evaluation" not in parsed_payload
