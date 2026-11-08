"""Five checkers plus the single-Agent task workflow.
"""
import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from nsbi_agent.artifact_readers import load_json
from nsbi_agent.contracts import EnvironmentRequest, ToolError, ToolResult
from nsbi_agent.tools.environment import inspect_environment
from nsbi_agent.tools.spec import validate_spec
from nsbi_agent.tools.card_validator import validate_cards
from nsbi_agent.tools.sample import check_sample
from nsbi_agent.tools.experience import check_experience
from nsbi_agent.validation_contracts import CardValidationRequest, IntentRequest, SampleRequest, ExperienceRequest


def environment_graph(request):
    from nsbi_agent.graph import run_graph
    return run_graph(request)


def request_graph(request):
    from nsbi_agent.graph import run_spec_graph
    return run_spec_graph(request)


COMMANDS = {
    'inspect': (EnvironmentRequest, inspect_environment, 'inspect_environment'),
    'graph': (EnvironmentRequest, environment_graph, 'inspect_environment'),
    'validate-spec': (IntentRequest, validate_spec, 'validate_spec'),
    'validate-spec-graph': (IntentRequest, request_graph, 'validate_spec'),
    'workflow': (IntentRequest, request_graph, 'validate_spec'),
    'validate-cards': (CardValidationRequest, validate_cards, 'validate_cards'),
    'check-sample': (SampleRequest, check_sample, 'check_sample'),
    'check-experience': (ExperienceRequest, check_experience, 'check_experience'),
}
TASK_COMMANDS = {'task', 'resume'}
ARCHIVED = {'match-model', 'write-cards', 'cards-workflow', 'resolve-request', 'load-model', 'query-model'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', help='1 inspect; 2 validate-spec; 3 validate-cards; 4 check-sample; '
                                        '5 check-experience; graph/workflow; chat; task/resume <task.json>')
    parser.add_argument('input', type=Path, nargs='?', help='JSON request or task file; inspect/graph accept nothing')
    args = parser.parse_args()
    if args.command == 'chat':
        if args.input is not None:
            parser.error('usage: ./run.sh chat')
        from nsbi_agent.interaction.terminal import run_chat
        return run_chat()
    if args.command in ARCHIVED:
        result = ToolResult(tool=args.command, status='failed', errors=[ToolError(
            code='ARCHIVED_COMMAND', message='Retired operation. Use chat for tasks or the five checkers listed in docs/ToolList.md. Model sources: models/smeftsim/.')])
        print(result.model_dump_json(indent=2))
        return 2
    if args.command in TASK_COMMANDS:
        from nsbi_agent.workflow import runner
        if args.input is None:
            summary = {'status': 'invalid', 'exit_code': 2,
                       'message': 'usage: ./run.sh %s /absolute/path/task.json' % args.command}
            print(json.dumps(summary, indent=2))
            return 2
        summary = (runner.run_task(str(args.input)) if args.command == 'task'
                   else runner.run_resume(str(args.input)))
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return int(summary.get('exit_code') or 0)
    if args.command not in COMMANDS:
        parser.error('Unknown command: ' + args.command)
    schema, function, name = COMMANDS[args.command]
    try:
        if args.input is None:
            if args.command not in {'inspect', 'graph'}:
                raise ValueError('A JSON request file is required')
            payload = {}
        else:
            payload = load_json(args.input.read_text(encoding='utf-8'))
        request = schema.model_validate(payload)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        result = ToolResult(tool=name, status='failed', errors=[ToolError(code='INVALID_INPUT', message=str(error))])
        code = 2
    else:
        result = function(request)
        code = {'succeeded': 0, 'failed': 1, 'needs_review': 5, 'needs_configuration': 3, 'no_available_model': 4, 'unverified': 5}[result.status]
    print(result.model_dump_json(indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
