"""Inspect the owned backend and shared compiler versions without importing MadGraph."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ANCHORS = ('VERSION', 'bin/mg5_aMC', 'madgraph/various/banner.py', 'models/check_param_card.py',
           'Template/LO/Cards/run_card.dat')


class BackendError(ValueError):
    """A readable backend-check failure, including corrupt local metadata."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise BackendError('BACKEND_METADATA_INVALID', label + ' must be a JSON object')
    return value


def text_field(record: dict, name: str, label: str, *, digest=False) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BackendError('BACKEND_METADATA_INVALID', label + '.' + name + ' must be a nonempty string')
    if digest and re.fullmatch('[a-f0-9]{64}', value) is None:
        raise BackendError('BACKEND_METADATA_INVALID', label + '.' + name + ' must be a SHA-256 digest')
    return value


def owned_path(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise BackendError('BACKEND_PATH_INVALID', 'Unsafe backend path: ' + relative)
    target = ROOT/path
    if not target.is_relative_to(ROOT/'backends'):
        raise BackendError('BACKEND_PATH_INVALID', 'Path must be inside project backends: ' + relative)
    # Test links before resolve: a self-referencing link otherwise raises RuntimeError.
    for item in [target, *target.parents]:
        if item.is_symlink():
            raise BackendError('BACKEND_PATH_INVALID', 'Backend symlinks are not allowed: ' + str(item))
        if item == ROOT:
            break
    if not target.resolve().is_relative_to((ROOT/'backends').resolve()):
        raise BackendError('BACKEND_PATH_INVALID', 'Resolved backend path escapes its store')
    return target


def read_backend_info() -> dict:
    registry = mapping(json.loads(owned_path('backends/registry.json').read_text()), 'registry')
    record = mapping(registry.get('madgraph'), 'registry.madgraph')
    manifest_path = text_field(record, 'manifest', 'registry.madgraph')
    root_path = text_field(record, 'root', 'registry.madgraph')
    expected_hash = text_field(record, 'manifest_sha256', 'registry.madgraph', digest=True)
    expected_version = text_field(record, 'version', 'registry.madgraph')
    raw = owned_path(manifest_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise BackendError('BACKEND_SOURCE_MISMATCH', 'Backend manifest hash mismatch')
    manifest = mapping(json.loads(raw), 'manifest')
    files = mapping(manifest.get('files'), 'manifest.files')
    commit = text_field(manifest, 'commit', 'manifest')
    root = owned_path(root_path)
    for name in ANCHORS:
        row = mapping(files.get(name), 'manifest.files.' + name)
        expected = text_field(row, 'sha256', 'manifest.files.' + name, digest=True)
        content = owned_path(str(root.relative_to(ROOT)/name)).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise BackendError('BACKEND_SOURCE_MISMATCH', 'Backend source anchor changed: ' + name)
    match = re.search(r'^version\s*=\s*(\S+)', (root/'VERSION').read_text(), re.M)
    if match is None:
        raise BackendError('BACKEND_VERSION_UNREADABLE', 'Missing version declaration in MadGraph VERSION')
    version = match.group(1)
    if version != expected_version or commit != 'eb76cab72b8d44aac7162ac7221ac08a4384a169':
        raise BackendError('BACKEND_SOURCE_MISMATCH', 'Unexpected backend source identity')
    return {'version': version, 'path': str(root), 'commit': commit,
            'checked_source_anchors': list(ANCHORS), 'model_import': 'not_checked'}


def backend_info() -> dict:
    try:
        return read_backend_info()
    except BackendError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise BackendError('BACKEND_READ_FAILED', 'Cannot inspect owned backend: ' + str(error)) from error


def software_version(name: str) -> dict:
    if name == 'madgraph':
        return backend_info()
    executable = shutil.which(name)
    if executable is None:
        raise BackendError('BACKEND_TOOL_MISSING', name + ' is not on PATH; load the site compiler toolchain')
    run = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=5, check=True)
    # This interface reads stdout; a stderr-only banner is a structured failure.
    line = next((line.strip() for line in run.stdout.splitlines() if line.strip()), '')
    if not line:
        raise BackendError('BACKEND_VERSION_UNREADABLE', 'No version banner on stdout for ' + name)
    match = re.search(r'\b\d+\.\d+(?:\.\d+)?\b', line)
    if match is None:
        raise BackendError('BACKEND_VERSION_UNREADABLE', 'Cannot read ' + name + ' version')
    return {'version': match.group(), 'path': executable, 'banner': line,
            'isolation': 'compiler/tool selected from the configured PATH'}
