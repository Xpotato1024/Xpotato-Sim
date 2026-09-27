"""Selfrionette 7-channel serial-protocol dry-run entry point."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from xpotato_sim.plugins.input_sources.selfrionette import (
    LoadcellNormalizationConfig,
    LoadcellNormalizedInputIntentConverter,
    NormalizedLoadcellInputIntent,
    SerialDiagnosticEvent,
    SerialInputSource,
)
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import (
    LoadcellEndpointMappingConfig,
    build_r7_a_lite_smoke_endpoint_mapping_config,
)
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.runtime.experiment.contracts import ControlMappingPlugin, PluginSelection
from xpotato_sim.schemas import InputIntent, MotionCommand, RawInputFrame

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "r7_a_lite_serial_frames" / "minimal_valid.txt"


@dataclass(frozen=True, slots=True)
class SelfrionetteSerialDryRunSmokeResult:
    frames_read: int
    vectors_read: int
    diagnostics: tuple[SerialDiagnosticEvent, ...]
    raw_frame: RawInputFrame | None
    normalized_intent: NormalizedLoadcellInputIntent | None
    motion_command: MotionCommand | None


def run_selfrionette_serial_dry_run_smoke(
    lines: Iterable[str],
    *,
    max_vectors: int = 1,
    normalization_config: LoadcellNormalizationConfig | None = None,
    endpoint_config: LoadcellEndpointMappingConfig | None = None,
    current_tip_position_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    mapping_plugin: ControlMappingPlugin,
    mapping_parameters: Mapping[str, object] | None = None,
) -> SelfrionetteSerialDryRunSmokeResult:
    """Run recorded Selfrionette protocol frames with an explicit mapping."""

    if max_vectors < 1:
        raise ValueError("max_vectors must be a positive integer")

    normalized_converter = LoadcellNormalizedInputIntentConverter(normalization_config)
    selected_mapping_parameters = (
        {
            "mapping_config": endpoint_config or {},
            "current_tip_position_m": current_tip_position_m,
        }
        if mapping_parameters is None
        else mapping_parameters
    )
    selected_mapping_parameters = mapping_plugin.normalize_parameters(
        selected_mapping_parameters
    )
    source = SerialInputSource.from_lines(lines)

    frames_read = 0
    vectors_read = 0
    last_raw_frame: RawInputFrame | None = None
    last_normalized_intent: NormalizedLoadcellInputIntent | None = None
    last_motion_command: MotionCommand | None = None

    while vectors_read < max_vectors:
        try:
            raw_frame = source.read_frame()
        except StopIteration:
            break

        frames_read += 1
        vectors_read += 1
        last_raw_frame = raw_frame
        last_normalized_intent = normalized_converter.convert(raw_frame)
        mapped_intent = mapping_plugin.strategy.map_input(
            last_normalized_intent,
            selected_mapping_parameters,
        )
        if not isinstance(mapped_intent, InputIntent):
            raise TypeError("loadcell mapping strategy returned an invalid input intent")
        last_motion_command = MotionCommand(
            timestamp_s=mapped_intent.timestamp_s,
            metadata=mapped_intent.metadata,
        )

    return SelfrionetteSerialDryRunSmokeResult(
        frames_read=frames_read,
        vectors_read=vectors_read,
        diagnostics=source.diagnostics,
        raw_frame=last_raw_frame,
        normalized_intent=last_normalized_intent,
        motion_command=last_motion_command,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("max-vectors must be a positive integer")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("deadzone must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("scale, gain-m, and max-delta-m must be positive")
    return parsed


def _vector3_csv(value: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError("current-tip-position-m must be x,y,z")

    try:
        vector = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("current-tip-position-m must contain numeric values") from exc

    return vector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the offline R7-A-lite Selfrionette serial-protocol dry-run "
            "from recorded fixture lines."
        ),
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help="fixture file with serial frame lines to replay offline",
    )
    parser.add_argument(
        "--max-vectors",
        type=_positive_int,
        default=1,
        help="maximum number of vector frames to consume",
    )
    parser.add_argument(
        "--current-tip-position-m",
        type=_vector3_csv,
        default=(0.0, 0.0, 0.0),
        help="comma-separated current tip position in meters",
    )
    parser.add_argument(
        "--scale",
        type=_positive_float,
        default=100000.0,
        help="normalization scale for raw loadcell values",
    )
    parser.add_argument(
        "--deadzone",
        type=_non_negative_float,
        default=0.0,
        help="normalization deadzone",
    )
    parser.add_argument(
        "--gain-m",
        type=_positive_float,
        default=1.0,
        help="provisional endpoint gain used for dry-run smoke only",
    )
    parser.add_argument(
        "--max-delta-m",
        type=_positive_float,
        default=0.03,
        help="maximum endpoint delta used for dry-run smoke only",
    )
    return parser


def _build_report_lines(result: object) -> list[str]:
    diagnostics = getattr(result, "diagnostics")
    motion_command = getattr(result, "motion_command")
    metadata = {} if motion_command is None else dict(motion_command.metadata)
    last_endpoint_delta_m = metadata.get("endpoint_delta_m")
    last_desired_endpoint_m = metadata.get("desired_endpoint_m")

    lines = [
        f"frames_read={getattr(result, 'frames_read')}",
        f"vectors={getattr(result, 'vectors_read')}",
        f"diagnostics={len(diagnostics)}",
        f"last_endpoint_delta_m={last_endpoint_delta_m}",
        f"last_desired_endpoint_m={last_desired_endpoint_m}",
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    fixture_lines = args.fixture.read_text(encoding="utf-8").splitlines()
    normalization_config = LoadcellNormalizationConfig(
        scale=args.scale,
        clamp_abs=1.0,
    )
    endpoint_config = build_r7_a_lite_smoke_endpoint_mapping_config(
        gain_m=args.gain_m,
        max_delta_m=args.max_delta_m,
        operational_deadzone=args.deadzone,
    )
    result = run_selfrionette_serial_dry_run_smoke(
        fixture_lines,
        max_vectors=args.max_vectors,
        normalization_config=normalization_config,
        endpoint_config=endpoint_config,
        current_tip_position_m=args.current_tip_position_m,
        mapping_plugin=resolve_control_mapping_plugin(
            PluginSelection("loadcell_endpoint_mapping", 1)
        ),
        mapping_parameters={
            "mapping_config": endpoint_config,
            "current_tip_position_m": args.current_tip_position_m,
        },
    )

    for line in _build_report_lines(result):
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
