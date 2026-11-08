"""The no-gate route executes a complete task without any checker invocation."""
import json
from pathlib import Path

import pytest

from nsbi_agent.workflow import checks, graph, runner
from nsbi_agent.workflow.prompts import stage_analysis, ungated_system_prompt
from nsbi_agent.workflow.state import PROJECT_ROOT, TaskStore


def test_no_gate_graph_runs_without_checker_feedback(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / 'workspace').create()
    def forbidden(*args, **kwargs):
        raise AssertionError('automatic checker called in no-gate workflow')
    for name in ['run_environment_check', 'run_spec_check', 'run_card_check', 'run_sample_check', 'run_experience_check']:
        monkeypatch.setattr(checks, name, forbidden)
    request = store.write_json('request.json', {'simulation': {'process': {'initial_state': ['p','p'],
        'final_state': ['e+','e-']}, 'generation': {'nevents': 2}}, 'analysis': {}})
    defaults = store.write_json('defaults.json', {})
    cards = {}
    for name in ['param_card', 'run_card', 'process_card']:
        cards[name] = str(store.write_json('cards/' + name + '.json', {}))
    script = store.workspace / 'run.sh';script.write_text('true\n')
    event = store.workspace / 'Events/events.lhe';event.parent.mkdir();event.write_text('sample')
    banner = event.parent / 'run_01_banner.txt';banner.write_text('native banner')
    artifacts = {key: str(store.write_json('analysis/' + key + '.json', {}))
                 for key in ['plot','table','program','summary']}
    seen = []
    class Client:
        def chat(self, messages, **kwargs):
            assert messages[0]['content'] == ungated_system_prompt()
            user = messages[1]['content']
            if '## Stage: plan_resolve' in user:
                stage='plan_resolve';extra={'artifacts': {'request': str(request), 'defaults': str(defaults)}}
            elif '## Stage: prepare_cards' in user:
                stage='prepare_cards';extra={'artifacts':cards, 'proposed_job': {'script': str(script),
                    'cwd':str(store.workspace), 'expected_outputs':[str(event.parent)]}}
            else:
                assert 'No automatic sample-check report' in user
                stage='analyze_deliver';extra={'artifacts':artifacts}
            seen.append(stage)
            call={'id':str(len(seen)), 'type':'function', 'function':{'name':'finish_stage',
                'arguments':json.dumps({'stage':stage,'complete':True,'summary':'done',**extra})}}
            return {'content':'','tool_calls':[call], 'usage':{},'model':'fake','latency_s':0}
    task={'task_id':'test','task_file':'test.json','workspace':str(store.workspace),'user_request':'test',
          'runtime':{**runner.DEFAULT_RUNTIME,'gates_enabled':False}, 'experience':{},'fixtures':[],
          'analysis':{},'task_type':'generation'}
    controller=graph.TaskController(store,Client(),task)
    record={'job_id':'fake','cwd':str(store.workspace),'expected_outputs':[str(event.parent)],
            'status':'finished','exit_code':0,'elapsed_s':1}
    monkeypatch.setattr(controller.jobs,'start',lambda **kwargs:record)
    monkeypatch.setattr(controller.jobs,'status',lambda job:record)
    monkeypatch.setattr(graph,'load_job',lambda *args:record)
    result=controller.start(runner.initial_state(task))
    assert result['status']=='completed' and result['checks']=={} and result['check_history']==[]
    assert seen==['plan_resolve','prepare_cards','analyze_deliver']
    assert 'requested_configuration' in result['delivery'] and 'verified_configuration' not in result['delivery']
    rows=[json.loads(line) for line in store.trajectory_path.open()]
    assert not any(row['kind']=='check_result' for row in rows)
    assert not any(row.get('name') in ['check_environment','check_spec','check_cards','check_sample','dispositions','experience'] for row in rows if row['kind']=='node')


@pytest.mark.parametrize('value',[0,1,'false',None])
def test_gate_setting_must_be_boolean(tmp_path,value):
    path=tmp_path/'task.json';path.write_text(json.dumps({'task_id':'t','workspace':str(PROJECT_ROOT/'workspace/t'),
        'user_request':'test','runtime':{'gates_enabled':value}}))
    with pytest.raises(runner.TaskFileError,match='gates_enabled'):runner.load_task_file(path)
