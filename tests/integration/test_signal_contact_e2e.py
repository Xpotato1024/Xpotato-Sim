"""入力からraw contact・wireまでの因果経路と失敗artifactを検証する。"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import pytest
from xpotato_sim.runtime.runners.signal_contact import capture_signal_contact, canonical, strict_json, validate_scenario
from xpotato_sim.runtime.runners.signal_contact_artifact import decode_signal_trace
from xpotato_sim.runtime.contact.log import decode_contact_task_log
from xpotato_sim.plugins.tasks.contact_press_hold_task import implementation as task_impl

ROOT=Path(__file__).resolve().parents[2]
REVISION='test-only-integration'

def fixture(source):
    return json.loads((ROOT/'tests/fixtures/prehardware_signal'/f'{source}.json').read_text(encoding='utf-8'))

def execute(scenario):
    raw=capture_signal_contact(scenario,software_revision=REVISION)
    return raw,decode_signal_trace(raw,expected_revision=REVISION,expected_scenario_sha256=sha256(canonical(scenario)).hexdigest())

def alter_axes(scenario,value):
    result=deepcopy(scenario)
    if result['source']=='selfrionette':
        result['payloads']=[','.join(line.split(',')[:2]+[str(value)]+line.split(',')[3:]) for line in result['payloads']]
    else:
        messages=[json.loads(p) for p in result['payloads']]
        for m in messages:m['gamepad']['axes'][0]=float(value)
        result['payloads']=[json.dumps(m) for m in messages]
    return result

@pytest.mark.parametrize('source',('selfrionette','gamepad'))
def test_input_to_contact_success_is_deterministic_and_not_a_fixed_success(source,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('actual socket/DNS is forbidden')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    config=fixture(source)
    first,p=execute(config);second,_=execute(config)
    assert first==second
    log=decode_contact_task_log(p['contact_log'].encode())
    assert log.summary.outcome.classification.value=='success'
    assert p['termination']['kind']=='task_terminal' and len(p['records'])==5
    assert len(p['final_payload']['qpos'])==11 and len(p['records'][-1]['after_robot_qpos'])==4
    assert [r['after_scene']['frame_index'] for r in p['records']]==[1,2,3,4,5]
    assert all(r['physical_permission']['status']=='rejected' for r in p['records'])
    assert all(r['acknowledgement']['reason']=='simulated_router_observation_correlated' for r in p['records'])
    _,zero=execute(alter_axes(config,0))
    no_contact=decode_contact_task_log(zero['contact_log'].encode())
    assert no_contact.summary.outcome.classification.value=='failure'
    assert all(s.observation.contact_evidence.status.value=='no_contact' for s in no_contact.samples)
    assert zero['records'][-1]['after_robot_qpos']==zero['records'][0]['before_robot_qpos']
    assert zero['records'][1]['wire_hex'] != p['records'][1]['wire_hex']
    _,reverse=execute(alter_axes(config,1))
    assert decode_contact_task_log(reverse['contact_log'].encode()).summary.outcome.classification.value!='success'

@pytest.mark.parametrize('source',('selfrionette','gamepad'))
def test_malformed_input_preserves_completed_trace_without_a_new_step(source):
    config=fixture(source);config['payloads'][2]='vector,not,a,vector' if source=='selfrionette' else '{broken'
    _,p=execute(config)
    assert p['termination']['kind']=='execution_failure'
    assert p['termination']['exception_type'] is not None
    assert len(p['records'])==2 and p['final_payload']['frame_index']==2
    assert len(decode_contact_task_log(p['contact_log'].encode()).samples)==3

def test_eof_retains_partial_evidence_not_old_nonzero_command():
    config=fixture('selfrionette');config['payloads']=config['payloads'][:2]
    _,p=execute(config)
    assert p['termination']['exception_type']=='StopIteration'
    assert len(p['records'])==2 and p['final_payload']['time_s']==.004

def test_gamepad_silence_is_stale_and_not_a_zero_measurement():
    config=fixture('gamepad');config['payloads'][2]=None
    config['host_times_s'][2:]=[.6+i*.002 for i in range(len(config['host_times_s'])-2)]
    _,p=execute(config)
    assert len(p['records'])==3
    last=p['records'][-1]
    assert last['runtime_safety']['is_stale']
    assert last['after_robot_qpos']==last['before_robot_qpos']
    assert p['metric']['value']['classification']=='failure'

@pytest.mark.parametrize('mode',('missing','malformed','disconnect'))
def test_bad_or_missing_response_is_not_acknowledged_and_stops_further_input(mode):
    config=fixture('selfrionette');config['response_mode']=mode
    _,p=execute(config)
    assert p['termination']['kind']=='response_failure'
    assert len(p['records'])==1
    assert p['records'][0]['acknowledgement']['status'] != 'router_command_observed'
    assert p['metric']['value']['classification'] != 'success'

@pytest.mark.parametrize('change',('extra_field','wrong_source','wrong_mapping','missing_wire','bad_clock','empty','hardware_flag'))
def test_invalid_scenario_fails_before_reader_or_model_execution(change):
    config=fixture('selfrionette')
    if change=='extra_field':config['anything']=1
    elif change=='wrong_source':config['source']='hardware'
    elif change=='wrong_mapping':config['mapping']['plugin_id']='not_a_plugin'
    elif change=='missing_wire':del config['wire_mapping']['joint_coordinate_signs']
    elif change=='bad_clock':config['host_times_s'][1]=config['host_times_s'][0]
    elif change=='empty':config['host_times_s']=[]
    else:config['port']='COM-any'
    with pytest.raises((ValueError,TypeError,KeyError)):
        capture_signal_contact(config,software_revision=REVISION)

@pytest.mark.parametrize('tamper',('wire','request','projection','intent','metric','coverage','missing_record','unknown_field','physical_safety','response','model','proxy'))
def test_rehashed_semantic_tampering_is_still_rejected(tamper):
    raw,_=execute(fixture('selfrionette'));envelope=strict_json(raw);p=envelope['payload']
    if tamper=='wire':p['records'][1]['wire_hex']='00'
    elif tamper=='request':p['records'][1]['request']['sequence']=99
    elif tamper=='projection':p['projection']['qpos_addresses']=[7,8,9,10]
    elif tamper=='intent':p['records'][1]['intent']['values'][0]=.123
    elif tamper=='metric':p['metric']['value']['classification']='failure'
    elif tamper=='coverage':p['coverage']['physical_gate']='allowed'
    elif tamper=='missing_record':p['records'].pop()
    elif tamper=='physical_safety':p['records'][0]['physical_safety']['status']='allowed'
    elif tamper=='response':p['records'][0]['acknowledgement']['status']='router_command_observed'
    elif tamper=='model':p['model_sha256']='0'*64
    elif tamper=='proxy':p['proxy']['radius_m']=.03
    else:p['records'][0]['unexpected']=True
    envelope['payload_sha256']=sha256(canonical(p)).hexdigest()
    with pytest.raises((ValueError,TypeError,KeyError)):
        decode_signal_trace(canonical(envelope)+b'\n',expected_revision=REVISION)

def test_wrong_revision_truncation_and_duplicate_keys_are_rejected():
    raw,_=execute(fixture('selfrionette'))
    with pytest.raises(ValueError,match='revision'):decode_signal_trace(raw,expected_revision='another')
    with pytest.raises(ValueError):decode_signal_trace(raw[:-80],expected_revision=REVISION)
    with pytest.raises(ValueError,match='duplicate'):strict_json('{"x":1,"x":2}')

def test_normal_alignment_roundoff_is_bounded_without_relaxing_contract():
    _,p=execute(fixture('selfrionette'))
    log=decode_contact_task_log(p['contact_log'].encode())
    for s in log.samples:
        x=task_impl._normal_alignment(s.observation,log.header.context)
        assert x is None or -1<=x<=1

def test_normal_alignment_large_error_is_not_clamped_to_success(monkeypatch):
    _,p=execute(fixture('selfrionette'))
    log=decode_contact_task_log(p['contact_log'].encode())
    monkeypatch.setattr(task_impl,'_dot',lambda *args:1.001)
    with pytest.raises(ValueError,match='floating-point tolerance'):
        task_impl._normal_alignment(log.samples[-1].observation,log.header.context)


def test_reset_outside_declared_joint_limits_is_rejected():
    config=fixture('selfrionette')
    config['contact_manifest']['scene']['reset']['qpos_rad'][1]=100.0
    with pytest.raises(ValueError,match='reset'):
        capture_signal_contact(config,software_revision='test-only-extra-audit')

def test_final_payload_profile_tampering_is_rejected_after_rehash():
    raw=capture_signal_contact(fixture('selfrionette'),software_revision='test-only-extra-audit')
    data=strict_json(raw)
    data['payload']['final_payload']['metadata']['robot_profile_id']='different_robot'
    data['payload_sha256']=sha256(canonical(data['payload'])).hexdigest()
    with pytest.raises(ValueError):
        decode_signal_trace(canonical(data)+b'\n',expected_revision='test-only-extra-audit')

def test_unrepresentable_ack_deadline_is_rejected_at_preflight():
    config=fixture('selfrionette');config['host_times_s']=[1e308];config['payloads']=config['payloads'][:1]
    with pytest.raises(ValueError):validate_scenario(config)


@pytest.mark.parametrize('change',('fixture_payload','health'))
def test_rehashed_source_provenance_must_match_execution(change):
    config=json.loads((ROOT/'tests/fixtures/prehardware_signal/selfrionette.json').read_text(encoding='utf-8'))
    envelope=strict_json(capture_signal_contact(config,software_revision='test-only-source-audit'))
    p=envelope['payload']
    if change=='fixture_payload':
        p['scenario']['payloads'][1]='vector,2,1,0,0,0,0,0,0'
        p['scenario_sha256']=sha256(canonical(p['scenario'])).hexdigest()
    else:p['records'][1]['health']['status']='disconnected'
    envelope['payload_sha256']=sha256(canonical(p)).hexdigest()
    with pytest.raises(ValueError):
        decode_signal_trace(canonical(envelope)+b'\n',expected_revision='test-only-source-audit')
