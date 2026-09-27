"""Generic, test-only conformance contracts for robot runtime plugins."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from pathlib import Path

from xpotato_sim.mujoco_backend.model_info import inspect_mujoco_model
from xpotato_sim.mujoco_backend.model_loader import load_mujoco_model
from xpotato_sim.mujoco_backend.model_loader import ModelResourceBundle
from xpotato_sim.runtime.composition.robot_resource import PackageResource
from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.composition.robot_plugin import RobotRuntimePlugin
from xpotato_sim.schemas import JointCommand, MotionCommand, MuJoCoState, Vector3


QposApplier = Callable[[HeadlessMuJoCoSimulator, tuple[float, ...]], None]
QposFeasibilityChecker = Callable[[object, tuple[float, ...]], None]
EndpointEvaluator = Callable[[tuple[float, ...]], Vector3]
FailureRunner = Callable[[Path], object]


@dataclass(frozen=True, slots=True)
class KnownForwardKinematicsCase:
    """A literal, independently recorded FK expectation."""

    case_id: str
    qpos: tuple[float, ...]
    expected_endpoint_m: Vector3
    coordinate_frame: str
    unit: str
    tolerance_m: float
    provenance: str


@dataclass(frozen=True, slots=True)
class InverseKinematicsRoundTripCase:
    """A literal reachable target and seed for an IK -> FK check."""

    case_id: str
    target_position_m: Vector3
    seed_qpos: tuple[float, ...]
    coordinate_frame: str
    unit: str
    tolerance_m: float
    expected_feasible: bool = True
    require_determinism: bool = True


@dataclass(frozen=True, slots=True)
class MuJoCoEndpointConsistencyCase:
    """A qpos applied to a real model before snapshot endpoint checks."""

    case_id: str
    qpos: tuple[float, ...]
    coordinate_frame: str
    unit: str
    tolerance_m: float
    qpos_applier: QposApplier | None = None


@dataclass(frozen=True, slots=True)
class FailClosedCase:
    """A robot-owned negative probe executed by the generic suite."""

    case_id: str
    description: str
    run: FailureRunner
    exception_type: type[Exception]
    message_pattern: str


@dataclass(frozen=True, slots=True)
class RobotRuntimePluginConformanceCase:
    """Immutable declarations needed to exercise one production robot pair."""

    case_id: str
    expected_profile_id: str
    profile: RobotProfile
    plugin: RobotRuntimePlugin
    model_asset: Path | ModelResourceBundle
    home_keyframe_name: str
    expected_joint_names: tuple[str, ...]
    expected_qpos_dimension: int
    expected_qvel_dimension: int
    endpoint_site_name: str
    joint_limit_config_asset: Path | PackageResource | None
    known_fk_cases: tuple[KnownForwardKinematicsCase, ...]
    ik_round_trip_cases: tuple[InverseKinematicsRoundTripCase, ...]
    mujoco_endpoint_cases: tuple[MuJoCoEndpointConsistencyCase, ...]
    fail_closed_cases: tuple[FailClosedCase, ...]
    model_aligned_endpoint_evaluator: EndpointEvaluator | None = None
    qpos_feasibility_checker: QposFeasibilityChecker | None = None


def _assert_finite_numeric_values(
    scope: str,
    field_name: str,
    value: Sequence[object],
) -> None:
    try:
        components = tuple(value)
    except TypeError as error:
        raise AssertionError(
            f"{scope}: {field_name} must contain numeric finite values"
        ) from error

    for index, component in enumerate(components):
        if isinstance(component, bool) or not isinstance(component, Real):
            raise AssertionError(
                f"{scope}: {field_name}[{index}] must be numeric and finite; "
                f"got {component!r}"
            )
        if not isfinite(float(component)):
            raise AssertionError(
                f"{scope}: {field_name}[{index}] must be finite; got {component!r}"
            )


def _assert_finite_vector3(
    scope: str,
    field_name: str,
    value: Sequence[object],
) -> None:
    try:
        length = len(value)
    except TypeError as error:
        raise AssertionError(
            f"{scope}: {field_name} must contain exactly three values"
        ) from error
    if length != 3:
        raise AssertionError(f"{scope}: {field_name} must contain exactly three values")
    _assert_finite_numeric_values(scope, field_name, value)


def _assert_finite_numeric_values_with_dimension(
    scope: str,
    field_name: str,
    value: Sequence[object],
    expected_dimension: int,
) -> None:
    try:
        components = tuple(value)
    except TypeError as error:
        raise AssertionError(
            f"{scope}: {field_name} dimension mismatch and values must be numeric"
        ) from error
    if len(components) != expected_dimension:
        raise AssertionError(f"{scope}: {field_name} dimension mismatch")
    _assert_finite_numeric_values(scope, field_name, components)


def _assert_positive_finite(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise AssertionError(f"{name} must be numeric, finite, and positive")
    if not isfinite(float(value)) or float(value) <= 0.0:
        raise AssertionError(f"{name} must be finite and positive")


def _validate_nested_case_ids(
    robot_case_id: str,
    collection_name: str,
    nested_cases: Sequence[object],
) -> None:
    seen_case_ids: set[str] = set()
    for nested_case in nested_cases:
        nested_case_id = nested_case.case_id
        scope = f"{robot_case_id}/{collection_name}"
        if not isinstance(nested_case_id, str) or not nested_case_id.strip():
            raise AssertionError(f"{scope}: empty case ID")
        if nested_case_id in seen_case_ids:
            raise AssertionError(
                f"{scope}: duplicate case ID {nested_case_id!r}"
            )
        seen_case_ids.add(nested_case_id)


def validate_conformance_case_registry(
    cases: Sequence[RobotRuntimePluginConformanceCase],
) -> None:
    """Validate the explicit test-only registry before parametrization."""

    if not cases:
        raise AssertionError("robot runtime plugin conformance registry must not be empty")

    case_ids = tuple(case.case_id for case in cases)
    profile_ids = tuple(case.expected_profile_id for case in cases)
    if any(not case_id for case_id in case_ids):
        raise AssertionError("conformance case IDs must be non-empty")
    if len(case_ids) != len(set(case_ids)):
        raise AssertionError(f"duplicate conformance case IDs: {case_ids}")
    if len(profile_ids) != len(set(profile_ids)):
        raise AssertionError(f"duplicate conformance profile IDs: {profile_ids}")

    for case in cases:
        if not case.expected_profile_id:
            raise AssertionError(f"{case.case_id}: profile ID must be non-empty")
        if not case.expected_joint_names:
            raise AssertionError(f"{case.case_id}: joint names must not be empty")
        if (
            isinstance(case.expected_qpos_dimension, bool)
            or not isinstance(case.expected_qpos_dimension, int)
            or case.expected_qpos_dimension <= 0
        ):
            raise AssertionError(
                f"{case.case_id}: expected_qpos_dimension must be a positive integer; "
                f"got {case.expected_qpos_dimension!r}"
            )
        if (
            isinstance(case.expected_qvel_dimension, bool)
            or not isinstance(case.expected_qvel_dimension, int)
            or case.expected_qvel_dimension <= 0
        ):
            raise AssertionError(
                f"{case.case_id}: expected_qvel_dimension must be a positive integer; "
                f"got {case.expected_qvel_dimension!r}"
            )
        if case.profile.profile_id != case.expected_profile_id:
            raise AssertionError(
                f"{case.case_id}: case profile ID does not match expected profile ID"
            )
        if case.plugin.profile_id != case.expected_profile_id:
            raise AssertionError(
                f"{case.case_id}: case plugin ID does not match expected profile ID"
            )
        if case.profile.canonical_joint_names != case.expected_joint_names:
            raise AssertionError(f"{case.case_id}: declared joint order is inconsistent")
        if case.profile.qpos_dimension != case.expected_qpos_dimension:
            raise AssertionError(f"{case.case_id}: declared qpos dimension is inconsistent")
        if case.profile.qvel_dimension != case.expected_qvel_dimension:
            raise AssertionError(f"{case.case_id}: declared qvel dimension is inconsistent")
        if case.model_asset != case.profile.mujoco_model_asset:
            raise AssertionError(f"{case.case_id}: model asset is not profile-owned")
        if case.home_keyframe_name != case.profile.initial_keyframe_name:
            raise AssertionError(f"{case.case_id}: home keyframe is not profile-owned")
        profile_endpoint_site_name = case.profile.endpoint.site_name
        if profile_endpoint_site_name is None:
            raise AssertionError(f"{case.case_id}: endpoint site binding is required")
        if case.endpoint_site_name != profile_endpoint_site_name:
            raise AssertionError(f"{case.case_id}: endpoint site is not profile-owned")
        if case.joint_limit_config_asset != case.profile.joint_limit_config_asset:
            raise AssertionError(f"{case.case_id}: joint-limit config is not profile-owned")
        if not case.known_fk_cases:
            raise AssertionError(f"{case.case_id}: known FK cases must not be empty")
        if not case.ik_round_trip_cases:
            raise AssertionError(f"{case.case_id}: IK round-trip cases must not be empty")
        if not case.mujoco_endpoint_cases:
            raise AssertionError(f"{case.case_id}: MuJoCo endpoint cases must not be empty")
        if not case.fail_closed_cases:
            raise AssertionError(f"{case.case_id}: fail-closed cases must not be empty")

        _validate_nested_case_ids(case.case_id, "known_fk_cases", case.known_fk_cases)
        _validate_nested_case_ids(
            case.case_id,
            "ik_round_trip_cases",
            case.ik_round_trip_cases,
        )
        _validate_nested_case_ids(
            case.case_id,
            "mujoco_endpoint_cases",
            case.mujoco_endpoint_cases,
        )
        _validate_nested_case_ids(
            case.case_id,
            "fail_closed_cases",
            case.fail_closed_cases,
        )

        for known in case.known_fk_cases:
            scope = f"{case.case_id}/known_fk_cases/{known.case_id}"
            _assert_finite_numeric_values_with_dimension(
                scope,
                "qpos",
                known.qpos,
                case.expected_qpos_dimension,
            )
            _assert_finite_vector3(scope, "expected_endpoint_m", known.expected_endpoint_m)
            _assert_positive_finite(f"{scope}: tolerance_m", known.tolerance_m)
            if not known.coordinate_frame or not known.unit or not known.provenance:
                raise AssertionError(f"{scope}: provenance is incomplete")

        for round_trip in case.ik_round_trip_cases:
            scope = f"{case.case_id}/ik_round_trip_cases/{round_trip.case_id}"
            _assert_finite_numeric_values_with_dimension(
                scope,
                "seed_qpos",
                round_trip.seed_qpos,
                case.expected_qpos_dimension,
            )
            _assert_finite_vector3(scope, "target_position_m", round_trip.target_position_m)
            _assert_positive_finite(f"{scope}: tolerance_m", round_trip.tolerance_m)
            if not round_trip.coordinate_frame or not round_trip.unit:
                raise AssertionError(f"{scope}: frame/unit is incomplete")

        for consistency in case.mujoco_endpoint_cases:
            scope = f"{case.case_id}/mujoco_endpoint_cases/{consistency.case_id}"
            _assert_finite_numeric_values_with_dimension(
                scope,
                "qpos",
                consistency.qpos,
                case.expected_qpos_dimension,
            )
            _assert_positive_finite(f"{scope}: tolerance_m", consistency.tolerance_m)
            if not consistency.coordinate_frame or not consistency.unit:
                raise AssertionError(f"{scope}: frame/unit is incomplete")

        for failure in case.fail_closed_cases:
            scope = f"{case.case_id}/fail_closed_cases/{failure.case_id}"
            if not isinstance(failure.description, str) or not failure.description.strip():
                raise AssertionError(f"{scope}: description must be non-empty")
            if not callable(failure.run):
                raise AssertionError(f"{scope}: run must be callable")
            if not isinstance(failure.exception_type, type) or not issubclass(
                failure.exception_type,
                Exception,
            ):
                raise AssertionError(
                    f"{scope}: exception_type must be an Exception class"
                )
            if not isinstance(failure.message_pattern, str) or not failure.message_pattern.strip():
                raise AssertionError(f"{scope}: message_pattern must be non-empty")


def load_case_model(case: RobotRuntimePluginConformanceCase):
    """Load a case through the production model-loading boundary."""

    return load_mujoco_model(
        case.model_asset,
        initial_keyframe_name=case.home_keyframe_name,
    )


def assert_qpos_feasible(
    case: RobotRuntimePluginConformanceCase,
    model: object,
    qpos: tuple[float, ...],
) -> None:
    """Exercise the profile/plugin-owned feasibility boundary."""

    if case.qpos_feasibility_checker is not None:
        case.qpos_feasibility_checker(model, qpos)
        return

    if len(qpos) != len(case.expected_joint_names):
        raise AssertionError(
            f"{case.case_id}: provide a qpos_feasibility_checker for a non-joint qpos layout"
        )
    guard = case.plugin.build_qpos_feasibility_guard(
        model=model,
        config_path=case.joint_limit_config_asset,
    )
    result = guard.evaluate(
        MotionCommand(timestamp_s=0.0, joint=JointCommand(joint_angles_rad=qpos)),
        current_qpos_rad=qpos,
    )
    if not result.accepted:
        raise AssertionError(f"{case.case_id}: qpos is outside the configured feasibility contract")


def snapshot_at_case_qpos(
    case: RobotRuntimePluginConformanceCase,
    consistency: MuJoCoEndpointConsistencyCase,
) -> MuJoCoState:
    """Apply direct position through the safe backend boundary and snapshot it."""

    simulator = HeadlessMuJoCoSimulator.from_model_path(
        case.model_asset,
        initial_keyframe_name=case.home_keyframe_name,
    )
    if consistency.qpos_applier is not None:
        consistency.qpos_applier(simulator, consistency.qpos)
    else:
        joint_names = inspect_mujoco_model(simulator.model).joint_names
        if len(consistency.qpos) != len(joint_names):
            raise AssertionError(
                f"{case.case_id}/{consistency.case_id}: a qpos_applier is required for this model layout"
            )
        simulator.apply_qpos_command(JointCommand(joint_angles_rad=consistency.qpos))
    return simulator.snapshot()


def endpoint_position_from_state(
    state: MuJoCoState,
    *,
    site_name: str,
) -> Vector3:
    for site in state.sites:
        if site.name == site_name:
            return site.position_m
    raise AssertionError(f"declared endpoint site is missing from snapshot: {site_name!r}")


__all__ = [
    "FailClosedCase",
    "InverseKinematicsRoundTripCase",
    "KnownForwardKinematicsCase",
    "MuJoCoEndpointConsistencyCase",
    "RobotRuntimePluginConformanceCase",
    "assert_qpos_feasible",
    "endpoint_position_from_state",
    "load_case_model",
    "snapshot_at_case_qpos",
    "validate_conformance_case_registry",
]
