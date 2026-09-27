from __future__ import annotations

from dataclasses import replace
from math import sqrt

import pytest

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_POSE_V1,
    EndpointPoseProvider,
)
from xpotato_sim.runtime.control.input_source_state import RuntimeInputSourceState
from xpotato_sim.runtime.evaluation.manifest import (
    EvaluationReadinessError,
    SoftwareExecutionIdentity,
    build_evaluation_readiness,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.composition import PluginParameters
from xpotato_sim.runtime.experiment.contracts import (
    PluginAxis,
    PluginParameterOwner,
    TaskTerminalClassification,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachMotionStatus,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourcePlugin,
)
from xpotato_sim.runtime.experiment.world_tool_runner import (
    ExperimentRunnerError,
    ExperimentStopReason,
    _immutable_execution_fact,
    _validate_measured_initial_state,
    _project_motion_status,
    run_r7_g_world_tool_experiment,
    run_experiment_condition,
)
from xpotato_sim.runtime.safety.input_safety import RuntimeInputSafetyResult
from xpotato_sim.schemas import InputIntent, MotionCommand


REVISION = "test-revision:issue-406-runner"
EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity="Xpotato1024/Xpotato-Sim",
    software_revision_identity=REVISION,
)


def _world_readiness():
    manifest = build_r7_g_free_space_manifest_pair(
        software_revision_identity=REVISION
    ).world
    return build_evaluation_readiness(
        manifest,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )


def _safety_result(
    *,
    metadata: dict[str, object],
    stale_reason: str | None = None,
    qpos_rejected: bool = False,
) -> RuntimeInputSafetyResult:
    source_state = RuntimeInputSourceState(
        source_kind="analog_fixture",
        source_active=stale_reason is None,
        stale_reason=stale_reason,
    )
    return RuntimeInputSafetyResult(
        motion_command=MotionCommand(timestamp_s=0.0, metadata=metadata),
        source_state=source_state,
        is_stale=stale_reason is not None,
        should_update_target_position_m=not qpos_rejected,
        stale_reason=stale_reason,
        command_age_ms=0,
        qpos_feasibility_rejected=qpos_rejected,
    )


def test_execution_fact_snapshot_deep_freezes_nested_metadata() -> None:
    metadata = {"nested": {"axis": [1.0, 0.0, 0.0]}}
    intent = InputIntent(source="fixture", timestamp_s=0.0, metadata=metadata)

    frozen = _immutable_execution_fact(intent)
    metadata["nested"]["axis"][0] = 9.0  # type: ignore[index]

    assert frozen.metadata["nested"]["axis"] == (1.0, 0.0, 0.0)  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen.metadata["changed"] = True  # type: ignore[index]


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_reason"),
    (
        (
            _safety_result(metadata={"motion_status": "accepted"}),
            EndpointReachMotionStatus.NOMINAL,
            None,
        ),
        (
            _safety_result(
                metadata={"motion_status": "accepted"},
                stale_reason="fixture_stale",
            ),
            EndpointReachMotionStatus.STALE,
            "fixture_stale",
        ),
        (
            _safety_result(
                metadata={
                    "motion_status": "accepted",
                    "qpos_rejection_reason": "joint_limit_violation",
                },
                qpos_rejected=True,
            ),
            EndpointReachMotionStatus.REJECTED,
            "joint_limit_violation",
        ),
        (
            _safety_result(
                metadata={
                    "motion_status": "held",
                    "motion_rejection_reason": "tool_orientation_unavailable",
                }
            ),
            EndpointReachMotionStatus.HELD,
            "tool_orientation_unavailable",
        ),
    ),
)
def test_runtime_status_projection_preserves_non_nominal_state(
    result: RuntimeInputSafetyResult,
    expected_status: EndpointReachMotionStatus,
    expected_reason: str | None,
) -> None:
    assert _project_motion_status(result) == (expected_status, expected_reason)


def test_invalid_control_frame_default_is_technical_invalid() -> None:
    result = _safety_result(
        metadata={
            "motion_status": "accepted",
            "control_frame_resolution_status": "invalid_control_frame_defaulted",
            "control_frame_resolution_reason": "invalid_control_frame_defaulted_to_world",
        }
    )

    status, reason = _project_motion_status(result)

    assert status is EndpointReachMotionStatus.TECHNICAL_INVALID
    assert reason == "invalid_control_frame_defaulted_to_world"


def test_changed_manifest_after_freeze_fails_before_execution() -> None:
    readiness = _world_readiness()
    changed = replace(
        readiness,
        manifest=replace(readiness.manifest, target_identity="changed-after-freeze"),
    )

    with pytest.raises(EvaluationReadinessError, match="changed after readiness"):
        run_experiment_condition(changed)


def test_production_entry_rejects_actual_revision_mismatch() -> None:
    with pytest.raises(
        EvaluationReadinessError,
        match="does not match actual execution identity",
    ):
        run_r7_g_world_tool_experiment(
            manifest_software_revision_identity=REVISION,
            execution_identity=SoftwareExecutionIdentity(
                repository_identity="Xpotato1024/Xpotato-Sim",
                software_revision_identity="test-revision:different-actual-revision",
            ),
        )


@pytest.mark.parametrize(
    "manifest_change",
    (
        {"initial_qpos_rad": (0.1, -1.5707963267948966, 0.0, 0.0)},
        {"initial_tool_orientation_wxyz": (1.0, 0.0, 0.0, 0.0)},
    ),
)
def test_measured_reset_state_must_match_frozen_manifest(
    manifest_change: dict[str, object],
) -> None:
    readiness = _world_readiness()
    pipeline_readiness = replace(
        readiness,
        manifest=replace(readiness.manifest, **manifest_change),
    )
    bundle = readiness.composition.robot_bundle
    simulator = bundle.runtime_plugin.build_simulator(
        model_path=None,
        initial_keyframe_name=readiness.manifest.initial_keyframe_name,
    )
    endpoint_provider = bundle.provider(ENDPOINT_POSE_V1)
    assert isinstance(endpoint_provider, EndpointPoseProvider)
    observation = endpoint_provider.observe_endpoint_pose(simulator.snapshot())

    with pytest.raises(ExperimentRunnerError, match="does not match"):
        _validate_measured_initial_state(
            pipeline_readiness,
            simulator.snapshot(),
            observation,
        )


def test_measured_reset_accepts_equivalent_quaternion_sign() -> None:
    readiness = _world_readiness()
    bundle = readiness.composition.robot_bundle
    simulator = bundle.runtime_plugin.build_simulator(
        model_path=None,
        initial_keyframe_name=readiness.manifest.initial_keyframe_name,
    )
    endpoint_provider = bundle.provider(ENDPOINT_POSE_V1)
    assert isinstance(endpoint_provider, EndpointPoseProvider)
    state = simulator.snapshot()
    observation = endpoint_provider.observe_endpoint_pose(state)
    assert observation.quaternion_wxyz is not None
    equivalent = replace(
        observation,
        quaternion_wxyz=tuple(-value for value in observation.quaternion_wxyz),
    )

    _validate_measured_initial_state(readiness, state, equivalent)


def _unit_quaternion_with_component_delta(
    expected: tuple[float, float, float, float],
    delta: float,
) -> tuple[float, float, float, float]:
    index = min(range(len(expected)), key=lambda item: abs(expected[item]))
    values = list(expected)
    values[index] += delta
    norm = sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values)  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("delta", "accepted"),
    ((5e-10, True), (2e-9, False)),
)
def test_measured_reset_qpos_uses_bounded_runtime_tolerance(
    delta: float,
    accepted: bool,
) -> None:
    readiness = _world_readiness()
    bundle = readiness.composition.robot_bundle
    simulator = bundle.runtime_plugin.build_simulator(
        model_path=None,
        initial_keyframe_name=readiness.manifest.initial_keyframe_name,
    )
    endpoint_provider = bundle.provider(ENDPOINT_POSE_V1)
    assert isinstance(endpoint_provider, EndpointPoseProvider)
    state = simulator.snapshot()
    observation = endpoint_provider.observe_endpoint_pose(state)
    shifted_qpos = tuple(
        value + delta for value in readiness.manifest.initial_qpos_rad
    )

    if accepted:
        _validate_measured_initial_state(
            readiness,
            replace(state, qpos=shifted_qpos),
            observation,
        )
    else:
        with pytest.raises(ExperimentRunnerError, match="reset state qpos"):
            _validate_measured_initial_state(
                readiness,
                replace(state, qpos=shifted_qpos),
                observation,
            )


@pytest.mark.parametrize(
    ("delta", "accepted"),
    ((5e-10, True), (2e-9, False)),
)
def test_measured_reset_accepts_sign_equivalent_quaternion_with_bounded_tolerance(
    delta: float,
    accepted: bool,
) -> None:
    readiness = _world_readiness()
    bundle = readiness.composition.robot_bundle
    simulator = bundle.runtime_plugin.build_simulator(
        model_path=None,
        initial_keyframe_name=readiness.manifest.initial_keyframe_name,
    )
    endpoint_provider = bundle.provider(ENDPOINT_POSE_V1)
    assert isinstance(endpoint_provider, EndpointPoseProvider)
    state = simulator.snapshot()
    observation = endpoint_provider.observe_endpoint_pose(state)
    assert observation.quaternion_wxyz is not None
    expected_orientation = readiness.manifest.initial_tool_orientation_wxyz
    shifted_orientation = tuple(
        -value
        for value in _unit_quaternion_with_component_delta(
            expected_orientation,
            delta,
        )
    )
    shifted_observation = replace(
        observation,
        quaternion_wxyz=shifted_orientation,
    )

    if accepted:
        _validate_measured_initial_state(readiness, state, shifted_observation)
    else:
        with pytest.raises(
            ExperimentRunnerError,
            match="reset endpoint orientation",
        ):
            _validate_measured_initial_state(readiness, state, shifted_observation)


def test_malformed_fixture_fails_closed_during_reader_assembly() -> None:
    readiness = _world_readiness()
    manifest = readiness.manifest
    input_owner = PluginParameterOwner(PluginAxis.INPUT_SOURCE, manifest.input_source)
    parameters = tuple(
        PluginParameters(item.owner, {"samples": ({"timestamp_s": 0.0},)})
        if item.owner == input_owner
        else item
        for item in manifest.parameters
    )
    malformed_readiness = build_evaluation_readiness(
        replace(manifest, parameters=parameters),
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )

    with pytest.raises(ValueError, match="fixture sample fields"):
        run_experiment_condition(malformed_readiness)


def test_stale_fixture_projects_to_task_failure_without_nominal_collapse() -> None:
    readiness = _world_readiness()
    manifest = readiness.manifest
    input_owner = PluginParameterOwner(PluginAxis.INPUT_SOURCE, manifest.input_source)
    stale_sample = {
        "timestamp_s": 0.0,
        "raw_values": (0.0, 0.0, 0.0),
        "active": False,
        "stale_reason": "fixture_stale",
    }
    parameters = tuple(
        PluginParameters(item.owner, {"samples": (stale_sample,)})
        if item.owner == input_owner
        else item
        for item in manifest.parameters
    )
    stale_readiness = build_evaluation_readiness(
        replace(manifest, parameters=parameters),
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )

    result = run_experiment_condition(stale_readiness)

    assert result.classification is TaskTerminalClassification.FAILURE
    assert result.step_count == 1
    assert result.final_elapsed_time_s == pytest.approx(manifest.cadence_s)


class _FailingReader:
    def read_frame(self):
        raise RuntimeError("fixture read failed")

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)


def _failing_reader_factory(parameters, *, runtime_dependencies=None):
    _ = (parameters, runtime_dependencies)
    return _FailingReader()


def test_source_failure_becomes_task_owned_technical_invalid() -> None:
    readiness = _world_readiness()
    plugin = readiness.composition.input_source
    failing_plugin = InputSourcePlugin(
        identity=plugin.identity,
        produced_sample_schema=plugin.produced_sample_schema,
        mode=plugin.mode,
        factory=_failing_reader_factory,
        parameter_contract=plugin.parameter_contract,
        initial_health=plugin.initial_health,
        initial_metadata=plugin.initial_metadata,
        produced_evidence=plugin.produced_evidence,
        mapping_input_adapter=plugin.mapping_input_adapter,
    )
    failing_readiness = replace(
        readiness,
        composition=replace(readiness.composition, input_source=failing_plugin),
    )

    result = run_experiment_condition(failing_readiness)

    assert result.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert result.step_count == 1
    assert result.motion_steps == ()


def test_explicit_step_bound_returns_running_task_as_bounded_stop(monkeypatch) -> None:
    readiness = _world_readiness()
    monkeypatch.setattr(
        "xpotato_sim.runtime.experiment.world_tool_runner._bounded_step_count",
        lambda _: 1,
    )

    result = run_experiment_condition(readiness)

    assert result.classification is TaskTerminalClassification.RUNNING
    assert result.stop_reason is ExperimentStopReason.BOUNDED_STEP_LIMIT
    assert result.step_count == 1
    assert len(result.motion_steps) == 1
    assert result.motion_steps[0].sample_index == 0
    assert result.motion_steps[0].runtime_timestamp_s == pytest.approx(
        readiness.manifest.cadence_s
    )
