"""Task-level budget configuration; no provider or simulation calls."""
import json

import pytest

from nsbi_agent.interaction.tasks import create_task
from nsbi_agent.workflow import graph, runner
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore


@pytest.mark.parametrize('runtime,step_multiplier,token_multiplier', [
    ({}, 3, 1),
    ({'agent_budget_multiplier': 1}, 1, 1),
    ({'agent_budget_multiplier': 2}, 2, 2),
])
@pytest.mark.parametrize('stage,base', [
    ('plan_resolve', (10, 1800)), ('prepare_cards', (16, 2200)),
    ('dispositions', (6, 1400)), ('analyze_deliver', (14, 3000)), ('experience', (6, 1600)),
])
def test_stage_budget_controls_and_trace(tmp_path, monkeypatch, runtime, step_multiplier,
                                         token_multiplier, stage, base):
    captured = {}
    def fake_run(store, client, stage, prompt, toolbox, **kwargs):
        captured.update(kwargs)
        assert f'up to {base[0] * step_multiplier} model turns' in prompt
        return {'summary': 'done'}
    monkeypatch.setattr(graph, 'run_stage', fake_run)
    path = tmp_path / 'task.json'
    path.write_text(json.dumps({'task_id': 'budget-test', 'user_request': 'test',
        'workspace': str(PROJECT_ROOT / 'workspace/budget-test'), 'runtime': runtime}))
    store = TaskStore(tmp_path / 'store').create()
    controller = graph.TaskController(store, None, runner.load_task_file(path))
    controller._run_agent_stage({}, stage, 'same prompt')
    assert captured == {'max_steps': base[0] * step_multiplier, 'max_tokens': base[1] * token_multiplier}
    record = json.loads(store.trajectory_path.read_text().splitlines()[0])
    assert record['kind'] == 'stage_budget'
    assert record['max_steps'] == captured['max_steps'] and record['max_tokens'] == captured['max_tokens']


@pytest.mark.parametrize('value', [0, -1, 1.5, 'two'])
def test_reject_invalid_multiplier(tmp_path, value):
    path = tmp_path / 'task.json'
    path.write_text(json.dumps({'task_id': 'budget-test', 'user_request': 'test',
        'workspace': str(PROJECT_ROOT / 'workspace/budget-test'),
        'runtime': {'agent_budget_multiplier': value}}))
    with pytest.raises(runner.TaskFileError, match='agent_budget_multiplier'):
        runner.load_task_file(path)


def test_task_api_preserves_requested_budget(tmp_path):
    runtime = {'agent_budget_multiplier': 2, 'max_repairs': 4, 'job_timeout_seconds': 7200}
    path = create_task('  same physics request  ', root=tmp_path, runtime=runtime)
    payload = json.loads(path.read_text())
    assert payload['runtime'] == runtime
    assert payload['user_request'] == 'same physics request'


def test_unlimited_task_budget(tmp_path, monkeypatch):
    path = create_task('same physics', root=tmp_path,
                       runtime={'agent_budget_multiplier': None, 'max_repairs': None})
    # Task loading also enforces workspace confinement, so use an allowed path.
    task = json.loads(path.read_text())
    task['workspace'] = str(PROJECT_ROOT / 'workspace/budget-test')
    path.write_text(json.dumps(task))
    loaded = runner.load_task_file(path)
    captured = {}
    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return {'summary': 'done'}
    monkeypatch.setattr(graph, 'run_stage', fake_run)
    store = TaskStore(tmp_path / 'store').create()
    controller = graph.TaskController(store, None, loaded)
    controller._run_agent_stage({}, 'prepare_cards', 'same physics')
    assert captured == {'max_steps': None, 'max_tokens': None}
    assert controller._can_repair({'repairs': 1000})


def test_stage_can_continue_past_old_limit(tmp_path):
    from nsbi_agent.workflow.agent import run_stage
    from nsbi_agent.workflow.jobs import JobManager
    from nsbi_agent.workflow.tools import ToolBox
    store = TaskStore(tmp_path).create()
    class Client:
        calls = 0
        def chat(self, *args, **kwargs):
            self.calls += 1
            calls = [] if self.calls < 20 else [{'id': 'done', 'type': 'function', 'function': {
                'name': 'finish_stage', 'arguments': json.dumps({'stage': 'prepare_cards',
                                                               'complete': True, 'summary': 'done'})}}]
            return {'content': '', 'tool_calls': calls, 'usage': {}, 'model': 'fake', 'latency_s': 0}
    client = Client()
    result = run_stage(store, client, 'prepare_cards', 'test', ToolBox(store, JobManager(store)),
                       max_steps=None, max_tokens=None)
    assert result['complete'] and client.calls == 20


def test_unlimited_model_payload_uses_provider_default(monkeypatch):
    from nsbi_agent.workflow.model_client import ModelClient, ModelConfig
    client = ModelClient(ModelConfig(max_tokens=None))
    captured = []
    def post(payload):
        captured.append(payload)
        return {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': {}}
    monkeypatch.setattr(client, '_post', post)
    client.chat([{'role': 'user', 'content': 'test'}])
    assert 'max_tokens' not in captured[-1]
    client.chat([{'role': 'user', 'content': 'test'}], max_tokens=500)
    assert captured[-1]['max_tokens'] == 500
