"""R7-H measured contact press/hold Task lifecycle。

The binding consumes raw ``ContactEvidence`` from the #413 MuJoCo extractor.
It owns only phase transitions and the deterministic outcome projection; it
does not calculate contacts, filter forces, drive a robot, or render a viewer.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from xpotato_sim.runtime.composition.robot_bundle import (
    RESET_INITIAL_STATE_V1,
    ROBOT_TOOL_ENDPOINT_ROLE,
)
from xpotato_sim.runtime.contact.evidence import (
    ContactEvidence,
    ContactEvidenceStatus,
    ContactRecord,
)
from xpotato_sim.runtime.contact.task_contract import (
    CONTACT_TASK_OUTCOME_EVIDENCE,
    CONTACT_TASK_OUTCOME_PROVENANCE,
    CONTACT_TASK_TERMINAL_EVIDENCE,
    CONTACT_TASK_TERMINAL_PROVENANCE,
    ContactOperatorStatus,
    ContactTaskContext,
    ContactTaskContractError,
    ContactTaskObservation,
    ContactTaskOutcome,
    ContactTaskPhase,
    ContactTrialIdentity,
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


CONTACT_PRESS_HOLD_TASK_IDENTITY = VersionedIdentity(
    "contact_press_hold_task", 1
)


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(
        math.fsum((left[index] - right[index]) ** 2 for index in range(3))
    )


def _magnitude(value: Sequence[float]) -> float:
    return math.sqrt(math.fsum(item * item for item in value))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def _normalize(value: Sequence[float]) -> tuple[float, float, float] | None:
    magnitude = _magnitude(value)
    if magnitude <= 0.0 or not math.isfinite(magnitude):
        return None
    return tuple(item / magnitude for item in value)  # type: ignore[return-value]


def _mean_vector(values: Sequence[Sequence[float]]) -> tuple[float, float, float] | None:
    if not values:
        return None
    return tuple(
        math.fsum(value[index] for value in values) / len(values)
        for index in range(3)
    )  # type: ignore[return-value]


def _rotate_quaternion(
    quaternion: Sequence[float],
    vector: Sequence[float],
) -> tuple[float, float, float]:
    """Rotate an object-frame vector by MuJoCo wxyz quaternion."""

    w, x, y, z = quaternion
    vx, vy, vz = vector
    # q * v * q^-1, expanded to avoid a dependency or implicit frame helper.
    return (
        (1.0 - 2.0 * (y * y + z * z)) * vx
        + 2.0 * (x * y - z * w) * vy
        + 2.0 * (x * z + y * w) * vz,
        2.0 * (x * y + z * w) * vx
        + (1.0 - 2.0 * (x * x + z * z)) * vy
        + 2.0 * (y * z - x * w) * vz,
        2.0 * (x * z - y * w) * vx
        + 2.0 * (y * z + x * w) * vy
        + (1.0 - 2.0 * (x * x + y * y)) * vz,
    )


def _object_surface_normal(
    record: ContactRecord,
    *,
    object_surface_name: str,
) -> tuple[float, float, float]:
    """Orient MuJoCo's pair normal toward the target object's outward face."""

    if getattr(record, "g" + "eom1_name") == object_surface_name:
        return record.normal_world
    if getattr(record, "g" + "eom2_name") == object_surface_name:
        return tuple(-value for value in record.normal_world)  # type: ignore[return-value]
    # Identity validation catches this for normal production evidence.  Keep a
    # deterministic fallback for old serialized fixtures that omit names.
    return record.normal_world


def _contact_location(
    observation: ContactTaskObservation,
) -> tuple[float, float, float] | None:
    if observation.contact_location_world_m is not None:
        return observation.contact_location_world_m
    records = observation.contact_evidence.target_contacts
    return _mean_vector(tuple(item.point_world_m for item in records))


def _contact_normal(
    observation: ContactTaskObservation,
    *,
    object_surface_name: str,
) -> tuple[float, float, float] | None:
    records = observation.contact_evidence.target_contacts
    return _normalize(
        _mean_vector(
            tuple(
                _object_surface_normal(item, object_surface_name=object_surface_name)
                for item in records
            )
            or ()
        )
        or ()
    )


def _normal_alignment(
    observation: ContactTaskObservation,
    context: ContactTaskContext,
) -> float | None:
    if observation.object_orientation_wxyz is None:
        return None
    measured = _contact_normal(
        observation,
        object_surface_name=getattr(context.manifest.scene.object, "g" + "eom_name"),
    )
    if measured is None:
        return None
    expected = _normalize(
        _rotate_quaternion(
            observation.object_orientation_wxyz,
            context.target_normal_object,
        )
    )
    if expected is None:
        return None
    cosine = _dot(measured, expected)
    # 両vectorは直前にnormalize済み。端点の数ulpだけを丸め、実際の範囲外値は拒否する。
    if abs(cosine) > 1.0 + 8.0 * math.ulp(1.0):
        raise ContactTaskContractError("normalized contact cosine exceeds floating-point tolerance")
    return min(1.0, max(-1.0, cosine))


def _penetration(observation: ContactTaskObservation) -> float | None:
    contacts = observation.contact_evidence.target_contacts
    if not contacts:
        return None
    return max(item.penetration_m for item in contacts)


def _normal_force(observation: ContactTaskObservation) -> float | None:
    aggregate = observation.contact_evidence.aggregate
    return None if aggregate is None else aggregate.normal_force_n


def _tangential_force(observation: ContactTaskObservation) -> float | None:
    aggregate = observation.contact_evidence.aggregate
    if aggregate is None:
        return None
    return _magnitude(aggregate.tangential_force_world_n)


def _population_stddev(values: Sequence[float]) -> float | None:
    if not values:
        return None
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / len(values))


def _target_observations(
    observations: Sequence[ContactTaskObservation],
) -> tuple[ContactTaskObservation, ...]:
    return tuple(item for item in observations if item.has_target_contact)


def _last_value(
    observations: Sequence[ContactTaskObservation],
    field: str,
) -> object | None:
    for observation in reversed(observations):
        value = getattr(observation, field)
        if value is not None:
            return value
    return None


def _temporal_reason(
    state: "ContactTaskState",
    observation: ContactTaskObservation,
) -> str | None:
    """Reject non-monotonic or stale measured clocks at the task boundary."""

    if not state.observations:
        return None
    previous = state.observations[-1]
    if observation.elapsed_time_s < previous.elapsed_time_s:
        return "contact observation elapsed time moved backwards"
    for name in ("sample_time_s", "simulation_time_s"):
        current = getattr(observation.contact_evidence, name)
        prior = getattr(previous.contact_evidence, name)
        if current < prior:
            return f"contact evidence {name} moved backwards"
        if current == prior:
            return f"contact evidence {name} is stale"
    return None


def _measured_evidence_reason(
    evidence: object,
) -> str | None:
    """Recheck measured evidence invariants before any task success path."""

    if not isinstance(evidence, ContactEvidence):
        return "contact task requires typed contact evidence"
    if evidence.status is not ContactEvidenceStatus.MEASURED:
        return None
    # ContactEvidenceはfrozenだが、構築後にfieldを変更したobjectをcallerが渡すことはできる。
    # ここでpublic contractを再構築し、容易に検出できるcount mismatchだけでなく、
    # aggregate / recordの全不整合をsuccess gateでfail-closedにする。
    try:
        validated_contacts = tuple(replace(record) for record in evidence.contacts)
        ContactEvidence(
            status=evidence.status,
            scene_identity=evidence.scene_identity,
            object_identity=evidence.object_identity,
            manifest_digest=evidence.manifest_digest,
            sample_time_s=evidence.sample_time_s,
            simulation_time_s=evidence.simulation_time_s,
            contacts=validated_contacts,
            aggregate=evidence.aggregate,
            reason=evidence.reason,
            frame_index=evidence.frame_index,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return f"measured contact evidence failed validation: {exc}"
    target = evidence.target_contacts
    if not target:
        return "measured contact evidence has no target record"
    if evidence.aggregate is None:
        return "measured contact evidence has no aggregate"
    if any(
        record.force_status is not ContactEvidenceStatus.MEASURED
        for record in target
    ):
        return "measured target contact contains unavailable or invalid force"
    if evidence.aggregate.contact_count != len(target):
        return "measured contact aggregate count does not match target records"
    return None


@dataclass(frozen=True, slots=True)
class ContactTaskState:
    """Immutable Task-owned state; every raw observation remains replayable."""

    observations: tuple[ContactTaskObservation, ...] = ()
    phase: ContactTaskPhase = ContactTaskPhase.READY
    classification: TaskTerminalClassification = TaskTerminalClassification.RUNNING
    terminal_reason: str | None = None
    terminal_time_s: float | None = None
    dwell_started_at_s: float | None = None
    first_contact_time_s: float | None = None
    contact_loss_count: int = 0
    recontact_count: int = 0

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        if any(not isinstance(item, ContactTaskObservation) for item in observations):
            raise TypeError("contact task state observations must be typed")
        if not isinstance(self.phase, ContactTaskPhase):
            raise TypeError("contact task state phase must be typed")
        if not isinstance(self.classification, TaskTerminalClassification):
            raise TypeError("contact task state classification must be typed")
        if self.contact_loss_count < 0 or self.recontact_count < 0:
            raise ValueError("contact task state counters must be non-negative")
        object.__setattr__(self, "observations", observations)


def _outcome(
    context: ContactTaskContext,
    state: ContactTaskState,
) -> ContactTaskOutcome:
    measured = _target_observations(state.observations)
    penetrations = tuple(
        value for value in (_penetration(item) for item in measured) if value is not None
    )
    forces = tuple(
        value for value in (_normal_force(item) for item in measured) if value is not None
    )
    tangential = tuple(
        value
        for value in (_tangential_force(item) for item in measured)
        if value is not None
    )
    locations = tuple(
        value
        for value in (_contact_location(item) for item in measured)
        if value is not None
    )
    alignments = tuple(
        value
        for value in (_normal_alignment(item, context) for item in measured)
        if value is not None
    )
    max_penetration = max(penetrations) if penetrations else None
    penetration_high = context.target_penetration_band_m[1]
    overshoot = (
        max(0.0, max_penetration - penetration_high)
        if max_penetration is not None
        else None
    )
    steady_state_error = (
        abs(penetrations[-1] - math.fsum(context.target_penetration_band_m) / 2.0)
        if penetrations
        else None
    )
    slip_proxy = (
        max(
            _distance(locations[index - 1], locations[index])
            for index in range(1, len(locations))
        )
        if len(locations) > 1
        else 0.0 if locations else None
    )
    contact_drift = (
        _distance(locations[0], locations[-1]) if locations else None
    )
    terminal = state.terminal_time_s
    completion = terminal if state.classification is TaskTerminalClassification.SUCCESS else None
    return ContactTaskOutcome(
        manifest_digest=context.manifest_digest,
        trial=context.trial,
        dwell_interval_s=context.dwell_interval_s,
        timeout_s=context.timeout_s,
        target_penetration_band_m=context.target_penetration_band_m,
        target_normal_force_band_n=context.target_normal_force_band_n,
        approach_alignment_min_cosine=context.approach_alignment_min_cosine,
        normal_alignment_min_cosine=context.normal_alignment_min_cosine,
        max_contact_location_drift_m=context.max_contact_location_drift_m,
        require_pose_measurement=context.require_pose_measurement,
        phase=state.phase,
        classification=state.classification,
        reason=state.terminal_reason,
        terminal_time_s=terminal,
        completion_time_s=completion,
        first_contact_time_s=state.first_contact_time_s,
        peak_normal_force_n=max(forces) if forces else None,
        max_penetration_m=max_penetration,
        overshoot_m=overshoot,
        steady_state_error_m=steady_state_error,
        force_variability_n=_population_stddev(forces),
        peak_tangential_force_n=max(tangential) if tangential else None,
        slip_proxy_m=slip_proxy,
        contact_loss_count=state.contact_loss_count,
        recontact_count=state.recontact_count,
        final_tip_position_world_m=_last_value(
            state.observations, "tip_position_world_m"
        ),
        final_object_position_world_m=_last_value(
            state.observations, "object_position_world_m"
        ),
        final_object_orientation_wxyz=_last_value(
            state.observations, "object_orientation_wxyz"
        ),
        final_contact_location_world_m=locations[-1] if locations else None,
        contact_location_drift_m=contact_drift,
        final_normal_alignment_cosine=alignments[-1] if alignments else None,
        observations_count=len(state.observations),
    )


def _terminal_document(
    context: ContactTaskContext,
    state: ContactTaskState,
) -> dict[str, object]:
    outcome = _outcome(context, state)
    return {
        "classification": outcome.classification.value,
        "completion_time_s": outcome.completion_time_s,
        "manifest_digest": outcome.manifest_digest,
        "phase": outcome.phase.value,
        "reason": outcome.reason,
        "terminal_time_s": outcome.terminal_time_s,
        "trial": outcome.trial.to_document(),
    }


@dataclass(frozen=True, slots=True)
class ContactTaskBinding(TaskExecutionBinding):
    """frozen contact contextからpure transitionを生成するTask binding。"""

    context: ContactTaskContext

    def initial_state(self) -> ContactTaskState:
        return ContactTaskState()

    def _transition(self, state: ContactTaskState) -> TaskTransition:
        outcome = _outcome(self.context, state)
        outcome_status = (
            EvidenceStatus.INVALID
            if state.classification is TaskTerminalClassification.TECHNICAL_INVALID
            else EvidenceStatus.MEASURED
        )
        outcome_value = None if outcome_status is EvidenceStatus.INVALID else outcome.to_document()
        evidence = CanonicalEvidenceSet(
            (
                CanonicalEvidence(
                    identity=CONTACT_TASK_TERMINAL_EVIDENCE,
                    status=EvidenceStatus.MEASURED,
                    value=_terminal_document(self.context, state),
                    provenance=CONTACT_TASK_TERMINAL_PROVENANCE,
                ),
                CanonicalEvidence(
                    identity=CONTACT_TASK_OUTCOME_EVIDENCE,
                    status=outcome_status,
                    value=outcome_value,
                    provenance=CONTACT_TASK_OUTCOME_PROVENANCE,
                    reason=(
                        state.terminal_reason
                        if outcome_status is EvidenceStatus.INVALID
                        else None
                    ),
                ),
            )
        )
        return TaskTransition(
            state=state,
            classification=state.classification,
            evidence=evidence,
        )

    def _invalid(
        self,
        state: ContactTaskState,
        *,
        elapsed_time_s: float,
        reason: str,
        observation: ContactTaskObservation | None = None,
    ) -> TaskTransition:
        observations = state.observations
        if observation is not None:
            observations = observations + (observation,)
        next_state = replace(
            state,
            observations=observations,
            phase=ContactTaskPhase.TECHNICAL_INVALID,
            classification=TaskTerminalClassification.TECHNICAL_INVALID,
            terminal_reason=reason,
            terminal_time_s=elapsed_time_s,
        )
        return self._transition(next_state)

    def _validate_evidence_identity(
        self,
        observation: ContactTaskObservation,
    ) -> str | None:
        evidence = observation.contact_evidence
        expected_digest = self.context.manifest_digest
        if evidence.manifest_digest != expected_digest:
            return "contact evidence manifest digest does not match task context"
        if evidence.scene_identity != self.context.manifest.scene.identity:
            return "contact evidence scene identity does not match task context"
        if evidence.object_identity != self.context.manifest.scene.object.identity:
            return "contact evidence object identity does not match task context"
        return None

    def _within_target_band(
        self,
        observation: ContactTaskObservation,
        prior_observations: Sequence[ContactTaskObservation] = (),
    ) -> tuple[bool, str | None]:
        penetration = _penetration(observation)
        if penetration is None:
            return False, "target contact penetration is unavailable"
        low, high = self.context.target_penetration_band_m
        if not low <= penetration <= high:
            return False, None
        force_band = self.context.target_normal_force_band_n
        if force_band is not None:
            normal_force = _normal_force(observation)
            if normal_force is None:
                return False, "target normal force is unavailable"
            if not force_band[0] <= normal_force <= force_band[1]:
                return False, None
        alignment_min = self.context.normal_alignment_min_cosine
        if alignment_min is not None:
            alignment = _normal_alignment(observation, self.context)
            if alignment is None:
                return False, "target normal alignment measurement is unavailable"
            if alignment < alignment_min:
                return False, None
        drift_limit = self.context.max_contact_location_drift_m
        if drift_limit is not None:
            previous_target = _target_observations(prior_observations)
            if previous_target:
                previous_location = _contact_location(previous_target[0])
                current_location = _contact_location(observation)
                if previous_location is not None and current_location is not None:
                    if _distance(previous_location, current_location) > drift_limit:
                        return False, None
        return True, None

    def advance(self, state: object, observation: object) -> TaskTransition:
        """Consume one raw contact snapshot and return a versioned transition."""

        if not isinstance(state, ContactTaskState):
            raise TypeError("contact task state must use ContactTaskState")
        if state.classification is not TaskTerminalClassification.RUNNING:
            raise ValueError("contact task cannot advance after terminal state")
        if not isinstance(observation, ContactTaskObservation):
            raise TypeError("contact task observation must use ContactTaskObservation")
        elapsed = observation.elapsed_time_s
        identity_reason = self._validate_evidence_identity(observation)
        if identity_reason is not None:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason=identity_reason,
                observation=observation,
            )
        if state.observations and elapsed < state.observations[-1].elapsed_time_s:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason="contact observation elapsed time moved backwards",
                observation=observation,
            )
        if not state.observations and elapsed != 0.0:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason="first contact observation must start at elapsed_time_s=0",
                observation=observation,
            )
        temporal_reason = _temporal_reason(state, observation)
        if temporal_reason is not None:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason=temporal_reason,
                observation=observation,
            )
        if observation.operator_status in {
            ContactOperatorStatus.RESET_FAILURE,
            ContactOperatorStatus.TECHNICAL_INVALID,
        }:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason=observation.reason or "operator reported a technical-invalid trial",
                observation=observation,
            )
        evidence_status = observation.contact_evidence.status
        if evidence_status in {
            ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
            ContactEvidenceStatus.INVALID_CONTACT,
            ContactEvidenceStatus.SOLVER_INVALID,
        }:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason=observation.reason
                or observation.contact_evidence.reason
                or "contact measurement is unavailable or invalid",
                observation=observation,
            )
        measured_reason = _measured_evidence_reason(observation.contact_evidence)
        if measured_reason is not None:
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason=measured_reason,
                observation=observation,
            )
        if self.context.require_pose_measurement and any(
            value is None
            for value in (
                observation.tip_position_world_m,
                observation.object_position_world_m,
                observation.object_orientation_wxyz,
            )
        ):
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason="required final pose measurement is unavailable",
                observation=observation,
            )
        if self.context.approach_alignment_min_cosine is not None:
            previous_tip = (
                None
                if not state.observations
                else state.observations[-1].tip_position_world_m
            )
            if observation.tip_position_world_m is None or (
                state.observations and previous_tip is None
            ):
                return self._invalid(
                    state,
                    elapsed_time_s=elapsed,
                    reason="approach alignment gate requires measured tip positions",
                    observation=observation,
                )
        if (
            observation.has_target_contact
            and self.context.normal_alignment_min_cosine is not None
            and observation.object_orientation_wxyz is None
        ):
            return self._invalid(
                state,
                elapsed_time_s=elapsed,
                reason="normal alignment gate requires measured object orientation",
                observation=observation,
            )

        observations = state.observations + (observation,)
        if observation.operator_status in {
            ContactOperatorStatus.HELD,
            ContactOperatorStatus.REJECTED,
            ContactOperatorStatus.STALE,
            ContactOperatorStatus.TIMEOUT,
        }:
            failed = replace(
                state,
                observations=observations,
                phase=ContactTaskPhase.FAILURE,
                classification=TaskTerminalClassification.FAILURE,
                terminal_reason=observation.reason
                or f"operator status is {observation.operator_status.value}",
                terminal_time_s=elapsed,
            )
            return self._transition(failed)

        previous_contact = bool(state.observations and state.observations[-1].has_target_contact)
        current_contact = observation.has_target_contact
        first_contact = state.first_contact_time_s
        if current_contact and first_contact is None:
            first_contact = elapsed
        contact_loss_count = state.contact_loss_count + int(
            previous_contact and not current_contact
        )
        recontact_count = state.recontact_count + int(
            current_contact and not previous_contact and state.first_contact_time_s is not None
        )

        # Approach-direction is a measured pose gate only when the caller
        # explicitly declares a threshold.  Missing optional pose is not a
        # zero displacement and therefore cannot manufacture a pass.
        if (
            self.context.approach_alignment_min_cosine is not None
            and len(state.observations) > 0
            and observation.tip_position_world_m is not None
            and state.observations[-1].tip_position_world_m is not None
        ):
            previous_tip = state.observations[-1].tip_position_world_m
            assert previous_tip is not None
            displacement = tuple(
                observation.tip_position_world_m[index] - previous_tip[index]
                for index in range(3)
            )
            displacement_norm = _magnitude(displacement)
            if displacement_norm > 0.0:
                cosine = _dot(
                    tuple(item / displacement_norm for item in displacement),
                    self.context.approach_direction_world,
                )
                if cosine < self.context.approach_alignment_min_cosine:
                    failed = replace(
                        state,
                        observations=observations,
                        phase=ContactTaskPhase.FAILURE,
                        classification=TaskTerminalClassification.FAILURE,
                        terminal_reason="measured approach direction left the declared bound",
                        terminal_time_s=elapsed,
                    )
                    return self._transition(failed)

        within_band = False
        band_reason: str | None = None
        if current_contact:
            within_band, band_reason = self._within_target_band(
                observation,
                state.observations,
            )
            if band_reason is not None:
                return self._invalid(
                    state,
                    elapsed_time_s=elapsed,
                    reason=band_reason,
                    observation=observation,
                )
        dwell_started = state.dwell_started_at_s
        if current_contact and within_band and dwell_started is None:
            dwell_started = elapsed
        elif not current_contact or not within_band:
            dwell_started = None

        phase = ContactTaskPhase.APPROACH
        if current_contact:
            if state.first_contact_time_s is None:
                phase = ContactTaskPhase.FIRST_CONTACT
            elif within_band:
                phase = ContactTaskPhase.HOLD
            else:
                phase = ContactTaskPhase.PRESS

        classification = TaskTerminalClassification.RUNNING
        reason = None
        terminal_time = None
        if (
            current_contact
            and within_band
            and dwell_started is not None
            and elapsed - dwell_started >= self.context.dwell_interval_s
            and elapsed <= self.context.timeout_s
        ):
            classification = TaskTerminalClassification.SUCCESS
            phase = ContactTaskPhase.SUCCESS
            terminal_time = elapsed
        elif elapsed >= self.context.timeout_s:
            classification = TaskTerminalClassification.FAILURE
            phase = ContactTaskPhase.FAILURE
            reason = "contact press/hold timeout reached before success"
            terminal_time = elapsed

        next_state = ContactTaskState(
            observations=observations,
            phase=phase,
            classification=classification,
            terminal_reason=reason,
            terminal_time_s=terminal_time,
            dwell_started_at_s=dwell_started,
            first_contact_time_s=first_contact,
            contact_loss_count=contact_loss_count,
            recontact_count=recontact_count,
        )
        return self._transition(next_state)

    def finalize(
        self,
        state: object,
        *,
        reason: str = "contact task fixture ended before terminal classification",
    ) -> TaskTransition:
        """Close a finite fixture without inventing completion/force metrics."""

        if not isinstance(state, ContactTaskState):
            raise TypeError("contact task state must use ContactTaskState")
        if state.classification is not TaskTerminalClassification.RUNNING:
            return self._transition(state)
        terminal_time = (
            state.observations[-1].elapsed_time_s if state.observations else None
        )
        failed = replace(
            state,
            phase=ContactTaskPhase.FAILURE,
            classification=TaskTerminalClassification.FAILURE,
            terminal_reason=reason,
            terminal_time_s=terminal_time,
        )
        return self._transition(failed)


class ContactTaskLifecycle:
    """Validate empty plugin parameters and bind an immutable task context."""

    def initial_state(self, parameters: Mapping[str, object]) -> ContactTaskState:
        if parameters:
            raise ValueError("contact press/hold task does not accept plugin parameters")
        return ContactTaskState()

    def bind_context(
        self,
        context: object,
        parameters: Mapping[str, object],
    ) -> ContactTaskBinding:
        if parameters:
            raise ValueError("contact press/hold task does not accept plugin parameters")
        if not isinstance(context, ContactTaskContext):
            raise TypeError("contact press/hold task requires ContactTaskContext")
        return ContactTaskBinding(context)


@dataclass(frozen=True, slots=True)
class ContactRetryPolicy:
    """Bounded retry policy; only technical-invalid attempts are retryable."""

    max_attempts: int = 1

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 16:
            raise ContactTaskContractError("retry max_attempts must be within [1, 16]")


@dataclass(frozen=True, slots=True)
class ContactTaskAttemptResult:
    """One immutable trial attempt, retained when a bounded retry follows."""

    trial: ContactTrialIdentity
    transition: TaskTransition
    outcome: ContactTaskOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.trial, ContactTrialIdentity):
            raise TypeError("attempt result trial must be typed")
        if not isinstance(self.transition, TaskTransition):
            raise TypeError("attempt result transition must be typed")
        if not isinstance(self.outcome, ContactTaskOutcome):
            raise TypeError("attempt result outcome must be typed")
        if self.outcome.trial != self.trial:
            raise ContactTaskContractError("attempt result trial/outcome identity mismatch")


@dataclass(frozen=True, slots=True)
class ContactTaskExecutionResult:
    """Deterministic software-only fixture execution and retry summary."""

    manifest_digest: str
    attempts: tuple[ContactTaskAttemptResult, ...]

    def __post_init__(self) -> None:
        if not self.attempts:
            raise ContactTaskContractError("contact task execution must contain an attempt")
        if not all(isinstance(item, ContactTaskAttemptResult) for item in self.attempts):
            raise TypeError("contact task execution attempts must be typed")
        if any(item.outcome.manifest_digest != self.manifest_digest for item in self.attempts):
            raise ContactTaskContractError("execution attempt manifest identities disagree")

    @property
    def final_attempt(self) -> ContactTaskAttemptResult:
        return self.attempts[-1]

    @property
    def transition(self) -> TaskTransition:
        return self.final_attempt.transition

    @property
    def outcome(self) -> ContactTaskOutcome:
        return self.final_attempt.outcome

    @property
    def classification(self) -> TaskTerminalClassification:
        return self.outcome.classification

    def to_document(self) -> dict[str, object]:
        return {
            "attempts": [item.outcome.to_document() for item in self.attempts],
            "manifest_digest": self.manifest_digest,
            "schema_version": "contact-task-execution/v1",
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class ContactTaskRunner:
    """Run a deterministic sequence of pre-measured observations.

    This runner is deliberately a fixture/replay boundary: it never steps
    MuJoCo and never emits a robot command.  A retry is admitted only after a
    technical-invalid attempt and every executed attempt remains in the
    result with a distinct trial identity.
    """

    def __init__(
        self,
        context: ContactTaskContext,
        *,
        retry_policy: ContactRetryPolicy = ContactRetryPolicy(),
    ) -> None:
        if not isinstance(context, ContactTaskContext):
            raise TypeError("contact task runner requires ContactTaskContext")
        if not isinstance(retry_policy, ContactRetryPolicy):
            raise TypeError("contact task runner requires ContactRetryPolicy")
        self.context = context
        self.retry_policy = retry_policy

    def _context_for_attempt(
        self,
        attempt_index: int,
        previous: ContactTrialIdentity | None,
    ) -> ContactTaskContext:
        base = self.context.trial
        trial = ContactTrialIdentity(
            trial_id=(
                base.trial_id
                if attempt_index == base.attempt_index
                else f"{base.trial_id}/retry-{attempt_index}"
            ),
            repetition_index=base.repetition_index,
            attempt_index=attempt_index,
            retry_of_trial_id=None if previous is None else previous.trial_id,
        )
        return replace(self.context, trial=trial)

    def run_attempts(
        self,
        attempts: Sequence[Sequence[ContactTaskObservation]],
    ) -> ContactTaskExecutionResult:
        streams = tuple(tuple(stream) for stream in attempts)
        if not streams:
            raise ContactTaskContractError("contact task fixture requires observations")
        if len(streams) > self.retry_policy.max_attempts:
            raise ContactTaskContractError(
                "fixture supplied more attempts than the bounded retry policy"
            )
        results: list[ContactTaskAttemptResult] = []
        previous_trial: ContactTrialIdentity | None = None
        for index, stream in enumerate(streams):
            context = self._context_for_attempt(
                self.context.trial.attempt_index + index,
                previous_trial,
            )
            binding = ContactTaskBinding(context)
            state = binding.initial_state()
            transition: TaskTransition | None = None
            for observation in stream:
                transition = binding.advance(state, observation)
                state = transition.state
                if transition.classification is not TaskTerminalClassification.RUNNING:
                    break
            if transition is None or transition.classification is TaskTerminalClassification.RUNNING:
                transition = binding.finalize(state)
            outcome = _outcome(context, transition.state)
            result = ContactTaskAttemptResult(
                trial=context.trial,
                transition=transition,
                outcome=outcome,
            )
            results.append(result)
            previous_trial = context.trial
            if transition.classification is not TaskTerminalClassification.TECHNICAL_INVALID:
                if index + 1 < len(streams):
                    raise ContactTaskContractError(
                        "only technical-invalid attempts may be retried"
                    )
                break
        return ContactTaskExecutionResult(
            manifest_digest=self.context.manifest_digest,
            attempts=tuple(results),
        )

    def run(
        self,
        observations: Sequence[ContactTaskObservation]
        | Sequence[Sequence[ContactTaskObservation]],
    ) -> ContactTaskExecutionResult:
        values = tuple(observations)
        if not values:
            raise ContactTaskContractError("contact task fixture requires observations")
        if isinstance(values[0], ContactTaskObservation):
            return self.run_attempts((values,))  # type: ignore[arg-type]
        return self.run_attempts(values)  # type: ignore[arg-type]


def run_contact_task_fixture(
    context: ContactTaskContext,
    observations: Sequence[ContactTaskObservation]
    | Sequence[Sequence[ContactTaskObservation]],
    *,
    retry_policy: ContactRetryPolicy = ContactRetryPolicy(),
) -> ContactTaskExecutionResult:
    """Run a software-only deterministic contact task fixture/replay."""

    return ContactTaskRunner(context, retry_policy=retry_policy).run(observations)


def derive_contact_outcome(
    context: ContactTaskContext,
    observations: Sequence[ContactTaskObservation],
) -> ContactTaskOutcome:
    """Pure convenience projection used to regenerate an outcome summary."""

    return run_contact_task_fixture(context, observations).outcome


regenerate_contact_outcome_summary = derive_contact_outcome


CONTACT_PRESS_HOLD_TASK_PLUGIN = TaskPlugin(
    identity=CONTACT_PRESS_HOLD_TASK_IDENTITY,
    lifecycle=ContactTaskLifecycle(),
    required_robot_capabilities=frozenset({RESET_INITIAL_STATE_V1}),
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
    task_event_identity=CONTACT_TASK_TERMINAL_EVIDENCE,
    produced_evidence=frozenset(
        {CONTACT_TASK_TERMINAL_EVIDENCE, CONTACT_TASK_OUTCOME_EVIDENCE}
    ),
    compatible_backend_kinds=frozenset({"mujoco"}),
)


__all__ = [
    "CONTACT_PRESS_HOLD_TASK_IDENTITY",
    "CONTACT_PRESS_HOLD_TASK_PLUGIN",
    "CONTACT_TASK_OUTCOME_EVIDENCE",
    "CONTACT_TASK_OUTCOME_PROVENANCE",
    "CONTACT_TASK_TERMINAL_EVIDENCE",
    "CONTACT_TASK_TERMINAL_PROVENANCE",
    "ContactOperatorStatus",
    "ContactRetryPolicy",
    "ContactTaskAttemptResult",
    "ContactTaskBinding",
    "ContactTaskContext",
    "ContactTaskExecutionResult",
    "ContactTaskLifecycle",
    "ContactTaskObservation",
    "ContactTaskOutcome",
    "ContactTaskPhase",
    "ContactTaskRunner",
    "ContactTaskState",
    "ContactTrialIdentity",
    "derive_contact_outcome",
    "regenerate_contact_outcome_summary",
    "run_contact_task_fixture",
]
