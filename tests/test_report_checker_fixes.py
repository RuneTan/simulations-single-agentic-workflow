"""Regression controls for REPORT F1–F6, including the actual CLI boundary."""
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys

import pytest

from nsbi_agent import model_store
from nsbi_agent.contracts import EnvironmentRequest
from nsbi_agent.tools.card_validator import validate_cards
from nsbi_agent.tools.environment import PROJECT_ROOT, inspect_environment
from nsbi_agent.tools.spec import validate_spec
from nsbi_agent.validation_contracts import CardValidationRequest, IntentRequest
from test_catalog import copy_official_default
from test_verification_architecture import card_case


@pytest.mark.parametrize('term,selectors,status', [
    ('sm', 'NP=0 NPprop=0', 'succeeded'),
    ('sm', 'NP=0 NPprop=0 NP^2==1', 'failed'),
    ('sm', 'NP=0 NPprop=0 NPprop^2==2', 'failed'),
    ('interference', 'NP<=1 NP^2==1 NPprop=0 NPprop^2==2', 'failed'),
    ('sm', 'NP==0 NPprop==0 NP^2<=2 NPprop^2==0', 'succeeded'),
    ('interference', 'NP<=1 NP^2==1 NPprop=0', 'succeeded'),
    ('dim6_squared', 'NP==1 NP^2==2 NPprop=0', 'succeeded'),
    ('dim6_squared', 'NP<=1 NP^2==2 NPprop=0', 'succeeded'),
    ('propagator_interference', 'NP=0 NPprop<=2 NPprop^2==2', 'succeeded'),
    ('interference', 'NP<=1 NP^2<=1 NPprop=0', 'needs_review'),
    ('dim6_squared', 'NP^2==2 NPprop=0', 'needs_review'),
    ('dim6_squared', 'NP<=999999 NP^2==999999 NPprop=0', 'needs_review'),
])
def test_all_explicit_eft_constraints_are_interpreted(card_case, term, selectors, status):
    payload = card_case()
    payload['request']['simulation']['eft'] = {'contribution': term}
    if term == 'propagator_interference':
        param = Path(payload['artifacts']['param_card'])
        lines = param.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.endswith('# linearPropCorrections'):
                lines[i] = line.split()[0] + ' 1.0 # linearPropCorrections'
        param.write_text('\n'.join(lines) + '\n')
    proc = Path(payload['artifacts']['process_card'])
    proc.write_text(proc.read_text().replace('NP=0 NPprop=0', selectors))
    intent = validate_spec(IntentRequest.model_validate(payload['request']))
    payload['expected_intent_sha256'] = intent.data['intent_sha256']
    result = validate_cards(CardValidationRequest.model_validate(payload))
    assert result.status == status, result.model_dump()
    assert any(c.name == 'intent.sha256' and c.status == 'pass' for c in result.checks)


@pytest.mark.parametrize('shared,per_beam,status', [
    (True, {}, 'succeeded'),
    (False, {'fixed_fac_scale1': True, 'fixed_fac_scale2': True}, 'succeeded'),
    (True, {'fixed_fac_scale1': False, 'fixed_fac_scale2': False}, 'failed'),
    (True, {'fixed_fac_scale1': True, 'fixed_fac_scale2': False}, 'failed'),
    (True, {'fixed_fac_scale1': True}, 'succeeded'),
    (False, {'fixed_fac_scale2': True}, 'failed'),
    (None, {'fixed_fac_scale1': True, 'fixed_fac_scale2': True}, 'succeeded'),
    (None, {'fixed_fac_scale1': True}, 'needs_review'),
])
def test_effective_factorization_switches_override_shared_flag(card_case, shared, per_beam, status):
    payload = card_case()
    sim = payload['request']['simulation']
    sim.update(process='u u~ > mu- mu+')
    sim['generation'].update(beam_types=[1, 1], pdf={'label': 'lhapdf', 'set_id': 247000})
    payload['request']['production'] = {'factorization_scale_GeV': 91.0}
    proc = Path(payload['artifacts']['process_card']); proc.write_text(proc.read_text().replace('e- e+', 'u u~'))
    run = Path(payload['artifacts']['run_card'])
    text = run.read_text().replace('0 = lpp1', '1 = lpp1').replace('0 = lpp2', '1 = lpp2')
    settings = {'dsqrt_q2fact1': 91.0, 'dsqrt_q2fact2': 91.0, 'pdlabel': 'lhapdf', 'lhaid': 247000, **per_beam}
    if shared is not None: settings['fixed_fac_scale'] = shared
    run.write_text(text + ''.join(repr(value) + ' = ' + name + '\n' for name, value in settings.items()))
    intent = validate_spec(IntentRequest.model_validate(payload['request']))
    payload['expected_intent_sha256'] = intent.data['intent_sha256']
    result = validate_cards(CardValidationRequest.model_validate(payload))
    assert result.status == status, result.model_dump()


def test_exact_zero_and_zero_caps_have_identical_coupling_meaning(card_case):
    payload = card_case()
    payload['request']['production'] = {'qcd_order': 0, 'qed_order': 2}
    proc = Path(payload['artifacts']['process_card'])
    proc.write_text(proc.read_text().replace('SMHLOOP=0', 'QCD==0 QED<=2 SMHLOOP==0'))
    assert validate_cards(CardValidationRequest.model_validate(payload)).status == 'succeeded'


def test_combined_prediction_routes_channel_review_to_agent(card_case):
    payload = card_case()
    payload['request']['simulation']['eft'] = {'prediction':'sm_plus_linear', 'propagator_corrections':'excluded'}
    proc = Path(payload['artifacts']['process_card'])
    proc.write_text(proc.read_text()+'add process e- e+ > mu- mu+ SMHLOOP=0 NP<=1 NP^2==1 NPprop=0\n')
    result = validate_cards(CardValidationRequest.model_validate(payload))
    assert result.status == 'needs_review' and not result.errors
    assert any(item['topic']=='process_card' for item in result.data['review_items'])


@pytest.mark.parametrize('label', [None, [], {}, 17, '', ' \t'])
def test_known_pdf_label_requires_a_nonempty_string(label):
    request = IntentRequest(simulation={'process': 'u u~ > mu- mu+', 'generation': {'pdf': {'label': label, 'set_id': 247000}}})
    result = validate_spec(request)
    assert result.status == 'failed'
    assert any(c.name == 'generation.pdf.label' and c.status == 'fail' for c in result.checks)


def test_well_typed_unknown_pdf_label_is_preserved_and_unverified():
    payload = {'simulation': {'process': 'u u~ > mu- mu+', 'generation': {'pdf': {'label': 'future_pdf_label', 'set_id': 1}}}}
    result = validate_spec(IntentRequest.model_validate(payload))
    assert result.status == 'needs_review' and not result.errors
    assert result.data['original_request'] == payload


@pytest.mark.parametrize('version_line', ['', 'Version: \n', 'Version:    \n'])
@pytest.mark.parametrize('assert_expected', [False, True])
def test_damaged_package_metadata_is_structured_and_other_packages_continue(tmp_path, monkeypatch, version_line, assert_expected):
    directory = tmp_path / 'bad_metadata-1.dist-info'; directory.mkdir()
    (directory/'METADATA').write_text('Name: bad-metadata\n' + version_line)
    damaged = metadata.PathDistribution(directory)
    original = metadata.distribution
    monkeypatch.setattr(metadata, 'distribution', lambda name: damaged if name == 'bad-metadata' else original(name))
    request = EnvironmentRequest(package_names=['bad-metadata', 'six'], packages={'bad-metadata': '1'} if assert_expected else {})
    result = inspect_environment(request)
    assert result.status == 'failed'
    assert result.data['packages']['bad-metadata']['error_code'] == 'PACKAGE_VERSION_UNREADABLE'
    assert result.data['packages']['six']['version'] == '1.17.0'
    result.model_dump_json()  # None must never enter Check.actual.


def test_cli_returns_json_for_real_missing_version_metadata(tmp_path):
    directory = tmp_path/'bad-1.dist-info'; directory.mkdir(); (directory/'METADATA').write_text('Name: bad\n')
    request = tmp_path/'request.json'; request.write_text('{"package_names":["bad"]}')
    runner = '''import runpy,sys
from pathlib import Path
from importlib import metadata
distribution=metadata.PathDistribution(Path(sys.argv.pop(1)))
metadata.distribution=lambda name:distribution
sys.argv[0]='nsbi_agent'
runpy.run_module('nsbi_agent',run_name='__main__')
'''
    done = subprocess.run([sys.executable, '-I', '-B', '-c', runner, str(directory), 'inspect', str(request)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 1 and not done.stderr
    assert json.loads(done.stdout)['data']['packages']['bad']['error_code'] == 'PACKAGE_VERSION_UNREADABLE'


@pytest.mark.parametrize('loop_kind', ['self', 'mutual'])
def test_model_loops_are_structured_through_all_tools_and_cli(tmp_path, monkeypatch, card_case, loop_kind):
    payload = card_case()  # Create evidence before redirecting the model store.
    store, record = copy_official_default(tmp_path, monkeypatch)
    path = store/record['ufo_directory']/'parameters.py'
    path.unlink()
    if loop_kind == 'self': path.symlink_to('parameters.py')
    else:
        path.symlink_to('loop.py'); path.with_name('loop.py').symlink_to('parameters.py')
    model = {'collection': 'official'}
    intent = {'simulation': {'process': 'e- e+ > mu- mu+', 'model': model}}
    for result in (validate_spec(IntentRequest.model_validate(intent)),
                   validate_cards(CardValidationRequest.model_validate(payload))):
        assert result.status == 'failed'
        assert any(e.code == 'UNSAFE_MODEL_PATH' for e in result.errors)
    runner = '''import runpy,sys
from pathlib import Path
from nsbi_agent import model_store
model_store.STORE_ROOT=Path(sys.argv.pop(1))
sys.argv[0]='nsbi_agent'
runpy.run_module('nsbi_agent',run_name='__main__')
'''
    for command, data in [('validate-spec', intent), ('workflow', intent), ('validate-cards', payload)]:
        source = tmp_path/(command+'.json'); source.write_text(json.dumps(data))
        done = subprocess.run([sys.executable, '-I', '-B', '-c', runner, str(store), command, str(source)], capture_output=True, text=True, timeout=30)
        assert done.returncode == 1 and not done.stderr
        result = json.loads(done.stdout)
        assert result['status'] == 'failed' and any(e['code'] == 'UNSAFE_MODEL_PATH' for e in result['errors'])
