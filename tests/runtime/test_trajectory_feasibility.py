from __future__ import annotations

import copy
import math
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

import xpotato_sim.runtime.safety.trajectory_feasibility as _trajectory_module

from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitQuantity,
    LimitSpace,
    LimitConversionProvenance,
    LimitSourceProvenance,
    PhysicalLimit,
)
from xpotato_sim.runtime.safety.limit_resolution import (
    JointSpaceConversion,
    project_limit_to_joint_space,
)
from xpotato_sim.runtime.safety.trajectory_feasibility import (
    ConfigurationFeasibilityResult,
    ConfigurationState,
    DynamicQuantity,
    FeasibilityDiagnostic,
    FeasibilityStatus,
    JacobianDiagnostic,
    TrajectoryFeasibilityPolicy,
    TrajectoryFeasibilityResult,
    TrajectorySample,
    VelocityEvidenceBinding,
    VelocityEvidenceKind,
    evaluate_configuration_feasibility,
    evaluate_trajectory_feasibility,
    validate_trajectory_feasibility_policy,
    validate_configuration_feasibility_result,
    validate_trajectory_feasibility_result,
)


JOINTS = ("joint_a", "joint_b", "joint_c")


def test_evaluated_candidate_projection_preserves_actual_configuration_and_samples() -> None:
    state = ConfigurationState((0.1, 0.2, 0.3), (0.0, 0.0, 0.0), _jacobian(), "same-source")
    result = evaluate_configuration_feasibility(state, _policy())
    assert result.evaluated_candidate.joint_names == JOINTS
    assert result.evaluated_candidate.configurations == ((state.qpos_rad, state.qvel_rad_s),)
    changed = replace(state, qpos_rad=(0.2, 0.2, 0.3))
    other = evaluate_configuration_feasibility(changed, _policy())
    assert result.source_id == other.source_id
    assert result.evaluated_candidate != other.evaluated_candidate
    samples = (
        _sample(0.0, (0.0, 0.0, 0.0)),
        _sample(0.1, (0.01, 0.0, 0.0)),
        _sample(0.2, (0.02, 0.0, 0.0)),
    )
    trajectory = evaluate_trajectory_feasibility(samples, _policy())
    assert trajectory.evaluated_candidate.timestamps_s == (0.0, 0.1, 0.2)
    assert trajectory.evaluated_candidate.configurations == tuple((s.qpos_rad, s.qvel_rad_s) for s in samples)


def _source(
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
    *,
    source_status: EvidenceStatus | None = None,
) -> LimitSourceProvenance:
    resolved_source_status = status if source_status is None else source_status
    return LimitSourceProvenance(
        source_kind="manufacturer_document"
        if resolved_source_status is EvidenceStatus.AUTHORITATIVE
        else "software_config",
        source_id="dynamic-fixture",
        revision="v1",
        status=resolved_source_status,
        evidence_reference="fixture-manual-001"
        if resolved_source_status is EvidenceStatus.AUTHORITATIVE
        else None,
    )


def _limit(
    joint_name: str,
    quantity: LimitQuantity,
    lower: float,
    upper: float,
    *,
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
    source_status: EvidenceStatus | None = None,
    frame: str = "fast_arm joint space",
    conversion: LimitConversionProvenance | None = None,
) -> PhysicalLimit:
    return PhysicalLimit(
        name=joint_name,
        quantity=quantity,
        lower=lower if status in {EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL} else None,
        upper=upper if status in {EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL} else None,
        unit="rad/s" if quantity is LimitQuantity.VELOCITY else "rad/s^2",
        space=LimitSpace.JOINT,
        frame=frame,
        status=status,
        source=_source(status, source_status=source_status),
        conversion=conversion,
        reason=None if status in {EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL} else "fixture source unavailable",
    )


def _policy(
    *,
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
    source_status: EvidenceStatus | None = None,
    frame: str = "fast_arm joint space",
    minimum_singular_value: float = 0.1,
    maximum_condition_number: float = 100.0,
) -> TrajectoryFeasibilityPolicy:
    limits = tuple(
        limit
        for joint_name in JOINTS
        for limit in (
            _limit(
                joint_name,
                LimitQuantity.VELOCITY,
                -2.0,
                2.0,
                status=status,
                source_status=source_status,
                frame=frame,
            ),
            _limit(
                joint_name,
                LimitQuantity.ACCELERATION,
                -10.0,
                10.0,
                status=status,
                source_status=source_status,
                frame=frame,
            ),
        )
    )
    return TrajectoryFeasibilityPolicy(
        joint_names=JOINTS,
        dynamic_limits=limits,
        expected_cadence_s=0.1,
        cadence_tolerance_s=1e-9,
        maximum_gap_s=0.2,
        required_jacobian_rank=3,
        minimum_singular_value=minimum_singular_value,
        maximum_condition_number=maximum_condition_number,
    )


def _projected_policy(source_space: LimitSpace) -> TrajectoryFeasibilityPolicy:
    limits = tuple(
        project_limit_to_joint_space(
            PhysicalLimit(
                name=f"{source_space.value}_{joint_name}",
                quantity=quantity,
                lower=-2.0 if quantity is LimitQuantity.VELOCITY else -10.0,
                upper=2.0 if quantity is LimitQuantity.VELOCITY else 10.0,
                unit="rad/s" if quantity is LimitQuantity.VELOCITY else "rad/s^2",
                space=source_space,
                frame="fast_arm joint space",
                status=EvidenceStatus.AUTHORITATIVE,
                source=_source(),
            ),
            JointSpaceConversion(
                source_space=source_space,
                joint_name=joint_name,
                source_name=f"{source_space.value}_{joint_name}",
                gear_ratio=2.0,
                sign=1.0,
                offset=0.0,
                relation_id=f"{source_space.value}-{joint_name}/v1",
                unit="rad/s" if quantity is LimitQuantity.VELOCITY else "rad/s^2",
            ),
        )
        for joint_name in JOINTS
        for quantity in (LimitQuantity.VELOCITY, LimitQuantity.ACCELERATION)
    )
    return TrajectoryFeasibilityPolicy(
        joint_names=JOINTS,
        dynamic_limits=limits,
        expected_cadence_s=0.1,
        cadence_tolerance_s=1e-9,
        maximum_gap_s=0.2,
        required_jacobian_rank=3,
        minimum_singular_value=0.1,
        maximum_condition_number=100.0,
    )


def _jacobian(*, rank: int = 3, minimum: float = 0.5, condition: float = 2.0) -> JacobianDiagnostic:
    return JacobianDiagnostic(
        source_id="fixture-jacobian",
        row_count=3,
        column_count=3,
        numeric_rank=rank,
        effective_rank=rank,
        minimum_singular_value=minimum,
        condition_number=condition,
    )


def _malformed_jacobian(*, minimum: float = 0.5, condition: float = 2.0) -> JacobianDiagnostic:
    diagnostic = _jacobian()
    object.__setattr__(diagnostic, "minimum_singular_value", minimum)
    object.__setattr__(diagnostic, "condition_number", condition)
    return diagnostic


def _sample(
    timestamp: float,
    qpos: tuple[float, ...],
    *,
    qvel: tuple[float, ...] | None = None,
    jacobian: JacobianDiagnostic | None = None,
) -> TrajectorySample:
    return TrajectorySample(timestamp, qpos, qvel, jacobian or _jacobian())


def _bypass_constructor(result: object) -> object:
    bypassed = object.__new__(type(result))
    for item in fields(result):
        object.__setattr__(bypassed, item.name, getattr(result, item.name))
    return bypassed


def _init_fields(value: object) -> dict[str, object]:
    return {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.init
    }


def test_configuration_and_trajectory_are_separate_and_feasible() -> None:
    policy = _policy()
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        policy,
    )
    assert configuration.status is FeasibilityStatus.FEASIBLE
    assert configuration.authoritative

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0), qvel=(0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0), qvel=(1.0, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0), qvel=(1.0, 0.0, 0.0)),
        ),
        policy,
    )
    assert trajectory.status is FeasibilityStatus.FEASIBLE
    assert trajectory.sample_count == 3
    assert trajectory.authoritative


@pytest.mark.parametrize("source_space", (LimitSpace.MOTOR, LimitSpace.ACTUATOR))
def test_projected_dynamic_limit_source_identity_round_trips_through_p4_revalidation(
    source_space: LimitSpace,
) -> None:
    policy = _projected_policy(source_space)
    first_limit = policy.dynamic_limits[0]
    assert first_limit.conversion is not None
    assert policy.canonical_fingerprint[2][0][15] == first_limit.conversion.source_name

    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        policy,
    )
    assert configuration.status is FeasibilityStatus.FEASIBLE
    assert validate_configuration_feasibility_result(configuration) is configuration

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        policy,
    )
    assert trajectory.status is FeasibilityStatus.FEASIBLE
    assert validate_trajectory_feasibility_result(trajectory) is trajectory

    fingerprint = configuration.policy_fingerprint
    raw_limit = fingerprint[2][0]
    missing_source_name = raw_limit[:15] + (None,) + raw_limit[16:]
    forged = fingerprint[:2] + ((missing_source_name,) + fingerprint[2][1:],) + fingerprint[3:]
    with pytest.raises(ValueError, match="source_name|reconstructed|malformed"):
        replace(configuration, policy_fingerprint=forged)


def test_projected_dynamic_limit_public_constructor_remains_guarded() -> None:
    with pytest.raises(
        ValueError,
        match="joint-space projected limit requires canonical projection origin",
    ):
        _limit(
            "joint_a",
            LimitQuantity.VELOCITY,
            -2.0,
            2.0,
            conversion=LimitConversionProvenance.projected(
                source_space=LimitSpace.MOTOR,
                relation_id="motor-joint_a/v1",
                gear_ratio=2.0,
                sign=1.0,
                offset=0.0,
                source_name="motor_joint_a",
            ),
        )


def test_dynamic_limits_require_the_canonical_fast_arm_joint_frame() -> None:
    with pytest.raises(ValueError, match="canonical fast_arm joint-space frame"):
        _policy(frame="world")

    policy = _policy()
    assert all(limit.frame == "fast_arm joint space" for limit in policy.dynamic_limits)


def test_finite_difference_velocity_evidence_keeps_missing_qvel_trajectory_valid() -> None:
    result = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )

    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.qvel_available == (False, False, False)
    assert len(result.velocity_evidence) == 2
    assert all(item.kind is VelocityEvidenceKind.FINITE_DIFFERENCE for item in result.velocity_evidence)


def test_public_feasible_result_constructors_require_evaluator_origin() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    with pytest.raises(ValueError, match="created by the evaluator"):
        ConfigurationFeasibilityResult(**_init_fields(configuration))

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    with pytest.raises(ValueError, match="created by the evaluator"):
        TrajectoryFeasibilityResult(**_init_fields(trajectory))


def test_feasible_result_clones_and_constructor_bypasses_cannot_gain_authority() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    assert copy.deepcopy(configuration).feasible is False
    assert copy.deepcopy(configuration).authoritative is False

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    bypassed = _bypass_constructor(trajectory)
    assert bypassed.feasible is False
    assert bypassed.authoritative is False


def test_same_semantic_nested_replacement_invalidates_state_and_sample_origin() -> None:
    policy = _policy()
    state = ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian())
    object.__setattr__(state, "jacobian", _jacobian())
    configuration = evaluate_configuration_feasibility(state, policy)
    assert configuration.status is FeasibilityStatus.INVALID
    assert configuration.reason_code == "invalid_state_binding"

    samples = [
        _sample(0.0, (0.0, 0.0, 0.0)),
        _sample(0.1, (0.1, 0.0, 0.0)),
        _sample(0.2, (0.2, 0.0, 0.0)),
    ]
    object.__setattr__(samples[1], "jacobian", _jacobian())
    trajectory = evaluate_trajectory_feasibility(tuple(samples), policy)
    assert trajectory.status is FeasibilityStatus.INVALID
    assert trajectory.reason_code == "invalid_sample_binding"


def test_same_semantic_nested_replacement_invalidates_result_origin() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    diagnostic = configuration.diagnostics[0]
    object.__setattr__(
        configuration,
        "diagnostics",
        (
            FeasibilityDiagnostic(
                diagnostic.code,
                diagnostic.detail,
                diagnostic.joint_name,
                diagnostic.sample_index,
                diagnostic.observed,
                diagnostic.threshold,
                diagnostic.provenance,
            ),
        ),
    )
    assert configuration.feasible is False
    assert configuration.authoritative is False

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    evidence = trajectory.velocity_evidence[0]
    replacement = VelocityEvidenceBinding(evidence.kind, evidence.sample_index, evidence.source_id)
    object.__setattr__(trajectory, "velocity_evidence", (replacement, *trajectory.velocity_evidence[1:]))
    assert trajectory.feasible is False
    assert trajectory.authoritative is False


def test_same_semantic_dynamic_limit_replacement_invalidates_result_origin() -> None:
    policy = _policy()
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        policy,
    )
    replacement_limits = tuple(
        replace(limit)
        for limit in policy.dynamic_limits
    )
    object.__setattr__(policy, "dynamic_limits", replacement_limits)
    assert configuration.feasible is False
    assert configuration.authoritative is False


def test_exact_type_validation_rejects_subclassed_trusted_dtos() -> None:
    class SubclassedConfigurationState(ConfigurationState):
        pass

    class SubclassedTrajectorySample(TrajectorySample):
        pass

    class SubclassedVelocityEvidenceBinding(VelocityEvidenceBinding):
        pass

    policy = _policy()
    state = SubclassedConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian())
    with pytest.raises(TypeError, match="typed contracts"):
        evaluate_configuration_feasibility(state, policy)

    sample = SubclassedTrajectorySample(0.0, (0.0, 0.0, 0.0), None, _jacobian())
    sample_result = evaluate_trajectory_feasibility(
        (sample, _sample(0.1, (0.1, 0.0, 0.0))),
        policy,
    )
    assert sample_result.status is FeasibilityStatus.INVALID
    assert sample_result.reason_code == "invalid_trajectory_sample"

    evidence = SubclassedVelocityEvidenceBinding(VelocityEvidenceKind.FINITE_DIFFERENCE, 1, "sample")
    with pytest.raises(TypeError, match="VelocityEvidenceBinding"):
        unavailable = evaluate_trajectory_feasibility(
            (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0))),
            policy,
        )
        validate_trajectory_feasibility_result(
            replace(unavailable, velocity_evidence=(evidence,))
        )


def test_exact_type_public_validators_reject_subclass_bypasses() -> None:
    policy = _policy()
    state = ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian())
    configuration = evaluate_configuration_feasibility(state, policy)
    trajectory = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0)), _sample(0.2, (0.2, 0.0, 0.0))),
        policy,
    )

    class SubclassedJacobian(JacobianDiagnostic):
        pass

    bypassed_jacobian = object.__new__(SubclassedJacobian)
    for item in fields(state.jacobian):
        object.__setattr__(bypassed_jacobian, item.name, getattr(state.jacobian, item.name))
    with pytest.raises(TypeError, match="JacobianDiagnostic"):
        _trajectory_module.validate_jacobian_diagnostic(bypassed_jacobian)

    class SubclassedPolicy(TrajectoryFeasibilityPolicy):
        pass

    bypassed_policy = object.__new__(SubclassedPolicy)
    for item in fields(policy):
        object.__setattr__(bypassed_policy, item.name, getattr(policy, item.name))
    with pytest.raises(TypeError, match="TrajectoryFeasibilityPolicy"):
        validate_trajectory_feasibility_policy(bypassed_policy)

    class SubclassedDiagnostic(FeasibilityDiagnostic):
        pass

    diagnostic = configuration.diagnostics[0]
    subclassed_diagnostic = SubclassedDiagnostic(
        diagnostic.code,
        diagnostic.detail,
        diagnostic.joint_name,
        diagnostic.sample_index,
        diagnostic.observed,
        diagnostic.threshold,
        diagnostic.provenance,
    )
    with pytest.raises(TypeError, match="FeasibilityDiagnostic"):
        replace(configuration, diagnostics=(subclassed_diagnostic,))

    class SubclassedConfigurationResult(ConfigurationFeasibilityResult):
        pass

    bypassed_configuration = object.__new__(SubclassedConfigurationResult)
    for item in fields(configuration):
        object.__setattr__(bypassed_configuration, item.name, getattr(configuration, item.name))
    with pytest.raises(TypeError, match="ConfigurationFeasibilityResult"):
        validate_configuration_feasibility_result(bypassed_configuration)

    class SubclassedTrajectoryResult(TrajectoryFeasibilityResult):
        pass

    bypassed_trajectory = object.__new__(SubclassedTrajectoryResult)
    for item in fields(trajectory):
        object.__setattr__(bypassed_trajectory, item.name, getattr(trajectory, item.name))
    with pytest.raises(TypeError, match="TrajectoryFeasibilityResult"):
        validate_trajectory_feasibility_result(bypassed_trajectory)


def test_unavailable_statuses_remain_valid_without_evaluator_origin() -> None:
    policy = _policy()
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), None, _jacobian()),
        policy,
    )
    ConfigurationFeasibilityResult(**_init_fields(configuration))

    trajectory = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0))),
        policy,
    )
    TrajectoryFeasibilityResult(**_init_fields(trajectory))


def test_feasible_result_requires_complete_diagnostics_and_evidence() -> None:
    kwargs = dict(
        source_id="configuration",
        bound_statuses=(EvidenceStatus.AUTHORITATIVE,) * len(JOINTS),
        expected_joint_names=JOINTS,
        policy_id="trajectory-feasibility",
        policy_revision="v1",
        limit_source_ids=tuple(f"velocity-source-{index}" for index in range(len(JOINTS))),
        bound_evidence_ids=("velocity-evidence",) * len(JOINTS),
        qvel_available=True,
        jacobian_available=True,
        policy_fingerprint=_policy().canonical_fingerprint,
        jacobian_source_ids=("fixture-jacobian",),
        jacobian_evidence_ids=("fixture-jacobian",),
    )
    with pytest.raises(ValueError, match="clear diagnostics"):
        ConfigurationFeasibilityResult(
            FeasibilityStatus.FEASIBLE,
            "feasibility_clear",
            (),
            **kwargs,
        )

    with pytest.raises(ValueError, match="equal length"):
        ConfigurationFeasibilityResult(
            FeasibilityStatus.FEASIBLE,
            "feasibility_clear",
            (FeasibilityDiagnostic("feasibility_clear", "clear"),),
            **{**kwargs, "bound_evidence_ids": ()},
        )


def test_feasible_result_rejects_unknown_bound_and_missing_source_identity() -> None:
    clear = (FeasibilityDiagnostic("feasibility_clear", "clear"),)
    common = dict(
        source_id="configuration",
        expected_joint_names=JOINTS,
        policy_id="trajectory-feasibility",
        policy_revision="v1",
        limit_source_ids=tuple(f"velocity-source-{index}" for index in range(len(JOINTS))),
        bound_evidence_ids=("velocity-evidence",) * len(JOINTS),
        qvel_available=True,
        jacobian_available=True,
        policy_fingerprint=_policy().canonical_fingerprint,
        jacobian_source_ids=("fixture-jacobian",),
        jacobian_evidence_ids=("fixture-jacobian",),
    )
    with pytest.raises(ValueError, match="unresolved dynamic evidence"):
        ConfigurationFeasibilityResult(
            status=FeasibilityStatus.FEASIBLE,
            reason_code="feasibility_clear",
            diagnostics=clear,
            bound_statuses=(EvidenceStatus.UNKNOWN,) * len(JOINTS),
            **common,
        )
    with pytest.raises(ValueError, match="source_id"):
        ConfigurationFeasibilityResult(
            status=FeasibilityStatus.FEASIBLE,
            reason_code="feasibility_clear",
            diagnostics=clear,
            bound_statuses=(EvidenceStatus.AUTHORITATIVE,) * len(JOINTS),
            **{**common, "source_id": ""},
        )


def test_unknown_source_status_does_not_calculate_provisional_bounds() -> None:
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(status=EvidenceStatus.PROVISIONAL, source_status=EvidenceStatus.UNKNOWN),
    )

    assert result.status is FeasibilityStatus.UNKNOWN
    assert result.reason_code == "unknown_limit_source"
    assert all(status is EvidenceStatus.UNKNOWN for status in result.bound_statuses)
    assert not any(item.code == "rejected_dynamic_limit" for item in result.diagnostics)


def test_every_result_status_requires_policy_and_joint_identity() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), None, _jacobian()),
        _policy(),
    )
    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    for result in (configuration, trajectory):
        for field_name, value in (
            ("expected_joint_names", ()),
            ("policy_id", ""),
            ("policy_revision", ""),
        ):
            with pytest.raises(ValueError):
                replace(result, **{field_name: value})


def test_trajectory_result_rejects_sample_source_and_velocity_evidence_contradictions() -> None:
    result = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    for field_name, value in (
        ("sample_count", 2),
        ("source_ids", ("wrong", "wrong", "wrong")),
        ("velocity_evidence", ()),
    ):
        with pytest.raises((TypeError, ValueError)):
            replace(result, **{field_name: value})


def test_public_result_validator_rejects_object_setattr_and_constructor_bypass() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    object.__setattr__(configuration, "policy_id", "tampered-policy")
    with pytest.raises(ValueError, match="binding was mutated"):
        validate_configuration_feasibility_result(configuration)

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    bypassed = _bypass_constructor(trajectory)
    object.__setattr__(bypassed, "velocity_evidence", ())
    with pytest.raises(ValueError, match="evidence"):
        validate_trajectory_feasibility_result(bypassed)


def test_two_samples_report_unavailable_acceleration_without_fabricating_value() -> None:
    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0), qvel=(0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0), qvel=(1.0, 0.0, 0.0)),
        ),
        _policy(),
    )

    assert trajectory.status is FeasibilityStatus.UNAVAILABLE
    assert trajectory.reason_code == "unavailable_acceleration"
    assert trajectory.sample_count == 2
    assert any(item.code == "unavailable_acceleration" for item in trajectory.diagnostics)


def test_provisional_bounds_remain_distinct_from_authoritative_evidence() -> None:
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(status=EvidenceStatus.PROVISIONAL),
    )

    assert result.status is FeasibilityStatus.FEASIBLE
    assert not result.authoritative
    assert all(status is EvidenceStatus.PROVISIONAL for status in result.bound_statuses)


def test_velocity_and_acceleration_bounds_reject_trajectory() -> None:
    velocity_result = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.3, 0.0, 0.0)),
            _sample(0.2, (0.6, 0.0, 0.0)),
        ),
        _policy(),
    )
    assert velocity_result.status is FeasibilityStatus.REJECTED
    assert velocity_result.reason_code == "rejected_dynamic_limit"
    assert any(item.joint_name == "joint_a" and item.sample_index == 1 for item in velocity_result.diagnostics)

    acceleration_result = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.4, 0.0, 0.0)),
        ),
        _policy(),
    )
    assert acceleration_result.status is FeasibilityStatus.REJECTED
    assert any(item.code == "rejected_dynamic_limit" and item.sample_index == 2 for item in acceleration_result.diagnostics)


def test_cadence_dimension_and_nonfinite_inputs_fail_closed() -> None:
    cadence_result = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.2, (0.1, 0.0, 0.0)), _sample(0.3, (0.2, 0.0, 0.0))),
        _policy(),
    )
    assert cadence_result.status is FeasibilityStatus.INVALID
    assert cadence_result.reason_code == "invalid_cadence_discontinuity"

    dimension_result = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0)), _sample(0.1, (0.1, 0.0)), _sample(0.2, (0.2, 0.0))),
        _policy(),
    )
    assert dimension_result.status is FeasibilityStatus.INVALID
    assert dimension_result.reason_code == "invalid_dimension_mismatch"

    nonfinite_result = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (float("nan"), 0.0, 0.0)), _sample(0.2, (0.2, 0.0, 0.0))),
        _policy(),
    )
    assert nonfinite_result.status is FeasibilityStatus.INVALID
    assert nonfinite_result.reason_code == "invalid_non_finite"
    assert next(
        item for item in nonfinite_result.diagnostics if item.code == "invalid_non_finite"
    ).joint_name == JOINTS[0]


def test_missing_qvel_jacobian_and_dynamic_source_are_not_success() -> None:
    no_qvel = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), None, _jacobian()),
        _policy(),
    )
    assert no_qvel.status is FeasibilityStatus.UNAVAILABLE
    assert no_qvel.reason_code == "unavailable_qvel"
    assert no_qvel.qvel_available is False
    assert validate_configuration_feasibility_result(no_qvel) is no_qvel

    no_jacobian = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), None),
        _policy(),
    )
    assert no_jacobian.status is FeasibilityStatus.UNAVAILABLE
    assert no_jacobian.reason_code == "unavailable_jacobian_diagnostic"

    unknown_source = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(status=EvidenceStatus.UNKNOWN),
    )
    assert unknown_source.status is FeasibilityStatus.UNKNOWN
    assert unknown_source.reason_code == "unknown_limit_source"


def test_jacobian_rank_condition_and_singularity_thresholds_reject() -> None:
    rank_result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian(rank=2)),
        _policy(),
    )
    assert rank_result.status is FeasibilityStatus.REJECTED
    assert rank_result.reason_code == "rejected_jacobian_rank"

    singular_result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian(minimum=0.1)),
        _policy(),
    )
    assert singular_result.status is FeasibilityStatus.REJECTED
    assert singular_result.reason_code == "rejected_jacobian_singularity"

    condition_result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian(condition=101.0)),
        _policy(),
    )
    assert condition_result.status is FeasibilityStatus.REJECTED
    assert condition_result.reason_code == "rejected_jacobian_condition"


def test_jacobian_thresholds_must_be_finite_and_positive() -> None:
    with pytest.raises(ValueError, match="finite"):
        _policy(maximum_condition_number=math.inf)
    with pytest.raises(ValueError, match="positive"):
        _policy(maximum_condition_number=0.0)
    with pytest.raises(ValueError, match="positive"):
        _policy(minimum_singular_value=-1.0)


@pytest.mark.parametrize(
    ("minimum", "condition"),
    ((math.inf, 2.0), (0.5, math.inf), (0.5, math.nan), (0.5, -1.0)),
)
def test_nonfinite_or_invalid_jacobian_diagnostic_is_typed_invalid(
    minimum: float,
    condition: float,
) -> None:
    result = evaluate_configuration_feasibility(
        ConfigurationState(
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            _malformed_jacobian(minimum=minimum, condition=condition),
        ),
        _policy(),
    )

    assert result.status is FeasibilityStatus.INVALID
    assert result.reason_code == "invalid_jacobian_diagnostic"
    diagnostic = next(
        item for item in result.diagnostics if item.code == "invalid_jacobian_diagnostic"
    )
    assert diagnostic.provenance == "fixture-jacobian"


def test_existing_jacobian_metrics_are_adapted_without_reimplementation() -> None:
    metrics = SimpleNamespace(
        jacobian=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        numeric_rank=3,
        effective_rank=3,
        minimum_singular_value=1.0,
        condition_number=1.0,
    )
    diagnostic = JacobianDiagnostic.from_metrics(metrics, source_id="existing-fast-arm-diagnostic")
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), diagnostic),
        _policy(),
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    assert diagnostic.source_id == "existing-fast-arm-diagnostic"


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("cadence_tolerance_s", math.nan),
        ("maximum_gap_s", -0.1),
        ("required_jacobian_rank", True),
        ("minimum_singular_value", math.inf),
        ("maximum_condition_number", -1.0),
        ("qvel_consistency_tolerance_rad_s", True),
        ("policy_id", "unknown"),
    ),
)
def test_public_policy_validator_rejects_tampered_thresholds_and_placeholders(
    field_name: str,
    value: object,
) -> None:
    policy = _policy()
    object.__setattr__(policy, field_name, value)
    with pytest.raises((TypeError, ValueError)):
        validate_trajectory_feasibility_policy(policy)


def test_public_policy_validator_deep_rejects_tampered_limit_unit_and_source() -> None:
    policy = _policy()
    object.__setattr__(policy.dynamic_limits[0], "unit", "m/s")
    with pytest.raises(ValueError, match="(unit|mutated)"):
        validate_trajectory_feasibility_policy(policy)

    policy = _policy()
    object.__setattr__(policy.dynamic_limits[0].source, "source_id", "unknown")
    with pytest.raises(ValueError, match="(non-placeholder|concrete identities)"):
        validate_trajectory_feasibility_policy(policy)

    policy = _policy()
    object.__setattr__(policy.dynamic_limits[0].source, "source_kind", "fixture")
    with pytest.raises(ValueError, match="(synthetic source|approved for physical authority)"):
        validate_trajectory_feasibility_policy(policy)


def test_policy_constructor_bypass_is_typed_invalid_at_evaluator_boundary() -> None:
    policy = _policy()
    object.__setattr__(policy, "dynamic_limits", "not-a-tuple")
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        policy,
    )
    assert result.status is FeasibilityStatus.INVALID


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("source_id", "unknown"),
        ("minimum_singular_value", math.nan),
        ("condition_number", True),
    ),
)
def test_jacobian_constructor_rejects_placeholder_or_non_numeric_values(
    field_name: str,
    value: object,
) -> None:
    kwargs = dict(
        source_id="jacobian-source",
        row_count=3,
        column_count=3,
        numeric_rank=3,
        effective_rank=3,
        minimum_singular_value=0.5,
        condition_number=2.0,
    )
    kwargs[field_name] = value
    with pytest.raises((TypeError, ValueError)):
        JacobianDiagnostic(**kwargs)


def test_nested_jacobian_mutation_becomes_typed_invalid_without_leaking() -> None:
    diagnostic = _jacobian()
    object.__setattr__(diagnostic, "source_id", "unknown")
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), diagnostic),
        _policy(),
    )
    assert result.status is FeasibilityStatus.INVALID
    assert result.reason_code == "invalid_jacobian_diagnostic"

    trajectory_diagnostic = _jacobian()
    object.__setattr__(trajectory_diagnostic, "condition_number", math.nan)
    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0), jacobian=trajectory_diagnostic),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    assert trajectory.status is FeasibilityStatus.INVALID


@pytest.mark.parametrize(
    "field_value",
    ("bad", (float("inf"), 0.0, 0.0)),
)
def test_malformed_configuration_state_fields_are_typed_invalid(field_value: object) -> None:
    state = object.__new__(ConfigurationState)
    object.__setattr__(state, "qpos_rad", field_value)
    object.__setattr__(state, "qvel_rad_s", (0.0, 0.0, 0.0))
    object.__setattr__(state, "jacobian", _jacobian())
    object.__setattr__(state, "source_id", "configuration-source")
    result = evaluate_configuration_feasibility(state, _policy())
    assert result.status is FeasibilityStatus.INVALID


def test_unknown_configuration_source_and_malformed_qvel_are_typed_invalid() -> None:
    state = ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian(), source_id="unknown")
    result = evaluate_configuration_feasibility(state, _policy())
    assert result.status is FeasibilityStatus.INVALID

    state = object.__new__(ConfigurationState)
    object.__setattr__(state, "qpos_rad", (0.0, 0.0, 0.0))
    object.__setattr__(state, "qvel_rad_s", "bad")
    object.__setattr__(state, "jacobian", _jacobian())
    object.__setattr__(state, "source_id", "configuration-source")
    result = evaluate_configuration_feasibility(state, _policy())
    assert result.status is FeasibilityStatus.INVALID


def test_malformed_trajectory_container_and_samples_are_typed_invalid() -> None:
    policy = _policy()
    iterator_result = evaluate_trajectory_feasibility(
        iter((_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0)))),
        policy,
    )
    assert iterator_result.status is FeasibilityStatus.INVALID

    non_sample_result = evaluate_trajectory_feasibility((object(), object()), policy)
    assert non_sample_result.status is FeasibilityStatus.INVALID

    sample = object.__new__(TrajectorySample)
    object.__setattr__(sample, "timestamp_s", 0.0)
    object.__setattr__(sample, "qpos_rad", (0.0, 0.0, 0.0))
    object.__setattr__(sample, "qvel_rad_s", (0.0, 0.0, 0.0))
    object.__setattr__(sample, "jacobian", _jacobian())
    object.__setattr__(sample, "source_id", "unknown")
    result = evaluate_trajectory_feasibility((sample, _sample(0.1, (0.1, 0.0, 0.0)), _sample(0.2, (0.2, 0.0, 0.0))), policy)
    assert result.status is FeasibilityStatus.INVALID

    huge = object.__new__(TrajectorySample)
    object.__setattr__(huge, "timestamp_s", 0.0)
    object.__setattr__(huge, "qpos_rad", (1e308, -1e308, 0.0))
    object.__setattr__(huge, "qvel_rad_s", None)
    object.__setattr__(huge, "jacobian", _jacobian())
    object.__setattr__(huge, "source_id", "huge-source")
    huge_result = evaluate_trajectory_feasibility(
        (huge, _sample(0.1, (0.0, 0.0, 0.0)), _sample(0.2, (0.0, 0.0, 0.0))),
        policy,
    )
    assert huge_result.status is FeasibilityStatus.INVALID


def test_policy_tamper_and_missing_feasible_evidence_never_produce_success() -> None:
    policy = _policy()
    object.__setattr__(policy, "maximum_condition_number", 0.0)
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        policy,
    )
    assert result.status is FeasibilityStatus.INVALID

    valid = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    with pytest.raises(ValueError, match="policy fingerprint"):
        replace(valid, policy_fingerprint=())
    with pytest.raises(ValueError, match="Jacobian source/evidence"):
        replace(valid, jacobian_source_ids=())


def test_result_policy_fingerprint_binds_limit_source_status_and_evidence() -> None:
    valid = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    with pytest.raises(ValueError, match="dynamic limit binding"):
        replace(valid, limit_source_ids=tuple(f"swapped-source-{index}" for index in range(len(JOINTS))))

    provisional = _policy(status=EvidenceStatus.PROVISIONAL).canonical_fingerprint
    with pytest.raises(ValueError, match="dynamic limit binding"):
        replace(
            valid,
            policy_fingerprint=provisional,
            bound_statuses=(EvidenceStatus.AUTHORITATIVE,) * len(JOINTS),
        )


def test_result_properties_fail_closed_after_status_or_binding_tamper() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    object.__setattr__(configuration, "status", FeasibilityStatus.INVALID)
    assert configuration.feasible is False
    assert configuration.authoritative is False

    trajectory = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0)), _sample(0.2, (0.2, 0.0, 0.0))),
        _policy(),
    )
    object.__setattr__(trajectory, "bound_evidence_ids", ())
    assert trajectory.feasible is False
    assert trajectory.authoritative is False

    bypassed = _bypass_constructor(configuration)
    object.__delattr__(bypassed, "_binding_fingerprint")
    assert bypassed.feasible is False
    assert bypassed.authoritative is False


@pytest.mark.parametrize("field_name", ("qpos_rad", "qvel_rad_s"))
def test_huge_integer_state_values_are_typed_invalid(field_name: str) -> None:
    state = object.__new__(ConfigurationState)
    object.__setattr__(state, "qpos_rad", (0.0, 0.0, 0.0))
    object.__setattr__(state, "qvel_rad_s", (0.0, 0.0, 0.0))
    object.__setattr__(state, field_name, (10**1000, 0.0, 0.0))
    object.__setattr__(state, "jacobian", _jacobian())
    object.__setattr__(state, "source_id", "huge-integer-state")
    result = evaluate_configuration_feasibility(state, _policy())
    assert result.status is FeasibilityStatus.INVALID


def test_legitimate_unavailable_dynamic_inputs_remain_unavailable() -> None:
    configuration = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), None, _jacobian()),
        _policy(),
    )
    assert configuration.status is FeasibilityStatus.UNAVAILABLE
    assert configuration.reason_code == "unavailable_qvel"

    trajectory = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0))),
        _policy(),
    )
    assert trajectory.status is FeasibilityStatus.UNAVAILABLE
    assert trajectory.reason_code == "unavailable_acceleration"


@pytest.mark.parametrize(
    ("status", "reason_code", "diagnostic_code"),
    (
        (FeasibilityStatus.FEASIBLE, "feasibility_clear", "feasibility_clear"),
        (FeasibilityStatus.UNAVAILABLE, "unavailable_qvel", "unavailable_qvel"),
        (FeasibilityStatus.INVALID, "invalid_trajectory_input", "invalid_trajectory_input"),
    ),
)
def test_trajectory_result_rejects_contradictory_short_shape_status(
    status: FeasibilityStatus,
    reason_code: str,
    diagnostic_code: str,
) -> None:
    short = evaluate_trajectory_feasibility((_sample(0.0, (0.0, 0.0, 0.0)),), _policy())
    with pytest.raises(ValueError, match="fewer than two|invalid_trajectory_length"):
        TrajectoryFeasibilityResult(
            **{
                **_init_fields(short),
                "status": status,
                "reason_code": reason_code,
                "diagnostics": (FeasibilityDiagnostic(diagnostic_code, "contradictory shape"),),
            }
        )


def test_trajectory_result_rejects_two_sample_success_like_or_wrong_shape_reason() -> None:
    two_samples = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0))),
        _policy(),
    )
    assert two_samples.status is FeasibilityStatus.UNAVAILABLE
    assert two_samples.reason_code == "unavailable_acceleration"

    clear = FeasibilityDiagnostic("feasibility_clear", "contradictory clear")
    with pytest.raises(ValueError, match="two-sample|acceleration"):
        TrajectoryFeasibilityResult(
            **{
                **_init_fields(two_samples),
                "status": FeasibilityStatus.FEASIBLE,
                "reason_code": "feasibility_clear",
                "diagnostics": (clear,),
            }
        )

    invalid_length = FeasibilityDiagnostic("invalid_trajectory_length", "two samples are valid input")
    with pytest.raises(ValueError, match="invalid_trajectory_length|unavailable_acceleration|two-sample"):
        TrajectoryFeasibilityResult(
            **{
                **_init_fields(two_samples),
                "status": FeasibilityStatus.INVALID,
                "reason_code": "invalid_trajectory_length",
                "diagnostics": (invalid_length, two_samples.diagnostics[0]),
            }
        )


def test_trajectory_result_accepts_canonical_short_and_two_sample_non_success_results() -> None:
    short = evaluate_trajectory_feasibility((_sample(0.0, (0.0, 0.0, 0.0)),), _policy())
    short_copy = TrajectoryFeasibilityResult(**_init_fields(short))
    assert short_copy.status is FeasibilityStatus.INVALID
    assert short_copy.reason_code == "invalid_trajectory_length"
    assert validate_trajectory_feasibility_result(short_copy) is short_copy

    two_samples = evaluate_trajectory_feasibility(
        (_sample(0.0, (0.0, 0.0, 0.0)), _sample(0.1, (0.1, 0.0, 0.0))),
        _policy(),
    )
    two_copy = TrajectoryFeasibilityResult(**_init_fields(two_samples))
    assert two_copy.status is FeasibilityStatus.UNAVAILABLE
    assert two_copy.reason_code == "unavailable_acceleration"
    assert validate_trajectory_feasibility_result(two_copy) is two_copy


def test_trajectory_result_shape_tamper_and_constructor_bypass_fail_closed() -> None:
    valid = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    bypassed = _bypass_constructor(valid)
    object.__setattr__(bypassed, "sample_count", 2)
    object.__setattr__(bypassed, "source_ids", bypassed.source_ids[:2])
    object.__setattr__(bypassed, "qvel_available", bypassed.qvel_available[:2])
    object.__setattr__(bypassed, "jacobian_available", bypassed.jacobian_available[:2])
    object.__setattr__(bypassed, "jacobian_source_ids", bypassed.jacobian_source_ids[:2])
    object.__setattr__(bypassed, "jacobian_evidence_ids", bypassed.jacobian_evidence_ids[:2])
    object.__setattr__(
        bypassed,
        "velocity_evidence",
        tuple(item for item in bypassed.velocity_evidence if item.sample_index < 2),
    )
    with pytest.raises(ValueError, match="two-sample|acceleration"):
        validate_trajectory_feasibility_result(bypassed)
    assert bypassed.feasible is False
    assert bypassed.authoritative is False


def test_policy_fingerprint_rejects_zero_gap_and_incomplete_limit_inventory() -> None:
    valid = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    fingerprint = valid.policy_fingerprint
    zero_gap = fingerprint[:5] + (0.0,) + fingerprint[6:]
    with pytest.raises(ValueError, match="maximum_gap_s must be positive"):
        replace(valid, policy_fingerprint=zero_gap)

    raw_limits = fingerprint[2]
    duplicate_and_missing = raw_limits[:-1] + (raw_limits[0],)
    incomplete = fingerprint[:2] + (duplicate_and_missing,) + fingerprint[3:]
    with pytest.raises(ValueError, match="exactly cover"):
        replace(valid, policy_fingerprint=incomplete)


def test_policy_requires_canonical_joint_quantity_inventory_and_external_seal() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="exactly cover"):
        replace(policy, dynamic_limits=policy.dynamic_limits[:-1])

    fingerprint = policy.canonical_fingerprint
    object.__setattr__(policy, "maximum_gap_s", 0.1)
    forged_fingerprint = fingerprint[:5] + (0.1,) + fingerprint[6:]
    object.__setattr__(policy, "_binding_fingerprint", forged_fingerprint)
    with pytest.raises(ValueError, match="mutated"):
        validate_trajectory_feasibility_policy(policy)

    bypassed = object.__new__(TrajectoryFeasibilityPolicy)
    for item in fields(policy):
        if item.name != "_binding_fingerprint":
            object.__setattr__(bypassed, item.name, getattr(_policy(), item.name))
    with pytest.raises(ValueError, match="fingerprint"):
        validate_trajectory_feasibility_policy(bypassed)


def test_limits_for_revalidates_tampered_dynamic_limits_before_returning() -> None:
    policy = _policy()
    object.__setattr__(policy.dynamic_limits[0], "upper", 0.0)

    with pytest.raises(ValueError, match="(mutated|dynamic limit)"):
        policy.limits_for(DynamicQuantity.VELOCITY)

    policy = _policy()
    object.__setattr__(policy, "dynamic_limits", policy.dynamic_limits[:-1])
    with pytest.raises(ValueError, match="exactly cover"):
        policy.limits_for(DynamicQuantity.ACCELERATION)


def test_result_clear_diagnostic_must_bind_to_authoritative_source() -> None:
    valid = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        _policy(),
    )
    unbound_clear = FeasibilityDiagnostic("feasibility_clear", "clear")
    with pytest.raises(ValueError, match="bound to its source"):
        replace(valid, diagnostics=(unbound_clear,))

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        _policy(),
    )
    with pytest.raises(ValueError, match="bound to its source"):
        replace(trajectory, diagnostics=(unbound_clear,))


def test_nested_constructor_bypass_and_private_rewrite_never_become_success() -> None:
    policy = _policy()
    valid = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        policy,
    )

    bypassed_diagnostic = object.__new__(FeasibilityDiagnostic)
    for name, value in (
        ("code", "feasibility_clear"),
        ("detail", "clear"),
        ("joint_name", None),
        ("sample_index", None),
        ("observed", None),
        ("threshold", None),
        ("provenance", valid.source_id),
    ):
        object.__setattr__(bypassed_diagnostic, name, value)
    with pytest.raises(ValueError, match="constructor-sealed"):
        replace(valid, diagnostics=(bypassed_diagnostic,))

    object.__setattr__(valid.diagnostics[0], "provenance", "forged-source")
    assert valid.feasible is False
    assert valid.authoritative is False

    trajectory = evaluate_trajectory_feasibility(
        (
            _sample(0.0, (0.0, 0.0, 0.0)),
            _sample(0.1, (0.1, 0.0, 0.0)),
            _sample(0.2, (0.2, 0.0, 0.0)),
        ),
        policy,
    )
    evidence = trajectory.velocity_evidence[0]
    object.__setattr__(evidence, "source_id", "forged-source")
    object.__setattr__(trajectory, "_binding_fingerprint", trajectory._binding_fingerprint)
    assert trajectory.feasible is False
    assert trajectory.authoritative is False

    bypassed_policy = object.__new__(TrajectoryFeasibilityPolicy)
    clean_policy = _policy()
    for item in fields(clean_policy):
        if item.name != "_binding_fingerprint":
            object.__setattr__(bypassed_policy, item.name, getattr(clean_policy, item.name))
    result = evaluate_configuration_feasibility(
        ConfigurationState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), _jacobian()),
        bypassed_policy,
    )
    assert result.status is FeasibilityStatus.INVALID


def test_bypassed_valid_trajectory_sample_is_typed_invalid() -> None:
    sample = object.__new__(TrajectorySample)
    for name, value in (
        ("timestamp_s", 0.0),
        ("qpos_rad", (0.0, 0.0, 0.0)),
        ("qvel_rad_s", (0.0, 0.0, 0.0)),
        ("jacobian", _jacobian()),
        ("source_id", "bypassed-sample"),
    ):
        object.__setattr__(sample, name, value)
    result = evaluate_trajectory_feasibility(
        (sample, _sample(0.1, (0.1, 0.0, 0.0)), _sample(0.2, (0.2, 0.0, 0.0))),
        _policy(),
    )
    assert result.status is FeasibilityStatus.INVALID
    assert result.reason_code == "invalid_sample_binding"


def test_public_jacobian_validator_rejects_bypass() -> None:
    diagnostic = object.__new__(JacobianDiagnostic)
    for name, value in (
        ("source_id", "bypassed-jacobian"),
        ("row_count", 3),
        ("column_count", 3),
        ("numeric_rank", 3),
        ("effective_rank", 3),
        ("minimum_singular_value", 0.5),
        ("condition_number", 2.0),
        ("evidence_reference", "bypassed-jacobian"),
    ):
        object.__setattr__(diagnostic, name, value)
    with pytest.raises(ValueError, match="fingerprint"):
        _trajectory_module.validate_jacobian_diagnostic(diagnostic)
