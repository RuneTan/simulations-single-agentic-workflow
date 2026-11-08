#!/usr/bin/env python3
"""Independently read the card settings recorded in a MadGraph run banner.

The five accepted checkers read cards through ``nsbi_agent``.  This script is a
second, standalone reading of the banner that the generator itself wrote next
to every sample (``*_banner.txt``), so the delivery can compare recorded
settings with the request without trusting the workflow code.

Usage:
    .venv/bin/python -I scripts/verify_banner.py <banner.txt> [--json out.json]

Exit code 0 when the banner is readable, 1 otherwise.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SETTINGS = ('nevents', 'iseed', 'lpp1', 'lpp2', 'ebeam1', 'ebeam2', 'pdlabel',
            'lhaid', 'fixed_ren_scale', 'fixed_fac_scale', 'scale',
            'dsqrt_q2fact1', 'dsqrt_q2fact2', 'ptl', 'ptlmax', 'etal', 'etalmin',
            'mmll', 'mmllmax', 'bwcutoff', 'maxjetflavor')


def section(text, name):
    match = re.search(r'<' + name + r'\b[^>]*>(.*?)</' + name + r'\s*>', text, re.S | re.I)
    if match is None:
        return None
    content = match.group(1)
    content = re.sub(r'^\s*<!\[CDATA\[\s*', '', content)
    return re.sub(r'\s*\]\]>\s*$', '', content)


def scalar(value):
    value = value.strip()
    lowered = value.lower()
    if lowered in ('.true.', 'true'):
        return True
    if lowered in ('.false.', 'false'):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value.replace('D', 'e'))
    except ValueError:
        return value


def parse_run_card(text):
    values = {}
    for line in text.splitlines():
        line = line.split('!', 1)[0].split('#', 1)[0].strip()
        if not line or '=' not in line:
            continue
        value, _, key = line.rpartition('=')
        key = key.strip().lower()
        if re.fullmatch('[a-z][a-z0-9_]*', key):
            values[key] = scalar(value)
    return values


def parse_param_card(text):
    blocks, parameters, block = {}, [], None
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        fields = line.split()
        if not fields:
            continue
        if fields[0].upper() == 'BLOCK':
            block, blocks[fields[1].upper()] = fields[1].upper(), True
            continue
        if fields[0].upper() == 'DECAY':
            parameters.append((('DECAY', fields[1]), scalar(fields[2])))
            continue
        if block and len(fields) >= 2:
            parameters.append(((block, ' '.join(fields[:-1])), scalar(fields[-1])))
    return parameters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('banner', type=Path)
    parser.add_argument('--json', type=Path, default=None)
    args = parser.parse_args()
    try:
        text = args.banner.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}))
        return 1

    proc = section(text, 'mg5proccard')
    run_text = section(text, 'mgruncard')
    slha = section(text, 'slha')
    generation = section(text, 'mggenerationinfo')
    if proc is None or run_text is None or slha is None:
        print(json.dumps({'status': 'failed',
                          'error': 'missing banner section (mg5proccard/mgruncard/slha)'}))
        return 1
    run = parse_run_card(run_text)
    parameters = parse_param_card(slha)
    interesting = {str(key): value for key, value in parameters
                   if key[0] in ('SMEFTCUTOFF',)}
    smeft_nonzero = {str(key): value for key, value in parameters
                     if key[0] in ('SMEFT', 'SMEFTCPV', 'SMEFTFV') and value != 0}
    result = {
        'status': 'succeeded',
        'banner': str(args.banner.resolve()),
        'process_card_lines': [line for line in proc.strip().splitlines() if line.strip()],
        'run_card_settings': {key: run.get(key) for key in SETTINGS},
        'cutoff': interesting,
        'nonzero_wilson_coefficients': smeft_nonzero,
        'generation_info': generation.strip() if generation else None,
        'init_reported_cross_section_pb': _cross_section(generation),
    }
    print(json.dumps(result, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return 0


def _cross_section(generation):
    if not generation:
        return None
    for line in generation.splitlines():
        if 'Integrated weight' in line and ':' in line:
            try:
                return float(line.split(':')[-1].strip())
            except ValueError:
                return None
    return None


if __name__ == '__main__':
    sys.exit(main())
