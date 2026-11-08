"""LangGraph stage graph for one single-Agent task.

The graph owns the *control* flow only: allocate workspace, run checker 1,
Agent resolves the request, run checker 2, Agent prepares cards, run checker 3,
launch a background job, await it, run checker 4 (+ recorded-banner check),
Agent analyses and delivers, optional experience admission via checker 5.
Every node persists the whole task state; ``resume`` re-enters the graph at the
saved stage, so a foreground exit after ``launch`` does not re-run anything.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from nsbi_agent.workflow import checks, experience
from nsbi_agent.workflow.agent import StageError, UserInputRequested, run_stage
from nsbi_agent.workflow.jobs import JobManager, load_job
from nsbi_agent.workflow.model_client import ModelClient
from nsbi_agent.workflow.prompts import (stage_analysis, stage_dispositions, stage_experience,
                                         stage_plan, stage_prepare_cards)
from nsbi_agent.workflow.state import (PROJECT_ROOT, TaskState, TaskStore, json_fingerprint,
                                       read_json, sha256_file, utc_now)
from nsbi_agent.workflow.tools import ToolBox

NODE_NAMES = ('allocate', 'check_environment', 'plan_resolve', 'check_spec',
              'prepare_cards', 'inject_fixture', 'check_cards', 'dispositions',
              'launch', 'await_job', 'check_sample', 'analyze_deliver',
              'experience', 'done', 'failed', 'awaiting_user')


class TaskController:
    """Node implementations plus the state bookkeeping they share."""

    def __init__(self, store: TaskStore, client: ModelClient, task: dict):
        self.store = store
        self.client = client
        self.task = task
        self.jobs = JobManager(store)

    # -- helpers ---------------------------------------------------------
    @property
    def gates_enabled(self) -> bool:
        return (self.task.get('runtime') or {}).get('gates_enabled', True)

    @property
    def max_repairs(self) -> int | None:
        value = (self.task.get('runtime') or {}).get('max_repairs', 2)
        return int(value) if value is not None else None

    def _can_repair(self, state: TaskState) -> bool:
        return self.max_repairs is None or int(state.get('repairs') or 0) < self.max_repairs

    @property
    def job_timeout(self) -> int:
        return int((self.task.get('runtime') or {}).get('job_timeout_seconds', 3600))

    def _record_check(self, state: TaskState, key: str, record: dict) -> dict:
        checks_map = dict(state.get('checks') or {})
        checks_map[key] = record
        history = list(state.get('check_history') or [])
        history.append(record)
        return {'checks': checks_map, 'check_history': history}

    def _fail(self, message: str) -> dict:
        return {'stage': 'failed', 'status': 'failed', 'exit_code': 1, 'message': message}

    @staticmethod
    def _inside(path: str | None, root: Path) -> Path:
        if not path:
            raise StageError('missing artifact path')
        candidate = Path(path)
        if not candidate.is_absolute():
            raise StageError('artifact path must be absolute: ' + path)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise StageError('artifact outside the task workspace: ' + str(resolved))
        if not resolved.is_file():
            raise StageError('artifact does not exist: ' + str(resolved))
        return resolved

    def _run_agent_stage(self, state: TaskState, stage: str, prompt: str) -> dict:
        toolbox = ToolBox(self.store, self.jobs)
        budgets = {'prepare_cards': (16, 2200), 'analyze_deliver': (14, 3000),
                   'plan_resolve': (10, 1800), 'dispositions': (6, 1400),
                   'experience': (6, 1600)}
        max_steps, max_tokens = budgets.get(stage, (10, 1600))
        multiplier = (self.task.get('runtime') or {}).get('agent_budget_multiplier', 1)
        if multiplier is None:
            max_steps = max_tokens = None
            prompt = ('Runtime: no local model-turn, output-token or repair budgets for this task. '
                      'The efficiency targets below are guidance. Complete the stage.\n\n' + prompt)
        else:
            max_steps, max_tokens = max_steps * multiplier, max_tokens * multiplier
        self.store.trace('stage_budget', stage=stage, multiplier=multiplier,
                         max_steps=max_steps, max_tokens=max_tokens)
        options = {'max_steps': max_steps, 'max_tokens': max_tokens}
        if not self.gates_enabled:
            options['gates_enabled'] = False
        finished = run_stage(self.store, self.client, stage, prompt, toolbox, **options)
        self.store.event('stage_finished', stage=stage, summary=finished.get('summary', '')[:800])
        return finished

    # -- post-audit helpers ---------------------------------------------
    def _review_resolved(self, state: TaskState, record: dict) -> bool:
        """True only if an earlier disposition covers this exact check record.

        Audit F2: dispositions are bound to the check number, version and record
        hash; a new check version with new topics is never covered by an older
        disposition for the same tool number.
        """
        digest = record.get('sha256')
        if not digest:
            return False
        topics = {group.get('topic') for group in record.get('review_items') or []}
        if not topics:
            return False
        for item in state.get('dispositions') or []:
            if item.get('check_number') != record.get('number'):
                continue
            if item.get('check_sha256') != digest:
                continue
            covered = set(item.get('topics') or [item.get('topic')])
            if topics <= covered:
                return True
        return False

    @staticmethod
    def _owner_stage(number) -> str:
        return {2: 'plan_resolve', 3: 'prepare_cards', 4: 'prepare_cards'}.get(number, 'prepare_cards')

    @staticmethod
    def _experience_retrieval(state: TaskState) -> dict | None:
        retrieval = (state.get('experience') or {}).get('retrieval') or {}
        return retrieval if retrieval.get('content') else None

    def _record_experience_stage(self, state: TaskState, stage: str, finished: dict) -> dict:
        """Record whether the supplied experience was actually applied in a stage (F5)."""
        retrieval = self._experience_retrieval(state)
        if not retrieval:
            return {}
        entry = dict(state.get('experience') or {})
        usage = list(entry.get('stage_usage') or [])
        summary = str(finished.get('summary') or '')
        matched = retrieval.get('matched') or []
        applied = None
        for item in matched:
            if item['entry_sha256'][:12] in summary:
                applied = item['entry_sha256']
                break
        usage.append({'stage': stage, 'entry_sha256': applied or matched[0]['entry_sha256'],
                      'applied': bool(applied), 'summary': summary[:300]})
        entry['stage_usage'] = usage
        return {'experience': entry}

    def _verified_configuration(self, state: TaskState) -> dict:
        """Authoritative configuration for the analysis summary (audit F1/F4)."""
        request = state.get('request') or {}
        simulation = request.get('simulation') or {}
        generation = simulation.get('generation') or {}
        eft = simulation.get('eft') or {}
        checks = state.get('checks') or {}
        recorded_run = {}
        recorded_params = {}
        run_record = checks.get('cards_run_record') or {}
        try:
            stored = read_json(run_record['path']) if run_record.get('path') else {}
        except (OSError, ValueError):
            stored = {}
        data = stored.get('data') or {}
        run = data.get('actual_run_settings') or {}
        for key in ('nevents', 'iseed', 'lpp1', 'lpp2', 'ebeam1', 'ebeam2', 'pdlabel',
                    'lhaid', 'fixed_ren_scale', 'fixed_fac_scale', 'scale',
                    'dsqrt_q2fact1', 'dsqrt_q2fact2', 'ptl', 'ptlmax', 'etal', 'etalmin',
                    'mmll', 'mmllmax', 'bwcutoff', 'maxjetflavor'):
            if key in run:
                recorded_run[key] = run[key]
        params = data.get('actual_parameters') or {}
        if 'LambdaSMEFT' in params:
            recorded_params['LambdaSMEFT'] = params['LambdaSMEFT']
        coefficients = {}
        for name in (eft.get('coefficients') or {}):
            if name in params:
                coefficients[name] = params[name]
        if coefficients:
            recorded_params['coefficients'] = coefficients
        sample_reading = {}
        sample_check = checks.get('sample') or {}
        try:
            sample_stored = read_json(sample_check['path']) if sample_check.get('path') else {}
        except (OSError, ValueError):
            sample_stored = {}
        sample_data = sample_stored.get('data') or {}
        if sample_data:
            sample_reading = {'events': sample_data.get('events'),
                              'nominal': sample_data.get('nominal'),
                              'init_cross_section_pb': [
                                  row.get('reported_cross_section_pb')
                                  for row in (sample_data.get('init') or {}).get('processes') or []]}
        beams = generation.get('beam_energies_GeV') or []
        process = simulation.get('process')
        label = 'p p > e+ e-' if isinstance(process, str) else 'parton process'
        if isinstance(process, dict):
            label = ' '.join(process.get('initial_state') or []) + ' > ' + \
                    ' '.join(process.get('final_state') or [])
        if len(beams) == 2:
            label += ' @ %g+%g GeV' % (float(beams[0]), float(beams[1]))
        return {
            'sample_label': label,
            'process': process,
            'model': simulation.get('model'),
            'production': request.get('production'),
            'generation': {key: generation.get(key) for key in
                           ('nevents', 'seed', 'beam_energies_GeV', 'beam_types',
                            'pdf', 'cuts')},
            'eft': eft,
            'recorded_run_card': recorded_run,
            'recorded_param_card': recorded_params,
            'sample_reading': sample_reading,
            'scope_note': ('These values come from the request and the retained checker '
                           '2/3/4 records; describe only these settings.' if self.gates_enabled else
                           'Requested setup only. Inspect native cards/banner and analysis outputs '
                           'to establish actual values; automatic checker reports are disabled.'),
        }

    def _stage_seconds(self, state: TaskState, stage: str, started: float) -> dict:
        timing = dict(state.get('timing') or {})
        stages = dict(timing.get('stages') or {})
        stages[stage] = round(stages.get(stage, 0.0) + time.time() - started, 2)
        timing['stages'] = stages
        return timing

    # -- nodes -----------------------------------------------------------
    def node_allocate(self, state: TaskState) -> dict:
        self.store.create()
        (self.store.workspace / 'request.txt').write_text(state['user_request'], encoding='utf-8')
        self.store.write_json('task.snapshot.json', self.task)
        query = {
            'task': self.task.get('task_type', 'generation'),
            'backend': 'madgraph 2.9.27',
            'model': 'SMEFTsim U35 MwScheme 3.0.2 full restriction',
            'beams': 'proton proton 6500 GeV',
            'request': state['user_request'][:400],
        }
        retrieval = experience.retrieve(self.store, query)
        return {
            'stage': 'check_environment' if self.gates_enabled else 'plan_resolve',
            'status': 'running',
            'original_request_sha256': sha256_file(self.store.workspace / 'request.txt'),
            'experience': {'retrieval': retrieval, 'used_by_model': None},
            'started_at': state.get('started_at') or utc_now(),
            'message': 'workspace allocated; experience retrieved',
        }

    def node_check_environment(self, state: TaskState) -> dict:
        record = checks.run_environment_check(self.store)
        updates = self._record_check(state, 'environment', record)
        if record['status'] == 'succeeded':
            updates['stage'] = 'plan_resolve'
        else:
            updates.update(self._fail('checker 1 failed: ' + '; '.join(record['errors'])))
        return updates

    def node_plan_resolve(self, state: TaskState) -> dict:
        feedback = state.get('repair_feedback')
        feedback = feedback['record'] if feedback and feedback.get('check') == 2 else None
        retrieval = (state.get('experience') or {}).get('retrieval')
        prompt = stage_plan(state['user_request'], state['workspace'], retrieval, feedback,
                            state.get('clarifications'))
        try:
            finished = self._run_agent_stage(state, 'plan_resolve', prompt)
        except UserInputRequested as pause:
            question = {'topic': 'clarification_%03d' % (len(state.get('clarifications') or []) + 1),
                        **pause.question}
            self.store.event('user_question', stage='plan_resolve', **question)
            return {'stage': 'awaiting_user', 'status': 'awaiting_user', 'exit_code': 5,
                    'question_return': 'plan_resolve', 'user_questions': [question],
                    'message': question['question']}
        artifacts = finished.get('artifacts') or {}
        request_path = self._inside(artifacts.get('request'), self.store.workspace)
        defaults_path = self._inside(artifacts.get('defaults'), self.store.workspace)
        request = read_json(request_path)
        if not isinstance(request.get('simulation'), dict):
            raise StageError('request.json must contain a simulation object')
        summary = str(finished.get('summary') or '')
        retrieval = state.get('experience') or {}
        matched = (retrieval.get('retrieval') or {}).get('matched') or []
        used = any(item['entry_sha256'][:12] in summary for item in matched)
        updates = {
            'request': request,
            'request_file': str(request_path),
            'defaults': read_json(defaults_path),
            'plan_summary': summary,
            'request_sha256': json_fingerprint(request),
            'experience': {**retrieval, 'used_by_model': used, 'plan_summary': summary},
            'stage': 'check_spec' if self.gates_enabled else 'prepare_cards',
            'repair_feedback': None,
        }
        updates.update(self._record_experience_stage({**state, **updates}, 'plan_resolve', finished))
        return updates

    def node_check_spec(self, state: TaskState) -> dict:
        version = int(state.get('spec_version') or 0) + 1
        record = checks.run_spec_check(self.store, state, version)
        updates = self._record_check(state, 'spec', record)
        updates['spec_version'] = version
        if record.get('intent_sha256'):
            updates['intent_sha256'] = record['intent_sha256']
        status = record['status']
        if status == 'succeeded':
            updates.update(stage='prepare_cards', repair_feedback=None)
        elif status == 'failed':
            if self._can_repair(state):
                updates.update(repairs=int(state.get('repairs') or 0) + 1,
                               stage='plan_resolve',
                               repair_feedback={'check': 2, 'record': record})
            else:
                updates.update(self._fail('checker 2 failed and the repair budget is exhausted'))
        elif self._review_resolved(state, record):
            updates['stage'] = 'prepare_cards'
        else:
            updates.update(stage='dispositions', pending_review=record,
                           disposition_return='prepare_cards')
        return updates

    def node_prepare_cards(self, state: TaskState) -> dict:
        attempt = int(state.get('attempts') or 0) + 1
        feedback = state.get('repair_feedback')
        feedback = feedback['record'] if feedback and feedback.get('check') in (3, 4) else None
        prompt = stage_prepare_cards(state['request'], state['workspace'], attempt, feedback,
                                     self._experience_retrieval(state))
        finished = self._run_agent_stage(state, 'prepare_cards', prompt)
        artifacts = finished.get('artifacts') or {}
        cards = {name: str(self._inside(artifacts.get(name), self.store.workspace))
                 for name in ('param_card', 'run_card', 'process_card')}
        proposed = finished.get('proposed_job') or {}
        script = self._inside(proposed.get('script'), self.store.workspace)
        cwd = Path(proposed.get('cwd') or '').resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(self.store.workspace):
            raise StageError('proposed job working directory must be a task workspace directory')
        expected = [str(Path(item).resolve()) for item in (proposed.get('expected_outputs') or [])]
        if any(not Path(path).is_relative_to(self.store.workspace) for path in expected):
            raise StageError('job outputs must stay inside the task workspace')
        updates = {
            'attempts': attempt,
            'card_version': attempt,
            'cards': cards,
            'proposed_job': {'script': str(script), 'cwd': str(cwd), 'expected_outputs': expected,
                             'command': 'bash ' + str(script)},
            'job': {},
            'stage': 'inject_fixture' if self.gates_enabled else 'launch',
            'repair_feedback': None,
            'message': str(finished.get('summary') or ''),
        }
        updates.update(self._record_experience_stage({**state, **updates}, 'prepare_cards', finished))
        return updates

    def _disposition_context(self, state: TaskState) -> dict:
        """Compact factual context so dispositions describe this task, not another one."""
        request = state.get('request') or {}
        simulation = request.get('simulation') or {}
        cards = state.get('cards') or {}
        context = {
            'request_eft': simulation.get('eft'),
            'request_generation': {key: value for key, value in
                                   (simulation.get('generation') or {}).items()
                                   if key in ('nevents', 'seed', 'beam_energies_GeV', 'cuts')},
            'request_production': request.get('production'),
        }
        process_card = Path(cards.get('process_card') or '')
        if process_card.is_file():
            context['process_card_lines'] = [line for line in
                                             process_card.read_text(encoding='utf-8').splitlines()
                                             if line.strip() and not line.startswith('output')]
        param_card = Path(cards.get('param_card') or '')
        if param_card.is_file():
            nonzero = []
            for line in param_card.read_text(encoding='utf-8').splitlines():
                fields = line.split('#', 1)[0].split()
                if len(fields) >= 2 and not line.lstrip().upper().startswith(('BLOCK', 'DECAY')):
                    try:
                        value = float(fields[-1])
                    except ValueError:
                        continue
                    if value != 0.0:
                        nonzero.append(line.strip())
            context['nonzero_parameters'] = nonzero[:40]
        return context

    def node_inject_fixture(self, state: TaskState) -> dict:
        fixtures = state.get('fixtures') or []
        if not fixtures or state.get('fixture'):
            return {'stage': 'check_cards'}
        fixture = dict(fixtures[0])
        if fixture.get('type') != 'run_card_beam_mismatch':
            return {'stage': 'check_cards', 'fixture': {'type': fixture.get('type'), 'applied': False,
                                                        'reason': 'unknown fixture type'}}
        run_card = Path(state['cards']['run_card'])
        before = run_card.read_text(encoding='utf-8')
        field = fixture.get('field', 'ebeam1')
        old_value, new_value = float(fixture.get('from', 6500.0)), float(fixture.get('to', 7000.0))
        changed, found = before, False
        lines = []
        for line in before.splitlines():
            if not found and line.split('=', 1)[0].strip():
                value = line.split('=', 1)[0].strip()
                try:
                    numeric = float(value)
                except ValueError:
                    numeric = None
                if numeric == old_value and field in line.split('=', 1)[1]:
                    line = line.replace(value, repr(new_value), 1)
                    found = True
            lines.append(line)
        if not found:
            raise StageError('fixture could not find %s=%s in the run card' % (field, old_value))
        changed = '\n'.join(lines) + '\n'
        fixture_dir = self.store.workspace / 'fixtures' / '001'
        fixture_dir.mkdir(parents=True, exist_ok=True)
        (fixture_dir / 'run_card_before.dat').write_text(before, encoding='utf-8')
        (fixture_dir / 'run_card_after.dat').write_text(changed, encoding='utf-8')
        (fixture_dir / 'diff.txt').write_text(_line_diff(before, changed), encoding='utf-8')
        run_card.write_text(changed, encoding='utf-8')
        record = {'type': fixture.get('type'), 'field': field, 'from': old_value, 'to': new_value,
                  'applied_at': utc_now(), 'run_card': str(run_card),
                  'before_sha256': sha256_file(fixture_dir / 'run_card_before.dat'),
                  'after_sha256': sha256_file(fixture_dir / 'run_card_after.dat'),
                  'diff': str(fixture_dir / 'diff.txt')}
        (fixture_dir / 'fixture.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        self.store.event('fault_injected', **record)
        return {'fixture': record, 'stage': 'check_cards',
                'cards': {**state['cards'],
                          'run_card': str(run_card),
                          'run_card_sha256': sha256_file(run_card)}}

    def node_check_cards(self, state: TaskState) -> dict:
        version = int(state.get('card_version') or state.get('attempts') or 1)
        record = checks.run_card_check(self.store, state, version, 'configuration')
        updates = self._record_check(state, 'cards', record)
        updates['check3_count'] = int(state.get('check3_count') or 0) + 1
        updates['cards_checked_sha256'] = (record.get('bindings') or {}).get('artifact_sha256')
        snapshot = self._snapshot_cards(state, version)
        if snapshot:
            updates['cards_snapshot'] = snapshot
            self.store.event('cards_frozen', version=version,
                             sha256={name: item['sha256'] for name, item in snapshot.items()})
        if updates['check3_count'] == 1:
            exp = dict(state.get('experience') or {})
            exp['first_check3'] = {key: record.get(key) for key in
                                   ('status', 'path', 'sha256', 'summary', 'review_items', 'failed_checks')}
            updates['experience'] = exp
        status = record['status']
        if status == 'succeeded':
            updates['stage'] = 'launch'
        elif status == 'failed':
            if self._can_repair(state):
                updates.update(repairs=int(state.get('repairs') or 0) + 1, stage='prepare_cards',
                               repair_feedback={'check': 3, 'record': record})
            else:
                updates.update(self._fail('checker 3 failed and the repair budget is exhausted'))
        elif self._review_resolved(state, record):
            updates['stage'] = 'launch'
        else:
            updates.update(stage='dispositions', pending_review=record, disposition_return='launch')
        return updates

    def node_dispositions(self, state: TaskState) -> dict:
        """Record review dispositions; accepted advances, repaired rechecks, deferred pauses."""
        record = state.get('pending_review') or {}
        wanted = {group.get('topic') for group in record.get('review_items') or []}
        user_decisions = {topic: text for topic, text in (state.get('user_decisions') or {}).items()
                          if isinstance(text, str) and text.strip()}
        if wanted and wanted <= set(user_decisions):
            finished = {
                'dispositions': [
                    {'topic': topic, 'disposition': 'accepted', 'reason': user_decisions[topic],
                     'evidence': [], 'decided_by': 'user'}
                    for topic in sorted(wanted)],
                'summary': 'Recorded the user decision for: ' + ', '.join(sorted(wanted)),
            }
        else:
            prompt = stage_dispositions(record, self._disposition_context(state),
                                       self._experience_retrieval(state), user_decisions or None)
            finished = self._run_agent_stage(state, 'dispositions', prompt)
        dispositions = finished.get('dispositions') or []
        if not isinstance(dispositions, list) or not dispositions:
            raise StageError('needs_review requires at least one recorded disposition')
        topics = [item.get('topic') for item in dispositions if isinstance(item, dict)]
        missing = wanted - set(topics)
        if missing:
            raise StageError('dispositions missing review topics: ' + ', '.join(sorted(missing)))
        valid = {'accepted', 'repaired', 'deferred'}
        for item in dispositions:
            if item.get('disposition') not in valid:
                raise StageError('invalid disposition %r for %s' % (item.get('disposition'),
                                                                   item.get('topic')))
        history = list(state.get('dispositions') or [])
        for item in dispositions:
            history.append({**item,
                            'check_number': record.get('number'),
                            'check_version': record.get('version'),
                            'check_phase': record.get('phase'),
                            'check_path': record.get('path'),
                            'check_sha256': record.get('sha256'),
                            'topics': list(wanted),
                            'recorded_at': utc_now()})
        deferred = [item for item in dispositions if item.get('disposition') == 'deferred']
        repaired = [item for item in dispositions if item.get('disposition') == 'repaired']
        base = {'dispositions': history,
                'message': str(finished.get('summary') or '')}
        base.update(self._record_experience_stage({**state, **base}, 'dispositions', finished))
        if deferred:
            questions = [{'topic': item.get('topic'), 'reason': item.get('reason'),
                          'evidence': item.get('evidence') or [],
                          'check_path': record.get('path'),
                          'check_sha256': record.get('sha256')} for item in deferred]
            return {**base,
                    'pending_review': record,
                    'user_questions': questions,
                    'status': 'awaiting_user',
                    'stage': 'awaiting_user',
                    'message': 'awaiting user decision for: '
                               + ', '.join(question['topic'] for question in questions)}
        dispositioned = list(state.get('dispositioned') or [])
        if record.get('number') not in dispositioned:
            dispositioned.append(record.get('number'))
        base['dispositioned'] = dispositioned
        base['pending_review'] = None
        base['user_questions'] = []
        if repaired:
            repairs = int(state.get('repairs') or 0) + 1
            if self.max_repairs is not None and repairs > self.max_repairs:
                return {**base, **self._fail('disposition repaired an artifact but the repair budget '
                                             'is exhausted')}
            return {**base,
                    'repairs': repairs,
                    'repair_feedback': {'check': record.get('number'), 'record': record},
                    'stage': self._owner_stage(record.get('number')),
                    'message': 'artifact repaired in disposition; re-running the owning stage and check'}
        base['stage'] = state.get('disposition_return') or 'done'
        return base

    def node_awaiting_user(self, state: TaskState) -> dict:
        return {'status': 'awaiting_user', 'stage': 'awaiting_user',
                'message': state.get('message') or 'awaiting a user decision'}

    def _snapshot_cards(self, state: TaskState, version: int) -> dict:
        """Freeze the checked cards; MadGraph rewrites live Cards/run_card.dat."""
        cards = state.get('cards') or {}
        if not cards:
            return {}
        directory = self.store.workspace / 'checks' / f'cards_v{version:02d}'
        directory.mkdir(parents=True, exist_ok=True)
        snapshot = {}
        for name in ('param_card', 'run_card', 'process_card'):
            source = Path(cards.get(name) or '')
            if not source.is_file():
                return {}
            target = directory / source.name
            if not target.exists() or sha256_file(target) != sha256_file(source):
                target.write_bytes(source.read_bytes())
            snapshot[name] = {'path': str(target), 'sha256': sha256_file(target)}
        return snapshot

    def node_launch(self, state: TaskState) -> dict:
        proposed = state.get('proposed_job') or {}
        script = Path(proposed['script'])
        cwd = Path(proposed['cwd'])
        snapshot = state.get('cards_snapshot') or {}
        live = state.get('cards') or {}
        for name, item in snapshot.items():
            path = Path(live.get(name) or '')
            if not path.is_file() or sha256_file(path) != item['sha256']:
                raise StageError('checked card changed before launch: ' + name)
        job = self.jobs.start(name='generate_' + state['task_id'], command='bash ' + _quote(str(script)),
                              cwd=str(cwd), expected_outputs=proposed.get('expected_outputs') or [],
                              timeout_seconds=self.job_timeout)
        self.store.event('job_launched', job_id=job['job_id'], launch_number=int(state.get('job_launches') or 0) + 1,
                         checked_cards_frozen=bool(snapshot))
        return {'job': job, 'job_launches': int(state.get('job_launches') or 0) + 1,
                'status': 'waiting_job', 'stage': 'await_job',
                'message': 'background job started; exit the foreground and resume when it finishes'}

    def node_await_job(self, state: TaskState) -> dict:
        record = self.jobs.status(load_job(self.store, state['job']['job_id']))
        timing = dict(state.get('timing') or {})
        if record['status'] == 'running':
            return {'job': record, 'stage': 'wait', 'status': 'waiting_job'}
        timing['generator_wall_s'] = round(float(timing.get('generator_wall_s') or 0)
                                          + float(record.get('elapsed_s') or 0), 1)
        if record['status'] == 'finished':
            updates = {'job': record,
                    'stage': 'check_sample' if self.gates_enabled else 'analyze_deliver', 'status': 'running',
                    'timing': timing,
                    'message': 'job finished with exit code %s' % record.get('exit_code')}
            if not self.gates_enabled:
                updates['banner'] = checks.find_banner(record)
            return updates
        reason = 'job %s (exit code %s)' % (record['status'], record.get('exit_code'))
        if record['status'] == 'timeout':
            self.jobs.cancel(record)
        if self._can_repair(state):
            return {'job': record, 'repairs': int(state.get('repairs') or 0) + 1,
                    'stage': 'prepare_cards', 'timing': timing,
                    'repair_feedback': {'check': 4, 'record': {
                        'status': 'failed', 'job': {k: record.get(k) for k in
                                                    ('job_id', 'status', 'exit_code', 'log_tail')}}},
                    'message': reason}
        return {'job': record, 'timing': timing, **self._fail(reason)}

    def node_check_sample(self, state: TaskState) -> dict:
        version = int(state.get('attempts') or 1)
        try:
            record = checks.run_sample_check(self.store, state, version)
        except ValueError as error:
            record = {'number': 4, 'status': 'failed', 'path': None, 'sha256': None,
                      'summary': {}, 'review_items': [],
                      'errors': [str(error)], 'failed_checks': []}
        updates = self._record_check(state, 'sample', record)
        banner = checks.find_banner(state.get('job') or {})
        banner_record = None
        if banner:
            banner_state = {**state, 'banner': banner}
            try:
                banner_record = checks.run_card_check(self.store, banner_state, version, 'run_record')
            except (OSError, ValueError) as error:
                banner_record = {'number': 3, 'status': 'failed', 'phase': 'run_record',
                                 'errors': [str(error)], 'path': None}
            updates = {**updates, **self._record_check({**state, **updates}, 'cards_run_record',
                                                      banner_record)}
            updates['banner'] = banner
        status = record['status']
        banner_status = (banner_record or {}).get('status')
        # Audit F3: a failed run-record check is a configuration failure after the
        # run; it must never be skipped by an earlier disposition for tool 3.
        if banner_status == 'failed':
            if self._can_repair(state):
                updates.update(repairs=int(state.get('repairs') or 0) + 1,
                               stage='prepare_cards', job={},
                               repair_feedback={'check': 3, 'record': banner_record},
                               message='recorded configuration differs from the checked cards; '
                                       'repairing the generated configuration')
            else:
                updates.update(self._fail('run-record configuration check failed and the repair '
                                          'budget is exhausted'))
            return updates
        if status == 'failed':
            if self._can_repair(state):
                updates.update(repairs=int(state.get('repairs') or 0) + 1, stage='prepare_cards',
                               job={}, repair_feedback={'check': 4, 'record': record})
            else:
                updates.update(self._fail('checker 4 failed and the repair budget is exhausted'))
            return updates
        if status == 'needs_review' and not self._review_resolved(state, record):
            updates.update(stage='dispositions', pending_review=record,
                           disposition_return='analyze_deliver')
            return updates
        if banner_status == 'needs_review' and not self._review_resolved(state, banner_record):
            updates.update(stage='dispositions', pending_review=banner_record,
                           disposition_return='analyze_deliver')
            return updates
        updates['stage'] = 'analyze_deliver'
        return updates

    def node_analyze_deliver(self, state: TaskState) -> dict:
        event_file = checks.find_event_file(state.get('job') or {})
        if event_file is None:
            raise StageError('no event file available for analysis')
        self._inside(event_file, self.store.workspace)
        sample = (state.get('checks') or {}).get('sample') or {}
        xsec_note = ''
        retained = sample.get('path')
        if retained and Path(retained).is_file():
            data = read_json(retained).get('data') or {}
            processes = (data.get('init') or {}).get('processes') or []
            if processes:
                xsec_note = json.dumps([row.get('reported_cross_section_pb') for row in processes])
        configuration = self._verified_configuration(state)
        options = {} if self.gates_enabled else {'gates_enabled': False}
        prompt = stage_analysis(state['task_id'], state['request'], configuration,
                                state['workspace'], int(state.get('attempts') or 1),
                                event_file, sample, xsec_note,
                                self._experience_retrieval(state), **options)
        finished = self._run_agent_stage(state, 'analyze_deliver', prompt)
        artifacts = finished.get('artifacts') or {}
        delivery = {}
        for name in ('plot', 'table', 'program', 'summary'):
            path = self._inside(artifacts.get(name), self.store.workspace)
            delivery[name] = str(path)
            delivery[name + '_sha256'] = sha256_file(path)
        delivery['events'] = event_file
        delivery['analysis_summary'] = str(finished.get('summary') or '')
        delivery['verified_configuration' if self.gates_enabled else 'requested_configuration'] = {
            'process': configuration.get('process'),
            'model': configuration.get('model'),
            'recorded_run_card': configuration.get('recorded_run_card'),
            'recorded_param_card': configuration.get('recorded_param_card'),
            'sample_reading': configuration.get('sample_reading'),
        }
        policy = state.get('experience_policy') or {}
        updates = {'delivery': delivery,
                   'stage': 'experience' if policy.get('draft') and self.gates_enabled else 'done',
                   'message': delivery['analysis_summary']}
        updates.update(self._record_experience_stage({**state, **updates},
                                                     'analyze_deliver', finished))
        return updates

    def node_experience(self, state: TaskState) -> dict:
        policy = state.get('experience_policy') or {}
        if not policy.get('draft'):
            return {'stage': 'done'}
        history = state.get('check_history') or []
        failed_cards = next((item for item in history
                             if item.get('number') == 3 and item.get('status') == 'failed'), None)
        repaired_cards = next((item for item in reversed(history)
                               if item.get('number') == 3 and item.get('status') != 'failed'), None)
        sample = (state.get('checks') or {}).get('sample') or {}
        spec = (state.get('checks') or {}).get('spec') or {}
        evidence = []
        if failed_cards and failed_cards.get('path'):
            evidence.append({'path': failed_cards['path'], 'role': 'observation'})
        if repaired_cards and repaired_cards.get('path'):
            evidence.append({'path': repaired_cards['path'], 'role': 'observation'})
        for record in (sample, spec):
            if record.get('path') and record.get('status') == 'succeeded':
                evidence.append({'path': record['path'], 'role': 'validation'})
        if not evidence:
            return {'stage': 'done', 'message': 'no experience evidence available'}
        scope = policy.get('applies_to') or {'backend': 'madgraph 2.9.27', 'task': 'beam configuration'}
        prompt = stage_experience(state['request'], state['workspace'], evidence, scope)
        finished = self._run_agent_stage(state, 'experience', prompt)
        candidate_path = self._inside((finished.get('artifacts') or {}).get('candidate'),
                                      self.store.workspace)
        payload = read_json(candidate_path)
        saved = experience.save_candidate(self.store, payload)
        record = checks.run_experience_check(self.store, payload, 1)
        exp = dict(state.get('experience') or {})
        exp.update({'candidate': saved, 'admission': record['status'],
                    'check5': record, 'persisted': None})
        stored = read_json(record['path'])
        if record['status'] == 'succeeded' and (stored.get('data') or {}).get('admission') == 'ready':
            entry = stored['data']['entry']
            entry_sha = stored['data']['entry_sha256']
            persisted = experience.persist_entry(self.store, entry, entry_sha)
            exp['persisted'] = persisted
        return {'experience': exp, 'stage': 'done',
                'message': 'experience admission=%s' % record['status']}

    def node_done(self, state: TaskState) -> dict:
        summary = {
            'task_id': state.get('task_id'),
            'status': 'completed',
            'stage': 'done',
            'checks': {key: value.get('status') for key, value in (state.get('checks') or {}).items()},
            'repairs': state.get('repairs', 0),
            'job': (state.get('job') or {}).get('job_id'),
            'delivery': state.get('delivery'),
            'experience': (state.get('experience') or {}).get('persisted'),
        }
        return {'status': 'completed', 'exit_code': 0, 'stage': 'done', 'summary': summary,
                'message': 'task completed'}

    def node_failed(self, state: TaskState) -> dict:
        return {'status': 'failed', 'exit_code': 1, 'stage': 'failed',
                'message': state.get('message') or 'task failed'}

    # -- graph -----------------------------------------------------------
    def build_graph(self):
        controller = self
        graph = StateGraph(TaskState)

        def wrapped(name):
            def node(state):
                started = time.time()
                try:
                    updates = getattr(controller, 'node_' + name)(state)
                except StageError as error:
                    updates = controller._fail(str(error))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                    updates = controller._fail('%s: %s' % (type(error).__name__, error))
                merged = {**state, **updates}
                stage_timing = controller._stage_seconds(state, name, started)
                base_timing = dict(updates.get('timing') or state.get('timing') or {})
                base_timing['stages'] = stage_timing['stages']
                merged['timing'] = base_timing
                merged['updated_at'] = utc_now()
                controller.store.save_state(merged)
                controller.store.event('node', name=name, status=merged.get('status'),
                                       next_stage=merged.get('stage'),
                                       elapsed_s=round(time.time() - started, 3))
                return {**updates, 'timing': merged['timing'],
                        'updated_at': merged['updated_at']}
            return node

        for name in NODE_NAMES:
            graph.add_node(name, wrapped(name))

        def entry(state):
            stage = state.get('stage') or 'allocate'
            return 'await_job' if stage == 'wait' else stage

        entry_map = {name: name for name in NODE_NAMES}
        entry_map['wait'] = 'await_job'
        graph.add_conditional_edges(START, entry, entry_map)

        graph.add_conditional_edges('allocate', _route,
                                    _routes('check_environment', 'plan_resolve', 'failed'))
        graph.add_conditional_edges('check_environment', _route,
                                    {'plan_resolve': 'plan_resolve', 'failed': 'failed'})
        graph.add_conditional_edges('plan_resolve', _route,
                                    {'check_spec': 'check_spec', 'prepare_cards': 'prepare_cards', 'failed': 'failed',
                                     'awaiting_user': 'awaiting_user'})
        graph.add_conditional_edges('check_spec', _route,
                                    {**_routes('prepare_cards', 'plan_resolve', 'dispositions', 'failed'),
                                     'awaiting_user': 'awaiting_user'})
        graph.add_conditional_edges('prepare_cards', _route,
                                    _routes('inject_fixture', 'launch', 'failed'))
        graph.add_conditional_edges('inject_fixture', _route,
                                    {'check_cards': 'check_cards', 'failed': 'failed'})
        graph.add_conditional_edges('check_cards', _route,
                                    {**_routes('launch', 'prepare_cards', 'dispositions', 'failed'),
                                     'awaiting_user': 'awaiting_user'})
        graph.add_conditional_edges('dispositions', _route,
                                    {**_routes('launch', 'prepare_cards', 'plan_resolve',
                                               'analyze_deliver', 'done', 'failed'),
                                     'awaiting_user': 'awaiting_user'})
        graph.add_conditional_edges('launch', _route,
                                    {'await_job': 'await_job', 'failed': 'failed'})
        graph.add_conditional_edges('await_job', _route,
                                    {**_routes('check_sample', 'analyze_deliver', 'prepare_cards', 'failed'), 'wait': END})
        graph.add_conditional_edges('check_sample', _route,
                                    {**_routes('analyze_deliver', 'prepare_cards', 'dispositions', 'failed'),
                                     'awaiting_user': 'awaiting_user'})
        graph.add_conditional_edges('analyze_deliver', _route,
                                    _routes('experience', 'done', 'failed'))
        graph.add_conditional_edges('experience', _route,
                                    {'done': 'done', 'failed': 'failed'})
        graph.add_edge('awaiting_user', END)
        graph.add_edge('done', END)
        graph.add_edge('failed', END)
        return graph.compile()

    def start(self, state: TaskState) -> TaskState:
        graph = self.build_graph()
        unlimited = (self.task.get('runtime') or {}).get('max_repairs', 2) is None
        return graph.invoke(state, {'recursion_limit': sys.maxsize if unlimited else 200})


def _route(state: TaskState) -> str:
    return state.get('stage') or 'done'


def _routes(*names: str) -> dict:
    mapping = {name: name for name in names}
    mapping['done'] = 'done'
    return mapping


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _line_diff(before: str, after: str) -> str:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    output = []
    for index, (old, new) in enumerate(zip(before_lines, after_lines), 1):
        if old != new:
            output.append('line %d:\n- %s\n+ %s' % (index, old, new))
    if len(before_lines) != len(after_lines):
        output.append('line count changed: %d -> %d' % (len(before_lines), len(after_lines)))
    return '\n'.join(output) + ('\n' if output else '')
