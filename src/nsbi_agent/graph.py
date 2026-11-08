"""LangGraph entrypoints for environment and request checks."""
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from nsbi_agent.contracts import EnvironmentRequest, ToolResult
from nsbi_agent.tools.environment import inspect_environment
from nsbi_agent.tools.spec import validate_spec
from nsbi_agent.validation_contracts import IntentRequest


class CheckState(TypedDict):
    request: dict
    result: dict


def build_check_graph(schema, function):
    def check(state):
        result = function(schema.model_validate(state['request']))
        return {'result': result.model_dump(mode='json')}
    graph = StateGraph(CheckState)
    graph.add_node('check', check)
    graph.add_edge(START, 'check')
    graph.add_edge('check', END)
    return graph.compile()


def run_graph(request: EnvironmentRequest):
    state = build_check_graph(EnvironmentRequest, inspect_environment).invoke({'request': request.model_dump(exclude_unset=True)})
    return ToolResult.model_validate(state['result'])


def run_spec_graph(request: IntentRequest):
    state = build_check_graph(IntentRequest, validate_spec).invoke({'request': request.original()})
    return ToolResult.model_validate(state['result'])
