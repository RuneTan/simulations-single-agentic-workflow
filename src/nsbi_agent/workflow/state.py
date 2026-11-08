"""Durable task state and workspace layout.

One task owns ``workspace/tasks/<task_id>/``.  Everything the Agent writes
lives there; large artifacts are referenced by absolute path.  State is kept
in two forms:

* ``state.json``: the current snapshot, replaced atomically after every node;
* ``checkpoints.sqlite``: an append-only history of snapshots ("equivalent
  durable storage" for LangGraph resume; one row per completed stage).

``events.jsonl`` is the append-only task trace (nodes, checks, tools, jobs,
repairs).  It is the primary audit record.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Ordered stages.  ``waiting_job`` is a state, not a stage: the foreground
# runner exits after ``launch`` and a later ``resume`` continues at ``await_job``.
STAGES = (
    'allocate',
    'check_environment',
    'plan_resolve',
    'check_spec',
    'prepare_cards',
    'inject_fixture',
    'check_cards',
    'launch',
    'await_job',
    'check_sample',
    'analyze_deliver',
    'experience',
    'done',
)


class TaskState(TypedDict, total=False):
    # Identity and request lineage.
    task_id: str
    task_file: str
    task_type: str
    user_request: str
    workspace: str
    runtime: dict
    fixtures: list
    experience_policy: dict
    analysis: dict
    # Workflow position and outcome.
    stage: str
    status: str          # running | waiting_job | completed | failed | awaiting_user
    exit_code: int
    message: str
    # Per-stage bookkeeping.
    spec_version: int
    card_version: int
    check3_count: int
    attempts: int
    repairs: int
    job_launches: int
    dispositioned: list
    pending_review: dict
    disposition_return: str
    user_questions: list
    user_decisions: dict
    question_return: str | None
    clarifications: list
    human_interventions: int
    repair_feedback: dict
    plan_mode: str
    # Structured request and effective defaults (tool 2 is authoritative).
    request: dict
    defaults: dict
    plan_summary: str
    request_sha256: str
    original_request_sha256: str
    intent_sha256: str
    # Artifacts and checks.
    cards: dict          # {'param_card': path, ...} from the Agent's finish_stage
    cards_snapshot: dict # immutable copies of the checked cards, made at check time
    proposed_job: dict
    request_file: str
    banner: str
    model: dict
    checks: dict         # check name -> latest retained evidence record
    check_history: list
    dispositions: list
    repairs: int
    attempts: int
    job: dict
    fixture: dict
    experience: dict
    delivery: dict
    usage: dict
    timing: dict
    started_at: str
    updated_at: str
    analysis_refresh: dict


def utc_now() -> str:
    """ISO-8601 UTC timestamp with second resolution."""
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def json_fingerprint(value: Any) -> str:
    """Same canonical JSON hashing rule as the checkers use."""
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def read_json(path: str | Path) -> Any:
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


class TaskStore:
    """Filesystem layout, atomic state persistence and append-only traces."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.state_path = self.workspace / 'state.json'
        self.checkpoint_path = self.workspace / 'checkpoints.sqlite'
        self.events_path = self.workspace / 'events.jsonl'
        self.trajectory_path = self.workspace / 'trajectory.jsonl'
        self._trajectory = None
        self.model_trace_dir = self.workspace / 'model_trace'
        self.checks_dir = self.workspace / 'checks'
        self.jobs_dir = self.workspace / 'jobs'
        self.logs_dir = self.workspace / 'logs'
        self.attempts_dir = self.workspace / 'attempts'

    # -- workspace -------------------------------------------------------
    def create(self) -> 'TaskStore':
        for path in (self.workspace, self.model_trace_dir, self.checks_dir,
                     self.jobs_dir, self.logs_dir, self.attempts_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        self.trajectory_path.touch(exist_ok=True)
        self._init_checkpoints()
        return self

    def exists(self) -> bool:
        return self.state_path.is_file()

    def attempt_dir(self, number: int) -> Path:
        path = self.attempts_dir / f'{number:03d}'
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- atomic JSON -----------------------------------------------------
    def write_json(self, relative: str, payload: Any) -> Path:
        """Atomically write JSON below the workspace (tmp file + rename)."""
        target = (self.workspace / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.tmp')
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2, sort_keys=False, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, target)
        return target

    def read_json(self, relative: str) -> Any:
        return read_json(self.workspace / relative)

    def save_state(self, state: TaskState) -> None:
        state['updated_at'] = utc_now()
        self.write_json('state.json', state)
        self._append_checkpoint(state)

    def load_state(self) -> TaskState:
        return read_json(self.state_path)

    def events(self) -> list[dict]:
        if not self.events_path.is_file():
            return []
        records = []
        with open(self.events_path, encoding='utf-8') as stream:
            for line in stream:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def event(self, kind: str, **fields: Any) -> dict:
        """Append one trace record; returns it so callers can reuse ids."""
        record = {'ts': utc_now(), 'kind': kind, **fields}
        with open(self.events_path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + '\n')
        self.trace(kind, **fields)
        return record

    def trace(self, kind: str, **fields: Any) -> None:
        from nsbi_agent.workflow.trajectory import Trajectory
        if self._trajectory is None:
            self._trajectory = Trajectory(self.trajectory_path)
        self._trajectory.append(kind, **fields)

    def trace_model_call(self, stage: str, index: int, record: dict) -> None:
        """Append the compact per-call record; full messages go to the stage log."""
        path = self.model_trace_dir / 'calls.jsonl'
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'stage': stage, 'call': index, **record},
                                    ensure_ascii=False) + '\n')

    def trace_messages(self, stage: str, messages: list) -> None:
        """Keep the exact conversation supplied to the model for this stage."""
        self.write_json(f'model_trace/{stage}.messages.json', messages)

    # -- checkpoints -----------------------------------------------------
    def _init_checkpoints(self) -> None:
        connection = sqlite3.connect(self.checkpoint_path)
        try:
            connection.execute(
                'CREATE TABLE IF NOT EXISTS checkpoints ('
                ' seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, stage TEXT,'
                ' status TEXT, created_at TEXT, state_json TEXT)')
            connection.commit()
        finally:
            connection.close()

    def _append_checkpoint(self, state: TaskState) -> None:
        connection = sqlite3.connect(self.checkpoint_path)
        try:
            connection.execute(
                'INSERT INTO checkpoints (task_id, stage, status, created_at, state_json)'
                ' VALUES (?, ?, ?, ?, ?)',
                (state.get('task_id'), state.get('stage'), state.get('status'),
                 state.get('updated_at'), json.dumps(state, ensure_ascii=False)))
            connection.commit()
        finally:
            connection.close()

    def checkpoint_stages(self) -> list[str]:
        connection = sqlite3.connect(self.checkpoint_path)
        try:
            rows = connection.execute('SELECT stage FROM checkpoints ORDER BY seq').fetchall()
        finally:
            connection.close()
        return [row[0] for row in rows]
