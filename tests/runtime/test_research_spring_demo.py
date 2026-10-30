"""ばねモデル、保存した連続値、デモ隔離を実MuJoCoで確認する。実機・知覚評価ではない。"""
from dataclasses import replace
import json
from pathlib import Path
import mujoco
import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition, shared_task_profile
from xpotato_sim.runtime.experiment.continuous_record import comparison_metrics
from xpotato_sim.runtime.scene.spring import observe_springs, SpringAxis
from tests.runtime.test_trial_runner import Clock, start, message


def test_demo_never_constructs_experiment_recorders(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("experiment recorder created during demo")
    monkeypatch.setattr("xpotato_sim.runtime.experiment.trial_runner.TrialRecorder",forbidden)
    monkeypatch.setattr("xpotato_sim.runtime.experiment.continuous_record.ContinuousRecord",forbidden)
    r,ticket,clock=prepared(tmp_path,mode="demo")
    run(r,ticket,clock)
    r.close()
    assert not list(tmp_path.iterdir())


def test_failed_continuous_write_cannot_publish_success(tmp_path):
    r,ticket,clock=prepared(tmp_path)
    start(r,ticket,clock)
    r._continuous.size=64*1024*1024
    with pytest.raises(OSError,match="bound"):
        r.ingest(ticket,message(1),received_at_s=clock())
    assert r.status=="recording_failed"
    assert r.result.to_document()["recording"]=="failed"
    assert not (tmp_path/ticket.trial_id/"terminal.json").exists()
    r.close()


def prepared(root, *, mode="experiment", cue="no_force_cue"):
    document = preset_condition("tasks/research-spring", input_provider="gamepad/v1")
    document["display_comparison"]["mode"] = cue
    document["limits"]["max_ticks"] = 4
    profile, limits, _ = resolve_condition(document)
    clock = Clock()
    runner = TrialRunner(result_root=root, software_revision="software-test-only", clock=clock)
    ticket = runner.prepare(profile, limits, session_mode=mode)
    return runner, ticket, clock


def run(runner, ticket, clock):
    start(runner, ticket, clock)
    runner.ingest(ticket, message(1, (.1, 0, 0, 0)), received_at_s=clock())
    while runner.status == "running":
        runner.advance(ticket)
    assert runner.status == "terminal", runner.result.to_document()


def test_spring_force_and_reset(tmp_path):
    r, ticket, clock = prepared(tmp_path, mode="demo")
    provider = r._execution.instance.provider
    manifest = r._execution.profile.scene_plan.manifest
    jid = mujoco.mj_name2id(provider.model, mujoco.mjtObj.mjOBJ_JOINT, "object__cube__spring")
    assert provider.model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_SLIDE
    assert provider.model.jnt_range[jid].tolist() == [0., .05]
    qa, va = provider.model.jnt_qposadr[jid], provider.model.jnt_dofadr[jid]
    # native live dataの正式ownerが持つdataで解析解とpassive forceを比較する。
    data = mujoco.MjData(provider.model)
    data.qpos[qa], data.qvel[va] = .02, .1
    mujoco.mj_forward(provider.model, data)
    observed = observe_springs(provider.model, data, manifest)[0]
    assert observed["axial_force_n"] == pytest.approx(-2.2)
    assert data.qfrc_passive[va] == pytest.approx(-2.2)
    # 同一の合成modelを一定軸荷重で積分し、静的釣合い F/k を確認する。
    mujoco.mj_resetDataKeyframe(provider.model, data, mujoco.mj_name2id(provider.model, mujoco.mjtObj.mjOBJ_KEY, "home"))
    data.qfrc_applied[va] = 1.
    for _ in range(2400):
        mujoco.mj_step(provider.model, data)
    assert data.qpos[qa] == pytest.approx(.01, abs=1e-5)
    run(r, ticket, clock)
    r.retry()
    assert r.snapshot().metadata["scene_dynamics_v1"]["springs"][0]["displacement_m"] == 0
    r.close()
    assert not list(tmp_path.iterdir())


def test_saved_metrics_and_cue_independence(tmp_path):
    records=[]
    for cue in ("no_force_cue", "force_cue"):
        r, ticket, clock = prepared(tmp_path/cue, cue=cue)
        run(r, ticket, clock)
        directory = tmp_path/cue/ticket.trial_id
        metrics = comparison_metrics(directory)
        assert metrics["ticks"] == 4
        lines = [json.loads(s) for s in (directory/"samples.jsonl").read_bytes().splitlines()]
        ticks = [{k:v for k,v in row.items() if k not in {"trial_id","epoch","condition_sha256","input_identity"}}
                 for row in lines if row["kind"] == "tick"]
        for row in ticks:
            row["task"] = {k:v for k,v in row["task"].items() if k != "epoch"}
            assert row["sites"] and len(row["qvel"]) == len(row["qpos"])
        # geometry/dynamicsとTaskのraw値はdisplay modeから独立する。
        records.append(ticks)
        assert any(row["kind"] == "input" and row["message"]["sequence"] == 1 for row in lines)
        assert lines[-1]["runner_stop_reason"] == "simulation_budget"
        r.close()
    assert records[0] == records[1]
    path = directory/"samples.jsonl"
    path.write_bytes(path.read_bytes()+b"\n")
    with pytest.raises(ValueError, match="digest"):
        comparison_metrics(directory)


def test_demo_switch_abort_retry_and_experiment_isolation(tmp_path):
    r, ticket, clock = prepared(tmp_path, mode="demo")
    start(r, ticket, clock)
    with pytest.raises(AttributeError):
        r.session_mode="experiment"
    assert r.session_mode=="demo"
    with pytest.raises(RuntimeError, match="inactive"):
        r.prepare(shared_task_profile("tasks/research-spring", "selfrionette-device/v1"), TrialLimits(4), session_mode="demo")
    r.abort(ticket)
    assert r.result.to_document()["recording"] == "disabled_demo"
    assert not list(tmp_path.iterdir())
    # input選択を変える準備は別Source/Mapping、epoch、homeと新しいneutral gateを作る。
    old_execution = r._execution
    fresh = r.prepare(shared_task_profile("tasks/research-spring", "selfrionette-device/v1"), TrialLimits(4), session_mode="demo")
    assert r._execution is not old_execution and fresh.epoch != ticket.epoch
    assert r._execution.state == "waiting_neutral" and r.tick_count == 0
    with pytest.raises(ValueError, match="old"):
        r.start(ticket, input_provenance={"source":"test"})
    r.prepare(shared_task_profile("tasks/research-spring", "gamepad/v1"), TrialLimits(4), session_mode="experiment")
    run(r, r.ticket, clock)
    assert len(list(tmp_path.iterdir())) == 1
    rows = [json.loads(s) for s in (tmp_path/r.ticket.trial_id/"samples.jsonl").read_bytes().splitlines()]
    assert all(row["epoch"] == r.ticket.epoch for row in rows)
    r.close()


@pytest.mark.parametrize("changes", [{"axis":(0,0,0)}, {"range_m":(0,0)}, {"stiffness_n_m":0},
    {"damping_n_s_m":-1}, {"initial_displacement_m":1}, {"initial_velocity_m_s":float("nan")}])
def test_spring_invalid_configuration(changes):
    with pytest.raises(ValueError):
        SpringAxis(**({"axis":(0,1,0),"stiffness_n_m":100,"damping_n_s_m":2,"range_m":(0,.05),
            "initial_displacement_m":0,"initial_velocity_m_s":0}|changes))


def test_device_spring_display_replay_and_disconnect(tmp_path):
    from tests.runtime.test_device_force_integration import ingest
    traces=[]
    for mode in ("no_force_cue", "force_cue"):
        doc=preset_condition("tasks/research-spring", input_provider="selfrionette-device/v1")
        doc["display_comparison"]["mode"]=mode
        doc["limits"]["max_ticks"]=4
        p, limits, _=resolve_condition(doc)
        clock=Clock()
        r=TrialRunner(result_root=tmp_path/mode, software_revision="software-device-fixture", clock=clock)
        ticket=r.prepare(p, limits)
        r.start(ticket,input_provenance={"source":"software-device-fixture","hardware":False})
        clock.sleep(.02)
        ingest(r,ticket,clock,("left",),0)
        r.advance(ticket)
        trace=[]
        for tick in range(1,5):
            clock.sleep(p.dt_s)
            ingest(r,ticket,clock,("left",),tick)
            r.advance(ticket)
            sample=r.snapshot()
            task={k:v for k,v in r._execution.task_view.items() if k!="epoch"}
            trace.append((sample.qpos,sample.qvel,sample.metadata["scene_dynamics_v1"],task))
        traces.append(trace)
        assert comparison_metrics(tmp_path/mode/ticket.trial_id)["ticks"]==4
        r.retry()
        ticket=r.ticket
        r.start(ticket,input_provenance={"source":"software-disconnect-fixture"})
        clock.sleep(.02)
        # retryの旧sessionは拒否され、新しいsessionとneutralを要求する。
        with pytest.raises(ValueError,match="previous execution"):
            ingest(r,ticket,clock,("left",),5)
        assert r.result.to_document()["runner_stop_reason"]=="technical_invalid"
        r.close()
    assert traces[0]==traces[1]


def test_common_input_reaches_and_compresses_spring(tmp_path):
    traces=[]
    for mode in ("no_force_cue", "force_cue"):
        doc=preset_condition("tasks/research-spring", input_provider="gamepad/v1")
        doc["display_comparison"]["mode"]=mode
        doc["limits"]["max_ticks"]=180
        p,limits,_=resolve_condition(doc)
        clock=Clock()
        r=TrialRunner(result_root=tmp_path/mode,software_revision="software-push-fixture",clock=clock)
        ticket=r.prepare(p,limits)
        start(r,ticket,clock)
        peak=0.;contacts=0;trace=[]
        for tick in range(1,181):
            clock.sleep(p.dt_s)
            r.ingest(ticket,message(tick,(.7,0,0,0)),received_at_s=clock())
            r.advance(ticket)
            raw=r.snapshot().metadata["scene_dynamics_v1"]
            peak=max(peak,raw["springs"][0]["displacement_m"])
            contacts+=sum(c["status"]=="measured" for c in raw["contacts"])
            trace.append((raw,{k:v for k,v in r._execution.task_view.items() if k!="epoch"}))
            if r.status=="terminal":break
        assert r.result.to_document()["runner_stop_reason"]=="simulation_budget"
        assert peak>1e-4 and contacts>0
        metrics=comparison_metrics(tmp_path/mode/ticket.trial_id)
        assert metrics["peak_spring_displacement_m"]==peak and metrics["peak_contact_force_n"]>0
        traces.append(trace)
        r.close()
    assert traces[0]==traces[1]


def test_device_input_compresses_spring_identically_across_display_modes(tmp_path):
    from tests.runtime.test_device_force_integration import device_message
    traces=[]
    for mode in ("no_force_cue", "force_cue"):
        doc=preset_condition("tasks/research-spring", input_provider="selfrionette-device/v1")
        doc["display_comparison"]["mode"]=mode
        doc["limits"]["max_ticks"]=360
        p,limits,_=resolve_condition(doc)
        clock=Clock()
        r=TrialRunner(result_root=tmp_path/mode,software_revision="software-device-push-fixture",clock=clock)
        ticket=r.prepare(p,limits)
        r.start(ticket,input_provenance={"source":"software-device-negative-y-fixture","hardware":False})
        clock.sleep(.02)
        trace=[];peak=0.;contacts=0
        for tick in range(361):
            if tick:
                clock.sleep(p.dt_s)
            raw=device_message("left",tick)
            values=[0.,-.8 if tick else 0.,0.,0.,0.,0.,0.]
            raw["event"].update(command=values.copy(),normalized=values.copy(),
                active=[False,bool(tick),False,False,False,False,False])
            r.ingest(ticket,json.dumps(raw),received_at_s=clock())
            r.advance(ticket)
            sample=r.snapshot()
            dynamics=sample.metadata["scene_dynamics_v1"]
            peak=max(peak,dynamics["springs"][0]["displacement_m"])
            contacts+=sum(c["status"]=="measured" for c in dynamics["contacts"])
            trace.append((sample.qpos,sample.qvel,dynamics,
                None if r._execution.task_view is None else
                    {k:v for k,v in r._execution.task_view.items() if k!="epoch"}))
        assert r.result.to_document()["runner_stop_reason"]=="simulation_budget"
        assert peak>1e-4 and contacts>0
        metrics=comparison_metrics(tmp_path/mode/ticket.trial_id)
        assert metrics["peak_spring_displacement_m"]==peak and metrics["peak_contact_force_n"]>0
        traces.append(trace)
        r.close()
    assert traces[0]==traces[1]
