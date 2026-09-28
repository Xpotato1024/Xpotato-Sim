"""標準publisherへの幾何診断統合。socketをfakeへ置き換えた有限入力。"""
from dataclasses import replace
import json
import pytest
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.runners import model_websocket_publisher as module
from tests.runtime.test_model_websocket_publisher import FakeServer, message


@pytest.mark.parametrize("suffix,count",[("single",4),("left",4),("right",4),("bimanual",8)])
def test_scene_task_and_robot_use_same_published_frame(monkeypatch,suffix,count):
    p=replace(load_launch_profile("contact-debug-"+suffix),steps=5,interval_s=.0001)
    FakeServer.instances.clear();FakeServer.incoming=[message((0,0,0,0),0),message((0,-.6,0,-.6),1)]
    monkeypatch.setattr(module,"WebSocketPublisherServer",FakeServer)
    module.run_model_websocket_publisher(p,clock=lambda:1.)
    payloads=[json.loads(v) for v in FakeServer.instances[-1].messages]
    assert payloads and all(len(v["qpos"])==count for v in payloads)
    for v in payloads:
        m=v["metadata"];g=m["scene_contact_geometry_v1"];t=m["scene_contact_task_v1"];b=m["scene_contact_binding_v1"]
        assert g["frame_index"]==v["frame_index"]==t["presentation_frame_index"]
        assert g["simulation_time_s"]==v["time_s"]==t["presentation_time_s"]
        assert b["scene_digest"]==g["scene_digest"]==t["scene_digest"]
        assert b["model_sha256"]==g["model_sha256"]==m["model_sha256"]
        assert m["physical_output"]=="disabled" and g["force_n"] is None
        assert "gamepad_trigger_control_v1" in m
    last=payloads[-1]["metadata"]
    assert last["scene_contact_task_v1"]["phase"]=="aborted"
    assert last["scene_contact_task_v1"]["reason"]=="execution_budget_exhausted"


def test_terminal_freezes_pose_but_keeps_display_frames_consistent(monkeypatch):
    p=replace(load_launch_profile("contact-debug-bimanual"),steps=8,interval_s=.0001)
    params=json.loads(p.task_parameters_json);params["duration_s"]=.02;p=replace(p,task_parameters_json=json.dumps(params))
    FakeServer.instances.clear();FakeServer.incoming=[message((0,0,0,0),0),message((0,-.6,0,-.6),1)]
    monkeypatch.setattr(module,"WebSocketPublisherServer",FakeServer)
    module.run_model_websocket_publisher(p,clock=lambda:1.)
    ps=[json.loads(v) for v in FakeServer.instances[-1].messages]
    done=[p for p in ps if p["metadata"]["scene_contact_task_v1"]["phase"]=="completed"]
    assert len(done)>=2
    assert len({tuple(p["qpos"]) for p in done})==1 and len({p["time_s"] for p in done})==1
    assert done[-1]["frame_index"]>done[0]["frame_index"]
    for p in done:
        assert p["metadata"]["scene_contact_task_v1"]["presentation_frame_index"]==p["frame_index"]


def test_disconnection_aborts_without_auto_restart(monkeypatch):
    p=replace(load_launch_profile("contact-debug-bimanual"),steps=7,interval_s=.0001)
    FakeServer.instances.clear();FakeServer.incoming=[message((0,0,0,0),0),message((0,-.6,0,-.6),1),message((0,0,0,0),2,connected=False),message((0,0,0,0),3)]
    monkeypatch.setattr(module,"WebSocketPublisherServer",FakeServer)
    module.run_model_websocket_publisher(p,clock=lambda:1.)
    ps=[json.loads(v) for v in FakeServer.instances[-1].messages]
    stopped=[p for p in ps if p["metadata"]["scene_contact_task_v1"]["phase"]=="aborted"]
    assert stopped and len({tuple(p["qpos"]) for p in stopped})==1


@pytest.mark.parametrize("suffix,axes",[
    ("single",(.85,0,0,0)),("left",(0,-.85,0,0)),
    ("right",(0,0,0,-.85)),("bimanual",(0,-.85,0,-.85))])
def test_shipped_scene_is_reachable_from_actual_home_with_production_mapping(monkeypatch,suffix,axes):
    p=replace(load_launch_profile("contact-debug-"+suffix),steps=100,interval_s=.0001)
    FakeServer.instances.clear();FakeServer.incoming=[message((0,0,0,0),0),message(axes,1)]
    monkeypatch.setattr(module,"WebSocketPublisherServer",FakeServer)
    module.run_model_websocket_publisher(p,clock=lambda:1.)
    ps=[json.loads(v) for v in FakeServer.instances[-1].messages]
    touched={c["object_id"] for v in ps for c in v["metadata"]["scene_contact_geometry_v1"]["contacts"] if c["distance_m"]<=0}
    assert touched==set(json.loads(p.task_parameters_json)["target_object_ids"])
