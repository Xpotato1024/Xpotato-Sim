"""接触を成功にしない有限観測Taskのstatus・対象・時刻境界。"""
from dataclasses import replace
import pytest
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.scene.task import GeometryTaskObservation
from xpotato_sim.runtime.scene.observation import SceneContact
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification as C


def setup():
    p=load_launch_profile("contact-debug-bimanual");b=p.bind_scene_task();v=p.build_model()
    return b,v.provider.sample(frame_index=0,metadata={}).geometry


def test_no_contact_can_complete_observation_but_not_contact_success():
    b,g=setup();state=b.initial_state()
    first=b.advance(state,GeometryTaskObservation(g))
    assert first.classification is C.RUNNING and first.state.observed_pairs==()
    done=b.advance(first.state,GeometryTaskObservation(replace(g,frame_index=1,simulation_time_s=b.duration_s)))
    assert done.classification is C.SUCCESS and done.state.phase=="completed"
    assert "not contact success" in done.state.reason and done.state.observed_pairs==()
    with pytest.raises(ValueError,match="terminal"):
        b.advance(done.state,GeometryTaskObservation(g))


def test_target_selection_does_not_change_scene_or_consider_near_success():
    b,g=setup();b=replace(b,targets=("cube_left",))
    def contact(oid,d):
        return SceneContact("left",oid,"tool","cube",(0,0,0),(1,0,0),d,max(0,-d),"near" if d>0 else "penetrating")
    obs=replace(g,contacts=(contact("cube_left",.001),contact("cube_right",-.001)))
    state=b.advance(b.initial_state(),GeometryTaskObservation(obs)).state
    assert state.observed_pairs==()
    obs=replace(obs,frame_index=1,contacts=(contact("cube_left",-.001),))
    state=b.advance(state,GeometryTaskObservation(obs)).state
    assert state.observed_pairs==(("left","cube_left"),) and state.classification is C.RUNNING


@pytest.mark.parametrize("mutation",[
    {"scene_digest":"f"*64}, {"objects":()},
])
def test_wrong_scene_is_technical_invalid(mutation):
    b,g=setup();transition=b.advance(b.initial_state(),GeometryTaskObservation(replace(g,**mutation)))
    assert transition.classification is C.TECHNICAL_INVALID


def test_stale_frame_and_reverse_time_rejected():
    b,g=setup();s=b.advance(b.initial_state(),GeometryTaskObservation(g)).state
    assert b.advance(s,GeometryTaskObservation(g)).classification is C.TECHNICAL_INVALID
    s=replace(s,time_s=1.)
    assert b.advance(s,GeometryTaskObservation(replace(g,frame_index=1))).classification is C.TECHNICAL_INVALID


def test_budget_exhaustion_is_not_success_and_stop_wins_over_timeout():
    b,g=setup()
    assert b.advance(b.initial_state(),GeometryTaskObservation(g,budget_exhausted=True)).state.reason=="execution_budget_exhausted"
    result=b.advance(b.initial_state(),GeometryTaskObservation(replace(g,simulation_time_s=b.duration_s),"disconnected"))
    assert result.classification is C.FAILURE and result.state.reason=="disconnected"


def test_fresh_task_has_no_prior_pairs_or_clock():
    b,g=setup();s=b.advance(b.initial_state(),GeometryTaskObservation(g)).state
    fresh=b.initial_state()
    assert fresh.frame_index==-1 and fresh.time_s==0 and fresh.observed_pairs==()
    assert b.advance(fresh,GeometryTaskObservation(g)).state==s
