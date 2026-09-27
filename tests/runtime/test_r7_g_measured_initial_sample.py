from __future__ import annotations

import pytest

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_POSE_V1,
    EndpointPoseProvider,
)
from xpotato_sim.runtime.evaluation.manifest import (
    SoftwareExecutionIdentity,
    build_evaluation_readiness,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachObservation,
    decode_endpoint_reach_trajectory_evidence,
)


SOFTWARE_REVISION = "test-revision:r7-g-measured-initial-sample"


def test_production_task_accepts_actual_mujoco_home_measurement_as_origin() -> None:
    pair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=SOFTWARE_REVISION
    )
    readiness = build_evaluation_readiness(
        pair.world,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=SoftwareExecutionIdentity(
            repository_identity="Xpotato1024/Xpotato-Sim",
            software_revision_identity=SOFTWARE_REVISION,
        ),
    )

    bundle = readiness.composition.robot_bundle
    simulator = bundle.runtime_plugin.build_simulator(
        model_path=None,
        initial_keyframe_name=pair.world.initial_keyframe_name,
    )
    bundle.runtime_plugin.validate_model(simulator.model)
    endpoint_provider = bundle.provider(ENDPOINT_POSE_V1)
    assert isinstance(endpoint_provider, EndpointPoseProvider)
    measured_pose = endpoint_provider.observe_endpoint_pose(simulator.snapshot())
    assert measured_pose.position_m is not None
    assert measured_pose.position_m == pytest.approx(
        pair.world.initial_tip_position_m,
        rel=0.0,
        abs=1e-6,
    )

    transition = readiness.task_execution_binding.advance(
        readiness.task_execution_binding.initial_state(),
        EndpointReachObservation(
            elapsed_time_s=0.0,
            position_world_m=measured_pose.position_m,
        ),
    )

    assert transition.classification is TaskTerminalClassification.RUNNING
    trajectory = decode_endpoint_reach_trajectory_evidence(transition.evidence)
    assert trajectory.initial_position_world_m == measured_pose.position_m
    assert trajectory.samples[0].position_world_m == measured_pose.position_m
