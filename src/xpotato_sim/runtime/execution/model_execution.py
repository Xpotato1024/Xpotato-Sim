"""名前付きモデルの入力・同一snapshot・Taskを結ぶ、通信非依存の実行所有者。"""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import json

from xpotato_sim.plugins.tasks.catalog import resolve_task_plugin
from xpotato_sim.runtime.composition.input_sessions import resolve_source_session
from xpotato_sim.runtime.composition.coordinated_input import CoordinatedInputRuntime
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.scene.task import GeometryTaskObservation
from xpotato_sim.runtime.scene.force_signal import SceneForceProcessor, default_force_config, force_condition
from xpotato_sim.runtime.scene.force_presentation import presentation_binding
from xpotato_sim.schemas import ViewerControlMessage
from xpotato_sim.runtime.experiment.session_input import SessionInputMessage
from xpotato_sim.runtime.scene.command_mapping import profile_command_mapping, ContactResistanceCommandMapping


class ModelExecution:
    """publisherと有限trialが共有する実行。呼出側が単一所有と予算を監督する。"""

    def __init__(self, profile, *, clock, instance=None):
        self.profile = profile
        self.instance = profile.build_model() if instance is None else instance
        self.clock = clock
        self._bind(profile.epoch)

    def _bind(self, epoch):
        coordination = json.loads(self.profile.coordination_json)
        coordination["epoch"] = epoch
        profile = replace(self.profile, coordination_json=json.dumps(coordination))
        selected = select_runtime_input_source(profile.input_source.plugin_id, steps=1,
            control_mapping_selection=profile.mapping,
            control_mapping_parameters=profile.mapping_parameters, source_parameters=profile.input_parameters)
        mapping = selected.control_mapping
        if mapping is None or mapping.session_strategy_factory is None:
            raise ValueError("session Mapping required")
        session = resolve_source_session(profile.input_source, profile.provider_id)
        self.runtime = CoordinatedInputRuntime(
            provider=self.instance.provider, mapping_factory=mapping.session_strategy_factory,
            mapping_parameters=profile.mapping_parameters, side_to_arm=profile.side_to_endpoint,
            epoch=epoch, dt_s=profile.dt_s, max_input_age_s=profile.max_input_age_s, clock=self.clock,
            source_factory=session.factory, source_parameters=profile.input_parameters)
        self.epoch = epoch
        self.task_binding = profile.bind_scene_task()
        self.task_state = None if self.task_binding is None else self.task_binding.initial_state()
        self.task_event = (None if self.task_binding is None else
            resolve_task_plugin(profile.task_selection).task_event_identity)
        self.task_view = None
        self.tick_count = 0
        self.state, self.reason = "waiting_neutral", self.runtime.initial_wait_reason
        self.scene_binding = None if profile.scene_plan is None else {
            "schema_version": "scene-contact-binding/v1",
            "scene_digest": profile.scene_plan.manifest.digest,
            "model_sha256": self.instance.viewer.metadata["model_sha256"],
            "epoch": epoch, "endpoint_ids": list(self.instance.provider.endpoint_ids),
            "object_ids": [obj.instance_id for obj in profile.scene_plan.manifest.objects]}
        self.force_presentation = presentation_binding(profile, epoch, self.instance.viewer.metadata["model_sha256"])
        signal_condition = force_condition(profile)
        self.force_processor = None if signal_condition is None else SceneForceProcessor(
            model_sha256=self.instance.viewer.metadata["model_sha256"],
            scene_digest=profile.scene_plan.manifest.digest,
            settings_digest=None if profile.scene_plan.dynamics is None else profile.scene_plan.dynamics.digest,
            endpoint_ids=tuple(signal_condition["endpoint_ids"]),
            object_ids=tuple(self.scene_binding["object_ids"]),
            target_object_ids=tuple(signal_condition["target_object_ids"]),
            config=default_force_config(profile.max_input_age_s))
        command = profile_command_mapping(profile)
        self.command_mapping = None if command is None else ContactResistanceCommandMapping(command,
            epoch=epoch, model_sha256=self.instance.viewer.metadata["model_sha256"],
            scene_digest=profile.scene_plan.manifest.digest, settings_digest=profile.scene_plan.dynamics.digest)
        self._command_sample = self._command_signal = None
        self.runtime.runtime.command_mapper = None if self.command_mapping is None else self._map_commands

    def _map_commands(self, commands, **context):
        contacts = () if self._command_sample is None or self._command_sample.dynamics is None else self._command_sample.dynamics["contacts"]
        return self.command_mapping.apply(commands, signal=self._command_signal, contacts=contacts, **context)

    def _capture_command_sample(self, *, armed=False):
        """中立での初期化またはcommit直後だけ観測。GUI更新は制御sampleを更新しない。"""
        sample = self.instance.provider.sample(frame_index=self.tick_count, metadata={})
        if armed:
            self.force_processor.reset()
        self._command_sample = sample
        self._command_signal = self.force_processor.process(sample.dynamics, epoch=self.epoch,
            tick=self.tick_count, frame_index=self.tick_count, simulation_time_s=sample.state.time_s, now_s=self.clock())

    def reset(self, epoch):
        """modelを保持し、全dataとSource/Mapping/Taskの試行参照を置換する。"""
        self.stop()
        self.instance.provider.reset()
        if self.instance.provider.preflight() is not True:
            raise RuntimeError("reset preflight rejected")
        self._bind(epoch)

    def ingest(self, message):
        """旧publisherのwire validationを共用する。epoch gateは有限runnerが所有する。"""
        if self.runtime.runtime.state in {"faulted", "stopped"}:
            return
        try:
            parsed = self.parse_input_message(message)
        except Exception as exc:
            self.runtime.runtime.fail(f"source_ingress_failed:{type(exc).__name__}:{exc}")
            raise
        self._ingest_parsed(parsed)

    def parse_input_message(self, message):
        return self.runtime.source.parse_message(message)

    def _ingest_parsed(self, parsed: SessionInputMessage | ViewerControlMessage):
        """wire検証を終えたownerから同じtyped sampleを受け取り、再decodeしない。"""
        if self.runtime.runtime.state in {"faulted", "stopped"}:
            return
        try:
            self.runtime.ingest(parsed)
        except Exception as exc:
            self.runtime.runtime.fail(f"source_ingress_failed:{type(exc).__name__}:{exc}")
            raise

    def tick(self, *, freshness_barrier=None):
        """中立待ちはstepしない。戻り値は実際にcommitされたtick数。"""
        previous_state, previous_tick = self.state, self.tick_count
        if (self.runtime.source.last_received_at_s is None
                and self.runtime.runtime.state == "waiting_neutral"):
            self.state, self.reason = "waiting_neutral", self.runtime.initial_wait_reason
        else:
            result = self.runtime.tick(epoch=self.epoch, freshness_barrier=freshness_barrier)
            if result is None:
                return self.tick_count
            self.state, self.reason, self.tick_count = result.state, result.reason, result.tick
        if self.command_mapping is not None and self.state == "running":
            armed = previous_state == "waiting_neutral"
            if armed or self.tick_count != previous_tick:
                try:
                    self._capture_command_sample(armed=armed)
                except Exception as exc:
                    self.runtime.runtime.fail(f"command_observation_failed:{type(exc).__name__}:{exc}")
                    self.state, self.reason = self.runtime.runtime.state, self.runtime.runtime.reason
        if self.command_mapping is not None and self.state in {"stopped", "faulted"}:
            self.command_mapping.invalidate(self.reason or self.state)
        return self.tick_count

    def _metadata(self):
        runtime = self.runtime
        metadata = {} if runtime.last_frame is None else dict(runtime.last_frame.metadata)
        metadata.update(dict(self.instance.viewer.metadata))
        if runtime.source.last_received_at_s is not None:
            metadata.update(input_received_at_s=runtime.source.last_received_at_s,
                command_age_ms=max(0, round((self.clock() - runtime.source.last_received_at_s) * 1000)))
        metadata.update(physical_output="disabled", motion_status=self.state,
            motion_rejection_reason=self.reason,
            coordinated_runtime_v1={"schema_version": "coordinated-runtime-presentation/v1",
                "state": self.state, "reason": self.reason, "epoch": self.epoch,
                "tick": self.tick_count, "arm_ids": list(self.instance.provider.endpoint_ids)})
        if self.state in {"faulted", "stopped"}:
            metadata.update(source_active=False, stale_reason=self.reason or self.state)
        for name, value in (("gamepad_trigger_control_v1", runtime.latest_trigger_presentation),):
            if value is not None:
                metadata[name] = value
        if self.force_presentation is not None:
            metadata["force_presentation_v1"] = deepcopy(self.force_presentation)
        if self.command_mapping is not None and self.command_mapping.latest is not None:
            metadata["command_mapping_v1"] = deepcopy(self.command_mapping.latest)
        return metadata

    def sample(self, frame_index, *, advance_task=False, budget_exhausted=False, stopped_reason=None):
        """同一model/dataの描画とTask観測を照合する。表示のみではTaskを進めない。"""
        bundle = self.instance.viewer
        metadata = self._metadata()
        sample = self._command_sample
        if sample is None or sample.robot != self.instance.provider.snapshot():
            sample = self.instance.provider.sample(frame_index=frame_index, metadata=metadata)
        else:
            # 同じ確定観測を表示frameへ投影する。原力/poseを再計算・再取得しない。
            sample = replace(sample,
                state=replace(sample.state, frame_index=frame_index, metadata=metadata),
                geometry=None if sample.geometry is None else replace(sample.geometry, frame_index=frame_index),
                dynamics=None if sample.dynamics is None else {**deepcopy(sample.dynamics), "frame_index": frame_index})
        snapshot, state, addresses = sample.robot, sample.state, sample.robot_qpos_addresses
        if (snapshot.model_sha256 != metadata["model_sha256"]
                or snapshot.joint_names != bundle.declaration.joint_names
                or len(snapshot.joint_positions_rad) != bundle.declaration.qpos_dimension
                or len(addresses) != len(snapshot.joint_positions_rad)
                or len(set(addresses)) != len(addresses)
                or any(type(i) is not int or i < 0 or i >= len(state.qpos) for i in addresses)):
            raise ValueError("backend/viewer assembly declaration mismatch")
        if (tuple(state.qpos[i] for i in addresses) != snapshot.joint_positions_rad
                or state.time_s != snapshot.simulation_time_s):
            raise ValueError("backend/viewer snapshot mismatch")
        if sample.dynamics is not None:
            metadata = {**state.metadata, "scene_dynamics_v1": sample.dynamics}
        else:
            metadata = dict(state.metadata)
        if self.force_processor is not None:
            signal = self.force_processor.process(sample.dynamics, epoch=self.epoch,
                tick=self.tick_count, frame_index=frame_index,
                simulation_time_s=state.time_s, now_s=self.clock())
            if self.state != "running":
                self.force_processor.invalidate(self.reason or self.state)
                signal = self.force_processor.latest
            metadata["scene_force_signal_v1"] = signal.to_document()
        if self.task_binding is not None:
            geometry = sample.geometry
            if (geometry is None or geometry.model_sha256 != snapshot.model_sha256
                    or geometry.simulation_time_s != state.time_s or geometry.frame_index != frame_index):
                raise ValueError("scene geometry and applied Robot sample differ")
            if advance_task and self.task_state.classification is TaskTerminalClassification.RUNNING:
                stopped = stopped_reason or (self.reason if self.state in {"faulted", "stopped"} else None)
                transition = self.task_binding.advance(self.task_state,
                    GeometryTaskObservation(geometry, stopped, budget_exhausted=budget_exhausted))
                self.task_state = transition.state
                self.task_view = dict(transition.evidence.require(self.task_event).value)
                if transition.classification is not TaskTerminalClassification.RUNNING:
                    self.stop()
                    if self.force_processor is not None:
                        metadata["scene_force_signal_v1"] = self.force_processor.latest.to_document()
                    if self.command_mapping is not None and self.command_mapping.latest is not None:
                        metadata["command_mapping_v1"] = deepcopy(self.command_mapping.latest)
                    metadata.update({
                        "motion_status": self.runtime.runtime.state, "source_active": False,
                        "coordinated_runtime_v1": {**metadata["coordinated_runtime_v1"],
                            "state": self.runtime.runtime.state, "reason": self.task_view["reason"]},
                        "motion_rejection_reason": self.task_view["reason"]})
            metadata.update({"scene_contact_geometry_v1": geometry.to_document(),
                "scene_contact_binding_v1": deepcopy(self.scene_binding)})
            if self.task_view is not None:
                metadata["scene_contact_task_v1"] = {**deepcopy(self.task_view),
                    "presentation_frame_index": frame_index, "presentation_time_s": state.time_s}
        return replace(state, metadata=metadata)

    def stop(self):
        """未commit候補を無効化する。Task結果の判定は変更しない。"""
        self.runtime.stop()
        self.state, self.reason = self.runtime.runtime.state, self.runtime.runtime.reason
        if self.force_processor is not None:
            self.force_processor.invalidate(self.reason or self.state)
        if self.command_mapping is not None:
            self.command_mapping.invalidate(self.reason or self.state)
        self._command_sample = self._command_signal = None
