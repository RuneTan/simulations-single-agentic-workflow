"""Score verbatim workflow cards with the pinned upstream scorers."""
import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_score(case, card):
    scorer = ROOT / 'upstream' / case / 'tests/score.py'
    output = subprocess.check_output([sys.executable, '-I', '-B', str(scorer),
                                      '--submission', str(card)], encoding='utf-8')
    return json.loads(output)


def event_evidence(workspace, result, check_lepton_cuts):
    event_name = (result.get('delivery') or {}).get('events')
    job_dir = (result.get('job') or {}).get('cwd')
    events = [Path(event_name)] if event_name else []
    if not events and job_dir and Path(job_dir).resolve().is_relative_to(workspace):
        events = sorted(Path(job_dir).glob('Events/run_*/unweighted_events.lhe.gz'))
    events = [p for p in events if p.is_file() and p.resolve().is_relative_to(workspace)]
    if len(events) != 1:
        return None, None
    event = events[0]
    opener = gzip.open if event.suffix == '.gz' else open
    count = 0
    leptons = 0
    violations = 0
    minimum_pt = math.inf
    maximum_eta = 0.0
    try:
        with opener(event, 'rb') as stream:
            for _, element in ET.iterparse(stream, events=['end']):
                if element.tag == 'event':
                    count += 1
                    if check_lepton_cuts:
                        lines = [line.split() for line in element.text.splitlines()
                                 if line.strip() and not line.lstrip().startswith('#')]
                        final = [line for line in lines[1:] if int(line[1]) == 1]
                        if sorted(int(line[0]) for line in final) != [-11, 11]:
                            raise ValueError(f'Unexpected final state at event {count}')
                        for line in final:
                            pt = math.hypot(float(line[6]), float(line[7]))
                            eta = abs(math.asinh(float(line[8]) / pt)) if pt else math.inf
                            leptons += 1
                            violations += pt < 20 - 1e-6 or eta > 2.5 + 1e-6
                            minimum_pt = min(minimum_pt, pt)
                            maximum_eta = max(maximum_eta, eta)
                element.clear()
    except (OSError, ET.ParseError, EOFError, ValueError) as error:
        return {'path': str(event.relative_to(workspace)), 'error': str(error)}, None
    banners = sorted(event.parent.glob('*_banner.txt'))
    banner = banners[0] if len(banners) == 1 else None
    evidence = {'path': str(event.relative_to(workspace)), 'events': count, 'sha256': digest(event)}
    if check_lepton_cuts and leptons:
        evidence['lepton_cut_audit'] = {'leptons': leptons, 'violations': violations,
                                         'min_pt_GeV': minimum_pt, 'max_abs_eta': maximum_eta}
    return evidence, banner


def evaluate(row, output):
    case = row['case']
    workspace = Path(row['task_file']).parent.resolve()
    result = row['result']
    card_name = 'run_card.dat' if case == 'mg_runcard_004' else 'proc_card.dat'
    role = 'run_card' if case == 'mg_runcard_004' else 'process_card'
    case_out = output / row['task_id']
    case_out.mkdir(exist_ok=True)
    task_request = read_json(Path(row['task_file']))['user_request']
    if hashlib.sha256(task_request.encode()).hexdigest() != row['request_sha256']:
        raise ValueError(f'Request changed: {workspace}')

    def score_bytes(kind, content, source):
        copy = case_out / f'{kind}_{card_name}'
        copy.write_bytes(content)
        score = official_score(case, copy)
        score['submission'] = copy.name
        return {'source': source, 'sha256': digest(copy), 'official': score}

    root_card = workspace / card_name
    candidates = {}
    if root_card.is_file() and root_card.resolve().is_relative_to(workspace):
        candidates['standalone'] = score_bytes('standalone', root_card.read_bytes(), card_name)
    cards = result.get('cards') or {}
    if cards.get(role):
        stage_card = Path(cards[role])
        if stage_card.is_file() and stage_card.resolve().is_relative_to(workspace):
            candidates['stage_card'] = score_bytes('stage_card', stage_card.read_bytes(),
                                                  str(stage_card.relative_to(workspace)))
    event, banner = event_evidence(workspace, result, case == 'mg_runcard_004')
    if banner:
        tag = b'mgruncard' if role == 'run_card' else b'mg5proccard'
        section = re.search(rb'<' + tag + rb'>(.*?)</' + tag + rb'>', banner.read_bytes(), re.I | re.S)
        if section:
            candidates['banner_card'] = score_bytes('banner_card', section.group(1),
                                                   str(banner.relative_to(workspace)))
    trajectory = workspace / 'trajectory.jsonl'
    checks = 0
    if trajectory.exists():
        with trajectory.open(encoding='utf-8') as lines:
            checks = sum(json.loads(line)['kind'] == 'check_result' for line in lines)
    selected = candidates.get('standalone') or candidates.get('stage_card')
    banner_score = candidates.get('banner_card', {}).get('official', {})
    evaluation = {
        **{key: row[key] for key in ('case', 'task_id', 'gates_enabled', 'request_sha256')},
        'status': result['status'], 'message': result.get('message'),
        'questions': result.get('user_questions'), 'attempts': result.get('attempts'),
        'repairs': result.get('repairs'), 'job_launches': result.get('job_launches'),
        'tokens': result.get('usage', {}).get('total_tokens'), 'model': result.get('model', {}).get('model'),
        'elapsed_s': result.get('timing', {}).get('elapsed_wall_s'), 'automatic_check_records': checks,
        'events': event, 'candidates': candidates,
        'official_score_on_workflow_card': selected['official']['score'] if selected else None,
        'official_pass_on_workflow_card': selected['official']['passed'] if selected else None,
        'official_banner_score': banner_score.get('score'),
        'official_banner_pass': banner_score.get('passed'),
        'event_count': event.get('events') if event else None,
        'standalone_card_present': 'standalone' in candidates,
    }
    write_json(case_out / 'score.json', evaluation)
    return evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    provenance = read_json(ROOT / 'provenance.json')
    for name, sha in provenance['upstream_files_sha256'].items():
        if digest(ROOT / 'upstream' / name) != sha:
            raise ValueError(f'Upstream file hash mismatch: {name}')
    rows = read_json(args.manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    controls = {}
    for case in sorted({row['case'] for row in rows}):
        reference, = (ROOT / 'upstream' / case / 'expected').iterdir()
        control = official_score(case, reference)
        assert control['passed'] and control['score'] == 1.0, control
        controls[case] = control['score']
    results = [evaluate(row, args.output) for row in rows if 'result' in row]
    pairs = {}
    for case in controls:
        matched = [row for row in rows if row['case'] == case and 'result' in row]
        if len(matched) == 2:
            first, second = matched
            a, b = (dict(row['result']['runtime']) for row in matched)
            assert a.pop('gates_enabled') != b.pop('gates_enabled')
            assert a == b and first['request_sha256'] == second['request_sha256']
            assert first['request_sha256'] == provenance['request_sha256'][case]
            pairs[case] = {'same_request': True, 'same_budget': True}
    write_json(args.output / 'results.json', {'reference_controls': controls, 'pairs': pairs, 'trials': results})
    keys = ['case', 'gates_enabled', 'task_id', 'status', 'standalone_card_present',
            'official_score_on_workflow_card', 'official_pass_on_workflow_card',
            'official_banner_score', 'official_banner_pass', 'event_count', 'tokens', 'elapsed_s',
            'automatic_check_records', 'job_launches', 'repairs']
    with (args.output / 'results.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    for row in results:
        print(*(row[key] for key in keys))


if __name__ == '__main__':
    main()
