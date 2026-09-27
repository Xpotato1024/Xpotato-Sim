from __future__ import annotations

from xpotato_sim.runtime.evaluation.manifest import SoftwareExecutionIdentity
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    decode_endpoint_reach_terminal_evidence,
    decode_endpoint_reach_trajectory_evidence,
)
from xpotato_sim.runtime.experiment.world_tool_runner import (
    ExperimentStopReason,
    run_r7_g_world_tool_experiment,
)


REVISION = "test-revision:issue-406-canonical-runner"
EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity="Xpotato1024/Xpotato-Sim",
    software_revision_identity=REVISION,
)


def test_canonical_world_tool_fixture_runs_real_mujoco_deterministically() -> None:
    first = run_r7_g_world_tool_experiment(
        manifest_software_revision_identity=REVISION,
        execution_identity=EXECUTION_IDENTITY,
    )
    second = run_r7_g_world_tool_experiment(
        manifest_software_revision_identity=REVISION,
        execution_identity=EXECUTION_IDENTITY,
    )

    assert first.pair_identity == second.pair_identity
    for left, right in (
        (first.world, second.world),
        (first.tool, second.tool),
    ):
        assert (
            left.condition_id,
            left.requested_control_frame,
            left.classification,
            left.step_count,
            left.final_elapsed_time_s,
            left.stop_reason,
            left.initial_measured_endpoint_world_m,
            left.final_measured_endpoint_world_m,
            decode_endpoint_reach_terminal_evidence(left.transition.evidence),
            decode_endpoint_reach_trajectory_evidence(left.transition.evidence),
        ) == (
            right.condition_id,
            right.requested_control_frame,
            right.classification,
            right.step_count,
            right.final_elapsed_time_s,
            right.stop_reason,
            right.initial_measured_endpoint_world_m,
            right.final_measured_endpoint_world_m,
            decode_endpoint_reach_terminal_evidence(right.transition.evidence),
            decode_endpoint_reach_trajectory_evidence(right.transition.evidence),
        )
    assert first.world.requested_control_frame == "world"
    assert first.tool.requested_control_frame == "tool"
    for condition in (first.world, first.tool):
        assert condition.initial_measured_endpoint_world_m is not None
        assert condition.classification is not TaskTerminalClassification.TECHNICAL_INVALID
        assert condition.step_count > 0
        assert condition.stop_reason in {
            ExperimentStopReason.TASK_TERMINAL,
            ExperimentStopReason.BOUNDED_STEP_LIMIT,
        }
        if condition.stop_reason is ExperimentStopReason.TASK_TERMINAL:
            assert condition.classification in {
                TaskTerminalClassification.SUCCESS,
                TaskTerminalClassification.FAILURE,
            }
