from __future__ import annotations

from pathlib import Path

import pytest

from xpotato_sim.runtime.runners.selfrionette_serial_dry_run import (
    run_selfrionette_serial_dry_run_smoke as _run_canonical_selfrionette_smoke,
)
from xpotato_sim.plugins.input_sources.selfrionette import (
    LoadcellNormalizationConfig,
    SerialFrameParseError,
    SerialInputSource,
)
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import (
    LoadcellEndpointMotionCommandConverter,
    build_r7_a_lite_smoke_endpoint_mapping_config,
)
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.runtime.runners.selfrionette_serial_dry_run import (
    DEFAULT_FIXTURE_PATH,
    main as run_selfrionette_serial_dry_run_main,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "r7_a_lite_serial_frames"


def _run_resolved_selfrionette_smoke(*args, **kwargs):
    kwargs.setdefault(
        "mapping_plugin",
        resolve_control_mapping_plugin(
            PluginSelection("loadcell_endpoint_mapping", 1)
        ),
    )
    return _run_canonical_selfrionette_smoke(*args, **kwargs)


def read_fixture_lines(name: str) -> list[str]:
    return FIXTURE_ROOT.joinpath(name).read_text(encoding="utf-8").splitlines()


def test_r7_a_lite_serial_dry_run_smoke_runs_recorded_fixture_through_command_chain() -> None:
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

    assert result.frames_read == 1
    assert result.vectors_read == 1
    assert [event.prefix for event in result.diagnostics] == ["status", "status", "status", "warn", "warn"]
    assert result.raw_frame is not None
    assert result.normalized_intent is not None
    assert result.motion_command is not None
    assert result.motion_command.target is None
    assert result.motion_command.joint is None
    assert result.motion_command.metadata["desired_endpoint_m"] == pytest.approx(
        (0.2496233, 0.5009906, 0.751376)
    )
    assert result.motion_command.metadata["endpoint_delta_m"] == pytest.approx(
        (-0.0003767, 0.0009906, 0.001376)
    )
    assert "target_position_m" not in result.motion_command.metadata


def test_r7_a_lite_serial_dry_run_smoke_preserves_diagnostics_and_stops_before_max_vectors() -> None:
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
        current_tip_position_m=(0.0, 0.0, 0.0),
    )

    assert len(result.diagnostics) == 5
    assert result.diagnostics[0].fields == ("setup_start",)
    assert result.diagnostics[-1].fields == ("calibration_spread", "4", "2501.0")


def test_r7_a_lite_serial_dry_run_smoke_rejects_malformed_fixture_deterministically() -> None:
    with pytest.raises(SerialFrameParseError):
        _run_resolved_selfrionette_smoke(
            read_fixture_lines("malformed.txt"),
            max_vectors=1,
            normalization_config=LoadcellNormalizationConfig(
                scale=100000.0,
                clamp_abs=1.0,
            ),
            endpoint_config=build_r7_a_lite_smoke_endpoint_mapping_config(
                gain_m=1.0,
                max_delta_m=0.03,
            ),
            current_tip_position_m=(0.0, 0.0, 0.0),
        )


def test_r7_a_lite_serial_dry_run_smoke_uses_injected_lines_instead_of_opening_a_port(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"from_lines": False}
    original_from_lines = SerialInputSource.from_lines

    def tracking_from_lines(cls: type[SerialInputSource], lines: list[str]) -> SerialInputSource:
        called["from_lines"] = True
        return original_from_lines(lines)

    monkeypatch.setattr(SerialInputSource, "from_lines", classmethod(tracking_from_lines))

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
        current_tip_position_m=(0.0, 0.0, 0.0),
    )

    assert called["from_lines"] is True
    assert result.frames_read == 1


def test_r7_a_lite_serial_dry_run_rejects_invalid_mapping_before_reading_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"from_lines": False}
    original_from_lines = SerialInputSource.from_lines

    def tracking_from_lines(cls: type[SerialInputSource], lines: list[str]) -> SerialInputSource:
        called["from_lines"] = True
        return original_from_lines(lines)

    monkeypatch.setattr(SerialInputSource, "from_lines", classmethod(tracking_from_lines))

    with pytest.raises(ValueError, match="gain_m must be non-negative"):
        _run_resolved_selfrionette_smoke(
            read_fixture_lines("minimal_valid.txt"),
            max_vectors=1,
            mapping_plugin=resolve_control_mapping_plugin(
                PluginSelection("loadcell_endpoint_mapping", 1)
            ),
            mapping_parameters={
                "mapping_config": {"gain_m": -0.01},
                "current_tip_position_m": (0.1, 0.2, 0.3),
            },
        )

    assert called["from_lines"] is False


def test_r7_a_lite_serial_dry_run_cli_fixture_mode_outputs_endpoint_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_selfrionette_serial_dry_run_main(
        [
            "--fixture",
            str(DEFAULT_FIXTURE_PATH),
            "--max-vectors",
            "1",
            "--current-tip-position-m",
            "0.25,0.5,0.75",
            "--scale",
            "100000.0",
            "--deadzone",
            "0.0",
            "--gain-m",
            "1.0",
            "--max-delta-m",
            "0.03",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "frames_read=1" in captured.out
    assert "vectors=1" in captured.out
    assert "diagnostics=5" in captured.out
    assert "last_endpoint_delta_m=" in captured.out
    assert "last_desired_endpoint_m=" in captured.out


def test_default_endpoint_mapping_is_no_op_and_explicit_mapping_changes_desired_endpoint_m() -> None:
    from xpotato_sim.plugins.input_sources.selfrionette import NormalizedLoadcellInputIntent
    from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import LoadcellEndpointMotionCommandConverter

    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=1.0,
        values=(0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        metadata={"origin": "smoke"},
    )

    no_op_command = LoadcellEndpointMotionCommandConverter().convert(
        intent,
        current_tip_position_m=(0.1, 0.2, 0.3),
    )
    explicit_command = build_r7_a_lite_smoke_endpoint_mapping_config(
        gain_m=1.0,
        max_delta_m=0.03,
    )
    mapped_command = _run_resolved_selfrionette_smoke(
        ["vector,1000,40000,0,0,0,0,0,0"],
        max_vectors=1,
        normalization_config=LoadcellNormalizationConfig(
            scale=100000.0,
            clamp_abs=1.0,
        ),
        endpoint_config=explicit_command,
        current_tip_position_m=(0.1, 0.2, 0.3),
    ).motion_command

    assert no_op_command.metadata["endpoint_delta_m"] == (0.0, 0.0, 0.0)
    assert no_op_command.metadata["desired_endpoint_m"] == (0.1, 0.2, 0.3)
    assert mapped_command is not None
    assert mapped_command.metadata["desired_endpoint_m"] != (0.1, 0.2, 0.3)
    assert "target_position_m" not in mapped_command.metadata
