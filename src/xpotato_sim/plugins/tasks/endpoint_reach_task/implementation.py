"""R7-G endpoint reachのTask-owned lifecycleとevidence生成。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import sqrt

from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_POSE_V1,
    RESET_INITIAL_STATE_V1,
    ROBOT_TOOL_ENDPOINT_ROLE,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    CanonicalEvidenceSet,
    EvidenceStatus,
    ParameterContract,
    ROLE_ATTRIBUTE_WILDCARD,
    SemanticRoleRequirement,
    TaskExecutionBinding,
    TaskPlugin,
    TaskTerminalClassification,
    TaskTransition,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    ENDPOINT_REACH_TERMINAL_PROVENANCE,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
    EndpointReachMotionStatus,
    EndpointReachObservation,
    EndpointReachTaskContext,
    EndpointReachTrajectorySample,
)


ENDPOINT_REACH_TASK_IDENTITY = VersionedIdentity("endpoint_reach_task", 1)

# canonical initial tipは現行contractで小数6桁のreferenceを保持する一方、MuJoCoの
# measured site positionはfull precisionで返る。この値は物理的なreset許容差ではなく、
# 同一stateの数値表現差だけを許容するためのsoftware toleranceである。
_INITIAL_POSITION_NUMERICAL_TOLERANCE_M = 1e-6


@dataclass(frozen=True, slots=True)
class EndpointReachTaskState:
    """sample列と連続dwell開始時刻を保持するimmutable state。"""

    samples: tuple[EndpointReachTrajectorySample, ...] = ()
    dwell_started_at_s: float | None = None
    classification: TaskTerminalClassification = TaskTerminalClassification.RUNNING
    terminal_reason: str | None = None


def _distance_m(
    position_world_m: tuple[float, float, float],
    target_world_m: tuple[float, float, float],
) -> float:
    return sqrt(
        sum(
            (position_world_m[index] - target_world_m[index]) ** 2
            for index in range(3)
        )
    )


def _terminal_value(
    classification: TaskTerminalClassification,
    elapsed_time_s: float | None,
    reason: str | None,
) -> dict[str, object]:
    return {
        "classification": classification.value,
        "elapsed_time_s": elapsed_time_s,
        "reason": reason,
    }


@dataclass(frozen=True, slots=True)
class EndpointReachTaskBinding(TaskExecutionBinding):
    """frozen manifest contextからTask transitionと2種のevidenceを生成する。"""

    context: EndpointReachTaskContext

    def initial_state(self) -> EndpointReachTaskState:
        return EndpointReachTaskState()

    def _evidence(
        self,
        state: EndpointReachTaskState,
        *,
        elapsed_time_s: float | None,
        trajectory_status: EvidenceStatus = EvidenceStatus.MEASURED,
        trajectory_reason: str | None = None,
    ) -> CanonicalEvidenceSet:
        trajectory_value: object | None = None
        if trajectory_status is EvidenceStatus.MEASURED:
            measured_initial_position = (
                state.samples[0].position_world_m
                if state.samples
                else self.context.initial_position_world_m
            )
            trajectory_value = {
                "initial_position_world_m": measured_initial_position,
                "target_position_world_m": self.context.target_position_world_m,
                "samples": tuple(
                    {
                        "elapsed_time_s": sample.elapsed_time_s,
                        "position_world_m": sample.position_world_m,
                    }
                    for sample in state.samples
                ),
            }
        return CanonicalEvidenceSet(
            (
                CanonicalEvidence(
                    identity=ENDPOINT_REACH_TERMINAL_EVIDENCE,
                    status=EvidenceStatus.MEASURED,
                    value=_terminal_value(
                        state.classification,
                        elapsed_time_s,
                        state.terminal_reason,
                    ),
                    provenance=ENDPOINT_REACH_TERMINAL_PROVENANCE,
                ),
                CanonicalEvidence(
                    identity=ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
                    status=trajectory_status,
                    value=trajectory_value,
                    provenance=ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
                    reason=trajectory_reason,
                ),
            )
        )

    def _transition(
        self,
        state: EndpointReachTaskState,
        *,
        elapsed_time_s: float | None,
        trajectory_status: EvidenceStatus = EvidenceStatus.MEASURED,
        trajectory_reason: str | None = None,
    ) -> TaskTransition:
        return TaskTransition(
            state=state,
            classification=state.classification,
            evidence=self._evidence(
                state,
                elapsed_time_s=elapsed_time_s,
                trajectory_status=trajectory_status,
                trajectory_reason=trajectory_reason,
            ),
        )

    def advance(self, state: object, observation: object) -> TaskTransition:
        """1 measured observationを消費し、dwell resetを含む次状態を返す。"""

        if not isinstance(state, EndpointReachTaskState):
            raise TypeError("endpoint reach state must use EndpointReachTaskState")
        if state.classification is not TaskTerminalClassification.RUNNING:
            raise ValueError("endpoint reach task cannot advance after terminal state")
        if not isinstance(observation, EndpointReachObservation):
            raise TypeError("endpoint reach observation must use EndpointReachObservation")
        elapsed = observation.elapsed_time_s
        if state.samples and elapsed < state.samples[-1].elapsed_time_s:
            invalid = EndpointReachTaskState(
                samples=state.samples,
                classification=TaskTerminalClassification.TECHNICAL_INVALID,
                terminal_reason="endpoint sample time moved backwards",
            )
            return self._transition(
                invalid,
                elapsed_time_s=elapsed,
                trajectory_status=EvidenceStatus.INVALID,
                trajectory_reason=invalid.terminal_reason,
            )
        if observation.measurement_status is not EvidenceStatus.MEASURED:
            invalid = EndpointReachTaskState(
                samples=state.samples,
                classification=TaskTerminalClassification.TECHNICAL_INVALID,
                terminal_reason=observation.reason,
            )
            return self._transition(
                invalid,
                elapsed_time_s=elapsed,
                trajectory_status=observation.measurement_status,
                trajectory_reason=observation.reason,
            )
        if observation.motion_status in {
            EndpointReachMotionStatus.RESET,
            EndpointReachMotionStatus.TECHNICAL_INVALID,
        }:
            invalid = EndpointReachTaskState(
                samples=state.samples,
                classification=TaskTerminalClassification.TECHNICAL_INVALID,
                terminal_reason=observation.reason,
            )
            return self._transition(
                invalid,
                elapsed_time_s=elapsed,
                trajectory_status=EvidenceStatus.INVALID,
                trajectory_reason=observation.reason,
            )

        assert observation.position_world_m is not None
        if not state.samples:
            initial_position_error_m = _distance_m(
                observation.position_world_m,
                self.context.initial_position_world_m,
            )
            if (
                elapsed != 0.0
                or initial_position_error_m > _INITIAL_POSITION_NUMERICAL_TOLERANCE_M
            ):
                invalid = EndpointReachTaskState(
                    classification=TaskTerminalClassification.TECHNICAL_INVALID,
                    terminal_reason=(
                        "first measured endpoint sample must start at elapsed_time_s=0 "
                        "and match the frozen initial position within numerical tolerance"
                    ),
                )
                return self._transition(
                    invalid,
                    elapsed_time_s=elapsed,
                    trajectory_status=EvidenceStatus.INVALID,
                    trajectory_reason=invalid.terminal_reason,
                )
        samples = state.samples + (
            EndpointReachTrajectorySample(elapsed, observation.position_world_m),
        )
        if observation.motion_status in {
            EndpointReachMotionStatus.HELD,
            EndpointReachMotionStatus.REJECTED,
            EndpointReachMotionStatus.STALE,
        }:
            failed = EndpointReachTaskState(
                samples=samples,
                classification=TaskTerminalClassification.FAILURE,
                terminal_reason=observation.reason,
            )
            return self._transition(failed, elapsed_time_s=elapsed)

        within_tolerance = (
            _distance_m(observation.position_world_m, self.context.target_position_world_m)
            <= self.context.target_tolerance_m
        )
        dwell_started_at_s = state.dwell_started_at_s if within_tolerance else None
        if within_tolerance and dwell_started_at_s is None:
            dwell_started_at_s = elapsed

        classification = TaskTerminalClassification.RUNNING
        reason = None
        if elapsed > self.context.timeout_s:
            classification = TaskTerminalClassification.FAILURE
            reason = "endpoint reach timeout exceeded before success"
        elif (
            within_tolerance
            and dwell_started_at_s is not None
            and elapsed - dwell_started_at_s >= self.context.dwell_interval_s
        ):
            classification = TaskTerminalClassification.SUCCESS
        elif elapsed >= self.context.timeout_s:
            classification = TaskTerminalClassification.FAILURE
            reason = "endpoint reach timeout reached before success"

        next_state = EndpointReachTaskState(
            samples=samples,
            dwell_started_at_s=dwell_started_at_s,
            classification=classification,
            terminal_reason=reason,
        )
        return self._transition(next_state, elapsed_time_s=elapsed)


class EndpointReachTaskLifecycle:
    """upper contextを検証してendpoint reach executionへbindする。"""

    def initial_state(self, parameters: Mapping[str, object]) -> EndpointReachTaskState:
        if parameters:
            raise ValueError("endpoint reach task does not accept plugin parameters")
        return EndpointReachTaskState()

    def bind_context(
        self,
        context: object,
        parameters: Mapping[str, object],
    ) -> EndpointReachTaskBinding:
        if parameters:
            raise ValueError("endpoint reach task does not accept plugin parameters")
        if not isinstance(context, EndpointReachTaskContext):
            raise TypeError("endpoint reach task requires EndpointReachTaskContext")
        return EndpointReachTaskBinding(context)


ENDPOINT_REACH_TASK_PLUGIN = TaskPlugin(
    identity=ENDPOINT_REACH_TASK_IDENTITY,
    lifecycle=EndpointReachTaskLifecycle(),
    required_robot_capabilities=frozenset(
        {ENDPOINT_POSE_V1, RESET_INITIAL_STATE_V1}
    ),
    required_semantic_roles=frozenset(
        {
            SemanticRoleRequirement(
                role=ROBOT_TOOL_ENDPOINT_ROLE,
                object_kind="robot_endpoint",
                frame=ROLE_ATTRIBUTE_WILDCARD,
                unit="meter",
            )
        }
    ),
    parameter_contract=ParameterContract(),
    task_event_identity=ENDPOINT_REACH_TERMINAL_EVIDENCE,
    produced_evidence=frozenset(
        {ENDPOINT_REACH_TERMINAL_EVIDENCE, ENDPOINT_REACH_TRAJECTORY_EVIDENCE}
    ),
    compatible_backend_kinds=frozenset({"mujoco"}),
)


__all__ = [
    "ENDPOINT_REACH_TASK_IDENTITY",
    "ENDPOINT_REACH_TASK_PLUGIN",
    "ENDPOINT_REACH_TERMINAL_EVIDENCE",
    "ENDPOINT_REACH_TRAJECTORY_EVIDENCE",
    "EndpointReachTaskBinding",
    "EndpointReachTaskLifecycle",
    "EndpointReachTaskState",
]
