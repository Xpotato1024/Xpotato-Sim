"""既存の入力planとphysical sessionを専有する、caller-driven有限実行owner。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal

from xpotato_sim.runtime.control.input_source_state import (
    annotate_raw_input_frame, reconcile_runtime_input_source_state,
    build_runtime_input_source_state_from_health,
)
from xpotato_sim.runtime.execution.input_step_loop import RuntimeInputSourceStepLoopPlan
from xpotato_sim.runtime.execution.command_routes import project_joint_position_command
from xpotato_sim.runtime.experiment.input_source import InputSourceHealth, ManagedInputSource
from xpotato_sim.runtime.output.fast_arm_adapter import FastArmPhysicalOutputResult, FastArmPhysicalOutputSession
from xpotato_sim.runtime.output.fast_arm_observation import (
    BoundedFastArmObservationDriver, FastArmObservationTick, observation_timestamp,
)
from xpotato_sim.runtime.output.safety_gate import PhysicalOutputSafetyEvaluation, evaluate_and_bind_physical_output_safety
from xpotato_sim.runtime.safety.input_safety import build_runtime_input_safety_result
from xpotato_sim.runtime.safety.physical_safety_core import SafetyInput
from xpotato_sim.schemas import JointPositionCommand, MotionCommand, MuJoCoState, PhysicalOutputPermission, PhysicalOutputRequest, RawInputFrame


@dataclass(frozen=True, slots=True)
class FastArmRuntimeTick:
    """source時刻、host指令、simulation、P5、transport/ACKを混同しない1 tickの証拠。"""

    index: int
    phase: Literal["waiting_ack", "waiting_cadence", "dispatched", "terminal"]
    reason: str | None = None
    exception_type: str | None = None
    frame: RawInputFrame | None = None
    health: InputSourceHealth | None = None
    simulation_before: MuJoCoState | None = None
    simulation_after: MuJoCoState | None = None
    source_command: JointPositionCommand | None = None
    request: PhysicalOutputRequest | None = None
    evaluation: PhysicalOutputSafetyEvaluation | None = None
    output: FastArmPhysicalOutputResult | None = None
    observation: FastArmObservationTick | None = None


class FastArmInputRuntime:
    """resolved plan/sessionを単一threadで専有する。constructorは取得・arm・通信を開始しない。

    safety_inputは外部の明示producerが所有し、simulation snapshotをphysical measurementへ
    昇格しない。callerは同じhost monotonic clockでsession/transport/readerを構成する。
    startに明示permissionが必要で、stop後の再arm、command queue、schedulerは提供しない。
    """

    def __init__(
        self, plan: RuntimeInputSourceStepLoopPlan, session: FastArmPhysicalOutputSession, *,
        software_revision: str, safety_input: Callable[[PhysicalOutputRequest, MuJoCoState], SafetyInput],
        receive_nowait: Callable[[], bytes | None], clock: Callable[[], float],
        max_ticks: int, max_datagrams_per_tick: int,
    ) -> None:
        if not isinstance(plan, RuntimeInputSourceStepLoopPlan) or type(session) is not FastArmPhysicalOutputSession:
            raise TypeError("resolved input plan and physical session are required")
        if not callable(safety_input) or not callable(clock):
            raise TypeError("explicit safety input producer and clock are required")
        if type(max_ticks) is not int or max_ticks < 1:
            raise ValueError("max_ticks must be a positive integer")
        if type(software_revision) is not str or not software_revision or software_revision != session.transport_config.software_revision:
            raise ValueError("runtime/session software revision mismatch")
        if session.state != "disarmed" or session.lifecycle.state != "disabled":
            raise ValueError("runtime requires a new disarmed session")
        pipeline = plan.pipeline
        selection = plan.selection
        if selection.resolved_plugin is None or not isinstance(pipeline.input_source, ManagedInputSource):
            raise TypeError("runtime requires a resolved managed input source")
        if pipeline.input_source is not selection.runtime_reader:
            raise ValueError("runtime source differs from resolved selection")
        if pipeline.control_mapping is not selection.control_mapping or pipeline.control_mapping is not plan.control_mapping:
            raise ValueError("runtime Mapping differs from resolved selection")
        if selection.effective_mapping_input_sample_schema not in pipeline.control_mapping.accepted_input_sample_schemas:
            raise ValueError("runtime source/Mapping schemas differ")
        if pipeline.command_execution is not plan.command_execution or pipeline.command_semantics_route != plan.command_semantics_route:
            raise ValueError("runtime plan command binding changed")
        if pipeline.command_semantics_route != pipeline.control_mapping.resolve_command_semantics_route(selection.command_semantics_route_selection):
            raise ValueError("runtime route differs from selected Mapping")
        if pipeline.command_execution.command_type is not JointPositionCommand:
            raise ValueError("physical runtime requires a joint-position route")
        if pipeline.config.robot_profile_id != session.profile.profile_id or pipeline.config.robot_logical_version != session.profile.profile_contract_version:
            raise ValueError("runtime/session Robot profile mismatch")
        if len(pipeline.simulator.snapshot().qpos) != session.profile.qpos_dimension:
            raise ValueError("runtime/session joint dimension mismatch")
        self._plan, self._session = plan, session
        self._reader = pipeline.input_source
        self._revision, self._safety_input, self._clock = software_revision, safety_input, clock
        self._budget, self._ticks, self._sequence = max_ticks, 0, 0
        self._started = self._closed = self._reader_started = self._busy = False
        self._last_now: float | None = None
        self._last_dispatch: float | None = None
        self.reason: str | None = None
        self.last_tick: FastArmRuntimeTick | None = None
        self._components = self._current_components()
        self._parameters = pipeline.control_mapping.normalize_runtime_parameters(pipeline.control_mapping_parameters)
        self._session_binding = self._current_session_binding()
        self._driver = BoundedFastArmObservationDriver(session, receive_nowait=receive_nowait,
            clock=clock, max_datagrams=max_datagrams_per_tick)

    @property
    def plan(self) -> RuntimeInputSourceStepLoopPlan:
        return self._plan

    @property
    def session(self) -> FastArmPhysicalOutputSession:
        return self._session

    def _current_components(self) -> tuple[object, ...]:
        p = self.plan.pipeline
        return (p, p.config, p.input_source, p.control_mapping, p.command_execution, p.command_semantics_route,
                p.motion_generator, p.qpos_feasibility_guard, p.endpoint_pose_provider, p.simulator,
                self.session, self.session.transport_adapter)

    def _current_session_binding(self) -> tuple[object, ...]:
        s = self.session
        return (s.profile, s.runtime_plugin_id, s.runtime_robot_id, s.target_robot_id, s.endpoint_id,
                s.session_id, s.mapping, s.accepted_evidence, s.transport_config,
                s.cadence_s, s.authorization_ttl_s, s.acknowledgement_timeout_s)

    def _check_binding(self) -> None:
        if any(a is not b for a, b in zip(self._components, self._current_components(), strict=True)):
            raise ValueError("active runtime components changed")
        if self._session_binding != self._current_session_binding() or self._parameters != self.plan.pipeline.control_mapping_parameters:
            raise ValueError("active runtime configuration changed")
        if self.session.transport_adapter.config is not self.session.transport_config:
            raise ValueError("session transport config changed")

    def _now(self) -> float:
        now = observation_timestamp(self._clock())
        if self._last_now is not None and now < self._last_now:
            raise ValueError("runtime clock moved backwards")
        self._last_now = now
        for delay in (self.session.cadence_s, self.session.authorization_ttl_s, self.session.acknowledgement_timeout_s):
            if not isfinite(now + delay) or now + delay <= now:
                raise ValueError("runtime deadline is not representable")
        return now

    @property
    def closed(self) -> bool:
        return self._closed

    def _finish(self, reason: str, *, abort: bool = False, primary: BaseException | None = None) -> None:
        """外部出力の許可を先に撤回し、readerのclose失敗で原例外を失わない。"""
        if self._closed:
            return
        self._closed, self.reason = True, reason
        errors: list[Exception] = []
        if self.session.state not in {"stopped", "aborted", "failed"}:
            try:
                if abort:
                    self.session.abort(now_s=self._last_now)
                else:
                    self.session.stop(now_s=self._last_now)
            except Exception as exc:
                errors.append(exc)
        if self._reader_started:
            self._reader_started = False
            try:
                self._reader.close()
            except Exception as exc:
                errors.append(exc)
        if primary is not None:
            for exc in errors:
                primary.add_note(f"runtime cleanup failed: {exc!r}")
        elif errors:
            self.reason = "cleanup_failure"
            for exc in errors[1:]:
                errors[0].add_note(f"additional runtime cleanup failed: {exc!r}")
            raise errors[0]

    def start(self, physical_permission: PhysicalOutputPermission, transmission_permission: PhysicalOutputPermission) -> None:
        """明示permissionを既存gateで受理した場合だけreaderを開始する。"""
        if self._started or self._closed:
            raise RuntimeError("runtime is single-use")
        self._started = True
        try:
            self._check_binding()
            result = self.session.arm(physical_permission, transmission_permission, now_s=self._now())
            if not result.accepted:
                raise ValueError(result.reason or "physical session arm rejected")
            self._reader_started = True
            self.plan.pipeline.input_source.start()
        except Exception as exc:
            self._finish("start_failure", abort=True, primary=exc)
            raise

    def stop(self) -> None:
        """local sessionの許可を撤回する。実機停止packetや実測停止の証拠ではない。"""
        self._finish("operator_stop")

    def abort(self) -> None:
        """local abort後にreaderを閉じる。新sessionなしの再armは許さない。"""
        self._finish("operator_abort", abort=True)

    def _fresh(self, *, expected_source_kind: str | None = None) -> bool:
        health = self._reader.current_health()
        if health.age_ms is None:
            return False
        state = build_runtime_input_source_state_from_health(health,
            source_kind=self.plan.selection.resolved_plugin.identity.name)
        if expected_source_kind is not None and state.source_kind != expected_source_kind:
            raise ValueError("input source health identity changed")
        return not build_runtime_input_safety_result(MotionCommand(0.0), source_state=state).is_stale

    def _ready_to_dispatch(self, expected_source_kind: str) -> bool:
        """送信先prepareの復帰後にも入力期限・source identity・終了状態を確認する追加veto。"""
        self._check_binding()
        self._now()
        fresh = self._fresh(expected_source_kind=expected_source_kind)
        return not self._closed and fresh

    def tick(self) -> FastArmRuntimeTick:
        """1 tickで最大1指令。応答待ち・cadence待ちでは入力を消費しない。"""
        if not self._started or self._closed:
            raise RuntimeError("runtime is not active")
        if self._busy:
            raise RuntimeError("runtime tick is not reentrant")
        self._busy = True
        record = FastArmRuntimeTick(self._ticks, "terminal")
        self._ticks += 1
        try:
            self._check_binding()
            self._now()
            observation = self._driver.tick()
            record = replace(record, observation=observation)
            if self._closed:
                record = replace(record, reason=self.reason)
                return record
            if self.session.state not in {"armed", "active"}:
                self._finish(observation.acknowledgement.reason or "session_ended")
                record = replace(record, reason=self.reason)
                return record
            if self._ticks >= self._budget:
                self._finish("tick_budget_exhausted")
                record = replace(record, reason=self.reason)
                return record
            if self.session.pending_acknowledgement.status == "pending":
                if not self._fresh():
                    self._finish("source_not_fresh_while_pending")
                    record = replace(record, reason=self.reason)
                    return record
                record = replace(record, phase="waiting_ack")
                return record
            now = self._now()
            if self._last_dispatch is not None and now < self._last_dispatch + self.session.cadence_s:
                if not self._fresh():
                    self._finish("source_not_fresh_while_waiting")
                    record = replace(record, reason=self.reason)
                    return record
                record = replace(record, phase="waiting_cadence")
                return record
            pipeline = self.plan.pipeline
            frame = pipeline.input_source.read_frame()
            health = pipeline.input_source.current_health()
            if frame.source != self.plan.selection.resolved_plugin.identity.name:
                raise ValueError("raw input source identity mismatch")
            state = reconcile_runtime_input_source_state(frame, health,
                source_kind=self.plan.selection.resolved_plugin.identity.name)
            frame_source_kind = frame.metadata.get("source_kind")
            if frame_source_kind is not None and str(frame_source_kind) != state.source_kind:
                raise ValueError("input source frame and health identities differ")
            frame = annotate_raw_input_frame(frame, state)
            record = replace(record, frame=frame, health=health)
            self._now()
            if self._closed:
                record = replace(record, reason=self.reason)
                return record
            if not self._fresh(expected_source_kind=state.source_kind):
                self._finish("source_not_fresh")
                record = replace(record, reason=self.reason)
                return record
            before = pipeline.simulator.snapshot()
            intent = pipeline.map_input(frame, pre_step_state=before)
            safety = pipeline.execute_intent(intent, dt_s=self.session.cadence_s, pre_step_state=before, source_state=state)
            source_command = pipeline.simulator.last_joint_position_command
            if type(source_command) is not JointPositionCommand:
                raise TypeError("backend did not retain a typed joint command")
            if source_command != project_joint_position_command(safety.motion_command):
                raise ValueError("backend command differs from route result")
            record = replace(record, simulation_before=before, source_command=source_command)
            if safety.is_stale or safety.qpos_feasibility_rejected or safety.motion_command.metadata.get("motion_status") == "held":
                self._finish(safety.stale_reason or "local_motion_rejected_or_held")
                record = replace(record, reason=self.reason)
                return record
            pipeline.simulator.step(self.session.cadence_s)
            issued_at = self._now()
            request = PhysicalOutputRequest(
                target_robot_id=self.session.target_robot_id, endpoint_id=self.session.endpoint_id,
                command_semantics="joint_position_command/v1",
                command=replace(source_command, timestamp_s=issued_at), session_id=self.session.session_id,
                sequence=self._sequence, timestamp_s=issued_at, cadence_s=self.session.cadence_s,
                software_revision=self._revision,
            )
            record = replace(record, simulation_after=pipeline.simulator.snapshot(), request=request)
            inputs = self._safety_input(request, before)
            evaluation = evaluate_and_bind_physical_output_safety(request, inputs, checked_at_s=self._now())
            record = replace(record, evaluation=evaluation)
            self._check_binding()
            if self._closed:
                record = replace(record, reason=self.reason)
                return record
            if not self._fresh(expected_source_kind=state.source_kind):
                self._finish("source_stale_during_evaluation")
                record = replace(record, reason=self.reason)
                return record
            output = self.session.submit(
                evaluation,
                pre_dispatch_check=lambda: self._ready_to_dispatch(state.source_kind),
            )
            record = replace(record, output=output)
            if self._closed:
                record = replace(record, reason=self.reason)
                return record
            if output.status != "transmission_attempted" or self.session.pending_acknowledgement.status != "pending":
                self._finish(output.reason or "physical_output_rejected")
                record = replace(record, reason=self.reason)
                return record
            self._sequence += 1
            self._last_dispatch = output.transport_result.attempt.started_at_s
            record = replace(record, phase="dispatched")
            return record
        except Exception as exc:
            record = replace(record, phase="terminal", reason=str(exc) or type(exc).__name__, exception_type=type(exc).__name__)
            self._finish("execution_failure", abort=True, primary=exc)
            raise
        finally:
            self._busy = False
            self.last_tick = replace(record, phase="terminal", reason=record.reason or self.reason) if self._closed else record
