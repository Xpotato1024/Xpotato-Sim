from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import (
    build_fast_arm_endpoint_diagnostic_log_rows,
    build_fast_arm_fk_site_consistency_log_rows,
    build_fast_arm_ik_fk_sanity_log_rows,
    run_fast_arm_fk_site_consistency_diagnostics,
    run_fast_arm_ik_fk_sanity_diagnostics,
    run_fast_arm_endpoint_motion_sanity,
    run_fast_arm_endpoint_trajectory_diagnostics,
    run_fast_arm_local_jacobian_diagnostics,
    write_fast_arm_fk_site_consistency_log_jsonl,
    write_fast_arm_endpoint_trajectory_log_csv,
    write_fast_arm_endpoint_trajectory_log_jsonl,
    write_fast_arm_endpoint_diagnostic_log_jsonl,
    write_fast_arm_ik_fk_sanity_log_jsonl,
)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _format_vector3(value: tuple[float, float, float] | None) -> str:
    if value is None:
        return "None"
    return "[" + ", ".join(f"{component:.6f}" for component in value) + "]"


def _format_value(value: object) -> str:
    if hasattr(value, "__dataclass_fields__"):
        items = {
            field_name: getattr(value, field_name)
            for field_name in value.__dataclass_fields__  # type: ignore[attr-defined]
        }
        return _format_value(items)
    if isinstance(value, tuple):
        formatted_components: list[str] = []
        for component in value:
            if isinstance(component, float):
                formatted_components.append(f"{component:.6f}")
            else:
                formatted_components.append(_format_value(component))
        return "[" + ", ".join(formatted_components) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key}={_format_value(item)}" for key, item in value.items()) + "}"
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fast_arm endpoint motion sanity check.")
    parser.add_argument(
        "--base-desired-endpoint-m",
        nargs=3,
        type=float,
        default=None,
        metavar=("X", "Y", "Z"),
        help="explicit command-side base endpoint in meters; omitted uses the initial tip position",
    )
    parser.add_argument(
        "--command-delta-m",
        type=_positive_float,
        default=0.02,
        help="small offset applied along each axis in meters",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="optional MuJoCo model path override",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="print structured backend diagnostics for command, solver, qpos, and MuJoCo tip alignment",
    )
    parser.add_argument(
        "--jacobian-diagnostics",
        action="store_true",
        help="print local Jacobian diagnostics around the initial and nearby qpos presets",
    )
    parser.add_argument(
        "--trajectory-diagnostics",
        action="store_true",
        help="print multi-step endpoint command trajectory diagnostics",
    )
    parser.add_argument(
        "--trajectory-steps",
        type=int,
        default=30,
        help="number of repeated endpoint command steps for trajectory diagnostics",
    )
    parser.add_argument(
        "--trajectory-delta-m",
        type=_positive_float,
        default=0.005,
        help="endpoint delta per repeated trajectory step in meters",
    )
    parser.add_argument(
        "--trajectory-export-csv",
        type=Path,
        default=None,
        help="write trajectory diagnostics rows to a CSV file",
    )
    parser.add_argument(
        "--trajectory-export-jsonl",
        type=Path,
        default=None,
        help="write trajectory diagnostics rows to a JSONL file",
    )
    parser.add_argument(
        "--endpoint-diagnostics-jsonl",
        type=Path,
        default=None,
        help="write endpoint diagnostic rows to a JSONL file",
    )
    parser.add_argument(
        "--fk-site-consistency",
        action="store_true",
        help="print runtime FK versus MuJoCo tip site consistency diagnostics for fixed qpos fixtures",
    )
    parser.add_argument(
        "--fk-site-consistency-jsonl",
        type=Path,
        default=None,
        help="write FK versus MuJoCo tip site consistency diagnostics to a JSONL file",
    )
    parser.add_argument(
        "--ik-fk-sanity",
        action="store_true",
        help="print structured diagnostics for target -> IK output qpos -> runtime FK endpoint",
    )
    parser.add_argument(
        "--ik-fk-sanity-jsonl",
        type=Path,
        default=None,
        help="write IK/FK sanity diagnostic rows to a JSONL file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    results = run_fast_arm_endpoint_motion_sanity(
        base_desired_endpoint_m=(
            None
            if args.base_desired_endpoint_m is None
            else tuple(args.base_desired_endpoint_m)
        ),
        command_delta_m=args.command_delta_m,
        model_path=args.model_path,
    )

    endpoint_diagnostic_rows = build_fast_arm_endpoint_diagnostic_log_rows(results)

    for step_index, (result, endpoint_row) in enumerate(zip(results, endpoint_diagnostic_rows, strict=True), start=1):
        fields = (
            f"step_index={step_index}",
            f"case_label={result.command_label}",
            f"axis={result.command_label}",
            f"status={result.status}",
            f"reason={result.reason}",
            f"base_endpoint_source={result.base_endpoint_source}",
            f"desired_endpoint_source={endpoint_row['desired_endpoint_source']}",
            f"base_endpoint_m={_format_vector3(result.base_endpoint_m)}",
            f"commanded_delta_m={_format_vector3(result.commanded_delta_m)}",
            f"actual_delta_m={_format_vector3(result.actual_delta_m)}",
            f"initial_tip_position_m={_format_vector3(result.initial_tip_position_m)}",
            f"final_tip_position_m={_format_vector3(result.final_tip_position_m)}",
            f"actual_tip_position_m={_format_value(endpoint_row['actual_tip_position_m'])}",
            f"desired_endpoint_m={_format_vector3(result.desired_endpoint_m)}",
            f"target_position_m={_format_vector3(result.target_position_m)}",
            f"endpoint_error_m={_format_value(endpoint_row['endpoint_error_m'])}",
            f"endpoint_error_norm_m={_format_value(endpoint_row['endpoint_error_norm_m'])}",
            f"target_rejected={result.target_rejected}",
            f"target_rejection_reason={result.target_rejection_reason}",
            f"target_rejection_message={result.target_rejection_message}",
            f"qpos_before={list(result.qpos_before[:4])}",
            f"qpos_after={list(result.qpos_after[:4])}",
            f"direction_dot={result.direction_dot}",
        )
        if args.diagnostics:
            fields = fields + (
                f"solver_input_endpoint_m={_format_value(result.solver_input_endpoint_m)}",
                f"solver_seed_qpos={_format_value(result.solver_seed_qpos)}",
                f"solver_result_qpos={_format_value(result.solver_result_qpos)}",
                f"reachable_workspace_summary={_format_value(result.reachable_workspace_summary)}",
                f"distance_from_solver_base_m={_format_value(result.distance_from_solver_base_m)}",
                f"target_constraints_summary={_format_value(result.target_constraints_summary)}",
                f"frame_mapping_summary={_format_value(result.frame_mapping_summary)}",
                f"rejected_desired_endpoint_m={_format_value(result.rejected_desired_endpoint_m)}",
                f"last_valid_target_position_m={_format_value(result.last_valid_target_position_m)}",
                f"mujoco_base_link_position_m={_format_value(result.mujoco_base_link_position_m)}",
                f"mujoco_base_link_frame={result.mujoco_base_link_frame}",
                f"mujoco_tip_position_m={_format_value(result.mujoco_tip_position_m)}",
                f"tip_relative_to_base_link_m={_format_value(result.tip_relative_to_base_link_m)}",
                f"tip_relative_to_solver_base_m={_format_value(result.tip_relative_to_solver_base_m)}",
                f"solver_base_world_position_m={_format_value(result.solver_base_world_position_m)}",
                f"solver_local_target_m={_format_value(result.solver_local_target_m)}",
                f"world_target_m={_format_value(result.world_target_m)}",
                f"frame_transform_status={result.frame_transform_status}",
                f"qpos_ref_summary={_format_value(result.qpos_ref_summary)}",
                f"joint_axis_mapping_summary={_format_value(result.joint_axis_mapping_summary)}",
                f"qpos_perturbation_results={_format_value(result.qpos_perturbation_results)}",
                f"solver_to_mujoco_mapping={_format_value(result.solver_to_mujoco_mapping)}",
                f"mujoco_to_solver_mapping={_format_value(result.mujoco_to_solver_mapping)}",
                f"mapping_status={result.mapping_status}",
                f"solver_fk_endpoint_m={_format_value(result.solver_fk_endpoint_m)}",
                f"transformed_solver_fk_world_m={_format_value(result.transformed_solver_fk_world_m)}",
                f"diagnosis={result.diagnosis}",
            )
        print(" ".join(fields))

    if args.diagnostics or args.jacobian_diagnostics:
        jacobian_results = run_fast_arm_local_jacobian_diagnostics(model_path=args.model_path)
        for pose in jacobian_results:
            print(
                " ".join(
                    (
                        "local_jacobian",
                        f"pose_label={pose.pose_label}",
                        f"qpos={_format_value(pose.qpos)}",
                        f"tip_position_m={_format_value(pose.tip_position_m)}",
                        f"jacobian_matrix={_format_value(pose.jacobian_matrix)}",
                        f"joint_contribution_summary={_format_value(pose.joint_contribution_summary)}",
                    )
                )
            )

    if args.trajectory_diagnostics:
        if args.trajectory_steps <= 0:
            parser.error("--trajectory-steps must be positive")
        trajectory_results = run_fast_arm_endpoint_trajectory_diagnostics(
            trajectory_steps=args.trajectory_steps,
            trajectory_delta_m=args.trajectory_delta_m,
            model_path=args.model_path,
        )
        for trajectory in trajectory_results:
            print(
                " ".join(
                    (
                        "endpoint_trajectory",
                        f"command_label={trajectory.command_label}",
                        f"steps={trajectory.step_count}",
                        f"delta_m={trajectory.command_delta_m_per_step}",
                        f"initial_tip_position_m={_format_value(trajectory.initial_tip_position_m)}",
                        f"initial_qpos={_format_value(trajectory.initial_qpos)}",
                        f"summary={_format_value(trajectory.summary)}",
                    )
                )
            )
        if args.trajectory_export_csv is not None:
            csv_path = write_fast_arm_endpoint_trajectory_log_csv(
                trajectory_results,
                args.trajectory_export_csv,
            )
            print(f"trajectory_export_csv={csv_path}")
        if args.trajectory_export_jsonl is not None:
            jsonl_path = write_fast_arm_endpoint_trajectory_log_jsonl(
                trajectory_results,
                args.trajectory_export_jsonl,
            )
            print(f"trajectory_export_jsonl={jsonl_path}")
    elif args.trajectory_export_csv is not None or args.trajectory_export_jsonl is not None:
        parser.error("--trajectory-export-csv and --trajectory-export-jsonl require --trajectory-diagnostics")

    if args.endpoint_diagnostics_jsonl is not None:
        jsonl_path = write_fast_arm_endpoint_diagnostic_log_jsonl(
            results,
            args.endpoint_diagnostics_jsonl,
        )
        print(f"endpoint_diagnostics_jsonl={jsonl_path}")

    if args.ik_fk_sanity or args.ik_fk_sanity_jsonl is not None:
        ik_fk_results = run_fast_arm_ik_fk_sanity_diagnostics(model_path=args.model_path)
        ik_fk_rows = build_fast_arm_ik_fk_sanity_log_rows(ik_fk_results)
        if args.ik_fk_sanity:
            for index, (record, row) in enumerate(zip(ik_fk_results, ik_fk_rows, strict=True), start=1):
                print(
                    " ".join(
                        (
                            "ik_fk_sanity",
                            f"step_index={index}",
                            f"fixture_label={row['fixture_label']}",
                            f"status={record.status}",
                            f"ik_status={row['ik_status']}",
                            f"reason={record.reason}",
                            f"target_endpoint_m={_format_value(row['target_endpoint_m'])}",
                            f"ik_input_target_m={_format_value(row['ik_input_target_m'])}",
                            f"ik_output_qpos={_format_value(row['ik_output_qpos'])}",
                            f"fk_endpoint_from_ik_qpos_m={_format_value(row['fk_endpoint_from_ik_qpos_m'])}",
                            f"ik_fk_error_m={_format_value(row['ik_fk_error_m'])}",
                            f"ik_fk_error_norm_m={_format_value(row['ik_fk_error_norm_m'])}",
                            f"known_fk_site_consistency_status={row['known_fk_site_consistency_status']}",
                            f"known_fk_site_consistency_note={row['known_fk_site_consistency_note']}",
                            f"seed_qpos={_format_value(row['seed_qpos'])}",
                            f"joint_names={_format_value(row['joint_names'])}",
                            f"model_path={_format_value(row['model_path'])}",
                        )
                    )
                )
        if args.ik_fk_sanity_jsonl is not None:
            jsonl_path = write_fast_arm_ik_fk_sanity_log_jsonl(
                ik_fk_results,
                args.ik_fk_sanity_jsonl,
            )
            print(f"ik_fk_sanity_jsonl={jsonl_path}")

    if args.fk_site_consistency or args.fk_site_consistency_jsonl is not None:
        fk_site_results = run_fast_arm_fk_site_consistency_diagnostics(model_path=args.model_path)
        fk_site_rows = build_fast_arm_fk_site_consistency_log_rows(fk_site_results)
        for index, (record, row) in enumerate(zip(fk_site_results, fk_site_rows, strict=True), start=1):
            print(
                " ".join(
                    (
                        "fk_site_consistency",
                        f"step_index={index}",
                        f"fixture_label={row['fixture_label']}",
                        f"qpos={_format_value(row['qpos'])}",
                        f"solver_qpos={_format_value(row['solver_qpos'])}",
                        f"fk_endpoint_m={_format_value(row['fk_endpoint_m'])}",
                        f"transformed_solver_fk_world_m={_format_value(row['transformed_solver_fk_world_m'])}",
                        f"mujoco_tip_site_position_m={_format_value(row['mujoco_tip_site_position_m'])}",
                        f"fk_site_error_m={_format_value(row['fk_site_error_m'])}",
                        f"fk_site_error_norm_m={_format_value(row['fk_site_error_norm_m'])}",
                        f"status={record.status}",
                        f"reason={record.reason}",
                        f"site_name={row['site_name']}",
                        f"joint_names={_format_value(row['joint_names'])}",
                    )
                )
            )
        if args.fk_site_consistency_jsonl is not None:
            jsonl_path = write_fast_arm_fk_site_consistency_log_jsonl(
                fk_site_results,
                args.fk_site_consistency_jsonl,
            )
            print(f"fk_site_consistency_jsonl={jsonl_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
