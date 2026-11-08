"""Tests for the independent LHE analysis verifier (scripts/verify_analysis.py)."""
import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify_analysis.py'
sys.path.insert(0, str(SCRIPT.parent))
import verify_analysis  # noqa: E402


def lhe(events, xsec=0.1):
    """LHE 3.0 text with one final-state e+ e- pair per event, back-to-back along z."""
    lines = ['<LesHouchesEvents version="3.0">', '<header/>', '<init>',
             '2212 -2212 6500 6500 0 0 0 0 -4 1', f'{xsec} 0.1 5.0 1', '</init>']
    for weight, mass in events:
        energy = mass / 2
        lines += ['<event>', f'4 1 {weight} 20 0.007 0.118',
                  '2212 -1 0 0 0 0 0 0 6500 6500 0 0 9',
                  '-2212 -1 0 0 0 0 0 0 -6500 6500 0 0 9',
                  f'11 1 1 2 0 0 0 0 {energy} {energy} 0 0 9',
                  f'-11 1 1 2 0 0 0 0 {-energy} {energy} 0 0 9', '</event>']
    lines.append('</LesHouchesEvents>')
    return '\n'.join(lines) + '\n'


def write_lhe(tmp_path, events, name='events.lhe', compress=False):
    text = lhe(events)
    path = tmp_path / name
    path.write_bytes(gzip.compress(text.encode(), mtime=0) if compress else text.encode())
    return path


def test_weights_bins_and_boundaries(tmp_path):
    report = verify_analysis.analyze(write_lhe(tmp_path, [(1.5, 66.0), (-0.5, 71.0)]))
    assert report['status'] == 'succeeded' and report['errors'] == []
    assert report['events'] == 2 and report['dilepton_pairs'] == 2
    assert report['sumw'] == pytest.approx(1.0)
    assert report['sumw2'] == pytest.approx(2.5)
    assert report['init_reported_cross_section_pb'] == pytest.approx([0.1])
    assert report['bin_edges'] == [66 + 5 * index for index in range(11)]
    histogram = report['m_ll']
    assert histogram['bins'] == [[66 + 5 * index, 71 + 5 * index] for index in range(10)]
    assert histogram['sumw'][0] == pytest.approx(1.5)   # m=66 starts the first bin
    assert histogram['sumw'][1] == pytest.approx(-0.5)  # m=71 starts the second bin
    assert histogram['stderr'][0] == pytest.approx(1.5)
    assert histogram['in_range_count'] == 2
    assert histogram['in_range_sumw'] == pytest.approx(1.0)
    assert histogram['underflow']['count'] == histogram['overflow']['count'] == 0
    assert report['histogram_total_sumw'] == pytest.approx(report['sumw'])
    assert all(report['checks'].values())


def test_flows_use_half_open_bins_and_keep_the_total(tmp_path):
    report = verify_analysis.analyze(write_lhe(tmp_path, [(1.0, 66.0), (2.0, 116.0), (0.5, 50.0)]))
    histogram = report['m_ll']
    assert report['dilepton_pairs'] == 3 and report['status'] == 'succeeded'
    assert histogram['in_range_count'] == 1
    assert histogram['in_range_sumw'] == pytest.approx(1.0)
    assert histogram['underflow'] == {'sumw': pytest.approx(0.5),
                                      'sumw2': pytest.approx(0.25), 'count': 1}
    assert histogram['overflow'] == {'sumw': pytest.approx(2.0),
                                     'sumw2': pytest.approx(4.0), 'count': 1}
    assert histogram['sumw'][9] == pytest.approx(0.0)  # m=116 GeV is overflow, not the last bin
    assert report['histogram_total_sumw'] == pytest.approx(3.5)
    assert report['histogram_total_sumw'] == pytest.approx(report['sumw'])
    assert all(report['checks'].values())


def test_gzip_sample_matches_plain_sample(tmp_path):
    events = [(1.5, 66.0), (-0.5, 71.0)]
    plain = verify_analysis.analyze(write_lhe(tmp_path, events))
    packed = verify_analysis.analyze(write_lhe(tmp_path, events, name='events.lhe.gz', compress=True))
    assert packed['status'] == plain['status'] == 'succeeded'
    assert packed['file_sha256'] != plain['file_sha256']
    assert packed['m_ll'] == plain['m_ll'] and packed['sumw'] == plain['sumw']


@pytest.mark.parametrize('damage', ['truncated', 'nup', 'pairing', 'noevents'])
def test_damaged_or_unpairable_samples_fail(tmp_path, damage):
    path = write_lhe(tmp_path, [] if damage == 'noevents' else [(1.0, 91.0)])
    text = path.read_text()
    if damage == 'truncated':
        text = text[:-30]
    elif damage == 'nup':
        text = text.replace('<event>\n4 1 1.0', '<event>\n5 1 1.0')
    elif damage == 'pairing':
        text = text.replace('11 1 1 2 0 0 0 0 45.5 45.5 0 0 9',
                            '13 1 1 2 0 0 0 0 45.5 45.5 0 0 9')
    path.write_text(text)
    report = verify_analysis.analyze(path)
    assert report['status'] == 'failed' and report['errors']
    if damage == 'nup':
        assert 'NUP' in report['errors'][0]
    if damage == 'pairing':
        assert report['dilepton_pairs'] == 0 and 'pair' in report['errors'][0]


def test_cli_writes_json_and_exits_zero(tmp_path):
    path = write_lhe(tmp_path, [(1.0, 66.0), (2.0, 116.0), (0.5, 50.0)])
    out = tmp_path / 'report.json'
    run = subprocess.run([sys.executable, '-I', str(SCRIPT), str(path), '--bins', '10',
                          '--lo', '66', '--hi', '116', '--json', str(out)],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and not run.stderr
    report = json.loads(out.read_text())
    assert report['status'] == 'succeeded'
    assert report['events'] == 3 and report['dilepton_pairs'] == 3
    assert len(report['m_ll']['bins']) == len(report['m_ll']['sumw']) == 10
    assert len(report['m_ll']['stderr']) == 10
    assert report['bin_edges'] == [66 + 5 * index for index in range(11)]
    assert report['histogram_total_sumw'] == pytest.approx(report['sumw'])
    assert json.loads(run.stdout)['status'] == 'succeeded'


def test_cli_reports_failure_with_nonzero_exit(tmp_path):
    path = write_lhe(tmp_path, [(1.0, 91.0)])
    path.write_text(path.read_text()[:-30])
    out = tmp_path / 'report.json'
    run = subprocess.run([sys.executable, '-I', str(SCRIPT), str(path), '--json', str(out)],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 1
    assert json.loads(out.read_text())['status'] == 'failed'
