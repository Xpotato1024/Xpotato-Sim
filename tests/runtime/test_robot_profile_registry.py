from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

import xpotato_sim.plugins.robots.fast_arm.adapter.runtime as fast_arm_plugin_module
from xpotato_sim.mujoco_backend.model_info import MuJoCoModelInfo
from xpotato_sim.runtime.composition.robot_profile import (
    CoordinateUnitContract,
    EndpointReference,
    RobotProfile,
    robot_profile_runtime_metadata,
)
from xpotato_sim.plugins.robots.catalog import (
    registered_robot_profile_ids,
    resolve_robot_profile,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import (
    FAST_ARM_RUNTIME_PLUGIN,
    FastArmRuntimePlugin,
)
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
from xpotato_sim.plugins.robots.catalog import (
    resolve_robot_runtime,
    registered_robot_runtime_plugin_ids,
    resolve_robot_runtime_plugin,
)
from xpotato_sim.runtime.composition.robot_resolution import (
    ImmutableRegistry,
    validate_production_robot_selection_consistency,
    validate_robot_profile_plugin_consistency,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    VersionedIdentity,
)
from xpotato_sim.transport import mujoco_state_to_payload


@dataclass(frozen=True)
class _Entry:
    profile_id: str


class _Publisher:
    async def publish(self, state) -> None:  # noqa: ANN001
        _ = state


@dataclass(frozen=True)
class _PluginEntry:
    profile: RobotProfile

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id


def _dummy_profile(*, profile_id: str, joints: tuple[str, ...], nq: int, nv: int) -> RobotProfile:
    return RobotProfile(
        profile_id=profile_id,
        profile_contract_version=1,
        model_contract_version=f"{profile_id}/v1",
        backend_kind="mujoco",
        mujoco_model_asset=FAST_ARM_ROBOT_PROFILE.mujoco_model_asset,
        canonical_joint_names=joints,
        qpos_dimension=nq,
        qvel_dimension=nv,
        initial_keyframe_name="initial",
        endpoint=EndpointReference(site_name="endpoint", body_name=None),
        joint_limit_config_asset=None,
        coordinate_units=CoordinateUnitContract("meter", "rad", "world", "wxyz"),
        viewer_profile_id=profile_id,
        supported_capabilities=frozenset(),
    )


def test_fast_arm_profile_and_plugin_resolve_explicitly() -> None:
    profile = resolve_robot_profile("fast_arm")
    plugin = resolve_robot_runtime_plugin("fast_arm")

    assert profile is FAST_ARM_ROBOT_PROFILE
    assert plugin is FAST_ARM_RUNTIME_PLUGIN
    assert plugin.profile is profile
    assert registered_robot_profile_ids() == ("fast_arm",)
    assert registered_robot_runtime_plugin_ids() == ("fast_arm",)

    resolved = resolve_robot_runtime("fast_arm")
    assert resolved.profile is profile
    assert resolved.plugin is plugin


def test_unknown_profile_and_plugin_ids_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="unknown robot profile ID"):
        resolve_robot_profile("unknown")
    with pytest.raises(ValueError, match="unknown robot runtime plugin ID"):
        resolve_robot_runtime_plugin("unknown")


def test_duplicate_registration_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate test registration"):
        ImmutableRegistry((_Entry("same"), _Entry("same")), kind="test")


def test_production_composition_requires_profile_when_config_is_supplied() -> None:
    with pytest.raises(ValueError, match="requires robot_profile_id"):
        build_concrete_mujoco_pipeline(config=RuntimeConfig(), publisher=_Publisher())


def test_production_composition_rejects_unknown_profile_at_startup() -> None:
    with pytest.raises(ValueError, match="unknown robot profile ID"):
        build_concrete_mujoco_pipeline(
            config=RuntimeConfig(robot_profile_id="unknown"),
            publisher=_Publisher(),
        )


def test_cross_registry_rejects_ids_present_on_only_one_side() -> None:
    profile = _dummy_profile(profile_id="dummy", joints=("joint",), nq=1, nv=1)
    empty_profiles = ImmutableRegistry((), kind="robot profile")
    profiles = ImmutableRegistry((profile,), kind="robot profile")
    empty_plugins = ImmutableRegistry((), kind="robot runtime plugin")
    plugins = ImmutableRegistry((_PluginEntry(profile),), kind="robot runtime plugin")

    with pytest.raises(ValueError, match="registry ID mismatch"):
        resolve_robot_runtime("dummy", profile_registry=profiles, plugin_registry=empty_plugins)
    with pytest.raises(ValueError, match="registry ID mismatch"):
        resolve_robot_runtime("dummy", profile_registry=empty_profiles, plugin_registry=plugins)


def test_cross_registry_rejects_model_contract_and_profile_object_mismatches() -> None:
    profile = _dummy_profile(profile_id="dummy", joints=("joint",), nq=1, nv=1)
    different_model_contract = replace(profile, model_contract_version="dummy/v2")
    equal_but_distinct_profile = replace(profile)

    with pytest.raises(ValueError, match="model contract version mismatch"):
        validate_robot_profile_plugin_consistency(
            "dummy", profile, _PluginEntry(different_model_contract)
        )
    with pytest.raises(ValueError, match="registered profile object"):
        validate_robot_profile_plugin_consistency(
            "dummy", profile, _PluginEntry(equal_but_distinct_profile)
        )


def test_production_selection_rejects_runtime_plugin_identity_mismatch() -> None:
    profile = _dummy_profile(profile_id="robot_a", joints=("joint",), nq=1, nv=1)

    @dataclass(frozen=True)
    class _MismatchedPlugin:
        profile: RobotProfile
        profile_id: str = "robot_b"

    with pytest.raises(
        ValueError,
        match="production Robot Bundle/Runtime Plugin logical identity mismatch",
    ):
        validate_production_robot_selection_consistency(
            PluginSelection("robot_a", 1),
            bundle_identity=VersionedIdentity("robot_a", 1),
            profile=profile,
            plugin=_MismatchedPlugin(profile),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("joints", "nq", "nv"),
    [(('ball_joint',), 4, 3), (('root',), 7, 6)],
)
def test_generic_profile_allows_joint_count_to_differ_from_qpos_dimension(
    joints: tuple[str, ...], nq: int, nv: int
) -> None:
    assert _dummy_profile(profile_id="dummy", joints=joints, nq=nq, nv=nv).qpos_dimension == nq


def test_generic_profile_rejects_duplicate_names_and_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="unique"):
        _dummy_profile(profile_id="dummy", joints=("joint", "joint"), nq=2, nv=2)
    with pytest.raises(ValueError, match="positive"):
        _dummy_profile(profile_id="dummy", joints=("joint",), nq=0, nv=1)
    with pytest.raises(ValueError, match="positive"):
        _dummy_profile(profile_id="dummy", joints=("joint",), nq=1, nv=0)


def test_fast_arm_plugin_retains_specific_dimension_and_order_validation() -> None:
    model = type("Model", (), {"nq": 4, "nv": 4})()
    with pytest.raises(ValueError, match="joint order mismatch"):
        FastArmRuntimePlugin(
            replace(FAST_ARM_ROBOT_PROFILE, canonical_joint_names=tuple(reversed(FAST_ARM_ROBOT_PROFILE.canonical_joint_names)))
        ).validate_model(model)
    with pytest.raises(ValueError, match="dimensions 4/4"):
        FastArmRuntimePlugin(
            replace(FAST_ARM_ROBOT_PROFILE, qpos_dimension=5, qvel_dimension=4)
        ).validate_model(model)


@pytest.mark.parametrize(
    ("nq", "nv", "joint_names", "message"),
    [
        (3, 4, FAST_ARM_ROBOT_PROFILE.canonical_joint_names, "qpos dimension mismatch"),
        (4, 3, FAST_ARM_ROBOT_PROFILE.canonical_joint_names, "qvel dimension mismatch"),
        (4, 4, tuple(reversed(FAST_ARM_ROBOT_PROFILE.canonical_joint_names)), "joint order mismatch"),
    ],
)
def test_profile_model_dimension_and_joint_order_mismatch_fail_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    nq: int,
    nv: int,
    joint_names: tuple[str, ...],
    message: str,
) -> None:
    model = type("Model", (), {"nq": nq, "nv": nv})()
    monkeypatch.setattr(
        fast_arm_plugin_module,
        "inspect_mujoco_model",
        lambda _model: MuJoCoModelInfo(joint_names=joint_names, body_names=(), site_names=()),
    )

    with pytest.raises(ValueError, match=message):
        FAST_ARM_RUNTIME_PLUGIN.validate_model(model)


def test_profile_identity_uses_payload_v0_metadata_without_shape_break() -> None:
    pipeline = build_concrete_mujoco_pipeline(publisher=_Publisher())
    state = pipeline.simulator.snapshot()
    state = type(state)(
        frame_index=state.frame_index,
        time_s=state.time_s,
        qpos=state.qpos,
        qvel=state.qvel,
        bodies=state.bodies,
        sites=state.sites,
        target_position_m=state.target_position_m,
        metadata=robot_profile_runtime_metadata(FAST_ARM_ROBOT_PROFILE),
    )

    payload = mujoco_state_to_payload(state)

    assert payload["version"] == 0
    assert payload["metadata"]["robot_profile_id"] == "fast_arm"
    assert payload["metadata"]["model_contract_version"] == FAST_ARM_ROBOT_PROFILE.model_contract_version
    assert payload["metadata"]["robot_joint_names"] == FAST_ARM_ROBOT_PROFILE.canonical_joint_names
    assert set(payload) == {
        "version",
        "frame_index",
        "time_s",
        "qpos",
        "qvel",
        "bodies",
        "sites",
        "target_position_m",
        "metadata",
    }


def test_profile_declaration_contains_no_executable_factory_fields() -> None:
    field_names = set(RobotProfile.__dataclass_fields__)
    assert not any("module" in name or "class" in name or "factory" in name for name in field_names)
