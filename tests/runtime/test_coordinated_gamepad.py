"""実MuJoCoの両腕更新、旧Mappingとの一致、異常時の全体拒否。実I/Oなし。"""
from dataclasses import replace
from math import cos, pi, sin
import socket
import pytest
from fast_arm_core.assembly import FastArmAssembly, FastArmInstance
from fast_arm_core.models import resolve_fast_arm_model
from xpotato_sim.plugins.robots.fast_arm.adapter.coordinated import FastArmAssemblyMotionProvider
from xpotato_sim.runtime.composition.fast_arm_coordinated import FastArmCoordinatedGamepadRuntime
from xpotato_sim.schemas.coordinated import EndpointVelocity
from tests.plugins.mappings.viewer_keyboard_gamepad_mapping.test_gamepad_planes import message, parameters




@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("coordinated diagnostic must not access socket/DNS")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def assembly(ids=("left", "right")):
    by_id = {item.arm_id: item for item in resolve_fast_arm_model("bimanual").assembly.instances}
    return FastArmAssembly(tuple(by_id[arm_id] for arm_id in ids))


def app(ids=("left", "right")):
    clock=[0.]
    instance=FastArmCoordinatedGamepadRuntime(assembly=assembly(ids), mapping_parameters=parameters(),
        side_to_arm={s:s for s in ids}, epoch="run-1", dt_s=1/60, max_input_age_s=.2, clock=lambda:clock[0])
    return instance,clock


def step(a,clock,msg):
    clock[0]=msg.timestamp_s
    a.ingest(msg)
    return a.tick(epoch=a.runtime.epoch)


@pytest.mark.parametrize("ids", [("left",),("right",),("left","right"),("right","left")])
@pytest.mark.parametrize("axis,sign",[(0,1),(0,-1),(1,1),(1,-1),(2,1),(2,-1)])
def test_both_arms_six_directions_match_independent_same_snapshot(ids,axis,sign):
    a,c=app(ids); held=(4,5) if axis==2 else ()
    assert step(a,c,message(sequence=0,held=held)).state=="running"
    raw=[0.]*4
    for side in ids:
        offset=0 if side=="left" else 2
        raw[offset+int(axis!=0)]=sign if axis==0 else -sign
    row=step(a,c,message(tuple(raw),held,1))
    assert row.state=="running",row.reason
    assert row.after.simulation_time_s==pytest.approx(1/60)
    before=dict(zip(row.before.joint_names,row.before.joint_positions_rad))
    after=dict(zip(row.after.joint_names,row.after.joint_positions_rad))
    for arm in assembly(ids).instances:
        solo=FastArmAssemblyMotionProvider(FastArmAssembly((arm,)))
        v=[0.]*3;v[axis]=sign*.1
        ticket=solo.prepare((EndpointVelocity(arm.arm_id,tuple(v),"world"),),1/60)
        expect=solo.commit(ticket)
        assert tuple(after[n] for n in arm.joint_names)==pytest.approx(expect.joint_positions_rad)
        assert any(after[n]!=before[n] for n in arm.joint_names)


def test_mode_switch_stops_only_switching_side_without_reinterpreting_tilt():
    a,c=app();step(a,c,message(sequence=0))
    row=step(a,c,message((.55,0.,0.,-.55),(5,),1))
    assert row.input.endpoints[0].velocity_m_s==pytest.approx((.05,0,0))
    assert row.input.endpoints[1].velocity_m_s==(0.,0.,0.)
    step(a,c,message((.55,0.,0.,0.),(5,),2))
    row=step(a,c,message((.55,0.,0.,-.55),(5,),3))
    assert row.input.endpoints[1].velocity_m_s==pytest.approx((0,0,.05))


def test_one_failed_candidate_never_publishes_other_arm(monkeypatch):
    a,c=app();step(a,c,message(sequence=0));p=a.runtime.provider;before=p.snapshot()
    original=p._arm_candidate; seen=[]
    def fail(base,arm,command,dt,*,observed=None):
        seen.append(tuple(base.qpos))
        if arm.arm_id=="right":raise ValueError("right candidate failed")
        return original(base,arm,command,dt,observed=observed)
    monkeypatch.setattr(p,"_arm_candidate",fail)
    row=step(a,c,message((.55,0.,.55,0.),sequence=1))
    assert row.state=="faulted" and "right candidate failed" in row.reason
    assert len(seen)==2 and seen[0]==seen[1]
    assert p.snapshot()==before and row.command is None
    assert step(a,c,message(sequence=2)).state=="faulted"


def test_exact_prepared_ticket_cannot_be_copied_reused_or_survive_reset():
    p=FastArmAssemblyMotionProvider(assembly())
    cmd=tuple(EndpointVelocity(s,(.01,0.,0.),"world") for s in p.endpoint_ids)
    before=p.snapshot();t=p.prepare(cmd,.01)
    with pytest.raises(ValueError):p.commit(replace(t))
    assert p.snapshot()==before
    t=p.prepare(cmd,.01);p.commit(t)
    with pytest.raises(ValueError):p.commit(t)
    t=p.prepare(cmd,.01);p.reset()
    with pytest.raises(ValueError):p.commit(t)


def test_receipt_age_not_refreshed_by_repeated_tick_and_fault_needs_new_epoch():
    a,c=app();step(a,c,message(sequence=0));step(a,c,message((.55,0.,-.55,0.),sequence=1))
    receipt=a.source.last_received_at_s;c[0]+=.05
    assert a.tick(epoch="run-1").state=="running"
    assert a.source.last_received_at_s==receipt
    c[0]+=.3;row=a.tick(epoch="run-1");assert row.state=="faulted"
    with pytest.raises(ValueError):a.restart(epoch="run-1")
    a.restart(epoch="run-2")
    # 新しいruntimeでも旧provider系列は復活させない。
    old=replace(message(sequence=30),timestamp_s=c[0]);a.ingest(old)
    assert a.tick(epoch="run-2").state=="faulted"
    a.restart(epoch="run-3")
    fresh=replace(old,metadata={"viewer_provider_session_id":"new-provider"})
    a.ingest(fresh);assert a.tick(epoch="run-3").state=="running"


@pytest.mark.parametrize("bad", [
    lambda m:replace(m,gamepad=replace(m.gamepad,connected=False)),
    lambda m:replace(m,gamepad=replace(m.gamepad,stale=True)),
])
def test_initial_unavailable_input_stays_waiting_and_recovers_on_fresh_neutral(bad):
    a,c=app()
    before=a.runtime.provider.snapshot()
    row=step(a,c,bad(message(sequence=0)))
    assert row.state=="waiting_neutral"
    assert row.reason=="input_unavailable"
    assert a.runtime.provider.snapshot()==before
    row=step(a,c,message(sequence=1))
    assert row.state=="running" and row.reason is None
    assert a.runtime.provider.snapshot()==before


@pytest.mark.parametrize("bad", [lambda m:replace(m,gamepad=replace(m.gamepad,connected=False)),
    lambda m:replace(m,gamepad=replace(m.gamepad,stale=True)),
    lambda m:replace(m,gamepad=replace(m.gamepad,raw_axes=(.5,0.))),
    lambda m:replace(m,metadata={"viewer_provider_session_id":"other-stream"}),
    lambda m:replace(m,gamepad=replace(m.gamepad,id="another-device"))])
def test_bad_input_latches_both_arms(bad):
    a,c=app();step(a,c,message(sequence=0));before=a.runtime.provider.snapshot()
    row=step(a,c,bad(message((.55,0.,.55,0.),sequence=1)))
    assert row.state=="faulted" and a.runtime.provider.snapshot()==before


def test_provider_snapshot_failure_latches(monkeypatch):
    a,c=app();step(a,c,message(sequence=0))
    def fail():raise RuntimeError("snapshot failure")
    monkeypatch.setattr(a.runtime.provider,"snapshot",fail)
    row=a.tick(epoch="run-1")
    assert row.state=="faulted" and row.before is None and row.after is None



def test_final_candidate_validation_failure_does_not_publish(monkeypatch):
    a,c=app();step(a,c,message(sequence=0));p=a.runtime.provider;before=p.snapshot();old=p._check_data
    def reject_candidate(data):
        old(data)
        if data is not p._data:raise ValueError("post-candidate validation rejected")
    monkeypatch.setattr(p,"_check_data",reject_candidate)
    row=step(a,c,message((.55,0.,-.55,0.),sequence=1))
    assert row.state=="faulted" and p.snapshot()==before


def test_diagnostic_document_runs_both_and_records_terminal_fault():
    import json
    from pathlib import Path
    from xpotato_sim.runtime.runners.coordinated_gamepad import run_document
    raw=json.loads((Path(__file__).parents[1]/"fixtures/coordinated_gamepad/bimanual.json").read_text(encoding="utf-8"))
    mounts = {item["arm_id"]: item["quaternion_wxyz"] for item in raw["assembly"]}
    for item in resolve_fast_arm_model("bimanual").assembly.instances:
        assert mounts[item.arm_id] == pytest.approx(item.quaternion_wxyz)
    result=run_document(raw)
    assert result["state"]=="stopped" and len(result["rows"])==6
    assert result["physical_output"]=="disabled"
    assert all(row["state"]=="running" for row in result["rows"])
    assert result["rows"][1]["command"]["timestamp_s"]==raw["samples"][1]["host_time_s"]
    raw["samples"].append({"host_time_s":10.,"message":None})
    result=run_document(raw)
    assert result["state"]=="faulted" and result["rows"][-1]["command"] is None


@pytest.mark.parametrize("path,value",[("schema","wrong"),("samples",[]),("dt_s",0),
    ("side_to_arm",{"left":"left"}),("unknown",True)])
def test_diagnostic_configuration_rejects_unknown_or_incomplete(path,value):
    import json
    from pathlib import Path
    from xpotato_sim.runtime.runners.coordinated_gamepad import run_document
    raw=json.loads((Path(__file__).parents[1]/"fixtures/coordinated_gamepad/bimanual.json").read_text(encoding="utf-8"))
    raw[path]=value
    with pytest.raises((ValueError,TypeError)):
        run_document(raw)
