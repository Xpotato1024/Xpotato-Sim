from __future__ import annotations

from dataclasses import dataclass, field, replace

from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.keyboard import build_keyboard_motion_command
from xpotato_sim.plugins.mappings.replay_mapping import build_motion_command_from_replay_frame
from xpotato_sim.runtime.runners.offline_input_smoke import run_offline_input_runtime_stepping_smoke
from xpotato_sim.runtime.runners import offline_input_smoke as offline_smoke_module
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    CapabilityProviderBinding,
    RobotCommandSemanticProviderBinding,
    RobotBundle,
)
from xpotato_sim.runtime.composition.robot_provider_adapters import (
    RuntimeEndpointCommandProvider,
    RuntimeEndpointPoseProvider,
    RuntimeJointPositionCommandProvider,
    RuntimeQposFeasibilityProvider,
)
from xpotato_sim.schemas import MuJoCoState, RawInputFrame
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE


@dataclass
class _RecordingRuntimePlugin:
    wrapped: object
    calls: list[tuple[str, object]] = field(default_factory=list)

    @property
    def profile(self):
        return self.wrapped.profile

    @property
    def profile_id(self):
        return self.wrapped.profile_id

    def validate_model(self, model):
        self.calls.append(("validate_model", model))
        return self.wrapped.validate_model(model)

    def build_simulator(self, **kwargs):
        self.calls.append(("build_simulator", kwargs))
        return self.wrapped.build_simulator(**kwargs)

    def build_target_motion_generator(self, **kwargs):
        self.calls.append(("build_target_motion_generator", kwargs))
        return self.wrapped.build_target_motion_generator(**kwargs)

    def build_forward_kinematics(self):
        self.calls.append(("build_forward_kinematics", None))
        return self.wrapped.build_forward_kinematics()

    def build_qpos_feasibility_guard(self, **kwargs):
        self.calls.append(("build_qpos_feasibility_guard", kwargs))
        return self.wrapped.build_qpos_feasibility_guard(**kwargs)

    def endpoint_position_from_state(self, state):
        self.calls.append(("endpoint_position_from_state", state))
        return self.wrapped.endpoint_position_from_state(state)

    def endpoint_orientation_from_state(self, state):
        return self.wrapped.endpoint_orientation_from_state(state)


def _assert_runtime_smoke_result(
    result,
    *,
    expected_desired_endpoint_m: tuple[float, float, float],
    expected_target_position_m: tuple[float, float, float] | None,
) -> None:
    assert isinstance(result.state, MuJoCoState)
    assert result.motion_command.joint is not None
    assert result.resolved_desired_endpoint_m == expected_desired_endpoint_m
    assert result.motion_command.metadata["desired_endpoint_m"] == expected_desired_endpoint_m
    assert result.state.metadata["desired_endpoint_m"] == expected_desired_endpoint_m
    assert result.state.target_position_m == expected_target_position_m

    assert result.payload is not None
    assert result.payload["metadata"]["desired_endpoint_m"] == expected_desired_endpoint_m
    if expected_target_position_m is None:
        assert result.payload["target_position_m"] is None
    else:
        assert result.payload["target_position_m"] == list(expected_target_position_m)

    endpoint_evaluation = result.endpoint_evaluation
    if endpoint_evaluation is None:
        assert "endpoint_evaluation" not in result.payload
    else:
        assert endpoint_evaluation["desired_endpoint_m"] == list(expected_desired_endpoint_m)
        assert result.payload["endpoint_evaluation"] == endpoint_evaluation


def test_offline_input_runtime_stepping_smoke_accepts_keyboard_motion_command() -> None:
    command = build_keyboard_motion_command(
        (),
        current_tip_position_m=(0.1, 0.0, 0.3),
        timestamp_s=0.5,
    )
    command = command.__class__(
        timestamp_s=command.timestamp_s,
        target=command.target,
        joint=command.joint,
        metadata={**command.metadata, "desired_endpoint_m": (0.1, 0.0, 0.3)},
    )

    result = run_offline_input_runtime_stepping_smoke(command)

    _assert_runtime_smoke_result(
        result,
        expected_desired_endpoint_m=(0.1, 0.0, 0.3),
        expected_target_position_m=None,
    )
    assert result.motion_command.metadata["source_kind"] == "keyboard"


def test_offline_input_runtime_stepping_smoke_changes_desired_endpoint_for_keyboard_input() -> None:
    command = build_keyboard_motion_command(
        ("KeyD",),
        current_tip_position_m=(0.1, 0.0, 0.3),
        timestamp_s=1.0,
    )
    command = command.__class__(
        timestamp_s=command.timestamp_s,
        target=command.target,
        joint=command.joint,
        metadata={**command.metadata, "desired_endpoint_m": (0.1 + 0.1 / 60.0, 0.0, 0.3)},
    )

    result = run_offline_input_runtime_stepping_smoke(command)

    _assert_runtime_smoke_result(
        result,
        expected_desired_endpoint_m=(0.1 + 0.1 / 60.0, 0.0, 0.3),
        expected_target_position_m=None,
    )
    assert result.resolved_desired_endpoint_m != (0.1, 0.0, 0.3)


def test_offline_input_runtime_stepping_smoke_accepts_replay_fixture_motion_command() -> None:
    frame = RawInputFrame(
        source="replay",
        timestamp_s=1.25,
        metadata={
            "source_kind": "replay",
            "desired_endpoint_m": (0.4, 0.0, 0.6),
            "target_position_m": (9.0, 9.0, 9.0),
        },
    )
    command = build_motion_command_from_replay_frame(frame)

    result = run_offline_input_runtime_stepping_smoke(command, initial_qpos=(0.0, 0.0, 0.0, 0.0))

    _assert_runtime_smoke_result(
        result,
        expected_desired_endpoint_m=(0.4, 0.0, 0.6),
        expected_target_position_m=(9.0, 9.0, 9.0),
    )
    assert result.motion_command.metadata["target_position_m"] == (9.0, 9.0, 9.0)
    assert result.motion_command.metadata["source_kind"] == "replay"


def test_offline_input_runtime_profile_metadata_cannot_be_spoofed() -> None:
    frame = RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            "desired_endpoint_m": (0.4, 0.0, 0.6),
            "robot_profile_id": "spoofed",
            "model_contract_version": "spoofed/v9",
            "robot_joint_names": ("wrong",),
            "robot_qpos_dimension": 999,
        },
    )
    result = run_offline_input_runtime_stepping_smoke(
        build_motion_command_from_replay_frame(frame)
    )

    assert result.state.metadata["robot_profile_id"] == "fast_arm"
    assert result.state.metadata["model_contract_version"] == FAST_ARM_ROBOT_PROFILE.model_contract_version
    assert result.state.metadata["robot_joint_names"] == FAST_ARM_ROBOT_PROFILE.canonical_joint_names
    assert result.state.metadata["robot_qpos_dimension"] == 4


def test_offline_input_runtime_uses_resolved_plugin_components_and_home_seed(monkeypatch) -> None:
    resolved = resolve_robot_bundle("fast_arm")
    plugin = _RecordingRuntimePlugin(resolved.runtime_plugin)
    replacement_providers = {
        ENDPOINT_POSE_V1: RuntimeEndpointPoseProvider(plugin),
        ENDPOINT_COMMAND_V1: RuntimeEndpointCommandProvider(plugin),
        QPOS_FEASIBILITY_V1: RuntimeQposFeasibilityProvider(plugin),
    }
    bundle = replace(
        resolved,
        runtime_plugin=plugin,
        capability_providers=tuple(
            CapabilityProviderBinding(
                binding.identity,
                replacement_providers.get(binding.identity, binding.provider),
            )
            for binding in resolved.capability_providers
        ),
        command_semantic_providers=tuple(
            RobotCommandSemanticProviderBinding(
                binding.identity,
                RuntimeJointPositionCommandProvider(plugin),
            )
            for binding in resolved.command_semantic_providers
        ),
    )

    def recording_resolver(profile_id: str, **kwargs) -> RobotBundle:
        assert profile_id == "fast_arm"
        assert kwargs == {"robot_logical_version": 1}
        return bundle

    monkeypatch.setattr(offline_smoke_module, "resolve_robot_bundle", recording_resolver)
    command = build_keyboard_motion_command(
        (),
        current_tip_position_m=(0.1, 0.0, 0.3),
        timestamp_s=0.5,
    )
    command = command.__class__(
        timestamp_s=command.timestamp_s,
        target=command.target,
        joint=command.joint,
        metadata={**command.metadata, "desired_endpoint_m": (0.1, 0.0, 0.3)},
    )

    result = run_offline_input_runtime_stepping_smoke(command)

    call_names = [name for name, _ in plugin.calls]
    assert call_names == [
        "build_simulator",
        "validate_model",
        "build_qpos_feasibility_guard",
        "build_target_motion_generator",
        "build_forward_kinematics",
        "endpoint_position_from_state",
    ]
    motion_call = next(value for name, value in plugin.calls if name == "build_target_motion_generator")
    assert motion_call["seed_joint_angles_rad"] == (0.0, -0.5235987755982989, 0.0, -1.0471975511965976)
    assert len(result.motion_command.joint.joint_angles_rad) == FAST_ARM_ROBOT_PROFILE.qpos_dimension
    assert result.endpoint_evaluation is not None
    assert result.endpoint_evaluation["site_endpoint_m"] == list(
        plugin.endpoint_position_from_state(result.state)
    )

    plugin.calls.clear()
    run_offline_input_runtime_stepping_smoke(
        command,
        initial_qpos=(0.1, -0.2, 0.3, -0.4),
    )
    motion_call = next(value for name, value in plugin.calls if name == "build_target_motion_generator")
    assert motion_call["seed_joint_angles_rad"] == (0.1, -0.2, 0.3, -0.4)


def test_offline_input_runtime_preserves_plugin_target_reject_and_home_hold_metadata() -> None:
    command = build_motion_command_from_replay_frame(
        RawInputFrame(
            source="replay",
            timestamp_s=2.0,
            metadata={"source_kind": "replay", "desired_endpoint_m": (10.0, 0.0, 0.0)},
        )
    )

    result = run_offline_input_runtime_stepping_smoke(command)

    assert result.motion_command.joint is not None
    assert result.motion_command.joint.joint_angles_rad == (
        0.0,
        -0.5235987755982989,
        0.0,
        -1.0471975511965976,
    )
    assert result.motion_command.metadata["target_status"] == "held"
    assert result.motion_command.metadata["target_rejected"] is True
    assert result.motion_command.metadata["target_rejection_reason"] == "target_unreachable"
    assert result.motion_command.metadata["rejected_desired_endpoint_m"] == (10.0, 0.0, 0.0)
    assert result.state.qpos == result.motion_command.joint.joint_angles_rad
    assert result.state.metadata["target_rejection_reason"] == "target_unreachable"
