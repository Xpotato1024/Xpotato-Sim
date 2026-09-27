"""両側で既存physical gateを通す。合成evidence/in-memory senderのみで試験する。"""
from dataclasses import replace
import socket
import pytest
from xpotato_sim.runtime.output.coordinated import CoordinatedPhysicalOutputGroup
from xpotato_sim.runtime.output.fast_arm_adapter import FastArmPreparedSubmission
from xpotato_sim.runtime.output.fast_arm_emulation import emulate_fast_arm_peer
from xpotato_sim.schemas.coordinated import CoordinatedInput, EndpointVelocity
from tests.runtime import test_fast_arm_physical_output as support


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a,**k):raise AssertionError("no actual socket or DNS")
    monkeypatch.setattr(socket,"socket",forbidden)
    monkeypatch.setattr(socket,"getaddrinfo",forbidden)


def source(seq=0, *, stamp=1., neutral=True):
    return CoordinatedInput(tuple(EndpointVelocity(s,(0.,0.,0.) if neutral else (.01,0.,0.),"world")
                  for s in ("left","right")),"provider-1",seq,stamp,stamp,True,neutral,"a"*64)


def setup(monkeypatch, *, veto=None, max_age=10.):
    sessions={};senders={};evals={};permissions={};stops=[];clocks={}
    for side in ("left","right"):
        with monkeypatch.context() as m:
            m.setattr(support,"_SESSION_ID","coordinated-"+side)
            target="target_"+side
            session,mapping,evidence,config,sender,clock,physical,transmission=support._new_session(target_robot_id=target)
            request=support._request(target_robot_id=target)
            evals[side]=support._evaluation(request,evidence)
        sessions[side]=session;senders[side]=sender;permissions[side]=(physical,transmission);clocks[side]=clock
    def stop(side):stops.append(side);return True
    group=CoordinatedPhysicalOutputGroup(sessions,scene_preflight=(lambda es:True) if veto is None else veto,
        stop_requesters={s:lambda s=s:stop(s) for s in sessions},max_input_age_s=max_age,clock=lambda:1.)
    assert group.arm(permissions,neutral=source(),now_s=1.).state=="armed"
    return group,sessions,senders,evals,stops,permissions,clocks


def test_all_prepared_before_first_send_and_ack_remains_nonphysical(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    for side,sender in senders.items():
        old=sender.send
        def send(destination,datagram,old=old):
            assert all(len(other.prepare_calls)==1 for other in senders.values())
            return old(destination,datagram)
        monkeypatch.setattr(sender,"send",send)
    result=g.submit(ev,input=source(1,neutral=False),now_s=1.)
    assert result.state=="active" and result.dispatched_arms==("left","right")
    assert result.physical_stop_confirmed is False and not stops
    for side,session in ss.items():
        reply=emulate_fast_arm_peer(senders[side].send_calls[0],target_robot_id=session.target_robot_id,
                                   wire_joint_order=session.mapping.wire_joint_order)
        ack=g.observe(side,reply.response_datagram,now_s=1.01)
        assert ack.reason=="simulated_router_observation_correlated"
        assert ack.status=="unavailable" and g.state=="active"
    assert g.poll(now_s=1.02).state=="active"


@pytest.mark.parametrize("side",["left","right"])
def test_one_invalid_evaluation_prevents_all_sends(monkeypatch,side):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    ev[side]=replace(ev[side],request=replace(ev[side].request,target_robot_id="different"))
    result=g.submit(ev,input=source(1),now_s=1.)
    assert result.state=="faulted" and result.dispatched_arms==()
    assert all(s.send_calls==[] for s in senders.values())
    assert stops==["left","right"] and all(s.state=="stopped" for s in ss.values())


def test_late_scene_veto_revokes_all_prepared_work(monkeypatch):
    calls=[]
    def veto(es):calls.append(1);return len(calls)==1
    g,ss,senders,ev,stops,_,_=setup(monkeypatch,veto=veto)
    result=g.submit(ev,input=source(1),now_s=1.)
    assert result.state=="faulted" and result.dispatched_arms==()
    assert all(s.send_calls==[] and len(s.prepare_calls)==1 for s in senders.values())
    assert stops==["left","right"]


def test_partial_send_fault_stops_both_and_never_retries(monkeypatch):
    g,ss,senders,ev,stops,permissions,_=setup(monkeypatch)
    def fail(*a,**k):raise OSError("synthetic send failure")
    monkeypatch.setattr(senders["right"],"send",fail)
    result=g.submit(ev,input=source(1),now_s=1.)
    assert result.state=="faulted" and result.dispatched_arms==("left",)
    assert stops==["left","right"] and result.physical_stop_confirmed is False
    assert all(s.state=="stopped" for s in ss.values())
    assert g.arm(permissions,neutral=source(2),now_s=1.).state=="faulted"
    g.submit(ev,input=source(3),now_s=1.)
    assert len(senders["left"].send_calls)==1


def test_stop_request_failure_does_not_skip_other_arm(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    def fail():stops.append("left");raise OSError("stop request failed")
    from types import MappingProxyType
    g.stop_requesters=MappingProxyType({"left":fail,"right":lambda:stops.append("right")})
    result=g.stop("injected_failure",fault=True)
    assert stops==["left","right"]
    assert result.stop_results[0][2]=="stop_request_failed:OSError"
    assert result.stop_results[1][2]=="stop_request_unconfirmed"
    assert not result.physical_stop_confirmed
    assert all(s.state=="stopped" for s in ss.values())


def test_wrong_arm_reply_latches_all(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    g.submit(ev,input=source(1),now_s=1.)
    right=ss["right"]
    reply=emulate_fast_arm_peer(senders["right"].send_calls[0],target_robot_id=right.target_robot_id,
                               wire_joint_order=right.mapping.wire_joint_order)
    g.observe("left",reply.response_datagram,now_s=1.01)
    assert g.state=="faulted" and stops==["left","right"]


def test_timeout_of_one_arm_stops_both(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    g.submit(ev,input=source(1),now_s=1.)
    left=ss["left"]
    reply=emulate_fast_arm_peer(senders["left"].send_calls[0],target_robot_id=left.target_robot_id,
                               wire_joint_order=left.mapping.wire_joint_order)
    g.observe("left",reply.response_datagram,now_s=1.01)
    result=g.poll(now_s=3.)
    assert result.state=="faulted" and "right" in result.reason
    assert stops==["left","right"]


def test_input_expiry_faults_even_without_pending_ack(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch,max_age=.2)
    result=g.poll(now_s=1.21)
    assert result.state=="faulted" and stops==["left","right"]
    assert all(s.send_calls==[] for s in senders.values())


def test_prepare_ticket_is_single_owner_single_use(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    ticket=ss["left"].prepare_submission(ev["left"],now_s=1.)
    assert type(ticket) is FastArmPreparedSubmission
    assert ss["right"].dispatch_submission(ticket,now_s=1.).status=="rejected"
    assert ss["left"].dispatch_submission(replace(ticket),now_s=1.).status=="rejected"
    assert all(s.send_calls==[] for s in senders.values())
    assert ss["left"].dispatch_submission(ticket,now_s=1.).status=="transmission_attempted"
    assert ss["left"].dispatch_submission(ticket,now_s=1.).status=="rejected"
    assert len(senders["left"].send_calls)==1


def test_stop_during_prepare_prevents_dispatch(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    old=senders["right"].prepare
    def prepare(endpoint):
        g.stop("operator_stop_during_prepare")
        return old(endpoint)
    monkeypatch.setattr(senders["right"],"prepare",prepare)
    result=g.submit(ev,input=source(1),now_s=1.)
    assert result.state in ("stopped","faulted") and not result.dispatched_arms
    assert all(s.send_calls==[] for s in senders.values()) and stops==["left","right"]


@pytest.mark.parametrize("side",["left","right"])
def test_external_single_arm_stop_is_propagated_by_group_poll(monkeypatch,side):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    ss[side].stop(now_s=1.)
    result=g.poll(now_s=1.01)
    assert result.state=="faulted" and stops==["left","right"]
    assert all(s.state=="stopped" for s in ss.values())


def test_missing_stop_capability_is_not_silently_enabled(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    for session in ss.values():session.stop(now_s=1.)
    # 新しい未armingのsessionがあっても、一側の停止経路欠落は拒否。
    session,*_=support._new_session(target_robot_id="new-target")
    with pytest.raises(ValueError,match="stop requester"):
        CoordinatedPhysicalOutputGroup({"left":session},scene_preflight=lambda es:True,
            stop_requesters={},max_input_age_s=.2)


def test_runtime_named_command_reaches_both_physical_gates(monkeypatch):
    from tests.runtime.test_coordinated_gamepad import app
    from tests.plugins.mappings.viewer_keyboard_gamepad_mapping.test_gamepad_planes import message
    from xpotato_sim.plugins.robots.fast_arm.adapter.assembly_output import FastArmOutputBinding, build_fast_arm_assembly_requests
    a,c=app()
    # 既存のsynthetic physical envelopeは全jointが[-1,1]。限界を緩和せず、
    # その内側にある明示initial stateから実MuJoCo共同更新を試す。
    import mujoco
    provider=a.runtime.provider
    for arm in provider.addresses:
        provider._data.qpos[list(arm.qpos_addresses)]=(0.,-.5,0.,-.8)
    mujoco.mj_forward(provider.model,provider._data)
    c[0]=1.;a.ingest(message(sequence=0));neutral=a.tick(epoch="run-1")
    c[0]=1.01;a.ingest(message((.55,0.,-.55,0.),sequence=1));row=a.tick(epoch="run-1")
    assert row.state=="running" and row.command.timestamp_s==1.01
    assert row.after.simulation_time_s!=row.command.timestamp_s
    spec=a.runtime.provider.assembly
    bindings=tuple(FastArmOutputBinding(s,"target_"+s,support._ENDPOINT_ID,support._mapping()) for s in spec.arm_ids)
    batch=build_fast_arm_assembly_requests(spec,row.command,joint_names=row.after.joint_names,bindings=bindings,
        model_sha256=row.after.model_sha256,session_id="runtime-batch",sequence=1,cadence_s=.1,software_revision=support._REVISION)
    sessions={};senders={};evals={};permissions={};stops=[]
    for item in batch.items:
        with monkeypatch.context() as m:
            m.setattr(support,"_SESSION_ID",item.request.session_id)
            session,mapping,evidence,config,sender,clock,physical,transmission=support._new_session(target_robot_id=item.request.target_robot_id)
            clock.value=c[0]  # adapter最終guardも同じhost時刻を観測させる。
            evals[item.arm_id]=support._evaluation(item.request,evidence)
        sessions[item.arm_id]=session;senders[item.arm_id]=sender;permissions[item.arm_id]=(physical,transmission)
    group=CoordinatedPhysicalOutputGroup(sessions,scene_preflight=lambda es:True,
        stop_requesters={s:lambda s=s:stops.append(s) for s in sessions},max_input_age_s=.2,clock=lambda:c[0])
    assert group.arm(permissions,neutral=neutral.input,now_s=1.).state=="armed"
    result=group.submit(evals,input=row.input,now_s=1.01)
    assert result.state=="active",result.reason
    assert result.dispatched_arms==spec.arm_ids and result.dispatch_call_arms==spec.arm_ids
    assert all(len(sender.send_calls)==1 for sender in senders.values())
    group.stop()



def test_concurrent_stop_during_transport_prepare_never_dispatches(monkeypatch):
    from threading import Event, Thread
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    entered,release=Event(),Event();old=senders["right"].prepare;results=[]
    def blocked(endpoint):
        entered.set()
        if not release.wait(3):raise TimeoutError("test preflight deadline")
        return old(endpoint)
    monkeypatch.setattr(senders["right"],"prepare",blocked)
    thread=Thread(target=lambda:results.append(g.submit(ev,input=source(1),now_s=1.)))
    thread.start()
    try:
        assert entered.wait(3)
        g.stop("concurrent_operator_stop")
    finally:
        release.set();thread.join(3)
    assert not thread.is_alive() and len(results)==1
    assert not results[0].dispatched_arms and stops==["left","right"]
    assert all(sender.send_calls==[] for sender in senders.values())



def test_local_stop_error_attempts_abort_and_does_not_skip_other_side(monkeypatch):
    g,ss,senders,ev,stops,_,_=setup(monkeypatch)
    ticket=ss["left"].prepare_submission(ev["left"],now_s=1.)
    def broken_stop(**kwargs):raise OSError("injected local stop error")
    monkeypatch.setattr(ss["left"],"stop",broken_stop)
    result=g.stop("test_stop_failure",fault=True)
    assert ss["left"].state=="aborted" and ss["right"].state=="stopped"
    assert stops==["left","right"]
    assert result.stop_results[0][1]=="local_stop_failed:OSError;local_abort_attempted"
    assert ss["left"].dispatch_submission(ticket,now_s=1.).status=="rejected"
    assert all(sender.send_calls==[] for sender in senders.values())
