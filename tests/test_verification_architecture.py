"""Positive cases, changed intent, tampered evidence, and honest coverage boundaries."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from nsbi_agent import backend
from nsbi_agent.catalog import load_catalog
from nsbi_agent.contracts import EnvironmentRequest
from nsbi_agent.graph import run_spec_graph
from nsbi_agent.tools.card_validator import validate_cards
from nsbi_agent.tools.environment import PROJECT_ROOT, inspect_environment
from nsbi_agent.tools.spec import validate_spec
from nsbi_agent.validation_contracts import CardValidationRequest, IntentRequest


def check(payload):
    return validate_spec(IntentRequest.model_validate(payload))


def test_inventory_does_not_require_tutorial_version_assertions():
    result = inspect_environment(EnvironmentRequest(package_names=['six'], backend_names=['madgraph']))
    assert result.status == 'succeeded'
    assert result.data['packages']['six']['version'] == '1.17.0'
    assert result.data['backends']['madgraph']['version'] == '2.9.27'
    assert result.data['tool_number'] == 1


@pytest.mark.parametrize('flavour', ['general', 'U35', 'MFV', 'top', 'topU3l'])
@pytest.mark.parametrize('ew', ['MwScheme', 'alphaScheme'])
def test_all_variants_expose_catalog_and_zero_sm_point(flavour, ew):
    model = {'collection': 'official', 'flavour_scheme': flavour, 'ew_input_scheme': ew}
    result = check({'simulation': {'process': 'e- e+ > mu- mu+', 'model': model}})
    assert result.status == 'succeeded', result.model_dump()
    assert result.data['baseline']['contribution'] == 'sm'
    assert not any(result.data['baseline']['coefficients'].values())
    assert result.data['generation'] == {}  # no fabricated seeds/beams/cuts
    assert result.data['missing_execution_settings']
    catalog, _ = load_catalog(f'SMEFTsim_{flavour}_{ew}_UFO', '3.0.2', 'official')
    assert len(result.data['baseline']['coefficients']) == sum(r['block'] in {'SMEFT','SMEFTCPV','SMEFTFV'} for r in catalog['parameters'].values())


@pytest.mark.parametrize('edit', [
    {'stage': 'nlo'}, {'process': 'p p > z, z > e- e+'},
    {'generation': {'custom_cut': 'user defined'}}, {'model': {'family': 'other'}},
    {'eft': {'contribution': 'custom_interference'}},
])
def test_unimplemented_intent_is_preserved_and_not_declared_unphysical(edit):
    payload = {'simulation': {'process': 'h > b b~', **edit}}
    result = check(payload)
    assert result.status == ('succeeded' if 'stage' in edit or 'generation' in edit else 'needs_review'), result.model_dump()
    assert result.data['original_request'] == payload
    assert not result.errors


@pytest.mark.parametrize('edit', [
    {'sm_inputs': {'MW': 100.0}}, {'sm_inputs': {'Me': -1.0}},
    {'eft': {'contribution': 'sm', 'coefficients': {'cHbox': 1.0}}},
    {'eft': {'coefficients': {'missingCoefficient': 1.0}}},
    {'eft': {'cutoff_GeV': True}}, {'generation': {'nevents': True}},
    {'model': {'model_id': 'SMEFTsim_U35_MwScheme_UFO', 'flavour_scheme': 'general'}},
])
def test_known_contradictions_are_failures(edit):
    assert check({'simulation': {'process': 'h > b b~', **edit}}).status == 'failed'


def test_reweight_points_reset_and_analysis_does_not_change_simulation():
    payload = {'simulation': {'process': 'h > b b~', 'eft': {'contribution': 'interference', 'coefficients': {'cHbox': 2.0}},
                             'reweighting': [{'weight_id': 'sm', 'coefficients': {}},
                                            {'weight_id': 'p2', 'contribution': 'interference', 'coefficients': {'cHW': 3.0}}]},
               'analysis': {'observable': 'm_bb', 'bins': 20}}
    result = check(payload)
    assert result.status == 'succeeded' and not result.errors
    assert result.data['reweighting'][0]['coefficients']['cHbox'] == 0
    assert result.data['reweighting'][1]['coefficients']['cHbox'] == 0
    changed = copy.deepcopy(payload)
    changed['analysis']['bins'] = 30
    assert check(changed).data['intent_sha256'] != result.data['intent_sha256']
    assert run_spec_graph(IntentRequest.model_validate(payload)).model_dump() == result.model_dump()


@pytest.fixture
def card_case(tmp_path):
    """Agent-like file production in arbitrary paths. No archived writer is imported."""
    def make(flavour='U35', ew='MwScheme', mass=0.0, cut=None):
        model = {'collection': 'official', 'flavour_scheme': flavour, 'ew_input_scheme': ew, 'restriction': 'full'}
        model_id = f'SMEFTsim_{flavour}_{ew}_UFO'
        catalog, _ = load_catalog(model_id, '3.0.2', 'official')
        root = tmp_path / (flavour + ew + str(mass)); root.mkdir(exist_ok=True)
        params, blocks = [], {}
        for name, row in catalog['parameters'].items():
            block, code = row['block'], row['code']
            value = 0.0 if block in {'SMEFT', 'SMEFTCPV', 'SMEFTFV'} else row['ufo_default']
            if name == 'MMU': value = mass
            if block == 'DECAY': params.append(f'DECAY {code[0]} {value}')
            else: blocks.setdefault(block, []).append(' '.join(map(str, [*code, value])) + ' # ' + name)
        for block, rows in blocks.items(): params.extend(['BLOCK ' + block, *rows])
        param = root / 'parameters.txt'; param.write_text('\n'.join(params) + '\n')
        run = root / 'settings.any'; run.write_text('''1000 = nevents
7 = iseed
0 = lpp1
0 = lpp2
125 = ebeam1
125 = ebeam2
0 = ptl
-1 = ptlmax
0 = etalmin
-1 = etal
0 = mmll
-1 = mmllmax
''')
        reference = json.loads((PROJECT_ROOT/'src/nsbi_agent/data/run_card_lo.json').read_text())
        already = {'ptl', 'ptlmax', 'etalmin', 'etal', 'mmll', 'mmllmax'}
        with run.open('a') as stream:
            for key in reference['cut_parameters']:
                if key not in already and key in reference['defaults']: stream.write(repr(reference['defaults'][key]) + ' = ' + key + '\n')
        process = root / 'arbitrary_process.txt'
        ufo = PROJECT_ROOT / 'models/smeftsim/official/3.0.2' / model_id
        process.write_text(f'import model {ufo}-full\ngenerate e- e+ > mu- mu+ SMHLOOP=0 NP=0 NPprop=0\noutput elsewhere\n')
        simulation = {'model': model, 'process': 'e- e+ > mu- mu+', 'sm_inputs': {'MMU': mass},
                      'generation': {'nevents': 1000, 'seed': 7, 'beam_energies_GeV': [125.0, 125.0], 'beam_types': [0, 0], 'cuts': []}}
        if cut:
            simulation['generation']['cuts'] = [cut]
            run.write_text(run.read_text().replace('-1 = etal', '2.5 = etal'))
        payload = {'request': {'simulation': simulation}, 'artifacts': {'param_card': str(param), 'run_card': str(run), 'process_card': str(process)}}
        return payload
    return make


def validate(payload):
    return validate_cards(CardValidationRequest.model_validate(payload))


@pytest.mark.parametrize('flavour', ['general', 'U35', 'MFV', 'top', 'topU3l'])
@pytest.mark.parametrize('ew', ['MwScheme', 'alphaScheme'])
def test_cards_need_no_writer_manifest_and_allow_lepton_beams(card_case, flavour, ew):
    result = validate(card_case(flavour, ew))
    assert result.status == 'succeeded', result.model_dump()
    assert result.data['tool_number'] == 3
    assert all('card_bundles' not in a['path'] for a in result.artifacts)


@pytest.mark.parametrize('change', ['parameter', 'events', 'process', 'fingerprint', 'extra_process', 'duplicate_run'])
def test_actual_evidence_changes_are_caught(card_case, change):
    payload = card_case()
    if change == 'fingerprint': payload['expected_intent_sha256'] = '0' * 64
    elif change == 'parameter':
        f = Path(payload['artifacts']['param_card']); f.write_text(f.read_text().replace('13 0.0 # MMU', '13 1.0 # MMU'))
    elif change in {'events', 'duplicate_run'}:
        f = Path(payload['artifacts']['run_card']); f.write_text(f.read_text().replace('1000 = nevents', '2000 = nevents') if change == 'events' else f.read_text() + '2000 = nevents\n')
    else:
        f = Path(payload['artifacts']['process_card'])
        f.write_text(f.read_text().replace('> mu- mu+', '> e- e+') if change == 'process' else f.read_text() + 'add process h > b b~\n')
    assert validate(payload).status == 'failed'


@pytest.mark.parametrize('mass,observable,expected', [
    (0.0, 'abs_charged_lepton_eta', 'succeeded'),
    (0.105, 'abs_charged_lepton_eta', 'failed'),
    (0.105, 'abs_charged_lepton_rapidity', 'succeeded'),
    (20.0, 'abs_charged_lepton_rapidity', 'succeeded'),
    (20.001, 'abs_charged_lepton_rapidity', 'failed'),
])
def test_native_eta_y_and_heavy_cut_boundary(card_case, mass, observable, expected):
    payload = card_case(mass=mass, cut={'observable': observable, 'minimum': None, 'maximum': 2.5, 'unit': 'dimensionless'})
    result = validate(payload)
    assert result.status == expected, result.model_dump()


def test_heavy_muon_without_a_requested_cut_remains_allowed(card_case):
    assert validate(card_case(mass=21.0)).status == 'succeeded'


@pytest.mark.parametrize('change', ['version', 'missing_param', 'reweight', 'missing_banner', 'additional_cut'])
def test_incomplete_evidence_is_unverified(card_case, change):
    payload = card_case()
    if change == 'version': payload['backend_version'] = 'other'
    elif change == 'missing_param':
        f = Path(payload['artifacts']['param_card']); f.write_text(f.read_text().replace('13 0.0 # MMU', ''))
    elif change == 'reweight': payload['request']['simulation']['reweighting'] = [{'weight_id': 'sm'}]
    elif change == 'additional_cut':
        f = Path(payload['artifacts']['run_card']); f.write_text(f.read_text().replace('0.0 = ptj\n', '10.0 = ptj\n'))
    else: payload['phase'] = 'run_record'
    assert validate(payload).status == 'needs_review'


def test_banner_readback_checks_configuration_without_claiming_events(card_case):
    payload = card_case()
    paths = payload['artifacts']
    banner = Path(paths['run_card']).parent / 'record.txt'
    banner.write_text(''.join(f'<{tag}>\n{Path(paths[key]).read_text()}\n</{tag}>' for key, tag in [('param_card', 'slha'), ('run_card', 'mgruncard'), ('process_card', 'mg5proccard')]))
    paths['banner'] = str(banner); payload['phase'] = 'run_record'
    result = validate(payload)
    assert result.status == 'succeeded' and result.data['recorded_configuration_present']
    banner.write_text(banner.read_text().replace('1000 = nevents', '2000 = nevents'))
    assert validate(payload).status == 'failed'


def test_symlink_evidence_is_not_followed(card_case):
    payload = card_case()
    source = Path(payload['artifacts']['run_card']); alias = source.with_name('alias'); alias.symlink_to(source)
    payload['artifacts']['run_card'] = str(alias)
    assert validate(payload).status == 'failed'


@pytest.mark.parametrize('change', ['auto_width', 'repeated_block', 'implicit_cut'])
def test_native_syntax_or_defaults_outside_coverage_remain_unverified(card_case, change):
    payload = card_case()
    if change == 'implicit_cut':
        f = Path(payload['artifacts']['run_card']); f.write_text(f.read_text().replace('0.0 = drll\n', ''))
    else:
        f = Path(payload['artifacts']['param_card'])
        if change == 'auto_width':
            lines = f.read_text().splitlines(); lines[0] = 'DECAY ' + lines[0].split()[1] + ' Auto'; f.write_text('\n'.join(lines)+'\n')
        else:
            f.write_text(f.read_text() + 'BLOCK MASS Q=100\n')
    assert validate(payload).status == 'needs_review'


def test_actual_fixed_restriction_cannot_satisfy_independent_variation(tmp_path):
    baseline = PROJECT_ROOT/'tests/fixtures/sm_decay_cards'
    process = tmp_path/'proc_card.mg5'
    process.write_text((baseline/'proc_card.mg5').read_text().replace('{PROJECT_ROOT}', str(PROJECT_ROOT)))
    payload = {'request': {'simulation': {'process': 'h > e- ve~ mu+ vm', 'eft': {'varying_coefficients': ['cHbox']}}},
               'artifacts': {'param_card': str(baseline/'param_card.dat'), 'run_card': str(baseline/'run_card.dat'), 'process_card': str(process)}}
    result = validate(payload)
    assert result.status == 'failed'
    assert any(c.name == 'restriction.varying.cHbox' and c.status == 'fail' for c in result.checks)


def test_scale_factor_changes_the_requested_fixed_scale(card_case):
    payload = card_case(); payload['request']['production'] = {'renormalization_scale_GeV': 125.0}
    file = Path(payload['artifacts']['run_card'])
    file.write_text(file.read_text() + 'True = fixed_ren_scale\n125.0 = scale\n1.0 = scalefact\n')
    assert validate(payload).status == 'succeeded'
    file.write_text(file.read_text().replace('1.0 = scalefact', '2.0 = scalefact'))
    assert validate(payload).status == 'failed'


@pytest.mark.parametrize('content', ['{"simulation":{"process":"h > b b~","eft":{"cutoff_GeV":1e999}}}',
                                   '{"simulation":{"process":"h > b b~"},"simulation":{}}'])
def test_nonfinite_or_ambiguous_json_never_crashes_the_cli(tmp_path, content):
    file = tmp_path/'bad.json'; file.write_text(content)
    done = subprocess.run([str(PROJECT_ROOT/'run.sh'), 'validate-spec', str(file)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 2 and not done.stderr
    assert json.loads(done.stdout)['errors'][0]['code'] == 'INVALID_INPUT'


@pytest.mark.parametrize('command,expected', [('write-cards', 2), ('match-model', 2), ('cards-workflow', 2)])
def test_archived_operations_cannot_run(command, expected):
    done = subprocess.run([str(PROJECT_ROOT/'run.sh'), command], capture_output=True, text=True, timeout=30)
    result = json.loads(done.stdout)
    assert done.returncode == expected and result['errors'][0]['code'] == 'ARCHIVED_COMMAND'
