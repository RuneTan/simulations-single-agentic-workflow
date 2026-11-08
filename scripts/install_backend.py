"""Install a pinned, project-owned MadGraph source tree; never run its setup/code."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def encoded(data):
    return (json.dumps(data, indent=2, sort_keys=True) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def install(archive: Path, source: dict):
    if sha(archive.read_bytes()) != source['archive_sha256']:
        raise ValueError('Backend archive SHA-256 mismatch')
    prefix = 'mg5amcnlo-' + source['commit'] + '/'
    targets, entries = {}, {}
    release = ROOT/'backends/madgraph'/source['version']
    with tarfile.open(archive) as tar:
        members = {m.name: m for m in tar.getmembers()}
        def contents(member, visited=()):
            if member.name in visited:
                raise ValueError('Cyclic source link')
            if member.isfile():
                return tar.extractfile(member).read()
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname)
                                            if member.issym() else member.linkname)
                if not target.startswith(prefix) or target not in members:
                    raise ValueError('External source link: ' + member.name)
                return contents(members[target], (*visited, member.name))
            raise ValueError('Unsupported source type: ' + member.name)
        for member in members.values():
            if member.isdir():
                continue
            if not member.name.startswith(prefix):
                raise ValueError('Unexpected source root')
            name = member.name[len(prefix):]
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('Unsafe source member')
            raw = contents(member)
            targets[release/'source'/name] = (raw, member.mode)
            entries[name] = {'sha256': sha(raw), 'size_bytes': len(raw),
                             'source_link': member.linkname if member.issym() or member.islnk() else None}
    manifest = {**source, 'files': entries, 'link_policy': 'internal archive links materialized as owned regular files'}
    raw = encoded(manifest)
    targets[release/'manifest.json'] = (raw, 0o644)
    targets[ROOT/'backends/registry.json'] = (encoded({'madgraph': {
        'version': source['version'], 'root': str((release/'source').relative_to(ROOT)),
        'manifest': str((release/'manifest.json').relative_to(ROOT)), 'manifest_sha256': sha(raw)}}), 0o644)
    for path, (data, mode) in targets.items():
        if not path.resolve().is_relative_to(ROOT.resolve()) or any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(ROOT)):
            raise ValueError('Unsafe backend destination')
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Refusing to replace a different backend file: ' + str(path))
    for path, (data, mode) in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data); path.chmod(0o755 if mode & 0o111 else 0o644)
    return {'version': source['version'], 'source_files': len(entries), 'root': str(release/'source')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    source = json.loads((ROOT/'scripts/backend_source.json').read_text())
    archive = args.archive or ROOT/'.cache/backend_downloads'/('madgraph-'+source['commit']+'.tar.gz')
    if not archive.exists():
        archive.parent.mkdir(parents=True,exist_ok=True)
        url = 'https://codeload.github.com/mg5amcnlo/mg5amcnlo/tar.gz/' + source['commit']
        with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as out:
            while chunk := response.read(1024*1024):
                out.write(chunk)
    print(json.dumps(install(archive,source),indent=2))


if __name__ == '__main__':
    main()
