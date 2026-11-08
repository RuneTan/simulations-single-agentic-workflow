"""Read-only parsers. File contents are data, never Python or shell commands."""
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import stat

DATA = Path(__file__).parent / 'data'


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field: ' + key)
        result[key] = value
    return result


def reject_nonfinite(value):
    raise ValueError('Nonfinite JSON constant: ' + value)


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('JSON number exceeds finite range: ' + text)
    return value


def load_json(text):
    return json.loads(text, object_pairs_hook=unique_fields, parse_constant=reject_nonfinite,
                      parse_float=finite_float)


class UnsupportedSyntax(ValueError):
    """Valid generator syntax whose meaning this parser cannot independently verify."""


def read_file(name):
    path = regular_path(name)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Evidence file exceeds 16 MiB: ' + name)
    raw = path.read_bytes()
    return raw.decode('utf-8'), hashlib.sha256(raw).hexdigest()


def regular_path(name):
    path = Path(name)
    if not path.is_absolute():
        raise ValueError('Evidence paths must be absolute: ' + name)
    if '..' in path.parts or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Evidence paths must not contain parent traversal or symlinks: ' + name)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Expected a regular evidence file: ' + name)
    return path


def source_digest(name, expected_size):
    path = regular_path(name)
    if path.stat().st_size != expected_size:
        raise ValueError('UFO source size differs from the verified snapshot: ' + name)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def significant_lines(text):
    return [' '.join(line.split('#', 1)[0].split()) for line in text.splitlines()
            if line.split('#', 1)[0].strip()]


def read_run_card(text):
    defaults = json.loads((DATA / 'run_card_lo.json').read_text())['defaults']
    values = {}
    for line in significant_lines(text):
        line = line.split('!', 1)[0].strip()
        if not line:
            continue
        value, sep, key = line.rpartition('=')
        key, value = key.strip().lower(), value.strip()
        if not sep or not re.fullmatch('[a-z][a-z0-9_]*', key) or key in values:
            raise ValueError('Invalid or duplicate run-card assignment: ' + line)
        try:
            decoded = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            if value.lower() in {'.true.', '.false.', 'true', 'false'}:
                decoded = value.lower() in {'.true.', 'true'}
            else:
                try:
                    decoded = float(value.replace('D', 'e').replace('d', 'e'))
                except ValueError:
                    decoded = value
        default = defaults.get(key)
        if type(default) is str:
            decoded = decoded if isinstance(decoded, str) else value
        elif key in defaults:
            # MadGraph writes list-valued settings as bare comma lists in the
            # recorded banner card ("21, 1, 2, ..."), which ast.literal_eval
            # reads as a tuple; the template type is a list.
            if type(default) is list and type(decoded) is tuple:
                decoded = list(decoded)
            valid = type(decoded) is type(default)
            if type(default) is float:
                valid = type(decoded) in (int, float) and math.isfinite(decoded)
            if not valid:
                raise ValueError('Invalid run-card type: ' + key)
        values[key] = decoded
    return values


def banner_sections(text):
    output = {}
    for name in ('slha', 'mgruncard', 'mg5proccard'):
        matches = re.findall(r'<' + name + r'\b[^>]*>(.*?)</' + name + r'\s*>', text, flags=re.I | re.S)
        if len(matches) > 1:
            raise ValueError('Duplicate banner section: ' + name)
        if matches:
            content = matches[0]
            # MadGraph 2.9.27 wraps the recorded run card in <![CDATA[ ... ]]>
            # inside the banner XML; the markers are not card content.
            content = re.sub(r'^\s*<!\[CDATA\[\s*', '', content)
            content = re.sub(r'\s*\]\]>\s*$', '', content)
            output[name] = content
    return output


def parse_lha(text):
    rows, block, blocks = {}, None, set()
    for line in significant_lines(text):
        fields = line.split()
        if fields[0].upper() == 'BLOCK':
            block = fields[1].upper()
            if block == 'QNUMBERS':
                block = None
            elif block in blocks:
                raise UnsupportedSyntax('Repeated/scaled LHA blocks need a generator-specific interpretation: ' + block)
            else:
                blocks.add(block)
            continue
        if fields[0].upper() == 'DECAY':
            key, value = ('DECAY', (int(fields[1]),)), fields[2]
            if value.lower().startswith('auto'):
                raise UnsupportedSyntax('Automatic widths require the evaluated generator card')
            block = None
        elif block:
            key, value = (block, tuple(int(v) for v in fields[:-1])), fields[-1]
        else:
            continue
        value = float(value.replace('D', 'E').replace('d', 'e'))
        if key in rows or not math.isfinite(value):
            raise ValueError('Duplicate/nonfinite LHA coordinate: ' + str(key))
        rows[key] = value
    return rows
