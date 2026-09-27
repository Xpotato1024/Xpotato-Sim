"""有限の合成入力から同一MuJoCo scene・接触Task・no-I/O出力へ結ぶ。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
import re

from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.plugins.environments.catalog import resolve_environment_plugin
from xpotato_sim.plugins.tasks.catalog import resolve_task_plugin
from xpotato_sim.plugins.evaluations.catalog import resolve_evaluation_plugin
from xpotato_sim.plugins.tasks.contact_press_hold_task import derive_contact_outcome
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.robot_bundle import ENDPOINT_POSE_V1, ENDPOINT_COMMAND_V1
from xpotato_sim.runtime.composition.robot_profile import robot_profile_runtime_metadata
from xpotato_sim.runtime.control.input_source_state import reconcile_runtime_input_source_state, annotate_raw_input_frame
from xpotato_sim.runtime.experiment.composition import resolve_command_execution
from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity, TaskTerminalClassification
from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.execution.command_routes import RouteMotionGeneratorFactory
from xpotato_sim.runtime.contact.manifest import decode_contact_manifest
from xpotato_sim.runtime.contact.scene import ContactSceneBuildRequest
from xpotato_sim.runtime.contact.robot_view import ContactRobotView, add_signal_tool_proxy, PROXY_NAME, PROXY_RADIUS_M
from xpotato_sim.runtime.contact.task_contract import ContactTaskContext, ContactTaskObservation, ContactTrialIdentity, ContactOperatorStatus
from xpotato_sim.runtime.contact.log import ContactTaskLogHeader, ContactTaskLogRecorder, ContactTaskLogTaskState, ContactTaskLogSourceKind
from xpotato_sim.runtime.contact.virtual_reaction_force import VirtualReactionForceConfig, VirtualReactionForceManifest, VirtualReactionForceProcessor, VirtualReactionForceFrame
from xpotato_sim.runtime.contact.presentation import contact_task_payload_metadata_v1, contact_scene_robot_qpos_payload_metadata_v1
from xpotato_sim.runtime.output.fast_arm_emulation import FastArmOutputMapping, FastArmSignalSession, emulate_fast_arm_peer
from xpotato_sim.runtime.output.fast_arm_observation import BoundedFastArmObservationDriver
from xpotato_sim.runtime.output.permission import evaluate_physical_output_permission
from xpotato_sim.runtime.output.safety_gate import evaluate_and_bind_physical_output_safety, physical_output_candidate_id
from xpotato_sim.runtime.safety.physical_safety_core import SafetyInput
from xpotato_sim.mujoco_backend.snapshot import snapshot_mujoco_state
from xpotato_sim.schemas import PhysicalOutputRequest, PhysicalOutputPermission, MotionCommand, JointCommand
from xpotato_sim.transport.payload import mujoco_state_to_payload

SCENARIO_SCHEMA = "prehardware-signal-scenario/v1"
TRACE_SCHEMA = "prehardware-signal-trace/v1"
MAX_STEPS = 512


def json_value(value):
    """証拠の値を変えずJSON型へ投影する。不明型を文字列で隠さない。"""
    if isinstance(value, Enum):
        return value.value
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("non-finite evidence")
        return value
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TypeError("evidence keys must be strings")
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def canonical(value) -> bytes:
    return json.dumps(json_value(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def strict_json(data: bytes | str):
    """duplicate/nonfinite/BOMを拒否するlocal fixture/artifact reader。"""
    if type(data) is bytes:
        data = data.decode("utf-8", errors="strict")
    if type(data) is not str or data.startswith("\ufeff"):
        raise ValueError("expected UTF-8 JSON without BOM")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite JSON value")
    result = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    canonical(result)
    return result


def exact(value, keys, name):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"{name} fields are missing or unknown")
    return value


def validate_scenario(document: bytes | str | dict) -> dict:
    """全入力を有限化し、deviceやnetwork設定を受理しない。"""
    value = strict_json(canonical(document)) if isinstance(document, dict) else strict_json(document)
    exact(value, ("schema_version", "scenario_id", "source", "payloads", "host_times_s", "mapping", "mapping_parameters",
                  "route", "contact_manifest", "task", "wire_mapping", "ack_timeout_s", "response_mode"), "scenario")
    if value["schema_version"] != SCENARIO_SCHEMA or value["source"] not in ("selfrionette", "gamepad"):
        raise ValueError("unsupported scenario or source")
    if type(value["scenario_id"]) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value["scenario_id"]):
        raise ValueError("scenario_id must be a stable identifier")
    times, payloads = value["host_times_s"], value["payloads"]
    if type(times) is not list or not 1 <= len(times) <= MAX_STEPS:
        raise ValueError("scenario step count is invalid")
    if any(type(t) not in (int, float) or not isfinite(t) or t < 0 for t in times) or any(b <= a for a,b in zip(times,times[1:])):
        raise ValueError("host times must be finite and strictly increasing")
    if type(payloads) is not list or len(payloads) > MAX_STEPS * 64:
        raise ValueError("payload count is invalid")
    if any(p is not None and (type(p) is not str or len(p.encode('utf-8')) > 65536) for p in payloads):
        raise ValueError("payload must be bounded wire text or null")
    if value["source"] == "gamepad" and len(payloads) != len(times):
        raise ValueError("gamepad needs one event or null per tick")
    if value["source"] == "selfrionette" and any(p is None for p in payloads):
        raise ValueError("Selfrionette fixture must contain wire lines")
    exact(value["mapping"], ("plugin_id", "contract_version"), "mapping selection")
    exact(value["route"], ("name", "version"), "route")
    mapping = resolve_control_mapping_plugin(PluginSelection(**value["mapping"]))
    mapping.normalize_runtime_parameters(value["mapping_parameters"])
    mapping.resolve_command_semantics_route(VersionedIdentity(**value["route"]))
    manifest = decode_contact_manifest(value["contact_manifest"])
    # このsingle-metric runnerは未対応の評価器を黙って切り捨てない。
    if manifest.evaluators != (PluginSelection("contact_outcome", 1),):
        raise ValueError("signal contact evaluator must be exactly contact_outcome/v1")
    resolve_evaluation_plugin(manifest.evaluators[0])
    if manifest.robot_bundle != PluginSelection("fast_arm", 1) or not manifest.scene.enabled or not manifest.object.enabled:
        raise ValueError("signal contact runner requires enabled fast_arm contact scene")
    if manifest.reset.simulation_time_s != 0.0:
        raise ValueError("signal contact trial must start at simulation time zero")
    exact(value["task"], ("dwell_interval_s", "timeout_s", "target_normal_force_band_n"), "task conditions")
    ContactTaskContext(manifest=manifest, **value["task"])
    FastArmOutputMapping.from_mapping(value["wire_mapping"])
    timeout = value["ack_timeout_s"]
    if type(timeout) not in (int,float) or not isfinite(timeout) or timeout <= 0:
        raise ValueError("ack timeout must be positive and finite")
    if any(not isfinite(float(t)+float(timeout)) or float(t)+float(timeout)<=float(t) for t in times):
        raise ValueError("ack deadline must be representable and later than its start")
    if value["response_mode"] not in ("immediate", "missing", "malformed", "disconnect"):
        raise ValueError("unsupported synthetic response mode")
    return value


class _Clock:
    def __init__(self, value): self.value = value
    def __call__(self): return self.value


class _NullPublisher:
    async def publish(self, state): pass


def _model_digest(xml, assets):
    value = {"xml_sha256": sha256(xml).hexdigest(), "assets": {p:sha256(b).hexdigest() for p,b in sorted(assets.items())}}
    return sha256(canonical(value)).hexdigest()


def missing_physical_safety_evidence(request: PhysicalOutputRequest, *, now_s: float) -> dict:
    """未取得の実機根拠をNoneとしてP5へ渡し、non-allowの実判定を記録する。"""
    safety_input=SafetyInput(candidate_id=physical_output_candidate_id(request),limit_resolution=None,
        collision=None,dynamic=None,provenance=(f"software_revision:{request.software_revision}",))
    result=evaluate_and_bind_physical_output_safety(request,safety_input,checked_at_s=now_s)
    return {"status":result.status,"reason":result.reason,"decision":json_value(result.decision),
            "request_sha256":result.request_sha256,"binding_sha256":result.binding_sha256,
            "checked_at_s":result.checked_at_s,"inputs":"physical_evidence_unavailable"}


def capture_signal_contact(document: bytes | str | dict, *, software_revision: str) -> bytes:
    """有限fixtureを実行し、成功も失敗も同じstrict trace envelopeへ保存する。"""
    scenario = validate_scenario(document)
    if type(software_revision) is not str or not (re.fullmatch(r"[0-9a-f]{40}", software_revision) or software_revision.startswith("test-only-")):
        raise ValueError("explicit source SHA or test-only revision is required")
    manifest = decode_contact_manifest(scenario["contact_manifest"])
    bundle = resolve_robot_bundle(manifest.robot_bundle.plugin_id)
    profile = bundle.profile
    base = bundle.runtime_plugin.build_simulator(model_path=None, initial_keyframe_name=profile.initial_keyframe_name)
    bundle.runtime_plugin.validate_model(base.model)
    guard = bundle.runtime_plugin.build_qpos_feasibility_guard(model=base.model, config_path=None)
    pose = bundle.provider(ENDPOINT_POSE_V1)
    motion_provider = bundle.provider(ENDPOINT_COMMAND_V1)
    mapping = resolve_control_mapping_plugin(PluginSelection(**scenario["mapping"]))
    parameters = mapping.normalize_runtime_parameters(scenario["mapping_parameters"])
    resolved = resolve_command_execution(mapping, bundle, VersionedIdentity(**scenario["route"]))
    if not isinstance(resolved.binding, RouteMotionGeneratorFactory):
        raise ValueError("signal contact requires an explicit local motion route")
    motion = resolved.binding.build_motion_generator(motion_provider)
    registration = INPUT_SOURCE_CATALOG.resolve("selfrionette" if scenario["source"] == "selfrionette" else "viewer")
    if registration.plugin.effective_mapping_input_sample_schema not in mapping.accepted_input_sample_schemas:
        raise ValueError("source and mapping schemas differ")
    wire_mapping = FastArmOutputMapping.from_mapping(scenario["wire_mapping"])
    signal_session = FastArmSignalSession(profile=profile, mapping=wire_mapping, acknowledgement_timeout_s=scenario["ack_timeout_s"])
    request = add_signal_tool_proxy(ContactSceneBuildRequest.from_robot_bundle(manifest, bundle), profile)
    environment = resolve_environment_plugin(manifest.environment)
    instance = environment.scene_provider.compose_scene({"request": request})
    view = ContactRobotView(instance, profile)
    reset_state=view.snapshot()
    reset_check=guard.evaluate(MotionCommand(timestamp_s=reset_state.time_s,joint=JointCommand(joint_angles_rad=reset_state.qpos)),current_qpos_rad=reset_state.qpos)
    if not reset_check.accepted:
        raise ValueError("contact reset violates declared Robot joint constraints")
    dt = manifest.scene.mujoco.timestep_s
    clock = _Clock(float(scenario["host_times_s"][0]))
    if scenario["source"] == "selfrionette":
        source_parameters = {"lines": tuple(scenario["payloads"])}
    else:
        source_parameters = {"metadata": {}, "initial_endpoint_m": pose.observe_endpoint_pose(view.snapshot()).position_m}
    reader = registration.plugin.create_runtime_reader(source_parameters, runtime_dependencies=InputSourceRuntimeDependencies(clock=clock))
    pipeline = ControlMappedRuntimePipeline(
        config=RuntimeConfig(robot_profile_id=profile.profile_id), input_source=reader,
        control_mapping=mapping, control_mapping_parameters=parameters,
        mapping_input_adapter=registration.plugin.mapping_input_adapter, motion_generator=motion,
        simulator=view, publisher=_NullPublisher(), command_semantics_route=resolved.route,
        command_execution=resolved.binding, qpos_feasibility_guard=guard, endpoint_pose_provider=pose,
    )
    context = ContactTaskContext(manifest=manifest, trial=ContactTrialIdentity(scenario["scenario_id"]),
                                 require_pose_measurement=True, **scenario["task"])
    task = resolve_task_plugin(manifest.task_plugin)
    binding = task.lifecycle.bind_context(context, {})
    task_state = binding.initial_state()
    force_manifest = VirtualReactionForceManifest(manifest, VirtualReactionForceConfig(
        output_frame=VirtualReactionForceFrame.MUJOCO_WORLD, deadband_n=0.0,
        low_pass_time_constant_s=0.0, smoothing_window_samples=1, rate_limit_n_per_s=None,
        magnitude_clamp_n=2.0, max_inter_sample_gap_s=dt*2.0,
    ))
    processor = VirtualReactionForceProcessor(force_manifest)
    recorder = ContactTaskLogRecorder(ContactTaskLogHeader(context,force_manifest,source_kind=ContactTaskLogSourceKind.RUNTIME_CAPTURE))
    observations, records = [], []
    import mujoco
    object_id = mujoco.mj_name2id(instance.model,mujoco.mjtObj.mjOBJ_BODY,manifest.object.body_name)
    if object_id < 0: raise ValueError("contact object missing")
    def observe(operator=ContactOperatorStatus.NOMINAL, reason=None):
        nonlocal task_state
        snapshot = instance.simulator.snapshot()
        raw = instance.measure_contact_evidence(robot_geom_names=(PROXY_NAME,), sample_time_s=snapshot.time_s, frame_index=snapshot.frame_index)
        observation = ContactTaskObservation(
            elapsed_time_s=snapshot.time_s, contact_evidence=raw,
            tip_position_world_m=pose.observe_endpoint_pose(snapshot).position_m,
            object_position_world_m=tuple(float(x) for x in instance.data.xpos[object_id]),
            object_orientation_wxyz=tuple(float(x) for x in instance.data.xquat[object_id]),
            contact_location_world_m=raw.target_contacts[0].point_world_m if raw.target_contacts else None,
            operator_status=operator,reason=reason,
        )
        transition = binding.advance(task_state,observation)
        task_state = transition.state
        observations.append(observation)
        recorder.append(observation, processor.process(raw,trial=context.trial), ContactTaskLogTaskState(
            task_state.phase,task_state.classification,task_state.terminal_reason))
        return transition
    initial = json_value(instance.simulator.snapshot())
    transition = observe()
    terminal = {"kind":"budget_exhausted", "reason":"finite fixture ended", "input_index":None, "exception_type":None}
    queue = []
    def receive_nowait():
        if scenario["response_mode"] == "disconnect": raise OSError("synthetic_peer_disconnected")
        return queue.pop(0) if queue else None
    driver = BoundedFastArmObservationDriver(signal_session, receive_nowait=receive_nowait,clock=clock,max_datagrams=4)
    primary = None
    started = False
    try:
        started=True; reader.start()
        for index, host_time in enumerate(scenario["host_times_s"]):
            clock.value=float(host_time)
            if scenario["source"] == "gamepad" and scenario["payloads"][index] is not None:
                reader.viewer_bridge_capability.ingest_control_message_json(scenario["payloads"][index])
            raw_frame = reader.read_frame()
            health = reader.current_health()
            state = reconcile_runtime_input_source_state(raw_frame,health,source_kind=registration.plugin.identity.name)
            frame = annotate_raw_input_frame(raw_frame,state)
            before = view.snapshot()
            intent = pipeline.map_input(frame,pre_step_state=before)
            safety = pipeline.execute_intent(intent,dt_s=dt,pre_step_state=before,source_state=state)
            actual_request = view.last_joint_position_command
            if actual_request is None: raise ValueError("backend request is missing")
            view.step(dt)
            after = instance.simulator.snapshot()
            output_request = PhysicalOutputRequest(
                target_robot_id="signal-arm",endpoint_id="signal-joints",command_semantics="joint_position_command/v1",
                command=actual_request,session_id=scenario["scenario_id"],sequence=index,
                timestamp_s=actual_request.timestamp_s,cadence_s=dt,software_revision=software_revision,
            )
            preview=signal_session.submit(output_request,now_s=clock.value)
            receipt=emulate_fast_arm_peer(preview.datagram,target_robot_id=output_request.target_robot_id,wire_joint_order=wire_mapping.wire_joint_order)
            response=receipt.response_datagram if scenario["response_mode"]=="immediate" else (b"invalid" if scenario["response_mode"]=="malformed" else None)
            if response is not None: queue.append(response)
            tick=driver.tick()
            operator,reason=ContactOperatorStatus.NOMINAL,None
            if safety.is_stale: operator,reason=ContactOperatorStatus.STALE,safety.stale_reason
            elif safety.qpos_feasibility_rejected: operator,reason=ContactOperatorStatus.REJECTED,"qpos feasibility rejected"
            elif safety.motion_command.metadata.get("motion_status") == "held": operator,reason=ContactOperatorStatus.HELD,"motion held"
            transition=observe(operator,reason)
            decision=evaluate_physical_output_permission(output_request,PhysicalOutputPermission())
            records.append({
                "index":index,"host_time_s":clock.value,"raw_frame":json_value(frame),"health":json_value(health),
                "intent":json_value(intent),"motion":json_value(safety.motion_command),
                "runtime_safety":{"is_stale":safety.is_stale,"qpos_rejected":safety.qpos_feasibility_rejected,"reason":safety.stale_reason},
                "request":strict_json(output_request.to_json_bytes()),"before_robot_qpos":list(before.qpos),
                "after_scene":json_value(after),"after_robot_qpos":list(view.snapshot().qpos),
                "wire_hex":preview.datagram.hex(),"wire_sha256":preview.datagram_sha256,
                "response_hex":None if response is None else response.hex(),"acknowledgement":json_value(tick.acknowledgement),
                "observation_events":json_value(tick.observations),"response_checked_at_s":clock.value,
                "physical_permission":{"status":decision.status,"reason":decision.reason},
                "physical_safety":missing_physical_safety_evidence(output_request,now_s=float(host_time)),
                "contact_sample_index":len(observations)-1,
            })
            if signal_session.pending_acknowledgement.status == "pending":
                clock.value = float(host_time) + scenario["ack_timeout_s"]
                timeout_tick=driver.tick()
                records[-1]["acknowledgement"]=json_value(timeout_tick.acknowledgement)
                records[-1]["observation_events"]+=json_value(timeout_tick.observations)
                records[-1]["response_checked_at_s"]=clock.value
            if signal_session.state != "active":
                terminal={"kind":"response_failure","reason":signal_session.pending_acknowledgement.reason,"input_index":index,"exception_type":None};break
            if transition.classification is not TaskTerminalClassification.RUNNING:
                terminal={"kind":"task_terminal","reason":task_state.terminal_reason,"input_index":index,"exception_type":None};break
    except Exception as failure:
        primary=failure
        terminal={"kind":"execution_failure","reason":str(failure) or type(failure).__name__,"input_index":len(records),"exception_type":type(failure).__name__}
    finally:
        if started:
            try: reader.close()
            except Exception as cleanup:
                if primary is not None: primary.add_note(f"cleanup failed: {cleanup!r}")
                else: terminal={"kind":"cleanup_failure","reason":str(cleanup) or type(cleanup).__name__,"input_index":len(records),"exception_type":type(cleanup).__name__}
        signal_session.stop(now_s=clock.value)
    if instance.simulator.snapshot().frame_index != len(records):
        # step後の内部失敗を、古い観測と新しいsnapshotの混在artifactへ変換しない。
        if primary is not None:
            raise primary
        raise RuntimeError("incomplete simulation record")
    transition=binding.finalize(task_state)
    outcome=derive_contact_outcome(context,observations)
    log=recorder.finalize(outcome)
    metric=resolve_evaluation_plugin(manifest.evaluators[0]).derive_metric(transition.evidence,{})
    final_state=instance.simulator.snapshot()
    metadata={**robot_profile_runtime_metadata(profile),
        **contact_task_payload_metadata_v1(log,payload_time_s=final_state.time_s,payload_frame_index=final_state.frame_index),
        **contact_scene_robot_qpos_payload_metadata_v1(log,instance=instance,robot_profile=profile,payload_time_s=final_state.time_s,payload_frame_index=final_state.frame_index)}
    final_payload=mujoco_state_to_payload(snapshot_mujoco_state(instance.model,instance.data,frame_index=final_state.frame_index,metadata=metadata))
    payload={"software_revision":software_revision,"scenario":scenario,"scenario_sha256":sha256(canonical(scenario)).hexdigest(),
        "model_sha256":_model_digest(instance.definition.model_xml,request.assets),"mujoco_version":mujoco.__version__,
        "proxy":{"name":PROXY_NAME,"radius_m":PROXY_RADIUS_M,"observation_class":"synthetic"},
        "source_identity":registration.plugin.identity.canonical_id,"mapping_identity":mapping.identity.canonical_id,
        "route_identity":resolved.route.identity.canonical_id,"frozen_mapping_parameters":json_value(parameters),
        "projection":{"joint_names":list(profile.canonical_joint_names),"qpos_addresses":list(view.qpos_addresses),"dof_addresses":list(view.dof_addresses)},
        "initial_scene":initial,"records":records,"contact_log":log.to_jsonl().decode('utf-8'),"final_payload":final_payload,
        "metric":json_value(metric),"termination":terminal,
        "coverage":{"input_to_contact":bool(records),"wire_previews":len(records),"physical_gate":"disabled",
            "not_run":["physical_measurement","actual_serial","actual_network","physical_trajectory_safety","participant_trial"]}}
    return canonical({"schema_version":TRACE_SCHEMA,"payload":payload,"payload_sha256":sha256(canonical(payload)).hexdigest()})+b"\n"
