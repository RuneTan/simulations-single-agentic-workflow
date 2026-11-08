"""Allocate task directories and save answers without changing the request."""
import json
from pathlib import Path
import re

from nsbi_agent.workflow.state import PROJECT_ROOT

TASKS_ROOT = PROJECT_ROOT / 'workspace' / 'tasks'
TASK_NAME = re.compile(r'task([1-9][0-9]*)')


def create_task(user_request: str, root: Path | None = None, *, runtime: dict | None = None) -> Path:
    """Reserve the next number with mkdir, then write taskN/taskN.json."""
    if not user_request.strip():
        raise ValueError('请输入任务内容。')
    root = Path(root or TASKS_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    numbers = [int(match[1]) for path in root.iterdir()
               if (match := TASK_NAME.fullmatch(path.name))]
    number = max(numbers, default=0) + 1
    while True:
        workspace = root / f'task{number}'
        try:
            workspace.mkdir()
            break
        except FileExistsError:
            number += 1
    task_id = workspace.name
    path = workspace / f'{task_id}.json'
    payload = {'task_id': task_id, 'user_request': user_request.strip(), 'workspace': str(workspace)}
    if runtime is not None:
        payload['runtime'] = dict(runtime)
    _write_json(path, payload)
    return path


def task_path(name: str, root: Path | None = None) -> Path:
    name = 'task' + name if name.isdecimal() else name
    if not TASK_NAME.fullmatch(name):
        raise ValueError('请输入任务编号，例如 /resume task1。')
    path = Path(root or TASKS_ROOT).resolve() / name / f'{name}.json'
    if not path.is_file():
        raise ValueError(f'找不到 {name}，可用 /tasks 查看已有任务。')
    return path


def save_answer(path: Path, topic: str, answer: str) -> None:
    if not answer.strip():
        raise ValueError('请填写回答，或输入 /quit 稍后继续。')
    payload = json.loads(path.read_text(encoding='utf-8'))
    decisions = dict(payload.get('user_decisions') or {})
    decisions[topic] = answer.strip()
    payload['user_decisions'] = decisions
    _write_json(path, payload)


def list_tasks(root: Path | None = None) -> list[tuple[str, str]]:
    root = Path(root or TASKS_ROOT)
    if not root.is_dir():
        return []
    result = []
    directories = [path for path in root.iterdir() if TASK_NAME.fullmatch(path.name)]
    for path in sorted(directories, key=lambda item: int(TASK_NAME.fullmatch(item.name)[1])):
        state = path / 'state.json'
        status = 'created'
        if state.is_file():
            try:
                status = json.loads(state.read_text(encoding='utf-8')).get('status', 'created')
            except (OSError, ValueError):
                status = '状态文件读取失败'
        result.append((path.name, status))
    return result


def save_outputs(task_file: Path, result: dict) -> Path | None:
    """Index final artifacts in their owning task without duplicating samples."""
    if result.get('status') != 'completed':
        return None
    workspace = task_file.parent.resolve()
    delivery = {**(result.get('delivery') or {}), **(result.get('trajectory') or {})}
    files = {}
    for name in ('plot', 'table', 'events', 'summary', 'program', 'trajectory', 'trajectory_summary'):
        if not delivery.get(name):
            continue
        artifact = Path(delivery[name]).resolve()
        if not artifact.is_relative_to(workspace):
            raise ValueError('任务产物应位于当前工作区：' + str(artifact))
        files[name] = str(artifact)
    if not files:
        return None
    target = workspace / 'outputs.json'
    _write_json(target, {'task_id': workspace.name, 'status': 'completed', 'files': files})
    return target


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
