"""信号・指令・scene・contact logのlocal artifactを厳密に照合する。"""
from __future__ import annotations

from hashlib import sha256
from dataclasses import replace
from math import isclose, isfinite
import re
from xpotato_sim.runtime.control.input_source_state import build_runtime_input_source_state_from_metadata
from xpotato_sim.runtime.safety.input_safety import build_runtime_input_safety_result
from xpotato_sim.runtime.runners.signal_contact import canonical, strict_json, exact, validate_scenario, TRACE_SCHEMA, json_value
from xpotato_sim.runtime.contact.log import decode_contact_task_log
from xpotato_sim.runtime.contact.task_contract import ContactOperatorStatus
from xpotato_sim.runtime.composition.robot_profile import robot_profile_runtime_metadata
from xpotato_sim.runtime.contact.presentation import contact_task_payload_metadata_v1, CONTACT_SCENE_ROBOT_QPOS_SCHEMA_VERSION
from xpotato_sim.runtime.contact.manifest import decode_contact_manifest
from xpotato_sim.runtime.output.fast_arm_emulation import FastArmOutputMapping, build_fast_arm_signal_preview, emulate_fast_arm_peer
from xpotato_sim.runtime.output.fast_arm_observation import FastArmPendingObservation, FastArmAcknowledgementEvidence, resolve_fast_arm_router_datagram, expired_fast_arm_acknowledgement
from xpotato_sim.plugins.tasks.contact_press_hold_task import derive_contact_outcome
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity
from xpotato_sim.plugins.tasks.catalog import resolve_task_plugin
from xpotato_sim.plugins.evaluations.catalog import resolve_evaluation_plugin
from xpotato_sim.runtime.contact.scene import ContactSceneBuildRequest, ContactSceneComposer
from xpotato_sim.runtime.contact.robot_view import add_signal_tool_proxy, PROXY_NAME, PROXY_RADIUS_M
from xpotato_sim.runtime.runners.signal_contact import _model_digest, missing_physical_safety_evidence
from xpotato_sim.schemas import PhysicalOutputRequest, RawInputFrame, MotionCommand, JointCommand, MuJoCoState

PAYLOAD_KEYS = ("software_revision", "scenario", "scenario_sha256", "model_sha256", "mujoco_version", "proxy",
    "source_identity", "mapping_identity", "route_identity", "frozen_mapping_parameters", "projection", "initial_scene",
    "records", "contact_log", "final_payload", "metric", "termination", "coverage")
RECORD_KEYS = ("index", "host_time_s", "raw_frame", "health", "intent", "motion", "runtime_safety", "request",
    "before_robot_qpos", "after_scene", "after_robot_qpos", "wire_hex", "wire_sha256", "response_hex",
    "acknowledgement", "observation_events", "response_checked_at_s", "physical_permission", "physical_safety", "contact_sample_index")
SCENE_KEYS = ("bodies", "sites", "frame_index", "metadata", "qpos", "qvel", "target_position_m", "time_s")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _vector(value, length):
    _require(type(value) is list and len(value) == length, "trace vector dimension mismatch")
    _require(all(type(x) in (int,float) and isfinite(x) for x in value), "invalid trace vector")


def _validate_source_records(scenario, registration, initial_tip, records):
    """原fixtureからproduction Sourceを再生し、raw frameとhealthの出所を照合する。"""
    from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies
    from xpotato_sim.runtime.control.input_source_state import reconcile_runtime_input_source_state, annotate_raw_input_frame
    from xpotato_sim.runtime.runners.signal_contact import _Clock
    clock=_Clock(float(scenario["host_times_s"][0]))
    parameters=({"lines":tuple(scenario["payloads"])} if scenario["source"]=="selfrionette"
                else {"metadata":{},"initial_endpoint_m":initial_tip})
    reader=registration.plugin.create_runtime_reader(parameters,runtime_dependencies=InputSourceRuntimeDependencies(clock=clock))
    try:
        reader.start()
        for index,record in enumerate(records):
            clock.value=float(scenario["host_times_s"][index])
            if scenario["source"]=="gamepad" and scenario["payloads"][index] is not None:
                reader.viewer_bridge_capability.ingest_control_message_json(scenario["payloads"][index])
            frame=reader.read_frame()
            health=reader.current_health()
            state=reconcile_runtime_input_source_state(frame,health,source_kind=registration.plugin.identity.name)
            expected=annotate_raw_input_frame(frame,state)
            _require(record["raw_frame"]==json_value(expected),"raw input differs from original scenario")
            _require(record["health"]==json_value(health),"source health differs from original scenario")
    except Exception as exc:
        raise ValueError("source provenance replay failed") from exc
    finally:
        reader.close()


def _validate_runtime_diagnostics(record, frame, sample, guard, before_scene):
    """保存candidateに既存guardを再適用する。solver結果や実機安全性の真正性は主張しない。"""
    diagnostic = exact(record["runtime_safety"], ("is_stale", "qpos_rejected", "reason"), "runtime safety")
    _require(type(diagnostic["is_stale"]) is bool and type(diagnostic["qpos_rejected"]) is bool,
             "runtime safety flags must be booleans")
    motion = exact(record["motion"], ("timestamp_s", "target", "joint", "metadata"), "motion")
    _require(type(motion["timestamp_s"]) in (int, float) and motion["timestamp_s"] == frame.timestamp_s,
             "motion timestamp mismatch")
    joint = exact(motion["joint"], ("joint_angles_rad", "joint_velocities_rad_s"), "motion joint")
    _require(joint["joint_velocities_rad_s"] == [] and motion["target"] is None, "unexpected motion command kind")
    metadata = motion["metadata"]
    _require(type(metadata) is dict, "motion metadata must be an object")
    before = record["before_robot_qpos"]
    candidate = metadata.get("candidate_qpos_rad")
    _vector(candidate, len(before))
    _vector(joint["joint_angles_rad"], len(before))
    _require(metadata.get("qpos_before_rad") == before, "motion pre-state mismatch")
    state = build_runtime_input_source_state_from_metadata(frame.metadata, default_source_kind=frame.source)
    result = build_runtime_input_safety_result(
        MotionCommand(timestamp_s=frame.timestamp_s, joint=JointCommand(joint_angles_rad=tuple(candidate))),
        source_state=state,
        current_state=MuJoCoState(frame_index=before_scene["frame_index"], time_s=before_scene["time_s"], qpos=tuple(before)),
        qpos_feasibility_guard=guard,
    )
    _require(diagnostic == {"is_stale": result.is_stale, "qpos_rejected": result.qpos_feasibility_rejected,
                            "reason": result.stale_reason}, "runtime safety differs from source/candidate")
    _require(joint["joint_angles_rad"] == list(result.motion_command.joint.joint_angles_rad),
             "safety result differs from backend command")
    for key, value in result.motion_command.metadata.items():
        _require(metadata.get(key) == json_value(value), "motion safety metadata mismatch")
    status = metadata.get("motion_status")
    _require(status in ("accepted", "scaled", "held"), "invalid local motion status")
    if status == "held":
        _require(candidate == before and type(metadata.get("motion_rejection_reason")) is str
                 and bool(metadata["motion_rejection_reason"]), "held motion lacks hold evidence")
    operator, reason = "nominal", None
    if result.is_stale:
        operator, reason = "stale", result.stale_reason
    elif result.qpos_feasibility_rejected:
        operator, reason = "rejected", "qpos feasibility rejected"
    elif status == "held":
        operator, reason = "held", "motion held"
    expected = replace(sample, operator_status=ContactOperatorStatus(operator),
                       motion_status=ContactOperatorStatus(operator), reason=reason)
    _require(sample.operator_status is expected.operator_status and sample.reason == expected.reason,
             "Task operator status differs from runtime safety")


def _validate_termination(terminal, records, scenario, task_state):
    """最終advanceの状態から終了経路を照合し、finalizeで作るfailureとは区別する。"""
    exact(terminal, ("kind", "reason", "input_index", "exception_type"), "termination")
    kind, reason, index, exception = (terminal[key] for key in ("kind", "reason", "input_index", "exception_type"))
    count, budget = len(records), len(scenario["host_times_s"])
    _require(index is None or type(index) is int, "termination index must be an integer or null")
    _require(reason is None or type(reason) is str, "termination reason must be text or null")
    running = task_state.classification.value == "running"
    response_ok = not records or records[-1]["acknowledgement"]["reason"] == "simulated_router_observation_correlated"
    if kind in ("execution_failure", "cleanup_failure"):
        _require(type(exception) is str and exception.isidentifier() and bool(reason), "failure needs exception and reason")
        _require(type(index) is int and index == count, "failure index differs from completed records")
        if kind == "execution_failure":
            _require(count < budget and running and response_ok, "execution failure follows an already ended run")
        else:
            _require(count == budget or not running or not response_ok, "cleanup failure has no preceding end condition")
    else:
        _require(exception is None, "normal or response termination cannot carry an exception")
        if kind == "task_terminal":
            _require(count > 0 and index == count - 1 and not running and response_ok
                     and reason == task_state.terminal_reason, "Task termination mismatch")
        elif kind == "response_failure":
            _require(count > 0 and index == count - 1 and not response_ok
                     and reason == records[-1]["acknowledgement"]["reason"], "response termination mismatch")
        elif kind == "budget_exhausted":
            _require(count == budget and index is None and running and response_ok
                     and reason == "finite fixture ended", "budget termination mismatch")
        else:
            raise ValueError("unknown termination")


def decode_signal_trace(document: bytes, *, expected_revision: str, expected_scenario_sha256: str | None = None) -> dict:
    """digestだけでなく、既存型・再変換・frame/time・Taskの再導出を検証する。

    hashは破損検出であり署名ではない。捏造されたphysicsの真正性は保証せず、別実行とのbyte比較で確認する。
    """
    _require(type(document) is bytes and 0 < len(document) <= 64*1024*1024, "trace size is invalid")
    envelope = exact(strict_json(document), ("schema_version", "payload", "payload_sha256"), "trace")
    _require(document == canonical(envelope)+b"\n", "trace must use canonical JSON bytes")
    _require(envelope["schema_version"] == TRACE_SCHEMA, "unknown trace schema")
    p = exact(envelope["payload"], PAYLOAD_KEYS, "trace payload")
    _require(envelope["payload_sha256"] == sha256(canonical(p)).hexdigest(), "trace digest mismatch")
    _require(p["software_revision"] == expected_revision, "trace revision mismatch")
    version = p["mujoco_version"]
    _require(type(version) is str and len(version) <= 128
             and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*", version) is not None,
             "invalid MuJoCo version string")
    scenario = validate_scenario(p["scenario"])
    digest = sha256(canonical(scenario)).hexdigest()
    _require(p["scenario_sha256"] == digest, "scenario digest mismatch")
    if expected_scenario_sha256 is not None:
        _require(digest == expected_scenario_sha256, "unexpected scenario")
    manifest = decode_contact_manifest(scenario["contact_manifest"])
    bundle = resolve_robot_bundle(manifest.robot_bundle.plugin_id)
    profile = bundle.profile
    mapping = resolve_control_mapping_plugin(PluginSelection(**scenario["mapping"]))
    params = mapping.normalize_runtime_parameters(scenario["mapping_parameters"])
    _require(p["frozen_mapping_parameters"] == json_value(params), "frozen Mapping differs")
    registration = INPUT_SOURCE_CATALOG.resolve("selfrionette" if scenario["source"] == "selfrionette" else "viewer")
    _require(p["source_identity"] == registration.plugin.identity.canonical_id and p["mapping_identity"] == mapping.identity.canonical_id,
             "source or Mapping identity mismatch")
    route = mapping.resolve_command_semantics_route(VersionedIdentity(**scenario["route"]))
    _require(p["route_identity"] == route.identity.canonical_id, "route identity mismatch")
    projection=exact(p["projection"], ("joint_names", "qpos_addresses", "dof_addresses"), "projection")
    _require(projection["joint_names"] == list(profile.canonical_joint_names), "Robot joint order mismatch")
    initial=exact(p["initial_scene"],SCENE_KEYS,"initial scene")
    nq,nv=len(initial["qpos"]),len(initial["qvel"])
    for field,length,limit in (("qpos_addresses",profile.qpos_dimension,nq),("dof_addresses",profile.qvel_dimension,nv)):
        addresses=projection[field]
        _require(type(addresses) is list and len(addresses)==length and len(set(addresses))==length and
                 all(type(a) is int and 0<=a<limit for a in addresses), "invalid joint address projection")
    qadr=projection["qpos_addresses"]
    _require(initial["frame_index"]==0 and initial["time_s"]==0.0, "trial did not start from reset")
    _vector(initial["qpos"],nq); _vector(initial["qvel"],nv)
    _require([initial["qpos"][a] for a in qadr]==list(manifest.reset.qpos_rad), "initial Robot/reset mismatch")
    log=decode_contact_task_log(p["contact_log"].encode('utf-8'))
    _require(log.header.context.manifest==manifest and log.header.context.trial.trial_id==scenario["scenario_id"], "contact log identity mismatch")
    for key,value in scenario["task"].items():
        _require(json_value(getattr(log.header.context,key))==value, "contact task condition mismatch")
    outcome=derive_contact_outcome(log.header.context,[s.observation for s in log.samples])
    _require(outcome==log.summary.outcome,"contact outcome is not derivable from observations")
    binding=resolve_task_plugin(manifest.task_plugin).lifecycle.bind_context(log.header.context,{})
    state=binding.initial_state()
    for sample in log.samples:
        _require(state.classification.value == "running", "contact sample follows Task termination")
        state=binding.advance(state,sample.observation).state
        _require(sample.task_state.phase is state.phase and sample.task_state.classification is state.classification
                 and sample.task_state.reason == state.terminal_reason, "intermediate Task state mismatch")
    transition=binding.finalize(state)
    metric=resolve_evaluation_plugin(manifest.evaluators[0]).derive_metric(transition.evidence,{})
    _require(p["metric"]==json_value(metric),"metric differs from canonical Task evidence")
    _require(p["proxy"]=={"name":PROXY_NAME,"radius_m":PROXY_RADIUS_M,"observation_class":"synthetic"}, "proxy identity mismatch")
    request=add_signal_tool_proxy(ContactSceneBuildRequest.from_robot_bundle(manifest,resolve_robot_bundle(manifest.robot_bundle.plugin_id)),profile)
    scene=ContactSceneComposer(request).compose()
    _require(p["model_sha256"]==_model_digest(scene.model_xml,request.assets),"model resource digest mismatch")

    records=p["records"]
    _require(type(records) is list and len(records)<=len(scenario["host_times_s"]) and len(log.samples)==len(records)+1,
             "missing or extra trace records")
    _validate_source_records(scenario,registration,log.samples[0].observation.tip_position_world_m,records)
    # Robotの既存preflight APIを使い、具象config解析やguard実装を複製しない。
    validation_model = bundle.runtime_plugin.build_simulator(model_path=None, initial_keyframe_name=profile.initial_keyframe_name)
    bundle.runtime_plugin.validate_model(validation_model.model)
    guard = bundle.runtime_plugin.build_qpos_feasibility_guard(model=validation_model.model, config_path=None)
    wire_mapping=FastArmOutputMapping.from_mapping(scenario["wire_mapping"])
    before=initial
    for i,record in enumerate(records):
        r=exact(record,RECORD_KEYS,"trace record")
        _require(type(r["index"]) is int and r["index"]==i and r["contact_sample_index"]==i+1,"trace sequence mismatch")
        _require(r["host_time_s"]==scenario["host_times_s"][i],"host time mismatch")
        after=exact(r["after_scene"],SCENE_KEYS,"scene observation")
        _vector(after["qpos"],nq);_vector(after["qvel"],nv)
        _require(type(after["frame_index"]) is int and after["frame_index"]==i+1,"scene frame mismatch")
        _require(isclose(after["time_s"],before["time_s"]+manifest.scene.mujoco.timestep_s,rel_tol=0.,abs_tol=1e-12),"scene cadence mismatch")
        _require(r["before_robot_qpos"]==[before["qpos"][a] for a in qadr],"pre-state projection mismatch")
        _require(r["after_robot_qpos"]==[after["qpos"][a] for a in qadr],"post-state projection mismatch")
        sample=log.samples[i+1].observation
        _require(sample.contact_evidence.frame_index==after["frame_index"] and sample.contact_evidence.simulation_time_s==after["time_s"],"contact/scene time mismatch")
        raw=exact(r["raw_frame"],("source","timestamp_s","values","buttons","metadata"),"raw input")
        frame=RawInputFrame(source=raw["source"],timestamp_s=raw["timestamp_s"],values=tuple(raw["values"]),buttons=tuple(raw["buttons"]),metadata=raw["metadata"])
        _validate_runtime_diagnostics(r, frame, sample, guard, before)
        current_params=dict(params)
        if mapping.runtime_context_parameters:
            current_params["current_tip_position_m"]=log.samples[i].observation.tip_position_world_m
        adapted=registration.plugin.mapping_input_adapter(frame) if registration.plugin.mapping_input_adapter else frame
        expected_intent=mapping.strategy.map_input(adapted,current_params)
        _require(r["intent"]==json_value(expected_intent),"mapped intent is not derived from recorded input")
        request=PhysicalOutputRequest.from_json(canonical(r["request"]))
        _require(request.software_revision==expected_revision and request.session_id==scenario["scenario_id"] and
                 request.sequence==i and request.timestamp_s==raw["timestamp_s"] and request.cadence_s==manifest.scene.mujoco.timestep_s,
                 "request identity/time mismatch")
        _require(list(request.command.joint_angles_rad)==r["after_robot_qpos"],"backend request/post-qpos mismatch")
        _require(r["motion"]["joint"]["joint_angles_rad"]==list(request.command.joint_angles_rad),"motion/backend request mismatch")
        preview=build_fast_arm_signal_preview(request,wire_mapping,attempt_id=f"signal-preview-{i}")
        _require(r["wire_hex"]==preview.datagram.hex() and r["wire_sha256"]==preview.datagram_sha256,"wire byte/request mismatch")
        _require(r["physical_permission"]=={"status":"rejected","reason":"physical_output_disabled"},"physical output must remain disabled")
        _require(r["physical_safety"]==missing_physical_safety_evidence(request,now_s=r["host_time_s"]),"physical safety evidence mismatch")
        if scenario["response_mode"]=="immediate":
            receipt=emulate_fast_arm_peer(preview.datagram,target_robot_id=request.target_robot_id,wire_joint_order=wire_mapping.wire_joint_order)
            _require(r["response_hex"]==receipt.response_datagram.hex(),"peer response differs from wire")
            pending=FastArmPendingObservation(preview.attempt_id,preview.wire_command,r["host_time_s"]+scenario["ack_timeout_s"],"simulated",started_at_s=r["host_time_s"])
            result=resolve_fast_arm_router_datagram(pending,receipt.response_datagram,now_s=r["host_time_s"])
            _require(r["acknowledgement"]==json_value(result.evidence) and r["observation_events"]==[json_value(result.evidence)] and
                     r["response_checked_at_s"]==r["host_time_s"],"synthetic acknowledgement mismatch")
        elif scenario["response_mode"] in ("missing", "malformed"):
            pending=FastArmPendingObservation(preview.attempt_id,preview.wire_command,r["host_time_s"]+scenario["ack_timeout_s"],"simulated",started_at_s=r["host_time_s"])
            events=[] if scenario["response_mode"]=="missing" else [json_value(resolve_fast_arm_router_datagram(pending,b"invalid",now_s=r["host_time_s"]).evidence)]
            _require(r["response_hex"]==(None if scenario["response_mode"]=="missing" else b"invalid".hex()), "unexpected response bytes")
            _require(r["response_checked_at_s"]==pending.deadline_s and r["observation_events"]==events and
                     r["acknowledgement"]==json_value(expired_fast_arm_acknowledgement(pending,now_s=pending.deadline_s)), "response timeout evidence mismatch")
        else:
            _require(r["response_hex"] is None and r["observation_events"]==[] and r["response_checked_at_s"]==r["host_time_s"] and
                     r["acknowledgement"]==json_value(FastArmAcknowledgementEvidence("unavailable","transport_disconnected")), "disconnect evidence mismatch")
        _require(r["acknowledgement"]["status"]!="router_command_observed","synthetic result cannot be physical ACK")
        if i < len(records) - 1:
            _require(r["acknowledgement"]["reason"] == "simulated_router_observation_correlated",
                     "command recorded after terminal response")
        before=after
    final=p["final_payload"]
    _require(final["qpos"]==before["qpos"] and final["qvel"]==before["qvel"] and final["frame_index"]==before["frame_index"] and final["time_s"]==before["time_s"],"final payload/state mismatch")
    for key,value in json_value(robot_profile_runtime_metadata(profile)).items():
        _require(final["metadata"].get(key)==value,"final payload Robot profile mismatch")
    for key,value in contact_task_payload_metadata_v1(log,payload_time_s=before["time_s"],payload_frame_index=before["frame_index"]).items():
        _require(final["metadata"].get(key)==json_value(value),"final payload contact projection mismatch")
    expected_projection={"schema_version":CONTACT_SCENE_ROBOT_QPOS_SCHEMA_VERSION,
        "scene_identity":{"name":manifest.scene.identity.name,"version":manifest.scene.identity.version},
        "manifest_digest":log.header.context.manifest_digest,"frame_index":before["frame_index"],"time_s":before["time_s"],
        "source_qpos_dimension":nq,"robot_profile_id":profile.profile_id,"model_contract_version":profile.model_contract_version,
        "robot_qpos_dimension":profile.qpos_dimension,"robot_joint_names":list(profile.canonical_joint_names),"qpos_addresses":qadr}
    _require(final["metadata"].get("contact_scene_robot_qpos_v1")==expected_projection,"final payload joint projection mismatch")
    _validate_termination(p["termination"], records, scenario, state)
    _require(p["coverage"]=={"input_to_contact":bool(records),"wire_previews":len(records),"physical_gate":"disabled",
        "not_run":["physical_measurement","actual_serial","actual_network","physical_trajectory_safety","participant_trial"]},"coverage claims mismatch")
    return p
