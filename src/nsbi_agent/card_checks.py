"""Independent meaning checks for actual MG5 2.9.27 cards, not a card recipe."""
from pathlib import Path
import re
import shlex

from nsbi_agent.artifact_readers import read_file, significant_lines, source_digest
from nsbi_agent.tools.spec import CUTS, flat_process
from nsbi_agent.order_checks import REFERENCE_SELECTORS, check_eft_orders
from nsbi_agent.scale_checks import effective_factorization_flags
CUT_FIELDS = {'charged_lepton_pt': ('ptl', 'ptlmax'),
              'abs_charged_lepton_eta': ('etalmin', 'etal'),
              'abs_charged_lepton_rapidity': ('etalmin', 'etal'),
              'dilepton_mass': ('mmll', 'mmllmax')}


def process_records(text):
    imports, processes, aliases, others = [], [], {}, []
    for line in significant_lines(text):
        if line.startswith('import model '):
            imports.append(shlex.split(line)[2:])
        elif line.startswith('define '):
            key, sep, members = line.removeprefix('define ').partition('=')
            key = key.strip()
            if not sep or key in aliases:
                raise ValueError('Ambiguous multiparticle definition: ' + line)
            aliases[key.lower()] = members.lower().split()
        elif line.startswith(('generate ', 'add process ')):
            processes.append(line.removeprefix('generate ').removeprefix('add process '))
        elif not line.startswith('output '):
            others.append(line)
    return imports, processes, aliases, others


def check_process(report, text, prepared, production):
    imports, processes, aliases, others = process_records(text)
    for line in others:
        report.unknown('process_card.command', line, 'Review this additional command')
    requested = prepared.get('process')
    if requested is None:
        report.unknown('process_card.syntax', processes)
        return imports
    baseline = prepared['baseline']
    term = baseline['contribution']
    simple_term = isinstance(term, str) and term in REFERENCE_SELECTORS
    if len(processes) != 1 and (baseline['prediction'] is not None or not simple_term):
        report.unknown('process_card.channels', processes, 'Compare the channels for this prediction')
        return imports
    if not report.check('process_card.count', len(processes) == 1, 'one flat process; multi-channel requests need a richer checker', len(processes)):
        return imports
    expression = processes[0]
    # Strip only recognized order syntax. Remaining complex syntax is never approximated.
    pattern = r'\b([A-Za-z][A-Za-z0-9_]*(?:\^2)?)(<=|==|=)([0-9]+)\b'
    selectors = {}
    for match in re.finditer(pattern, expression):
        name, op, value = match.groups()
        if name in selectors:
            raise ValueError('Duplicate coupling-order selector: ' + name)
        selectors[name] = (int(value), 'exact' if op == '==' else 'cap')
    legs = flat_process(re.sub(pattern, '', expression))
    if legs is None:
        report.unknown('process_card.syntax', expression)
        return imports
    for field in ('initial_state', 'final_state'):
        report.check('process_card.' + field, legs[field] == requested[field], requested[field], legs[field])
    for name in set(legs['initial_state'] + legs['final_state']):
        if name in requested['multiparticles']:
            report.check('process_card.alias.' + name, aliases.get(name) == requested['multiparticles'][name], requested['multiparticles'][name], aliases.get(name))
        elif name in aliases:
            report.check('process_card.shadow.' + name, False, 'selected particle cannot be shadowed by a card alias', aliases[name])
    if baseline['prediction'] is not None or not simple_term:
        report.unknown('process_card.contribution', baseline, 'Check the combined/custom contribution definition')
    else:
        check_eft_orders(report, selectors, term)
    for name in ('qcd', 'qed'):
        if name + '_order' in production:
            expected = (production[name + '_order'], 'cap')
            actual = selectors.get(name.upper())
            equivalent_zero = expected == (0, 'cap') and actual == (0, 'exact')
            report.check('process_card.order.' + name.upper(), actual == expected or equivalent_zero, expected, actual)
    for name in set(selectors) - {'NP', 'NP^2', 'NPprop', 'NPprop^2', 'QCD', 'QED', 'SMHLOOP'}:
        report.unknown('process_card.order.' + name, selectors[name])
    if selectors.get('SMHLOOP') not in {(0, 'cap'), (0, 'exact')}:
        report.unknown('process_card.SMHLOOP', selectors.get('SMHLOOP'), 'Check the Higgs loop-vertex choice')
    return imports


def check_model_source(report, imports, local, evidence, requested_restriction=None):
    if len(imports) != 1 or len(imports[0]) != 1:
        report.unknown('model.import_count_or_options', imports, 'Resolve the model path and import options')
        return None
    imported = imports[0][0]
    source = Path(imported)
    restriction = None
    if not source.is_dir():
        parent, sep, restriction = imported.rpartition('-')
        source = Path(parent) if sep else source
    if not source.is_absolute() or not source.is_dir():
        report.unknown('model.import_source', imported, 'absolute accessible UFO path required for source verification')
        return None
    # Compare actual source files, allowing an Agent-owned copy outside the old bundle layout.
    if requested_restriction is not None:
        report.check('model.restriction_choice', requested_restriction == (restriction or 'full'),
                     requested_restriction, restriction or 'full')
    for name in local['files']:
        if name.startswith('restrict_') or not name.endswith('.py'):
            continue
        try:
            owned = Path(local['ufo_path']) / name
            size = owned.stat().st_size
            expected = source_digest(str(owned), size)
            digest = source_digest(str(source / name), size)
            report.check('model.source.' + name, digest == expected, expected, digest, 'MODEL_SOURCE_MISMATCH')
        except (OSError, ValueError, UnicodeError) as error:
            report.check('model.source.' + name, False, 'readable verified source', error)
    if restriction and restriction != 'full':
        path = source / ('restrict_' + restriction + '.dat')
        try:
            text, digest = read_file(str(path))
            evidence.append({'role': 'import_restriction', 'path': str(path), 'sha256': digest})
            return text
        except (OSError, ValueError, UnicodeError) as error:
            report.check('model.restriction', False, 'readable imported restriction', error)
    elif restriction is None and (source / 'restrict_default.dat').exists():
        report.unknown('model.default_restriction', 'implicit restrict_default.dat; import must be made explicit')
    return None


def check_run(report, actual, prepared):
    generation, production = prepared['generation'], prepared['production']
    wanted = {}
    for source, target in (('nevents', 'nevents'), ('seed', 'iseed')):
        if source in generation:
            wanted[target] = generation[source]
    for source, targets in (('beam_energies_GeV', ('ebeam1', 'ebeam2')), ('beam_types', ('lpp1', 'lpp2'))):
        if generation.get(source) is not None:
            wanted.update(zip(targets, generation[source]))
    pdf = generation.get('pdf')
    if pdf is not None:
        wanted.update(pdlabel=pdf.get('label'), lhaid=pdf.get('set_id'))
    for source, target in (('renormalization_scale_GeV', 'scale'), ('bwcutoff', 'bwcutoff'), ('maxjetflavor', 'maxjetflavor')):
        if source in production:
            wanted[target] = production[source]
    if 'renormalization_scale_GeV' in production:
        wanted['fixed_ren_scale'] = True
        wanted['scalefact'] = 1.0
    if production.get('factorization_scale_GeV') is not None:
        wanted.update(dsqrt_q2fact1=production['factorization_scale_GeV'], dsqrt_q2fact2=production['factorization_scale_GeV'])
        flags = effective_factorization_flags(actual)
        for i, flag in enumerate(flags, 1):
            name = f'run_card.effective_fixed_fac_scale{i}'
            if flag is None:
                report.unknown(name, 'insufficient explicit switch/beam settings')
            else:
                report.check(name, flag is True, 'effective fixed scale after per-beam precedence', flag,
                             'FACTORIZATION_SCALE_CONFLICT')
    for name, expected in wanted.items():
        report.check('run_card.' + name, actual.get(name) == expected and (type(actual.get(name)) is bool) == (type(expected) is bool), expected, actual.get(name))
    if 'beam_types' not in generation and len(prepared.get('process', {}).get('initial_state', [])) == 2:
        report.unknown('run_card.beam_interpretation', {'lpp1': actual.get('lpp1'), 'lpp2': actual.get('lpp2')}, 'beam species/types are not inferred from hard-process partons')


def check_cuts(report, actual, prepared, actual_parameters):
    generation = prepared['generation']
    if 'cuts' not in generation:
        report.unknown('run_card.cuts', 'not specified in intent; actual cuts require user review')
        return
    cuts = generation['cuts']
    final = prepared.get('process', {}).get('final_state', [])
    aliases = prepared.get('process', {}).get('multiparticles', {})
    final = [p for token in final for p in aliases.get(token, [token])]
    masses = {'e-': 'Me', 'e+': 'Me', 'mu-': 'MMU', 'mu+': 'MMU'}
    relevant = [p for p in final if p in masses]
    observables = [c.get('observable') for c in cuts]
    if 'dilepton_mass' in observables:
        pairs = final.count('e-') * final.count('e+') + final.count('mu-') * final.count('mu+')
        if pairs != 1:
            report.unknown('cuts.dilepton_pairing', pairs, 'one explicit same-flavour opposite-sign pair is currently interpreted')
    if cuts and (not relevant or any(p in {'ta-', 'ta+'} for p in final)):
        report.unknown('cuts.particle_coverage', final, 'generic cuts currently checked for explicit electrons/muons only')
    if 'abs_charged_lepton_eta' in observables and 'abs_charged_lepton_rapidity' in observables:
        report.unknown('cuts.eta_and_y', 'same native fields; combined intent needs a custom cut implementation')
        return
    desired = {key: 0.0 if i == 0 else -1.0 for pair in set(CUT_FIELDS.values()) for i, key in enumerate(pair)}
    for cut in cuts:
        obs = cut.get('observable')
        if not isinstance(obs, str) or obs not in CUT_FIELDS:
            report.unknown('cuts.mapping', obs)
            continue
        for p in relevant:
            mass = actual_parameters.get(masses[p])
            if mass is None:
                report.unknown('cuts.mass.' + p, 'mass absent in actual card; not replaced by requested value')
            else:
                report.check('cuts.native_mass_limit.' + p, mass <= 20, '<=20 GeV: heavier states are excluded by native do_cuts', mass)
                if obs == 'abs_charged_lepton_eta':
                    report.check('cuts.eta_semantics.' + p, mass == 0, 'massless: MG5 2.9.27 etal uses rapidity y, equal to eta only at m=0', mass)
        lower, upper = CUT_FIELDS[obs]
        desired[lower] = cut.get('minimum') if cut.get('minimum') is not None else 0.0
        desired[upper] = cut.get('maximum') if cut.get('maximum') is not None else -1.0
    for name, expected in desired.items():
        report.check('cuts.native.' + name, actual.get(name) == expected, expected, actual.get(name))
    if len(prepared.get('process', {}).get('initial_state', [])) == 1 and cuts:
        report.check('cuts.decay_enabled', actual.get('cut_decays') is True, True, actual.get('cut_decays'))
