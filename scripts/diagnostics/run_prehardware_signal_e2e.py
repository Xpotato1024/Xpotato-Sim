"""実機接続を持たない信号/contact統合検証のcommand-line入口。"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from xpotato_sim.runtime.runners.signal_contact import capture_signal_contact, canonical, validate_scenario
from xpotato_sim.runtime.runners.signal_contact_artifact import decode_signal_trace

ROOT=Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='Synthetic input -> MuJoCo contact -> no-I/O wire evidence')
    parser.add_argument('--fixture',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--software-revision',required=True)
    args=parser.parse_args(argv)
    head=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=no'],text=True).strip()
    if args.software_revision!=head or dirty:
        parser.error('source revision must match a clean tracked checkout')
    scenario=validate_scenario(args.fixture.read_bytes())
    if args.output_dir.exists():
        parser.error('output directory already exists; existing evidence is not overwritten')
    digest=sha256(canonical(scenario)).hexdigest()
    first=capture_signal_contact(scenario,software_revision=head)
    second=capture_signal_contact(scenario,software_revision=head)
    if first!=second:
        raise ValueError('same-condition execution did not regenerate identical bytes')
    payload=decode_signal_trace(first,expected_revision=head,expected_scenario_sha256=digest)
    files={'signal-trace.json':first,'contact-task.jsonl':payload['contact_log'].encode('utf-8'),
           'final-payload.json':canonical(payload['final_payload'])+b'\n'}
    summary={'schema_version':'prehardware-signal-run/v1','software_revision':head,'scenario_sha256':digest,
             'trace_sha256':sha256(first).hexdigest(),'deterministic_reexecution':True,
             'executed_steps':len(payload['records']),'termination':payload['termination'],
             'task_metric_status':payload['metric']['status'],
             'task_classification':payload['metric']['value'].get('classification') if isinstance(payload['metric']['value'],dict) else None,
             'physical_readiness':False,
             'files':{name:sha256(data).hexdigest() for name,data in files.items()}}
    args.output_dir.mkdir(parents=True,exist_ok=False)
    for name,data in files.items():
        path=args.output_dir/name
        with path.open('xb') as stream:stream.write(data)
        if path.read_bytes()!=data:raise IOError('artifact read-back differs')
    # 最後のsummaryが存在する場合だけ、全fileの検証を完了したbatchである。
    with (args.output_dir/'summary.json').open('xb') as stream:stream.write(canonical(summary)+b'\n')
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
