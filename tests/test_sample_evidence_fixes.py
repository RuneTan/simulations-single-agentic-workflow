"""REPORT F1–F4: record layout, damaged input, strict JSON and version binding."""
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

from nsbi_agent.tools import sample, experience
from nsbi_agent.tools.environment import PROJECT_ROOT
from nsbi_agent.validation_contracts import SampleRequest, ExperienceRequest
from test_sample_experience import sample_text, candidate


def check_file(tmp_path, text):
    path = tmp_path/'events.lhe'; path.write_text(text)
    return sample.check_sample(SampleRequest(event_file=str(path)))


@pytest.mark.parametrize('column', [4, 5, 6, 7])
def test_each_pdf_init_field_is_an_integer(tmp_path, column):
    before = '11 -11 10 10 0 0 0 0 -1 1'
    fields = before.split(); fields[column] = 'nonsense'
    result = check_file(tmp_path, sample_text().replace(before, ' '.join(fields)))
    assert result.status == 'failed'
    assert result.errors[0].code == 'SAMPLE_READ_FAILED'


def test_pdf_metadata_is_reported_after_type_checks(tmp_path):
    result = check_file(tmp_path, sample_text().replace('10 10 0 0 0 0 -1 1','10 10 0 -1 247000 244600 -1 1'))
    assert result.status == 'succeeded'
    assert result.data['init']['pdf_group_ids'] == [0, -1]
    assert result.data['init']['pdf_set_ids'] == [247000, 244600]


def test_header_event_is_metadata_and_real_events_are_counted(tmp_path):
    text = sample_text(); event = re.search(r'<event>.*?</event>', text, re.S).group()
    result = check_file(tmp_path, text.replace('</header>', event+'</header>'))
    assert result.status == 'succeeded' and result.data['events'] == 2
    header_only = re.sub(r'<event>.*?</event>', '', text, flags=re.S).replace('</header>', event+'</header>')
    result = check_file(tmp_path, header_only)
    assert result.status == 'failed' and result.data['events'] == 0


@pytest.mark.parametrize('change', ['duplicate_init', 'event_before_init'])
def test_init_is_unique_and_precedes_events(tmp_path, change):
    text = sample_text(); init = re.search(r'<init>.*?</init>', text, re.S).group()
    if change == 'duplicate_init': text = text.replace(init, init+init)
    else: text = text.replace(init, '').replace('</LesHouchesEvents>', init+'</LesHouchesEvents>')
    assert check_file(tmp_path, text).status == 'failed'


def test_event_groups_and_namespaces_keep_record_counts(tmp_path):
    text = sample_text().replace('version="3.0"','version="3.0" xmlns="urn:lhe"')
    text = text.replace('<event>', '<eventgroup><event>').replace('</event>', '</event></eventgroup>')
    result = check_file(tmp_path, text)
    assert result.status == 'succeeded'
    assert result.data['events'] == result.data['event_groups'] == 2
    assert result.data['nominal']['sumw'] == 1.0


def test_other_event_container_goes_to_agent_review(tmp_path):
    text = sample_text().replace('<event>', '<custom><event>').replace('</event>', '</event></custom>')
    result = check_file(tmp_path, text)
    assert result.status == 'needs_review'
    assert result.data['review_items'][0]['topic'] == 'sample'


@pytest.mark.parametrize('damage', ['empty_weight', 'bad_deflate'])
def test_damaged_samples_return_json_and_keep_log(tmp_path, damage):
    path = tmp_path/('events.lhe.gz' if damage=='bad_deflate' else 'events.lhe')
    if damage == 'empty_weight': path.write_text(sample_text().replace('<wgt id="c1">2</wgt>', '<wgt id="c1"/>'))
    else:
        raw = bytearray(gzip.compress(sample_text().encode(),mtime=0)); raw[10] = 7; path.write_bytes(raw)
    log = tmp_path/'run.log'; log.write_text('latest diagnostic\n')
    payload = {'event_file':str(path),'log_file':str(log)}
    result = sample.check_sample(SampleRequest.model_validate(payload))
    assert result.status == 'failed' and result.errors[0].code == 'SAMPLE_READ_FAILED'
    assert result.data['log_tail'] == ['latest diagnostic']
    assert result.artifacts[0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    source = tmp_path/'request.json'; source.write_text(json.dumps(payload))
    run = subprocess.run([str(PROJECT_ROOT/'run.sh'),'check-sample',str(source)],capture_output=True,text=True,timeout=30)
    assert run.returncode == 1 and not run.stderr
    assert json.loads(run.stdout)['errors'][0]['code'] == 'SAMPLE_READ_FAILED'


@pytest.mark.parametrize('text,expected', [
    ('{"status":"failed","status":"succeeded"}', 'failed'),
    ('{"passed":false,"passed":true}', 'failed'),
    ('{"status":"succeeded","metric":NaN}', 'failed'),
    ('{"status":"succeeded","metric":Infinity}', 'failed'),
    ('{"status":"succeeded","metric":1e999}', 'failed'),
    ('{"status":"needs_review","passed":true}', 'needs_review'),
    ('{"status":"other","passed":true}', 'needs_review'),
    ('{"status":null,"passed":true}', 'needs_review'),
    ('{"status":"succeeded","passed":false}', 'failed'),
    ('{"status":"succeeded","passed":1}', 'needs_review'),
    ('{"status":"succeeded","passed":true}', 'succeeded'),
    ('{"passed":true}', 'succeeded'),
])
def test_validation_json_has_one_clear_conclusion(tmp_path, text, expected):
    path = tmp_path/'proof.json'; path.write_text(text)
    payload = candidate(path); payload['evidence'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = experience.check_experience(ExperienceRequest.model_validate(payload))
    assert result.status == expected
    assert result.data['admission'] == {'failed':'revise','needs_review':'review','succeeded':'ready'}[expected]
    source = tmp_path/'request.json'; source.write_text(json.dumps(payload))
    run = subprocess.run([str(PROJECT_ROOT/'run.sh'),'check-experience',str(source)],capture_output=True,text=True,timeout=30)
    assert run.returncode == {'failed':1,'needs_review':5,'succeeded':0}[expected] and not run.stderr


@pytest.mark.parametrize('compressed', [False, True])
def test_sample_update_before_open_binds_the_new_bytes(tmp_path, monkeypatch, compressed):
    text = sample_text(); old_text = re.sub(r'(<event>.*?</event>).*?(<event>.*?</event>)',r'\1',text,flags=re.S)
    encode = lambda text: gzip.compress(text.encode(),mtime=0) if compressed else text.encode()
    path = tmp_path/('events.lhe.gz' if compressed else 'events.lhe'); path.write_bytes(encode(old_text))
    original = sample.regular_path
    def replace_before_open(name):
        result = original(name); path.write_bytes(encode(text)); return result
    monkeypatch.setattr(sample,'regular_path',replace_before_open)
    result = sample.check_sample(SampleRequest(event_file=str(path),expected_events=2))
    assert result.status == 'succeeded' and result.data['events'] == 2
    assert result.data['nominal']['sumw'] == 1.0
    assert result.artifacts[0]['sha256'] == hashlib.sha256(encode(text)).hexdigest()


@pytest.mark.parametrize('compressed', [False, True])
def test_open_sample_stream_keeps_its_version_when_path_is_replaced(tmp_path, monkeypatch, compressed):
    text = sample_text(); old_text = re.sub(r'(<event>.*?</event>).*?(<event>.*?</event>)',r'\1',text,flags=re.S)
    encode = lambda text: gzip.compress(text.encode(),mtime=0) if compressed else text.encode()
    old, new = encode(old_text), encode(text)
    path = tmp_path/('events.lhe.gz' if compressed else 'events.lhe'); path.write_bytes(old)
    replacement = tmp_path/'replacement'; replacement.write_bytes(new)
    original = sample.HashingReader.read; changed = False
    def swap_after_read(reader, size=-1):
        nonlocal changed
        value = original(reader,size)
        if not changed:
            replacement.replace(path); changed = True
        return value
    monkeypatch.setattr(sample.HashingReader,'read',swap_after_read)
    result = sample.check_sample(SampleRequest(event_file=str(path),expected_events=1))
    assert changed and result.status == 'succeeded'
    assert result.data['events'] == 1 and result.data['nominal']['sumw'] == 1.5
    assert result.artifacts[0]['sha256'] == hashlib.sha256(old).hexdigest()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(new).hexdigest()


def test_validation_snapshot_binds_conclusion_and_expected_hash(tmp_path, monkeypatch):
    path = tmp_path/'proof.json'; old = b'{"status":"failed"}'; path.write_bytes(old)
    payload = candidate(path); payload['evidence'][0]['sha256'] = hashlib.sha256(old).hexdigest()
    original = experience.read_file
    def update_after_read(name):
        text,digest = original(name); path.write_text('{"status":"succeeded"}'); return text,digest
    monkeypatch.setattr(experience,'read_file',update_after_read)
    result = experience.check_experience(ExperienceRequest.model_validate(payload))
    assert result.status == 'failed' and result.data['admission'] == 'revise'
    assert result.data['entry']['evidence'][0]['sha256'] == hashlib.sha256(old).hexdigest()
    assert json.loads(path.read_text())['status'] == 'succeeded'


def test_bad_structure_flows_to_experience_revision(tmp_path):
    result = check_file(tmp_path,sample_text().replace('10 10 0 0 0 0 -1 1','10 10 nonsense 0 0 0 -1 1'))
    proof = tmp_path/'proof.json'; proof.write_text(result.model_dump_json())
    decision = experience.check_experience(ExperienceRequest.model_validate(candidate(proof)))
    assert result.status == decision.status == 'failed'
    assert decision.data['admission'] == 'revise'
