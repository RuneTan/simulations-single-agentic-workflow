"""Trace contracts needed by later experience extraction; no provider calls."""
import base64
import json
from pathlib import Path
import pytest

from nsbi_agent.workflow import jobs, runner, tools
from nsbi_agent.workflow.agent import run_stage
from nsbi_agent.workflow.state import TaskStore
from nsbi_agent.workflow.trajectory import write_summary


def test_append_resume_and_image_fingerprint(tmp_path):
    store = TaskStore(tmp_path).create()
    store.event('user_question', question='choose a scale')
    TaskStore(tmp_path).trace('user_answer', answer='91.1876 GeV')
    store = TaskStore(tmp_path)
    store.trace('image', url='data:image/png;base64,' + base64.b64encode(b'png-data').decode())
    rows = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    assert [row['sequence'] for row in rows] == [1, 2, 3]
    assert all(row['schema_version'] == 1 for row in rows)
    assert rows[2]['url']['bytes'] == 8
    assert len(rows[2]['url']['sha256']) == 64


def test_full_tool_arguments_results_and_statistics(tmp_path):
    store = TaskStore(tmp_path).create()
    payload = 'kept in full ' * 800
    calls = [
        {'id': '1', 'type': 'function', 'function': {'name': 'write_file',
         'arguments': json.dumps({'path': 'long.txt', 'content': payload})}},
        {'id': '2', 'type': 'function', 'function': {'name': 'finish_stage',
         'arguments': json.dumps({'stage': 'prepare_cards', 'complete': True, 'summary': 'done'})}},
    ]

    class Client:
        def chat(self, messages, **kwargs):
            return {'content': '', 'tool_calls': calls, 'usage': {'prompt_tokens': 7, 'completion_tokens': 4},
                    'model': 'fake', 'latency_s': 0.25, 'finish_reason': 'tool_calls'}

    run_stage(store, Client(), 'prepare_cards', 'test prompt', tools.ToolBox(store, jobs.JobManager(store)))
    rows = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    assert json.loads(next(row for row in rows if row['kind'] == 'tool_result')['arguments'])['content'] == payload
    assert next(row for row in rows if row['kind'] == 'model_request')['messages'][1]['content'] == 'test prompt'
    assert next(row for row in rows if row['kind'] == 'model_response')['assistant']['tool_calls'] == calls
    summary = runner._finalise(store, {'task_id': 't', 'status': 'completed'}, None, 1.0)
    stats = json.loads((tmp_path / 'trajectory_summary.json').read_text())
    assert stats['records'] == len(store.trajectory_path.read_text().splitlines())
    assert (stats['model_calls'], stats['total_tokens'], stats['tool_calls']) == (1, 11, 2)
    assert stats['tools_by_name']['write_file'] == 1
    assert Path(summary['trajectory']['trajectory']).is_file()


def test_massive_particle_pair(tmp_path):
    from nsbi_agent.workflow.templates.analysis import read_events
    # Two particles at rest: the pair mass must include both rest masses.
    event = '''<LesHouchesEvents><init>
2212 2212 6500 6500 0 0 0 0 3 1
1.0 0.01 1.0 1
</init><event>
2 1 1.0 250 0.007 0.1
23 1 0 0 0 0 0 0 0 91.1876 91.1876 0 9
25 1 0 0 0 0 0 0 0 125.0 125.0 0 9
</event></LesHouchesEvents>'''
    path = tmp_path / 'events.lhe'
    path.write_text(event)
    events, xs, _ = read_events(str(path), [23, 25])
    assert events == [(216.1876, 1.0)] and xs == [1.0]
    assert read_events(str(path))[0] == [(None, 1.0)]


def test_checker_feedback_is_retained_in_trace(tmp_path):
    from nsbi_agent.workflow.checks import run_environment_check
    store = TaskStore(tmp_path).create()
    record = run_environment_check(store)
    row = json.loads(store.trajectory_path.read_text().splitlines()[-1])
    assert row['kind'] == 'check_result'
    assert row['result'] == json.loads(Path(record['path']).read_text())
    assert row['sha256'] == record['sha256']
    write_summary(store, {'task_id': 't'})
    stats = json.loads((tmp_path / 'trajectory_summary.json').read_text())
    assert stats['check_results_by_status'][record['status']] == 1


def test_image_follows_all_replies_in_tool_batch(tmp_path):
    store = TaskStore(tmp_path).create()

    class Box:
        pending_question = pending_image = finished = None
        def execute(self, name, arguments):
            if name == 'view_image':
                self.pending_image = {'path': 'plot.png', 'width': 1, 'height': 1,
                                      'data_url': 'data:image/png;base64,eA=='}
            if name == 'finish_stage':
                self.finished = {'stage': 'analyze_deliver', 'complete': True}
            return {'ok': True}

    class Client:
        calls = 0
        def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                names = ['view_image', 'read_file']
            else:
                assistant = next(i for i, m in enumerate(messages) if m.get('tool_calls'))
                assert [m['role'] for m in messages[assistant + 1:assistant + 4]] == ['tool', 'tool', 'user']
                names = ['finish_stage']
            return {'content': '', 'tool_calls': [
                {'id': str(i), 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}
                for i, name in enumerate(names)], 'usage': {}, 'latency_s': 0, 'model': 'fake'}

    run_stage(store, Client(), 'analyze_deliver', 'analyze plot', Box())


def test_pair_template_requires_distinct_ids(tmp_path, monkeypatch):
    from nsbi_agent.workflow.templates.analysis import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'analysis_config.json').write_text(json.dumps({'event_file': 'unused', 'pair_pdg_ids': [25, 25]}))
    with pytest.raises(SystemExit, match='distinct integer PDG IDs'):
        main()
