"""Core model data used by parameter/card checks; direct file/catalog access."""
import json
import shutil
from pathlib import Path

import pytest
from nsbi_agent import model_store
from nsbi_agent.catalog import CatalogError, load_catalog
from nsbi_agent.tools.environment import PROJECT_ROOT


def copy_official_default(tmp_path, monkeypatch):
    store = tmp_path/'models'; store.mkdir()
    registry = json.loads((PROJECT_ROOT/'models/official_registry.json').read_text())
    name = 'SMEFTsim_U35_MwScheme_UFO'; record = registry['models'][name]
    (store/'official_registry.json').write_text(json.dumps({'schema_version': 1, 'models': {name: record}}))
    shutil.copytree(PROJECT_ROOT/'models'/record['ufo_directory'], store/record['ufo_directory'])
    manifest = PROJECT_ROOT/'models'/record['manifest']
    (store/record['manifest']).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest, store/record['manifest'])
    shutil.copytree(manifest.parent/'licenses', (store/record['manifest']).parent/'licenses')
    monkeypatch.setattr(model_store, 'STORE_ROOT', store)
    return store, record


@pytest.mark.parametrize('flavour,eft_count', [('general',2499),('U35',85),('MFV',130),('top',275),('topU3l',182)])
@pytest.mark.parametrize('scheme', ['MwScheme','alphaScheme'])
def test_all_variant_parameter_counts(flavour, eft_count, scheme):
    catalog, digest = load_catalog(f'SMEFTsim_{flavour}_{scheme}_UFO', '3.0.2', 'official')
    assert len(digest) == 64
    assert sum(p['block'] in {'SMEFT','SMEFTCPV','SMEFTFV'} for p in catalog['parameters'].values()) == eft_count
    assert ('MW' in catalog['parameters']) == (scheme == 'MwScheme')


@pytest.mark.parametrize('damage', ['bytes','missing','link'])
def test_catalog_checks_source_integrity(tmp_path, monkeypatch, damage):
    store, record = copy_official_default(tmp_path, monkeypatch)
    source = store/record['ufo_directory']/'parameters.py'
    if damage == 'bytes': source.write_bytes(source.read_bytes()+b'\n# changed\n')
    else:
        source.unlink()
        if damage == 'link': source.symlink_to('parameters.py')
    with pytest.raises(CatalogError): load_catalog('SMEFTsim_U35_MwScheme_UFO','3.0.2','official')


def test_official_and_tutorial_provenance_remain_separate():
    tutorial, a = load_catalog(collection='tutorial')
    official, b = load_catalog(collection='official')
    assert a != b and tutorial['provenance']['commit'] != official['provenance']['commit']
