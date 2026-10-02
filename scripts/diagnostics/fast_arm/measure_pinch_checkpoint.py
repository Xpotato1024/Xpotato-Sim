"""100mm cube反対面の独立tick比較。実操作軌跡やCI速度gateではない。"""
from pathlib import Path
import sys,json,time
root=Path(sys.argv[1]);output=Path(sys.argv[2]);ev=Path(__file__).parent
sys.path[:0]=[str(root/'src'),str(root/'src/xpotato_sim/plugins/robots/fast_arm/core/src')]
import mujoco,numpy as np
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.schemas.coordinated import EndpointVelocity
p=load_launch_profile('dynamic-cube-drop');r=p.build_model().provider;m=r.model
checkpoint=json.loads((ev/'pinch-checkpoint.json').read_text());speed=float(sys.argv[3]) if len(sys.argv)>3 else 0.;commands=tuple(EndpointVelocity(a,(0.,-speed if a=='left' else speed,0.),'world') for a in r.endpoint_ids)
rows=[];contacts=[];final=None
for i in range(220):
    d=r._data;mujoco.mj_resetData(m,d);d.qpos[:]=checkpoint['qpos'];d.ctrl[:]=checkpoint['ctrl'];mujoco.mj_forward(m,d)
    r._snapshot_cache=None
    before=time.perf_counter();candidate=r.prepare(commands,p.dt_s);prepared=time.perf_counter();r.commit(candidate);committed=time.perf_counter();sample=r.sample(frame_index=i,metadata={});sampled=time.perf_counter()
    if i>=20:rows.append([(prepared-before)*1000,(committed-prepared)*1000,(sampled-committed)*1000,(sampled-before)*1000]);contacts.append(int(r._data.ncon))
    final={'qpos':sample.state.qpos,'qvel':sample.state.qvel,'time':sample.state.time_s,'contacts':sample.dynamics['contacts'],'geometry':sample.geometry.to_document() if hasattr(sample.geometry,'to_document') else __import__('dataclasses').asdict(sample.geometry),'dynamics':sample.dynamics,'integration':r.trial_state(), 'native_arrays':{name:getattr(r._data,name).tolist() for name in ('qacc','qacc_warmstart','actuator_force','efc_force')}}
a=np.array(rows)
report={'root':str(root),'method':'200 independent ticks from same controlled native two-sided 100mm cube checkpoint after 20 warmups; not user trial, not end-to-end latency','mujoco':mujoco.__version__,'inward_velocity_m_s':speed,'stages':['prepare_ms','commit_ms','sample_ms','total_ms'],'median':np.median(a,axis=0).tolist(),'p95':np.percentile(a,95,axis=0).tolist(),'max':np.max(a,axis=0).tolist(),'contact_range':[min(contacts),max(contacts)],'final':final}
output.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='final'},indent=2))
