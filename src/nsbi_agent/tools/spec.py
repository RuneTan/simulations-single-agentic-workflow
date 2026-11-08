"""Tool 2: check SMEFTsim request parameters and resolve coefficient points."""
import math

from nsbi_agent.catalog import CatalogError, coefficient_names, load_catalog, reference_sm_inputs
from nsbi_agent.model_selection import select_identity, simulation_model_query
from nsbi_agent.ufo_catalog import SM_BLOCKS
from nsbi_agent.validation import Report, fingerprint, number
from nsbi_agent.validation_contracts import IntentRequest

CONTRIBUTIONS = {'sm', 'interference', 'dim6_squared', 'propagator_interference'}
PREDICTIONS = {'sm_plus_linear', 'sm_plus_linear_and_dim6_squared'}
GENERATION = {'nevents', 'seed', 'beam_energies_GeV', 'beam_types', 'pdf', 'cuts'}
PRODUCTION = {'qcd_order', 'qed_order', 'renormalization_scale_GeV', 'factorization_scale_GeV',
              'maxjetflavor', 'bwcutoff'}
CUTS = {'charged_lepton_pt': 'GeV', 'abs_charged_lepton_eta': 'dimensionless',
        'abs_charged_lepton_rapidity': 'dimensionless', 'dilepton_mass': 'GeV'}


def flat_process(value):
    """Return flat external legs, or None for syntax outside this checker's coverage."""
    if isinstance(value, str):
        if value.count('>') != 1 or any(c in value for c in ',/()[]=$@'):
            return None
        initial, final = (part.lower().split() for part in value.split('>'))
        return {'initial_state': initial, 'final_state': final, 'multiparticles': {}}
    if isinstance(value, dict) and {'initial_state', 'final_state'} <= value.keys():
        if all(isinstance(value[k], list) and all(isinstance(n, str) for n in value[k])
               for k in ('initial_state', 'final_state')) and isinstance(value.get('multiparticles', {}), dict):
            aliases = value.get('multiparticles', {})
            if len({n.lower() for n in aliases}) != len(aliases):
                return None
            return {**value, 'initial_state': [n.lower() for n in value['initial_state']],
                    'final_state': [n.lower() for n in value['final_state']],
                    'multiparticles': {n.lower(): [p.lower() if isinstance(p, str) else p for p in v]
                                      if isinstance(v, list) else v for n, v in aliases.items()}}
    return None


def validate_spec(request: IntentRequest):
    report = Report('validate_spec')
    original = request.original()
    data = {'tool_number': 2, 'original_request': original, 'intent_sha256': fingerprint(original),
            'scope': 'SMEFTsim parameter domains, coefficient points and request consistency.'}
    report.fields(original, {'simulation', 'production', 'analysis'}, 'request')
    sim = request.simulation
    report.fields(sim, {'model', 'process', 'eft', 'sm_inputs', 'generation', 'reweighting', 'stage'}, 'simulation')
    for name in ('model', 'eft', 'sm_inputs', 'generation'):
        if not report.check(name + '.object', isinstance(sim.get(name, {}), dict), 'JSON object', sim.get(name, {}), 'INVALID_INPUT'):
            return report.result(data)
    if not report.check('process.present', 'process' in sim, 'process supplied', sim.get('process'), 'INVALID_INPUT'):
        return report.result(data)
    model = sim.get('model', {})
    report.fields(model, {'family', 'collection', 'model_id', 'version', 'basis', 'flavour_scheme',
                          'ew_input_scheme', 'restriction'}, 'model')
    if model.get('family', 'SMEFTsim') != 'SMEFTsim':
        report.unknown('model.family', model.get('family'), 'installed SMEFTsim catalog required')
        return report.result(data)
    try:
        query = simulation_model_query(model)
        identity = select_identity(query)
        catalog, digest = load_catalog(identity['model_id'], identity['version'], query.collection)
    except (CatalogError, ValueError) as error:
        code = getattr(error, 'code', 'INVALID_MODEL_INPUT')
        if code in {'UNSUPPORTED_MODEL', 'UNSUPPORTED_MODEL_VERSION', 'UNSUPPORTED_COLLECTION'}:
            report.unknown('model.catalog', str(error))
        else:
            report.check('model.catalog', False, 'consistent installed identity', str(error), code)
        return report.result(data)
    report.check('model.catalog', True, 'verified source catalog', digest)
    data.update(model={**identity, 'collection': query.collection}, catalog_sha256=digest,
                model_default_policy='request-defaults@2: omitted identity/source uses the isolated tutorial U35/Mw snapshot')
    process = flat_process(sim['process'])
    if process is None:
        report.unknown('process.syntax', sim['process'], 'flat external legs supported; complex syntax preserved')
    else:
        report.fields(process, {'initial_state', 'final_state', 'multiparticles', 'kind'}, 'process')
        initial, final = process['initial_state'], process['final_state']
        report.check('process.legs', 1 <= len(initial) <= 2 and bool(final), '1–2 incoming and nonempty outgoing', process)
        known = {n.lower() for n in catalog['particle_names']}
        aliases = process['multiparticles']
        for alias, members in aliases.items():
            report.check('process.alias.' + alias, alias not in known and isinstance(members, list)
                         and bool(members) and all(isinstance(n, str) and n in known for n in members),
                         'nonempty known UFO particles; no shadowing', members)
        for token in initial + final:
            if token in {'p', 'j', 'l+', 'l-', 'vl', 'vl~'} and token not in aliases:
                report.unknown('process.alias.' + token, 'definition missing; not inferred from tutorial')
            else:
                report.check('process.particle.' + token, token in known or token in aliases,
                             'particle in selected UFO or explicit alias', token, 'UNKNOWN_PARTICLE')
        kind = process.get('kind')
        if isinstance(kind, str) and kind in {'decay', 'collision', 'hadron_collision', 'lepton_collision'}:
            report.check('process.kind', len(initial) == (1 if kind == 'decay' else 2), 'kind consistent with incoming legs', kind)
        elif kind is not None:
            report.unknown('process.kind', kind)
        data['process'] = process
    sm = reference_sm_inputs(catalog)
    for name, value in sim.get('sm_inputs', {}).items():
        row = catalog['parameters'].get(name, {})
        valid = row.get('block') in SM_BLOCKS and number(value)
        if valid:
            valid = value >= 0 if row['block'] in {'MASS', 'YUKAWA', 'DECAY'} or name == 'aS' else value > 0 if name in {'MW', 'Gf', 'aEW', 'MZ'} else True
        if report.check('sm_inputs.' + name, valid, 'independent SM input with finite physical domain', value):
            sm[name] = value
    if catalog['model']['ew_input_scheme'] == 'MwScheme':
        valid = 0 < sm['MW'] < sm['MZ'] and sm['Gf'] > 0
    else:
        valid = (sm['aEW'] > 0 and sm['MZ'] > 0 and sm['Gf'] > 0 and
                 math.sqrt(sm['aEW']) / sm['MZ'] <= math.sqrt((math.sqrt(2) / (4 * math.pi)) * sm['Gf']))
    report.check('sm_inputs.ew_real_domain', valid, 'real tree-level EW input domain',
                 {n: sm[n] for n in ('MZ', 'Gf', 'MW' if 'MW' in sm else 'aEW')})
    eft = sim.get('eft', {})
    report.fields(eft, {'cutoff_GeV', 'coefficients', 'contribution', 'prediction', 'propagator_corrections',
                       'coefficient_policy', 'width_policy', 'sm_higgs_loop_vertices', 'varying_coefficients'}, 'eft')
    for name in {'contribution', 'prediction', 'propagator_corrections', 'width_policy', 'coefficient_policy', 'sm_higgs_loop_vertices'} & eft.keys():
        report.check('eft.' + name + '.nonnull', eft[name] is not None, 'omit an optional field instead of null', eft[name])
    cutoff = eft.get('cutoff_GeV', catalog['parameters']['LambdaSMEFT']['ufo_default'])
    report.check('eft.cutoff_GeV', number(cutoff) and cutoff > 0, 'positive finite EFT scale [GeV]', cutoff)
    if 'coefficient_policy' in eft and eft['coefficient_policy'] != 'zero_then_set':
        report.unknown('eft.coefficient_policy', eft['coefficient_policy'])
    for name in ('width_policy', 'sm_higgs_loop_vertices'):
        if name in eft and eft[name] != {'width_policy': 'fixed_ufo_defaults', 'sm_higgs_loop_vertices': 'excluded'}[name]:
            report.unknown('eft.' + name, eft[name])
    if eft.get('width_policy') == 'fixed_ufo_defaults':
        for name, value in sm.items():
            row = catalog['parameters'][name]
            if row['block'] == 'DECAY':
                report.check('width_policy.' + name, value == row['ufo_default'], 'explicit fixed_ufo_defaults', value)
    names = coefficient_names(catalog)

    def point(label, obj):
        coefficients = obj.get('coefficients', {})
        values = dict.fromkeys(names, 0.0)
        if not report.check(label + '.coefficients', isinstance(coefficients, dict), 'object of exact UFO names', coefficients):
            return {'coefficients': values, 'contribution': None, 'prediction': None}
        for name, value in coefficients.items():
            if report.check(label + '.' + name, name in values and number(value), 'known Wilson coefficient; finite dimensionless value', value):
                values[name] = value
        contribution, prediction = obj.get('contribution'), obj.get('prediction')
        if contribution is not None and prediction is not None:
            report.check(label + '.selection', False, 'choose contribution or prediction', obj)
        if contribution is None and prediction is None:
            if any(values.values()):
                report.unknown(label + '.contribution', 'nonzero point needs a contribution/prediction definition')
            else:
                contribution = 'sm'
        if contribution is not None:
            if isinstance(contribution, str) and contribution in CONTRIBUTIONS:
                report.check(label + '.contribution', contribution != 'sm' or not any(values.values()),
                             'SM means zero Wilson coefficients', contribution)
            else:
                report.unknown(label + '.contribution', contribution)
        if prediction is not None and (not isinstance(prediction, str) or prediction not in PREDICTIONS):
            report.unknown(label + '.prediction', prediction)
        if isinstance(prediction, str) and prediction in PREDICTIONS and obj.get('propagator_corrections') not in ('excluded', 'linearized'):
            report.unknown(label + '.propagator_corrections', obj.get('propagator_corrections'))
        return {'coefficients': values, 'contribution': contribution, 'prediction': prediction}

    baseline = point('eft', eft)
    varying = eft.get('varying_coefficients', [])
    report.check('eft.varying_coefficients', isinstance(varying, list) and all(isinstance(n, str) and n in names for n in varying), 'exact Wilson names', varying)
    weights = sim.get('reweighting', [])
    resolved_weights, ids = [], []
    if report.check('reweighting.list', isinstance(weights, list), 'list', weights):
        for i, item in enumerate(weights):
            if not report.check(f'reweighting.{i}', isinstance(item, dict), 'point object', item):
                continue
            report.fields(item, {'weight_id', 'coefficients', 'contribution'}, f'reweighting.{i}')
            weight_id = item.get('weight_id')
            if report.check(f'reweighting.{i}.id', isinstance(weight_id, str) and bool(weight_id) and weight_id not in ids, 'unique nonempty ID', weight_id):
                ids.append(weight_id)
            resolved_weights.append({'weight_id': weight_id, **point(f'reweighting.{i}', item)})
    generation = sim.get('generation', {})
    report.fields(generation, GENERATION, 'generation')
    for name in ('nevents', 'seed'):
        if name in generation:
            report.check('generation.' + name, type(generation[name]) is int and generation[name] > 0, 'positive integer', generation[name])
    for name in ('beam_energies_GeV', 'beam_types'):
        value = generation.get(name)
        if value is not None:
            valid = isinstance(value, list) and len(value) == 2
            if valid:
                valid = all(number(v) and v > 0 for v in value) if name == 'beam_energies_GeV' else all(type(v) is int for v in value)
            report.check('generation.' + name, valid, 'two positive energies [GeV] or two integer lpp types', value)
    if 'pdf' in generation and generation['pdf'] is not None:
        pdf = generation['pdf']
        if report.check('generation.pdf', isinstance(pdf, dict), 'PDF object', pdf):
            report.fields(pdf, {'label', 'set_id'}, 'generation.pdf')
            label = pdf.get('label')
            if report.check('generation.pdf.label', isinstance(label, str) and bool(label.strip()), 'nonempty PDF label string', label):
                if label not in {'lhapdf', 'cteq6_m', 'cteq6_l', 'cteq6l1', 'nn23lo', 'nn23lo1', 'nn23nlo'}:
                    report.unknown('generation.pdf.label_coverage', label, 'PDF label not verified against the current LO backend')
            report.check('generation.pdf.set_id', type(pdf.get('set_id')) is int and pdf['set_id'] >= 0, 'nonnegative integer', pdf.get('set_id'))
    if 'cuts' in generation:
        cuts, seen = generation['cuts'], set()
        if report.check('generation.cuts', isinstance(cuts, list), 'list', cuts):
            for i, cut in enumerate(cuts):
                if not report.check(f'cuts.{i}', isinstance(cut, dict), 'cut object', cut):
                    continue
                report.fields(cut, {'observable', 'minimum', 'maximum', 'unit'}, f'cuts.{i}')
                obs = cut.get('observable')
                if not isinstance(obs, str) or obs not in CUTS:
                    report.unknown(f'cuts.{i}.observable', obs)
                    continue
                lo, hi = cut.get('minimum'), cut.get('maximum')
                valid = (lo is not None or hi is not None) and all(v is None or number(v) and v >= 0 for v in (lo, hi))
                valid = valid and (lo is None or hi is None or lo < hi) and obs not in seen and cut.get('unit') == CUTS[obs]
                report.check(f'cuts.{i}.range', valid, f'nonnegative unique range [{CUTS[obs]}]', cut)
                seen.add(obs)
    report.fields(request.production, PRODUCTION, 'production')
    for name, value in request.production.items():
        if name in PRODUCTION:
            valid = (type(value) is int and value >= 0) if name in {'qcd_order', 'qed_order', 'maxjetflavor'} else (value is None and name == 'factorization_scale_GeV' or number(value) and value > 0)
            report.check('production.' + name, valid, 'positive scale/bwcutoff or nonnegative integer order/flavour', value)
    data.update(sm_inputs=sm, baseline=baseline, reweighting=resolved_weights, cutoff_GeV=cutoff,
                generation=generation, production=request.production,
                parameter_sources={'sm_inputs': {n: 'user' if n in sim.get('sm_inputs', {}) else 'model_reference' for n in sm},
                                   'eft_policy': 'each baseline/reweight point independently zero_then_set',
                                   'contribution': 'user' if 'contribution' in eft or 'prediction' in eft else 'SM default only for a zero point'},
                missing_execution_settings=sorted(GENERATION - generation.keys()))
    return report.result(data)
