"""Install all ten pinned official UFO variants; never execute downloaded code.

Network is used only for installation. --archive supplies the same bytes offline.
Existing different files are never overwritten; the tutorial collection is untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def prepare_files(archive_path: Path, lock: dict) -> dict:
    """Read verified Git bytes directly from the archive, without extracting paths."""
    if sha(archive_path.read_bytes()) != lock['archive_sha256']:
        raise ValueError('Archive SHA-256 differs from pinned download')
    prefix = 'SMEFTsim-' + lock['commit'] + '/'
    payload = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            if member.isdir() and member.name.rstrip('/') == prefix.rstrip('/'):
                continue
            if not member.name.startswith(prefix):
                raise ValueError('Unexpected archive root: ' + member.name)
            name = member.name[len(prefix):]
            if name not in lock['files']:
                if name.startswith('UFO_models/') and member.isfile():
                    raise ValueError('Unregistered UFO file: ' + name)
                continue
            expected = lock['files'][name]
            if not member.isfile() or member.size != expected['size_bytes'] or name in payload:
                raise ValueError('Invalid/duplicate archive member: ' + name)
            raw = archive.extractfile(member).read()
            blob = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
            if blob != expected['git_blob']:
                raise ValueError('Upstream Git blob mismatch: ' + name)
            payload[name] = raw
    if payload.keys() != lock['files'].keys():
        raise ValueError('Archive does not contain all pinned source files')
    return payload


def install(archive_path: Path, lock: dict) -> dict:
    payload = prepare_files(archive_path, lock)
    store = ROOT / 'models'
    release = Path('smeftsim/official') / lock['version']
    license_name = 'licenses/SMEFTsim-MIT.txt'
    license_raw = payload['LICENSE']
    targets = {store / release / 'metadata' / license_name: license_raw}
    records = {}
    for model_id in sorted({n.split('/')[1] for n in payload if n.startswith('UFO_models/')}):
        flavour, ew = model_id.removeprefix('SMEFTsim_').removesuffix('_UFO').split('_')
        model = {'model_id': model_id, 'version': lock['version'], 'variant': 'official release',
                 'basis': 'Warsaw', 'flavour_scheme': flavour, 'ew_input_scheme': ew}
        source_dir = 'UFO_models/' + model_id
        files = {n[len(source_dir)+1:]: raw for n, raw in payload.items() if n.startswith(source_dir + '/')}
        if any(len(Path(n).parts) != 1 or Path(n).suffix not in {'.py','.dat','.info'} for n in files):
            raise ValueError('Unexpected nested/cache file in ' + model_id)
        if files['version.info'].decode().strip() != 'tag: v' + lock['version']:
            raise ValueError('UFO version mismatch: ' + model_id)
        manifest = {
            'schema_version': 1, 'model': model,
            'provenance': {'repository': lock['repository'], 'tag': lock['tag'], 'commit': lock['commit'],
                           'path': source_dir, 'archive_sha256': lock['archive_sha256'],
                           'copy_policy': 'exact pinned Git bytes; all UFO source/card files included; no execution'},
            'files': {n: {'sha256': sha(raw), 'size_bytes': len(raw),
                          'git_blob': lock['files'][source_dir + '/' + n]['git_blob']} for n, raw in files.items()},
            'classification': {'ufo_python': sorted(n for n in files if n.endswith('.py')),
                               'restriction_cards': sorted(n for n in files if n.startswith('restrict_')),
                               'parameter_card_templates': sorted(n for n in files if n.startswith('param_card')),
                               'version_metadata': ['version.info']},
            'license': {'path': license_name, 'sha256': sha(license_raw),
                        'source': lock['repository'] + '/blob/' + lock['commit'] + '/LICENSE'},
        }
        raw_manifest = encoded(manifest)
        manifest_name = release / 'metadata' / (model_id + '.json')
        records[model_id] = {'version': lock['version'], 'ufo_directory': str(release/model_id),
                             'manifest': str(manifest_name), 'manifest_sha256': sha(raw_manifest)}
        targets[store/manifest_name] = raw_manifest
        targets.update({store/release/model_id/n: raw for n,raw in files.items()})
    if len(records) != 10:
        raise ValueError('Expected all ten official variants')
    targets[store/'official_registry.json'] = encoded({'schema_version': 1, 'models': records})
    # Check all destinations before any write, including symlinked ancestors.
    for target, raw in targets.items():
        if not target.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('Destination outside project: ' + str(target))
        if any(p.is_symlink() for p in [target, *target.parents] if p.is_relative_to(ROOT)):
            raise ValueError('Symlinked destination: ' + str(target))
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Refusing to replace different existing file: ' + str(target))
    for target, raw in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
    return {'models': len(records), 'ufo_files': len(payload)-1, 'version': lock['version'],
            'commit': lock['commit'], 'registry': str(store/'official_registry.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='verified upstream tar.gz for offline installation')
    args = parser.parse_args()
    lock = json.loads((ROOT/'scripts/official_model_sources.json').read_text())
    archive = args.archive
    if archive is None:
        archive = ROOT/'.cache/model_downloads'/('SMEFTsim-' + lock['commit'] + '.tar.gz')
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            url = 'https://codeload.github.com/SMEFTsim/SMEFTsim/tar.gz/' + lock['commit']
            temporary = archive.with_suffix('.download')
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
                while chunk := response.read(1024*1024):
                    output.write(chunk)
            if sha(temporary.read_bytes()) != lock['archive_sha256']:
                raise ValueError('Downloaded archive checksum mismatch')
            temporary.replace(archive)
    print(json.dumps(install(archive, lock), indent=2))


if __name__ == '__main__':
    main()
