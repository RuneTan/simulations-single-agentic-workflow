#!/usr/bin/env python3
"""Independent LHE m_ee verification (stdlib only, no nsbi_agent imports).

Recomputes from an LHE 3.0 file: formally complete events, stored nominal
weights (XWGTUP, third event-header value), dilepton pairing, m_ee =
sqrt((p_e+ + p_e-)^2) and the 10-bin 66-116 GeV histogram including
underflow/overflow.  Bins are half-open [lo + i*w, lo + (i+1)*w); m < lo is
underflow and m >= hi is overflow.  Final-state electrons have status 1 and
PDG id 11 (e-) or -11 (e+).

Usage: verify_analysis.py EVENT_FILE --bins 10 --lo 66 --hi 116 --json OUT.json
"""
import argparse
import gzip
import hashlib
import json
import math
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def rows(text):
    """Split an LHE record into tokens per non-empty, comment-stripped line."""
    return [tokens for line in (text or '').splitlines()
            if (tokens := line.split('#', 1)[0].split())]


def number(text):
    value = float(text.replace('D', 'e').replace('d', 'e'))
    if not math.isfinite(value):
        raise ValueError('non-finite number: ' + text)
    return value


def parse_event(element, index):
    """Validate one <event> record; return (XWGTUP, [(pdg, status, E, px, py, pz)])."""
    lines = rows(element.text)
    if not lines or len(lines[0]) < 6:
        raise ValueError(f'event {index}: incomplete event header')
    nup = int(lines[0][0])
    int(lines[0][1])
    for value in lines[0][2:6]:
        number(value)
    if nup <= 0 or len(lines) != nup + 1:
        raise ValueError(f'event {index}: particle count differs from NUP')
    particles = []
    for line in lines[1:]:
        if len(line) != 13:
            raise ValueError(f'event {index}: 13 particle fields required')
        ints = [int(value) for value in line[:6]]
        floats = [number(value) for value in line[6:]]
        particles.append((ints[0], ints[1], floats[3], floats[0], floats[1], floats[2]))
    return number(lines[0][2]), particles


def parse_lhe(path, records):
    """Fill records['init']/records['events'] from an LHE 3.0 file; raise if damaged."""
    opener = gzip.open if path.suffix.lower() == '.gz' else open
    with opener(path, 'rb') as stream:
        root = ET.parse(stream).getroot()
    if tag(root) != 'LesHouchesEvents':
        raise ValueError('root element must be LesHouchesEvents')
    init = None
    for child in root:
        name = tag(child)
        if name == 'header':
            continue
        if name == 'init':
            if init is not None:
                raise ValueError('exactly one init block required')
            lines = rows(child.text)
            if not lines or len(lines[0]) != 10:
                raise ValueError('init record must contain ten fields')
            nprocess = int(lines[0][9])
            if nprocess < 0 or len(lines) != nprocess + 1:
                raise ValueError('init process count differs from the init rows')
            if any(len(line) != 4 for line in lines[1:]):
                raise ValueError('init process record must contain four fields')
            init = [number(line[0]) for line in lines[1:]]
        elif name in ('event', 'eventgroup'):
            if init is None:
                raise ValueError('init block must precede events')
            group = [child] if name == 'event' else [node for node in child if tag(node) == 'event']
            for element in group:
                records['events'].append(parse_event(element, len(records['events']) + 1))
    if init is None:
        raise ValueError('LHE init block required')
    records['init'] = init


def analyze(event_file, bins=10, lo=66.0, hi=116.0):
    """Return the independent verification report for one .lhe/.lhe.gz file."""
    path = Path(event_file)
    report = {'event_file': str(path), 'file_sha256': None, 'events': 0, 'sumw': 0.0,
              'sumw2': 0.0, 'dilepton_pairs': 0, 'm_ll': None, 'bin_edges': [],
              'histogram_total_sumw': 0.0, 'init_reported_cross_section_pb': [],
              'checks': {}, 'status': 'failed', 'errors': []}
    records = {'init': None, 'events': []}
    try:
        report['file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        parse_lhe(path, records)
    except (OSError, ValueError, EOFError, zlib.error, ET.ParseError) as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
    events = records['events']
    width = (hi - lo) / bins
    edges = [lo + index * width for index in range(bins + 1)]
    bin_sumw, bin_sumw2 = [0.0] * bins, [0.0] * bins
    flows = {name: {'sumw': 0.0, 'sumw2': 0.0, 'count': 0} for name in ('underflow', 'overflow')}
    sumw = sumw2 = in_range_sumw = 0.0
    in_range_count = pairs = 0
    anomalies = []
    for index, (weight, particles) in enumerate(events, 1):
        sumw += weight
        sumw2 += weight * weight
        plus = [row for row in particles if (row[0], row[1]) == (11, 1)]
        minus = [row for row in particles if (row[0], row[1]) == (-11, 1)]
        if len(plus) != 1 or len(minus) != 1:
            anomalies.append(index)
            continue
        pairs += 1
        total4 = [plus[0][column] + minus[0][column] for column in range(2, 6)]
        mass2 = total4[0] ** 2 - total4[1] ** 2 - total4[2] ** 2 - total4[3] ** 2
        mass = math.sqrt(max(mass2, 0.0))
        if mass < lo or mass >= hi:
            flow = flows['underflow' if mass < lo else 'overflow']
            flow['sumw'] += weight
            flow['sumw2'] += weight * weight
            flow['count'] += 1
        else:
            position = min(int((mass - lo) / width), bins - 1)
            bin_sumw[position] += weight
            bin_sumw2[position] += weight * weight
            in_range_sumw += weight
            in_range_count += 1
    total = in_range_sumw + sum(bucket['sumw'] for bucket in flows.values())
    report.update({
        'events': len(events), 'sumw': sumw, 'sumw2': sumw2, 'dilepton_pairs': pairs,
        'm_ll': {'bins': [[edges[i], edges[i + 1]] for i in range(bins)],
                 'sumw': bin_sumw, 'sumw2': bin_sumw2,
                 'stderr': [math.sqrt(value) for value in bin_sumw2],
                 'underflow': flows['underflow'], 'overflow': flows['overflow'],
                 'in_range_sumw': in_range_sumw, 'in_range_count': in_range_count},
        'bin_edges': edges, 'histogram_total_sumw': total,
        'init_reported_cross_section_pb': records['init'] or [],
        'checks': {
            'events_positive': len(events) > 0,
            'weights_finite': math.isfinite(sumw) and math.isfinite(sumw2),
            'underflow_overflow_consistent':
                sum(bucket['count'] for bucket in flows.values()) + in_range_count == len(events),
            'histogram_matches_total': math.isclose(total, sumw, rel_tol=1e-9, abs_tol=1e-9),
            'dilepton_pairing': not anomalies,
        },
    })
    if anomalies:
        report['errors'].append(
            'events without exactly one final-state e+/e- pair: '
            + ', '.join(str(index) for index in anomalies[:10]))
    if not events and not report['errors']:
        report['errors'].append('no complete events found')
    report['status'] = 'failed' if report['errors'] else 'succeeded'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent LHE m_ee histogram check (stdlib only).')
    parser.add_argument('event_file', help='LHE 3.0 file (.lhe or .lhe.gz)')
    parser.add_argument('--bins', type=int, default=10)
    parser.add_argument('--lo', type=float, default=66.0)
    parser.add_argument('--hi', type=float, default=116.0)
    parser.add_argument('--json', dest='json_path', help='write the full JSON report here')
    args = parser.parse_args(argv)
    if args.bins < 1 or not args.lo < args.hi:
        parser.error('--bins must be positive and --lo must be below --hi')
    report = analyze(args.event_file, args.bins, args.lo, args.hi)
    text = json.dumps(report, indent=2)
    if args.json_path:
        Path(args.json_path).write_text(text + '\n')
    print(json.dumps({key: report[key] for key in
                      ('event_file', 'events', 'dilepton_pairs', 'sumw', 'status')}))
    return 0 if report['status'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
