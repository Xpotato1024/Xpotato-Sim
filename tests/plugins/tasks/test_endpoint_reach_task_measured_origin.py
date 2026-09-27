from __future__ import annotations

from xpotato_sim.plugins.tasks.endpoint_reach_task.implementation import (
    ENDPOINT_REACH_TASK_PLUGIN,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
)
from xpotato_sim.runtime.experiment.contracts import (
    EvidenceStatus,
    TaskTerminalClassification,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachObservation,
    EndpointReachTaskContext,
    decode_endpoint_reach_trajectory_evidence,
)


EXPECTED_INITIAL_POSITION_M = (0.240000, -0.245951, 0.284308)
MEASURED_INITIAL_POSITION_M = (
    0.24000000000000005,
    -0.245951214674781,
    0.284307806183469,
)


def _binding():
    return ENDPOINT_REACH_TASK_PLUGIN.bind_context(
        EndpointReachTaskContext(
            initial_position_world_m=EXPECTED_INITIAL_POSITION_M,
            target_position_world_m=(0.240000, -0.145951, 0.284308),
            target_tolerance_m=0.01,
            dwell_interval_s=0.2,
            timeout_s=5.0,
        ),
        {},
    )


def test_submicrometre_initial_projection_difference_uses_measured_origin() -> None:
    binding = _binding()

    transition = binding.advance(
        binding.initial_state(),
        EndpointReachObservation(
            elapsed_time_s=0.0,
            position_world_m=MEASURED_INITIAL_POSITION_M,
        ),
    )

    assert transition.classification is TaskTerminalClassification.RUNNING
    trajectory = decode_endpoint_reach_trajectory_evidence(transition.evidence)
    assert trajectory.initial_position_world_m == MEASURED_INITIAL_POSITION_M
    assert trajectory.samples[0].position_world_m == MEASURED_INITIAL_POSITION_M


def test_initial_position_difference_above_numerical_tolerance_is_invalid() -> None:
    binding = _binding()

    transition = binding.advance(
        binding.initial_state(),
        EndpointReachObservation(
            elapsed_time_s=0.0,
            position_world_m=(0.240002, -0.245951, 0.284308),
        ),
    )

    assert transition.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert (
        transition.evidence.require(ENDPOINT_REACH_TRAJECTORY_EVIDENCE).status
        is EvidenceStatus.INVALID
    )
