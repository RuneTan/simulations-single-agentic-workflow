"""Regression tests for the independent audit findings F1-F6 (2026-11-02).

Each test reproduces one control from the external report and asserts the fixed
behaviour: deferred/repaired/stale dispositions, failed run-record routing,
request-driven prompts, configurable analysis bins, experience hand-off to
operational stages and finish-time based job metrics.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nsbi_agent.workflow import checks, jobs, prompts, runner
from nsbi_agent.workflow.agent import StageError
from nsbi_agent.workflow.graph import TaskController
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def chat(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        return self.responses.pop(0)


def _finish(stage, **declaration):
    arguments = {'stage': stage, 'complete': True, 'summary': 'stage done', **declaration}
    call = {'id': 'call-1', 'type': 'function',
            'function': {'name': 'finish_stage', 'arguments': json.dumps(arguments)}}
    return {'content': '', 'finish_reason': 'tool_calls', 'model': 'fake', 'latency_s': 0.0,
            'usage': {'prompt_tokens': 5, 'completion_tokens': 2}, 'tool_calls': [call]}


def _controller(tmp_path, client=None):
    store = TaskStore(tmp_path / 'workspace').create()
    task = {'task_type': 'generation',
            'runtime': {'max_repairs': 2, 'cores': 1, 'job_timeout_seconds': 60}}
    return TaskController(store, client, task)


def _review(number=3, digest='a' * 64, topics=('param_card',), status='needs_review'):
    return {'number': number, 'version': 1, 'phase': 'configuration', 'status': status,
            'path': '/audit/check-%s.json' % digest[:8], 'sha256': digest,
            'summary': {}, 'review_items': [{'topic': topic} for topic in topics],
            'errors': [], 'failed_checks': [], 'bindings': {'artifact_sha256': {}},
            'elapsed_s': 0.01}


# -- F2: disposition protocol ------------------------------------------------
def test_deferred_disposition_pauses_task(tmp_path, monkeypatch):
    """A deferred topic pauses the task instead of advancing to launch."""
    client = FakeClient([_finish('dispositions', dispositions=[
        {'topic': 'param_card', 'disposition': 'deferred',
         'reason': 'User must choose the width convention.', 'evidence': []}])])
    controller = _controller(tmp_path, client)
    state = {'task_id': 'probe', 'pending_review': _review(),
             'disposition_return': 'launch', 'dispositions': [], 'dispositioned': [],
             'experience': {}}
    updates = controller.node_dispositions(state)
    assert updates['status'] == 'awaiting_user'
    assert updates['stage'] == 'awaiting_user'
    assert updates['pending_review']['number'] == 3
    assert updates['user_questions'][0]['topic'] == 'param_card'
    merged = {**state, **updates}
    assert runner._exit_code(merged) == runner.EXIT_AWAITING_USER


def test_user_decisions_resume_dispositions_without_model(tmp_path):
    """Recorded user decisions resolve a paused task without another model call."""
    controller = _controller(tmp_path, client=None)
    record = _review(digest='b' * 64)
    state = {'task_id': 'probe', 'pending_review': record,
             'disposition_return': 'launch', 'dispositions': [],
             'dispositioned': [], 'user_questions': [{'topic': 'param_card'}],
             'user_decisions': {'param_card': 'Keep the native width convention.'}}
    updates = controller.node_dispositions(state)
    assert updates['stage'] == 'launch'
    assert updates['pending_review'] is None
    assert updates['user_questions'] == []
    assert updates['dispositions'][-1]['decided_by'] == 'user'
    assert updates['dispositions'][-1]['check_sha256'] == record['sha256']


def test_repaired_disposition_returns_to_owning_stage(tmp_path, monkeypatch):
    """A repaired disposition triggers a recheck instead of advancing."""
    client = FakeClient([_finish('dispositions', dispositions=[
        {'topic': 'param_card', 'disposition': 'repaired',
         'reason': 'Fixed the card value.', 'evidence': []}])])
    controller = _controller(tmp_path, client)
    state = {'task_id': 'probe', 'pending_review': _review(),
             'disposition_return': 'launch', 'dispositions': [], 'dispositioned': [],
             'repairs': 0, 'experience': {}}
    updates = controller.node_dispositions(state)
    assert updates['stage'] == 'prepare_cards'
    assert updates['repairs'] == 1
    assert updates['repair_feedback']['check'] == 3


def test_stale_disposition_does_not_cover_a_new_check_version(tmp_path, monkeypatch):
    """A disposition bound to an old check hash never authorises a new version."""
    fresh = _review(number=3, digest='c' * 64, topics=('new_scale_choice',))
    monkeypatch.setattr(checks, 'run_card_check', lambda *args, **kwargs: dict(fresh))
    monkeypatch.setattr(TaskController, '_snapshot_cards', lambda *args, **kwargs: {})
    controller = _controller(tmp_path)
    old = {'check_number': 3, 'check_sha256': 'a' * 64, 'topics': ['param_card'],
           'disposition': 'accepted'}
    state = {'task_id': 'probe', 'card_version': 2, 'dispositioned': [3],
             'dispositions': [old], 'checks': {}, 'check_history': []}
    updates = controller.node_check_cards(state)
    assert updates['stage'] == 'dispositions'
    assert updates['pending_review']['sha256'] == fresh['sha256']

    same = _review(number=3, digest='d' * 64, topics=('param_card',))
    monkeypatch.setattr(checks, 'run_card_check', lambda *args, **kwargs: dict(same))
    bound = {'check_number': 3, 'check_sha256': same['sha256'], 'topics': ['param_card'],
             'disposition': 'accepted'}
    resolved = controller.node_check_cards({**state, 'dispositions': [bound]})
    assert resolved['stage'] == 'launch'


# -- F3: failed run-record configuration ------------------------------------
def test_failed_run_record_check_routes_to_repair(tmp_path, monkeypatch):
    """A failed banner comparison must repair/fail, never reach analyze_deliver."""
    sample = {'number': 4, 'status': 'succeeded', 'path': None, 'sha256': None,
              'summary': {}, 'review_items': [], 'errors': [], 'failed_checks': []}
    banner_record = _review(number=3, digest='e' * 64, topics=('run_card',), status='failed')
    monkeypatch.setattr(checks, 'run_sample_check', lambda *args, **kwargs: dict(sample))
    monkeypatch.setattr(checks, 'find_banner', lambda job: '/tmp/banner.txt')
    monkeypatch.setattr(checks, 'run_card_check', lambda *args, **kwargs: dict(banner_record))
    controller = _controller(tmp_path)
    state = {'task_id': 'probe', 'attempts': 1, 'repairs': 0, 'checks': {},
             'check_history': [], 'job': {}, 'dispositions': [],
             'dispositioned': [3], 'experience': {}}
    updates = controller.node_check_sample(state)
    assert updates['stage'] == 'prepare_cards'
    assert updates['repairs'] == 1
    assert updates['repair_feedback']['check'] == 3
    assert updates['checks']['cards_run_record']['status'] == 'failed'


# -- F4: request-driven prompts and analysis settings -----------------------
def test_prompts_do_not_bake_the_t0_fixture_into_other_requests():
    """A different request must not inherit T0's beams, cuts or analysis values."""
    text = prompts.stage_plan('Generate p p > mu+ mu- with 200 events at 3000 GeV per beam.',
                              '/tmp/ws', None, None)
    for fixture in ('6500', '1000 events', '66.0', 'QED=2 QCD=0'):
        assert fixture not in text
    request = {'simulation': {'process': 'p p > mu+ mu-', 'model': {'model_id': 'X'},
                              'generation': {'nevents': 200, 'beam_energies_GeV': [3000.0, 3000.0]},
                              'eft': {}},
               'production': {'qcd_order': 0, 'qed_order': 2}}
    prepare = prompts.stage_prepare_cards(request, '/tmp/ws', 1, None, None)
    assert '6500' not in prepare and 'QED=2 QCD=0' not in prepare


def test_analysis_template_honours_supplied_bins_and_range(tmp_path):
    """The analysis program uses request.analysis bins/range instead of T0 defaults."""
    lhe = tmp_path / 'events.lhe'
    lhe.write_text('''<LesHouchesEvents version="3.0">
<header><initrwgt><weight id="c1">p</weight></initrwgt></header>
<init>
11 -11 3000 3000 0 0 0 0 -1 1
1.0 0.1 1.0 1
</init>
<event>
2 1 1.0 80.0 0.007 0.118
11 1 0 0 0 0 0 0 40.0 0.0 0.0 40.0 0.0 0.0
-11 1 0 0 0 0 0 0 40.0 0.0 0.0 -40.0 0.0 0.0
<rwgt><wgt id="c1">2.0</wgt></rwgt>
</event>
</LesHouchesEvents>
''', encoding='utf-8')
    (tmp_path / 'analysis_config.json').write_text(json.dumps({
        'task_id': 'probe', 'sample_label': 'probe', 'event_file': str(lhe),
        'nevents': 1, 'seed': 7, 'observable': 'm_ee', 'bins': 20,
        'range_GeV': [70.0, 110.0],
    }), encoding='utf-8')
    environment = dict(os.environ, MPLCONFIGDIR=str(tmp_path / 'mpl'))
    completed = subprocess.run([sys.executable, '-I', '-B',
                                str(PROJECT_ROOT / 'src/nsbi_agent/workflow/templates/analysis.py')],
                               cwd=tmp_path, env=environment, text=True,
                               capture_output=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    table = json.loads((tmp_path / 'analysis.json').read_text())
    assert table['bins'] == 20
    assert table['bin_edges'][0] == 70.0 and table['bin_edges'][-1] == 110.0
    assert table['range_GeV'] == [70.0, 110.0]
    assert 'PDG 11' in table['selection'] and 'PDG -11' in table['selection']


# -- F5: experience hand-off -------------------------------------------------
def test_experience_is_supplied_to_operational_stages_and_recorded(tmp_path):
    controller = _controller(tmp_path)
    entry_sha = '1eeacf28ad4028167bb2d5300b124042759dc484056c41e2986d3bdbc962f7cd'
    state = {'experience': {'retrieval': {
        'content': 'Entry %s: diff the run card before submitting.' % entry_sha,
        'matched': [{'entry_sha256': entry_sha, 'score': 6, 'path': '/memory/x.json'}]}}}
    brief = controller._experience_retrieval(state)
    assert brief and entry_sha[:12] in brief['content']
    prompt = prompts.stage_prepare_cards({'simulation': {'process': 'p p > e+ e-',
                                                         'model': {},
                                                         'generation': {},
                                                         'eft': {}},
                                          'production': {}}, '/tmp/ws', 1, None, brief)
    assert entry_sha[:12] in prompt
    finished = {'summary': 'Applied experience %s: diffed the run card.' % entry_sha[:12]}
    updates = controller._record_experience_stage({**state, 'task_id': 'p'}, 'prepare_cards',
                                                  finished)
    usage = updates['experience']['stage_usage'][-1]
    assert usage['stage'] == 'prepare_cards' and usage['applied'] is True
    assert usage['entry_sha256'] == entry_sha


# -- F6: timing metrics ------------------------------------------------------
def test_job_elapsed_uses_finish_timestamp_not_query_time(tmp_path, monkeypatch):
    """A finished job's duration stays at finish-start even when queried later."""
    controller = _controller(tmp_path)
    job_id = 'finished-test-job'
    directory = controller.store.jobs_dir / job_id
    directory.mkdir(parents=True, exist_ok=True)
    exit_file = directory / 'exit_code'
    exit_file.write_text('0\n')
    finished_file = directory / 'finished_epoch'
    finished_file.write_text('103\n')
    (directory / 'job.log').write_text('finished\n')
    record = {'job_id': job_id, 'exit_code_file': str(exit_file),
              'finished_epoch_file': str(finished_file),
              'started_epoch': 100, 'pid': None, 'timeout_seconds': 60}
    with monkeypatch.context() as patch:
        patch.setattr(jobs.time, 'time', lambda: 110.0)
        first = controller.jobs.status(record)
        patch.setattr(jobs.time, 'time', lambda: 120.0)
        second = controller.jobs.status(record)
    assert first['status'] == second['status'] == 'finished'
    assert first['elapsed_s'] == second['elapsed_s'] == 3.0


def test_checker_seconds_include_all_history_versions(tmp_path):
    """checker_s sums every retained check (including failed versions), not just the latest."""
    store = TaskStore(tmp_path / 'workspace').create()
    state = {'task_id': 'probe', 'workspace': str(store.workspace), 'status': 'running',
             'stage': 'done', 'check_history': [
                 {'number': 1, 'status': 'succeeded', 'elapsed_s': 0.01},
                 {'number': 3, 'status': 'failed', 'elapsed_s': 0.02},
                 {'number': 3, 'status': 'needs_review', 'elapsed_s': 0.04}],
             'checks': {'environment': {'status': 'succeeded', 'elapsed_s': 0.01},
                        'cards': {'status': 'needs_review', 'elapsed_s': 0.04}},
             'timing': {}, 'started_at': '2026-11-02T00:00:00Z',
             'updated_at': '2026-11-02T00:00:10Z'}
    summary = runner._finalise(store, state, None, 0.5)
    assert summary['timing']['checker_s'] == 0.07
    assert summary['timing']['elapsed_wall_s'] == 10.0


def test_task_file_accepts_user_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'PROJECT_ROOT', tmp_path)
    task = {'task_id': 'paused-task', 'user_request': 'request',
            'workspace': str(tmp_path / 'workspace'),
            'user_decisions': {'param_card': 'keep native width'}}
    path = tmp_path / 'task.json'
    path.write_text(json.dumps(task))
    loaded = runner.load_task_file(path)
    assert loaded['user_decisions'] == {'param_card': 'keep native width'}
    task['user_decisions'] = {'param_card': 3}
    path.write_text(json.dumps(task))
    with pytest.raises(runner.TaskFileError):
        runner.load_task_file(path)


def test_resume_without_decision_keeps_task_paused(tmp_path, monkeypatch):
    """Resuming a paused task without a user decision stays at exit code 5."""
    monkeypatch.setattr(runner, 'PROJECT_ROOT', tmp_path)
    store = TaskStore(tmp_path / 'workspace').create()
    store.save_state({'task_id': 'paused', 'workspace': str(store.workspace),
                      'status': 'awaiting_user', 'stage': 'awaiting_user',
                      'user_questions': [{'topic': 'param_card', 'reason': 'choose width'}],
                      'user_decisions': {}, 'started_at': '2026-11-02T00:00:00Z',
                      'updated_at': '2026-11-02T00:00:00Z'})
    task = {'task_id': 'paused', 'user_request': 'request',
            'workspace': str(tmp_path / 'workspace')}
    path = tmp_path / 'task.json'
    path.write_text(json.dumps(task))
    summary = runner.run_resume(path)
    assert summary['status'] == 'awaiting_user'
    assert summary['exit_code'] == runner.EXIT_AWAITING_USER


def test_resume_with_decision_reenters_dispositions(tmp_path, monkeypatch):
    """A resume carrying the requested decisions re-enters the dispositions stage."""
    monkeypatch.setattr(runner, 'PROJECT_ROOT', tmp_path)
    store = TaskStore(tmp_path / 'workspace').create()
    store.save_state({'task_id': 'paused', 'workspace': str(store.workspace),
                      'status': 'awaiting_user', 'stage': 'awaiting_user',
                      'user_questions': [{'topic': 'param_card', 'reason': 'choose width'}],
                      'user_decisions': {}, 'started_at': '2026-11-02T00:00:00Z',
                      'updated_at': '2026-11-02T00:00:00Z'})
    task = {'task_id': 'paused', 'user_request': 'request',
            'workspace': str(tmp_path / 'workspace'),
            'user_decisions': {'param_card': 'keep native width'}}
    path = tmp_path / 'task.json'
    path.write_text(json.dumps(task))
    captured = {}

    def fake_execute(task_payload, task_store, state):
        captured.update(state)
        return {'status': 'completed', 'exit_code': 0, 'task_id': state.get('task_id')}

    monkeypatch.setattr(runner, '_execute', fake_execute)
    runner.run_resume(path)
    assert captured['stage'] == 'dispositions'
    assert captured['status'] == 'running'
    assert captured['user_decisions'] == {'param_card': 'keep native width'}
