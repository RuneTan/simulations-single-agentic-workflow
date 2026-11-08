"""Tool 3: compare actual cards and recorded configuration with the request."""
import json
import math
from pathlib import Path

from nsbi_agent.artifact_readers import DATA, UnsupportedSyntax, banner_sections, parse_lha, read_file, read_run_card
from nsbi_agent.card_checks import check_cuts, check_model_source, check_process, check_run, process_records
from nsbi_agent.catalog import load_catalog
from nsbi_agent.model_store import inspect_local_model
from nsbi_agent.tools.spec import validate_spec
from nsbi_agent.validation import Report
from nsbi_agent.validation_contracts import CardValidationRequest


def validate_cards(request: CardValidationRequest):
    report = Report('validate_cards')
    prepared = validate_spec(request.request)
    report.checks.extend(prepared.checks)
    report.errors.extend(prepared.errors)
    report.reviews.update({item['topic']: item for item in prepared.data.get('review_items', [])})
    report.agent_fields.extend(prepared.data.get('agent_fields', []))
    data = {'tool_number': 3, 'intent_sha256': prepared.data['intent_sha256'], 'phase': request.phase,
            'backend_rules_version': request.backend_version,
            'backend_version_source': 'request.backend_version',
            'scope': 'Model source, card parameters, process orders and recorded configuration.'}
    if request.expected_intent_sha256:
        report.check('intent.sha256', request.expected_intent_sha256 == data['intent_sha256'],
                     request.expected_intent_sha256, data['intent_sha256'], 'INTENT_CHANGED')
    evidence, texts = [], {}
    for role, name in request.artifacts.model_dump(exclude_none=True).items():
        try:
            texts[role], digest = read_file(name)
            evidence.append({'role': role, 'path': name, 'sha256': digest})
        except (OSError, ValueError, UnicodeError, RuntimeError) as error:
            report.check('file.' + role, False, 'readable regular absolute file without symlinks', error, 'INVALID_EVIDENCE')
    if report.errors or 'model' not in prepared.data:
        return report.result(data, evidence)
    if request.request.simulation.get('eft', {}).get('coefficient_policy', 'zero_then_set') != 'zero_then_set':
        report.unknown('param_card.coefficient_policy', request.request.simulation['eft']['coefficient_policy'], 'Resolve the coefficient policy before comparing values')
        return report.result(data, evidence)
    if request.backend_version != '2.9.27':
        report.unknown('backend.semantic_rules', request.backend_version, 'Compare native card semantics for this backend version')
        return report.result(data, evidence)
    try:
        model = prepared.data['model']
        catalog, _ = load_catalog(model['model_id'], model['version'], model['collection'])
        local = (inspect_local_model(model['model_id'], model['version'], registry_name='official_registry.json')
                 if model['collection'] == 'official' else inspect_local_model(model['model_id'], model['version'], catalog['provenance']['files']))
        imports = check_process(report, texts['process_card'], prepared.data, request.request.production)
        selected = request.request.simulation.get('model', {})
        restriction = check_model_source(report, imports, local, evidence, selected.get('restriction'))
        actual_lha = parse_lha(texts['param_card'])
        actual = {name: actual_lha[(row['block'], tuple(row['code']))] for name, row in catalog['parameters'].items()
                  if (row['block'], tuple(row['code'])) in actual_lha}
        expected = {**prepared.data['sm_inputs'], **prepared.data['baseline']['coefficients'],
                    'LambdaSMEFT': prepared.data['cutoff_GeV']}
        eft = request.request.simulation.get('eft', {})
        uses_prop = (prepared.data['baseline']['contribution'] == 'propagator_interference'
                     or eft.get('propagator_corrections') == 'linearized'
                     or any(p['contribution'] == 'propagator_interference' for p in prepared.data['reweighting']))
        if uses_prop:
            expected['linearPropCorrections'] = 1.0
        for name, value in expected.items():
            if name not in actual:
                report.unknown('param_card.' + name, 'parameter absent', 'Read the evaluated restricted card')
            else:
                report.check('param_card.' + name, math.isclose(actual[name], value, rel_tol=2e-12, abs_tol=0), value, actual[name])
        known = {(row['block'], tuple(row['code'])) for row in catalog['parameters'].values()}
        for coord in actual_lha.keys() - known:
            report.unknown('param_card.coordinate', coord)
        # Check the restriction actually imported, never search for or choose a candidate.
        if restriction:
            from nsbi_agent.restriction_cards import parameter_constraints
            rows = parse_lha(restriction)
            values = {n: rows[(r['block'], tuple(r['code']))] for n, r in catalog['parameters'].items()
                      if (r['block'], tuple(r['code'])) in rows}
            if len(values) != len(catalog['parameters']):
                report.unknown('restriction.missing_inputs', sorted(catalog['parameters'].keys() - values.keys()))
            constraints = parameter_constraints(values, catalog['parameters'])
            points = [expected, *[{**expected, **p['coefficients']} for p in prepared.data['reweighting']]]
            for name, fixed in constraints['fixed_values'].items():
                if name in eft.get('varying_coefficients', []):
                    report.check('restriction.varying.' + name, False, 'independently varied input cannot be fixed at import', fixed)
                for i, point in enumerate(points):
                    if name in point:
                        report.check(f'restriction.point.{i}.' + name, point[name] == fixed, fixed, point[name])
            groups = [{n: 1 for n in group} for group in constraints['equality_groups']] + constraints['signed_groups']
            for i, point in enumerate(points):
                for group in groups:
                    if set(group).intersection(eft.get('varying_coefficients', [])):
                        report.check('restriction.independent_variation', False, 'independently varied inputs cannot be tied', group)
                    if all(n in point for n in group):
                        report.check(f'restriction.ties.{i}.' + ','.join(group), len({point[n] * s for n, s in group.items()}) == 1, 'signed/equal imported parameters stay tied', group)
                    else:
                        report.unknown('restriction.ties', group)
        run = read_run_card(texts['run_card'])
        check_run(report, run, prepared.data)
        check_cuts(report, run, prepared.data, actual)
        reference = json.loads((DATA / 'run_card_lo.json').read_text())
        defaults = reference['defaults']
        report.fields(run, defaults.keys(), 'run_card')
        # Additional active cuts are not made harmless by matching the requested cut subset.
        from nsbi_agent.card_checks import CUT_FIELDS
        covered = {n for pair in CUT_FIELDS.values() for n in pair}
        if 'cuts' in prepared.data['generation']:
            missing = (set(reference['cut_parameters']) & defaults.keys()) - run.keys()
            if missing:
                report.unknown('run_card.implicit_cut_defaults', sorted(missing), 'omitted native cut defaults require generator readback')
        for name in run:
            if name in reference['neutral_cuts'] and name not in covered | {'cut_decays'} and run[name] != reference['neutral_cuts'][name]:
                report.unknown('run_card.additional_cut.' + name, run[name])
        if prepared.data['reweighting'] or 'reweight_card' in texts:
            report.unknown('reweighting.execution', [p['weight_id'] for p in prepared.data['reweighting']], 'Check reweight launch order and inherited state')
        if 'banner' in texts:
            sections = banner_sections(texts['banner'])
            for name, parser, expected_content in (
                ('slha', parse_lha, actual_lha), ('mgruncard', read_run_card, run),
                ('mg5proccard', process_records, process_records(texts['process_card']))):
                if name not in sections:
                    report.unknown('banner.' + name, 'section missing')
                else:
                    report.check('banner.' + name, parser(sections[name]) == expected_content,
                                 'same parsed configuration as supplied cards', 'parsed section compared')
            data['recorded_configuration_present'] = True
        elif request.phase == 'run_record':
            report.unknown('banner', 'run_record phase requires a recorded banner')
        else:
            data['recorded_configuration_present'] = False
        data['actual_parameters'] = actual
        data['actual_run_settings'] = run
    except UnsupportedSyntax as error:
        report.unknown('evidence.syntax', error)
    except (OSError, ValueError, IndexError, KeyError, TypeError, OverflowError, RuntimeError) as error:
        report.check('evidence.parse', False, 'unambiguous supported card syntax', str(error), 'EVIDENCE_PARSE_FAILED')
    return report.result(data, evidence)
