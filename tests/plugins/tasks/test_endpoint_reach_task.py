from __future__ import annotations

from typing import get_type_hints

import pytest

from xpotato_sim.plugins.tasks.endpoint_reach_task.implementation import (
    ENDPOINT_REACH_TASK_PLUGIN,
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    EndpointReachTaskLifecycle,
    EndpointReachTaskState,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_POSE_V1,
    RESET_INITIAL_STATE_V1,
)
from xpotato_sim.runtime.experiment.contracts import (
    EvidenceStatus,
    TaskTerminalClassification,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachMotionStatus,
    EndpointReachObservation,
    EndpointReachTaskContext,
    decode_endpoint_reach_terminal_evidence,
    decode_endpoint_reach_trajectory_evidence,
)


def _binding(*, timeout_s: float = 1.0):
    return ENDPOINT_REACH_TASK_PLUGIN.bind_context(
        EndpointReachTaskContext(
            initial_position_world_m=(0.0, 0.0, 0.0),
            target_position_world_m=(0.1, 0.0, 0.0),
            target_tolerance_m=0.01,
            dwell_interval_s=0.2,
            timeout_s=timeout_s,
        ),
        {},
    )


def _measured(elapsed_time_s: float, position_x_m: float) -> EndpointReachObservation:
    return EndpointReachObservation(
        elapsed_time_s=elapsed_time_s,
        position_world_m=(position_x_m, 0.0, 0.0),
    )


def _started(binding):
    return binding.advance(binding.initial_state(), _measured(0.0, 0.0))


def test_endpoint_reach_task_owns_state_capabilities_and_evidence() -> None:
    plugin = ENDPOINT_REACH_TASK_PLUGIN

    assert plugin.identity == VersionedIdentity("endpoint_reach_task", 1)
    assert plugin.lifecycle.initial_state({}) == EndpointReachTaskState()
    assert plugin.required_robot_capabilities == frozenset(
        {ENDPOINT_POSE_V1, RESET_INITIAL_STATE_V1}
    )
    assert plugin.produced_evidence == frozenset(
        {ENDPOINT_REACH_TERMINAL_EVIDENCE, ENDPOINT_REACH_TRAJECTORY_EVIDENCE}
    )
    assert plugin.compatible_backend_kinds == frozenset({"mujoco"})


def test_initial_state_annotation_resolves_mapping() -> None:
    hints = get_type_hints(EndpointReachTaskLifecycle.initial_state)
    assert "parameters" in hints
    assert hints["return"] is EndpointReachTaskState


def test_measured_samples_reach_tolerance_and_complete_dwell() -> None:
    binding = _binding()
    started = _started(binding)
    first = binding.advance(started.state, _measured(0.5, 0.095))
    terminal = binding.advance(first.state, _measured(0.71, 0.1))

    assert first.classification is TaskTerminalClassification.RUNNING
    assert terminal.classification is TaskTerminalClassification.SUCCESS
    decoded = decode_endpoint_reach_terminal_evidence(terminal.evidence)
    assert decoded.classification is TaskTerminalClassification.SUCCESS
    assert decoded.elapsed_time_s == pytest.approx(0.71)
    trajectory = decode_endpoint_reach_trajectory_evidence(terminal.evidence)
    assert trajectory.samples[-1].position_world_m == (0.1, 0.0, 0.0)
    assert terminal.evidence.require(ENDPOINT_REACH_TERMINAL_EVIDENCE).provenance == (
        "endpoint_reach_task/v1:terminal"
    )


def test_leaving_tolerance_resets_dwell_and_does_not_succeed() -> None:
    binding = _binding()
    started = _started(binding)
    entered = binding.advance(started.state, _measured(0.4, 0.095))
    left = binding.advance(entered.state, _measured(0.5, 0.05))
    reentered = binding.advance(left.state, _measured(0.7, 0.1))
    still_running = binding.advance(reentered.state, _measured(0.85, 0.1))

    assert left.classification is TaskTerminalClassification.RUNNING
    assert still_running.classification is TaskTerminalClassification.RUNNING


def test_timeout_without_completed_dwell_is_failure() -> None:
    binding = _binding()
    started = _started(binding)
    terminal = binding.advance(started.state, _measured(1.0, 0.0))

    assert terminal.classification is TaskTerminalClassification.FAILURE
    decoded = decode_endpoint_reach_terminal_evidence(terminal.evidence)
    assert decoded.classification is TaskTerminalClassification.FAILURE
    assert decoded.elapsed_time_s == pytest.approx(1.0)


def test_elapsed_beyond_timeout_cannot_become_success() -> None:
    binding = _binding()
    started = _started(binding)
    entered = binding.advance(started.state, _measured(0.8, 0.1))
    terminal = binding.advance(entered.state, _measured(1.01, 0.1))

    assert terminal.classification is TaskTerminalClassification.FAILURE


@pytest.mark.parametrize("status", (EvidenceStatus.UNAVAILABLE, EvidenceStatus.INVALID))
def test_unusable_measurement_is_technical_invalid(status: EvidenceStatus) -> None:
    binding = _binding()
    terminal = binding.advance(
        binding.initial_state(),
        EndpointReachObservation(
            elapsed_time_s=0.1,
            position_world_m=None,
            measurement_status=status,
            reason="endpoint measurement is unusable",
        ),
    )

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert terminal.evidence.require(ENDPOINT_REACH_TRAJECTORY_EVIDENCE).status is status
    decoded = decode_endpoint_reach_terminal_evidence(terminal.evidence)
    assert decoded.classification is TaskTerminalClassification.TECHNICAL_INVALID


def test_reset_status_is_technical_invalid() -> None:
    binding = _binding()
    terminal = binding.advance(
        binding.initial_state(),
        EndpointReachObservation(
            elapsed_time_s=0.1,
            position_world_m=(0.0, 0.0, 0.0),
            motion_status=EndpointReachMotionStatus.RESET,
            reason="runtime reset interrupted the trial",
        ),
    )
    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID


@pytest.mark.parametrize(
    "position",
    ((float("nan"), 0.0, 0.0), (0.0, 0.0), "malformed"),
)
def test_non_finite_or_malformed_sample_fails_closed(position: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        EndpointReachObservation(  # type: ignore[arg-type]
            elapsed_time_s=0.1,
            position_world_m=position,
        )


@pytest.mark.parametrize(
    "motion_status",
    (
        EndpointReachMotionStatus.HELD,
        EndpointReachMotionStatus.REJECTED,
        EndpointReachMotionStatus.STALE,
    ),
)
def test_held_rejected_or_stale_trial_is_not_success(
    motion_status: EndpointReachMotionStatus,
) -> None:
    binding = _binding()
    started = _started(binding)
    terminal = binding.advance(
        started.state,
        EndpointReachObservation(
            elapsed_time_s=0.1,
            position_world_m=(0.1, 0.0, 0.0),
            motion_status=motion_status,
            reason=f"command was {motion_status.value}",
        ),
    )
    assert terminal.classification is TaskTerminalClassification.FAILURE


def test_first_measured_sample_must_match_frozen_initial_position() -> None:
    binding = _binding()
    terminal = binding.advance(binding.initial_state(), _measured(0.1, 0.01))

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert (
        terminal.evidence.require(ENDPOINT_REACH_TRAJECTORY_EVIDENCE).status
        is EvidenceStatus.INVALID
    )
