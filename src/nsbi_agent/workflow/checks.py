"""Invoke the five accepted checkers and retain their untouched results.

The graph never re-implements a checker: it builds the documented request
contract, calls the existing function, stores the original ``ToolResult``
JSON under ``checks/`` together with the request/artifact hashes that were
checked, and returns a compact record for routing and model context.
"""
from __future__ import annotations

import time
from pathlib import Path

from nsbi_agent.contracts import EnvironmentRequest
from nsbi_agent.tools.card_validator import validate_cards
from nsbi_agent.tools.environment import inspect_environment
from nsbi_agent.tools.experience import check_experience
from nsbi_agent.tools.sample import check_sample
from nsbi_agent.tools.spec import validate_spec
from nsbi_agent.validation_contracts import (CardPaths, CardValidationRequest,
                                             ExperienceRequest, IntentRequest, SampleRequest)
from nsbi_agent.workflow.state import TaskStore, json_fingerprint, sha256_file, utc_now

# Check number -> stable file prefix; one file per version is never overwritten.
CHECK_PREFIX = {1: '01_environment', 2: '02_spec', 3: '03_cards', 4: '04_sample', 5: '05_experience'}
ENVIRONMENT_PACKAGES = {'langgraph': '1.2.17', 'pydantic': '2.14.1'}
ENVIRONMENT_BACKENDS = ['madgraph', 'gfortran', 'make']


def run_environment_check(store: TaskStore) -> dict:
    request = EnvironmentRequest(packages=ENVIRONMENT_PACKAGES, backend_names=ENVIRONMENT_BACKENDS)
    started = time.time()
    result = inspect_environment(request)
    return _retain(store, 1, 1, result, {'request': request.model_dump()}, started)


def run_spec_check(store: TaskStore, state: dict, version: int) -> dict:
    request = IntentRequest.model_validate(state['request'])
    started = time.time()
    result = validate_spec(request)
    record = _retain(store, 2, version, result, {
        'request_sha256': json_fingerprint(request.original()),
        'request_path': state.get('request_file'),
    }, started)
    if result.data.get('intent_sha256'):
        record['intent_sha256'] = result.data['intent_sha256']
    return record


def run_card_check(store: TaskStore, state: dict, version: int, phase: str = 'configuration') -> dict:
    if not state.get('cards'):
        raise ValueError('no cards recorded in task state')
    request = IntentRequest.model_validate(state['request'])
    paths = state['cards']
    if phase == 'run_record' and state.get('cards_snapshot'):
        # The run phase compares the recorded banner with the frozen checked
        # cards; MadGraph rewrites Cards/run_card.dat while generating events.
        paths = {name: item['path'] for name, item in state['cards_snapshot'].items()}
    card_paths = {name: path for name, path in paths.items() if name in CardPaths.model_fields}
    if phase == 'run_record' and state.get('banner'):
        card_paths['banner'] = state['banner']
    artifacts = CardPaths.model_validate(card_paths)
    validation = CardValidationRequest(
        request=request, artifacts=artifacts,
        expected_intent_sha256=state.get('intent_sha256'), phase=phase)
    started = time.time()
    result = validate_cards(validation)
    bindings = {'request_sha256': json_fingerprint(request.original()),
                'intent_sha256': state.get('intent_sha256'),
                'artifact_sha256': {name: sha256_file(path)
                                    for name, path in card_paths.items() if Path(path).is_file()}}
    record = _retain(store, 3, version, result, bindings, started, label=phase)
    record['phase'] = phase
    return record


def run_sample_check(store: TaskStore, state: dict, version: int) -> dict:
    job = state.get('job') or {}
    event_file = find_event_file(job)
    if event_file is None:
        raise ValueError('no LHE event file found in the finished job outputs')
    generation = state['request']['simulation'].get('generation', {})
    request = SampleRequest(
        event_file=str(Path(event_file).resolve()),
        expected_events=generation.get('nevents'),
        required_weight_ids=[],
        run_exit_code=job.get('exit_code'),
        log_file=find_job_log(job),
    )
    started = time.time()
    result = check_sample(request)
    bindings = {'request_sha256': json_fingerprint(state['request']),
                'event_file_sha256': sha256_file(event_file)}
    return _retain(store, 4, version, result, bindings, started)


def run_experience_check(store: TaskStore, payload: dict, version: int) -> dict:
    request = ExperienceRequest.model_validate(payload)
    started = time.time()
    result = check_experience(request)
    return _retain(store, 5, version, result, {'payload_sha256': json_fingerprint(payload)}, started)


def find_event_file(job: dict) -> str | None:
    """Locate the LHE/LHE.gz produced by a finished job (expected outputs first)."""
    for expected in job.get('expected_outputs') or []:
        path = Path(expected)
        if path.is_file() and path.suffix.lower() in {'.lhe', '.gz'}:
            return str(path)
        if path.is_dir():
            found = _first_lhe(path)
            if found:
                return found
    cwd = Path(job.get('cwd') or '.')
    if cwd.is_dir():
        return _first_lhe(cwd / 'Events') or _first_lhe(cwd)
    return None


def find_job_log(job: dict) -> str | None:
    """Prefer a non-empty wrapper log; fall back to the generator's own log."""
    wrapper = Path(job.get('log') or '')
    if wrapper.is_file() and wrapper.stat().st_size > 0:
        return str(wrapper)
    cwd = Path(job.get('cwd') or '.')
    for name in ('run.log', 'generation.log'):
        candidate = cwd / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return str(candidate)
    if wrapper.is_file():
        return str(wrapper)
    return None


def find_banner(job: dict) -> str | None:
    """Locate the MadGraph run banner written next to the sample."""
    roots = [Path(job.get('cwd') or '.')]
    for expected in job.get('expected_outputs') or []:
        path = Path(expected)
        if path.is_dir():
            roots.append(path)
        elif path.is_file():
            roots.append(path.parent)
    for root in roots:
        if not root.is_dir():
            continue
        matches = sorted(root.rglob('*_banner.txt'))
        if matches:
            return str(matches[0])
    return None


def _first_lhe(root: Path) -> str | None:
    if not root.is_dir():
        return None
    candidates = sorted([p for p in root.rglob('*') if p.is_file() and
                         (p.suffix.lower() == '.lhe' or p.name.lower().endswith('.lhe.gz'))])
    return str(candidates[0]) if candidates else None


def _retain(store: TaskStore, number: int, version: int, result, bindings: dict,
            started: float, label: str | None = None) -> dict:
    suffix = f'_{label}' if label else ''
    name = f'{CHECK_PREFIX[number]}_v{version:02d}{suffix}.json'
    record = {
        'tool': result.tool,
        'check_number': number,
        'status': result.status,
        'data': result.data,
        'checks_failed': [c.model_dump() for c in result.checks if c.status == 'fail'],
        'errors': [e.model_dump() for e in result.errors],
        'artifacts': result.artifacts,
        'bindings': bindings,
        'elapsed_s': round(time.time() - started, 3),
        'retained_at': utc_now(),
    }
    path = store.write_json(f'checks/{name}', record)
    store.trace('check_result', number=number, version=version, phase=label,
                path=str(path), sha256=sha256_file(path), result=record)
    summary = result.data.get('summary', {})
    return {
        'number': number, 'version': version, 'path': str(path),
        'sha256': sha256_file(path), 'status': result.status,
        'summary': summary,
        'review_items': result.data.get('review_items', []),
        'errors': [e.message for e in result.errors][:8],
        'failed_checks': [(c.name, c.expected, c.actual)
                          for c in result.checks if c.status == 'fail'][:8],
        'intent_sha256': result.data.get('intent_sha256'),
        'elapsed_s': record['elapsed_s'],
        'bindings': bindings,
    }
