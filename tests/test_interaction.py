"""Numbered task entry, user questions, and real graph pause/resume controls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from nsbi_agent.interaction import tasks, terminal
from nsbi_agent.workflow import checks, experience, runner
from nsbi_agent.workflow.agent import UserInputRequested, run_stage
from nsbi_agent.workflow.graph import TaskController
from nsbi_agent.workflow.jobs import JobManager
from nsbi_agent.workflow.model_client import Usage
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore
from nsbi_agent.workflow.tools import ToolBox, ToolError


def payload(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reader(values):
    values = iter(values)

    def read(prompt):
        try:
            return next(values)
        except StopIteration:
            raise EOFError from None
    return read


def test_numbering_preserves_existing_tasks_and_uses_runner_contract(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    root = project / 'workspace/tasks'
    root.mkdir(parents=True)
    (root / 'task1').mkdir()
    (root / 'task3').mkdir()
    (root / 'T0-sm-baseline').mkdir()
    (root / 'task3/keep.txt').write_text('existing')
    path = tasks.create_task('生成一个 SM 样本', root)
    assert path == root / 'task4/task4.json'
    assert payload(path) == {'task_id': 'task4', 'user_request': '生成一个 SM 样本',
                              'workspace': str(path.parent)}
    assert (root / 'task3/keep.txt').read_text() == 'existing'
    monkeypatch.setattr(runner, 'PROJECT_ROOT', project)
    assert runner.load_task_file(path)['runtime'] == runner.DEFAULT_RUNTIME


def test_concurrent_allocations_have_unique_names_and_requests(tmp_path):
    root = tmp_path / 'tasks'
    with ThreadPoolExecutor(max_workers=4) as executor:
        paths = list(executor.map(lambda i: tasks.create_task(f'request {i}', root), range(12)))
    assert len(set(paths)) == 12
    assert {path.parent.name for path in paths} == {f'task{i}' for i in range(1, 13)}
    assert {payload(path)['user_request'] for path in paths} == {f'request {i}' for i in range(12)}


def test_empty_input_and_answers_do_not_change_files(tmp_path):
    with pytest.raises(ValueError):
        tasks.create_task('  ', tmp_path / 'tasks')
    assert not (tmp_path / 'tasks').exists()
    path = tasks.create_task('original request', tmp_path / 'tasks')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        tasks.save_answer(path, 'clarification_001', ' ')
    assert path.read_bytes() == before
    tasks.save_answer(path, 'clarification_001', '13 TeV')
    assert payload(path)['user_request'] == 'original request'
    assert payload(path)['user_decisions'] == {'clarification_001': '13 TeV'}
    assert tasks.task_path('1', tmp_path / 'tasks') == path
    with pytest.raises(ValueError):
        tasks.task_path('../task1', tmp_path / 'tasks')


def test_terminal_creates_asks_answers_polls_and_finishes_same_task(tmp_path):
    calls, messages, sleeps = [], [], []

    def run(path):
        calls.append(('run', path))
        assert payload(path)['user_request'] == '生成 SM 事件'
        return {'exit_code': 5, 'user_questions': [
            {'topic': 'clarification_001', 'question': '使用什么碰撞能量？',
             'choices': ['13 TeV', '14 TeV']}]}

    def resume(path):
        calls.append(('resume', path))
        assert payload(path)['user_decisions']['clarification_001'] == '14 TeV'
        if sum(name == 'resume' for name, _ in calls) == 1:
            return {'exit_code': 3, 'status': 'waiting_job'}
        return {'exit_code': 0, 'status': 'completed', 'task_id': 'task1',
                'workspace': str(Path(path).parent)}

    assert terminal.run_chat(tasks_root=tmp_path, input_fn=reader(['生成 SM 事件', '2', '/quit']),
                             output_fn=messages.append, run_fn=run, resume_fn=resume,
                             sleep_fn=sleeps.append) == 0
    assert [name for name, _ in calls] == ['run', 'resume', 'resume']
    assert len({path for _, path in calls}) == 1
    assert list(tmp_path.iterdir()) == [tmp_path / 'task1']
    assert sleeps == [2]
    assert any('使用什么碰撞能量' in value for value in messages)


def test_exit_during_question_keeps_pending_task_without_default_answer(tmp_path):
    messages = []
    terminal.run_chat(tasks_root=tmp_path, input_fn=reader(['a request', '/quit']),
                      output_fn=messages.append,
                      run_fn=lambda _: {'exit_code': 5, 'user_questions': [
                          {'topic': 'clarification_001', 'question': 'Which process?'}]},
                      resume_fn=lambda _: pytest.fail('Must wait for an answer'))
    path = tmp_path / 'task1/task1.json'
    assert path.is_file() and 'user_decisions' not in payload(path)
    assert any('/resume task1' in value for value in messages)


def test_terminal_resume_does_not_allocate_another_task(tmp_path):
    path = tasks.create_task('original', tmp_path)
    calls = []
    terminal.run_chat(tasks_root=tmp_path, input_fn=reader(['/tasks', '/resume task1', '/quit']),
                      output_fn=lambda _: None, run_fn=lambda _: pytest.fail('No new task'),
                      resume_fn=lambda name: calls.append(name) or {
                          'exit_code': 0, 'status': 'completed', 'task_id': 'task1'})
    assert calls == [str(path)] and not (tmp_path / 'task2').exists()


def test_output_index_keeps_plots_tables_and_event_data_in_the_task(tmp_path):
    path = tasks.create_task('generate', tmp_path)
    files = {}
    for name, relative in [('plot', 'analysis/plot.png'), ('table', 'analysis/analysis.json'),
                            ('events', 'attempts/001/Events/events.lhe')]:
        target = path.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('local artifact')
        files[name] = str(target)
    index = tasks.save_outputs(path, {'status': 'completed', 'delivery': files})
    assert index == path.parent / 'outputs.json' and payload(index)['files'] == files
    with pytest.raises(ValueError, match='当前工作区'):
        tasks.save_outputs(path, {'status': 'completed', 'delivery': {'plot': str(tmp_path / 'other.png')}})
    assert payload(index)['files'] == files


def response(*calls):
    return {'content': '', 'model': 'offline-control', 'latency_s': 0.0,
            'finish_reason': 'tool_calls', 'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
            'tool_calls': [{'id': f'call-{i}', 'type': 'function',
                            'function': {'name': name, 'arguments': json.dumps(arguments)}}
                           for i, (name, arguments) in enumerate(calls)]}


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.usage = Usage()
        self.inputs = []

    def chat(self, messages, tools=None, max_tokens=None):
        self.inputs.append({'messages': list(messages), 'tools': tools})
        answer = next(self.responses)
        self.usage.add(answer['usage'])
        return answer


def test_ask_user_pauses_before_other_tools_in_the_same_batch(tmp_path):
    store = TaskStore(tmp_path).create()
    toolbox = ToolBox(store, JobManager(store))
    client = Client([response(('ask_user', {'question': '哪一个过程？'}),
                              ('write_file', {'path': 'must-not-exist', 'content': 'x'}))])
    with pytest.raises(UserInputRequested) as paused:
        run_stage(store, client, 'plan_resolve', 'Clarify the process.', toolbox)
    assert paused.value.question['question'] == '哪一个过程？'
    assert not (tmp_path / 'must-not-exist').exists()
    assert any(t['function']['name'] == 'ask_user' for t in client.inputs[0]['tools'])
    assert (store.model_trace_dir / 'plan_resolve.messages.json').exists()


def test_question_tool_is_not_available_during_other_stages(tmp_path):
    store = TaskStore(tmp_path).create()
    toolbox = ToolBox(store, JobManager(store))
    toolbox.stage = 'prepare_cards'
    with pytest.raises(ToolError):
        toolbox.execute('ask_user', {'question': 'Question?'})
    client = Client([response(('finish_stage', {'stage': 'prepare_cards', 'complete': True,
                                               'summary': 'offline stage'}))])
    run_stage(store, client, 'prepare_cards', 'offline', toolbox)
    assert all(t['function']['name'] != 'ask_user' for t in client.inputs[0]['tools'])


def test_real_graph_pauses_resumes_planning_and_runs_request_checker(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    monkeypatch.setattr(runner, 'PROJECT_ROOT', project)
    monkeypatch.setattr(experience, 'ENTRY_DIR', tmp_path / 'empty-memory')
    path = tasks.create_task('生成 e+ e- > mu+ mu-，请先问我碰撞能量。', project / 'workspace/tasks')
    workspace = path.parent
    first = Client([response(('ask_user', {'question': '质心能量是多少？'}))])
    request = {'simulation': {'process': 'e+ e- > mu+ mu-',
                              'generation': {'beam_energies_GeV': [125.0, 125.0], 'beam_types': [0, 0]}}}
    second = Client([
        response(('write_file', {'path': 'request.json', 'content': json.dumps(request)}),
                 ('write_file', {'path': 'defaults.json', 'content': '{}'})),
        response(('finish_stage', {'stage': 'plan_resolve', 'complete': True,
                                  'artifacts': {'request': str(workspace / 'request.json'),
                                                'defaults': str(workspace / 'defaults.json')},
                                  'summary': 'Use 250 GeV from the user answer.'}))])
    clients = iter([first, second])
    monkeypatch.setattr(runner.ModelConfig, 'from_environment', lambda _: SimpleNamespace(public=lambda: {}))
    monkeypatch.setattr(runner, 'ModelClient', lambda _: next(clients))
    monkeypatch.setattr(checks, 'run_environment_check', lambda _: {
        'number': 1, 'status': 'succeeded', 'errors': [], 'summary': {}})
    original_check_spec = TaskController.node_check_spec

    def stop_after_real_check_spec(self, state):
        updates = original_check_spec(self, state)
        assert updates['checks']['spec']['status'] == 'succeeded'
        return {**updates, 'stage': 'done'}

    monkeypatch.setattr(TaskController, 'node_check_spec', stop_after_real_check_spec)
    paused = runner.run_task(str(path))
    assert paused['exit_code'] == 5 and paused['status'] == 'awaiting_user'
    assert paused['attempts'] == 0 and paused['repairs'] == 0
    topic = paused['user_questions'][0]['topic']
    # Resuming without an answer must not invoke the second client.
    assert runner.run_resume(str(path))['exit_code'] == 5
    tasks.save_answer(path, topic, '250 GeV')
    finished = runner.run_resume(str(path))
    assert finished['status'] == 'completed' and finished['human_interventions'] == 1
    assert finished['clarifications'][0]['answer'] == '250 GeV'
    assert finished['checks']['spec']['status'] == 'succeeded'
    prompt = second.inputs[0]['messages'][1]['content']
    assert '质心能量是多少' in prompt and '250 GeV' in prompt
    assert payload(path)['user_request'].endswith('请先问我碰撞能量。')


def test_chat_cli_opens_and_exits_without_creating_a_task():
    result = subprocess.run([str(PROJECT_ROOT / 'run.sh'), 'chat'], input='/quit\n',
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '自然语言任务窗口' in result.stdout and '已退出' in result.stdout
