"""同じlive publisherで全scene状態と動力学証拠が原子的に対応する。"""
from dataclasses import replace
import json
import pytest
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.runners import model_websocket_publisher as module
from tests.runtime.test_model_websocket_publisher import FakeServer, message


def run(monkeypatch, *, terminal=False, disconnect=False):
    p=replace(load_launch_profile("dynamic-cube-drop"),steps=15,interval_s=.0001)
    if terminal:
        params=json.loads(p.task_parameters_json);params["duration_s"]=.03;p=replace(p,task_parameters_json=json.dumps(params))
    FakeServer.instances.clear();FakeServer.incoming=[message((0,0,0,0),0),message((0,0,0,0),1)]
    if disconnect:FakeServer.incoming.extend([message((0,0,0,0),2,connected=False),message((0,0,0,0),3)])
    monkeypatch.setattr(module,"WebSocketPublisherServer",FakeServer)
    module.run_model_websocket_publisher(p,clock=lambda:1.)
    return [json.loads(v) for v in FakeServer.instances[-1].messages]


def test_publisher_has_full_scene_not_robot_only_qpos(monkeypatch):
    ps=run(monkeypatch)
    for p in ps:
        m=p["metadata"];d=m["scene_dynamics_v1"];g=m["scene_contact_geometry_v1"]
        assert len(p["qpos"])==15 and len(p["qvel"])==14
        assert m["robot_qpos_dimension"]==8
        assert d["frame_index"]==g["frame_index"]==p["frame_index"]
        assert d["simulation_time_s"]==g["simulation_time_s"]==p["time_s"]
        assert d["model_sha256"]==m["model_sha256"]
        assert m["physical_output"]=="disabled"
    assert ps[-1]["metadata"]["scene_dynamics_v1"]["objects"][0]["position_m"][2]<.5


@pytest.mark.parametrize("disconnect",[False,True])
def test_terminal_or_disconnect_freezes_cube_robot_and_clock(monkeypatch,disconnect):
    ps=run(monkeypatch,terminal=not disconnect,disconnect=disconnect)
    done=[p for p in ps if p["metadata"]["scene_contact_task_v1"]["phase"] in ("completed","aborted")]
    assert len(done)>1
    assert len({tuple(p["qpos"]) for p in done})==1 and len({tuple(p["qvel"]) for p in done})==1
    assert len({p["time_s"] for p in done})==1
    assert done[-1]["frame_index"]>done[0]["frame_index"]
