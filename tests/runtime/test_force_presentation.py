"""提示切替を物理・入力・Taskから分離し、条件と固定buildの出自を保存する。"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition, descriptors
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits, semantic_parameters
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.scene.force_presentation import default_presentation, profile_presentation, viewer_identity
from xpotato_sim.runtime.scene.objects import canonical
from tests.runtime.test_trial_runner import Clock, start, message


def selected(mode="no_force_cue"):
    document = preset_condition("tasks/dynamic-cube-drop")
    document["display_comparison"]["mode"] = mode
    document["limits"]["max_ticks"] = 20
    return resolve_condition(document)


def test_defaults_roundtrip_legacy_and_descriptor():
    p, _, d = selected()
    assert profile_presentation(p) == default_presentation()
    assert resolve_condition(canonical(d))[2] == d
    mode = next(v for v in descriptors(d) if v["path"] == ["display_comparison", "mode"])
    assert mode["available"] and mode["choices"] == ["no_force_cue", "force_cue"]
    del d["display_comparison"]
    old, _, normalized = resolve_condition(d)
    assert profile_presentation(old) is None and normalized["display_comparison"] is None
    assert preset_condition("diagnostics/contact-debug-left")["display_comparison"] is None


@pytest.mark.parametrize("change", [
    {"mode": "adaptive"}, {"scale_m_per_n": 0}, {"scale_m_per_n": True},
    {"scale_m_per_n": float("nan")}, {"normal_load_full_scale_n": -1},
    {"max_arrow_m": 1.1}, {"max_sample_age_s": .201}, {"unknown": 1},
])
def test_invalid_display_fails_closed(change):
    _, _, d = selected()
    d["display_comparison"].update(change)
    with pytest.raises(ValueError):
        resolve_condition(d)


def test_kinematic_cannot_claim_solver_force_comparison():
    p = replace(load_launch_profile("diagnostics/contact-debug-left"), display_comparison_json=canonical(default_presentation()).decode())
    with pytest.raises(ValueError, match="dynamic scene"):
        profile_presentation(p)


@pytest.mark.parametrize("bad", [
    {"mode": "production", "source_sha256": None, "build_sha256": None},
    {"mode": "headless", "source_sha256": "a" * 64, "build_sha256": "b" * 64},
    {"mode": "unknown", "source_sha256": None, "build_sha256": None},
    {"mode": "development"},
])
def test_viewer_identity_never_marks_unverified_build_as_production(bad):
    with pytest.raises(ValueError):
        viewer_identity(bad)


def test_condition_changes_only_display_and_reuses_native_model(tmp_path):
    a, limits, _ = selected()
    b, _, _ = selected("force_cue")
    x, y = semantic_parameters(a, limits), semantic_parameters(b, limits)
    assert x.pop("display_comparison") != y.pop("display_comparison") and x == y
    runner = TrialRunner(result_root=tmp_path, software_revision="test", clock=Clock())
    first = runner.prepare(a, limits)
    model = runner._execution.instance.provider.model
    initial = runner._execution.instance.provider.trial_state()
    second = runner.prepare(b, limits)
    assert first.condition_sha256 != second.condition_sha256 and first.epoch != second.epoch
    assert runner._execution.instance.provider.model is model and runner.model_build_count == 1
    assert runner._execution.instance.provider.trial_state() == initial
    binding = runner.snapshot().metadata["force_presentation_v1"]
    assert binding["epoch"] == second.epoch and binding["condition"]["mode"] == "force_cue"
    assert binding["condition_sha256"] == sha256(canonical(binding["condition"])).hexdigest()
    binding["condition"]["mode"] = "no_force_cue"
    assert runner.snapshot().metadata["force_presentation_v1"]["condition"]["mode"] == "force_cue"
    runner.close()


def test_started_trial_freezes_display_build_and_rejects_active_changes(tmp_path):
    p, limits, _ = selected("force_cue")
    build = {"mode": "production", "source_sha256": "a" * 64, "build_sha256": "b" * 64}
    clock = Clock()
    runner = TrialRunner(result_root=tmp_path, software_revision="test", clock=clock, viewer_build_identity=build)
    build["source_sha256"] = "c" * 64
    ticket = runner.prepare(p, limits)
    start(runner, ticket, clock)
    with pytest.raises(RuntimeError, match="inactive"):
        runner.prepare(selected()[0], limits)
    document = json.loads((tmp_path / ticket.trial_id / "condition.json").read_bytes())
    saved = document["condition"]["display_comparison"]
    assert saved["presentation"]["mode"] == "force_cue" and saved["viewer"]["source_sha256"] == "a" * 64
    assert document["condition_sha256"] == ticket.condition_sha256
    runner.abort(ticket)
    assert runner.snapshot().metadata["scene_force_signal_v1"]["status"] == "unavailable"
    runner.close()


def test_same_fixture_produces_identical_native_trajectory_raw_force_and_task(tmp_path):
    traces = []
    conditions = []
    for mode in ("no_force_cue", "force_cue"):
        p, limits, _ = selected(mode)
        clock = Clock()
        runner = TrialRunner(result_root=tmp_path / mode, software_revision="test", clock=clock)
        ticket = runner.prepare(p, limits)
        conditions.append(runner.condition.to_document())
        start(runner, ticket, clock)
        trace = []
        for tick in range(limits.max_ticks):
            runner.ingest(ticket, message(tick + 1, (.3, 0, -.3, 0)), received_at_s=clock())
            runner.advance(ticket)
            sample = runner.snapshot()
            task = deepcopy(sample.metadata["scene_contact_task_v1"])
            task.pop("epoch")
            trace.append((sample.qpos, sample.qvel, sample.time_s, sample.metadata["scene_dynamics_v1"], task))
        traces.append(trace)
        runner.close()
    for condition in conditions:
        condition.pop("display_comparison")
    assert conditions[0] == conditions[1] and traces[0] == traces[1]
