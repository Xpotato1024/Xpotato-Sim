from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

import xpotato_sim.runtime.runners.websocket_publisher as websocket_runner_module
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.plugins.input_sources.replay import ReplayInputSource
from tests.support.input_source_doubles import StaticInputSource
from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import FastArmEndpointInverseKinematicsSolver
from tests.support.kinematics_solver_doubles import ZeroInverseKinematicsSolver
from xpotato_sim.motion import TargetToJointMotionGenerator
from xpotato_sim.mujoco_backend import HeadlessMuJoCoSimulator
from tests.support.mujoco_doubles import NoOpMuJoCoSimulator
from xpotato_sim.runtime.evaluation.endpoint_metrics import EndpointEvaluationStatePublisher
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
from xpotato_sim.runtime.runners.dry_run import run_replay_mujoco_dry_run
from xpotato_sim.runtime.runners.websocket_publisher import run_replay_mujoco_websocket_publisher
from xpotato_sim.schemas import JointCommand, MuJoCoState
from xpotato_sim.transport import WebSocketStatePublisher
from tests.support.transport_doubles import NoOpStatePublisher


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SOURCE_MODULES = tuple((ROOT / "src" / "xpotato_sim").rglob("*.py"))
FORBIDDEN_RUNTIME_SYMBOLS = (
    "StaticInputSource",
    "NoOpMotionGenerator",
    "NoOpMuJoCoSimulator",
    "NoOpStatePublisher",
    "ZeroForwardKinematicsSolver",
    "ZeroInverseKinematicsSolver",
)
FORBIDDEN_RUNTIME_MODULES = {
    "tests.support.input_source_doubles",
    "tests.support.kinematics_solver_doubles",
    "tests.support.motion_doubles",
    "tests.support.mujoco_doubles",
    "tests.support.transport_doubles",
}



class RecordingPublisher:
    def __init__(self) -> None:
        self.states: list[MuJoCoState] = []

    async def publish(self, state: MuJoCoState) -> None:
        self.states.append(state)


class _DummyWebSocketPublisherServer:
    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.bound_port = port
        self.wait_for_client_calls: list[float | None] = []

    async def __aenter__(self) -> "_DummyWebSocketPublisherServer":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def wait_for_client(self, timeout_s: float | None = None) -> bool:
        self.wait_for_client_calls.append(timeout_s)
        return True


class _DummyPipeline:
    def __init__(self) -> None:
        self.run_once_calls: list[float | None] = []

    async def run_once(self, dt_s: float | None = None) -> object:
        self.run_once_calls.append(dt_s)
        return object()


def test_production_source_does_not_reference_test_double_symbols_or_modules() -> None:
    for path in PRODUCTION_SOURCE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module not in FORBIDDEN_RUNTIME_MODULES
                assert not node.module.endswith(".stubs")
                imported_names = {alias.name for alias in node.names}
                assert imported_names.isdisjoint(FORBIDDEN_RUNTIME_SYMBOLS)
            elif isinstance(node, ast.Import):
                imported_names = {alias.name for alias in node.names}
                assert imported_names.isdisjoint(FORBIDDEN_RUNTIME_MODULES)
                assert not any(name.endswith(".stubs") for name in imported_names)

        source = path.read_text(encoding="utf-8")
        offending = [symbol for symbol in FORBIDDEN_RUNTIME_SYMBOLS if symbol in source]
        assert not offending, f"{path.relative_to(ROOT)} references forbidden stub symbols: {offending}"


def test_build_concrete_mujoco_pipeline_uses_concrete_components() -> None:
    publisher = RecordingPublisher()
    pipeline = build_concrete_mujoco_pipeline(publisher=publisher)

    assert isinstance(pipeline.input_source, ReplayInputSource)
    assert not isinstance(pipeline.input_source, StaticInputSource)
    assert pipeline.control_mapping is REPLAY_CONTROL_MAPPING_PLUGIN
    assert isinstance(pipeline.motion_generator, TargetToJointMotionGenerator)
    assert isinstance(pipeline.motion_generator._ik_solver, FastArmEndpointInverseKinematicsSolver)
    assert not isinstance(pipeline.motion_generator._ik_solver, ZeroInverseKinematicsSolver)
    assert isinstance(pipeline.simulator, HeadlessMuJoCoSimulator)
    assert not isinstance(pipeline.simulator, NoOpMuJoCoSimulator)
    assert isinstance(pipeline.publisher, EndpointEvaluationStatePublisher)
    assert pipeline.publisher.publisher is publisher
    assert not isinstance(pipeline.publisher, NoOpStatePublisher)


def test_build_concrete_mujoco_pipeline_emits_non_empty_joint_command_and_four_dof_qpos() -> None:
    publisher = RecordingPublisher()
    command_pipeline = build_concrete_mujoco_pipeline(publisher=publisher)
    frame = command_pipeline.input_source.read_frame()
    intent = command_pipeline.map_input(frame)
    command = command_pipeline.motion_generator.update(intent, dt_s=1.0 / 60.0)

    state_pipeline = build_concrete_mujoco_pipeline(publisher=publisher)
    state = asyncio.run(state_pipeline.run_once())

    assert command.joint is not None
    assert command.joint != JointCommand()
    assert command.joint.joint_angles_rad != ()
    assert len(command.joint.joint_angles_rad) == 4
    assert command.joint.joint_angles_rad[:2] != (0.0, 0.0)
    assert command.joint.joint_angles_rad[2:] != (0.0, 0.0)
    assert state_pipeline.simulator.last_joint_position_command is not None
    assert state.qpos[:4] == pytest.approx(
        state_pipeline.simulator.last_joint_position_command.joint_angles_rad,
        abs=1e-9,
    )


def test_run_replay_mujoco_dry_run_default_path_uses_concrete_pipeline() -> None:
    payload = json.loads(run_replay_mujoco_dry_run(steps=1)[0])

    assert payload["qpos"][:4] != [0.0, 0.0, 0.0, 0.0]
    assert payload["version"] == 0


def test_run_replay_mujoco_dry_run_sweep_x_path_remains_explicit_compatibility_path() -> None:
    payload = json.loads(run_replay_mujoco_dry_run(steps=1, preset="sweep_x")[0])

    assert payload["metadata"]["preset"] == "sweep_x"
    assert payload["metadata"]["source_kind"] == "programmed_target"
    assert payload["metadata"]["trajectory_name"] == "sweep_x"
    assert payload["metadata"]["phase"] == "initial_hold"
    assert payload["metadata"]["desired_endpoint_m"] == payload["target_position_m"]


def test_run_replay_mujoco_websocket_publisher_uses_websocket_state_publisher(monkeypatch) -> None:
    captured: dict[str, object] = {}
    dummy_pipeline = _DummyPipeline()

    def fake_build_concrete_mujoco_pipeline(*, publisher, **kwargs):
        captured["publisher"] = publisher
        return dummy_pipeline

    monkeypatch.setattr(websocket_runner_module, "WebSocketPublisherServer", _DummyWebSocketPublisherServer)
    monkeypatch.setattr(websocket_runner_module, "build_concrete_mujoco_pipeline", fake_build_concrete_mujoco_pipeline)

    run_replay_mujoco_websocket_publisher(
        host="127.0.0.1",
        port=8766,
        steps=2,
        dt_s=1.0 / 60.0,
        interval_s=0.0,
        grace_period_s=0.0,
    )

    assert isinstance(captured["publisher"], WebSocketStatePublisher)
    assert dummy_pipeline.run_once_calls == [1.0 / 60.0, 1.0 / 60.0]
