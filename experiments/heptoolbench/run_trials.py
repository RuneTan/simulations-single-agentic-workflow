"""Submit the bundled upstream requests through the NSBI task API."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from nsbi_agent.interaction.tasks import create_task, save_outputs
from nsbi_agent.workflow.runner import run_resume, run_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New directory for the campaign manifest')
    output = parser.parse_args().output.resolve()
    root = Path(__file__).resolve().parent
    requests = json.loads((root / 'requests.json').read_text(encoding='utf-8'))
    provenance = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    manifest = output / 'campaign.json'
    rows = []

    def save():
        pending = manifest.with_suffix('.tmp')
        pending.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        pending.replace(manifest)

    for index, (case, request) in enumerate(requests.items()):
        request_hash = hashlib.sha256(request.encode()).hexdigest()
        if request_hash != provenance['request_sha256'][case]:
            raise ValueError(f'Request hash differs from the upstream snapshot: {case}')
        first_gate = index % 2 == 0
        for gates in (first_gate, not first_gate):
            task = create_task(request, runtime={'gates_enabled': gates})
            row = dict(case=case, gates_enabled=gates, task_id=task.parent.name,
                       task_file=str(task), request_sha256=request_hash)
            rows.append(row)
            save()
            print('START', case, gates, task.parent.name, flush=True)
            result = run_task(task)
            while result['exit_code'] == 3:
                time.sleep(10)
                result = run_resume(task)
            save_outputs(task, result)
            row['result'] = result
            save()
            print('RESULT', task.parent.name, result['status'], result.get('message'), flush=True)


if __name__ == '__main__':
    main()
