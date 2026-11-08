"""``./run.sh task <task.json>`` and ``./run.sh resume <task.json>``.

The runner owns the outer lifecycle: validate the task file, create or reload
the task workspace, build the model client, drive the LangGraph controller,
write ``summary.json`` and return a documented exit code.  Background jobs
survive the foreground exit; ``resume`` re-enters the saved stage instead of
starting a second job.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from nsbi_agent.workflow.graph import TaskController
from nsbi_agent.workflow.model_client import ModelClient, ModelConfig
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore, utc_now
from nsbi_agent.workflow.trajectory import write_summary as write_trajectory_summary

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_WAITING = 3
EXIT_AWAITING_USER = 5

DEFAULT_RUNTIME = {'max_repairs': 2, 'cores': 2, 'job_timeout_seconds': 3600,
                   'agent_budget_multiplier': 1, 'gates_enabled': True}


class TaskFileError(ValueError):
    def __init__(self, message: str, exit_code: int = EXIT_INVALID):
        super().__init__(message)
        self.exit_code = exit_code


def load_task_file(path: str | Path) -> dict:
    task_path = Path(path).resolve()
    if not task_path.is_file():
        raise TaskFileError('task file not found: ' + str(task_path))
    try:
        task = json.loads(task_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as error:
        raise TaskFileError('invalid task JSON: ' + str(error)) from error
    if not isinstance(task, dict):
        raise TaskFileError('task file must contain a JSON object')
    for key in ('task_id', 'user_request', 'workspace'):
        value = task.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TaskFileError('task.%s must be a nonempty string' % key)
    workspace = Path(task['workspace'])
    if not workspace.is_absolute():
        raise TaskFileError('task.workspace must be an absolute path')
    allowed = (PROJECT_ROOT / 'workspace').resolve()
    if not workspace.resolve().is_relative_to(allowed):
        raise TaskFileError('task.workspace must live under %s' % allowed)
    runtime = dict(DEFAULT_RUNTIME)
    runtime.update(task.get('runtime') or {})
    if type(runtime['gates_enabled']) is not bool:
        raise TaskFileError('task.runtime.gates_enabled must be a boolean')
    for key in ('max_repairs', 'cores', 'job_timeout_seconds', 'agent_budget_multiplier'):
        if key in ('max_repairs', 'agent_budget_multiplier') and runtime.get(key) is None:
            continue
        if not isinstance(runtime.get(key), int) or runtime[key] <= 0:
            raise TaskFileError('task.runtime.%s must be a positive integer' % key)
    task['runtime'] = runtime
    task['task_file'] = str(task_path)
    for key, default in (('fixtures', []), ('experience', {}), ('analysis', {}),
                         ('task_type', 'generation')):
        task.setdefault(key, default)
    if not isinstance(task['fixtures'], list) or not isinstance(task['experience'], dict):
        raise TaskFileError('task.fixtures / task.experience have the wrong type')
    decisions = task.get('user_decisions', {})
    if not isinstance(decisions, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                                  for key, value in decisions.items()):
        raise TaskFileError('task.user_decisions must be an object of topic -> decision text')
    task['user_decisions'] = decisions
    return task


def initial_state(task: dict) -> dict:
    return {
        'task_id': task['task_id'],
        'task_file': task['task_file'],
        'user_request': task['user_request'],
        'workspace': str(Path(task['workspace']).resolve()),
        'runtime': task['runtime'],
        'fixtures': task['fixtures'],
        'experience_policy': task['experience'],
        'analysis': task['analysis'],
        'task_type': task.get('task_type', 'generation'),
        'stage': 'allocate',
        'status': 'running',
        'exit_code': None,
        'message': 'created',
        'checks': {},
        'check_history': [],
        'dispositions': [],
        'dispositioned': [],
        'user_questions': [],
        'user_decisions': dict(task.get('user_decisions') or {}),
        'question_return': None,
        'clarifications': [],
        'human_interventions': 0,
        'repairs': 0,
        'attempts': 0,
        'spec_version': 0,
        'card_version': 0,
        'check3_count': 0,
        'job_launches': 0,
        'timing': {'stages': {}},
        'started_at': utc_now(),
    }


def run_task(task_path: str) -> dict:
    try:
        task = load_task_file(task_path)
        store = TaskStore(task['workspace'])
        if store.exists():
            raise TaskFileError('workspace already has state; use `./run.sh resume %s`'
                                % task['task_file'], EXIT_FAILED)
        store.create()
    except TaskFileError as error:
        return _error_summary(task_path, str(error), error.exit_code)
    return _execute(task, store, initial_state(task))


def run_resume(task_path: str) -> dict:
    try:
        task = load_task_file(task_path)
        store = TaskStore(task['workspace'])
        if not store.exists():
            raise TaskFileError('no checkpoint in %s; use `./run.sh task` first'
                                % task['workspace'], EXIT_FAILED)
        state = store.load_state()
        if state.get('status') in ('completed', 'failed'):
            return _finalise(store, dict(state), None, 0.0)
        # A resume picks up the current task file (runtime may be extended),
        # but keeps the original request and history from the checkpoint.
        state['runtime'] = task['runtime']
        state['fixtures'] = task['fixtures']
        state['experience_policy'] = task['experience']
        state['task_file'] = task['task_file']
        if state.get('stage') == 'wait':
            state['stage'] = 'await_job'
        if state.get('stage') == 'awaiting_user':
            # Audit F2: a paused task continues only when the task file now carries
            # a decision for every pending question; decisions are passed to the
            # dispositions node, which records them without another model call.
            decided = {topic: answer for topic, answer in (task.get('user_decisions') or {}).items()
                       if answer.strip()}
            wanted = {item.get('topic') for item in state.get('user_questions') or []}
            if wanted and wanted <= set(decided):
                state['human_interventions'] = int(state.get('human_interventions') or 0) + len(wanted)
                if state.get('question_return') == 'plan_resolve':
                    answers = [{'topic': question['topic'], 'question': question['question'],
                                'answer': decided[question['topic']]}
                               for question in state['user_questions']]
                    state['clarifications'] = list(state.get('clarifications') or []) + answers
                    for answer in answers:
                        store.event('user_answer', stage='plan_resolve', **answer)
                    state['stage'] = 'plan_resolve'
                    state['user_questions'] = []
                    state['question_return'] = None
                else:
                    state['user_decisions'] = {topic: decided[topic] for topic in wanted}
                    state['stage'] = 'dispositions'
                state['status'] = 'running'
                state['exit_code'] = None
                state['message'] = 'user answer received; continuing the task'
            else:
                return _finalise(store, dict(state), None, 0.0)
    except TaskFileError as error:
        return _error_summary(task_path, str(error), error.exit_code)
    return _execute(task, store, state)


def _execute(task: dict, store: TaskStore, state: dict) -> dict:
    started = time.time()
    store.trace('execution_started', task_id=task['task_id'], stage=state.get('stage'),
                task_file=task.get('task_file'), user_request=state.get('user_request'),
                runtime=task['runtime'])
    config = ModelConfig.from_environment(PROJECT_ROOT)
    if task['runtime'].get('agent_budget_multiplier', 1) is None:
        config.max_tokens = None  # Omit max_tokens; use the model service's default.
    client = ModelClient(config)
    state['model'] = config.public()
    controller = TaskController(store, client, task)
    try:
        final = controller.start(state)
    except Exception as error:  # noqa: BLE001 - the runner must always leave a record
        store.trace('execution_error', error_type=type(error).__name__, error=str(error))
        final = store.load_state() if store.exists() else dict(state)
        final.update(status='failed', exit_code=EXIT_FAILED, stage='failed',
                     message='%s: %s' % (type(error).__name__, error))
        store.save_state(final)
    final['usage'] = _merge_usage(final.get('usage'), client.usage.summary())
    store.save_state(final)
    elapsed = round(time.time() - started, 2)
    return _finalise(store, final, client, elapsed)


def _finalise(store: TaskStore, state: dict, client: ModelClient | None,
              elapsed: float, *, write: bool | None = None) -> dict:
    exit_code = _exit_code(state)
    timing = dict(state.get('timing') or {})
    checks = state.get('checks') or {}
    check_history = state.get('check_history') or []
    checker_seconds = round(sum(float(record.get('elapsed_s') or 0)
                                for record in check_history), 2)
    usage = state.get('usage') or (client.usage.summary() if client else {})
    task_elapsed = _task_elapsed_seconds(state) or (elapsed if elapsed else None)
    summary = {
        'task_id': state.get('task_id'),
        'task_file': state.get('task_file'),
        'workspace': state.get('workspace'),
        'status': state.get('status'),
        'stage': state.get('stage'),
        'exit_code': exit_code,
        'message': state.get('message'),
        'model': state.get('model'),
        'runtime': state.get('runtime'),
        'checks': {key: {'status': value.get('status'), 'path': value.get('path'),
                         'sha256': value.get('sha256'), 'summary': value.get('summary'),
                         'review_items': value.get('review_items')}
                   for key, value in checks.items()},
        'check_history': [{key: record.get(key) for key in
                           ('number', 'version', 'status', 'phase', 'path', 'sha256')}
                          for record in state.get('check_history') or []],
        'dispositions': state.get('dispositions') or [],
        'user_questions': state.get('user_questions') or [],
        'user_decisions': state.get('user_decisions') or {},
        'clarifications': state.get('clarifications') or [],
        'attempts': state.get('attempts'),
        'repairs': state.get('repairs'),
        'human_interventions': state.get('human_interventions', 0),
        'job': {key: (state.get('job') or {}).get(key) for key in
                ('job_id', 'status', 'exit_code', 'elapsed_s', 'command', 'cwd', 'log')},
        'job_launches': state.get('job_launches'),
        'fixture': state.get('fixture'),
        'cards': state.get('cards'),
        'experience': _experience_summary(state.get('experience') or {}),
        'delivery': state.get('delivery'),
        'request_sha256': state.get('request_sha256'),
        'intent_sha256': state.get('intent_sha256'),
        'timing': {
            'elapsed_wall_s': task_elapsed,
            'segment_elapsed_wall_s': elapsed if elapsed else None,
            'generator_wall_s': timing.get('generator_wall_s'),
            'checker_s': checker_seconds,
            'stage_seconds': timing.get('stages'),
            'started_at': state.get('started_at'),
            'updated_at': state.get('updated_at'),
        },
        'usage': usage,
        'analysis_refresh': state.get('analysis_refresh'),
        'summary_path': str(store.workspace / 'summary.json'),
    }
    should_write = bool(elapsed) if write is None else write
    summary['trajectory'] = {'trajectory': str(store.trajectory_path),
                             'trajectory_summary': str(store.workspace / 'trajectory_summary.json')}
    if should_write:
        store.write_json('summary.json', summary)
        store.event('summary_written', status=summary['status'], exit_code=exit_code)
    state['summary'] = summary
    state['exit_code'] = exit_code
    store.save_state(state)
    write_trajectory_summary(store, summary)
    return summary


def _merge_usage(previous: dict | None, current: dict) -> dict:
    merged = dict(previous or {})
    for key, value in (current or {}).items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] = merged[key] + value
        else:
            merged[key] = value
    return merged


def _task_elapsed_seconds(state: dict) -> float | None:
    """Wall time from the first foreground start to the current checkpoint."""
    import datetime as _dt
    try:
        started = _dt.datetime.strptime(state.get('started_at'), '%Y-%m-%dT%H:%M:%SZ')
        updated = _dt.datetime.strptime(state.get('updated_at'), '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        return None
    return round((updated - started).total_seconds(), 2)


def _exit_code(state: dict) -> int:
    status = state.get('status')
    if status == 'completed':
        return EXIT_COMPLETED
    if status == 'awaiting_user':
        return EXIT_AWAITING_USER
    if status == 'waiting_job' or state.get('stage') in ('wait', 'await_job'):
        return EXIT_WAITING
    return EXIT_FAILED


def _error_summary(task_path: str, message: str, exit_code: int) -> dict:
    return {'task_id': None, 'task_file': str(Path(task_path).resolve()), 'workspace': None,
            'status': 'invalid' if exit_code == EXIT_INVALID else 'failed', 'stage': 'failed',
            'exit_code': exit_code, 'message': message, 'checks': {}, 'dispositions': [],
            'timing': {'elapsed_wall_s': 0}}


def _experience_summary(experience: dict) -> dict:
    retrieval = experience.get('retrieval') or {}
    return {
        'query': retrieval.get('query'),
        'matched': [{key: item.get(key) for key in ('entry_sha256', 'score', 'path')}
                    for item in retrieval.get('matched') or []],
        'content_supplied': retrieval.get('content'),
        'used_by_model': experience.get('used_by_model'),
        'stage_usage': experience.get('stage_usage') or [],
        'first_check3': experience.get('first_check3'),
        'candidate': experience.get('candidate'),
        'admission': experience.get('admission'),
        'persisted': experience.get('persisted'),
        'check5_path': (experience.get('check5') or {}).get('path'),
        'check5_status': (experience.get('check5') or {}).get('status'),
    }
