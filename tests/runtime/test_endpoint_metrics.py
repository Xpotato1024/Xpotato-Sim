from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from xpotato_sim.mujoco_backend.endpoint_extraction import RuntimeMuJoCoEndpointEvaluation
from xpotato_sim.runtime.evaluation.endpoint_metrics import (
    RuntimeEndpointEvaluationMetrics,
    build_endpoint_evaluation_state_publisher,
    build_runtime_endpoint_evaluation_metrics,
    build_runtime_endpoint_evaluation_payload_from_state,
    build_runtime_endpoint_evaluation_payload,
    compute_error_norm_m,
    compute_vector_error_m,
    runtime_endpoint_evaluation_metrics_to_payload,
)
from xpotato_sim.runtime.evaluation.kinematics import (
    RuntimeForwardKinematicsEvaluation,
    evaluate_fk_endpoint_from_joint_command,
)
from xpotato_sim.runtime.composition.robot_profile import EndpointReference
from xpotato_sim.runtime.composition.robot_bundle import EndpointPoseObservation
from xpotato_sim.schemas import (
    BodyTransform,
    JointCommand,
    MotionCommand,
    MuJoCoState,
    SiteTransform,
)
from tests.support.kinematics_solver_doubles import (
    FailingForwardKinematicsSolver,
    FixedForwardKinematicsSolver,
)


@dataclass(frozen=True, slots=True)
class _ReferenceEndpointPoseProvider:
    reference: EndpointReference

    def observe_endpoint_pose(self, state: MuJoCoState) -> EndpointPoseObservation:
        if self.reference.site_name is not None:
            transforms = state.sites
            name = self.reference.site_name
        else:
            transforms = state.bodies
            assert self.reference.body_name is not None
            name = self.reference.body_name
        transform = next((item for item in transforms if item.name == name), None)
        if transform is None:
            return EndpointPoseObservation(position_m=None, quaternion_wxyz=None)
        return EndpointPoseObservation(
            position_m=transform.position_m,
            quaternion_wxyz=transform.quaternion_wxyz,
        )


def _site_endpoint_pose_provider() -> _ReferenceEndpointPoseProvider:
    return _ReferenceEndpointPoseProvider(
        EndpointReference(site_name="tip", body_name=None)
    )


@pytest.mark.parametrize(
    ("site_name", "body_name"),
    [("", None), (None, ""), (" tip", None), (None, "tool ")],
)
def test_endpoint_reference_rejects_empty_or_untrimmed_names(
    site_name: str | None,
    body_name: str | None,
) -> None:
    with pytest.raises(ValueError, match="endpoint reference .* must not be empty"):
        EndpointReference(site_name=site_name, body_name=body_name)


class _CommandSource:
    last_command: MotionCommand | None = None


class _RecordingPublisher:
    async def publish(self, state: MuJoCoState) -> None:
        self.state = state


def test_compute_vector_error_and_norm_use_end_minus_start_semantics() -> None:
    error_vector_m = compute_vector_error_m(start_m=(0.1, 0.2, 0.3), end_m=(0.4, 0.6, 0.8))

    assert error_vector_m == pytest.approx((0.3, 0.4, 0.5), abs=1e-9)
    assert compute_error_norm_m(error_vector_m) == pytest.approx(math.sqrt(0.3**2 + 0.4**2 + 0.5**2), abs=1e-9)


def test_build_runtime_endpoint_evaluation_metrics_keeps_desired_qpos_fk_and_site_in_one_object() -> None:
    solver = FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0))
    joint_command = JointCommand(joint_angles_rad=(0.3, -0.2))
    fk_evaluation = evaluate_fk_endpoint_from_joint_command(solver, joint_command)
    site_evaluation = RuntimeMuJoCoEndpointEvaluation(
        role="tip",
        kind="site",
        name="tip",
        position_m=(0.0, 0.0, 0.5),
    )

    metrics = build_runtime_endpoint_evaluation_metrics(
        desired_endpoint_m=(0.0, 0.0, 0.0),
        fk_evaluation=fk_evaluation,
        site_evaluation=site_evaluation,
        qpos_like_joint_angles_rad=(0.3, -0.2, 0.0, 0.0),
    )

    assert isinstance(metrics, RuntimeEndpointEvaluationMetrics)
    assert metrics.desired_endpoint_m == (0.0, 0.0, 0.0)
    assert metrics.qpos_like_joint_angles_rad == (0.3, -0.2, 0.0, 0.0)
    assert metrics.fk_endpoint_m == (0.3, 0.4, 0.0)
    assert metrics.site_endpoint_m == (0.0, 0.0, 0.5)
    assert metrics.fk_evaluation is fk_evaluation
    assert metrics.site_evaluation is site_evaluation
    assert metrics.desired_to_fk_error_vector_m == (0.3, 0.4, 0.0)
    assert metrics.desired_to_site_error_vector_m == (0.0, 0.0, 0.5)
    assert metrics.fk_to_site_error_vector_m == (-0.3, -0.4, 0.5)
    assert metrics.desired_to_fk_error_norm_m == pytest.approx(0.5, abs=1e-9)
    assert metrics.desired_to_site_error_norm_m == pytest.approx(0.5, abs=1e-9)
    assert metrics.fk_to_site_error_norm_m == pytest.approx(math.sqrt(0.5), abs=1e-9)
    assert metrics.unit == "meter"
    assert metrics.desired_endpoint_coordinate_frame == "command-side endpoint frame"
    assert metrics.fk_endpoint_coordinate_frame == "solver-defined frame"
    assert metrics.site_endpoint_coordinate_frame == "MuJoCo world / scene frame"
    assert "diagnostic only" in metrics.frame_mismatch_note
    assert not hasattr(metrics, "target_position_m")

    payload = runtime_endpoint_evaluation_metrics_to_payload(metrics)
    assert payload["desired_endpoint_m"] == [0.0, 0.0, 0.0]
    assert payload["qpos_like_joint_angles_rad"] == [0.3, -0.2, 0.0, 0.0]
    assert payload["unit"] == "meter"
    assert solver.calls == [(0.3, -0.2)]


@pytest.mark.parametrize(
    ("desired_endpoint_m", "fk_evaluation", "site_evaluation", "match"),
    [
        (
            None,
            RuntimeForwardKinematicsEvaluation((0.1,), (0.1,), (0.1, 0.2, 0.3)),
            RuntimeMuJoCoEndpointEvaluation("tip", "site", "tip", (0.1, 0.2, 0.3)),
            "desired_endpoint_m is required",
        ),
        (
            (0.1, 0.2, 0.3),
            None,
            RuntimeMuJoCoEndpointEvaluation("tip", "site", "tip", (0.1, 0.2, 0.3)),
            "fk_evaluation is required",
        ),
        (
            (0.1, 0.2, 0.3),
            RuntimeForwardKinematicsEvaluation((0.1,), (0.1,), (0.1, 0.2, 0.3)),
            None,
            "site_evaluation is required",
        ),
    ],
)
def test_build_runtime_endpoint_evaluation_metrics_rejects_missing_inputs(
    desired_endpoint_m: tuple[float, float, float] | None,
    fk_evaluation: RuntimeForwardKinematicsEvaluation | None,
    site_evaluation: RuntimeMuJoCoEndpointEvaluation | None,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        build_runtime_endpoint_evaluation_metrics(
            desired_endpoint_m=desired_endpoint_m,
            fk_evaluation=fk_evaluation,
            site_evaluation=site_evaluation,
        )


@pytest.mark.parametrize(
    ("desired_endpoint_m", "fk_endpoint_m", "site_endpoint_m", "match"),
    [
        (
            (0.1, 0.2),
            (0.4, 0.5, 0.6),
            (0.7, 0.8, 0.9),
            "desired_endpoint_m must contain exactly three values",
        ),
        (
            (0.1, 0.2, 0.3),
            (0.4, 0.5),
            (0.7, 0.8, 0.9),
            "fk_evaluation.endpoint_m must contain exactly three values",
        ),
        (
            (0.1, 0.2, 0.3),
            (0.4, 0.5, 0.6),
            (0.7, 0.8),
            "site_evaluation.position_m must contain exactly three values",
        ),
    ],
)
def test_build_runtime_endpoint_evaluation_metrics_rejects_malformed_vectors(
    desired_endpoint_m: tuple[float, ...],
    fk_endpoint_m: tuple[float, ...],
    site_endpoint_m: tuple[float, ...],
    match: str,
) -> None:
    fk_evaluation = RuntimeForwardKinematicsEvaluation((0.1, 0.2), (0.1, 0.2), fk_endpoint_m)
    site_evaluation = RuntimeMuJoCoEndpointEvaluation("tip", "site", "tip", site_endpoint_m)

    with pytest.raises(ValueError, match=match):
        build_runtime_endpoint_evaluation_metrics(
            desired_endpoint_m=desired_endpoint_m,
            fk_evaluation=fk_evaluation,
            site_evaluation=site_evaluation,
        )


def test_build_runtime_endpoint_evaluation_metrics_requires_meter_units() -> None:
    fk_evaluation = RuntimeForwardKinematicsEvaluation(
        (0.1, 0.2),
        (0.1, 0.2),
        (0.4, 0.5, 0.6),
        unit="centimeter",
    )
    site_evaluation = RuntimeMuJoCoEndpointEvaluation("tip", "site", "tip", (0.7, 0.8, 0.9))

    with pytest.raises(ValueError, match="fk_evaluation.unit must use meter units"):
        build_runtime_endpoint_evaluation_metrics(
            desired_endpoint_m=(0.1, 0.2, 0.3),
            fk_evaluation=fk_evaluation,
            site_evaluation=site_evaluation,
        )

    fk_evaluation = RuntimeForwardKinematicsEvaluation((0.1, 0.2), (0.1, 0.2), (0.4, 0.5, 0.6))
    site_evaluation = RuntimeMuJoCoEndpointEvaluation(
        "tip",
        "site",
        "tip",
        (0.7, 0.8, 0.9),
        unit="centimeter",
    )

    with pytest.raises(ValueError, match="site_evaluation.unit must use meter units"):
        build_runtime_endpoint_evaluation_metrics(
            desired_endpoint_m=(0.1, 0.2, 0.3),
            fk_evaluation=fk_evaluation,
            site_evaluation=site_evaluation,
        )


def test_build_runtime_endpoint_evaluation_metrics_rejects_empty_qpos_like_input() -> None:
    fk_evaluation = RuntimeForwardKinematicsEvaluation((), (), (0.4, 0.5, 0.6))
    site_evaluation = RuntimeMuJoCoEndpointEvaluation("tip", "site", "tip", (0.7, 0.8, 0.9))

    with pytest.raises(ValueError, match="qpos_like_joint_angles_rad must contain at least one joint angle"):
        build_runtime_endpoint_evaluation_metrics(
            desired_endpoint_m=(0.1, 0.2, 0.3),
            fk_evaluation=fk_evaluation,
            site_evaluation=site_evaluation,
            qpos_like_joint_angles_rad=(),
        )


def test_build_runtime_endpoint_evaluation_payload_returns_none_for_missing_inputs() -> None:
    assert build_runtime_endpoint_evaluation_payload(
        desired_endpoint_m=None,
        fk_evaluation=None,
        site_evaluation=None,
    ) is None


def test_build_runtime_endpoint_evaluation_payload_from_state_prefers_state_metadata_desired_endpoint_over_state_target_position() -> None:
    state = MuJoCoState(
        frame_index=1,
        time_s=0.0,
        sites=(
            SiteTransform(
                name="tip",
                position_m=(0.5, 0.2, 0.1),
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        ),
        target_position_m=(9.0, 8.0, 7.0),
        metadata={
            "desired_endpoint_m": (0.1, 0.2, 0.3),
            "target_position_m": (0.4, 0.5, 0.6),
        },
    )
    motion_command = MotionCommand(
        timestamp_s=0.0,
        joint=JointCommand(joint_angles_rad=(0.0, 0.0)),
    )

    payload = build_runtime_endpoint_evaluation_payload_from_state(
        state=state,
        motion_command=motion_command,
        fk_solver=FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0)),
        endpoint_pose_provider=_site_endpoint_pose_provider(),
    )

    assert payload is not None
    assert payload["desired_endpoint_m"] == [0.1, 0.2, 0.3]
    assert payload["qpos_like_joint_angles_rad"] == [0.0, 0.0]


def test_build_runtime_endpoint_evaluation_payload_from_state_supports_body_only_endpoint_reference() -> None:
    state = MuJoCoState(
        frame_index=1,
        time_s=0.0,
        bodies=(
            BodyTransform(
                name="tool_body",
                position_m=(0.5, 0.2, 0.1),
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        ),
        metadata={"desired_endpoint_m": (0.1, 0.2, 0.3)},
    )
    provider = _ReferenceEndpointPoseProvider(
        EndpointReference(site_name=None, body_name="tool_body")
    )
    motion_command = MotionCommand(
        timestamp_s=0.0,
        joint=JointCommand(joint_angles_rad=(0.0, 0.0)),
    )

    payload = build_runtime_endpoint_evaluation_payload_from_state(
        state=state,
        motion_command=motion_command,
        fk_solver=FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0)),
        endpoint_pose_provider=provider,
    )

    assert payload is not None
    assert payload["site_endpoint_m"] == [0.5, 0.2, 0.1]
    assert payload["unit"] == "meter"
    assert payload["site_endpoint_coordinate_frame"] == "MuJoCo world / scene frame"


def test_endpoint_evaluation_publisher_rejects_missing_initial_endpoint_transform() -> None:
    provider = _ReferenceEndpointPoseProvider(
        EndpointReference(site_name=None, body_name="missing_body")
    )

    with pytest.raises(
        ValueError,
        match="endpoint pose provider did not resolve an endpoint position",
    ):
        build_endpoint_evaluation_state_publisher(
            _RecordingPublisher(),
            simulator=_CommandSource(),
            fk_solver=FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0)),
            endpoint_pose_provider=provider,
            initial_state=MuJoCoState(frame_index=0, time_s=0.0),
        )


def test_build_runtime_endpoint_evaluation_payload_from_state_uses_target_position_fallback_only_when_desired_endpoint_is_missing() -> None:
    state = MuJoCoState(
        frame_index=1,
        time_s=0.0,
        sites=(
            SiteTransform(
                name="tip",
                position_m=(0.5, 0.2, 0.1),
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        ),
        target_position_m=(9.0, 8.0, 7.0),
        metadata={
            "target_position_m": (0.4, 0.5, 0.6),
        },
    )
    motion_command = MotionCommand(
        timestamp_s=0.0,
        joint=JointCommand(joint_angles_rad=(0.0, 0.0)),
    )

    payload = build_runtime_endpoint_evaluation_payload_from_state(
        state=state,
        motion_command=motion_command,
        fk_solver=FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0)),
        endpoint_pose_provider=_site_endpoint_pose_provider(),
    )

    assert payload is not None
    assert payload["desired_endpoint_m"] == [0.4, 0.5, 0.6]
    assert payload["qpos_like_joint_angles_rad"] == [0.0, 0.0]


def test_build_runtime_endpoint_evaluation_payload_from_state_returns_none_for_fk_failure() -> None:
    state = MuJoCoState(
        frame_index=1,
        time_s=0.0,
        sites=(
            SiteTransform(
                name="tip",
                position_m=(0.5, 0.2, 0.1),
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        ),
        metadata={"desired_endpoint_m": (0.1, 0.2, 0.3)},
    )
    motion_command = MotionCommand(
        timestamp_s=0.0,
        joint=JointCommand(joint_angles_rad=(0.0, 0.0)),
    )
    solver = FailingForwardKinematicsSolver(error_message="fk unavailable")

    payload = build_runtime_endpoint_evaluation_payload_from_state(
        state=state,
        motion_command=motion_command,
        fk_solver=solver,
        endpoint_pose_provider=_site_endpoint_pose_provider(),
    )

    assert payload is None
    assert solver.calls == [(0.0, 0.0)]
