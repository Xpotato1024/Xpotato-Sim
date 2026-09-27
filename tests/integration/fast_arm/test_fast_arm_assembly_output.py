"""単腕・双腕を同じ名前対応と既存OSC/応答経路へ通す。全てsynthetic/no-I/O。"""
from dataclasses import replace
from math import pi
import socket
import struct

import mujoco
import pytest

from fast_arm_core.assembly import FastArmAssembly, FastArmInstance, resolve_assembly_addresses
from fast_arm_core.assembly_model import build_fast_arm_assembly_model
from fast_arm_core.definition import FAST_ARM_JOINT_NAMES
from xpotato_sim.plugins.robots.fast_arm.adapter.assembly_output import (
    FastArmOutputBinding, build_fast_arm_assembly_requests,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import FastArmOutputMapping
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.runtime.output.fast_arm_adapter import create_fast_arm_wire_encoder
from xpotato_sim.runtime.output.fast_arm_emulation import FastArmSignalSession, emulate_fast_arm_peer
from xpotato_sim.schemas.command import JointPositionCommand
from xpotato_sim.transport.osc import decode_osc_message


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("assembly tests must not perform socket or DNS I/O")
    monkeypatch.setattr(socket,"socket",forbidden)
    monkeypatch.setattr(socket,"getaddrinfo",forbidden)


def setup():
    spec = FastArmAssembly((FastArmInstance("right",False,(0,-.4,0),(1,0,0,0)),
                            FastArmInstance("left",True,(0,.4,0),(1,0,0,0))))
    profile = FAST_ARM_ROBOT_PROFILE
    wire = ("wire2","wire0","wire3","wire1")
    def mapping(side):
        # 値は独立oracle用の合成校正。mirror_yから実機符号を自動導出しない。
        signs = (1,-1,-1,1) if side == "right" else (-1,1,1,-1)
        offsets = (10,20,30,40) if side == "right" else (1,2,3,4)
        return FastArmOutputMapping(
            profile.profile_id, profile.profile_contract_version, profile.model_contract_version,
            FAST_ARM_JOINT_NAMES, wire, tuple(zip(FAST_ARM_JOINT_NAMES,("wire0","wire1","wire2","wire3"))),
            "assembly-test-"+side, "rad", "degree", "degree",
            tuple(zip(FAST_ARM_JOINT_NAMES,signs)), tuple(zip(FAST_ARM_JOINT_NAMES,offsets)),
            "joint_position_command/v1",
        )
    bindings = tuple(FastArmOutputBinding(side,"target_"+side,"joint-group",mapping(side)) for side in spec.arm_ids)
    cmd = JointPositionCommand(timestamp_s=1., joint_angles_rad=(pi/2,pi/4,-pi/2,0.)*2)
    return spec, bindings, cmd


def batch(spec, bindings, command, **kwargs):
    options = dict(joint_names=spec.joint_names, model_sha256="a"*64,session_id="run",
                   sequence=0,cadence_s=.02,software_revision="synthetic-test-revision")
    options.update(kwargs)
    return build_fast_arm_assembly_requests(spec,command,bindings=bindings,**options)


@pytest.mark.parametrize("selected",[(0,), (1,), (0,1)])
def test_both_sides_have_real_codec_and_independent_golden_bytes(selected):
    spec,bindings,cmd = setup()
    spec = FastArmAssembly(tuple(spec.instances[i] for i in selected))
    bindings = tuple(bindings[i] for i in selected)
    cmd = replace(cmd,joint_angles_rad=(pi/2,pi/4,-pi/2,0.)*len(selected))
    result = batch(spec,bindings,cmd)
    assert len(result.items) == len(selected)
    for item in result.items:
        session = FastArmSignalSession(profile=FAST_ARM_ROBOT_PROFILE,mapping=item.mapping,
                                       acknowledgement_timeout_s=1.)
        preview = session.submit(item.request,now_s=10.)
        expected = (120.,100.,40.,-25.) if item.arm_id == "right" else (-87.,-89.,4.,47.)
        assert preview.datagram.endswith(b",ffff\0\0\0"+struct.pack("!ffff",*expected))
        message = decode_osc_message(preview.datagram)
        assert message.address.endswith("/target_"+item.arm_id+"/joint")
        assert message.arguments == pytest.approx(expected)
        assert create_fast_arm_wire_encoder(item.mapping).requires_external_authorization is True
        receipt = emulate_fast_arm_peer(preview.datagram,target_robot_id=item.request.target_robot_id,
                                        wire_joint_order=item.mapping.wire_joint_order)
        evidence = session.observe_router_datagram(receipt.response_datagram,now_s=10.1)
        assert evidence.status == "unavailable"  # 疑似相関を実機ACKに昇格しない。
        assert evidence.reason == "simulated_router_observation_correlated"


def test_shuffled_full_command_and_binding_orders_are_resolved_by_joint_names():
    spec,bindings,cmd = setup()
    expected = batch(spec,bindings,cmd)
    reversed_cmd = replace(cmd,joint_angles_rad=tuple(reversed(cmd.joint_angles_rad)))
    actual = batch(spec,tuple(reversed(bindings)),reversed_cmd,joint_names=tuple(reversed(spec.joint_names)))
    assert actual == expected
    assert actual.identity_sha256 == expected.identity_sha256
    assert len(actual.identity_sha256) == 64
    assert actual.items[0].request.session_id != actual.items[1].request.session_id


@pytest.mark.parametrize("mutate", [
    lambda bs: bs[:1],
    lambda bs: (bs[0],bs[0]),
    lambda bs: (bs[0],replace(bs[1],arm_id="missing")),
    lambda bs: (bs[0],replace(bs[1],target_robot_id=bs[0].target_robot_id)),
    lambda bs: (bs[0],replace(bs[1],target_robot_id="illegal/path")),
])
def test_missing_duplicate_or_ambiguous_target_bindings_reject_entire_batch(mutate):
    spec,bindings,cmd = setup()
    with pytest.raises((ValueError,TypeError)):
        batch(spec,mutate(bindings),cmd)


@pytest.mark.parametrize("names", [(), ("right__sholder_joint_1",)*8, ("wrong",)*8])
def test_bad_command_names_are_not_silently_sliced(names):
    spec,bindings,cmd = setup()
    with pytest.raises(ValueError):
        batch(spec,bindings,cmd,joint_names=names)


@pytest.mark.parametrize("options", [{"model_sha256":"unknown"},{"session_id":" "},
    {"sequence":True},{"cadence_s":0},{"software_revision":""}])
def test_missing_identity_and_timing_reject_before_any_output(options):
    spec,bindings,cmd = setup()
    with pytest.raises((ValueError,TypeError)):
        batch(spec,bindings,cmd,**options)


def test_float32_overflow_on_one_arm_does_not_return_partial_batch():
    spec,bindings,cmd = setup()
    cmd = replace(cmd,joint_angles_rad=(*cmd.joint_angles_rad[:-1],1e100))
    with pytest.raises(ValueError,match="float32"):
        batch(spec,bindings,cmd)


def test_wrong_arm_response_timeout_and_stop_are_independent_for_both_arms():
    spec,bindings,cmd = setup(); result = batch(spec,bindings,cmd)
    sessions = [FastArmSignalSession(profile=FAST_ARM_ROBOT_PROFILE,mapping=item.mapping,
                                    acknowledgement_timeout_s=1.) for item in result.items]
    previews = [session.submit(item.request,now_s=10.) for session,item in zip(sessions,result.items)]
    replies = [emulate_fast_arm_peer(p.datagram,target_robot_id=p.request.target_robot_id,
                                   wire_joint_order=p.wire_command.joint_order).response_datagram for p in previews]
    for i,session in enumerate(sessions):
        wrong = session.observe_router_datagram(replies[1-i],now_s=10.1)
        assert wrong.reason == "router_observation_correlation_mismatch"
        assert session.pending_acknowledgement.status == "pending"
    sessions[0].observe_router_datagram(replies[0],now_s=10.2)
    assert sessions[1].pending_acknowledgement.status == "pending"
    assert sessions[1].expire_acknowledgement(now_s=11.).reason == "simulated_router_observation_timeout"
    sessions[0].stop(now_s=11.)
    for session,item in zip(sessions,result.items):
        with pytest.raises(RuntimeError):
            session.submit(replace(item.request,sequence=1),now_s=11.1)


def test_same_model_joint_addresses_feed_both_existing_output_routes():
    spec,bindings,_ = setup(); built = build_fast_arm_assembly_model(spec)
    model = mujoco.MjModel.from_xml_string(built.xml.decode(),dict(built.assets))
    data = mujoco.MjData(model); mujoco.mj_resetDataKeyframe(model,data,0); mujoco.mj_forward(model,data)
    addresses = resolve_assembly_addresses(model,spec)
    names = tuple(name for arm in addresses for name in arm.joint_names)
    # 観測値のexportを試す。実機で送るべき目標を観測値から勝手に合成するrunnerではない。
    command = JointPositionCommand(timestamp_s=float(data.time),
              joint_angles_rad=tuple(float(data.qpos[i]) for arm in addresses for i in arm.qpos_addresses))
    result = batch(spec,bindings,command,joint_names=names,model_sha256=built.model_sha256)
    assert result.model_sha256 == built.model_sha256
    for item,arm in zip(result.items,addresses):
        assert item.arm_id == arm.arm_id
        assert item.request.command.joint_angles_rad == tuple(data.qpos[list(arm.qpos_addresses)])


@pytest.mark.parametrize("side", ["right","left"])
def test_each_assembly_request_reaches_existing_physical_gates_with_fake_sender(monkeypatch,side):
    # 既存test専用の合成evidenceとin-memory senderを利用し、実物の許可は作らない。
    from tests.runtime import test_fast_arm_physical_output as support
    spec,bindings,cmd = setup()
    cmd = replace(cmd,joint_angles_rad=(.1,.2,.3,.4)*2)
    result = batch(spec,bindings,cmd,cadence_s=.1)
    item = next(item for item in result.items if item.arm_id == side)
    monkeypatch.setattr(support,"_mapping",lambda: item.mapping)
    monkeypatch.setattr(support,"_SESSION_ID",item.request.session_id)
    monkeypatch.setattr(support,"_ENDPOINT_ID",item.request.endpoint_id)
    monkeypatch.setattr(support,"_REVISION",item.request.software_revision)
    session,_,evidence,_,sender,_,physical,transmission = support._new_session(target_robot_id=item.request.target_robot_id)
    evaluated = support._evaluation(item.request,evidence)
    # 未armingのrequestでは右/左とも送信されない。
    assert session.submit(evaluated,now_s=1.).status == "rejected"
    assert sender.send_calls == []
    support._arm_session(session,(physical,transmission))
    output = session.submit(evaluated,now_s=1.)
    assert output.status == "transmission_attempted"
    assert len(sender.send_calls) == 1
    receipt = emulate_fast_arm_peer(sender.send_calls[0],target_robot_id=item.request.target_robot_id,
                                   wire_joint_order=item.mapping.wire_joint_order)
    ack = session.observe_router_datagram(receipt.response_datagram,now_s=1.1)
    assert ack.reason == "simulated_router_observation_correlated"
    assert ack.status == "unavailable"
