"""Run a small MadGraph sample for every pinned official SMEFTsim variant."""
import argparse
import gzip
import json
import math
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from nsbi_agent.model_store import inspect_local_model

ROOT = Path(__file__).resolve().parents[1]
UFO_ROOT = ROOT / 'models/smeftsim/official/3.0.2'
TEMPLATE = ROOT / 'src/nsbi_agent/workflow/templates/run_card.dat'
PROCESS = 'generate p p > e+ e- QCD=0 QED=2 NP=0 NPprop=0 SMHLOOP=0'
SETTINGS = {'nevents': 100, 'iseed': 421023, 'lpp1': 1, 'lpp2': 1,
            'ebeam1': 6500.0, 'ebeam2': 6500.0, 'pdlabel': 'nn23lo1',
            'ptl': 20.0, 'etal': 2.5}


def check_sources(models):
    for model in models:
        evidence = inspect_local_model(model.name, '3.0.2', registry_name='official_registry.json')
        if Path(evidence['ufo_path']) != model:
            raise ValueError(f'Model manifest points to a different directory: {model.name}')
        yield evidence['verified_files']


def run_logged(command, cwd, log):
    with log.open('wb') as stream:
        subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=240)


def read_events(path):
    count = 0
    cross_section = None
    with gzip.open(path, 'rb') as stream:
        for _, node in ET.iterparse(stream, events=['end']):
            if node.tag == 'init':
                rows = [line.split() for line in node.text.splitlines()
                        if line.strip() and not line.lstrip().startswith('#')]
                if [float(v) for v in rows[0][:4]] != [2212, 2212, 6500, 6500]:
                    raise ValueError('LHE beam record differs from the request')
                cross_section = math.fsum(float(row[0]) for row in rows[1:])
            elif node.tag == 'event':
                rows = [line.split() for line in node.text.splitlines()
                        if line.strip() and not line.lstrip().startswith('#')]
                final = [line for line in rows[1:] if int(line[1]) == 1]
                if sorted(int(line[0]) for line in final) != [-11, 11]:
                    raise ValueError('Unexpected final state')
                for line in final:
                    pt = math.hypot(float(line[6]), float(line[7]))
                    if pt < 20 - 1e-5 or abs(math.asinh(float(line[8]) / pt)) > 2.5 + 1e-5:
                        raise ValueError('Event failed the requested lepton cuts')
                count += 1
            node.clear()
    if count != SETTINGS['nevents'] or cross_section is None or not 0 < cross_section < math.inf:
        raise ValueError(f'Unexpected sample: {count} events, sigma={cross_section}')
    return count, cross_section


def verify_variant(model, evidence, banner_tools):
    evidence.mkdir()
    with tempfile.TemporaryDirectory(prefix='nsbi-ufo-') as temporary:
        cwd = Path(temporary)
        process = cwd / 'process'
        command = evidence / 'process.mg5'
        command.write_text(f'import model {model}-full\n'
                           'define p = g u c d s u~ c~ d~ s~\n'
                           f'{PROCESS}\noutput {process}\n')
        run_logged(['bash', str(ROOT / 'scripts/mg5.sh'), '-f', str(command)],
                   cwd, evidence / 'prepare.log')
        cards = process / 'Cards'
        if not (cards / 'param_card_default.dat').is_file():
            raise ValueError('MadGraph did not export a process and native default card')
        lines = []
        for line in TEMPLATE.read_text().splitlines():
            _, _, key = line.partition('=')
            key = key.split('!', 1)[0].strip()
            lines.append(f'{SETTINGS[key]} = {key}' if key in SETTINGS else line)
        (cards / 'run_card.dat').write_text('\n'.join(lines) + '\n.false. = use_syst\n')
        shutil.copyfile(cards / 'run_card.dat', evidence / 'run_card.dat')
        run_logged(['bash', str(ROOT / 'scripts/mg5_run.sh'), str(process), '-f', '--nb_core=2'],
                   cwd, evidence / 'generate.log')
        event, = process.glob('Events/run_*/unweighted_events.lhe.gz')
        banner, = event.parent.glob('*_banner.txt')
        text = banner.read_text()
        run_card = banner_tools['parse_run_card'](banner_tools['section'](text, 'mgruncard'))
        actual = {key: run_card.get(key) for key in SETTINGS}
        if actual != SETTINGS:
            raise ValueError(f'Unexpected run banner values: {actual}')
        count, sigma = read_events(event)
        shutil.copyfile(banner, evidence / 'banner.txt')
        shutil.copyfile(event, evidence / event.name)
        return {'model': model.name, 'status': 'passed', 'events': count,
                'cross_section_pb': sigma, 'recorded_settings': actual}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New directory for logs, banners and LHE samples')
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    first = [UFO_ROOT / f'SMEFTsim_U35_{scheme}_UFO' for scheme in ('alphaScheme', 'MwScheme')]
    models = first + sorted(set(UFO_ROOT.glob('SMEFTsim_*_UFO')) - set(first))
    if len(models) != 10:
        raise ValueError(f'Expected 10 pinned UFO variants, found {len(models)}')
    helpers = runpy.run_path(str(ROOT / 'scripts/verify_banner.py'))
    summary = {'manifest_files_before': sum(check_sources(models)), 'variants': []}
    for model in models:
        print('VERIFY', model.name, flush=True)
        try:
            result = verify_variant(model, output / model.name, helpers)
        except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
            result = {'model': model.name, 'status': 'failed', 'error': str(error)}
        summary['variants'].append(result)
        print(json.dumps(result), flush=True)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    summary['manifest_files_after'] = sum(check_sources(models))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return int(any(row['status'] != 'passed' for row in summary['variants']))


if __name__ == '__main__':
    raise SystemExit(main())
