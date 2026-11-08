"""Known numerical samples and evidence/review admission controls."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from nsbi_agent.tools.sample import check_sample
from nsbi_agent.tools.experience import check_experience
from nsbi_agent.tools.environment import PROJECT_ROOT
from nsbi_agent.validation_contracts import SampleRequest, ExperienceRequest

PARTICLES = '''11 -1 0 0 0 0 0 0 10 10 0 0 9
-11 -1 0 0 0 0 0 0 -10 10 0 0 9
13 1 1 2 0 0 10 0 0 10 0 0 9
-13 1 1 2 0 0 -10 0 0 10 0 0 9'''


def sample_text(compact=False):
    def event(nominal, weight):
        body = f'<weights>{weight}</weights>' if compact else f'<rwgt><wgt id="c1">{weight}</wgt></rwgt>'
        return f'<event>\n4 1 {nominal} 20 0.007 0.118\n{PARTICLES}\n{body}\n</event>\n'
    return ('''<LesHouchesEvents version="3.0">
<header><initrwgt><weight id="c1">point 1</weight></initrwgt></header>
<init>\n11 -11 10 10 0 0 0 0 -1 1\n0.1 0.01 2 1\n</init>
''' + event(1.5, 2) + event(-0.5, -1) + '</LesHouchesEvents>\n')


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('compact', [False, True])
def test_lhe_counts_signed_weights_and_sumw2(tmp_path, compressed, compact):
    path = tmp_path/('events.lhe.gz' if compressed else 'events.lhe')
    text = sample_text(compact).encode()
    path.write_bytes(gzip.compress(text) if compressed else text)
    result = check_sample(SampleRequest(event_file=str(path), expected_events=2, required_weight_ids=['c1'], run_exit_code=0))
    assert result.status == 'succeeded', result.model_dump()
    assert result.data['tool_number'] == 4 and result.data['events'] == 2
    assert result.data['nominal'] == {'count':2, 'sumw':1.0, 'sumw2':2.5, 'negative':1,'zero':0,'min':-0.5,'max':1.5}
    assert result.data['weights']['c1']['sumw'] == 1.0
    assert result.data['weights']['c1']['sumw2'] == 5.0
    assert result.data['init']['processes'][0]['reported_cross_section_pb'] == 0.1
    assert result.artifacts[0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('damage', ['truncated','nup','nan','missing_weight','duplicate_weight','count','required_id','exit'])
def test_sample_differences_are_actionable(tmp_path, damage):
    path = tmp_path/'events.lhe'; text = sample_text()
    if damage == 'truncated': text = text.removesuffix('</LesHouchesEvents>\n')
    elif damage == 'nup': text = text.replace('4 1 1.5', '5 1 1.5')
    elif damage == 'nan': text = text.replace('4 1 1.5', '4 1 nan')
    elif damage == 'missing_weight': text = text.replace('<wgt id="c1">-1</wgt>', '')
    elif damage == 'duplicate_weight': text = text.replace('<wgt id="c1">2</wgt>', '<wgt id="c1">2</wgt><wgt id="c1">3</wgt>')
    path.write_text(text)
    result = check_sample(SampleRequest(event_file=str(path), expected_events=3 if damage=='count' else 2,
                                        required_weight_ids=['missing'] if damage=='required_id' else [], run_exit_code=1 if damage=='exit' else 0))
    assert result.status == 'failed' and result.errors


def test_log_is_context_and_is_available_for_a_broken_sample(tmp_path):
    path = tmp_path/'broken.lhe'; path.write_text('<LesHouchesEvents>')
    log = tmp_path/'run.log'; log.write_text('Error estimate = 0.01\nlast message\n')
    result = check_sample(SampleRequest(event_file=str(path), log_file=str(log), run_exit_code=1))
    assert result.status == 'failed'
    assert result.data['log_tail'][-1] == 'last message'
    path.write_text(sample_text())
    assert check_sample(SampleRequest(event_file=str(path), log_file=str(log), run_exit_code=0)).status == 'succeeded'


def candidate(path, review=True, role='validation'):
    data = {'lesson':'Use the checked weight IDs for this sample.', 'applies_to':{'format':'LHE','task':'weight analysis'},
            'evidence':[{'path':str(path),'role':role}],
            'review':{'by':'agent','verdict':'accept','reason':'Compared the event count and signed-weight sums.'}}
    if not review: data.pop('review')
    return data


def test_sample_report_to_experience_ready_and_read_only(tmp_path):
    events = tmp_path/'events.lhe'; events.write_text(sample_text())
    checked = check_sample(SampleRequest(event_file=str(events), expected_events=2))
    proof = tmp_path/'sample_check.json'; proof.write_text(checked.model_dump_json())
    files_before = {p.name for p in tmp_path.iterdir()}
    result = check_experience(ExperienceRequest.model_validate(candidate(proof)))
    assert result.status == 'succeeded' and result.data['admission'] == 'ready'
    assert result.data['tool_number'] == 5
    assert result.data['entry']['evidence'][0]['sha256'] == hashlib.sha256(proof.read_bytes()).hexdigest()
    assert len(result.data['entry_sha256']) == 64
    assert {p.name for p in tmp_path.iterdir()} == files_before
    changed = candidate(proof); changed['applies_to']['task'] = 'another task'
    assert check_experience(ExperienceRequest.model_validate(changed)).data['entry_sha256'] != result.data['entry_sha256']


@pytest.mark.parametrize('mode,expected', [('no_review','needs_review'),('revise','needs_review'),('failed','failed'),('changed','failed'),('pending','needs_review'),('observation','succeeded')])
def test_experience_admission_uses_evidence_and_review(tmp_path, mode, expected):
    path = tmp_path/'record.json'; path.write_text('{"status":"succeeded"}')
    payload = candidate(path, review=mode!='no_review', role='observation' if mode=='observation' else 'validation')
    if mode == 'revise': payload['review']['verdict'] = 'revise'
    if mode in {'failed','observation'}: path.write_text('{"status":"failed"}')
    if mode == 'pending': path.write_text('{"status":"needs_review"}')
    if mode == 'changed': payload['evidence'][0]['sha256'] = '0'*64
    result = check_experience(ExperienceRequest.model_validate(payload))
    assert result.status == expected
    assert result.data['admission'] == ('revise' if mode=='revise' or expected=='failed' else 'ready' if expected=='succeeded' else 'review')


def test_issues_are_grouped_and_extension_context_stays_separate():
    from nsbi_agent.validation import Report
    report = Report('check_sample')
    for i in range(100): report.unknown(f'weights.{i}', i, 'Review IDs')
    result = report.result()
    assert result.status == 'needs_review'
    assert len(result.data['review_items']) == 1
    assert result.data['review_items'][0]['count'] == 100
    assert len(result.data['review_items'][0]['examples']) == 3
    assert not any(c.status == 'unverified' for c in result.checks)


def test_new_tools_cli_and_retired_loader(tmp_path):
    events = tmp_path/'events.lhe'; events.write_text(sample_text())
    request = tmp_path/'sample.json'; request.write_text(json.dumps({'event_file':str(events),'expected_events':2}))
    done = subprocess.run([str(PROJECT_ROOT/'run.sh'),'check-sample',str(request)],capture_output=True,text=True,timeout=30)
    assert done.returncode == 0 and not done.stderr
    proof = tmp_path/'proof.json'; proof.write_text(done.stdout)
    request.write_text(json.dumps(candidate(proof)))
    done = subprocess.run([str(PROJECT_ROOT/'run.sh'),'check-experience',str(request)],capture_output=True,text=True,timeout=30)
    assert done.returncode == 0 and json.loads(done.stdout)['data']['admission'] == 'ready'
    for command in ('load-model','query-model'):
        done = subprocess.run([str(PROJECT_ROOT/'run.sh'),command],capture_output=True,text=True,timeout=30)
        assert done.returncode == 2 and json.loads(done.stdout)['errors'][0]['code'] == 'ARCHIVED_COMMAND'
