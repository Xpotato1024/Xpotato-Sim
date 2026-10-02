"""同じnativeモデルで段階を測る。source rootは引数で固定する。"""
import argparse, cProfile, io, json, os, pstats, statistics, time
from pathlib import Path
from collections import defaultdict
from dataclasses import replace
import mujoco
import numpy as np
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.schemas.coordinated import EndpointVelocity
from xpotato_sim.transport import mujoco_state_to_payload

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--ticks', type=int, default=120)
parser.add_argument('--velocity', type=float, default=0.)
parser.add_argument('--no-profile', action='store_true')
parser.add_argument('--plain', action='store_true')
args = parser.parse_args()
p = load_launch_profile('dynamic-fixed-contact')
# 同一cubeで両側接触を可能にする診断fixture。サイズ以外はpresetを維持する。
scene = p.scene_plan.manifest
definition = replace(scene.definitions[0], half_extents_m=(.05, .60, .05))
obj = replace(scene.objects[0], position_m=(.36, 0., .46))
p = replace(p, scene_plan=replace(p.scene_plan, manifest=replace(scene, definitions=(definition,), objects=(obj,))))
r = p.build_model().provider
report = {'mujoco_version': mujoco.__version__, 'source_root': os.environ['PYTHONPATH'].split(';')[0],
          'model_sha256': r.built.model_sha256, 'condition': r.numerical_condition(),
          'scene': r.scene_manifest.to_document(), 'ticks': args.ticks, 'scenarios': {}}
timings = defaultdict(list)
originals = {}
def instrument(owner, name, label):
    original = getattr(owner, name)
    originals[(owner, name)] = original
    def measured(*a, **kw):
        begin = time.perf_counter_ns()
        try: return original(*a, **kw)
        finally: timings[label].append((time.perf_counter_ns()-begin)/1e6)
    setattr(owner, name, measured)
if not args.plain:
    for name in ('mj_copyData', 'mj_step', 'mj_forward', 'mj_kinematics'):
        instrument(mujoco, name, name)
    for name in ('_arm_candidate', '_planning_state', '_integrate_candidate', 'sample', 'snapshot', 'prepare', 'commit', '_dynamics_observation'):
        instrument(r, name, name)

def commands(left, right):
    return tuple(EndpointVelocity(a, (left if a == 'left' else right, 0., 0.), 'world') for a in r.endpoint_ids)

for name, warm in [('free_space', 0), ('one_contact', 100), ('two_contact', 100)]:
    r.reset()
    # Servo targetsを徐々に近づける。qpos/solver/gainを直接変えない。
    for _ in range(warm):
        r.commit(r.prepare(commands(.06, .06 if name == 'two_contact' else 0.), p.dt_s))
    timings.clear()
    contacts, solvers, totals, states = [], [], [], []
    profiler = cProfile.Profile()
    if not args.no_profile: profiler.enable()
    for tick in range(args.ticks):
        begin = time.perf_counter_ns()
        r.snapshot()
        r.commit(r.prepare(commands(args.velocity, args.velocity), p.dt_s))
        sample = r.sample(frame_index=tick, metadata={})
        b = time.perf_counter_ns()
        json.dumps(mujoco_state_to_payload(sample.state), allow_nan=False)
        timings['serialization'].append((time.perf_counter_ns()-b)/1e6)
        totals.append((time.perf_counter_ns()-begin)/1e6)
        contacts.append(sorted({c.endpoint_id for c in sample.geometry.contacts}))
        solvers.append(r._data.solver_niter.tolist())
        states.append({'qpos': sample.state.qpos, 'qvel': sample.state.qvel,
                       'geometry': sample.geometry.to_document(), 'dynamics': sample.dynamics})
    if not args.no_profile: profiler.disable()
    if not args.no_profile:
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(35)
        args.output.with_name(args.output.stem+'-'+name+'-profile.txt').write_text(stream.getvalue(), encoding='utf-8')
    def stats(values):
        ordered = sorted(values)
        return {'count': len(values), 'mean_ms': statistics.mean(values), 'p50_ms': statistics.median(values),
                'p95_ms': ordered[int((len(ordered)-1)*.95)], 'max_ms': max(values), 'total_ms': sum(values)}
    report['scenarios'][name] = {'tick': stats(totals), 'stages': {k: stats(v) for k,v in timings.items()},
                               'contact_endpoint_sets': sorted({tuple(c) for c in contacts}),
                               'solver_niter_max': np.max(solvers, axis=0).tolist(), 'states': states,
                               'integration_state': r.trial_state()}
    print(name, report['scenarios'][name]['tick'], report['scenarios'][name]['contact_endpoint_sets'], flush=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
