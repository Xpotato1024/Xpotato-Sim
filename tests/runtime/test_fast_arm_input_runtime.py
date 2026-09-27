"""physical sessionへの結線を、既存の合成acceptanceとin-memory senderだけで検証する。"""
from __future__ import annotations

from dataclasses import replace
import json
import socket
import sys
from types import ModuleType

import pytest

from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.execution.input_step_loop import build_runtime_input_source_step_loop_plan
from xpotato_sim.runtime.experiment.input_source import InputSourceRuntimeDependencies
from xpotato_sim.runtime.output.fast_arm_emulation import emulate_fast_arm_peer
from xpotato_sim.runtime.output.safety_gate import physical_output_candidate_id
from xpotato_sim.runtime.runners.fast_arm_input_runtime import FastArmInputRuntime
from xpotato_sim.runtime.safety.physical_safety_core import SafetyInput
from xpotato_sim.schemas import JointPositionCommand, PhysicalOutputPermission
from tests.runtime.test_fast_arm_physical_output import _new_session, _evaluation


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    """localhostを含め、実socket/DNS/serialをテスト経路で使わせない。"""
    def forbidden(*args, **kwargs):
        raise AssertionError("real I/O is forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    serial = ModuleType("serial")
    serial.Serial = forbidden
    monkeypatch.setitem(sys.modules, "serial", serial)


class Harness:
    """本物のSource/Mapping/routeとsessionをfake I/Oだけで構成するtest owner。"""

    def __init__(self, source="selfrionette", *, max_ticks=12, mode="immediate", lines=None):
        (self.session, self.mapping, self.evidence, self.config, self.sender, self.clock,
         self.physical_permission, self.transmission_permission) = _new_session()
        self.mode, self.received, self.reads, self.starts, self.closes = mode, 0, 0, 0, 0
        self.source = source
        self.evaluations = []
        if source == "selfrionette":
            values = lines or tuple(f"vector,{900000+i*100},0.1,0,0,0,0,0,0" for i in range(8))
            weights = tuple((0.0, float(i == 0), 0.0) for i in range(7))
            selection = select_runtime_input_source(source, steps=8, line_source=values,
                control_mapping_parameters={"mapping_config": {"channel_axis_weights": weights, "gain_m":0.002, "max_delta_m":0.002}})
            reader = selection.resolved_plugin.create_runtime_reader(selection.validated_parameters,
                runtime_dependencies=InputSourceRuntimeDependencies(clock=self.clock))
            selection = replace(selection, runtime_reader=reader)
        else:
            selection = select_runtime_input_source("viewer", steps=8)
        self.plan = build_runtime_input_source_step_loop_plan(selection, viewer_clock=self.clock if source == "gamepad" else None)
        self.reader = self.plan.pipeline.input_source
        self.plan.pipeline.simulator.apply_joint_position_command(JointPositionCommand(0.0, (0.0, -0.5, 0.0, -0.5)))
        # cached snapshotはstep後に更新される。試験用resetを先に確定する。
        self.plan.pipeline.simulator.step(.1)
        self.runtime = FastArmInputRuntime(self.plan, self.session,
            software_revision=self.config.software_revision, safety_input=self.safety_input,
            receive_nowait=self.receive, clock=self.clock, max_ticks=max_ticks, max_datagrams_per_tick=4)

    def safety_input(self, request, before):
        """元のtest-only evidence producerを使い、allowを返すだけのmockを作らない。"""
        self.evaluations.append((request, before))
        return _evaluation(request, self.evidence).safety_input

    def receive(self):
        """送ったbyte列だけから疑似応答を生成する。"""
        if self.mode == "disconnect":
            raise OSError("fake receiver disconnected")
        if self.mode == "missing" or self.received == len(self.sender.send_calls):
            return None
        data = self.sender.send_calls[self.received]
        self.received += 1
        if self.mode == "malformed":
            return b"invalid"
        return emulate_fast_arm_peer(data, target_robot_id=self.session.target_robot_id,
            wire_joint_order=self.mapping.wire_joint_order).response_datagram

    def event(self):
        """Gamepadの実schemaへ合成eventを渡す。"""
        if self.source == "gamepad":
            message = {"type":"viewer_control_message", "timestamp_s":self.clock.value, "source_kind":"gamepad",
                "metadata":{"control_frame":"world"}, "gamepad":{
                "connected":True, "axes":[0.0,0.1,0.0], "buttons":[]}}
            self.plan.viewer_bridge_capability.ingest_control_message_json(json.dumps(message))

    def start(self):
        self.runtime.start(self.physical_permission, self.transmission_permission)
        self.event()


@pytest.mark.parametrize("source", ("selfrionette", "gamepad"))
def test_production_input_reaches_physical_session_with_distinct_clocks(source, monkeypatch):
    """元信号・host指令・simulation・疑似受信の境界を追跡する。"""
    h = Harness(source)
    closes = []
    original_close = h.reader.close
    def close():
        closes.append(True)
        original_close()
    monkeypatch.setattr(h.reader, "close", close)
    assert not h.sender.send_calls and h.session.state == "disarmed"
    h.start()
    first = h.runtime.tick()
    assert first.phase == "dispatched", first
    assert first.output.transport_result.local_send_result.evidence_kind == "simulated"
    assert first.request.command.joint_angles_rad == first.source_command.joint_angles_rad
    assert first.request.timestamp_s == first.request.command.timestamp_s == h.clock.value
    if source == "selfrionette":
        assert first.source_command.timestamp_s == first.frame.timestamp_s == 900.0
        assert first.request.timestamp_s == 1.0
    assert first.simulation_before.frame_index == 1 and first.simulation_after.frame_index == 2
    h.clock.value += .125
    h.event()
    second = h.runtime.tick()
    assert second.phase == "dispatched", second
    assert second.observation.acknowledgement.reason == "simulated_router_observation_correlated"
    assert second.observation.acknowledgement.status == "unavailable"
    assert second.request.sequence == 1 and len(h.sender.send_calls) == 2
    h.runtime.stop()
    assert h.runtime.closed and h.session.state == "stopped"
    # viewer closeは既存contract上no-op。呼出し1回と出力ownerのterminal性を確認する。
    assert closes == [True]
    with pytest.raises(RuntimeError): h.runtime.tick()
    with pytest.raises(RuntimeError): h.start()


@pytest.mark.parametrize("source", ("selfrionette", "gamepad"))
def test_pending_does_not_consume_input_or_step(source, monkeypatch):
    """応答待ちの間は新しい入力を読まず、期限切れで閉じる。"""
    h = Harness(source, mode="missing")
    h.start()
    assert h.runtime.tick().phase == "dispatched"
    def forbidden(): raise AssertionError("read while pending")
    monkeypatch.setattr(h.reader, "read_frame", forbidden)
    before = h.plan.pipeline.simulator.snapshot()
    h.clock.value += .125
    assert h.runtime.tick().phase == "waiting_ack"
    assert h.plan.pipeline.simulator.snapshot() == before
    assert len(h.sender.send_calls) == 1
    h.clock.value += 1.0
    ended = h.runtime.tick()
    assert ended.phase == "terminal" and h.session.state == "failed"
    assert "timeout" in ended.reason
    assert len(h.sender.send_calls) == 1


def test_cadence_does_not_consume_or_duplicate_source(monkeypatch):
    """次のcadenceまで入力を消費しない。"""
    h = Harness(); h.start(); h.runtime.tick()
    monkeypatch.setattr(h.reader, "read_frame", lambda: pytest.fail("early read"))
    assert h.runtime.tick().phase == "waiting_cadence"
    assert len(h.sender.send_calls) == 1
    h.runtime.stop()


@pytest.mark.parametrize("mode", ("malformed", "disconnect"))
def test_bad_receiver_ends_without_a_new_dispatch(mode):
    """異常応答を許可として扱わず追加送信を止める。"""
    h = Harness(mode="immediate"); h.start(); h.runtime.tick(); h.mode = mode
    h.clock.value += .125
    result = h.runtime.tick()
    if mode == "malformed":
        assert result.phase == "waiting_ack"
        h.clock.value += 1.0
        result = h.runtime.tick()
    assert result.phase == "terminal" and len(h.sender.send_calls) == 1


@pytest.mark.parametrize("missing", (False, True))
def test_nonallow_and_wrong_candidate_cannot_reach_transport(missing):
    """欠落根拠や別candidateではprepare/sendに到達しない。"""
    h = Harness()
    if missing:
        def producer(request, before):
            return SafetyInput(physical_output_candidate_id(request), None, None, None,
                (f"software_revision:{request.software_revision}",))
    else:
        def producer(request, before):
            return _evaluation(replace(request, sequence=99), h.evidence).safety_input
    h.runtime._safety_input = producer
    h.start()
    r = h.runtime.tick()
    assert r.phase == "terminal" and r.evaluation.status != "allowed"
    assert not h.sender.prepare_calls and not h.sender.send_calls


@pytest.mark.parametrize("line", ("vector,not,a,frame",))
def test_malformed_input_aborts_and_does_not_send(line):
    """不正入力ではlocal許可を撤回する。"""
    h = Harness(lines=(line,)); h.start()
    with pytest.raises(ValueError): h.runtime.tick()
    assert h.runtime.closed and h.session.state == "aborted"
    assert not h.sender.send_calls


def test_eof_after_one_command_preserves_no_retry():
    """EOF時に最後の入力を再送しない。"""
    h = Harness(lines=("vector,900000,0.1,0,0,0,0,0,0",)); h.start(); h.runtime.tick()
    h.clock.value += .125
    with pytest.raises((StopIteration, RuntimeError)): h.runtime.tick()
    assert h.runtime.closed and len(h.sender.send_calls) == 1


def test_source_stale_while_pending_stops_locally():
    """応答待ち中の入力staleも無視しない。"""
    h = Harness(mode="missing"); h.start(); h.runtime.tick(); h.clock.value += .5
    r = h.runtime.tick()
    assert r.phase == "terminal" and r.reason == "source_not_fresh_while_pending"
    assert h.session.state == "stopped" and len(h.sender.send_calls) == 1


def test_slow_evaluation_does_not_relabel_old_input_as_fresh():
    """計算の遅れで古くなった入力を新しい時刻で隠さない。"""
    h = Harness()
    def slow(request, before):
        result = h.safety_input(request, before)
        h.clock.value += .5
        return result
    h.runtime._safety_input = slow
    h.start(); r = h.runtime.tick()
    assert r.reason == "source_stale_during_evaluation" and not h.sender.send_calls


@pytest.mark.parametrize("field", ("input_source", "motion_generator", "command_execution"))
def test_hot_swap_is_rejected_before_read_or_dispatch(field):
    """構成差替え時は元のreaderを後始末する。"""
    h = Harness(); h.start()
    setattr(h.plan.pipeline, field, object())
    with pytest.raises(ValueError, match="components"): h.runtime.tick()
    assert not h.sender.send_calls and h.runtime.closed
    assert h.reader.current_health().status.value == "disconnected"


@pytest.mark.parametrize("bad", (float("nan"), float("inf"), -.1, True, .5, 1e308))
def test_bad_clock_ends_without_dispatch(bad):
    """不正clockで明示終了し送信しない。"""
    h = Harness(); h.start(); h.clock.value = bad
    with pytest.raises((ValueError, TypeError)): h.runtime.tick()
    assert h.runtime.closed and not h.sender.send_calls


@pytest.mark.parametrize("method", ("stop", "abort"))
def test_operator_end_from_callback_prevents_dispatch(method):
    """callback内のstop/abort後はdispatchしない。"""
    h = Harness()
    def end(request, before):
        result = h.safety_input(request, before)
        getattr(h.runtime, method)()
        return result
    h.runtime._safety_input = end
    h.start(); assert h.runtime.tick().phase == "terminal"
    assert not h.sender.send_calls


def test_tick_budget_is_finite_even_with_no_response():
    """無受信でも有限budgetで出力ownerを閉じる。"""
    h = Harness(max_ticks=3, mode="missing"); h.start()
    assert h.runtime.tick().phase == "dispatched"
    assert h.runtime.tick().phase == "waiting_ack"
    assert h.runtime.tick().reason == "tick_budget_exhausted"
    assert h.runtime.closed and len(h.sender.send_calls) == 1


def test_permission_denial_never_starts_reader(monkeypatch):
    """permission不足で取得を開始しない。"""
    h = Harness()
    monkeypatch.setattr(h.reader, "start", lambda: pytest.fail("unauthorized start"))
    with pytest.raises(ValueError): h.runtime.start(PhysicalOutputPermission(), h.transmission_permission)
    assert not h.sender.send_calls and h.runtime.closed


def test_primary_exception_survives_cleanup_failure(monkeypatch):
    """二重例外で元の原因を失わない。"""
    h = Harness(); h.start()
    def read(): raise ValueError("original read failure")
    def close(): raise OSError("cleanup failure")
    monkeypatch.setattr(h.reader, "read_frame", read)
    monkeypatch.setattr(h.reader, "close", close)
    with pytest.raises(ValueError, match="original read failure") as caught: h.runtime.tick()
    assert any("cleanup failure" in note for note in caught.value.__notes__)
    assert h.runtime.closed and h.session.state == "aborted" and not h.sender.send_calls


@pytest.mark.parametrize("field", ("mapping", "session_id", "target_robot_id", "transport_config"))
def test_session_identity_change_is_rejected_before_dispatch(field):
    """同じownerで出力先やwire mappingだけを差し替えられない。"""
    h = Harness(); h.start()
    if field == "mapping":
        h.session.mapping = replace(h.mapping, source_id="different-source")
    elif field == "transport_config":
        h.session.transport_config = replace(h.config, software_revision="different-revision")
    else:
        setattr(h.session, field, "different-identity")
    with pytest.raises(ValueError, match="configuration"): h.runtime.tick()
    assert not h.sender.prepare_calls and h.runtime.closed


def test_missing_evidence_cannot_construct_a_session():
    """新ownerも既存accepted-evidence要件を迂回するfactoryを持たない。"""
    with pytest.raises(TypeError): _new_session(evidence=object())


def test_unknown_runtime_revision_is_rejected_without_arm():
    """requestのrevisionをsessionと独立に作り替えない。"""
    h = Harness()
    with pytest.raises(ValueError, match="revision"):
        FastArmInputRuntime(h.plan, h.session, software_revision="different", safety_input=h.safety_input,
            receive_nowait=h.receive, clock=h.clock, max_ticks=10, max_datagrams_per_tick=1)
    assert h.session.state == "disarmed" and not h.sender.send_calls


def test_inactive_viewer_never_dispatches():
    """未受信をゼロの観測や有効commandへ変換しない。"""
    h = Harness("gamepad")
    h.runtime.start(h.physical_permission, h.transmission_permission)
    assert h.runtime.tick().reason == "source_not_fresh"
    assert not h.sender.send_calls


def test_guard_rejection_is_not_dispatched(monkeypatch):
    """test-only solver故障を本物のjoint-limit guardが拒否する。"""
    from xpotato_sim.schemas import MotionCommand, JointCommand
    h = Harness()
    def invalid_candidate(intent, dt_s):
        return MotionCommand(intent.timestamp_s, joint=JointCommand((100.0, 0.0, 0.0, 0.0)))
    monkeypatch.setattr(h.plan.pipeline.motion_generator, "update_delta", invalid_candidate)
    h.start(); r = h.runtime.tick()
    assert r.reason == "local_motion_rejected_or_held"
    assert not h.evaluations and not h.sender.prepare_calls


def test_slow_read_is_not_given_a_fresh_host_timestamp(monkeypatch):
    """read復帰直後に入力の受信ageを再確認する。"""
    h = Harness(); original = h.reader.read_frame
    def slow():
        frame = original(); h.clock.value += .5; return frame
    monkeypatch.setattr(h.reader, "read_frame", slow)
    h.start(); r = h.runtime.tick()
    assert r.reason == "source_not_fresh" and r.request is None
    assert not h.sender.prepare_calls


def test_cadence_is_based_on_actual_dispatch_start():
    """P5計算で時間が進んでも、古いrequest作成時刻を送信間隔の基点にしない。"""
    h = Harness()
    def delayed(request, before):
        result = h.safety_input(request, before); h.clock.value += .125; return result
    h.runtime._safety_input = delayed
    h.start(); assert h.runtime.tick().phase == "dispatched"
    h.clock.value = 1.2
    r = h.runtime.tick()
    assert r.phase == "waiting_cadence" and h.runtime.last_tick == r
    assert len(h.sender.send_calls) == 1
    h.runtime.stop()


@pytest.mark.parametrize("change", ("token", "target"))
def test_wellformed_wrong_response_keeps_pending(change):
    """構文的には正常な別token/targetをACKにしない。"""
    from xpotato_sim.transport.osc import OscMessage, decode_osc_message, encode_osc_message
    h = Harness(); original = h.receive
    def wrong():
        data = original()
        if data is None: return None
        msg = decode_osc_message(data)
        return encode_osc_message(OscMessage("/router/other/command" if change == "target" else msg.address,
            ("wrong-token", *msg.arguments[1:]) if change == "token" else msg.arguments))
    h.runtime._driver._receive = wrong
    h.start(); h.runtime.tick(); h.clock.value += .125
    assert h.runtime.tick().phase == "waiting_ack"
    assert len(h.sender.send_calls) == 1
    h.runtime.stop()


def test_start_failure_closes_partial_reader_once(monkeypatch):
    """startが途中で失敗してもarmを撤回し、closeを試みる。"""
    h = Harness(); closed = []
    def start(): raise OSError("partial start")
    def close(): closed.append(True)
    monkeypatch.setattr(h.reader, "start", start); monkeypatch.setattr(h.reader, "close", close)
    with pytest.raises(OSError, match="partial start"): h.start()
    h.runtime.stop()
    assert closed == [True] and h.session.state == "aborted"


def test_reentrant_tick_is_rejected_and_owner_closes():
    """callbackが同じownerを再入実行しない。"""
    h = Harness()
    def recursive(request, before): return h.runtime.tick()
    h.runtime._safety_input = recursive
    h.start()
    with pytest.raises(RuntimeError, match="reentrant"): h.runtime.tick()
    assert h.runtime.closed and not h.sender.send_calls


def test_closed_owner_from_receive_callback_does_not_read_source(monkeypatch):
    """応答callbackでstopされたら同tickで入力生成を再開しない。"""
    h = Harness(); h.start(); h.runtime.tick()
    def stop(): h.runtime.stop(); return None
    h.runtime._driver._receive = stop
    monkeypatch.setattr(h.reader, "read_frame", lambda: pytest.fail("read after stop"))
    assert h.runtime.tick().reason == "operator_stop"
    assert len(h.sender.send_calls) == 1


@pytest.mark.parametrize("delay", (.375, .5))
def test_stale_input_during_transport_preparation_is_not_sent(monkeypatch, delay):
    """prepare中にstaleになった入力はgrantが有効でも送信しない。"""
    h = Harness()
    original = h.sender.prepare
    def delayed(endpoint):
        result = original(endpoint)
        h.clock.value += delay
        return result
    monkeypatch.setattr(h.sender, "prepare", delayed)
    h.start()
    result = h.runtime.tick()
    assert not h.sender.send_calls, (result.phase, result.request.timestamp_s, h.clock.value)
    assert h.runtime.closed


@pytest.mark.parametrize("veto", (False, None, 1, "true"))
def test_pre_dispatch_veto_requires_exact_true(veto):
    """追加callbackは許可を増やさず、真値相当の別型でも送信しない。"""
    from tests.runtime.test_fast_arm_physical_output import _request
    h = Harness()
    assert h.session.arm(h.physical_permission, h.transmission_permission).accepted
    evaluation = _evaluation(_request(), h.evidence)
    result = h.session.submit(evaluation, pre_dispatch_check=lambda: veto)
    assert result.reason == "fast_arm_pre_dispatch_check_rejected"
    assert h.session.state == "failed" and h.session.latest_sendable_request is None
    assert len(h.sender.prepare_calls) == 1 and not h.sender.send_calls


@pytest.mark.parametrize("end", ("stop", "abort"))
def test_stop_from_pre_dispatch_check_wins_even_if_it_returns_true(end):
    """callback後にも既存generation検査が働くことを確認する。"""
    from tests.runtime.test_fast_arm_physical_output import _request
    h = Harness()
    assert h.session.arm(h.physical_permission, h.transmission_permission).accepted
    def check():
        getattr(h.session, end)()
        return True
    result = h.session.submit(_evaluation(_request(), h.evidence), pre_dispatch_check=check)
    assert result.status == "rejected" and not h.sender.send_calls
    assert h.session.state in {"stopped", "aborted"}


def test_pre_dispatch_check_exception_retains_cause_and_revokes():
    """送信直前の再検査が壊れた場合も許可を撤回し、元例外を伝播する。"""
    from tests.runtime.test_fast_arm_physical_output import _request
    h = Harness()
    assert h.session.arm(h.physical_permission, h.transmission_permission).accepted
    def check():
        raise ValueError("source freshness unavailable")
    with pytest.raises(ValueError, match="source freshness unavailable"):
        h.session.submit(_evaluation(_request(), h.evidence), pre_dispatch_check=check)
    assert h.session.state == "failed" and not h.sender.send_calls
    assert h.session.latest_sendable_request is None


def test_wrong_raw_source_is_rejected_before_mapping(monkeypatch):
    """同じschema形状でも取得元identityを別sourceへ付け替えられない。"""
    h = Harness(); h.start()
    read = h.reader.read_frame
    monkeypatch.setattr(h.reader, "read_frame", lambda: replace(read(), source="other-source"))
    with pytest.raises(ValueError, match="source identity"): h.runtime.tick()
    assert not h.evaluations and not h.sender.prepare_calls and h.runtime.closed


def test_frame_and_health_source_subtype_mismatch_is_rejected(monkeypatch):
    """同一readのframe/healthでsource subtypeが食い違えばMapping前に拒否する。"""
    h = Harness("gamepad"); h.start()
    current_health = h.reader.current_health

    def changed_health():
        health = current_health()
        metadata = dict(health.metadata)
        metadata["source_kind"] = "keyboard"
        return replace(health, metadata=metadata)

    monkeypatch.setattr(h.reader, "current_health", changed_health)
    with pytest.raises(ValueError, match="frame and health identities"):
        h.runtime.tick()
    assert not h.evaluations and not h.sender.prepare_calls and not h.sender.send_calls
    assert h.runtime.closed


def test_source_subtype_change_during_transport_prepare_is_not_sent(monkeypatch):
    """read済みcommandのsource subtypeがprepare中に変われば送信直前に拒否する。"""
    h = Harness("gamepad")
    current_health = h.reader.current_health
    prepared = False

    def changing_health():
        health = current_health()
        if not prepared:
            return health
        metadata = dict(health.metadata)
        metadata["source_kind"] = "keyboard"
        return replace(health, metadata=metadata)

    original_prepare = h.sender.prepare

    def prepare(endpoint):
        nonlocal prepared
        result = original_prepare(endpoint)
        prepared = True
        return result

    monkeypatch.setattr(h.reader, "current_health", changing_health)
    monkeypatch.setattr(h.sender, "prepare", prepare)
    h.start()
    with pytest.raises(ValueError, match="health identity changed"):
        h.runtime.tick()
    assert h.sender.prepare_calls and not h.sender.send_calls
    assert h.runtime.closed and h.session.state == "failed"


def test_recorded_route_command_must_equal_backend_command(monkeypatch):
    """backendが別の指令を保持した場合に、その値を黙って出力しない。"""
    h = Harness()
    sim = h.plan.pipeline.simulator
    apply = sim.apply_joint_position_command
    def changed(backend, command):
        assert backend is sim
        apply(replace(command, joint_angles_rad=(.1, -.5, 0., -.5)))
    monkeypatch.setattr(type(sim), "apply_joint_position_command", changed)
    h.start()
    with pytest.raises(ValueError, match="backend command differs"): h.runtime.tick()
    assert not h.evaluations and not h.sender.prepare_calls and h.runtime.closed


@pytest.mark.parametrize("source", ("selfrionette", "gamepad"))
def test_fresh_zero_is_a_valid_hold_command_not_missing_input(source):
    """新しいゼロsampleは未取得と区別し、同じ姿勢の明示指令として記録する。"""
    h = Harness(source, lines=("vector,900000,0,0,0,0,0,0,0",))
    h.start()
    if source == "gamepad":
        h.plan.viewer_bridge_capability.ingest_control_message_json(json.dumps({
            "type":"viewer_control_message", "timestamp_s":h.clock.value,
            "source_kind":"gamepad", "metadata":{"control_frame":"world"},
            "gamepad":{"connected":True,"axes":[0.,0.,0.],"buttons":[]}}))
    result = h.runtime.tick()
    assert result.phase == "dispatched" and result.health.status.value == "active"
    assert result.request.command.joint_angles_rad == result.simulation_before.qpos
    assert len(h.sender.send_calls) == 1
    h.runtime.stop()
