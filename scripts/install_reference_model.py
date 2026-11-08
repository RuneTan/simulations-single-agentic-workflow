"""Install the pinned reference UFO used by the process-only request defaults."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def install(offline=False):
    lock = json.loads((ROOT/'scripts/reference_model_sources.json').read_text())
    model = lock['model']
    release = ROOT/'models/smeftsim'/model['version']
    ufo = release/model['model_id']
    cache = ROOT/'.cache/reference_model'
    payload = {}
    for name, expected in lock['files'].items():
        target = cache/name
        # Reuse an already installed, checksum-matching source.
        existing = ufo/name
        if existing.is_file() and sha(existing.read_bytes()) == expected['sha256']:
            raw = existing.read_bytes()
        elif target.is_file():
            raw = target.read_bytes()
        else:
            if offline:
                raise FileNotFoundError('Missing cached reference source: ' + name)
            url = (lock['repository'].replace('github.com', 'raw.githubusercontent.com')
                   + '/' + lock['commit'] + '/' + expected['path'])
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read()
        if sha(raw) != expected['sha256'] or len(raw) != expected['size_bytes']:
            raise ValueError('Reference source checksum mismatch: ' + name)
        cache.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        payload[name] = raw

    # Keep an existing verified local installation, including historical metadata.
    if (ROOT/'models/registry.json').is_file():
        from nsbi_agent.model_store import inspect_local_model
        current = inspect_local_model(model['model_id'], model['version'])
        if (current['provenance'].get('commit') != lock['commit']
                or current['provenance'].get('repository') != lock['repository']):
            raise ValueError('Existing reference model has different provenance')
        return {'model': model['model_id'], 'files': len(payload), 'status': 'verified'}

    license_name = 'licenses/SMEFTsim-MIT.txt'
    license_raw = (ROOT/'docs/third_party/SMEFTsim-MIT.txt').read_bytes()
    manifest = {
        'schema_version': 1, 'model': model,
        'provenance': {'repository': lock['repository'], 'commit': lock['commit'],
                       'path': str(Path(next(iter(lock['files'].values()))['path']).parent),
                       'copy_policy': 'checksum-verified pinned upstream bytes'},
        'files': {name: {'sha256': sha(raw), 'size_bytes': len(raw)} for name, raw in payload.items()},
        'classification': {'ufo_python': sorted(n for n in payload if n.endswith('.py')),
                           'restriction_cards': sorted(n for n in payload if n.startswith('restrict_')),
                           'parameter_card_templates': sorted(n for n in payload if n.startswith('param_card')),
                           'version_metadata': ['version.info']},
        'license': {'path': license_name, 'sha256': sha(license_raw),
                    'source': 'https://github.com/SMEFTsim/SMEFTsim/blob/db7d4a80bdcff424eee27dde71f1eb09ac894039/LICENSE'},
    }
    raw_manifest = encoded(manifest)
    targets = {ufo/name: raw for name, raw in payload.items()}
    targets[release/license_name] = license_raw
    targets[release/'manifest.json'] = raw_manifest
    targets[ROOT/'models/registry.json'] = encoded({'schema_version': 1, 'models': {model['model_id']: {
        'version': model['version'], 'ufo_directory': str(ufo.relative_to(ROOT/'models')),
        'manifest': str((release/'manifest.json').relative_to(ROOT/'models')),
        'manifest_sha256': sha(raw_manifest)}}})
    for path, raw in targets.items():
        if (not path.resolve().is_relative_to(ROOT.resolve())
                or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(ROOT))):
            raise ValueError('Unsafe installation target: ' + str(path))
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Existing file differs: ' + str(path))
    for path, raw in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return {'model': model['model_id'], 'files': len(payload), 'status': 'installed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    print(json.dumps(install(parser.parse_args().offline), indent=2))
