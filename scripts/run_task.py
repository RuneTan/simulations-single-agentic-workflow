"""Run or resume one task and wait for its jobs in the same batch allocation."""
import argparse
import json
from pathlib import Path
import time

from nsbi_agent.interaction.tasks import create_task, save_outputs
from nsbi_agent.workflow.runner import run_resume, run_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--request-file', type=Path, help='UTF-8 natural-language request')
    inputs.add_argument('--resume', type=Path, help='Existing task JSON')
    parser.add_argument('--cores', type=int, default=2)
    parser.add_argument('--job-timeout', type=int, default=10800)
    args = parser.parse_args()
    if args.cores < 1 or args.job_timeout < 1:
        parser.error('cores and job-timeout must be positive')
    if args.resume:
        task = args.resume.resolve()
        result = run_resume(task)
    else:
        task = create_task(args.request_file.read_text(encoding='utf-8'),
                           runtime={'cores': args.cores, 'job_timeout_seconds': args.job_timeout})
        print('Task:', task, flush=True)
        result = run_task(task)
    while result['exit_code'] == 3:
        time.sleep(10)
        result = run_resume(task)
    save_outputs(task, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
