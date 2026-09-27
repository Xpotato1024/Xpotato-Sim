from __future__ import annotations

import sys
from decimal import Decimal
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest

from fast_arm_core.joint_limits import (
    FastArmJointLimit,
    FastArmJointLimitConfig,
)

from xpotato_sim.runtime.safety import limit_resolution as _limit_resolution_module

from xpotato_sim.plugins.robots.fast_arm.adapter.feasibility import (
    parse_fast_arm_joint_limit_config,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.physical_limit_resolution import (
    build_fast_arm_resolved_bounds_provider,
    fast_arm_toml_limits_to_physical_limits,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.resources import FAST_ARM_JOINT_LIMIT_RESOURCE
from xpotato_sim.runtime.safety.limit_resolution import (
    DEFAULT_COMPARISON_TOLERANCE_RAD,
    FastArmResolvedBoundsProvider,
    JointSpaceConversion,
    LimitParityRecord,
    LimitResolutionResult,
    LimitResolutionStatus,
    ParityStatus,
    ResolvedJointBound,
    authoritative_position_bound_violations,
    fast_arm_mujoco_limits_to_physical_limits,
    project_limit_to_joint_space,
    resolve_joint_space_bounds,
    validate_limit_resolution_identity,
    validate_limit_parity_record,
    validate_limit_resolution_result,
    validate_resolved_joint_bound,
)
from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    effective_limit_status,
    LimitConversionProvenance,
    LimitQuantity,
    LimitSourceProvenance,
    LimitSpace,
    PhysicalLimit,
    canonical_fast_arm_joint_space_frame,
    source_identity,
)


def _source(status: EvidenceStatus, name: str) -> LimitSourceProvenance:
    return LimitSourceProvenance(
        source_kind=name,
        source_id=f"{name}-source",
        revision="rev-1",
        status=status,
        evidence_reference="record-1" if status is EvidenceStatus.AUTHORITATIVE else None,
    )


def _limit(
    *,
    name: str = "joint_1",
    lower: float | None = -1.0,
    upper: float | None = 1.0,
    space: LimitSpace = LimitSpace.JOINT,
    status: EvidenceStatus = EvidenceStatus.PROVISIONAL,
    source_status: EvidenceStatus | None = None,
    source_kind: str = "software_config",
    unit: str = "rad",
    frame: str | None = None,
) -> PhysicalLimit:
    source_status = status if source_status is None else source_status
    return PhysicalLimit(
        name=name,
        quantity=LimitQuantity.POSITION,
        lower=lower,
        upper=upper,
        unit=unit,
        space=space,
        frame=(
            canonical_fast_arm_joint_space_frame()
            if frame is None
            else frame
        ),
        status=status,
        source=_source(source_status, source_kind),
        reason="fixture source is not authoritative" if status is not EvidenceStatus.PROVISIONAL else None,
    )


def _parsed_fast_arm_config() -> FastArmJointLimitConfig:
    config = parse_fast_arm_joint_limit_config(FAST_ARM_JOINT_LIMIT_RESOURCE)
    assert type(config) is FastArmJointLimitConfig
    assert all(type(joint) is FastArmJointLimit for joint in config.joints)
    return config


def _run_fast_arm_projection_helper(helper: object, config: object) -> object:
    if helper is build_fast_arm_resolved_bounds_provider:
        return build_fast_arm_resolved_bounds_provider(config=config)
    return fast_arm_toml_limits_to_physical_limits(config)


class _ExplodingText(str):
    def strip(self, chars: str | None = None) -> str:
        raise AssertionError("text validator must reject before strip")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("text validator must reject before equality")

    def __ne__(self, other: object) -> bool:
        raise AssertionError("text validator must reject before inequality")

    def __hash__(self) -> int:
        raise AssertionError("text validator must reject before hashing")


class _AlwaysEqualText(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return hash(str(self))


class _SpoofedFloat(float):
    def __new__(cls, raw: float, coerced: float) -> "_SpoofedFloat":
        value = float.__new__(cls, raw)
        value._coerced = coerced
        return value

    def __float__(self) -> float:
        return self._coerced


class _SpoofedInt(int):
    def __new__(cls, raw: int, coerced: float) -> "_SpoofedInt":
        value = int.__new__(cls, raw)
        value._coerced = coerced
        return value

    def __float__(self) -> float:
        return self._coerced


class _StatefulTuple(tuple[object, ...]):
    def __new__(
        cls,
        first: tuple[object, ...],
        later: tuple[object, ...],
    ) -> "_StatefulTuple":
        value = tuple.__new__(cls, first)
        value.iteration_count = 0
        value.later = later
        return value

    def __iter__(self):
        self.iteration_count += 1
        return iter(tuple.__iter__(self) if self.iteration_count == 1 else self.later)


def _forged_enum_member(
    enum_type: type[Enum],
    *,
    name: str,
    value: str,
    raw_value: str | None = None,
) -> Enum:
    """constructorとstored validatorを試す偽造str enum memberを作る。"""

    forged = str.__new__(enum_type, value if raw_value is None else raw_value)
    object.__setattr__(forged, "_name_", name)
    object.__setattr__(forged, "_value_", value)
    return forged


def _install_fake_mujoco(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mujoco = ModuleType("mujoco")
    fake_mujoco.mjtObj = SimpleNamespace(mjOBJ_JOINT=object())
    fake_mujoco.mj_name2id = lambda _model, _object_type, _name: 0
    monkeypatch.setitem(sys.modules, "mujoco", fake_mujoco)


def _install_fake_mujoco_with_joint_id(
    monkeypatch: pytest.MonkeyPatch,
    joint_id: object,
) -> None:
    fake_mujoco = ModuleType("mujoco")
    fake_mujoco.mjtObj = SimpleNamespace(mjOBJ_JOINT=object())
    fake_mujoco.mj_name2id = lambda _model, _object_type, _name: joint_id
    monkeypatch.setitem(sys.modules, "mujoco", fake_mujoco)


def _assert_unknown_mujoco_limit(limits: tuple[PhysicalLimit, ...]) -> None:
    assert len(limits) == 1
    limit = limits[0]
    assert limit.name == "joint_1"
    assert limit.status is EvidenceStatus.UNKNOWN
    assert effective_limit_status(limit) is EvidenceStatus.UNKNOWN
    assert limit.lower is None
    assert limit.upper is None
    assert limit.unit == "rad"
    assert limit.space is LimitSpace.JOINT
    assert limit.source.source_kind == "mujoco_jnt_range"
    assert limit.source.source_id == "invalid-model"
    assert limit.source.status is EvidenceStatus.UNKNOWN
    assert limit.reason is not None
    assert limit.reason.startswith("MuJoCo range inspection failed:")
    assert not limit.is_bounded
    assert not limit.is_authoritative

    result = resolve_joint_space_bounds(
        limits,
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.UNKNOWN
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert not result.authoritative


@pytest.mark.parametrize(
    "model",
    (
        SimpleNamespace(jnt_limited=[True], jnt_range=[]),
        SimpleNamespace(jnt_limited=[], jnt_range=[[-1.0, 1.0]]),
    ),
)
def test_mujoco_short_arrays_fail_closed_to_typed_unknown(
    monkeypatch: pytest.MonkeyPatch,
    model: object,
) -> None:
    _install_fake_mujoco(monkeypatch)

    limits = fast_arm_mujoco_limits_to_physical_limits(
        model,
        joint_names=("joint_1",),
    )

    _assert_unknown_mujoco_limit(limits)


def test_mujoco_overflow_numeric_data_fails_closed_to_typed_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mujoco(monkeypatch)

    model = SimpleNamespace(
        jnt_limited=[True],
        jnt_range=[[10**10000, 1.0]],
    )
    limits = fast_arm_mujoco_limits_to_physical_limits(
        model,
        joint_names=("joint_1",),
    )

    _assert_unknown_mujoco_limit(limits)


@pytest.mark.parametrize("joint_id", (0.9, "0", True, -2))
def test_mujoco_invalid_joint_id_scalars_fail_closed_to_typed_unknown(
    monkeypatch: pytest.MonkeyPatch,
    joint_id: object,
) -> None:
    _install_fake_mujoco_with_joint_id(monkeypatch, joint_id)

    limits = fast_arm_mujoco_limits_to_physical_limits(
        SimpleNamespace(jnt_limited=[1], jnt_range=[[-1.0, 1.0]]),
        joint_names=("joint_1",),
    )

    _assert_unknown_mujoco_limit(limits)


def test_mujoco_minus_one_joint_id_is_typed_missing_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mujoco_with_joint_id(monkeypatch, -1)

    limits = fast_arm_mujoco_limits_to_physical_limits(
        SimpleNamespace(jnt_limited=[1], jnt_range=[[-1.0, 1.0]]),
        joint_names=("joint_1",),
    )

    assert len(limits) == 1
    limit = limits[0]
    assert limit.status is EvidenceStatus.UNKNOWN
    assert limit.source.source_id == "model"
    assert limit.reason == "joint is missing from MuJoCo model"
    assert limit.lower is None
    assert limit.upper is None


@pytest.mark.parametrize("limited", (0.5, "1", 2, -1))
def test_mujoco_noncanonical_limited_scalars_fail_closed_to_typed_unknown(
    monkeypatch: pytest.MonkeyPatch,
    limited: object,
) -> None:
    _install_fake_mujoco(monkeypatch)

    limits = fast_arm_mujoco_limits_to_physical_limits(
        SimpleNamespace(jnt_limited=[limited], jnt_range=[[-1.0, 1.0]]),
        joint_names=("joint_1",),
    )

    _assert_unknown_mujoco_limit(limits)


@pytest.mark.parametrize(
    "failure_point",
    ("mj_name2id", "joint_id_index", "jnt_limited", "jnt_range"),
)
def test_mujoco_runtime_errors_at_adapter_boundaries_fail_closed_to_typed_unknown(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    fake_mujoco = ModuleType("mujoco")
    fake_mujoco.mjtObj = SimpleNamespace(mjOBJ_JOINT=object())

    class RuntimeErrorIndex:
        def __index__(self) -> int:
            raise RuntimeError("joint id index failed")

    class RuntimeErrorArray:
        def __getitem__(self, _index: object) -> object:
            raise RuntimeError(f"{failure_point} accessor failed")

    if failure_point == "mj_name2id":
        def mj_name2id(_model: object, _object_type: object, _name: str) -> int:
            raise RuntimeError("mj_name2id failed")

        fake_mujoco.mj_name2id = mj_name2id
        model = SimpleNamespace(jnt_limited=[1], jnt_range=[[-1.0, 1.0]])
    elif failure_point == "joint_id_index":
        fake_mujoco.mj_name2id = (
            lambda _model, _object_type, _name: RuntimeErrorIndex()
        )
        model = SimpleNamespace(jnt_limited=[1], jnt_range=[[-1.0, 1.0]])
    elif failure_point == "jnt_limited":
        fake_mujoco.mj_name2id = lambda _model, _object_type, _name: 0
        model = SimpleNamespace(
            jnt_limited=RuntimeErrorArray(),
            jnt_range=[[-1.0, 1.0]],
        )
    else:
        fake_mujoco.mj_name2id = lambda _model, _object_type, _name: 0
        model = SimpleNamespace(
            jnt_limited=[1],
            jnt_range=RuntimeErrorArray(),
        )

    monkeypatch.setitem(sys.modules, "mujoco", fake_mujoco)
    limits = fast_arm_mujoco_limits_to_physical_limits(
        model,
        joint_names=("joint_1",),
    )

    _assert_unknown_mujoco_limit(limits)


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_mujoco_adapter_does_not_catch_base_exception_types(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    fake_mujoco = ModuleType("mujoco")
    fake_mujoco.mjtObj = SimpleNamespace(mjOBJ_JOINT=object())

    def mj_name2id(_model: object, _object_type: object, _name: str) -> int:
        raise error_type("adapter boundary interruption")

    fake_mujoco.mj_name2id = mj_name2id
    monkeypatch.setitem(sys.modules, "mujoco", fake_mujoco)

    with pytest.raises(error_type):
        fast_arm_mujoco_limits_to_physical_limits(
            SimpleNamespace(jnt_limited=[1], jnt_range=[[-1.0, 1.0]]),
            joint_names=("joint_1",),
        )


@pytest.mark.parametrize(
    "endpoint",
    ("-1.0", True, Decimal("-1.0"), complex(-1.0, 0.0)),
)
def test_mujoco_nonreal_range_endpoint_fails_closed_to_typed_unknown(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: object,
) -> None:
    _install_fake_mujoco(monkeypatch)

    limits = fast_arm_mujoco_limits_to_physical_limits(
        SimpleNamespace(jnt_limited=[1], jnt_range=[[endpoint, 1.0]]),
        joint_names=("joint_1",),
    )

    _assert_unknown_mujoco_limit(limits)


def test_mujoco_numpy_integer_boolean_and_real_scalars_preserve_valid_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    numpy = pytest.importorskip("numpy")
    _install_fake_mujoco_with_joint_id(monkeypatch, numpy.int64(0))

    limits = fast_arm_mujoco_limits_to_physical_limits(
        SimpleNamespace(
            jnt_limited=[numpy.bool_(True)],
            jnt_range=[[numpy.float32(-1.25), numpy.float64(2.5)]],
        ),
        joint_names=("joint_1",),
    )

    assert len(limits) == 1
    limit = limits[0]
    assert limit.status is EvidenceStatus.PROVISIONAL
    assert limit.lower == pytest.approx(-1.25)
    assert limit.upper == pytest.approx(2.5)


def test_negative_gear_sign_reverses_projected_range_and_retains_provenance() -> None:
    source = _limit(name="motor_1", lower=-2.0, upper=4.0, space=LimitSpace.MOTOR)
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=2.0,
        sign=-1.0,
        offset=0.25,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )

    projected = project_limit_to_joint_space(source, relation)

    assert projected.name == "joint_1"
    assert projected.space is LimitSpace.JOINT
    assert projected.lower == pytest.approx(-1.75)
    assert projected.upper == pytest.approx(1.25)
    assert projected.conversion is not None
    assert projected.conversion.relation_id == relation.relation_id


def test_projection_requires_source_and_target_joint_identity() -> None:
    source = _limit(name="motor_1", space=LimitSpace.MOTOR)
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )

    with pytest.raises(ValueError, match="source identity mismatch"):
        project_limit_to_joint_space(
            _limit(name="motor_other", space=LimitSpace.MOTOR),
            relation,
        )
    with pytest.raises(ValueError, match="target joint identity mismatch"):
        project_limit_to_joint_space(source, relation, joint_name="joint_other")


def test_projection_rejects_implicit_unit_conversion() -> None:
    source = _limit(name="motor_1", space=LimitSpace.MOTOR)
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="deg",
    )

    with pytest.raises(ValueError, match="unit mismatch"):
        project_limit_to_joint_space(source, relation)


def test_duplicate_or_unexpected_conversion_identity_is_rejected() -> None:
    first = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    duplicate_source = JointSpaceConversion(
        source_space=LimitSpace.ACTUATOR,
        joint_name="joint_2",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_2/v1",
        unit="rad",
    )
    with pytest.raises(ValueError, match="duplicate conversion relation for source"):
        resolve_joint_space_bounds(
            (),
            expected_joint_names=("joint_1", "joint_2"),
            robot_id="fast_arm-test",
            conversion_relations=(first, duplicate_source),
        )

    unexpected_target = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_other",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_other/v1",
        unit="rad",
    )
    with pytest.raises(ValueError, match="target joint is not expected"):
        resolve_joint_space_bounds(
            (),
            expected_joint_names=("joint_1",),
            robot_id="fast_arm-test",
            conversion_relations=(unexpected_target,),
        )

    duplicate_relation_id = JointSpaceConversion(
        source_space=LimitSpace.ACTUATOR,
        joint_name="joint_2",
        source_name="actuator_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id=first.relation_id,
        unit="rad",
    )
    with pytest.raises(ValueError, match="duplicate conversion relation id"):
        resolve_joint_space_bounds(
            (),
            expected_joint_names=("joint_1", "joint_2"),
            robot_id="fast_arm-test",
            conversion_relations=(first, duplicate_relation_id),
        )


def test_distinct_conversion_sources_may_target_one_joint() -> None:
    motor_relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    actuator_relation = JointSpaceConversion(
        source_space=LimitSpace.ACTUATOR,
        joint_name="joint_1",
        source_name="actuator_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="actuator_1-to-joint_1/v1",
        unit="rad",
    )

    result = resolve_joint_space_bounds(
        (
            _limit(
                name="motor_1",
                space=LimitSpace.MOTOR,
                source_kind="motor_fixture",
            ),
            _limit(
                name="actuator_1",
                space=LimitSpace.ACTUATOR,
                source_kind="actuator_fixture",
            ),
        ),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(motor_relation, actuator_relation),
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL
    assert len(bound.parity) == 2
    assert len(result.conversion_relations) == 2


def test_parity_unit_is_part_of_identity_and_mismatch_fails_closed() -> None:
    degree_source = PhysicalLimit(
        name="joint_1",
        quantity=LimitQuantity.POSITION,
        lower=-1.0,
        upper=1.0,
        unit="deg",
        space=LimitSpace.JOINT,
        frame="fast_arm joint space",
        status=EvidenceStatus.PROVISIONAL,
        source=_source(EvidenceStatus.PROVISIONAL, "degree_profile"),
    )
    result = resolve_joint_space_bounds(
        (_limit(source_kind="joint_limit_toml"), degree_source),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.MISMATCH
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert bound.reason == "limit units disagree"
    assert "unit=rad" in bound.parity[0].source_name
    assert "unit=deg" in bound.parity[1].source_name


def test_single_non_rad_provisional_source_is_unknown_and_unbounded() -> None:
    result = resolve_joint_space_bounds(
        (_limit(unit="deg"),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.UNKNOWN
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert bound.reason is not None
    assert "rad" in bound.reason
    assert "conversion" in bound.reason


def test_matching_non_rad_provisional_sources_are_unknown_and_unbounded() -> None:
    result = resolve_joint_space_bounds(
        (_limit(unit="deg", source_kind="profile"), _limit(unit="deg", source_kind="model")),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.UNKNOWN
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert bound.reason is not None
    assert "rad" in bound.reason


def test_missing_conversion_rejects_even_with_other_joint_source() -> None:
    with pytest.raises(ValueError, match="conversion relation missing"):
        resolve_joint_space_bounds(
            (
                _limit(name="motor_1", space=LimitSpace.MOTOR),
                _limit(name="joint_1"),
            ),
            expected_joint_names=("joint_1",),
            robot_id="fast_arm-test",
        )


def test_equal_provisional_sources_resolve_without_becoming_authoritative() -> None:
    result = resolve_joint_space_bounds(
        (_limit(source_kind="joint_limit_toml"), _limit(source_kind="mujoco_jnt_range")),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL
    assert bound.bounded
    assert not bound.authoritative
    assert [item.status for item in bound.parity] == [ParityStatus.MATCH, ParityStatus.MATCH]


def test_single_rad_provisional_source_remains_resolved_provisional() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL
    assert bound.lower_rad == pytest.approx(-1.0)
    assert bound.upper_rad == pytest.approx(1.0)
    assert bound.reason is None
    assert result.expected_joint_names == ("joint_1",)
    assert result.comparison_tolerance_rad == DEFAULT_COMPARISON_TOLERANCE_RAD


def test_authoritative_and_matching_provisional_source_resolve_authoritatively() -> None:
    result = resolve_joint_space_bounds(
        (
            _limit(status=EvidenceStatus.AUTHORITATIVE, source_kind="lab_document"),
            _limit(source_kind="joint_limit_toml"),
        ),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE
    assert bound.lower_rad == pytest.approx(-1.0)
    assert bound.upper_rad == pytest.approx(1.0)
    assert bound.reason is None
    assert result.authoritative


def test_authoritative_position_bound_violations_are_inclusive() -> None:
    result = resolve_joint_space_bounds(
        (
            _limit(status=EvidenceStatus.AUTHORITATIVE, source_kind="lab_document"),
        ),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    assert authoritative_position_bound_violations(
        result,
        joint_names=("joint_1",),
        qpos_rad=(-1.0,),
    ) == ()
    assert authoritative_position_bound_violations(
        result,
        joint_names=("joint_1",),
        qpos_rad=(1.0,),
    ) == ()
    assert authoritative_position_bound_violations(
        result,
        joint_names=("joint_1",),
        qpos_rad=(1.0001,),
    ) == ("joint_1",)
    assert authoritative_position_bound_violations(
        result,
        joint_names=("joint_1",),
        qpos_rad=(-1.0001,),
    ) == ("joint_1",)


def test_authoritative_position_bound_check_requires_canonical_joint_inventory() -> None:
    result = resolve_joint_space_bounds(
        (
            _limit(name="joint_1", status=EvidenceStatus.AUTHORITATIVE, source_kind="lab_document"),
            _limit(name="joint_2", status=EvidenceStatus.AUTHORITATIVE, source_kind="manufacturer_document"),
        ),
        expected_joint_names=("joint_1", "joint_2"),
        robot_id="fast_arm-test",
    )

    with pytest.raises(ValueError, match="joint order"):
        authoritative_position_bound_violations(
            result,
            joint_names=("joint_2", "joint_1"),
            qpos_rad=(0.0, 0.0),
        )
    with pytest.raises(ValueError, match="qpos dimension"):
        authoritative_position_bound_violations(
            result,
            joint_names=("joint_1", "joint_2"),
            qpos_rad=(0.0,),
        )


def test_authoritative_position_bound_check_rejects_non_authoritative_resolution() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    with pytest.raises(ValueError, match="authoritative resolved bounds"):
        authoritative_position_bound_violations(
            result,
            joint_names=("joint_1",),
            qpos_rad=(0.0,),
        )


def test_conflicting_sources_are_not_resolved() -> None:
    result = resolve_joint_space_bounds(
        (_limit(upper=1.0, source_kind="joint_limit_toml"), _limit(upper=1.1, source_kind="mujoco_jnt_range")),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.MISMATCH
    assert bound.lower_rad is None
    assert bound.upper_rad is None


def test_unknown_source_prevents_authoritative_resolution() -> None:
    result = resolve_joint_space_bounds(
        (_limit(status=EvidenceStatus.UNKNOWN, lower=None, upper=None, source_kind="unknown"), _limit(status=EvidenceStatus.AUTHORITATIVE, source_kind="lab_document")),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    assert result.bound_for("joint_1").status is LimitResolutionStatus.UNKNOWN
    assert not result.authoritative


@pytest.mark.parametrize(
    ("value_status", "source_status", "expected"),
    (
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.PROVISIONAL, EvidenceStatus.PROVISIONAL),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.UNKNOWN, EvidenceStatus.UNKNOWN),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.UNAVAILABLE, EvidenceStatus.UNAVAILABLE),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.CONFLICT, EvidenceStatus.CONFLICT),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.INVALID, EvidenceStatus.INVALID),
        (EvidenceStatus.UNKNOWN, EvidenceStatus.CONFLICT, EvidenceStatus.CONFLICT),
        (EvidenceStatus.CONFLICT, EvidenceStatus.INVALID, EvidenceStatus.INVALID),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL),
    ),
)
def test_effective_status_uses_typed_value_and_source_precedence(
    value_status: EvidenceStatus,
    source_status: EvidenceStatus,
    expected: EvidenceStatus,
) -> None:
    source_kind = "lab_document" if source_status is EvidenceStatus.AUTHORITATIVE else "fixture"
    limit = _limit(
        status=value_status,
        source_status=source_status,
        source_kind=source_kind,
        lower=None if value_status in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        } else -1.0,
        upper=None if value_status in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        } else 1.0,
    )

    assert effective_limit_status(limit) is expected


def test_unknown_source_status_with_matching_authority_cannot_resolve() -> None:
    result = resolve_joint_space_bounds(
        (
            _limit(source_kind="unknown_source", source_status=EvidenceStatus.UNKNOWN),
            _limit(
                status=EvidenceStatus.AUTHORITATIVE,
                source_kind="lab_document",
            ),
        ),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.UNKNOWN
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert bound.parity[0].status is ParityStatus.UNKNOWN
    assert bound.parity[0].source_status is EvidenceStatus.UNKNOWN
    assert not result.authoritative


def test_default_fast_arm_provider_exposes_read_only_unavailable_model_range() -> None:
    config = parse_fast_arm_joint_limit_config(FAST_ARM_JOINT_LIMIT_RESOURCE)
    provider = build_fast_arm_resolved_bounds_provider(config=config)

    assert isinstance(provider, FastArmResolvedBoundsProvider)
    result = provider.resolve()
    assert result.robot_id == "fast_arm"
    assert all(bound.status is LimitResolutionStatus.UNKNOWN for bound in result.bounds)
    assert not result.authoritative
    assert all("joint_limit_toml" in bound.source_names[0] for bound in result.bounds)


def test_toml_projection_preserves_provisional_status() -> None:
    config = parse_fast_arm_joint_limit_config(FAST_ARM_JOINT_LIMIT_RESOURCE)
    limits = fast_arm_toml_limits_to_physical_limits(config)

    assert len(limits) == 4
    assert all(limit.status is EvidenceStatus.PROVISIONAL for limit in limits)
    assert all(limit.source.source_kind == "joint_limit_toml" for limit in limits)


def test_fast_arm_projection_helpers_are_adapter_owned() -> None:
    assert not hasattr(
        _limit_resolution_module,
        "fast_arm_toml_limits_to_physical_limits",
    )
    assert not hasattr(
        _limit_resolution_module,
        "build_fast_arm_resolved_bounds_provider",
    )


def test_fast_arm_projection_rejects_duck_config_before_reading_metadata() -> None:
    class DuckJoint:
        name = "sholder_joint_1"
        lower_rad = -1.0
        upper_rad = 1.0

    config = SimpleNamespace(
        schema_version=1,
        robot="fast_arm",
        model="fast_arm",
        angle_unit="rad",
        status="provisional",
        joints=(DuckJoint(),),
    )
    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        with pytest.raises(TypeError, match="FastArmJointLimitConfig"):
            _run_fast_arm_projection_helper(helper, config)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", 2),
        ("robot", "other_robot"),
        ("model", "other_model"),
        ("angle_unit", "deg"),
        ("status", "authoritative"),
    ),
)
def test_fast_arm_projection_reuses_core_metadata_validation(
    field: str,
    value: object,
) -> None:
    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        config = _parsed_fast_arm_config()
        object.__setattr__(config, field, value)
        with pytest.raises(ValueError):
            _run_fast_arm_projection_helper(helper, config)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", _SpoofedInt(1, 2)),
        ("robot", _ExplodingText("fast_arm")),
        ("model", _ExplodingText("fast_arm")),
        ("angle_unit", _ExplodingText("rad")),
        ("status", _ExplodingText("provisional")),
    ),
)
def test_fast_arm_projection_rejects_config_primitive_subclasses(
    field: str,
    value: object,
) -> None:
    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        config = _parsed_fast_arm_config()
        object.__setattr__(config, field, value)
        with pytest.raises(TypeError, match="built-in"):
            _run_fast_arm_projection_helper(helper, config)


def test_fast_arm_projection_rejects_joint_tuple_subclass_before_iteration() -> None:
    config = _parsed_fast_arm_config()
    stateful = _StatefulTuple(config.joints, ())
    object.__setattr__(config, "joints", stateful)

    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        with pytest.raises(TypeError, match="built-in tuple"):
            _run_fast_arm_projection_helper(helper, config)
    assert stateful.iteration_count == 0


def test_fast_arm_projection_rejects_nested_joint_type_before_constructor() -> None:
    config = _parsed_fast_arm_config()
    object.__setattr__(config, "joints", (object(),))

    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        with pytest.raises(TypeError, match="FastArmJointLimit"):
            _run_fast_arm_projection_helper(helper, config)


@pytest.mark.parametrize(
    "mutation",
    ("joint_name", "partial_joints"),
)
def test_fast_arm_projection_reuses_core_joint_inventory_validation(
    mutation: str,
) -> None:
    config = _parsed_fast_arm_config()
    if mutation == "joint_name":
        object.__setattr__(config.joints[0], "name", "forged_joint")
    else:
        object.__setattr__(config, "joints", config.joints[:-1])

    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        with pytest.raises(ValueError):
            _run_fast_arm_projection_helper(helper, config)


def test_fast_arm_projection_rejects_nested_numeric_subclass_before_core_hooks() -> None:
    config = _parsed_fast_arm_config()
    object.__setattr__(
        config.joints[0],
        "lower_rad",
        _SpoofedFloat(-1.0, -999.0),
    )

    for helper in (
        fast_arm_toml_limits_to_physical_limits,
        build_fast_arm_resolved_bounds_provider,
    ):
        with pytest.raises(TypeError, match="built-in int or float"):
            _run_fast_arm_projection_helper(helper, config)


def test_fast_arm_projection_preserves_core_integer_endpoint_inputs() -> None:
    parsed = _parsed_fast_arm_config()
    config = FastArmJointLimitConfig(
        schema_version=parsed.schema_version,
        robot=parsed.robot,
        model=parsed.model,
        angle_unit=parsed.angle_unit,
        status=parsed.status,
        joints=tuple(
            FastArmJointLimit(
                name=joint.name,
                lower_rad=-1,
                upper_rad=1,
            )
            for joint in parsed.joints
        ),
    )

    limits = fast_arm_toml_limits_to_physical_limits(config)

    assert all(type(limit.lower) is float for limit in limits)
    assert all(type(limit.upper) is float for limit in limits)
    assert all(limit.lower == -1.0 and limit.upper == 1.0 for limit in limits)


def test_fast_arm_provider_requires_explicit_profile_order() -> None:
    config = _parsed_fast_arm_config()

    with pytest.raises(ValueError, match="canonical joint names"):
        build_fast_arm_resolved_bounds_provider(
            config=config,
            profile_joint_names=(),
        )
    with pytest.raises(ValueError, match="canonical fast_arm joint order"):
        build_fast_arm_resolved_bounds_provider(
            config=config,
            profile_joint_names=tuple(reversed(config.joint_names)),
        )


def test_fast_arm_provider_rejects_extra_profile_bound_name() -> None:
    config = _parsed_fast_arm_config()

    with pytest.raises(ValueError, match="canonical fast_arm joint order"):
        build_fast_arm_resolved_bounds_provider(
            config=config,
            profile_bounds_rad={"extra_joint": (-1.0, 1.0)},
        )


def test_fast_arm_provider_preserves_valid_bounded_provisional_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mujoco(monkeypatch)
    config = _parsed_fast_arm_config()
    model = SimpleNamespace(
        jnt_limited=[1],
        jnt_range=[[-3.141592653589793, 3.141592653589793]],
    )

    provider = build_fast_arm_resolved_bounds_provider(
        config=config,
        model=model,
    )

    result = provider.resolve()
    assert all(
        bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL
        for bound in result.bounds
    )
    assert all(bound.bounded for bound in result.bounds)
    assert not result.authoritative


def test_invalid_conversion_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        JointSpaceConversion(
            source_space=LimitSpace.MOTOR,
            joint_name="joint_1",
            source_name="motor_1",
            gear_ratio=0.0,
            sign=1.0,
            offset=0.0,
            relation_id="invalid",
            unit="rad",
        )
    with pytest.raises(ValueError, match="either -1 or 1"):
        JointSpaceConversion(
            source_space=LimitSpace.MOTOR,
            joint_name="joint_1",
            source_name="motor_1",
            gear_ratio=1.0,
            sign=0.0,
            offset=0.0,
            relation_id="invalid",
            unit="rad",
        )


@pytest.mark.parametrize(
    ("source_value", "gear_ratio", "offset"),
    (
        (1e308, 1e-308, 0.0),
        (-1e308, 1e-308, 0.0),
        (1.0, 5e-324, 0.0),
        (1e308, 1.0, 1e308),
    ),
)
def test_source_to_joint_rejects_nonfinite_projection_result(
    source_value: float,
    gear_ratio: float,
    offset: float,
) -> None:
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=gear_ratio,
        sign=1.0,
        offset=offset,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )

    with pytest.raises(ValueError, match="joint value must be finite"):
        relation.source_to_joint(source_value)


def test_source_to_joint_preserves_finite_projection() -> None:
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=2.0,
        sign=-1.0,
        offset=0.25,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )

    assert relation.source_to_joint(4.0) == pytest.approx(-1.75)


def test_joint_source_space_conversion_is_rejected() -> None:
    with pytest.raises(ValueError, match="source_space must be motor or actuator"):
        JointSpaceConversion(
            source_space=LimitSpace.JOINT,
            joint_name="joint_1",
            source_name="joint_1",
            gear_ratio=1.0,
            sign=1.0,
            offset=0.0,
            relation_id="invalid-joint-conversion",
            unit="rad",
        )


def test_resolution_enum_constructors_normalize_builtin_strings() -> None:
    source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    relation = JointSpaceConversion(
        source_space="motor",  # type: ignore[arg-type]
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    assert relation.source_space is LimitSpace.MOTOR

    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=source_identity(source, unit="rad"),
        status="match",  # type: ignore[arg-type]
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=source,
        source_status="provisional",  # type: ignore[arg-type]
    )
    assert parity.status is ParityStatus.MATCH
    assert parity.source_status is EvidenceStatus.PROVISIONAL

    bound = ResolvedJointBound(
        joint_name="joint_1",
        lower_rad=-1.0,
        upper_rad=1.0,
        status="resolved_provisional",  # type: ignore[arg-type]
        source_names=(parity.source_name,),
        parity=(parity,),
    )
    assert bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL


def test_resolution_constructors_reject_forged_enum_members() -> None:
    source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    source_name = source_identity(source, unit="rad")

    forged_space = _forged_enum_member(
        LimitSpace,
        name="MOTOR",
        value="motor",
    )
    with pytest.raises(ValueError, match="source_space must be motor or actuator"):
        JointSpaceConversion(
            source_space=forged_space,  # type: ignore[arg-type]
            joint_name="joint_1",
            source_name="motor_1",
            gear_ratio=1.0,
            sign=1.0,
            offset=0.0,
            relation_id="motor_1-to-joint_1/v1",
            unit="rad",
        )

    forged_parity_status = _forged_enum_member(
        ParityStatus,
        name="MATCH",
        value="match",
    )
    with pytest.raises(ValueError, match="valid ParityStatus"):
        LimitParityRecord(
            joint_name="joint_1",
            source_name=source_name,
            status=forged_parity_status,  # type: ignore[arg-type]
            lower=-1.0,
            upper=1.0,
            unit="rad",
            source=source,
            source_status=EvidenceStatus.PROVISIONAL,
        )

    forged_resolution_status = _forged_enum_member(
        LimitResolutionStatus,
        name="RESOLVED_PROVISIONAL",
        value="resolved_provisional",
    )
    with pytest.raises(ValueError, match="valid LimitResolutionStatus"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=forged_resolution_status,  # type: ignore[arg-type]
            source_names=(source_name,),
            parity=(),
        )


def test_resolution_stored_enum_fields_reject_raw_strings_and_forged_members() -> None:
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    for bad in (
        "motor",
        _forged_enum_member(LimitSpace, name="MOTOR", value="motor"),
    ):
        object.__setattr__(relation, "source_space", bad)
        with pytest.raises(TypeError, match="canonical"):
            _limit_resolution_module._validate_joint_conversion(relation)

    source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=source_identity(source, unit="rad"),
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=source,
        source_status=EvidenceStatus.PROVISIONAL,
    )
    for bad in (
        "match",
        _forged_enum_member(ParityStatus, name="MATCH", value="match"),
    ):
        object.__setattr__(parity, "status", bad)
        with pytest.raises(TypeError, match="canonical"):
            validate_limit_parity_record(parity)

    object.__setattr__(parity, "status", ParityStatus.MATCH)
    for bad in (
        "provisional",
        _forged_enum_member(
            EvidenceStatus,
            name="PROVISIONAL",
            value="provisional",
        ),
    ):
        object.__setattr__(parity, "source_status", bad)
        with pytest.raises(TypeError, match="canonical"):
            validate_limit_parity_record(parity)

    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bounds[0]
    for bad in (
        "resolved_provisional",
        _forged_enum_member(
            LimitResolutionStatus,
            name="RESOLVED_PROVISIONAL",
            value="resolved_provisional",
        ),
    ):
        object.__setattr__(bound, "status", bad)
        with pytest.raises(TypeError, match="canonical"):
            validate_resolved_joint_bound(bound)


def test_actuator_projection_applies_one_explicit_conversion() -> None:
    source = _limit(
        name="actuator_1",
        lower=-2.0,
        upper=4.0,
        space=LimitSpace.ACTUATOR,
    )
    relation = JointSpaceConversion(
        source_space=LimitSpace.ACTUATOR,
        joint_name="joint_1",
        source_name="actuator_1",
        gear_ratio=2.0,
        sign=1.0,
        offset=0.25,
        relation_id="actuator_1-to-joint_1/v1",
        unit="rad",
    )

    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(relation,),
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL
    assert bound.lower_rad == pytest.approx(-0.75)
    assert bound.upper_rad == pytest.approx(2.25)
    assert bound.parity[0].source_status is EvidenceStatus.PROVISIONAL
    assert bound.to_dict()["parity"][0]["source"]["status"] == "provisional"


def test_projected_provenance_must_match_typed_relation_parameters() -> None:
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    source = _limit(
        name="motor_1",
        space=LimitSpace.MOTOR,
        status=EvidenceStatus.AUTHORITATIVE,
        source_status=EvidenceStatus.AUTHORITATIVE,
        source_kind="manufacturer_document",
    )
    forged_relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=2.0,
        sign=1.0,
        offset=0.0,
        relation_id=relation.relation_id,
        unit="rad",
    )
    forged_projected = project_limit_to_joint_space(source, forged_relation)
    canonical_source = _limit(
        name="motor_1",
        space=LimitSpace.MOTOR,
        status=EvidenceStatus.AUTHORITATIVE,
        source_status=EvidenceStatus.AUTHORITATIVE,
        source_kind="lab_document",
    )
    with pytest.raises(ValueError, match="conversion relation binding"):
        resolve_joint_space_bounds(
            (
                canonical_source,
                forged_projected,
            ),
            expected_joint_names=("joint_1",),
            robot_id="fast_arm-test",
            conversion_relations=(relation,),
        )


def test_joint_values_keep_identity_provenance_without_reconversion() -> None:
    source = _limit(name="joint_1", space=LimitSpace.JOINT)

    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.lower_rad == pytest.approx(source.lower)
    assert bound.upper_rad == pytest.approx(source.upper)
    assert bound.parity[0].source is source.source


def test_identical_rad_ranges_with_different_joint_space_frames_are_mismatched() -> None:
    result = resolve_joint_space_bounds(
        (
            _limit(name="joint_1", source_kind="frame_a", frame="frame-A"),
            _limit(name="joint_1", source_kind="frame_b", frame="frame-B"),
        ),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.MISMATCH
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert not result.authoritative
    assert [item.frame for item in bound.parity] == ["frame-A", "frame-B"]
    assert all(item.status is ParityStatus.MISMATCH for item in bound.parity)
    assert all(item.reason is not None and "canonical" in item.reason for item in bound.parity)


def test_single_wrong_joint_space_frame_is_typed_mismatch_and_unbounded() -> None:
    result = resolve_joint_space_bounds(
        (_limit(frame="wrong-joint-frame"),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.MISMATCH
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert not bound.authoritative
    assert not bound.bounded
    assert bound.parity[0].frame == "wrong-joint-frame"
    assert bound.reason is not None and "canonical" in bound.reason


def test_canonical_joint_space_frame_resolves_and_is_retained_in_parity() -> None:
    frame = canonical_fast_arm_joint_space_frame()
    result = resolve_joint_space_bounds(
        (_limit(frame=frame),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.RESOLVED_PROVISIONAL
    assert bound.lower_rad == pytest.approx(-1.0)
    assert bound.upper_rad == pytest.approx(1.0)
    assert bound.parity[0].frame == frame
    assert bound.to_dict()["parity"][0]["frame"] == frame


def test_authoritative_joint_limit_with_wrong_frame_cannot_resolve_authoritatively() -> None:
    source = _limit(
        status=EvidenceStatus.AUTHORITATIVE,
        source_status=EvidenceStatus.AUTHORITATIVE,
        source_kind="lab_document",
        frame="wrong-joint-frame",
    )

    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )

    bound = result.bound_for("joint_1")
    assert bound.status is LimitResolutionStatus.MISMATCH
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    assert not result.authoritative


def test_resolved_bound_constructor_rejects_noncanonical_parity_frame() -> None:
    source = _source(EvidenceStatus.PROVISIONAL, "frame_b")
    identity = source_identity(source, unit="rad")
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=source,
        frame="frame-B",
    )

    with pytest.raises(ValueError, match="parity frames"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
            source_names=(identity,),
            parity=(parity,),
        )


def test_resolved_bound_requires_non_empty_matching_typed_parity() -> None:
    with pytest.raises(ValueError, match="typed source provenance"):
        LimitParityRecord(
            joint_name="joint_1",
            source_name="fixture",
            status=ParityStatus.MATCH,
            lower=-1.0,
            upper=1.0,
            unit="rad",
        )

    provisional_source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    provisional_identity = source_identity(provisional_source, unit="rad")
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=provisional_identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=provisional_source,
    )

    with pytest.raises(ValueError, match="parity must be non-empty"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
            source_names=(provisional_identity,),
            parity=(),
        )
    with pytest.raises(ValueError, match="equal length"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
            source_names=(provisional_identity, "other"),
            parity=(parity,),
        )
    with pytest.raises(ValueError, match="exactly match"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
            source_names=("other",),
            parity=(parity,),
        )
    mismatched_joint = LimitParityRecord(
        joint_name="joint_2",
        source_name=provisional_identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=provisional_source,
    )
    with pytest.raises(ValueError, match="joint identity"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
            source_names=(provisional_identity,),
            parity=(mismatched_joint,),
        )


def test_authoritative_bound_requires_typed_authoritative_source() -> None:
    provisional_source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    provisional_identity = source_identity(provisional_source, unit="rad")
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=provisional_identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=provisional_source,
    )

    with pytest.raises(ValueError, match="typed authoritative source provenance"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
            source_names=(provisional_identity,),
            parity=(parity,),
        )

    provisional_parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=provisional_identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=provisional_source,
    )
    with pytest.raises(ValueError, match="typed authoritative source provenance"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
            source_names=(provisional_identity,),
            parity=(provisional_parity,),
        )


def test_limit_result_requires_unique_expected_joint_coverage() -> None:
    provisional_source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    provisional_identity = source_identity(provisional_source, unit="rad")
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=provisional_identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=provisional_source,
    )
    bound = ResolvedJointBound(
        joint_name="joint_1",
        lower_rad=-1.0,
        upper_rad=1.0,
        status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
        source_names=(provisional_identity,),
        parity=(parity,),
    )

    with pytest.raises(ValueError, match="expected_joint_names must be non-empty"):
        LimitResolutionResult(
            1,
            "fast_arm-test",
            (bound,),
            (),
            expected_joint_names=(),
        )
    with pytest.raises(TypeError):
        LimitResolutionResult(
            1,
            "fast_arm-test",
            (bound,),
            (),
        )
    with pytest.raises(TypeError, match="expected_joint_names"):
        LimitResolutionResult(
            1,
            "fast_arm-test",
            (bound,),
            (),
            expected_joint_names=None,
        )
    with pytest.raises(ValueError, match="expected_joint_names must be unique"):
        LimitResolutionResult(
            1,
            "fast_arm-test",
            (bound,),
            (),
            expected_joint_names=("joint_1", "joint_1"),
        )
    with pytest.raises(ValueError, match="exactly cover"):
        LimitResolutionResult(
            1,
            "fast_arm-test",
            (bound,),
            (),
            expected_joint_names=("joint_2",),
        )


def test_resolved_bound_rejects_non_rad_parity_units() -> None:
    provisional_source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    provisional_identity = source_identity(provisional_source, unit="deg")
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=provisional_identity,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="deg",
        source=provisional_source,
    )

    with pytest.raises(ValueError, match="parity units must be rad"):
        ResolvedJointBound(
            joint_name="joint_1",
            lower_rad=-1.0,
            upper_rad=1.0,
            status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
            source_names=(provisional_identity,),
            parity=(parity,),
        )


def test_parity_source_name_must_be_derived_from_typed_source() -> None:
    source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    with pytest.raises(ValueError, match="typed source identity"):
        LimitParityRecord(
            joint_name="joint_1",
            source_name="forged-source-name",
            status=ParityStatus.MATCH,
            lower=-1.0,
            upper=1.0,
            unit="rad",
            source=source,
        )


@pytest.mark.parametrize("tolerance_rad", (0.0, 1e-6, 1e9))
def test_canonical_tolerance_rejects_caller_override(tolerance_rad: float) -> None:
    with pytest.raises(ValueError, match="canonical default"):
        resolve_joint_space_bounds(
            (_limit(),),
            expected_joint_names=("joint_1",),
            robot_id="fast_arm-test",
            tolerance_rad=tolerance_rad,
        )


def test_conversion_relation_must_have_concrete_identity() -> None:
    with pytest.raises(ValueError, match="concrete identity"):
        JointSpaceConversion(
            source_space=LimitSpace.MOTOR,
            joint_name="joint_1",
            source_name="motor_1",
            gear_ratio=1.0,
            sign=1.0,
            offset=0.0,
            relation_id="unknown",
            unit="rad",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_name", "fixture"),
        ("source_name", "test_fixture"),
        ("source_name", "synthetic"),
        ("source_name", "n_a"),
        ("source_name", "not_available"),
        ("relation_id", "fixture"),
        ("relation_id", "test_fixture"),
        ("relation_id", "synthetic"),
        ("relation_id", "n_a"),
        ("relation_id", "not_available"),
    ),
)
def test_conversion_relation_uses_canonical_concrete_identity_validator(
    field: str,
    value: str,
) -> None:
    kwargs = {
        "source_space": LimitSpace.MOTOR,
        "joint_name": "joint_1",
        "source_name": "motor_1",
        "gear_ratio": 1.0,
        "sign": 1.0,
        "offset": 0.0,
        "relation_id": "motor_1-to-joint_1/v1",
        "unit": "rad",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="concrete identity"):
        JointSpaceConversion(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "identity",
    (
        "unknown",
        "UNKNOWN",
        "unavailable",
        "n/a",
        "none",
        "null",
        "placeholder",
        "sample",
        "synthetic",
        "fixture",
        "test_fixture",
        "fixture_data",
        "not_available",
    ),
)
def test_resolution_identity_validator_rejects_placeholders(identity: str) -> None:
    with pytest.raises(ValueError, match="concrete identity"):
        validate_limit_resolution_identity("robot_id", identity)
    with pytest.raises(ValueError, match="concrete identity"):
        validate_limit_resolution_identity("expected_joint_name", identity)


@pytest.mark.parametrize("identity", ("", " ", "\t"))
def test_resolution_identity_validator_rejects_empty_or_whitespace(
    identity: str,
) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        validate_limit_resolution_identity("robot_id", identity)


def test_resolution_identity_validator_preserves_concrete_identity() -> None:
    assert validate_limit_resolution_identity("robot_id", "fast_arm-test") == "fast_arm-test"
    assert validate_limit_resolution_identity("expected_joint_name", "joint_1") == "joint_1"


@pytest.mark.parametrize("field", ("robot_id", "expected_joint_names"))
def test_result_constructor_rejects_placeholder_identity(field: str) -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    kwargs: dict[str, object] = {
        "schema_version": result.schema_version,
        "robot_id": result.robot_id,
        "bounds": result.bounds,
        "conversion_relations": result.conversion_relations,
        "expected_joint_names": result.expected_joint_names,
    }
    kwargs[field] = "unknown" if field == "robot_id" else ("unknown",)
    with pytest.raises(ValueError, match="concrete identity"):
        LimitResolutionResult(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("identity", ("unknown", "UNKNOWN", "n/a", "placeholder"))
def test_resolver_rejects_placeholder_robot_and_joint_identity(identity: str) -> None:
    with pytest.raises(ValueError, match="concrete identity"):
        resolve_joint_space_bounds(
            (_limit(),),
            expected_joint_names=("joint_1",),
            robot_id=identity,
        )
    with pytest.raises(ValueError, match="concrete identity"):
        resolve_joint_space_bounds(
            (_limit(),),
            expected_joint_names=(identity,),
            robot_id="fast_arm-test",
        )


@pytest.mark.parametrize("identity", ("unknown", "UNKNOWN", "n/a", "placeholder"))
def test_fast_arm_factory_rejects_placeholder_joint_identity(identity: str) -> None:
    config = parse_fast_arm_joint_limit_config(FAST_ARM_JOINT_LIMIT_RESOURCE)
    with pytest.raises(ValueError, match="concrete identity"):
        build_fast_arm_resolved_bounds_provider(
            config=config,
            profile_joint_names=(identity,),
        )


def test_provider_constructor_and_accessor_revalidate_resolution_identity() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    object.__setattr__(result, "robot_id", "unknown")
    with pytest.raises(ValueError, match="concrete identity"):
        FastArmResolvedBoundsProvider(result)

    valid_result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    provider = FastArmResolvedBoundsProvider(valid_result)
    with pytest.raises(ValueError, match="concrete identity"):
        provider.bound_for("unknown")


def test_provider_bound_for_revalidates_result_before_delegation() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    provider = FastArmResolvedBoundsProvider(result)

    class ForgedResult:
        def bound_for(self, joint_name: str) -> object:
            return f"forged:{joint_name}"

    object.__setattr__(provider, "result", ForgedResult())
    with pytest.raises(TypeError, match="result must be LimitResolutionResult"):
        provider.bound_for("joint_1")

    nested_tampered_result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    nested_provider = FastArmResolvedBoundsProvider(nested_tampered_result)
    object.__setattr__(nested_tampered_result, "bounds", (object(),))
    with pytest.raises(TypeError, match="bound must be ResolvedJointBound"):
        nested_provider.bound_for("joint_1")


@pytest.mark.parametrize(
    ("field", "later"),
    (
        ("source_names", ("joint_2",)),
        ("parity", (object(),)),
    ),
)
def test_bound_stored_tuples_require_builtin_tuple_before_iteration(
    field: str,
    later: tuple[object, ...],
) -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bounds[0]
    stateful = _StatefulTuple(getattr(bound, field), later)
    object.__setattr__(bound, field, stateful)

    with pytest.raises(TypeError, match="built-in tuple"):
        validate_resolved_joint_bound(bound)
    assert stateful.iteration_count == 0


@pytest.mark.parametrize(
    ("field", "later"),
    (
        ("bounds", (object(),)),
        ("conversion_relations", (object(),)),
        ("expected_joint_names", ("joint_2",)),
    ),
)
def test_result_stored_tuples_require_builtin_tuple_before_iteration(
    field: str,
    later: tuple[object, ...],
) -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    stateful = _StatefulTuple(getattr(result, field), later)
    object.__setattr__(result, field, stateful)

    with pytest.raises(TypeError, match="built-in tuple"):
        validate_limit_resolution_result(result)
    assert stateful.iteration_count == 0


@pytest.mark.parametrize("field", ("lower", "upper"))
def test_parity_validator_rejects_spoofed_stored_float_before_float_hook(
    field: str,
) -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    parity = result.bounds[0].parity[0]
    object.__setattr__(parity, field, _SpoofedFloat(999.0, -1.0))

    with pytest.raises(TypeError, match="canonical float"):
        validate_limit_parity_record(parity)


def test_bound_validator_rejects_spoofed_stored_float_before_float_hook() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bounds[0]
    object.__setattr__(bound, "lower_rad", _SpoofedFloat(999.0, -1.0))

    assert not bound.bounded
    with pytest.raises(TypeError, match="canonical float"):
        validate_resolved_joint_bound(bound)
    with pytest.raises(TypeError, match="canonical float"):
        bound.to_dict()


@pytest.mark.parametrize("numeric_type", ("float", "int"))
def test_conversion_sign_requires_stored_canonical_float_before_operation(
    numeric_type: str,
) -> None:
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    result = resolve_joint_space_bounds(
        (_limit(name="motor_1", space=LimitSpace.MOTOR),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(relation,),
    )
    spoofed_sign: object = (
        _SpoofedFloat(-1.0, 1.0)
        if numeric_type == "float"
        else _SpoofedInt(-1, 1.0)
    )
    object.__setattr__(relation, "sign", spoofed_sign)

    with pytest.raises(TypeError, match="canonical float"):
        relation.source_to_joint(2.0)
    with pytest.raises(TypeError, match="canonical float"):
        validate_limit_resolution_result(result)


@pytest.mark.parametrize(
    "identity",
    (
        "unknown",
        "UNKNOWN",
        "unavailable",
        "N/A",
        "none",
        "placeholder",
        "fixture_data",
    ),
)
def test_resolver_rejects_bypassed_placeholder_limit_identity(
    identity: str,
) -> None:
    limit = _limit()
    object.__setattr__(limit, "name", identity)

    with pytest.raises(ValueError, match="concrete identity"):
        resolve_joint_space_bounds(
            (limit,),
            expected_joint_names=("joint_1",),
            robot_id="fast_arm-test",
        )


@pytest.mark.parametrize(
    "identity",
    ("unknown", "UNKNOWN", "unavailable", "N/A", "none", "fixture_data"),
)
def test_result_and_provider_lookup_reject_placeholder_identity(
    identity: str,
) -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    provider = FastArmResolvedBoundsProvider(result)

    with pytest.raises(ValueError, match="concrete identity"):
        result.bound_for(identity)
    with pytest.raises(ValueError, match="concrete identity"):
        provider.bound_for(identity)


def test_result_and_provider_lookup_reject_str_subclass_identity() -> None:
    class OverridingIdentity(str):
        def strip(self, chars: str | None = None) -> str:
            raise AssertionError("identity validator must reject before strip")

        def casefold(self) -> str:
            raise AssertionError("identity validator must reject before casefold")

    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    provider = FastArmResolvedBoundsProvider(result)
    identity = OverridingIdentity("unknown")

    with pytest.raises(ValueError, match="built-in string"):
        result.bound_for(identity)
    with pytest.raises(ValueError, match="built-in string"):
        provider.bound_for(identity)


@pytest.mark.parametrize("field", ("source_name", "unit", "frame", "reason"))
def test_resolution_text_boundary_rejects_str_subclass_before_hooks(
    field: str,
) -> None:
    source = _source(EvidenceStatus.AUTHORITATIVE, "lab_document")
    kwargs: dict[str, object] = {
        "joint_name": "joint_1",
        "source_name": source_identity(source, unit="rad"),
        "status": ParityStatus.MISMATCH,
        "lower": None,
        "upper": None,
        "unit": "rad",
        "reason": "ranges disagree",
        "source": source,
        "frame": canonical_fast_arm_joint_space_frame(),
    }
    kwargs[field] = _ExplodingText("unknown" if field != "frame" else "wrong-frame")

    with pytest.raises(ValueError, match="built-in string"):
        LimitParityRecord(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ("source_name", "frame"))
def test_authoritative_parity_rejects_always_equal_str_subclass_identity(
    field: str,
) -> None:
    source = _source(EvidenceStatus.AUTHORITATIVE, "lab_document")
    kwargs: dict[str, object] = {
        "joint_name": "joint_1",
        "source_name": source_identity(source, unit="rad"),
        "status": ParityStatus.MATCH,
        "lower": -1.0,
        "upper": 1.0,
        "unit": "rad",
        "source": source,
        "frame": canonical_fast_arm_joint_space_frame(),
    }
    kwargs[field] = _AlwaysEqualText("unknown" if field == "source_name" else "wrong-frame")

    with pytest.raises(ValueError, match="built-in string"):
        LimitParityRecord(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    (("status", "mismatch"), ("source_status", "authoritative")),
)
def test_resolution_enum_boundary_rejects_str_subclass_before_hooks(
    field: str,
    value: str,
) -> None:
    source = _source(EvidenceStatus.AUTHORITATIVE, "lab_document")
    kwargs: dict[str, object] = {
        "joint_name": "joint_1",
        "source_name": source_identity(source, unit="rad"),
        "status": ParityStatus.MISMATCH,
        "lower": None,
        "upper": None,
        "unit": "rad",
        "reason": "ranges disagree",
        "source": source,
        "source_status": source.status,
        "frame": canonical_fast_arm_joint_space_frame(),
    }
    kwargs[field] = _ExplodingText(value)

    with pytest.raises(ValueError, match="valid"):
        LimitParityRecord(**kwargs)  # type: ignore[arg-type]


def test_resolution_deep_validator_rejects_nested_str_subclass_before_hooks() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bounds[0]

    with pytest.raises(ValueError, match="built-in string"):
        ResolvedJointBound(
            joint_name=bound.joint_name,
            lower_rad=bound.lower_rad,
            upper_rad=bound.upper_rad,
            status=bound.status,
            source_names=(_ExplodingText("unknown"),),
            parity=bound.parity,
            comparison_tolerance_rad=bound.comparison_tolerance_rad,
        )

    object.__setattr__(bound, "source_names", (_ExplodingText("unknown"),))

    with pytest.raises(ValueError, match="built-in string"):
        validate_resolved_joint_bound(bound)

    valid_result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    valid_bound = valid_result.bounds[0]
    parity = valid_bound.parity[0]
    object.__setattr__(parity, "source_name", _ExplodingText("unknown"))
    with pytest.raises(ValueError, match="built-in string"):
        ResolvedJointBound(
            joint_name=valid_bound.joint_name,
            lower_rad=valid_bound.lower_rad,
            upper_rad=valid_bound.upper_rad,
            status=valid_bound.status,
            source_names=valid_bound.source_names,
            parity=(parity,),
            comparison_tolerance_rad=valid_bound.comparison_tolerance_rad,
        )


def test_result_constructor_revalidates_bounds_before_joint_name_inventory() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bounds[0]
    object.__setattr__(bound, "joint_name", _ExplodingText("joint_1"))

    with pytest.raises(ValueError, match="built-in string"):
        LimitResolutionResult(
            schema_version=result.schema_version,
            robot_id=result.robot_id,
            bounds=result.bounds,
            conversion_relations=result.conversion_relations,
            expected_joint_names=result.expected_joint_names,
        )


def test_result_rejects_relation_source_name_mismatch_with_projected_provenance() -> None:
    source = _source(EvidenceStatus.PROVISIONAL, "fixture")
    source_identity_value = source_identity(source, unit="rad")
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    mismatched_conversion = LimitConversionProvenance.projected(
        source_space=LimitSpace.MOTOR,
        relation_id=relation.relation_id,
        gear_ratio=relation.gear_ratio,
        sign=relation.sign,
        offset=relation.offset,
        source_name="motor_2",
    )
    parity = LimitParityRecord(
        joint_name="joint_1",
        source_name=source_identity_value,
        status=ParityStatus.MATCH,
        lower=-1.0,
        upper=1.0,
        unit="rad",
        source=source,
        conversion=mismatched_conversion,
    )
    bound = ResolvedJointBound(
        joint_name="joint_1",
        lower_rad=-1.0,
        upper_rad=1.0,
        status=LimitResolutionStatus.RESOLVED_PROVISIONAL,
        source_names=(source_identity_value,),
        parity=(parity,),
    )
    with pytest.raises(ValueError, match="conversion relation binding"):
        LimitResolutionResult(
            1,
            "fast_arm-test",
            (bound,),
            (relation,),
            expected_joint_names=("joint_1",),
        )


def test_public_p1_validators_reject_subclass_bypasses() -> None:
    class RelationSubclass(JointSpaceConversion):
        pass

    class ParitySubclass(LimitParityRecord):
        pass

    class BoundSubclass(ResolvedJointBound):
        pass

    class ResultSubclass(LimitResolutionResult):
        pass

    for dto_type, validator in (
        (ParitySubclass, validate_limit_parity_record),
        (BoundSubclass, validate_resolved_joint_bound),
        (ResultSubclass, validate_limit_resolution_result),
    ):
        with pytest.raises(TypeError):
            validator(object.__new__(dto_type))
    with pytest.raises(TypeError):
        object.__new__(RelationSubclass).provenance()


def test_extra_conversion_relation_is_not_silently_dropped() -> None:
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    with pytest.raises(ValueError, match="matching provided limit"):
        resolve_joint_space_bounds(
            (_limit(name="joint_1"),),
            expected_joint_names=("joint_1",),
            robot_id="fast_arm-test",
            conversion_relations=(relation,),
        )


def test_authoritative_result_detects_conversion_deletion_and_inventory_tamper() -> None:
    source = _limit(
        name="motor_1",
        space=LimitSpace.MOTOR,
        status=EvidenceStatus.AUTHORITATIVE,
        source_status=EvidenceStatus.AUTHORITATIVE,
        source_kind="lab_document",
    )
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(relation,),
    )
    assert result.authoritative
    object.__setattr__(result, "conversion_relations", ())
    assert not result.authoritative

    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(relation,),
    )
    object.__setattr__(result, "expected_joint_names", ("deleted_joint",))
    assert not result.authoritative

    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(relation,),
    )
    object.__setattr__(relation, "relation_id", "unknown")
    assert not result.authoritative


def test_nested_provisional_source_mutation_is_not_authoritative() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    source = result.bounds[0].parity[0].source
    assert source is not None
    object.__setattr__(source, "status", EvidenceStatus.AUTHORITATIVE)
    assert not result.authoritative


def test_result_rejects_same_semantic_nested_bound_or_relation_replacement() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    original_bound = result.bounds[0]
    replacement_bound = ResolvedJointBound(
        joint_name=original_bound.joint_name,
        lower_rad=original_bound.lower_rad,
        upper_rad=original_bound.upper_rad,
        status=original_bound.status,
        source_names=original_bound.source_names,
        parity=original_bound.parity,
        reason=original_bound.reason,
        comparison_tolerance_rad=original_bound.comparison_tolerance_rad,
    )
    object.__setattr__(result, "bounds", (replacement_bound,))
    assert not result.authoritative
    with pytest.raises(ValueError, match="mutated or bypassed"):
        result.to_dict()

    source = _limit(
        name="motor_1",
        space=LimitSpace.MOTOR,
        status=EvidenceStatus.AUTHORITATIVE,
        source_status=EvidenceStatus.AUTHORITATIVE,
        source_kind="lab_document",
    )
    relation = JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_1",
        source_name="motor_1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        relation_id="motor_1-to-joint_1/v1",
        unit="rad",
    )
    result = resolve_joint_space_bounds(
        (source,),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
        conversion_relations=(relation,),
    )
    replacement_relation = JointSpaceConversion(
        source_space=relation.source_space,
        joint_name=relation.joint_name,
        source_name=relation.source_name,
        gear_ratio=relation.gear_ratio,
        sign=relation.sign,
        offset=relation.offset,
        relation_id=relation.relation_id,
        unit=relation.unit,
    )
    object.__setattr__(result, "conversion_relations", (replacement_relation,))
    assert not result.authoritative


def test_parity_and_bound_reject_same_semantic_nested_replacement() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    bound = result.bounds[0]
    parity = bound.parity[0]
    source = parity.source
    assert source is not None
    replacement_source = _source(source.status, source.source_kind)
    object.__setattr__(parity, "source", replacement_source)
    with pytest.raises(ValueError, match="mutated or bypassed"):
        validate_limit_parity_record(parity)

    replacement_parity = LimitParityRecord(
        joint_name=parity.joint_name,
        source_name=parity.source_name,
        status=parity.status,
        lower=parity.lower,
        upper=parity.upper,
        unit=parity.unit,
        reason=parity.reason,
        source=source,
        source_status=parity.source_status,
        conversion=parity.conversion,
    )
    object.__setattr__(bound, "parity", (replacement_parity,))
    assert not bound.bounded
    with pytest.raises(ValueError, match="mutated or bypassed"):
        bound.to_dict()


def test_external_result_seal_rejects_coherent_private_snapshot_rewrite() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    object.__setattr__(result, "robot_id", "tampered-robot")
    object.__setattr__(
        result,
        "_canonical_snapshot",
        _limit_resolution_module._result_snapshot(result),
    )
    assert not result.authoritative


def test_constructor_bypassed_resolution_dtos_fail_closed() -> None:
    relation = object.__new__(JointSpaceConversion)
    assert relation.target_space is LimitSpace.JOINT
    with pytest.raises((AttributeError, TypeError, ValueError)):
        validate_limit_resolution_result(
            object.__new__(LimitResolutionResult)
        )

    bound = object.__new__(ResolvedJointBound)
    assert not bound.authoritative
    assert not bound.bounded

    parity = object.__new__(LimitParityRecord)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        validate_limit_parity_record(parity)


def test_bypassed_parity_frame_cannot_remain_authoritative() -> None:
    result = resolve_joint_space_bounds(
        (_limit(),),
        expected_joint_names=("joint_1",),
        robot_id="fast_arm-test",
    )
    parity = result.bounds[0].parity[0]
    object.__setattr__(parity, "frame", "frame-B")

    assert not result.authoritative
    with pytest.raises(ValueError, match="mutated or bypassed"):
        validate_limit_parity_record(parity)
