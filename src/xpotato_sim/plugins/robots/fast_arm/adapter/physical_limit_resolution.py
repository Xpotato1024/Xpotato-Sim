"""fast_armのjoint-limit configurationをgeneric P2へ投影するadapter。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fast_arm_core.joint_limits import (
    FastArmJointLimit,
    FastArmJointLimitConfig,
)

from xpotato_sim.runtime.safety.limit_resolution import (
    FastArmResolvedBoundsProvider,
    JointSpaceConversion,
    _range,
    fast_arm_mujoco_limits_to_physical_limits,
    resolve_joint_space_bounds,
    validate_limit_resolution_identity,
)
from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitQuantity,
    LimitSourceProvenance,
    LimitSpace,
    PhysicalLimit,
    canonical_fast_arm_joint_space_frame,
)


def _builtin_int(name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a built-in int")
    return value


def _builtin_string(name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a built-in string")
    return value


def _builtin_number(name: str, value: object) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a built-in int or float")
    try:
        return float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc


def _validated_fast_arm_joint_limit_config(
    config: object,
) -> FastArmJointLimitConfig:
    """configのprimitive境界を先に固定し、core constructorで再検証する。"""

    if type(config) is not FastArmJointLimitConfig:
        raise TypeError("config must be FastArmJointLimitConfig")

    schema_version = _builtin_int(
        "config.schema_version",
        config.schema_version,
    )
    robot = _builtin_string("config.robot", config.robot)
    model = _builtin_string("config.model", config.model)
    angle_unit = _builtin_string("config.angle_unit", config.angle_unit)
    status = _builtin_string("config.status", config.status)
    raw_joints = config.joints
    if type(raw_joints) is not tuple:
        raise TypeError("config.joints must be a built-in tuple")

    joints: list[FastArmJointLimit] = []
    for index, raw_joint in enumerate(raw_joints):
        if type(raw_joint) is not FastArmJointLimit:
            raise TypeError(
                f"config.joints[{index}] must be FastArmJointLimit"
            )
        name = _builtin_string(
            f"config.joints[{index}].name",
            raw_joint.name,
        )
        lower_rad = _builtin_number(
            f"config.joints[{index}].lower_rad",
            raw_joint.lower_rad,
        )
        upper_rad = _builtin_number(
            f"config.joints[{index}].upper_rad",
            raw_joint.upper_rad,
        )
        joints.append(
            FastArmJointLimit(
                name=name,
                lower_rad=lower_rad,
                upper_rad=upper_rad,
            )
        )

    # schema、metadata、有限値・順序、必須joint、重複、canonical orderは
    # core constructorのvalidationを正本として再利用する。
    return FastArmJointLimitConfig(
        schema_version=schema_version,
        robot=robot,
        model=model,
        angle_unit=angle_unit,
        status=status,
        joints=tuple(joints),
    )


def _project_validated_fast_arm_joint_limit_config(
    config: FastArmJointLimitConfig,
    *,
    source_id: str,
) -> tuple[PhysicalLimit, ...]:
    source = LimitSourceProvenance(
        source_kind="joint_limit_toml",
        source_id=source_id,
        revision=f"schema-{config.schema_version}",
        status=EvidenceStatus.PROVISIONAL,
        evidence_reference="software-configuration-only",
    )
    return tuple(
        PhysicalLimit(
            name=joint.name,
            quantity=LimitQuantity.POSITION,
            lower=joint.lower_rad,
            upper=joint.upper_rad,
            unit="rad",
            space=LimitSpace.JOINT,
            frame=canonical_fast_arm_joint_space_frame(),
            status=EvidenceStatus.PROVISIONAL,
            source=source,
        )
        for joint in config.joints
    )


def fast_arm_toml_limits_to_physical_limits(
    config: object,
    *,
    source_id: str = "fast_arm_core/resources/config/joint_limits.toml",
) -> tuple[PhysicalLimit, ...]:
    """検証済みfast_arm TOMLをprovisional sourceとしてP2へ投影する。"""

    validated_config = _validated_fast_arm_joint_limit_config(config)
    return _project_validated_fast_arm_joint_limit_config(
        validated_config,
        source_id=source_id,
    )


def build_fast_arm_resolved_bounds_provider(
    *,
    config: object,
    model: object | None = None,
    profile_joint_names: Sequence[str] | None = None,
    profile_bounds_rad: Mapping[str, Sequence[float]] | None = None,
    conversions: Sequence[JointSpaceConversion] = (),
) -> FastArmResolvedBoundsProvider:
    """fast_arm adapterのprojectionをgeneric resolutionへ渡す。"""

    validated_config = _validated_fast_arm_joint_limit_config(config)
    canonical_names = validated_config.joint_names
    if profile_joint_names is None:
        names = canonical_names
    else:
        names = tuple(
            validate_limit_resolution_identity("joint_name", name)
            for name in profile_joint_names
        )
        if not names:
            raise ValueError("fast_arm profile must declare canonical joint names")
        if names != canonical_names:
            raise ValueError(
                "profile_joint_names must match the canonical fast_arm joint order"
            )

    sources = list(
        _project_validated_fast_arm_joint_limit_config(
            validated_config,
            source_id="fast_arm_core/resources/config/joint_limits.toml",
        )
    )
    if profile_bounds_rad is not None:
        profile_source = LimitSourceProvenance(
            source_kind="robot_profile",
            source_id="fast_arm-profile",
            revision="profile-contract",
            status=EvidenceStatus.PROVISIONAL,
            evidence_reference="software-profile-only",
        )
        for name, values in profile_bounds_rad.items():
            profile_name = validate_limit_resolution_identity(
                "profile joint name",
                name,
            )
            if profile_name not in names:
                raise ValueError(
                    "profile bounds joint name must match the canonical "
                    f"fast_arm joint order: {profile_name}"
                )
            lower, upper = _range(values, f"profile bounds for {name}")
            sources.append(
                PhysicalLimit(
                    name=profile_name,
                    quantity=LimitQuantity.POSITION,
                    lower=lower,
                    upper=upper,
                    unit="rad",
                    space=LimitSpace.JOINT,
                    frame=canonical_fast_arm_joint_space_frame(),
                    status=EvidenceStatus.PROVISIONAL,
                    source=profile_source,
                )
            )

    sources.extend(
        fast_arm_mujoco_limits_to_physical_limits(
            model,
            joint_names=names,
        )
    )
    result = resolve_joint_space_bounds(
        sources,
        expected_joint_names=names,
        robot_id="fast_arm",
        conversion_relations=conversions,
    )
    return FastArmResolvedBoundsProvider(result)


__all__ = [
    "build_fast_arm_resolved_bounds_provider",
    "fast_arm_toml_limits_to_physical_limits",
]
