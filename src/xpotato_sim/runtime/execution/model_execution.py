"""名前付きモデルの入力・同一snapshot・Taskを結ぶ、通信非依存の実行所有者。"""
from __future__ import annotations

from dataclasses import replace
import json

from xpotato_sim.plugins.tasks.catalog import resolve_task_plugin
from xpotato_sim.runtime.composition.coordinated_input import CoordinatedInputRuntime
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.scene.task import GeometryTaskObservation
from xpotato_sim.schemas import parse_viewer_control_message_json


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
            control_mapping_parameters=profile.mapping_parameters)
        mapping = selected.control_mapping
        if mapping is None or mapping.session_strategy_factory is None:
            raise ValueError("session Mapping required")
        self.runtime = CoordinatedInputRuntime(
            provider=self.instance.provider, mapping_factory=mapping.session_strategy_factory,
            mapping_parameters=profile.mapping_parameters, side_to_arm=profile.side_to_endpoint,
            epoch=epoch, dt_s=profile.dt_s, max_input_age_s=profile.max_input_age_s, clock=self.clock)
        self.epoch = epoch
        self.task_binding = profile.bind_scene_task()
        self.task_state = None if self.task_binding is None else self.task_binding.initial_state()
        self.task_event = (None if self.task_binding is None else
            resolve_task_plugin(profile.task_selection).task_event_identity)
        self.task_view = None
        self.tick_count = 0
        self.state, self.reason = "waiting_neutral", "awaiting_gamepad_input"
        self.scene_binding = None if profile.scene_plan is None else {
            "schema_version": "scene-contact-binding/v1",
            "scene_digest": profile.scene_plan.manifest.digest,
            "model_sha256": self.instance.viewer.metadata["model_sha256"],
            "epoch": epoch, "endpoint_ids": list(self.instance.provider.endpoint_ids),
            "object_ids": [obj.instance_id for obj in profile.scene_plan.manifest.objects]}

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
            parsed = parse_viewer_control_message_json(message)
            if (parsed.source_kind != "gamepad" or parsed.provider_id != "gamepad/v1"
                    or parsed.provider_schema != "viewer_gamepad_sample/v1"):
                raise ValueError("coordinated viewer requires explicit gamepad/v1 input")
            self.runtime.ingest(parsed)
        except Exception as exc:
            self.runtime.runtime.fail(f"source_ingress_failed:{type(exc).__name__}:{exc}")
            raise

    def tick(self):
        """中立待ちはstepしない。戻り値は実際にcommitされたtick数。"""
        if (self.runtime.source.last_received_at_s is None
                and self.runtime.runtime.state == "waiting_neutral"):
            self.state, self.reason = "waiting_neutral", "awaiting_gamepad_input"
        else:
            result = self.runtime.tick(epoch=self.epoch)
            self.state, self.reason, self.tick_count = result.state, result.reason, result.tick
        return self.tick_count

    def _metadata(self):
        runtime = self.runtime
        metadata = {} if runtime.last_frame is None else dict(runtime.last_frame.metadata)
        metadata.update(dict(self.instance.viewer.metadata))
        metadata.update(physical_output="disabled", motion_status=self.state,
            motion_rejection_reason=self.reason,
            coordinated_runtime_v1={"schema_version": "coordinated-runtime-presentation/v1",
                "state": self.state, "reason": self.reason, "epoch": self.epoch,
                "tick": self.tick_count, "arm_ids": list(self.instance.provider.endpoint_ids)})
        if self.state in {"faulted", "stopped"}:
            metadata.update(source_active=False, stale_reason=self.reason or self.state)
        for name, value in (("gamepad_plane_control_v1", runtime.latest_plane_presentation),
                            ("gamepad_trigger_control_v1", runtime.latest_trigger_presentation)):
            if value is not None:
                metadata[name] = value
        return metadata

    def sample(self, frame_index, *, advance_task=False, budget_exhausted=False, stopped_reason=None):
        """同一model/dataの描画とTask観測を照合する。表示のみではTaskを進めない。"""
        bundle = self.instance.viewer
        sample = self.instance.provider.sample(frame_index=frame_index, metadata=self._metadata())
        snapshot, state, addresses = sample.robot, sample.state, sample.robot_qpos_addresses
        if (snapshot.model_sha256 != bundle.metadata["model_sha256"]
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
            state = replace(state, metadata={**state.metadata, "scene_dynamics_v1": sample.dynamics})
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
                    state = replace(state, metadata={**state.metadata,
                        "motion_status": self.runtime.runtime.state, "source_active": False,
                        "coordinated_runtime_v1": {**state.metadata["coordinated_runtime_v1"],
                            "state": self.runtime.runtime.state, "reason": self.task_view["reason"]},
                        "motion_rejection_reason": self.task_view["reason"]})
            metadata = {**state.metadata, "scene_contact_geometry_v1": geometry.to_document(),
                "scene_contact_binding_v1": self.scene_binding}
            if self.task_view is not None:
                metadata["scene_contact_task_v1"] = {**self.task_view,
                    "presentation_frame_index": frame_index, "presentation_time_s": state.time_s}
            state = replace(state, metadata=metadata)
        return state

    def stop(self):
        """未commit候補を無効化する。Task結果の判定は変更しない。"""
        self.runtime.stop()
        self.state, self.reason = self.runtime.runtime.state, self.runtime.runtime.reason
