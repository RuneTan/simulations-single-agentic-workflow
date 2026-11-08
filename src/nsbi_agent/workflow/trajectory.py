"""Append-only visible execution traces and small per-task statistics."""
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def _visible(value):
    """Keep message content; replace embedded image bytes with a fingerprint."""
    if isinstance(value, dict):
        return {key: _visible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_visible(item) for item in value]
    if isinstance(value, str) and value.startswith('data:image/') and ';base64,' in value:
        header, data = value.split(',', 1)
        raw = base64.b64decode(data)
        return {'image_reference': header, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest()}
    return value


class Trajectory:
    def __init__(self, path: Path):
        self.path = path
        self.sequence = 0
        if path.exists():
            with path.open(encoding='utf-8') as stream:
                self.sequence = sum(1 for line in stream if line.strip())

    def append(self, kind: str, **fields):
        self.sequence += 1
        record = {'schema_version': 1, 'sequence': self.sequence,
                  'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                  'kind': kind, **_visible(fields)}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def write_summary(store, task_summary: dict) -> dict:
    counts, tool_counts, check_counts, stage_counts = Counter(), Counter(), Counter(), {}
    calls = tokens_in = tokens_out = tool_errors = 0
    latency = 0.0
    cached_tokens = 0
    if store.trajectory_path.exists():
        with store.trajectory_path.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                kind = row['kind']
                counts[kind] += 1
                stage = row.get('stage') or (row.get('name') if kind == 'node' else None)
                stats = stage_counts.setdefault(stage, {'model_calls': 0, 'tool_calls': 0,
                                                        'node_visits': 0, 'node_seconds': 0.0}) if stage else None
                if kind == 'model_response':
                    calls += 1
                    usage = row.get('usage') or {}
                    tokens_in += int(usage.get('prompt_tokens') or 0)
                    tokens_out += int(usage.get('completion_tokens') or 0)
                    cached_tokens += int(usage.get('prompt_cache_hit_tokens') or
                                         (usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0)
                    latency += float(row.get('latency_s') or 0)
                    if stats: stats['model_calls'] += 1
                elif kind == 'tool_result':
                    tool_counts[row['tool']] += 1
                    tool_errors += int(not row.get('ok', True))
                    if stats: stats['tool_calls'] += 1
                elif kind == 'node' and stats:
                    stats['node_visits'] += 1
                    stats['node_seconds'] += float(row.get('elapsed_s') or 0)
                elif kind == 'check_result':
                    check_counts[row['result']['status']] += 1
    paths = {'trajectory': str(store.trajectory_path),
             'trajectory_summary': str(store.workspace / 'trajectory_summary.json')}
    from nsbi_agent.workflow.jobs import JobManager
    manager = JobManager(store)
    jobs = [manager.status(json.loads(path.read_text()))
            for path in sorted(store.jobs_dir.glob('*/job.json'))]
    job_stats = [{key: job.get(key) for key in
                  ('job_id', 'cwd', 'status', 'exit_code', 'elapsed_s', 'log', 'expected_outputs')}
                 for job in jobs]
    selected_event = (task_summary.get('delivery') or {}).get('events')
    for job in job_stats:
        job['selected_for_delivery'] = bool(selected_event and job.get('cwd') and
                                           Path(selected_event).is_relative_to(Path(job['cwd'])))
    result = {'schema_version': 1, 'task_id': task_summary.get('task_id'), 'status': task_summary.get('status'),
              'gates_enabled': (task_summary.get('runtime') or {}).get('gates_enabled', True),
              'records': sum(counts.values()), 'records_by_kind': dict(counts),
              'model_calls': calls, 'prompt_tokens': tokens_in, 'completion_tokens': tokens_out,
              'model_errors': counts['model_error'],
              'total_tokens': tokens_in + tokens_out, 'model_latency_s': round(latency, 3),
              'prompt_cache_hit_tokens': cached_tokens, 'check_results_by_status': dict(check_counts),
              'tool_calls': sum(tool_counts.values()), 'tools_by_name': dict(tool_counts),
              'tool_errors': tool_errors, 'stages': stage_counts,
              'attempts': task_summary.get('attempts'), 'repairs': task_summary.get('repairs'),
              'human_interventions': task_summary.get('human_interventions'),
              'operator_interventions': counts['operator_intervention'],
              'job_launches': task_summary.get('job_launches'),
              'jobs': job_stats,
              'generator_total_wall_s': round(sum(float(job.get('elapsed_s') or 0) for job in jobs), 3),
              'timing': task_summary.get('timing'), 'checks': task_summary.get('check_history'),
              'outputs': task_summary.get('delivery'), **paths}
    store.write_json('trajectory_summary.json', result)
    return paths
