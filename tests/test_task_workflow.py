"""Tests for the single-Agent workflow: state, tools, jobs, routing, memory and CLI."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from nsbi_agent.workflow import checks, experience, jobs, runner, tools
from nsbi_agent.workflow.graph import TaskController
from nsbi_agent.workflow.model_client import ModelConfig, Usage, load_project_credentials
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore

def _minimal_task(workspace):
    return {'task_id': 'pytest-task', 'user_request': 'generate 1000 events',
            'workspace': str(workspace)}

def _write_task(tmp_path, payload):
    path = tmp_path / 'task.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path

# -- 1. task file validation -------------------------------------------------
def test_load_task_file_accepts_minimal_task(tmp_path):
    """A minimal task gets defaults and its resolved file path recorded."""
    workspace = PROJECT_ROOT / 'workspace' / 'pytest-workflow-load'
    path = _write_task(tmp_path, _minimal_task(workspace))
    task = runner.load_task_file(path)
    assert task['task_id'] == 'pytest-task' and task['runtime'] == runner.DEFAULT_RUNTIME
    assert task['fixtures'] == [] and task['experience'] == {} and task['analysis'] == {}
    assert task['task_type'] == 'generation' and task['task_file'] == str(path.resolve())

@pytest.mark.parametrize('mutate', [
    lambda t: t.pop('task_id'), lambda t: t.pop('user_request'),
    lambda t: t.update(workspace='workspace/relative'),
    lambda t: t.update(workspace='/tmp/outside-project'),
    lambda t: t.update(runtime={'max_repairs': 0}), lambda t: t.update(runtime={'cores': -1}),
    lambda t: t.update(runtime={'job_timeout_seconds': 0}),
    lambda t: t.update(runtime={'max_repairs': 'two'}),
])
def test_load_task_file_rejects_invalid_tasks(tmp_path, mutate):
    """Missing fields, a bad workspace or a non-positive runtime exit invalid."""
    payload = _minimal_task(PROJECT_ROOT / 'workspace' / 'pytest-workflow-load')
    mutate(payload)
    with pytest.raises(runner.TaskFileError) as info:
        runner.load_task_file(_write_task(tmp_path, payload))
    assert info.value.exit_code == runner.EXIT_INVALID

# -- 2. durable state --------------------------------------------------------
def test_task_store_state_checkpoints_events_and_atomic_json(tmp_path):
    """State round-trips, each save appends a checkpoint, events append, no .tmp stays."""
    store = TaskStore(tmp_path / 'workspace').create()
    assert store.events_path.is_file() and store.checks_dir.is_dir()
    store.save_state({'task_id': 't1', 'stage': 'allocate', 'status': 'running'})
    store.save_state({'task_id': 't1', 'stage': 'check_spec', 'status': 'running'})
    assert store.load_state()['stage'] == 'check_spec' and store.checkpoint_stages() == [
        'allocate', 'check_spec']
    store.event('tool_call', tool='read_file')
    store.event('node', name='allocate')
    records = store.events()
    assert [(record['kind'], record.get('tool')) for record in records] == [
        ('tool_call', 'read_file'), ('node', None)]
    store.write_json('nested/data.json', {'ok': True})
    assert store.read_json('nested/data.json') == {'ok': True}
    assert not list(store.workspace.rglob('*.tmp'))

# -- 3. tool confinement -----------------------------------------------------
@pytest.fixture
def toolbox(tmp_path, monkeypatch):
    """A ToolBox whose fake project root is tmp_path, so both boundaries are testable."""
    monkeypatch.setattr(tools, 'PROJECT_ROOT', tmp_path)
    store = TaskStore(tmp_path / 'workspace').create()
    return tools.ToolBox(store, jobs.JobManager(store))

def test_toolbox_confines_writes_reads_lists_and_finishes(toolbox):
    """Writes stay in the workspace, reads inside the project, finish_stage is recorded."""
    result = toolbox.execute('write_file', {'path': 'notes/a.txt', 'content': 'hello'})
    assert Path(result['path']).read_text(encoding='utf-8') == 'hello'
    outside = toolbox.store.workspace.parent / 'outside.txt'
    with pytest.raises(tools.ToolError):
        toolbox.execute('write_file', {'path': str(outside), 'content': 'nope'})
    assert not outside.exists()
    with pytest.raises(tools.ToolError):
        toolbox.execute('read_file', {'path': '/etc/hostname'})
    toolbox.execute('write_file', {'path': 'cards/run_card.dat', 'content': '6500.0 = ebeam1\n'})
    listing = toolbox.execute('list_dir', {'path': 'cards'})
    assert [entry['name'] for entry in listing['entries']] == ['run_card.dat']
    read = toolbox.execute('read_file', {'path': 'cards/run_card.dat'})
    assert read['content'] == '6500.0 = ebeam1\n' and read['truncated'] is False
    declaration = {'stage': 'plan_resolve', 'complete': True, 'summary': 'request resolved',
                   'artifacts': {'request': 'request.json'}}
    assert toolbox.execute('finish_stage', declaration)['accepted'] is True
    assert toolbox.finished == declaration

# -- 4. background jobs ------------------------------------------------------
def test_job_manager_sleep_job_runs_to_completion(tmp_path, monkeypatch):
    """A 1-second job is running at start, then finishes with exit code 0 recorded."""
    monkeypatch.setattr(jobs, 'PROJECT_ROOT', tmp_path)
    store = TaskStore(tmp_path / 'workspace').create()
    manager = jobs.JobManager(store)
    cwd = store.workspace / 'run'
    cwd.mkdir()
    record = manager.start(name='sleeper', command='sleep 1', cwd=str(cwd), timeout_seconds=30)
    assert record['status'] == 'running' and manager.status(record)['status'] == 'running'
    assert Path(record['exit_code_file']).name == 'exit_code'
    deadline = time.time() + 15
    while manager.status(record)['status'] == 'running' and time.time() < deadline:
        time.sleep(0.2)
    finished = manager.status(record)
    assert finished['status'] == 'finished' and finished['exit_code'] == 0
    assert Path(record['exit_code_file']).is_file()

# -- 5. graph routing --------------------------------------------------------
class FakeClient:
    """Minimal chat transport: replays canned responses, never uses the network."""
    def __init__(self, responses=None):
        self.responses, self.calls = list(responses or []), 0

    def chat(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        return self.responses.pop(0)

def _finish_response(stage, **declaration):
    arguments = {'stage': stage, 'complete': True, 'summary': 'stage done', **declaration}
    call = {'id': 'call-1', 'type': 'function',
            'function': {'name': 'finish_stage', 'arguments': json.dumps(arguments)}}
    return {'content': '', 'finish_reason': 'tool_calls', 'model': 'fake', 'latency_s': 0.0,
            'usage': {'prompt_tokens': 5, 'completion_tokens': 2}, 'tool_calls': [call]}

def _controller(tmp_path, client=None):
    store = TaskStore(tmp_path / 'workspace').create()
    task = {'task_type': 'generation',
            'runtime': {'max_repairs': 2, 'cores': 1, 'job_timeout_seconds': 60}}
    return TaskController(store, client or FakeClient(), task)

def test_check_spec_repairs_then_fails_at_budget(tmp_path, monkeypatch):
    """A failed spec triggers one repair; failure at the repair budget stops the task."""
    failed = {'number': 2, 'status': 'failed', 'errors': ['beam energy mismatch'], 'path': None,
              'review_items': [], 'failed_checks': [], 'summary': {}}
    monkeypatch.setattr(checks, 'run_spec_check', lambda store, state, version: dict(failed))
    controller = _controller(tmp_path)
    repaired = controller.node_check_spec({'task_id': 't', 'repairs': 0})
    assert repaired['stage'] == 'plan_resolve' and repaired['repairs'] == 1
    assert repaired['repair_feedback']['check'] == 2
    exhausted = controller.node_check_spec({'task_id': 't', 'repairs': 2})
    assert exhausted['status'] == 'failed' and exhausted['stage'] == 'failed'

def test_check_cards_needs_review_then_dispositions(tmp_path, monkeypatch):
    """needs_review routes to dispositions; a complete topic set returns to launch."""
    topics = ['beam_energy', 'pdf_set', 'jet_cut']
    review = {'number': 3, 'status': 'needs_review', 'path': '/tmp/check3.json', 'summary': {},
              'failed_checks': [], 'errors': [], 'review_items': [{'topic': t} for t in topics],
              'bindings': {'artifact_sha256': {'run_card': 'abc'}}}
    monkeypatch.setattr(checks, 'run_card_check', lambda *args, **kwargs: dict(review))
    dispositions = [{'topic': t, 'disposition': 'accepted', 'reason': 'native value'}
                    for t in topics]
    client = FakeClient([_finish_response('dispositions', dispositions=dispositions)])
    controller = _controller(tmp_path, client)
    pending = controller.node_check_cards({'task_id': 't', 'check3_count': 0})
    assert pending['stage'] == 'dispositions' and pending['disposition_return'] == 'launch'
    assert pending['check3_count'] == 1
    resolved = controller.node_dispositions({**pending, 'task_id': 't'})
    assert resolved['stage'] == 'launch' and resolved['dispositioned'] == [3]
    assert len(resolved['dispositions']) == 3 and client.calls == 1

def test_await_job_routes_finished_and_lost(tmp_path, monkeypatch):
    """A finished job continues to check_sample; a lost job returns to prepare_cards."""
    controller = _controller(tmp_path)
    job_id = 'job-1'
    controller.store.write_json(f'jobs/{job_id}/job.json', {'job_id': job_id})
    base = {'task_id': 't', 'job': {'job_id': job_id}, 'repairs': 0}
    finished = {'job_id': job_id, 'status': 'finished', 'exit_code': 0, 'elapsed_s': 1.5}
    monkeypatch.setattr(jobs.JobManager, 'status', lambda self, record: dict(finished))
    updates = controller.node_await_job(dict(base))
    assert updates['stage'] == 'check_sample' and updates['status'] == 'running'
    assert updates['timing']['generator_wall_s'] == 1.5
    lost = {'job_id': job_id, 'status': 'lost', 'exit_code': None, 'elapsed_s': 2.0}
    monkeypatch.setattr(jobs.JobManager, 'status', lambda self, record: dict(lost))
    repaired = controller.node_await_job(dict(base))
    assert repaired['stage'] == 'prepare_cards' and repaired['repairs'] == 1
    assert repaired['repair_feedback']['check'] == 4

def test_inject_fixture_rewrites_one_beam_energy(tmp_path):
    """The beam fixture changes ebeam1 only and records before/after hashes."""
    controller = _controller(tmp_path)
    run_card = controller.store.workspace / 'cards' / 'run_card.dat'
    run_card.parent.mkdir(parents=True)
    run_card.write_text('# run card\n6500.0 = ebeam1  ! beam 1 energy\n'
                        '6500.0 = ebeam2  ! beam 2 energy\n', encoding='utf-8')
    state = {'task_id': 't', 'cards': {'run_card': str(run_card)}, 'fixtures': [
        {'type': 'run_card_beam_mismatch', 'field': 'ebeam1', 'from': 6500.0, 'to': 7000.0}]}
    fixture = controller.node_inject_fixture(state)['fixture']
    text = run_card.read_text(encoding='utf-8')
    assert '7000.0 = ebeam1' in text and '6500.0 = ebeam2' in text
    assert fixture['from'] == 6500.0 and fixture['to'] == 7000.0
    assert fixture['before_sha256'] != fixture['after_sha256']
    assert '7000.0' in Path(fixture['diff']).read_text(encoding='utf-8')

# -- 6. experience memory ----------------------------------------------------
def test_experience_persist_and_retrieve(tmp_path, monkeypatch):
    """An admitted entry is retrievable by an overlapping query and ignored otherwise."""
    entry_dir, candidate_dir = tmp_path / 'entries', tmp_path / 'candidates'
    monkeypatch.setattr(experience, 'ENTRY_DIR', entry_dir)
    monkeypatch.setattr(experience, 'CANDIDATE_DIR', candidate_dir)
    store = TaskStore(tmp_path / 'workspace').create()
    entry = {'lesson': 'always re-check ebeam1 after fixture injection',
             'applies_to': {'backend': 'madgraph 2.9.27', 'task': 'beam configuration'}}
    assert Path(experience.persist_entry(store, entry, 'deadbeef')['path']).is_file()
    candidate = experience.save_candidate(store, {'lesson': 'draft'})
    assert (candidate_dir / (candidate['candidate_sha256'] + '.json')).is_file()
    match = experience.retrieve(store, {'backend': 'madgraph 2.9.27',
                                        'task': 'beam configuration'})
    assert match['matched'] and match['matched'][0]['score'] > 0
    assert 'always re-check ebeam1' in match['content']
    miss = experience.retrieve(store, {'backend': 'pythia 8.3', 'task': 'histogram'})
    assert miss['matched'] == [] and miss['content'] == ''

# -- 7. model client ---------------------------------------------------------
def test_usage_and_public_config_are_safe(monkeypatch):
    """Usage.add sums tokens; public() shows credential presence but never the secret."""
    secret = 'sk-pytest-secret-value-123456'
    usage = Usage()
    usage.add({'prompt_tokens': 10, 'completion_tokens': 5,
               'prompt_tokens_details': {'cached_tokens': 4}})
    usage.add({'prompt_tokens': 3, 'completion_tokens': 2, 'prompt_cache_hit_tokens': 1})
    assert usage.calls == 2 and usage.prompt_tokens == 13 and usage.completion_tokens == 7
    assert usage.prompt_cache_hit_tokens == 5 and usage.summary()['total_tokens'] == 20
    monkeypatch.setenv('DEEPSEEK_API_KEY', secret)
    config = ModelConfig()
    public = config.public()
    assert config.api_key == secret and secret not in json.dumps(public)
    assert public['credential_present'] is True
    assert public['credential_variable'] == 'DEEPSEEK_API_KEY'

def test_load_project_credentials_does_not_override_environment(tmp_path, monkeypatch):
    """A .env.local value is loaded only when the variable is not already set."""
    monkeypatch.delenv('NSBI_TEST_TOKEN', raising=False)
    monkeypatch.setenv('NSBI_TEST_KEEP', 'keep')
    (tmp_path / '.env.local').write_text('NSBI_TEST_TOKEN=file\nNSBI_TEST_KEEP=file\n')
    assert load_project_credentials(tmp_path) == ['NSBI_TEST_TOKEN']
    assert os.environ['NSBI_TEST_TOKEN'] == 'file'
    assert os.environ['NSBI_TEST_KEEP'] == 'keep'

# -- 8. CLI ------------------------------------------------------------------
def test_cli_task_missing_file_prints_invalid_json(tmp_path):
    """`run.sh task <missing>` exits 2 and prints a JSON status invalid summary."""
    completed = subprocess.run(['/bin/bash', str(PROJECT_ROOT / 'run.sh'), 'task',
                                str(tmp_path / 'missing-task.json')],
                               text=True, capture_output=True, timeout=60)
    assert completed.returncode == 2, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload['status'] == 'invalid' and payload['exit_code'] == 2
    assert 'not found' in payload['message']
