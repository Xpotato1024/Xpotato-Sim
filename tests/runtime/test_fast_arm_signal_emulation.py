"""#542のwire境界と有限受信。fixtureは実機calibrationを意味しない。"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from math import pi
import socket

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import FastArmOutputMapping
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.runtime.output.fast_arm_emulation import (
    FastArmSignalSession, build_fast_arm_signal_preview, emulate_fast_arm_peer,
)
from xpotato_sim.runtime.output.fast_arm_observation import BoundedFastArmObservationDriver
from xpotato_sim.schemas.command import JointPositionCommand
from xpotato_sim.transport.osc import OscMessage, decode_osc_message, encode_osc_message
from tests.runtime.test_fast_arm_physical_output import (
    _mapping, _request, _new_session, _arm_session, _evaluation,
)


class Clock:
    """明示的にだけ進む、実時間と独立したsoftware clock。"""
    def __init__(self, now=10.0):
        self.now = now
    def __call__(self):
        return self.now


def mapping(unit="degree"):
    """順序2,0,3,1と符号+,-,-,+を明示した合成mapping。"""
    base = _mapping()
    offsets = (10.0, 20.0, 30.0, 40.0) if unit == "degree" else tuple(x * pi / 180 for x in (10,20,30,40))
    return replace(base, source_id="sil", angle_offset_unit=unit,
                   joint_coordinate_signs=tuple(zip(base.profile_joint_order, (1,-1,-1,1), strict=True)),
                   joint_angle_offsets=tuple(zip(base.profile_joint_order, offsets, strict=True)))


def request(sequence=1, *, timestamp=1.0):
    return _request(sequence=sequence, timestamp_s=timestamp, joint_angles_rad=(pi/2, pi/4, -pi/2, 0.0))


def session(unit="degree"):
    return FastArmSignalSession(profile=FAST_ARM_ROBOT_PROFILE, mapping=mapping(unit), acknowledgement_timeout_s=1.0)


def response(preview):
    """受信側へbyte列と明示配線条件だけを渡す。expected値を使わない。"""
    return emulate_fast_arm_peer(preview.datagram, target_robot_id="arm_a",
                                 wire_joint_order=mapping().wire_joint_order).response_datagram


def queue_source(items):
    queue = deque(items)
    return lambda: queue.popleft() if queue else None


@pytest.mark.parametrize("unit", ("rad", "degree"))
def test_mapping_wire_uses_independent_float32_golden_oracle(unit):
    preview = build_fast_arm_signal_preview(request(), mapping(unit), attempt_id="oracle")
    # 手計算: reorder(q2,q0,q3,q1) -> (120,100,40,-25) degrees。
    # ,ffff + NUL padding + IEEE754 big-endian float32。production encoderで期待値を作らない。
    golden_tail = bytes.fromhex("2c66666666000000 42f00000 42c80000 42200000 c1c80000")
    assert preview.datagram.endswith(golden_tail)
    decoded = decode_osc_message(preview.datagram)
    assert decoded.arguments == (120.0, 100.0, 40.0, -25.0)
    assert preview.observation_class == "synthetic"
    assert len(preview.datagram_sha256) == 64
    assert preview == build_fast_arm_signal_preview(request(), mapping(unit), attempt_id="oracle")


def test_peer_reads_literal_osc_bytes_not_expected_command():
    # address本体20 bytes + NUL/padding 4 bytes。独立した完全datagram oracle。
    golden = b"/fixture/arm_a/joint\0\0\0\0" + bytes.fromhex("2c66666666000000 42f00000 42c80000 42200000 c1c80000")
    receipt = emulate_fast_arm_peer(golden, target_robot_id="arm_a", wire_joint_order=mapping().wire_joint_order)
    assert receipt.decoded_command.position_degrees == (120.0,100.0,40.0,-25.0)
    assert receipt.observation_class == "synthetic"
    msg = decode_osc_message(receipt.response_datagram)
    assert msg.address == "/router/arm_a/command"
    assert msg.arguments == ("fixture", "joint", "[120.0, 100.0, 40.0, -25.0]")
    changed = golden[:-4] + bytes.fromhex("00000000")
    altered = emulate_fast_arm_peer(changed, target_robot_id="arm_a", wire_joint_order=mapping().wire_joint_order)
    assert altered.decoded_command.position_degrees[-1] == 0.0
    assert altered.response_datagram != receipt.response_datagram


@pytest.mark.parametrize("message", (
    OscMessage("/fixture/other/joint", (1.0,2.0,3.0,4.0)),
    OscMessage("/fixture/arm_a/stop", (1.0,2.0,3.0,4.0)),
    OscMessage("/fixture/arm_a/joint", (1,2,3,4)),
    OscMessage("/fixture/arm_a/joint", (1.0,2.0,3.0)),
))
def test_peer_rejects_wrong_target_command_shape_and_type_tags(message):
    with pytest.raises(ValueError):
        emulate_fast_arm_peer(encode_osc_message(message), target_robot_id="arm_a", wire_joint_order=mapping().wire_joint_order)


@pytest.mark.parametrize("mutate", (lambda b:b[:-1], lambda b:b+b"\0", lambda b:b"", lambda b:b"x"*65508))
def test_peer_rejects_malformed_datagrams(mutate):
    p = build_fast_arm_signal_preview(request(),mapping(),attempt_id="test")
    with pytest.raises(ValueError):
        emulate_fast_arm_peer(mutate(p.datagram),target_robot_id="arm_a",wire_joint_order=mapping().wire_joint_order)


def test_preview_waits_for_real_byte_correlation_and_does_not_claim_physical_ack():
    target = session()
    preview = target.submit(request(),now_s=10.0)
    with pytest.raises(RuntimeError,match="pending"):
        target.submit(request(2),now_s=10.1)
    ack = target.observe_router_datagram(response(preview),now_s=10.2)
    assert ack.status == "unavailable" and ack.reason == "simulated_router_observation_correlated"
    assert target.pending_acknowledgement.status != "pending"
    with pytest.raises(ValueError,match="sequence"):
        target.submit(request(),now_s=10.3)
    next_preview = target.submit(request(2),now_s=10.3)
    old = target.observe_router_datagram(response(preview),now_s=10.4)
    assert old.reason == "router_observation_correlation_mismatch"
    assert target.pending_acknowledgement.status == "pending"
    assert target.observe_router_datagram(response(next_preview),now_s=10.4).reason == "simulated_router_observation_correlated"
    assert target.observe_router_datagram(response(next_preview),now_s=10.5).reason == "router_observation_without_pending_attempt"


@pytest.mark.parametrize("kind", ("target","token","values","malformed","type"))
def test_wrong_observation_never_releases_pending(kind):
    target = session(); p = target.submit(request(),now_s=10.0)
    msg = decode_osc_message(response(p))
    if kind == "target": bad = encode_osc_message(replace(msg,address="/router/other/command"))
    elif kind == "token": bad = encode_osc_message(replace(msg,arguments=("wrong",*msg.arguments[1:])))
    elif kind == "values": bad = encode_osc_message(replace(msg,arguments=(*msg.arguments[:2],"[0.0, 0.0, 0.0, 0.0]")))
    elif kind == "type": bad = 123
    else: bad = b"malformed"
    assert target.observe_router_datagram(bad,now_s=10.1).status == "unavailable"
    assert target.pending_acknowledgement.status == "pending"
    assert target.expire_acknowledgement(now_s=11.0).reason == "simulated_router_observation_timeout"
    assert target.state == "failed"


@pytest.mark.parametrize("time,expected", ((10.999,"active"),(11.0,"failed"),(12.0,"failed")))
def test_deadline_boundary_cannot_accept_late_response(time, expected):
    target=session(); p=target.submit(request(),now_s=10.0)
    target.observe_router_datagram(response(p),now_s=time)
    assert target.state == expected
    if expected == "failed":
        with pytest.raises(RuntimeError): target.submit(request(2),now_s=time)


@pytest.mark.parametrize("field,value", (("target_robot_id","other"),("endpoint_id","other"),
    ("session_id","other"),("software_revision","other"),("cadence_s",0.2)))
def test_session_identity_cannot_change_after_first_request(field,value):
    target=session(); p=target.submit(request(),now_s=10.0)
    target.observe_router_datagram(response(p),now_s=10.1)
    with pytest.raises(ValueError,match="identity"):
        target.submit(replace(request(2),**{field:value}),now_s=10.2)


@pytest.mark.parametrize("action",("stop","disconnect"))
def test_stopped_session_does_not_reopen_from_late_observation(action):
    target=session(); p=target.submit(request(),now_s=10.0)
    getattr(target,action)(now_s=10.1)
    before=target.state
    assert target.observe_router_datagram(response(p),now_s=10.2).status == "unavailable"
    assert target.state==before
    with pytest.raises(RuntimeError): target.submit(request(2),now_s=10.3)


def test_driver_no_packet_still_expires_and_never_retries():
    target=session(); target.submit(request(),now_s=10.0); clock=Clock()
    calls=[]
    def receive(): calls.append(True); return None
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,clock=clock,max_datagrams=2)
    assert driver.tick().acknowledgement.status == "pending"
    clock.now=11.0
    assert driver.tick().acknowledgement.reason == "simulated_router_observation_timeout"
    assert len(calls)==1
    driver.tick()
    assert len(calls)==1


def test_driver_budget_prevents_packet_storm_from_hiding_timeout():
    target=session(); target.submit(request(),now_s=10.0); clock=Clock(); calls=[]
    def receive(): calls.append(True); return b"bad"
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,clock=clock,max_datagrams=3)
    tick=driver.tick()
    assert tick.received_count==3 and tick.budget_exhausted and len(calls)==3
    clock.now=11.0
    assert driver.tick().acknowledgement.reason == "simulated_router_observation_timeout"
    assert len(calls)==3


def test_driver_rechecks_time_after_receive_callback():
    target=session(); p=target.submit(request(),now_s=10.0); clock=Clock()
    def receive(): clock.now=11.1; return response(p)
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,clock=clock,max_datagrams=3)
    tick=driver.tick()
    assert tick.observations==() and target.state=="failed"
    assert tick.acknowledgement.reason=="simulated_router_observation_timeout"


def test_driver_disconnect_is_terminal():
    target=session(); target.submit(request(),now_s=10.0)
    def receive(): raise OSError("synthetic disconnect")
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,clock=Clock(),max_datagrams=3)
    assert driver.tick().acknowledgement.reason=="transport_disconnected"
    assert target.state=="failed"


@pytest.mark.parametrize("bad", (float("nan"),float("inf"),9.0,True))
def test_driver_invalid_clock_does_not_keep_pending(bad):
    target=session(); target.submit(request(),now_s=10.0); clock=Clock()
    driver=BoundedFastArmObservationDriver(target,receive_nowait=lambda:None,clock=clock,max_datagrams=2)
    driver.tick(); clock.now=bad
    with pytest.raises((ValueError,TypeError)):
        driver.tick()
    assert target.state=="failed" and target.pending_acknowledgement.status!="pending"


def test_same_driver_and_byte_peer_work_with_existing_physical_session_fixture():
    # 既存physicalのtest-only handoffを回帰用に使用。preview側はこれを生成/利用しない。
    target, mp, evidence, config, sender, clock, physical, transmission = _new_session()
    _arm_session(target,(physical,transmission))
    result=target.submit(_evaluation(_request(),evidence),now_s=1.0)
    assert result.status=="transmission_attempted" and len(sender.send_calls)==1
    peer=emulate_fast_arm_peer(sender.send_calls[0],target_robot_id="arm_a",wire_joint_order=mp.wire_joint_order)
    clock.value=1.1
    driver=BoundedFastArmObservationDriver(target,receive_nowait=queue_source([peer.response_datagram]),clock=clock,max_datagrams=2)
    tick=driver.tick()
    assert tick.acknowledgement.status=="unavailable"
    assert tick.acknowledgement.reason=="simulated_router_observation_correlated"
    assert len(sender.send_calls)==1


def test_no_io_session_does_not_construct_physical_session_or_socket(monkeypatch):
    import xpotato_sim.runtime.output.fast_arm_adapter as physical_adapter
    def forbidden(*args,**kwargs): pytest.fail("no-I/O boundary crossed")
    for name in ("socket","getaddrinfo","create_connection"):
        monkeypatch.setattr(socket,name,forbidden)
    monkeypatch.setattr(physical_adapter.FastArmPhysicalOutputSession,"__init__",forbidden)
    target=session(); p=target.submit(request(),now_s=10.0)
    driver=BoundedFastArmObservationDriver(target,receive_nowait=queue_source([response(p)]),clock=Clock(10.1),max_datagrams=2)
    assert driver.tick().acknowledgement.reason=="simulated_router_observation_correlated"
    assert not hasattr(target,"latest_sendable_request")
    assert not hasattr(p,"allows_transmission")



def test_float32_rounding_is_checked_against_literal_bits():
    mp=mapping()
    mp=replace(mp, joint_coordinate_signs=tuple((name,1) for name in mp.profile_joint_order),
               joint_angle_offsets=tuple((name,0.0) for name in mp.profile_joint_order))
    req=_request(joint_angles_rad=(pi/1800.0,)*4)
    preview=build_fast_arm_signal_preview(req,mp,attempt_id="float32")
    assert preview.datagram[-16:] == bytes.fromhex("3dcccccd"*4)
    receipt=emulate_fast_arm_peer(preview.datagram,target_robot_id="arm_a",wire_joint_order=mp.wire_joint_order)
    assert receipt.decoded_command.position_degrees == (0.10000000149011612,)*4


@pytest.mark.parametrize("value",(None, {}, "mapping"))
def test_preview_does_not_invent_missing_mapping(value):
    with pytest.raises(TypeError):
        build_fast_arm_signal_preview(request(),value,attempt_id="unknown")


@pytest.mark.parametrize("clock_value",(float("nan"),float("inf"),True,9.0))
def test_session_rejects_invalid_or_backwards_clock_without_driver(clock_value):
    target=session(); p=target.submit(request(),now_s=10.0)
    assert target.observe_router_datagram(response(p),now_s=clock_value).reason=="fast_arm_clock_invalid"
    assert target.state=="failed"


@pytest.mark.parametrize("budget",(0,-1,True,1.5))
def test_driver_requires_finite_positive_packet_budget(budget):
    with pytest.raises(ValueError):
        BoundedFastArmObservationDriver(session(),receive_nowait=lambda:None,clock=Clock(),max_datagrams=budget)


def test_driver_drains_delayed_and_duplicate_response_without_extra_preview():
    target=session(); p=target.submit(request(),now_s=10.0); clock=Clock(10.2)
    driver=BoundedFastArmObservationDriver(target,receive_nowait=queue_source([response(p),response(p)]),clock=clock,max_datagrams=3)
    result=driver.tick()
    assert [x.reason for x in result.observations]==[
        "simulated_router_observation_correlated","router_observation_without_pending_attempt"]
    assert result.received_count==2 and not result.budget_exhausted
    assert result.acknowledgement.reason=="simulated_router_observation_correlated"


@pytest.mark.parametrize("action",("stop","disconnect"))
def test_driver_never_polls_after_terminal_action(action):
    target=session(); target.submit(request(),now_s=10.0)
    getattr(target,action)(now_s=10.1)
    def forbidden(): pytest.fail("terminal driver received another datagram")
    driver=BoundedFastArmObservationDriver(target,receive_nowait=forbidden,clock=Clock(10.2),max_datagrams=2)
    assert driver.tick().received_count==0


@pytest.mark.parametrize("kind",("mismatch","malformed","late","missing","disconnect"))
def test_driver_failure_matrix_on_existing_physical_session(kind):
    target, mp, evidence, config, sender, clock, physical, transmission = _new_session()
    _arm_session(target,(physical,transmission))
    target.submit(_evaluation(_request(),evidence),now_s=1.0)
    packet=emulate_fast_arm_peer(sender.send_calls[0],target_robot_id="arm_a",wire_joint_order=mp.wire_joint_order).response_datagram
    if kind=="mismatch":
        msg=decode_osc_message(packet)
        packet=encode_osc_message(replace(msg,arguments=("wrong",*msg.arguments[1:])))
    elif kind=="malformed": packet=b"wrong"
    clock.value=1.1
    if kind=="disconnect":
        def receive(): raise OSError("synthetic disconnect")
    else:
        receive=queue_source([] if kind=="missing" else [packet])
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,clock=clock,max_datagrams=2)
    if kind=="late": clock.value=2.0
    first=driver.tick()
    if kind in {"mismatch","malformed","missing"}:
        assert target.pending_acknowledgement.status=="pending"
        assert target.submit(_evaluation(_request(sequence=2,timestamp_s=1.15),evidence),now_s=1.15).status=="rejected"
        clock.value=2.0
        driver.tick()
    assert target.state=="failed"
    assert target.latest_sendable_request is None
    assert len(sender.send_calls)==1


def test_peer_modified_payload_does_not_correlate_with_original_request():
    target=session(); p=target.submit(request(),now_s=10.0)
    altered=p.datagram[:-4]+bytes.fromhex("00000000")
    wrong=emulate_fast_arm_peer(altered,target_robot_id="arm_a",wire_joint_order=mapping().wire_joint_order)
    assert target.observe_router_datagram(wrong.response_datagram,now_s=10.2).reason=="router_observation_correlation_mismatch"
    assert target.pending_acknowledgement.status=="pending"



def test_unexpected_receiver_exception_is_not_misreported_as_clock_error():
    target=session(); target.submit(request(),now_s=10.0)
    def receive(): raise RuntimeError("fixture failure")
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,clock=Clock(10.1),max_datagrams=2)
    with pytest.raises(RuntimeError,match="fixture failure"):
        driver.tick()
    assert target.state=="failed"
    assert target.pending_acknowledgement.reason=="transport_disconnected"


def test_shared_ack_dto_alias_and_local_socket_classification_are_preserved():
    from xpotato_sim.runtime.output.fast_arm_adapter import FastArmAcknowledgementEvidence as OldEvidence
    from xpotato_sim.runtime.output.fast_arm_observation import (
        FastArmAcknowledgementEvidence, FastArmPendingObservation, resolve_fast_arm_router_datagram,
    )
    assert OldEvidence is FastArmAcknowledgementEvidence
    p=build_fast_arm_signal_preview(request(),mapping(),attempt_id="classification-test")
    # 判定関数だけのfixture。local_socketという値を実送信の証拠として報告しない。
    pending=FastArmPendingObservation(p.attempt_id,p.wire_command,11.0,"local_socket",started_at_s=10.0)
    result=resolve_fast_arm_router_datagram(pending,response(p),now_s=10.1)
    assert result.disposition=="clear"
    assert result.evidence.status=="router_command_observed"



def test_signal_preview_cannot_authorize_existing_physical_session():
    target, mp, evidence, config, sender, clock, physical, transmission = _new_session()
    _arm_session(target,(physical,transmission))
    preview=build_fast_arm_signal_preview(_request(),mp,attempt_id="not-a-permission")
    result=target.submit(preview,now_s=1.0)
    assert result.status=="failed"
    assert result.reason=="fast_arm_safety_evaluation_required"
    assert target.latest_sendable_request is None
    assert sender.prepare_calls==[] and sender.send_calls==[]


def test_malformed_packet_at_deadline_cannot_keep_physical_session_alive():
    target, mp, evidence, config, sender, clock, physical, transmission = _new_session()
    _arm_session(target,(physical,transmission))
    target.submit(_evaluation(_request(),evidence),now_s=1.0)
    result=target.observe_router_datagram(b"garbage",now_s=2.0)
    assert result.reason=="simulated_router_observation_timeout"
    assert target.state=="failed" and target.latest_sendable_request is None
    assert len(sender.send_calls)==1



@pytest.mark.parametrize("cause",("receiver","clock"))
def test_cleanup_failure_preserves_primary_cause_and_closes_driver(cause):
    class FailingCleanupSession(FastArmSignalSession):
        def disconnect(self, *, now_s):
            super().disconnect(now_s=now_s)
            raise RuntimeError("cleanup_failure")

        def expire_acknowledgement(self, *, now_s):
            result=super().expire_acknowledgement(now_s=now_s)
            if now_s != now_s:
                raise RuntimeError("cleanup_failure")
            return result

    target=FailingCleanupSession(profile=FAST_ARM_ROBOT_PROFILE,mapping=mapping(),acknowledgement_timeout_s=1.0)
    target.submit(request(),now_s=10.0)
    def receive(): raise ValueError("primary_receiver_failure")
    driver=BoundedFastArmObservationDriver(target,receive_nowait=receive,
        clock=Clock(float("nan") if cause=="clock" else 10.1),max_datagrams=1)
    with pytest.raises(ValueError,match="clock is invalid" if cause=="clock" else "primary_receiver_failure") as error:
        driver.tick()
    assert any("cleanup_failure" in note for note in error.value.__notes__)
    assert target.state=="failed"
    assert driver.tick().received_count==0
