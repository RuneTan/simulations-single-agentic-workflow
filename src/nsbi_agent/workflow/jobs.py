"""Background calculation jobs that outlive one foreground graph step.

A job is a shell script plus a log, a pid and an exit-code file under
``workspace/jobs/<job_id>/``.  The script is started in its own session, so it
keeps running after the foreground ``run.sh task`` process exits; ``resume``
queries the recorded job instead of launching another one (the execution section in REVIEW).
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore, utc_now


class JobManager:
    """Start, inspect and cancel at most a handful of long-running jobs."""

    def __init__(self, store: TaskStore):
        self.store = store

    def start(self, name: str, command: str, cwd: str,
              expected_outputs: list | None = None,
              timeout_seconds: int = 3600) -> dict:
        if not command.strip():
            raise ValueError('empty job command')
        cwd_path = Path(cwd).resolve()
        if not cwd_path.is_dir() or not cwd_path.is_relative_to(PROJECT_ROOT):
            raise ValueError('job working directory must exist inside the project: ' + str(cwd_path))
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        job_id = f'{stamp}-{name}-{hashlib.sha256(command.encode()).hexdigest()[:8]}'
        job_dir = self.store.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / 'job.log'
        exit_code_path = job_dir / 'exit_code'
        finished_epoch_path = job_dir / 'finished_epoch'
        wrapper = job_dir / 'run.sh'
        wrapper.write_text(
            '#!/usr/bin/env bash\n'
            'set -uo pipefail\n'
            f'cd {shlex.quote(str(cwd_path))}\n'
            f'{command}\n'
            'code=$?\n'
            f'printf "%s\\n" "$code" > {shlex.quote(str(exit_code_path))}\n'
            f'date +%s.%N > {shlex.quote(str(finished_epoch_path))}\n'
            'exit "$code"\n', encoding='utf-8')
        wrapper.chmod(0o755)
        with open(log_path, 'wb') as log:
            process = subprocess.Popen(
                ['bash', str(wrapper)], cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True)
        record = {
            'job_id': job_id, 'name': name, 'command': command, 'cwd': str(cwd_path),
            'wrapper': str(wrapper), 'log': str(log_path), 'exit_code_file': str(exit_code_path),
            'finished_epoch_file': str(finished_epoch_path),
            'pid': process.pid, 'started_at': utc_now(), 'started_epoch': time.time(),
            'timeout_seconds': timeout_seconds, 'expected_outputs': expected_outputs or [],
            'status': 'running', 'exit_code': None,
        }
        self.store.write_json(f'jobs/{job_id}/job.json', record)
        self.store.event('job_started', job_id=job_id, name=name, command=command,
                         cwd=str(cwd_path), pid=process.pid, log=str(log_path))
        return record

    def status(self, record: dict) -> dict:
        """Return the current state; ``status`` is running/finished/lost/timeout.

        Audit F6: for a finished job the duration is measured from the wrapper's
        finished timestamp (or the exit-code file mtime for old jobs), never from
        the time the status happens to be queried.  Waiting for ``resume`` is
        therefore not counted as generator time.
        """
        record = dict(record)
        exit_file = Path(record['exit_code_file'])
        started = float(record.get('started_epoch') or time.time())
        now = time.time()
        exit_code = None
        finished_epoch = None
        if exit_file.is_file():
            try:
                exit_code = int(exit_file.read_text().strip() or '-1')
            except ValueError:
                exit_code = -1
            finished_epoch = _read_epoch(record.get('finished_epoch_file'))
            if finished_epoch is None:
                try:
                    finished_epoch = exit_file.stat().st_mtime
                except OSError:
                    finished_epoch = now
        elapsed = round(max((finished_epoch if finished_epoch is not None else now) - started, 0.0), 1)
        alive = _process_alive(record.get('pid'))
        if exit_code is not None:
            status = 'finished'
        elif alive:
            status = 'running'
            timeout = int(record.get('timeout_seconds') or 0)
            if timeout and elapsed > timeout:
                status = 'timeout'
        else:
            status = 'lost'
        record.update(status=status, exit_code=exit_code, elapsed_s=elapsed,
                      finished_epoch=finished_epoch,
                      log_tail=self.log_tail(record.get('job_id'), 2500))
        return record

    def cancel(self, record: dict) -> dict:
        pid = record.get('pid')
        if _process_alive(pid):
            try:
                os.killpg(os.getpgid(int(pid)), 15)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        self.store.event('job_cancelled', job_id=record.get('job_id'), pid=pid)
        return self.status(record)

    def log_tail(self, job_id: str, max_chars: int = 2500) -> str:
        path = self.store.jobs_dir / job_id / 'job.log'
        if not path.is_file():
            return ''
        with open(path, 'rb') as stream:
            stream.seek(max(0, path.stat().st_size - 64 * 1024))
            text = stream.read().decode('utf-8', 'replace')
        return text[-max_chars:]


def _read_epoch(path) -> float | None:
    if not path:
        return None
    try:
        return float(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _process_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def job_identity(record: dict) -> dict:
    """Small subset safe to place in model context and summaries."""
    keys = ('job_id', 'name', 'status', 'exit_code', 'started_at', 'elapsed_s',
            'cwd', 'command', 'log', 'expected_outputs')
    return {key: record.get(key) for key in keys}


def load_job(store: TaskStore, job_id: str) -> dict:
    path = store.jobs_dir / job_id / 'job.json'
    if not path.is_file():
        raise FileNotFoundError('unknown job: ' + job_id)
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)
