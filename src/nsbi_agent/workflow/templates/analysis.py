#!/usr/bin/env python3
"""Analysis program template for the NSBI single-Agent workflow.

The task Agent copies this file into its task workspace and writes a small
``analysis_config.json`` next to it.  The program is deliberately independent
of the workflow code so that the sample analysis can be checked by a separate
program. It computes the invariant mass of a requested final-state particle pair:

* four-momenta selected by pair_pdg_ids (default [11, -11] for e-/e+),
* configurable histogram: ``bins`` equal-width bins over ``range_GeV``,
  half-open on the right (m < lo underflow, m >= hi overflow),
* per-bin sumw / sumw2 / sqrt(sumw2) of the stored nominal XWGTUP weights,
* the init-reported cross section in pb,
* analysis.json and plot.png (error bars, units, sample identification).

Required config keys: event_file; optional: task_id, sample_label, nevents,
seed, pair_pdg_ids, observable (default m_ee), bins (default 10), range_GeV (default
[66.0, 116.0]).  Audit F4: the analysis settings are never hard-coded.

Usage:  .venv/bin/python analysis.py        (reads ./analysis_config.json)
"""
import gzip
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

DEFAULT_BINS = 10
DEFAULT_LO, DEFAULT_HI = 66.0, 116.0


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def read_events(path, pair_pdg_ids=(11, -11)):
    """Return (events, init_cross_sections, digest); each event is (m_ll, weight)."""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    stream = gzip.decompress(raw) if path.lower().endswith('.gz') else raw
    root = ET.fromstring(stream)
    init = root.find('.//init')
    if init is None:
        raise SystemExit('LHE init block missing')
    rows = [line.split() for line in (init.text or '').splitlines() if line.split()]
    nprocess = int(rows[0][9])
    cross_sections = [float(row[0]) for row in rows[1:1 + nprocess]]
    events = []
    for element in root.iter('event'):
        lines = [line.split() for line in (element.text or '').splitlines() if line.split()]
        nup = int(lines[0][0])
        weight = float(lines[0][2].replace('D', 'e'))
        particles = {}
        for row in lines[1:1 + nup]:
            pid, status = int(row[0]), int(row[1])
            if status != 1:
                continue
            p = (float(row[9]), float(row[6]), float(row[7]), float(row[8]))  # E, px, py, pz
            particles.setdefault(pid, []).append(p)
        mass = None
        first, second = (particles.get(pid, []) for pid in pair_pdg_ids)
        if len(first) == 1 and len(second) == 1:
            total = [a + b for a, b in zip(first[0], second[0])]
            mass2 = total[0] ** 2 - total[1] ** 2 - total[2] ** 2 - total[3] ** 2
            mass = math.sqrt(max(mass2, 0.0))
        events.append((mass, weight))
    return events, cross_sections, digest


def histogram(events, lo, hi, bins):
    edges = [lo + index * (hi - lo) / bins for index in range(bins + 1)]
    width = (hi - lo) / bins
    sumw = [0.0] * bins
    sumw2 = [0.0] * bins
    underflow = {'sumw': 0.0, 'sumw2': 0.0, 'count': 0}
    overflow = {'sumw': 0.0, 'sumw2': 0.0, 'count': 0}
    for mass, weight in events:
        if mass is None:
            continue
        if mass < lo:
            underflow['sumw'] += weight
            underflow['sumw2'] += weight * weight
            underflow['count'] += 1
        elif mass >= hi:
            overflow['sumw'] += weight
            overflow['sumw2'] += weight * weight
            overflow['count'] += 1
        else:
            index = min(int((mass - lo) / width), bins - 1)
            sumw[index] += weight
            sumw2[index] += weight * weight
    return edges, sumw, sumw2, underflow, overflow


def main():
    config = json.loads(Path('analysis_config.json').read_text())
    event_file = config['event_file']
    observable = config.get('observable') or 'm_ee'
    pair_pdg_ids = config.get('pair_pdg_ids') or [11, -11]
    if (len(pair_pdg_ids) != 2 or any(type(pid) is not int for pid in pair_pdg_ids)
            or pair_pdg_ids[0] == pair_pdg_ids[1]):
        raise SystemExit('analysis_config: pair_pdg_ids requires two distinct integer PDG IDs')
    bins = int(config.get('bins') or DEFAULT_BINS)
    lo, hi = (config.get('range_GeV') or [DEFAULT_LO, DEFAULT_HI])[:2]
    lo, hi = float(lo), float(hi)
    if bins < 1 or lo >= hi:
        raise SystemExit('analysis_config: bins must be positive and range_GeV ordered')
    events, cross_sections, digest = read_events(event_file, pair_pdg_ids)
    edges, sumw, sumw2, underflow, overflow = histogram(events, lo, hi, bins)
    sqrt_sumw2 = [math.sqrt(value) for value in sumw2]
    total_sumw = sum(weight for _, weight in events)
    total_sumw2 = sum(weight * weight for _, weight in events)
    paired = sum(1 for mass, _ in events if mass is not None)
    selection = ('final-state PDG %d + PDG %d, status 1; invariant mass of the pair'
                 % tuple(pair_pdg_ids))
    table = {
        'task_id': config.get('task_id'),
        'sample_label': config.get('sample_label', config.get('task_id')),
        'event_file': str(Path(event_file).resolve()),
        'file_sha256': digest,
        'events': len(events),
        'dilepton_pairs': paired if set(pair_pdg_ids) == {11, -11} else 0,
        'selected_pairs': paired,
        'pair_pdg_ids': pair_pdg_ids,
        'observable': observable,
        'bins': bins,
        'range_GeV': [lo, hi],
        'bin_edges': edges,
        'sumw_per_bin': sumw,
        'sumw2_per_bin': sumw2,
        'sqrt_sumw2_per_bin': sqrt_sumw2,
        'underflow': underflow,
        'overflow': overflow,
        'sumw': total_sumw,
        'sumw2': total_sumw2,
        'sqrt_sumw2': math.sqrt(total_sumw2),
        'init_reported_cross_section_pb': cross_sections,
        'normalization': 'per-bin sum of stored nominal XWGTUP weights',
        'selection': selection,
    }
    Path('analysis.json').write_text(json.dumps(table, indent=2) + '\n', encoding='utf-8')

    width = (hi - lo) / bins
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(bins)]
    errors = [value if value > 0 else float('nan') for value in sqrt_sumw2]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.errorbar(centers, sumw, yerr=errors, fmt='o', capsize=3, color='tab:blue',
                ecolor='tab:blue', label=config.get('sample_label', 'sample'))
    ax.set_xlabel('%s [GeV]' % observable)
    ax.set_ylabel('Sum of nominal weights / bin (width %g GeV)' % width)
    title = '%s\n%s, %s events, seed %s' % (
        config.get('task_id', 'NSBI task'),
        config.get('sample_label', 'pp collisions'),
        config.get('nevents', len(events)),
        config.get('seed', 'n/a'))
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(loc='upper right', fontsize=8)
    fig.tight_layout()
    fig.savefig('plot.png', dpi=130)

    print(json.dumps({key: table[key] for key in
                      ('events', 'dilepton_pairs', 'sumw', 'sumw2', 'bins',
                       'range_GeV')}, indent=2))
    print('cross section (pb):', cross_sections)


if __name__ == '__main__':
    main()
