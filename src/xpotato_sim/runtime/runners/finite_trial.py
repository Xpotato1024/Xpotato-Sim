"""明示fixtureを実時間で共通TrialRunnerへ渡すheadless入口。"""
from time import monotonic, sleep

from xpotato_sim.runtime.experiment.trial_runner import TrialRunner


def run_finite_trial(profile, *, fixture, limits, result_root, software_revision,
                     clock=monotonic, sleeper=sleep):
    """physics/Taskを再実装せず、有限記録sampleを一度ずつdispatchする。"""
    from xpotato_sim.runtime.composition.scene_only_profile import SceneOnlyProfile
    passive = type(profile) is SceneOnlyProfile
    if passive:
        if fixture is not None:
            raise ValueError("scene-only trial cannot use Robot input fixture")
    elif fixture is None:
        raise ValueError("Robot trial requires a fixture")
    else:
        fixture.assert_profile(profile)
    runner = TrialRunner(result_root=result_root, software_revision=software_revision, clock=clock)
    try:
        ticket = runner.prepare(profile, limits)
        runner.start(ticket, input_provenance=(
            {"source": "passive_scene/v1", "scene_digest": profile.scene_plan.manifest.digest}
            if passive else fixture.identity()))
        started = clock()
        index = 0
        deadline = started
        while runner.status in {"waiting_input", "running"}:
            now = clock()
            while not passive and index < len(fixture.samples) and started + fixture.samples[index][0] <= now:
                offset, message = fixture.samples[index]
                runner.ingest(ticket, message, received_at_s=started + offset)
                index += 1
            runner.advance(ticket)
            if runner.status in {"waiting_input", "running"}:
                deadline += profile.dt_s
                sleeper(max(0.0, deadline - clock()))
        return runner.result
    finally:
        runner.close()
