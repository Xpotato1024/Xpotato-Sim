"""ロボットなしSceneのprofile、数値計算、保存、resetの回帰。"""
from __future__ import annotations

from pathlib import Path
import csv
import json
import mujoco
import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile, decode_launch_profile
from xpotato_sim.runtime.composition.scene_only_profile import SceneOnlyProfile
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.scene.objects import canonical, decode_object_scene
from xpotato_sim.plugins.environments.object_scene_environment.implementation import ConfiguredObjectSceneProvider


def load(name):
    return load_launch_profile("examples/" + name)


@pytest.mark.parametrize("preset,flex_count", [("sphere_wall",0),("elastic_plastic_sheets",2)])
def test_registered_scene_model_and_schema(preset,flex_count):
    provider=ConfiguredObjectSceneProvider()
    scene=provider.resolve_parameters({"preset":preset})
    assert decode_object_scene(canonical(scene.to_document())).digest==scene.digest
    profile=load("scene-sphere-wall" if flex_count==0 else "scene-elastic-plastic")
    instance=profile.build_model()
    assert instance.provider.model.nflex==flex_count
    assert instance.provider.model.nq > 0
    assert profile.robot is None and profile.input_source is None
    assert profile.task_selection is None
    assert profile.scene_plan.manifest.digest==scene.digest
    assert instance.provider.numerical_condition()["mujoco_version"] == "4.0.0"


def test_bounded_stored_trial_and_retry(tmp_path):
    p=load("scene-sphere-wall")
    runner=TrialRunner(result_root=tmp_path,software_revision="fixture-generic-main")
    ticket=runner.prepare(p,TrialLimits(max_ticks=20))
    assert runner.viewer_resources is None
    runner.start(ticket,input_provenance={"source":"uncontrolled-scene","version":"v1"})
    assert runner.status=="running"
    states=[]
    for i in range(20):
        state=runner.snapshot()
        if state is not None:states.append(state.time_s)
        runner.advance(ticket)
    assert states==sorted(states)
    assert runner.status=="terminal" and runner.tick_count==20
    assert runner.result.to_document()["runner_stop_reason"]=="simulation_budget"
    directory=tmp_path/ticket.trial_id
    assert set(p.name for p in directory.iterdir()) == {
        "condition.json","initial-state.json","start.json","samples.jsonl","final-state.json","terminal.json"}
    condition=json.loads((directory/"condition.json").read_text(encoding="utf-8"))["condition"]
    assert condition["schema_version"]=="trial-condition/v2"
    assert condition["robot"] is None and condition["input"] is None
    raw=json.loads((directory/"final-state.json").read_text(encoding="utf-8"))
    assert raw["mujoco_version"]=="4.0.0" and raw["time_s"]==pytest.approx(.4)
    records=[json.loads(s) for s in (directory/"samples.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len([x for x in records if x["kind"]=="tick"])==20
    assert all("scene_state_checkpoint_v1" in x["signals"] for x in records if x["kind"]=="tick")
    old_state=raw["integration_state"]
    new=runner.retry()
    assert new.epoch!=ticket.epoch and runner.status=="ready"
    assert runner.snapshot().time_s == 0
    runner.close()
    assert len(old_state)>20


def test_speculative_flag_does_not_mutate_shared_scene():
    before=load("scene-sphere-wall").build_model().provider.numerical_condition()
    after=load("scene-sphere-wall-speculative").build_model().provider.numerical_condition()
    assert before["model_sha256"]!=after["model_sha256"]
    bit=int(mujoco.mjtEnableBit.mjENBL_SPECULATIVE)
    assert not before["physics"]["enableflags"] & bit
    assert after["physics"]["enableflags"] & bit
    off=load("scene-sphere-wall").build_model().provider
    on=load("scene-sphere-wall-speculative").build_model().provider
    assert off.data.qpos[0] == on.data.qpos[0] == pytest.approx(-.12)
    off.step(.02)
    on.step(.02)
    assert off.data.qpos[0] == pytest.approx(.12,abs=1e-7)
    assert on.data.qpos[0] == pytest.approx(-.0171,abs=1e-6)


@pytest.mark.parametrize("change",[
    lambda x:x.update(robot={"name":"fast_arm","version":1}),
    lambda x:x["environment"].update(robot_collision_profile="tool_sphere_10mm/v1"),
    lambda x:x.update(input={"plugin":{"name":"viewer","version":1}}),
    lambda x:x["execution"]["physics"].update(solver="incorrect"),
    lambda x:x["execution"]["physics"].update(speculative="yes"),
    lambda x:x["execution"]["physics"].update(physics_dt_s=0),
    lambda x:x["execution"].update(dt_s=.016),
    lambda x:x["environment"]["parameters"].update(preset="../escape"),
    lambda x:x.update(task={"plugin":{"name":"contact_observation_task","version":1}}),
])
def test_invalid_robotless_configuration_fails_before_build(change):
    p=load("scene-sphere-wall")
    raw=json.loads(p.document_json)
    change(raw)
    with pytest.raises((ValueError,TypeError,KeyError)):
        decode_launch_profile(canonical(raw),source_path=p.source_path)


def test_scene_only_input_rejection_does_not_fake_success(tmp_path):
    p=load("scene-sphere-wall")
    runner=TrialRunner(result_root=tmp_path,software_revision="reject-input")
    ticket=runner.prepare(p,TrialLimits(max_ticks=20))
    runner.start(ticket,input_provenance={"source":"passive"})
    with pytest.raises(ValueError,match="passive scene"):
        runner.ingest(ticket,"{}",received_at_s=runner._now())
    assert runner.status=="terminal"
    assert runner.result.to_document()["runner_stop_reason"]=="technical_invalid"
    runner.close()


def test_scene_only_saved_trial_reanalysis(tmp_path):
    from xpotato_sim.runtime.experiment.trial_analysis import load_validated_trial
    p=load("scene-sphere-wall")
    runner=TrialRunner(result_root=tmp_path,software_revision="analyze-passive")
    ticket=runner.prepare(p,TrialLimits(max_ticks=8))
    runner.start(ticket,input_provenance={"source":"passive"})
    for _ in range(8):runner.advance(ticket)
    trial=load_validated_trial(tmp_path/ticket.trial_id)
    assert trial.analysis()["metrics"]["ticks"]==8
    assert trial.analysis()["condition"]["schema_version"]=="trial-condition/v2"
    assert len(list(trial.iter_rows(kind="tick")))==8
    runner.close()

    from xpotato_sim.cli.main import main
    output = tmp_path / "public-analysis"
    exit_code = main(["experiment", "analyze",
                      "--result-root", str(tmp_path / ticket.trial_id),
                      "--output", str(output), "--no-plots"])
    report = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    assert report["verified_count"] == 1, report["invalid_or_incomplete"]
    assert exit_code == 0
    assert report["trials"][0]["condition"]["input"] is None
    assert report["trials"][0]["metrics"]["wall_elapsed_s"] >= 0
    with (output / "trials.csv").open(encoding="utf-8", newline="") as stream:
        row, = csv.DictReader(stream)
    assert row["integrity"] == "verified" and row["input_provider"] == ""


def test_flex_impact_sphere_is_registered_and_separate_from_fast_wall_ball():
    flex=load("scene-elastic-plastic").scene_plan.manifest
    wall=load("scene-sphere-wall").scene_plan.manifest
    assert len(flex.definitions)==1 and len(wall.definitions)==2
    assert flex.definitions[0].radius_m == pytest.approx(.03)
    assert flex.definitions[0].mass_kg == pytest.approx(.44)
    assert flex.definitions[0].diagonal_inertia_kg_m2[0] == pytest.approx(2*.44*.03**2/5)
    assert flex.objects[0].position_m[2] == pytest.approx(.46)
    sphere=next(d for d in wall.definitions if d.radius_m is not None)
    assert sphere.radius_m == pytest.approx(.01)
    assert sphere.mass_kg == pytest.approx(.05)


@pytest.mark.parametrize("flags", [[], ["--check"], ["--startup-check"]])
def test_scene_only_app_is_rejected_with_cli_guidance(capsys, flags):
    from xpotato_sim.cli.main import main

    exit_code = main(["app", "--profile", "examples/scene-sphere-wall", *flags])
    assert exit_code == 1
    assert "scene-only profiles are not supported by app; use xpotato-sim trial" in (
        capsys.readouterr().err
    )


def test_passive_video_includes_verified_precontact_frame(tmp_path):
    from xpotato_sim.runtime.experiment.scene_video_export import _state_frames
    from xpotato_sim.runtime.experiment.trial_analysis import load_validated_trial

    profile = load("scene-sphere-wall")
    runner = TrialRunner(result_root=tmp_path, software_revision="video-passive")
    try:
        ticket = runner.prepare(profile, TrialLimits(max_ticks=3))
        runner.start(ticket, input_provenance={"source": "passive"})
        for _ in range(3):
            runner.advance(ticket)
    finally:
        runner.close()
    directory = tmp_path / ticket.trial_id
    trial = load_validated_trial(directory)
    provider = profile.build_model().provider
    frames = _state_frames(trial, provider, provider.model, directory)
    assert [frame[0] for frame in frames] == pytest.approx([0, .02, .04, .06])
    assert [frame[2][0] for frame in frames[:2]] == pytest.approx([-.12, .12])

    initial = directory / "initial-state.json"
    initial.write_bytes(initial.read_bytes() + b" ")
    with pytest.raises(ValueError, match="artifact changed after validation: initial-state.json"):
        _state_frames(trial, provider, provider.model, directory)
