"""Resolve and checksum local model files without importing or evaluating UFO code."""
import hashlib
import json
from pathlib import Path

STORE_ROOT = Path(__file__).resolve().parents[2] / 'models'
REQUIRED = {'__init__.py', 'particles.py', 'parameters.py', 'vertices.py', 'couplings.py',
            'lorentz.py', 'object_library.py', 'coupling_orders.py', 'function_library.py',
            'decays.py', 'propagators.py', 'write_param_card.py', 'version.info'}


class ModelStoreError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def owned_path(base: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ModelStoreError('UNSAFE_MODEL_PATH', str(relative))
    target = base / relative
    try:
        # Check before resolve: a self-link otherwise raises RuntimeError in Python 3.12.
        for item in [target, *target.parents]:
            if item.is_symlink():
                raise ModelStoreError('UNSAFE_MODEL_PATH', str(target))
        if not target.resolve().is_relative_to(base.resolve()):
            raise ModelStoreError('UNSAFE_MODEL_PATH', str(relative))
    except (OSError, RuntimeError) as error:
        raise ModelStoreError('UNSAFE_MODEL_PATH', str(error)) from error
    return target


def inspect_local_model(model_id: str, version: str, catalog_sources: dict | None = None,
                        *, registry_name: str = 'registry.json') -> dict:
    try:
        if STORE_ROOT.is_symlink():
            raise ModelStoreError('UNSAFE_MODEL_PATH', str(STORE_ROOT))
        registry = json.loads(owned_path(STORE_ROOT, registry_name).read_text())
        if registry['schema_version'] != 1:
            raise ModelStoreError('MODEL_STORE_INVALID', 'Unsupported registry schema')
        record = registry['models'].get(model_id)
        if record is None or record['version'] != version:
            raise ModelStoreError('LOCAL_MODEL_UNAVAILABLE', model_id + ' ' + version)
        manifest_path = owned_path(STORE_ROOT, record['manifest'])
        raw = manifest_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != record['manifest_sha256']:
            raise ModelStoreError('MODEL_MANIFEST_HASH_MISMATCH', str(manifest_path))
        manifest = json.loads(raw)
        if not isinstance(manifest.get('provenance'), dict):
            raise ModelStoreError('MODEL_STORE_INVALID', 'Manifest provenance must be an object')
        if (manifest['schema_version'] != 1 or manifest['model']['model_id'] != model_id
                or manifest['model']['version'] != version):
            raise ModelStoreError('MODEL_STORE_INVALID', 'Manifest identity mismatch')
        ufo = owned_path(STORE_ROOT, record['ufo_directory'])
        files = manifest['files']
        if not REQUIRED <= files.keys() or not any(n.startswith('restrict_') for n in files):
            raise ModelStoreError('INCOMPLETE_LOCAL_MODEL', str(ufo))
        for name, expected in files.items():
            path = owned_path(ufo, name)
            if path.suffix in {'.pyo', '.pyc'}:
                raise ModelStoreError('MODEL_STORE_INVALID', 'Compiled cache registered as source')
            content = path.read_bytes()
            if len(content) != expected['size_bytes'] or hashlib.sha256(content).hexdigest() != expected['sha256']:
                raise ModelStoreError('MODEL_FILE_HASH_MISMATCH', name)
        for path in ufo.rglob('*'):
            if path.is_symlink():
                raise ModelStoreError('UNSAFE_MODEL_PATH', str(path))
        actual = {str(p.relative_to(ufo)) for p in ufo.rglob('*') if p.is_file()}
        if actual != set(files):
            raise ModelStoreError('MODEL_FILE_SET_MISMATCH', str(sorted(actual ^ set(files))))
        if (ufo / 'version.info').read_text().strip() != 'tag: v' + version:
            raise ModelStoreError('MODEL_STORE_INVALID', 'version.info mismatch')
        license_path = owned_path(manifest_path.parent, manifest['license']['path'])
        if hashlib.sha256(license_path.read_bytes()).hexdigest() != manifest['license']['sha256']:
            raise ModelStoreError('MODEL_LICENSE_HASH_MISMATCH', str(license_path))
        for name, expected in (catalog_sources or {}).items():
            if files.get(name, {}).get('sha256') != expected['sha256']:
                raise ModelStoreError('MODEL_CATALOG_MISMATCH', name)
        return {'model': manifest['model'], 'ufo_path': str(ufo), 'manifest_path': str(manifest_path),
                'manifest_sha256': digest, 'verified_files': len(files),
                'files': sorted(files),
                'classification': manifest['classification'],
                'catalog_bound_files': len(catalog_sources or {}),
                'provenance': manifest['provenance'],
                'generator_import': 'not_checked'}
    except ModelStoreError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        raise ModelStoreError('MODEL_STORE_UNAVAILABLE', str(error)) from error
