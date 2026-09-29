"""固定sceneの値契約・資源展開・profile互換性を検査する。"""
from dataclasses import replace
import json
import pytest
from xpotato_sim.runtime.scene.objects import (
    ObjectDefinition, ObjectInstance, ObjectSceneManifest, ContactParameters, decode_object_scene, canonical,
)
from xpotato_sim.plugins.environments.object_scene_environment.implementation import ConfiguredObjectSceneProvider
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile, decode_launch_profile


def scene():
    return ConfiguredObjectSceneProvider().resolve_parameters({"preset":"two_cubes"})


def test_definition_instance_separation_and_box_inertia():
    s=scene(); d=s.definitions[0]
    assert d.half_extents_m==(.05,.05,.05)
    assert d.diagonal_inertia_kg_m2==pytest.approx((.1*.1**2/6,)*3)
    assert len(s.objects)==2 and s.objects[0].definition_id==s.objects[1].definition_id
    assert "position_m" not in d.to_document()
    assert "mass_kg" not in s.objects[0].to_document()


def test_scene_canonical_order_and_round_trip():
    s=scene(); data=canonical(s.to_document())
    assert decode_object_scene(data)==s
    assert replace(s,objects=tuple(reversed(s.objects))).digest==s.digest
    assert ConfiguredObjectSceneProvider().resolve_parameters({"scene":json.loads(data)}).digest==s.digest


@pytest.mark.parametrize("mutate",[
    lambda r:r.update(unknown=True),
    lambda r:r["objects"].append(r["objects"][0]),
    lambda r:r["definitions"].append(r["definitions"][0]),
    lambda r:r["objects"][0]["definition"].update(name="unknown"),
    lambda r:r["objects"][0]["definition"].update(version=2),
    lambda r:r["objects"][0].update(instance_id="../cube"),
    lambda r:r["objects"][0].update(motion_type="dynamic"),
    lambda r:r["objects"][0]["pose"].update(frame="camera"),
    lambda r:r["objects"][0]["pose"].update(orientation_wxyz=[0,0,0,0]),
    lambda r:r["objects"][0]["pose"].update(position_m=[False,0,0]),
    lambda r:r["definitions"][0]["geometry"].update(half_extents_m=[-.05,.05,.05]),
    lambda r:r["definitions"][0]["inertia"].update(mass_kg=0),
    lambda r:r["definitions"][0]["surface"].update(sliding_friction=-1),
    lambda r:r["definitions"][0]["appearance"].update(rgba=[0,0,0,2]),
    lambda r:r["definitions"][0].update(provenance=""),
    lambda r:r["contact"].update(condim=2),
    lambda r:r["contact"].update(solref=[0,1]),
    lambda r:r["contact"].update(solimp=[.9,.95,.001,1,2]),
])
def test_invalid_scene_rejected(mutate):
    raw=json.loads(canonical(scene().to_document()));mutate(raw)
    with pytest.raises((ValueError,TypeError)):
        decode_object_scene(canonical(raw))


@pytest.mark.parametrize("document",[b'{"a":1,"a":2}',b'{"n":NaN}',b'{"n":Infinity}',b'\xef\xbb\xbf{}',b'{}'*200000,b'[]'],ids=['duplicate','nan','infinity','bom','over_budget','array'])
def test_invalid_json_is_not_coerced(document):
    with pytest.raises(ValueError): decode_object_scene(document)


@pytest.mark.parametrize("name",["../two_cubes","C:/secret","missing"])
def test_invalid_preset_names_do_not_escape_package(name):
    with pytest.raises(ValueError): ConfiguredObjectSceneProvider().resolve_parameters({"preset":name})


@pytest.mark.parametrize("suffix",["single","left","right","bimanual"])
def test_contact_profiles_keep_current_gamepad_mapping_and_explicit_roles(suffix):
    p=load_launch_profile("contact-debug-"+suffix);base=load_launch_profile("fast-arm-"+suffix+"-gamepad")
    assert p.model==base.model and p.mapping_parameters==base.mapping_parameters
    assert p.side_to_endpoint==base.side_to_endpoint
    assert p.scene_plan is not None and p.bind_scene_task() is not None
    assert p.to_dict()["resolved"]["physical_output"]=="disabled"
    assert p.to_dict()["resolved"]["scene_digest"]==p.scene_plan.manifest.digest


@pytest.mark.parametrize("change",[
    lambda r:r["environment"]["plugin"].update(name="missing"),
    lambda r:r["environment"]["plugin"].update(name="free_space_environment"),
    lambda r:r["task"]["plugin"].update(name="missing"),
    lambda r:r["task"]["parameters"].update(target_object_ids=["missing"]),
    lambda r:r["task"]["parameters"].update(target_object_ids=["cube_left","cube_left"]),
    lambda r:r["task"]["parameters"].update(duration_s=0),
    lambda r:r["task"]["parameters"].update(unknown=True),
])
def test_incompatible_profile_rejected_before_build(change):
    p=load_launch_profile("contact-debug-bimanual");r=json.loads(p.document_json);change(r)
    with pytest.raises((ValueError,TypeError,KeyError)):
        decode_launch_profile(canonical(r),source_path=p.source_path)


def test_shape_pose_contact_changes_change_scene_identity():
    s=scene()
    assert replace(s,objects=(replace(s.objects[0],position_m=(.2,.4,.5)),s.objects[1])).digest!=s.digest
    assert replace(s,definitions=(replace(s.definitions[0],mass_kg=.2),)).digest!=s.digest
    assert replace(s,contact=replace(s.contact,margin_m=.004)).digest!=s.digest
