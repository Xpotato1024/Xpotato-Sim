"""native MuJoCo反復の参照数とmemoryをphase境界で測るsoftware validation。"""
from collections import Counter
import ctypes
import gc
import json
import os
from pathlib import Path
import tracemalloc
import weakref

import mujoco

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.composition.robot_model import RobotModelRegistration
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.experiment.trial_fixture import load_trial_fixture
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner


def process_memory():
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD)]+[
                (name,ctypes.c_size_t) for name in ("PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage",
                    "PagefileUsage","PeakPagefileUsage","PrivateUsage")]
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        psapi=ctypes.WinDLL("psapi",use_last_error=True)
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        counters=Counters()
        counters.cb=ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"rss_bytes":counters.WorkingSetSize,"private_bytes":counters.PrivateUsage}
    statm=Path("/proc/self/statm")
    if statm.is_file():
        return {"rss_bytes":int(statm.read_text().split()[1])*os.sysconf("SC_PAGE_SIZE"),"private_bytes":None}
    return {"rss_bytes":None,"private_bytes":None,"reason":"process memory API unavailable"}


def test_120_native_trials_keep_owners_and_workspaces_bounded(tmp_path,monkeypatch):
    construction={"factory":0,"native_model":0}
    original_build=RobotModelRegistration.build
    original_native=mujoco.MjModel.from_xml_string
    def build(self,scene_plan=None):
        construction["factory"]+=1
        return original_build(self,scene_plan)
    def native(*args,**kwargs):
        construction["native_model"]+=1
        return original_native(*args,**kwargs)
    monkeypatch.setattr(RobotModelRegistration,"build",build)
    monkeypatch.setattr(mujoco.MjModel,"from_xml_string",native)
    fixture=load_trial_fixture(Path(__file__).parents[1]/"fixtures/trial_gamepad/short-movement.json")
    profile=load_launch_profile("dynamic-cube-drop")
    runner=TrialRunner(result_root=tmp_path,software_revision="software-stress",clock=lambda:10.)
    phases=[]
    tracemalloc.start()
    def phase(name):
        gc.collect()
        counts=Counter(type(obj).__name__ for obj in gc.get_objects())
        current,peak=tracemalloc.get_traced_memory()
        entry={"phase":name,"tracemalloc_current":current,"tracemalloc_peak":peak,**process_memory(),
            "construction":dict(construction),"objects":{key:counts[key] for key in
                ("MjModel","MjData","ModelExecution","CoordinatedInputRuntime","ViewerInputSource",
                 "ViewerKeyboardGamepadMappingStrategy","ObservationState","TrialResult")}}
        if runner._execution is not None:
            provider=runner._execution.instance.provider
            entry.update(asset_count=len(runner._execution.instance.viewer.resources),
                asset_bytes=sum(len(v) for v in runner._execution.instance.viewer.resources.values()),
                pending_tickets=int(provider._pending is not None),
                retained_epochs=len(runner._execution.runtime.runtime._used_epochs),
                retained_source_epochs=len(runner._execution.runtime.runtime._retired_sources),
                input_slots=int(runner._execution.runtime.source.last_control_message is not None),
                native_model_slots=1,
                native_data_slots=len({id(provider._data),id(provider._base_data),
                    id(provider._planning_data),id(provider._candidate_data),
                    *(id(k.data) for k in provider._kinematics.values())}),
                mapping_type=type(runner._execution.runtime.mapping).__name__)
        phases.append(entry)
    phase("before_prepare")
    ticket=runner.prepare(profile,TrialLimits(2))
    provider=runner._execution.instance.provider
    stable_model=provider.model
    native_after_prepare=construction["native_model"]
    fixed_workspaces=(id(provider._base_data),id(provider._planning_data),
        tuple(id(k.data) for k in provider._kinematics.values()))
    initial=provider.trial_state()
    phase("after_prepare")
    previous_source=previous_mapping=None
    for attempt in range(120):
        if attempt:
            ticket=runner.retry()
            assert previous_source() is None and previous_mapping() is None
        assert provider.trial_state()==initial
        assert runner._execution.runtime.source.last_control_message is None
        assert provider.model is stable_model
        assert fixed_workspaces==(id(provider._base_data),id(provider._planning_data),
            tuple(id(k.data) for k in provider._kinematics.values()))
        runner.start(ticket,input_provenance=fixture.identity())
        runner.ingest(ticket,fixture.samples[0][1],received_at_s=10.)
        runner.advance(ticket)
        runner.ingest(ticket,fixture.samples[1][1],received_at_s=10.)
        runner.advance(ticket)
        runner.advance(ticket)
        assert runner.status=="terminal" and runner.tick_count==2
        assert provider._pending is None
        assert len(runner._execution.runtime.runtime._used_epochs)==1
        assert not runner._execution.runtime.runtime._retired_sources
        previous_source=weakref.ref(runner._execution.runtime.source)
        previous_mapping=weakref.ref(runner._execution.runtime.mapping)
        if attempt+1 in (20,40,80,120): phase(f"after_{attempt+1}")
    assert construction=={"factory":1,"native_model":native_after_prepare}
    assert len(list(tmp_path.iterdir()))==120
    assert all({p.name for p in directory.iterdir()}=={
        "condition.json","initial-state.json","start.json","final-state.json","terminal.json"}
        for directory in tmp_path.iterdir())
    warm=[p for p in phases if p["phase"] in {"after_20","after_40","after_80","after_120"}]
    for key in ("asset_count","asset_bytes","pending_tickets","retained_epochs","retained_source_epochs","input_slots",
                "native_model_slots","native_data_slots"):
        assert len({p[key] for p in warm})==1
    assert all(p["objects"]==warm[0]["objects"] for p in warm)
    # tracemallocはPython参照の補助測定。RSSの即時減少を要求しない。
    assert warm[-1]["tracemalloc_current"]-warm[0]["tracemalloc_current"] < 2_000_000
    provider_ref=weakref.ref(provider)
    runner.close()
    del provider,stable_model
    phase("after_close")
    assert provider_ref() is None and previous_source() is None and previous_mapping() is None
    print("TRIAL_RESOURCE_PHASES="+json.dumps(phases,sort_keys=True))
    tracemalloc.stop()
