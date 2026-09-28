"""単腕/双腕の同列モデル選択と、既存v1 profileの明示互換境界。"""
import json
from dataclasses import replace
from urllib.parse import parse_qs, urlparse
import pytest
from xpotato_sim.runtime.composition.launch_profile import (
    LaunchProfile, load_launch_profile, decode_launch_profile, override_launch_profile,
)
from xpotato_sim.runtime.runners.application import list_application_profiles, application_url


@pytest.mark.parametrize("name,model,endpoints", [
    ("fast-arm-single-gamepad","single_original",("arm",)),
    ("fast-arm-left-gamepad","single_left",("left",)),
    ("fast-arm-right-gamepad","single_right",("right",)),
    ("fast-arm-bimanual-gamepad","bimanual",("left","right")),
])
def test_peer_model_profiles_share_loader_and_endpoint_contract(name,model,endpoints):
    p=load_launch_profile(name)
    assert type(p) is LaunchProfile
    assert p.model.plugin_id==model
    assert p.model_registration().endpoint_ids==endpoints
    assert set(p.side_to_endpoint.values())==set(endpoints)
    assert p.name in list_application_profiles()
    assert parse_qs(urlparse(application_url(p)).query)["inputStartup"]==["scene"]
    resolved = p.to_dict()["resolved"]
    assert resolved["physical_output"]=="disabled"
    control = resolved["mapping_parameters"]["gamepad_trigger_control"]
    assert control["left"]["axes"] == [1, 0]
    assert control["left"]["signs"] == [-1, -1]
    assert control["right"]["axes"] == [3, 2]
    assert control["right"]["signs"] == [-1, -1]


def test_old_single_profile_configuration_and_digest_are_not_rewritten():
    p=load_launch_profile("sim-gamepad")
    assert p.model is None
    assert "inputStartup" not in parse_qs(urlparse(application_url(p)).query)
    assert "model" not in json.loads(p.document_json)
    assert type(p) is LaunchProfile


def test_override_changes_only_web_fields():
    p=load_launch_profile("fast-arm-bimanual-gamepad")
    new=override_launch_profile(p,web_port=5198,backend_port=8798,open_browser=False)
    assert (new.web_port,new.backend_port,new.open_browser)==(5198,8798,False)
    assert new.model==p.model and new.coordination_json==p.coordination_json
    assert new.mapping_parameters==p.mapping_parameters


@pytest.mark.parametrize("mutation", [
    lambda x:x.update(model={"name":"missing","version":1}),
    lambda x:x["model"].update(version=999),
    lambda x:x["coordination"].update(side_to_endpoint={"left":"left","right":"left"}),
    lambda x:x["coordination"].update(side_to_endpoint={"left":"left"}),
    lambda x:x["coordination"].update(side_to_endpoint={"bogus":"left","right":"right"}),
    lambda x:x["coordination"].update(max_input_age_s=0),
    lambda x:x["coordination"].update(epoch=""),
    lambda x:x["web"].update(host="192.0.2.1"),
    lambda x:x.update(unknown=True),
    lambda x:x.update(assembly=[]),
])
def test_bad_models_bindings_and_fields_fail_before_runtime(mutation):
    p=load_launch_profile("fast-arm-bimanual-gamepad");raw=json.loads(p.document_json)
    mutation(raw)
    with pytest.raises((ValueError,TypeError)):
        decode_launch_profile(json.dumps(raw).encode(),source_path=p.source_path)


def test_side_rebinding_changes_input_not_model():
    p=load_launch_profile("fast-arm-bimanual-gamepad");raw=json.loads(p.document_json)
    raw["coordination"]["side_to_endpoint"]={"left":"right","right":"left"}
    rebound=decode_launch_profile(json.dumps(raw).encode(),source_path=p.source_path)
    assert rebound.model_registration() is p.model_registration()
    assert rebound.model_registration().configuration_sha256==p.model_registration().configuration_sha256
    assert rebound.configuration_sha256!=p.configuration_sha256


def test_draft_specific_old_dual_schema_is_not_silently_migrated():
    p=load_launch_profile("fast-arm-bimanual-gamepad");raw=json.loads(p.document_json)
    raw["schema_version"]="fast-arm-coordinated-viewer-profile/v1"
    with pytest.raises(ValueError,match="schema_version"):
        decode_launch_profile(json.dumps(raw).encode(),source_path=p.source_path)
