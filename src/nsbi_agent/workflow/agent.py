"""Bounded tool-use loop for one workflow stage.

Each stage starts a fresh conversation (system prompt + stage goal + prior
check feedback).  The Agent may call the general tools, must end with
``finish_stage``, and the loop caps both the number of model calls and the
size of the context it keeps, so token cost stays predictable.
"""
from __future__ import annotations

import json
import uuid

from nsbi_agent.workflow.model_client import ModelClient, ModelError
from nsbi_agent.workflow.prompts import SYSTEM_PROMPT, ungated_system_prompt
from nsbi_agent.workflow.state import TaskStore
from nsbi_agent.workflow.tools import (TOOL_SCHEMAS, ToolBox, ToolError,
                                       summarize_tool_result)

MAX_STEPS = 12
OLD_TOOL_TRIM = 1200
# Accept the descriptive stage names used in some prompts.
STAGE_ALIASES = {'record_dispositions': 'dispositions', 'analyze': 'analyze_deliver',
                 'cards': 'prepare_cards', 'plan': 'plan_resolve'}


class StageError(RuntimeError):
    """The stage could not be completed (model error, budget, invalid finish)."""


class UserInputRequested(StageError):
    """A planning tool call paused the stage for a concrete user answer."""

    def __init__(self, question: dict):
        super().__init__(question['question'])
        self.question = question


def run_stage(store: TaskStore, client: ModelClient, stage: str, prompt: str,
              toolbox: ToolBox, max_steps: int | None = MAX_STEPS,
              max_tokens: int | None = None, gates_enabled: bool = True) -> dict:
    conversation = [{'role': 'system', 'content': SYSTEM_PROMPT if gates_enabled else ungated_system_prompt()},
                    {'role': 'user', 'content': prompt}]
    toolbox.stage = stage
    active_tools = [tool for tool in TOOL_SCHEMAS
                    if stage == 'plan_resolve' or tool['function']['name'] != 'ask_user']
    stage_run = uuid.uuid4().hex[:12]
    step = 0
    while max_steps is None or step < max_steps:
        step += 1
        store.trace('model_request', stage=stage, stage_run=stage_run, step=step,
                    messages=conversation, tools=active_tools, max_tokens=max_tokens)
        try:
            response = client.chat(conversation, tools=active_tools, max_tokens=max_tokens)
        except ModelError as error:
            store.trace('model_error', stage=stage, stage_run=stage_run, step=step, error=str(error))
            store.trace_messages(stage, conversation)
            raise StageError('model call failed in stage %s: %s' % (stage, error)) from error
        store.trace_model_call(stage, step, {
            'model': response.get('model'),
            'prompt_tokens': response['usage'].get('prompt_tokens'),
            'completion_tokens': response['usage'].get('completion_tokens'),
            'finish_reason': response.get('finish_reason'),
            'latency_s': response.get('latency_s'),
            'tools': [call['function']['name'] for call in response['tool_calls']],
        })
        assistant = {'role': 'assistant', 'content': response['content'] or None}
        if response['tool_calls']:
            assistant['tool_calls'] = response['tool_calls']
        store.trace('model_response', stage=stage, stage_run=stage_run, step=step,
                    assistant=assistant, usage=response['usage'], latency_s=response.get('latency_s'),
                    model=response.get('model'), finish_reason=response.get('finish_reason'))
        conversation.append(assistant)

        if not response['tool_calls']:
            conversation.append({'role': 'user', 'content':
                                 'Call tools directly. When the stage goal is met, call finish_stage now.'})
            if step == max_steps:
                store.trace_messages(stage, conversation)
                raise StageError('stage %s made no tool call in time' % stage)
            continue

        images = []
        for call in response['tool_calls']:
            name = call['function']['name']
            arguments = call['function'].get('arguments') or '{}'
            try:
                parsed = json.loads(arguments)
                if not isinstance(parsed, dict):
                    raise ToolError('tool arguments must be a JSON object')
                result = toolbox.execute(name, parsed)
                ok = True
            except json.JSONDecodeError as error:
                result = {'error': 'tool call arguments are not complete JSON (%s). A long file '
                                   'content was probably truncated by the output limit; write a '
                                   'shorter file or split it into several write/append calls.'
                                   % error}
                ok = False
            except (ToolError, KeyError, ValueError, OSError) as error:
                result = {'error': type(error).__name__ + ': ' + str(error)}
                ok = False
            conversation.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': summarize_tool_result(result)})
            store.event('tool_call', stage=stage, step=step, tool=name, ok=ok,
                        arguments=str(arguments)[:300])
            store.trace('tool_result', stage=stage, stage_run=stage_run, step=step,
                        call_id=call['id'], tool=name, arguments=arguments, result=result, ok=ok)
            if toolbox.pending_question:
                store.trace_messages(stage, conversation)
                raise UserInputRequested(toolbox.pending_question)
            if toolbox.pending_image:
                image, toolbox.pending_image = toolbox.pending_image, None
                images.append(image)
        # A tool-call batch must receive all of its tool replies before another
        # user message (including a plot attachment) enters the conversation.
        for image in images:
            conversation.append({'role': 'user', 'content': [
                    {'type': 'text', 'text':
                     'Image %s (%dx%d px) is attached. Review axis labels, units, error bars, '
                     'bin widths, empty bins and readability; mention concrete observations.'
                     % (image['path'], image['width'], image['height'])},
                    {'type': 'image_url', 'image_url': {'url': image['data_url']}},
            ]})
        _trim_old_tool_outputs(conversation)
        if toolbox.finished is not None:
            finished = toolbox.finished
            toolbox.finished = None
            store.trace_messages(stage, conversation)
            reported = STAGE_ALIASES.get(finished.get('stage'), finished.get('stage'))
            if reported != stage:
                raise StageError('finish_stage reported stage %r while running %r'
                                 % (finished.get('stage'), stage))
            if not finished.get('complete'):
                raise StageError('stage %s finished with complete=false' % stage)
            return finished
        if max_steps is not None and step == max_steps - 2:
            conversation.append({'role': 'user', 'content':
                                 'Budget reminder: you have %d model turns left in this stage. '
                                 'Stop exploring and call finish_stage now '
                                 '(write missing files first if needed).' % (max_steps - step)})
    store.trace_messages(stage, conversation)
    raise StageError('stage %s exceeded %d model steps' % (stage, max_steps))


def _trim_old_tool_outputs(conversation: list) -> None:
    """Keep the recent tool results verbatim, shorten older ones in place."""
    tool_indices = [i for i, message in enumerate(conversation) if message.get('role') == 'tool']
    for index in tool_indices[:-3]:
        content = conversation[index].get('content') or ''
        if len(content) > OLD_TOOL_TRIM:
            conversation[index]['content'] = content[:OLD_TOOL_TRIM] + '\n[older result shortened]'
