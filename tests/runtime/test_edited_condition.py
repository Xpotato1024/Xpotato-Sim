"""展開条件のstrict入口、編集/記録境界、同一native worldと反復を検査する。"""
from copy import deepcopy
import json
from pathlib import Path
import pytest
import gc
import weakref

from xpotato_sim.runtime.experiment.edited_condition import (
    preset_condition, resolve_condition, descriptors, condition_diff, condition_digest,
)
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.trial_fixture import load_trial_fixture
from xpotato_sim.runtime.runners.finite_trial import run_finite_trial
from xpotato_sim.runtime.application.workbench_control import WorkbenchControl
from xpotato_sim.runtime.scene.objects import canonical


def condition():
    d = preset_condition("dynamic-cube-drop")
    d["limits"]["max_ticks"] = 3
    return d


def test_roundtrip_clone_diff_and_profile_bytes_unchanged():
    d = condition()
    p, limits, normalized = resolve_condition(d)
    before = p.source_path.read_bytes()
    clone = resolve_condition(canonical(normalized))[2]
    clone["environment"]["parameters"]["scene"]["definitions"][0]["inertia"]["mass_kg"] = .25
    delta = condition_diff(normalized, clone)
    assert len(delta) == 1 and delta[0]["path"][-1] == "mass_kg"
    assert condition_digest(clone) != condition_digest(normalized)
    assert condition_digest(normalized) == condition_digest(d) and p.source_path.read_bytes() == before
    assert resolve_condition(canonical(clone))[2] == clone


@pytest.mark.parametrize("payload", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}',
    b'{"a":1e999}', b'[]', b'\xef\xbb\xbf{}', b'{'*60100], ids=["duplicate","nan","inf","overflow","array","bom","oversize"])
def test_strict_json(payload):
    with pytest.raises(ValueError):
        resolve_condition(payload)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(workspace="C:/private"),
    lambda d: d.update(evaluation={"name":"fake"}),
    lambda d: d["configuration"].update(web={"port":1}),
    lambda d: d["configuration"]["input"].update(path="C:/private"),
    lambda d: d["configuration"]["mapping"]["parameters"].update(code="import os"),
    lambda d: d["configuration"]["model"].update(name="../../evil.xml"),
    lambda d: d["environment"]["parameters"].update(xml="<mujoco/>"),
    lambda d: d["environment"].update(parameters=None),
    lambda d: d["environment"]["parameters"]["scene"]["definitions"][0]["inertia"].update(mass_kg=-1),
    lambda d: d["environment"]["parameters"]["scene"]["definitions"][0]["surface"].update(sliding_friction=-1),
    lambda d: d["environment"]["parameters"]["scene"]["objects"][0]["pose"].update(orientation_wxyz=[0,0,0,0]),
    lambda d: d["environment"]["parameters"]["scene"]["objects"].append(deepcopy(d["environment"]["parameters"]["scene"]["objects"][0])),
    lambda d: d["configuration"]["mapping"]["parameters"].update(gamepad_speed_m_s=True),
    lambda d: d["configuration"]["robot"].update(version=True),
    lambda d: d["configuration"]["execution"]["dynamics"].update(physics_dt_s=.003),
    lambda d: d["limits"].update(max_ticks=0),
    lambda d: d["limits"].update(wall_s=float("inf")),
])
def test_invalid_unknown_code_path_and_physics(mutate):
    d=condition(); mutate(d)
    with pytest.raises((ValueError, TypeError)):
        resolve_condition(d)


def test_descriptors_and_backend_ranges_agree():
    d=condition()
    for descriptor in descriptors(d):
        if not descriptor["available"] or descriptor["minimum"] is None:
            continue
        invalid=deepcopy(d); target=invalid
        for k in descriptor["path"][:-1]:
            target=target[k]
        target[descriptor["path"][-1]]=descriptor["minimum"]-1
        with pytest.raises(ValueError):
            resolve_condition(invalid)


@pytest.mark.parametrize("path", [("environment", "parameters"), ("environment", "parameters", "scene"),
    ("environment", "parameters", "scene", "objects"), ("environment", "parameters", "scene", "objects", 0),
    ("environment", "parameters", "scene", "definitions", 0), ("configuration", "model")])
@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_nested_import_types_are_typed_rejections(path, value):
    d=condition(); target=d
    for k in path[:-1]: target=target[k]
    target[path[-1]]=value
    c=WorkbenchControl([],"key");c.owner="owner"
    with pytest.raises(ValueError):
        c.command("owner",{"op":"import","capability":"key","revision":0,"ticket":None,
            "request_id":"import-bad","condition":canonical(d).decode()})
    assert c.command("owner",{"op":"status"})[0]["phase"]=="unselected"
    assert c.owner=="owner" and c.revision==0


def test_legacy_noop_parameters_disabled_and_motion_template_owned():
    from xpotato_sim.runtime.scene.objects import ObjectInstance
    d=condition(); metadata={tuple(x["path"]):x for x in descriptors(d)}
    for key in ("steps","interval_s","grace_period_s"):
        path=("configuration","execution",key)
        assert not metadata[path]["available"] and "TrialRunner" in metadata[path]["reason"]
        bad=deepcopy(d);bad["configuration"]["execution"][key]+=1
        with pytest.raises(ValueError):resolve_condition(bad)
    motion=next(x for x in metadata.values() if x["path"][-1]=="motion_type")
    assert motion["motion_templates"]==ObjectInstance.motion_templates()
    # Eulerは既存DynamicsSettings契約が明示対応している。
    d["configuration"]["execution"]["dynamics"]["integrator"]="Euler"
    assert resolve_condition(d)[2]["configuration"]["execution"]["dynamics"]["integrator"]=="Euler"


def test_gui_preset_clones_preserve_launcher_budgets_and_correlation():
    c=WorkbenchControl([],"key",launcher_limits={"ticks":5,"input_wait_s":7,"wall_s":8,"prepare_s":9});c.owner="owner"
    for preset in ("dynamic-cube-drop","dynamic-cube-push"):
        reply,_=c.command("owner",{"op":"clone","capability":"key","revision":c.revision,"ticket":None,
            "profile_id":preset,"request_id":"clone-request"})
        assert reply["request_id"]=="clone-request"
        assert reply["condition"]["limits"]=={"max_ticks":5,"input_wait_s":7.,"wall_s":8.,"prepare_s":9.}


def test_clone_path_is_rejected_before_filesystem_load(monkeypatch):
    def forbidden(*args):raise AssertionError("server filesystem must not be read")
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_control.load_launch_profile",forbidden)
    c=WorkbenchControl([],"key");c.owner="owner"
    with pytest.raises(ValueError,match="server path"):
        c.command("owner",{"op":"clone","capability":"key","revision":0,"ticket":None,
            "profile_id":"C:/private/profile.json"})


def test_accepted_condition_watchdog_matches_worker_limits_and_rejects_bad_requests():
    from xpotato_sim.runtime.application.workbench_service import prepare_watchdog_deadline
    d=condition();d["limits"]["prepare_s"]=17.
    c=WorkbenchControl([{"id":"dynamic-cube-drop","available":True}],"key");c.owner="owner"
    request={"op":"prepare","id":"budget","capability":"key","revision":0,"ticket":None,
        "profile_id":"dynamic-cube-drop","condition":d}
    bad=deepcopy(request);bad["condition"]["limits"]["prepare_s"]=-1
    with pytest.raises(ValueError):c.command("owner",bad)
    assert c.generation==0 and c.busy is None
    _,accepted=c.command("owner",request)
    limits=resolve_condition(accepted["condition"])[1]
    assert prepare_watchdog_deadline(accepted,{"prepare_s":.01},10)==10+limits.prepare_s+2
    with pytest.raises(ValueError):c.command("owner",{**request,"id":"stale"})
    assert prepare_watchdog_deadline({"op":"prepare"},{"prepare_s":9},10)==21


def test_browser_json_number_spelling_preserves_condition():
    d=condition()
    def javascript_numbers(value):
        if type(value) is float and value.is_integer(): return int(value)
        if type(value) is list: return [javascript_numbers(x) for x in value]
        if type(value) is dict: return {k:javascript_numbers(v) for k,v in value.items()}
        return value
    assert condition_digest(d)==condition_digest(javascript_numbers(d))
    a=resolve_condition(d)[0];b=resolve_condition(javascript_numbers(d))[0]
    assert a.task_parameters_json==b.task_parameters_json


@pytest.mark.parametrize("phase", ["waiting_input","running","finalizing","recording_failed","faulted"])
def test_backend_rejects_active_or_unfinalized_edit(phase):
    c=WorkbenchControl([],"key"); c.owner="owner"; c.state["phase"]=phase
    before=deepcopy(c.status())
    for op in ("clone","edit","import","export","diff"):
        r={"op":op,"capability":"key","revision":c.revision,"ticket":None}
        if op=="clone": r["profile_id"]="dynamic-cube-drop"
        if op in {"edit","diff"}: r["condition"]=condition()
        if op=="import": r["condition"]=canonical(condition()).decode()
        with pytest.raises(ValueError): c.command("owner",r)
    assert c.status()==before


def test_edit_revision_ticket_import_duplicates_and_immutable_applied():
    c=WorkbenchControl([],"key"); c.owner="owner"
    def request(op,**extra):
        return {"op":op,"capability":"key","revision":c.revision,"ticket":c.state["ticket"],**extra}
    value,_=c.command("owner",request("clone",profile_id="dynamic-cube-drop"))
    d=deepcopy(value["condition"]); c.state["applied_condition"]=deepcopy(d)
    d["limits"]["max_ticks"]=4
    c.command("owner",request("edit",condition=d))
    assert c.state["applied_condition"]["limits"]["max_ticks"] != 4
    with pytest.raises(ValueError): c.command("owner",{**request("edit",condition=d),"revision":0})
    with pytest.raises(ValueError): c.command("owner",{**request("edit",condition=d),"ticket":{"epoch":"old"}})
    with pytest.raises(ValueError): c.command("owner",request("import",condition='{"a":1,"a":2}'))
    with pytest.raises(ValueError): c.command("owner",request("import",condition="C:/private/condition.json"))
    exported,_=c.command("owner",request("export"))
    c.command("owner",request("import",condition=canonical(exported["condition"]).decode()))
    assert c.next_condition==exported["condition"]


def test_native_penetration_rejected_before_preview(tmp_path):
    d=condition(); d["environment"]["parameters"]["scene"]["objects"][0]["pose"]["position_m"][2]=-.1
    p,l,_=resolve_condition(d)
    r=TrialRunner(result_root=tmp_path,software_revision="test")
    try:
        with pytest.raises(ValueError,match="penetration"):
            r.prepare(p,l)
        assert r.status=="faulted"
    finally: r.close()


@pytest.mark.parametrize("model", ["single_original","single_left","single_right","bimanual"])
def test_model_selection_and_motion_native_world(tmp_path,model):
    d=condition(); descriptor=next(x for x in descriptors(d) if "model_bindings" in x)
    d["configuration"]["model"]["name"]=model
    d["configuration"]["coordination"]["side_to_endpoint"]=descriptor["model_bindings"][model]
    d["model_configuration_sha256"]=descriptor["model_configuration_digests"][model]
    obj=d["environment"]["parameters"]["scene"]["objects"][0]
    obj["motion_type"]="fixed"; del obj["initial_velocity"]
    p,l,_=resolve_condition(d); r=TrialRunner(result_root=tmp_path,software_revision="test")
    try:
        r.prepare(p,l)
        assert r.viewer_resources is not None and r.snapshot() is not None
        assert len(r.condition.to_document()["model_sha256"])==64
        assert r.condition.to_document()["scene"]["objects"][0]["motion_type"]=="fixed"
    finally: r.close()


def test_edited_condition_fixture_saved_result_and_parity(tmp_path):
    d=condition()
    d["configuration"]["mapping"]["parameters"]["gamepad_speed_m_s"] = .12
    definition=d["environment"]["parameters"]["scene"]["definitions"][0]
    definition["inertia"]["mass_kg"] = .3
    definition["geometry"]["half_extents_m"] = [.04,.04,.04]
    p,l,normalized=resolve_condition(d)
    fixture=load_trial_fixture(Path(__file__).parents[1]/"fixtures/trial_gamepad/short-movement.json")
    results=[]
    for label,doc in (("cli",canonical(normalized)),("gui",deepcopy(normalized))):
        profile,limits,_=resolve_condition(doc)
        result=run_finite_trial(profile,fixture=fixture,limits=limits,result_root=tmp_path/label,software_revision="test")
        assert result.to_document()["recording"]=="complete"
        results.append(next((tmp_path/label).glob("*/condition.json")).read_bytes())
    assert results[0]==results[1]


def test_repeated_parameter_rebuild_and_old_ticket_isolation(tmp_path):
    r=TrialRunner(result_root=tmp_path,software_revision="test"); old=[]; native=[]
    try:
        for i in range(12):
            d=condition(); d["environment"]["parameters"]["scene"]["definitions"][0]["inertia"]["mass_kg"]=.2+i*.01
            p,l,_=resolve_condition(d); ticket=r.prepare(p,l)
            assert r.model_build_count==i+1
            native.append(weakref.ref(r._execution.instance.provider.model))
            gc.collect()
            assert sum(ref() is not None for ref in native)==1
            for previous in old:
                with pytest.raises(ValueError): r.start(previous,input_provenance={"source":"test"})
            old.append(ticket)
            assert r.status=="ready"
        assert len({t.condition_sha256 for t in old})==12
    finally: r.close()
    gc.collect()
    assert all(ref() is None for ref in native)
