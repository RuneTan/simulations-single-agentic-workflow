"""Minimal OpenAI-compatible model client for the task Agent.

Provider settings come from the environment, with a project-local
``.env.local`` (mode 0600, ignored by git) as the only persisted source.
Only *variable names* are ever logged; values never enter traces or results.
Chat completions use the standard ``/chat/completions`` endpoint and the
DeepSeek ``thinking`` switch is disabled to keep trial costs and latency low
(requirements: one task Agent, no reasoning trace needed for tool use).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'
CREDENTIAL_FILE = '.env.local'


class ModelError(RuntimeError):
    """The model call failed after retries, or is misconfigured."""


@dataclass
class ModelConfig:
    provider: str = 'deepseek'
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = 'DEEPSEEK_API_KEY'
    max_tokens: int | None = 1600
    temperature: float = 0.0
    timeout_seconds: int = 240
    max_retries: int = 3
    loaded_from: list = field(default_factory=list)

    @classmethod
    def from_environment(cls, project_root: str | Path) -> 'ModelConfig':
        loaded = load_project_credentials(project_root)
        return cls(
            provider=os.environ.get('NSBI_MODEL_PROVIDER', 'deepseek'),
            model=os.environ.get('DEEPSEEK_MODEL', DEFAULT_MODEL),
            base_url=os.environ.get('DEEPSEEK_BASE_URL', DEFAULT_BASE_URL),
            max_tokens=int(os.environ.get('NSBI_MODEL_MAX_TOKENS', '1600')),
            loaded_from=loaded,
        )

    @property
    def api_key(self) -> str:
        value = os.environ.get(self.api_key_env, '')
        if not value:
            raise ModelError('missing model credential: set ' + self.api_key_env
                             + ' (project .env.local or environment)')
        return value

    def public(self) -> dict:
        """Configuration safe to log: names and non-secret values only."""
        return {'provider': self.provider, 'model': self.model,
                'base_url': self.base_url, 'credential_variable': self.api_key_env,
                'credential_present': bool(os.environ.get(self.api_key_env)),
                'max_tokens': self.max_tokens, 'temperature': self.temperature,
                'thinking': 'disabled', 'credentials_loaded_from': self.loaded_from}


def load_project_credentials(project_root: str | Path) -> list[str]:
    """Load simple ``KEY=VALUE`` lines into this process only; returns names."""
    path = Path(project_root) / CREDENTIAL_FILE
    loaded: list[str] = []
    if not path.is_file():
        return loaded
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, _, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_cache_hit_tokens: int = 0

    def add(self, usage: dict) -> None:
        self.calls += 1
        self.prompt_tokens += int(usage.get('prompt_tokens', 0) or 0)
        self.completion_tokens += int(usage.get('completion_tokens', 0) or 0)
        details = usage.get('prompt_tokens_details') or {}
        self.prompt_cache_hit_tokens += int(details.get('cached_tokens', 0) or
                                            usage.get('prompt_cache_hit_tokens', 0) or 0)

    def summary(self) -> dict:
        return {'calls': self.calls, 'prompt_tokens': self.prompt_tokens,
                'completion_tokens': self.completion_tokens,
                'total_tokens': self.prompt_tokens + self.completion_tokens,
                'prompt_cache_hit_tokens': self.prompt_cache_hit_tokens}


class ModelClient:
    """One chat-completion transport with bounded retries and usage accounting."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self.usage = Usage()

    def probe(self) -> dict:
        """Smallest possible connectivity call, used for the delivery record."""
        started = time.time()
        answer = self.chat([{'role': 'user', 'content': 'Reply with exactly: ok'}],
                           max_tokens=8)
        return {'ok': answer['content'].strip().lower().startswith('ok'),
                'model': answer['model'],
                'content': answer['content'].strip()[:80],
                'latency_s': round(time.time() - started, 3),
                'usage': answer['usage']}

    def chat(self, messages: list, tools: list | None = None,
             max_tokens: int | None = None) -> dict:
        payload = {
            'model': self.config.model,
            'messages': messages,
            'temperature': self.config.temperature,
            'thinking': {'type': 'disabled'},
            'stream': False,
        }
        limit = max_tokens if max_tokens is not None else self.config.max_tokens
        if limit is not None:
            payload['max_tokens'] = limit
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'
        started = time.time()
        data = self._post(payload)
        latency = round(time.time() - started, 3)
        try:
            choice = data['choices'][0]
            message = choice.get('message') or {}
        except (KeyError, IndexError, TypeError) as error:
            raise ModelError('unexpected model response: ' + str(error)) from error
        usage = data.get('usage') or {}
        self.usage.add(usage)
        return {
            'content': message.get('content') or '',
            'tool_calls': message.get('tool_calls') or [],
            'finish_reason': choice.get('finish_reason'),
            'model': data.get('model'),
            'usage': usage,
            'latency_s': latency,
        }

    def _post(self, payload: dict) -> dict:
        url = self.config.base_url.rstrip('/') + '/chat/completions'
        body = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + self.config.api_key}
        last_error = 'unknown error'
        for attempt in range(self.config.max_retries):
            request = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                last_error = 'HTTP %s: %s' % (error.code, _body_tail(error))
                if error.code not in (429, 500, 502, 503, 504):
                    raise ModelError(last_error) from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = 'transport: ' + str(error)
            if attempt < self.config.max_retries - 1:
                time.sleep(2 ** attempt)
        raise ModelError('model call failed after retries: ' + last_error)


def _body_tail(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode('utf-8', 'replace')[:300]
    except OSError:
        return ''
