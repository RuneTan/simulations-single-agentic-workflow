"""Local experience memory: retrieve before acting, admit through checker 5.

Layout (project root, shared by tasks):
    memory/candidates/<sha256>.json   draft lessons, never used for retrieval
    memory/entries/<sha256>.json      admitted entries (check 5 returned ready)

Retrieval is intentionally simple and transparent: token-overlap scoring of
``applies_to`` values against a query built from the task/model/backend, no
embeddings.  Every retrieval result is recorded in the task state so the
delivery can show which stale or missing knowledge was available.
"""
from __future__ import annotations

import re
import time

from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore, json_fingerprint, sha256_file, read_json, utc_now

MEMORY_ROOT = PROJECT_ROOT / 'memory'
ENTRY_DIR = MEMORY_ROOT / 'entries'
CANDIDATE_DIR = MEMORY_ROOT / 'candidates'
_TOKEN = re.compile(r'[a-z0-9]+')


def _tokens(value) -> set[str]:
    text = value if isinstance(value, str) else ' '.join(str(v) for v in value)
    return set(_TOKEN.findall(text.lower()))


def _score(entry: dict, query: dict) -> float:
    applies = _tokens(list((entry.get('applies_to') or {}).values()))
    query_tokens = _tokens([str(v) for v in query.values()])
    return len(applies & query_tokens)


def build_query(request: dict, task_type: str) -> dict:
    """Compact, stable query describing what this task needs experience on."""
    model = request.get('simulation', {}).get('model', {})
    generation = request.get('simulation', {}).get('generation', {})
    return {
        'backend': 'madgraph 2.9.27',
        'model': 'SMEFTsim %s %s %s %s' % (model.get('flavour_scheme', ''), model.get('ew_input_scheme', ''),
                                           model.get('version', ''), model.get('restriction', '')),
        'process': str(request.get('simulation', {}).get('process', ''))[:200],
        'beams': 'beam energies %s GeV' % generation.get('beam_energies_GeV', ''),
        'task': task_type,
    }


def retrieve(store: TaskStore, query: dict) -> dict:
    entries = []
    if ENTRY_DIR.is_dir():
        for path in sorted(ENTRY_DIR.glob('*.json')):
            try:
                payload = read_json(path)
                entry = payload.get('entry', payload)
            except (OSError, ValueError):
                continue
            score = _score(entry, query)
            if score > 0:
                entries.append({'entry_sha256': payload.get('entry_sha256', path.stem),
                                'path': str(path), 'score': score,
                                'lesson': entry.get('lesson', ''),
                                'applies_to': entry.get('applies_to', {})})
    entries.sort(key=lambda item: (-item['score'], item['entry_sha256']))
    chosen = entries[:2]
    content = '\n\n'.join('Entry %s (score %s):\n%s' % (item['entry_sha256'], item['score'], item['lesson'])
                          for item in chosen)
    result = {'query': query, 'matched': chosen, 'content': content, 'retrieved_at': utc_now()}
    store.event('experience_retrieved', query=query,
                matched=[(item['entry_sha256'], item['score']) for item in chosen])
    return result


def candidate_payload(lesson: str, applies_to: dict, evidence: list, review: dict) -> dict:
    return {'lesson': lesson, 'applies_to': applies_to,
            'evidence': [{'path': path, 'role': role} for path, role in evidence],
            'review': review}


def save_candidate(store: TaskStore, payload: dict) -> dict:
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    digest = json_fingerprint(payload)
    record = {'candidate_sha256': digest, 'created_at': utc_now(),
              'source_task': store.workspace.name, 'payload': payload}
    path = CANDIDATE_DIR / f'{digest}.json'
    store.write_json(f'experience/candidate-{digest}.json', record)
    path.write_text(_dumps(record), encoding='utf-8')
    store.event('experience_candidate', candidate_sha256=digest, path=str(path))
    return {'candidate_sha256': digest, 'path': str(path), 'payload': payload}


def persist_entry(store: TaskStore, entry: dict, entry_sha256: str) -> dict:
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    record = {'entry_sha256': entry_sha256, 'admitted_at': utc_now(),
              'source_task': store.workspace.name, 'entry': entry}
    path = ENTRY_DIR / f'{entry_sha256}.json'
    path.write_text(_dumps(record), encoding='utf-8')
    store.event('experience_persisted', entry_sha256=entry_sha256, path=str(path))
    return {'entry_sha256': entry_sha256, 'path': str(path), 'sha256': sha256_file(path)}


def _dumps(value) -> str:
    import json
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + '\n'
