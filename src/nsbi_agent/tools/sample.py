"""Tool 4: stream LHE events and summarize event counts and stored weights."""
from collections import deque
from contextlib import nullcontext
import gzip
import hashlib
import math
import xml.etree.ElementTree as ET
import zlib

from nsbi_agent.artifact_readers import UnsupportedSyntax, regular_path
from nsbi_agent.validation import Report
from nsbi_agent.validation_contracts import SampleRequest


def file_record(name, role):
    path = regular_path(name)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return path, {'role': role, 'path': str(path), 'sha256': digest}


def numeric(text):
    value = float(text.replace('D', 'e').replace('d', 'e'))
    if not math.isfinite(value):
        raise ValueError('Finite event/weight values required: ' + text)
    return value


class HashingReader:
    """Hash exactly the raw bytes supplied to the parser/decompressor."""
    def __init__(self, raw):
        self.raw, self.digest = raw, hashlib.sha256()

    def read(self, size=-1):
        data = self.raw.read(size)
        self.digest.update(data)
        return data


class Weights:
    def __init__(self):
        self.count = self.negative = self.zero = 0
        self.total = self.correction = self.sumw2 = 0.0
        self.minimum = self.maximum = None

    def add(self, value):
        total = self.total + value
        self.correction += ((self.total - total) + value if abs(self.total) >= abs(value)
                            else (value - total) + self.total)
        self.total = total
        self.sumw2 += value * value
        self.count += 1
        self.negative += value < 0
        self.zero += value == 0
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def result(self):
        sumw = self.total + self.correction
        if not all(math.isfinite(v) for v in (sumw, self.sumw2)):
            raise ValueError('Weight totals exceed floating-point range')
        return {'count': self.count, 'sumw': sumw, 'sumw2': self.sumw2,
                'negative': self.negative, 'zero': self.zero, 'min': self.minimum, 'max': self.maximum}


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def lines(text):
    return [line.split('#', 1)[0].split() for line in (text or '').splitlines()
            if line.split('#', 1)[0].strip()]


def scan_lhe(stream):
    count, groups, init, declared, nominal, weights = 0, 0, None, [], Weights(), {}
    stack = []
    for action, element in ET.iterparse(stream, events=('start', 'end')):
        if action == 'start':
            if not stack and tag(element) != 'LesHouchesEvents':
                raise ValueError('Expected LesHouchesEvents root')
            stack.append(element)
            continue
        name = tag(element)
        parents = [tag(node) for node in stack[:-1]]
        direct = len(parents) == 1
        metadata = 'header' in parents
        if name == 'initrwgt' and (direct or parents == ['LesHouchesEvents', 'header']):
            declared = [e.attrib['id'] for e in element.iter() if tag(e) == 'weight']
            if len(declared) != len(set(declared)):
                raise ValueError('Weight IDs in initrwgt must be unique')
        elif name == 'init' and direct:
            if init is not None: raise ValueError('Exactly one init block required')
            rows = lines(element.text)
            if not rows or len(rows[0]) != 10:
                raise ValueError('Expected ten fields in the LHE init record')
            head = rows[0]; nprocess = int(head[9])
            if nprocess < 0 or len(rows) != nprocess + 1:
                raise ValueError('Process count differs from the init rows')
            init = {'beam_ids': [int(head[0]), int(head[1])],
                    'beam_energies_GeV': [numeric(head[2]), numeric(head[3])],
                    'pdf_group_ids': [int(head[4]), int(head[5])],
                    'pdf_set_ids': [int(head[6]), int(head[7])],
                    'idwtup': int(head[8]), 'processes': []}
            for row in rows[1:]:
                if len(row) != 4: raise ValueError('Expected four process fields')
                init['processes'].append({'process_id': int(row[3]), 'reported_cross_section_pb': numeric(row[0]),
                                          'reported_error_pb': numeric(row[1]), 'max_weight': numeric(row[2])})
        elif name == 'event' and not metadata:
            if not direct and parents != ['LesHouchesEvents', 'eventgroup']:
                raise UnsupportedSyntax('Review event layout: ' + '/'.join(parents))
            if init is None: raise ValueError('Init must precede event records')
            rows = lines(element.text)
            if not rows or len(rows[0]) < 6: raise ValueError(f'Event {count+1}: six header fields required')
            nup = int(rows[0][0]); int(rows[0][1])
            header = [numeric(v) for v in rows[0][2:6]]
            if nup <= 0 or len(rows) != nup + 1:
                raise ValueError(f'Event {count+1}: particle count differs from NUP')
            for row in rows[1:]:
                if len(row) != 13: raise ValueError(f'Event {count+1}: 13 particle fields required')
                for value in row[:6]: int(value)
                for value in row[6:]: numeric(value)
            event_weights = {}
            for node in element.iter():
                if tag(node) == 'wgt':
                    key = node.attrib['id']
                    if key in event_weights: raise ValueError(f'Event {count+1}: duplicate weight {key}')
                    event_weights[key] = numeric((node.text or '').strip())
                elif tag(node) == 'weights':
                    values = (node.text or '').split()
                    if len(values) != len(declared) or not declared:
                        raise ValueError('Compact weights must match the initrwgt ID table')
                    for key, value in zip(declared, values):
                        if key in event_weights: raise ValueError('Duplicate compact/explicit weight: ' + key)
                        event_weights[key] = numeric(value)
            nominal.add(header[0])
            for key, value in event_weights.items(): weights.setdefault(key, Weights()).add(value)
            count += 1
            stack[-2].remove(element)
            element.clear()
        elif name == 'eventgroup' and direct:
            if init is None: raise ValueError('Init must precede event groups')
            groups += 1
            stack[-2].remove(element)
            element.clear()
        stack.pop()
    if init is None: raise ValueError('LHE init block required')
    return {'events': count, 'event_groups': groups, 'nominal': nominal.result(), 'weights': {k:v.result() for k,v in weights.items()},
            'declared_weight_ids': declared, 'init': init}


def check_sample(request: SampleRequest):
    report, evidence = Report('check_sample'), []
    data = {'tool_number': 4, 'scope': 'LHE structure, event count, stored weights and supplied exit code.'}
    if request.run_exit_code is not None:
        data['run_exit_code'] = request.run_exit_code
        report.check('run.exit_code', request.run_exit_code == 0, 0, request.run_exit_code)
    try:
        path = regular_path(request.event_file)
        record = {'role': 'events', 'path': str(path)}; evidence.append(record)
        with path.open('rb') as raw:
            reader = HashingReader(raw)
            try:
                source = gzip.GzipFile(fileobj=reader, mode='rb') if path.suffix.lower() == '.gz' else nullcontext(reader)
                with source as stream:
                    stats = scan_lhe(stream)
            finally:
                while reader.read(1024 * 1024): pass
                record['sha256'] = reader.digest.hexdigest()
        data.update(stats)
        report.check('sample.events', stats['events'] > 0, 'at least one event', stats['events'])
        if request.expected_events is not None:
            report.check('sample.event_count', stats['events'] == request.expected_events, request.expected_events, stats['events'])
        for key in set(request.required_weight_ids) | set(stats['weights']) | set(stats['declared_weight_ids']):
            actual = stats['weights'].get(key, {}).get('count', 0)
            report.check('sample.weight.' + key, actual == stats['events'], stats['events'], actual)
    except UnsupportedSyntax as error:
        report.unknown('sample.layout', error, 'Review this event layout')
    except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError, OverflowError, ET.ParseError, zlib.error) as error:
        report.check('sample.read', False, 'complete LHE records', error, 'SAMPLE_READ_FAILED')
    if request.log_file:
        try:
            log, record = file_record(request.log_file, 'log'); evidence.append(record)
            with log.open(encoding='utf-8', errors='replace') as stream:
                data['log_tail'] = [line.rstrip()[:1000] for line in deque(stream, maxlen=12)]
        except (OSError, ValueError) as error:
            report.check('log.read', False, 'readable log', error, 'LOG_READ_FAILED')
    return report.result(data, evidence)
