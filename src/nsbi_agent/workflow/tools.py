"""General capabilities supplied to the task Agent.

The Agent gets the same kind of operations a careful operator would use:
read/write files in its own task workspace, list directories, run shell
commands, start/observe long background jobs, look at plots, and declare a
stage complete with structured artifacts.  Everything is bounded: outputs are
truncated, writes are confined to the task workspace, command working
directories stay inside the project, and each call is logged.
"""
from __future__ import annotations

import base64
import json
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from nsbi_agent.workflow.jobs import JobManager, load_job
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore, sha256_file, utc_now

MAX_READ_CHARS = 20000
MAX_LIST_ENTRIES = 300
MAX_OUTPUT_CHARS = 4000


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': required, 'additionalProperties': False}}}


TOOL_SCHEMAS: list[dict] = [
    _function('read_file', 'Read a UTF-8 text file. Paths may be absolute or relative to the task workspace.',
              {'path': {'type': 'string'}, 'max_chars': {'type': 'integer'}}, ['path']),
    _function('write_file', 'Write a text file inside the task workspace (creates parent directories).',
              {'path': {'type': 'string'}, 'content': {'type': 'string'}}, ['path', 'content']),
    _function('list_dir', 'List a directory. Returns names, sizes and kinds.',
              {'path': {'type': 'string'}}, ['path']),
    _function('run_command', 'Run a shell command and wait for it to finish. Returns exit code and output tails. '
                             'Use a generous timeout for MadGraph generate/output steps.',
              {'command': {'type': 'string'}, 'cwd': {'type': 'string'},
               'timeout_seconds': {'type': 'integer'}}, ['command']),
    _function('start_job', 'Start a long background job that survives this task step. '
                           'Use it for event generation, not for short commands.',
              {'name': {'type': 'string'}, 'command': {'type': 'string'}, 'cwd': {'type': 'string'},
               'expected_outputs': {'type': 'array', 'items': {'type': 'string'}},
               'timeout_seconds': {'type': 'integer'}}, ['name', 'command']),
    _function('job_status', 'Check a background job started by start_job.',
              {'job_id': {'type': 'string'}}, ['job_id']),
    _function('view_image', 'Open a PNG image (plot) and inspect it visually. '
                            'The image is attached to the next model message.',
              {'path': {'type': 'string'}}, ['path']),
    _function('ask_user', 'Ask one concrete clarification during planning. The workflow pauses '
                           'and resumes planning after the user answers. Use the user\'s language.',
              {'question': {'type': 'string', 'minLength': 1},
               'choices': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6}}, ['question']),
    _function('finish_stage', 'Declare the current stage complete with structured results.',
              {'stage': {'type': 'string'}, 'complete': {'type': 'boolean'},
               'summary': {'type': 'string'},
               'artifacts': {'type': 'object'},
               'proposed_job': {'type': 'object'},
               'request': {'type': 'object'},
               'defaults': {'type': 'object'},
               'dispositions': {'type': 'array', 'items': {'type': 'object'}}},
              ['stage', 'complete', 'summary']),
]


class ToolError(ValueError):
    """A rejected tool call the Agent can correct by reading the message."""


class ToolBox:
    """Executes tool calls for one task stage; records the final declaration."""

    def __init__(self, store: TaskStore, jobs: JobManager):
        self.store = store
        self.jobs = jobs
        self.finished = None
        self.pending_image = None
        self.pending_question = None
        self.stage = None
        self.command_log_dir = store.logs_dir / 'commands'

    # -- path helpers ----------------------------------------------------
    def resolve_read(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.store.workspace / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(PROJECT_ROOT.resolve()):
            raise ToolError('path outside the project: ' + str(candidate))
        return candidate

    def resolve_write(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.store.workspace / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.store.workspace.resolve()):
            raise ToolError('writes are confined to the task workspace: ' + str(self.store.workspace))
        return candidate

    def resolve_cwd(self, cwd: str | None) -> Path:
        candidate = Path(cwd) if cwd else self.store.workspace
        candidate = candidate.resolve() if candidate.is_absolute() else (self.store.workspace / candidate).resolve()
        if not candidate.is_dir() or not candidate.is_relative_to(PROJECT_ROOT.resolve()):
            raise ToolError('working directory must exist inside the project: ' + str(candidate))
        return candidate

    # -- tool dispatch ---------------------------------------------------
    def execute(self, name: str, arguments: dict) -> dict:
        handler = getattr(self, 'tool_' + name, None)
        if handler is None:
            raise ToolError('unknown tool: ' + name)
        return handler(arguments)

    def tool_read_file(self, arguments: dict) -> dict:
        path = self.resolve_read(arguments['path'])
        if not path.is_file():
            raise ToolError('not a regular file: ' + str(path))
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.pdf'}:
            raise ToolError('binary file; use view_image for pictures: ' + str(path))
        limit = min(int(arguments.get('max_chars') or 8000), MAX_READ_CHARS)
        with open(path, 'rb') as stream:
            raw = stream.read(limit + 1)
        text = raw[:limit].decode('utf-8', 'replace')
        result = {'path': str(path), 'content': text,
                  'truncated': len(raw) > limit, 'size_bytes': path.stat().st_size}
        if result['truncated']:
            result['note'] = 'content truncated at max_chars; read a narrower range or use run_command'
        return result

    def tool_write_file(self, arguments: dict) -> dict:
        path = self.resolve_write(arguments['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = arguments['content']
        path.write_text(content, encoding='utf-8')
        return {'path': str(path), 'bytes': len(content.encode('utf-8')),
                'sha256': sha256_file(path)}

    def tool_list_dir(self, arguments: dict) -> dict:
        path = self.resolve_read(arguments['path'])
        if not path.is_dir():
            raise ToolError('not a directory: ' + str(path))
        entries = []
        for child in sorted(path.iterdir())[:MAX_LIST_ENTRIES]:
            info = {'name': child.name, 'is_dir': child.is_dir()}
            if child.is_file():
                info.update(size=child.stat().st_size, suffix=child.suffix)
            entries.append(info)
        return {'path': str(path), 'entries': entries,
                'truncated': len(list(path.iterdir())) > MAX_LIST_ENTRIES}

    def tool_run_command(self, arguments: dict) -> dict:
        cwd = self.resolve_cwd(arguments.get('cwd'))
        timeout = min(int(arguments.get('timeout_seconds') or 600), 3600)
        started = time.time()
        log_path = self.command_log_dir / (time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '.log')
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(['bash', '-c', arguments['command']],
                                       cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
            code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
            timed_out = False
        except subprocess.TimeoutExpired as error:
            code = -9
            stdout = _text(error.stdout)
            stderr = _text(error.stderr) + '\n[timeout after %ss]' % timeout
            timed_out = True
        elapsed = round(time.time() - started, 1)
        log_path.write_text(f'$ {arguments["command"]}\n# cwd={cwd} exit={code} elapsed={elapsed}s\n'
                            f'--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n', encoding='utf-8')
        self.store.event('command', command=arguments['command'], cwd=str(cwd), exit_code=code,
                         elapsed_s=elapsed, log=str(log_path), timed_out=timed_out)
        return {'exit_code': code, 'timed_out': timed_out, 'elapsed_s': elapsed,
                'stdout_tail': stdout[-MAX_OUTPUT_CHARS:], 'stderr_tail': stderr[-MAX_OUTPUT_CHARS:],
                'log': str(log_path)}

    def tool_start_job(self, arguments: dict) -> dict:
        cwd = self.resolve_cwd(arguments.get('cwd'))
        record = self.jobs.start(
            name=arguments['name'], command=arguments['command'], cwd=str(cwd),
            expected_outputs=arguments.get('expected_outputs') or [],
            timeout_seconds=min(int(arguments.get('timeout_seconds') or 3600), 6 * 3600))
        return {'job_id': record['job_id'], 'pid': record['pid'], 'log': record['log'],
                'cwd': record['cwd'], 'status': 'running',
                'note': 'background job started; it keeps running after this step ends'}

    def tool_job_status(self, arguments: dict) -> dict:
        job_id = arguments['job_id']
        record = self.jobs.status(load_job(self.store, job_id))
        return {'job_id': job_id, 'status': record['status'], 'exit_code': record['exit_code'],
                'elapsed_s': record['elapsed_s'], 'log_tail': record['log_tail'],
                'log': record['log']}

    def tool_view_image(self, arguments: dict) -> dict:
        path = self.resolve_read(arguments['path'])
        if not path.is_file() or path.suffix.lower() != '.png':
            raise ToolError('view_image expects a PNG file: ' + str(path))
        width, height = png_size(path)
        data = base64.b64encode(path.read_bytes()).decode('ascii')
        self.pending_image = {'path': str(path), 'width': width, 'height': height,
                              'data_url': 'data:image/png;base64,' + data}
        self.store.event('view_image', path=str(path), width=width, height=height,
                         sha256=sha256_file(path))
        return {'path': str(path), 'width': width, 'height': height,
                'attached': 'the image follows in the next user message'}

    def tool_finish_stage(self, arguments: dict) -> dict:
        if arguments.get('stage') is None:
            raise ToolError('finish_stage requires a stage name')
        self.finished = arguments
        return {'accepted': True, 'stage': arguments['stage'], 'complete': arguments['complete']}

    def tool_ask_user(self, arguments: dict) -> dict:
        if self.stage != 'plan_resolve':
            raise ToolError('ask_user is available during request planning')
        question = arguments.get('question')
        choices = arguments.get('choices', [])
        if not isinstance(question, str) or not question.strip():
            raise ToolError('ask_user requires a nonempty question')
        if not isinstance(choices, list) or len(choices) > 6 or any(
                not isinstance(choice, str) or not choice.strip() for choice in choices):
            raise ToolError('choices must contain up to six nonempty strings')
        self.pending_question = {'question': question.strip(),
                                 'choices': [choice.strip() for choice in choices]}
        return {'status': 'awaiting_user', **self.pending_question}


def _text(value: Any) -> str:
    if value is None:
        return ''
    return value if isinstance(value, str) else value.decode('utf-8', 'replace')


def png_size(path: Path) -> tuple[int, int]:
    """Read width/height from a PNG IHDR chunk without extra dependencies."""
    raw = path.read_bytes()[:33]
    if raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise ToolError('not a PNG file: ' + str(path))
    width, height = struct.unpack('>II', raw[16:24])
    return width, height


def summarize_tool_result(result: dict, limit: int = 6000) -> str:
    """Serialise a tool result for the model, bounded and with clear truncation."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f'\n[truncated at {limit} characters]'
