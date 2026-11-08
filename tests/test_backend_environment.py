"""Fault reporting and native lepton-cut applicability regressions."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from nsbi_agent import backend
from nsbi_agent.contracts import EnvironmentRequest
from nsbi_agent.graph import run_graph
from nsbi_agent.tools.card_validator import validate_cards
from nsbi_agent.tools.environment import PROJECT_ROOT, inspect_environment


def backend_fixture(tmp_path):
    root=tmp_path/'project';source=root/'backends/madgraph/2.9.27/source'
    source.mkdir(parents=True)
    for name in backend.ANCHORS:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(PROJECT_ROOT/'backends/madgraph/2.9.27/source'/name,path)
    shutil.copyfile(PROJECT_ROOT/'backends/madgraph/2.9.27/manifest.json',source.parent/'manifest.json')
    shutil.copyfile(PROJECT_ROOT/'backends/registry.json',root/'backends/registry.json')
    return root


def rewrite_manifest(root,change):
    path=root/'backends/madgraph/2.9.27/manifest.json'
    manifest=json.loads(path.read_text());change(manifest);path.write_text(json.dumps(manifest))
    registry=root/'backends/registry.json';data=json.loads(registry.read_text())
    data['madgraph']['manifest_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    registry.write_text(json.dumps(data))


@pytest.mark.parametrize('damage',[
    'array','null','entry_array','path_integer','root_integer','hash_array','version_null',
    'manifest_array','files_array','anchor_array','anchor_hash_null','commit_array','symlink_loop'])
def test_invalid_backend_metadata_and_paths_are_structured(tmp_path,monkeypatch,damage):
    root=backend_fixture(tmp_path);rp=root/'backends/registry.json';registry=json.loads(rp.read_text())
    if damage=='array':rp.write_text('[]')
    elif damage=='null':rp.write_text('null')
    elif damage=='entry_array':rp.write_text('{"madgraph":[]}')
    elif damage in {'path_integer','root_integer','hash_array','version_null'}:
        key,value={'path_integer':('manifest',17),'root_integer':('root',17),
                   'hash_array':('manifest_sha256',[]),'version_null':('version',None)}[damage]
        registry['madgraph'][key]=value;rp.write_text(json.dumps(registry))
    elif damage=='manifest_array':
        mp=root/'backends/madgraph/2.9.27/manifest.json';mp.write_text('[]')
        registry['madgraph']['manifest_sha256']=hashlib.sha256(mp.read_bytes()).hexdigest();rp.write_text(json.dumps(registry))
    elif damage=='files_array':rewrite_manifest(root,lambda m:m.update(files=[]))
    elif damage=='anchor_array':rewrite_manifest(root,lambda m:m['files'].update(VERSION=[]))
    elif damage=='anchor_hash_null':rewrite_manifest(root,lambda m:m['files']['VERSION'].update(sha256=None))
    elif damage=='commit_array':rewrite_manifest(root,lambda m:m.update(commit=[]))
    else:
        p=root/'backends/madgraph/2.9.27/source/VERSION';p.unlink();p.symlink_to('VERSION')
    monkeypatch.setattr(backend,'ROOT',root)
    with pytest.raises(backend.BackendError):backend.backend_info()
    make_version = backend.software_version('make')['version']
    request=EnvironmentRequest(packages={'six':'1.17.0'},backends={'madgraph':'2.9.27','make':make_version})
    direct=inspect_environment(request);via_graph=run_graph(request)
    assert direct.status==via_graph.status=='failed' and direct.model_dump()==via_graph.model_dump()
    assert direct.data['backends']['madgraph']['version'] is None
    assert direct.data['backends']['madgraph']['error_code'].startswith('BACKEND_')
    assert direct.data['backends']['make']['version']==make_version
    assert [e.message for e in direct.errors]==['backend_version:madgraph']
    assert direct.errors[0].code=='ENVIRONMENT_CHECK_FAILED'


@pytest.mark.parametrize('name',['gfortran','make'])
@pytest.mark.parametrize('stdout,stderr',[('',''),('   \n',''),('','GNU Make 4.3\n'),('','version on stderr\n')])
def test_empty_stdout_version_is_a_structured_failure(monkeypatch,name,stdout,stderr):
    fake=subprocess.CompletedProcess([name,'--version'],0,stdout,stderr)
    monkeypatch.setattr(backend.subprocess,'run',lambda *a,**kw:fake)
    result=inspect_environment(EnvironmentRequest(packages={'six':'1.17.0'},backends={name:'4.3'}))
    assert result.status=='failed'
    assert result.data['backends'][name]['error_code']=='BACKEND_VERSION_UNREADABLE'
    assert result.errors[0].code=='ENVIRONMENT_CHECK_FAILED'


def test_blank_lines_before_a_real_stdout_banner_are_not_a_missing_version(monkeypatch):
    fake=subprocess.CompletedProcess(['make','--version'],0,'\n  \nGNU Make 4.3\n','')
    monkeypatch.setattr(backend.subprocess,'run',lambda *a,**kw:fake)
    assert backend.software_version('make')['version']=='4.3'


@pytest.mark.parametrize('command',['inspect','graph'])
@pytest.mark.parametrize('fault',['registry','empty_banner'])
def test_faulted_real_cli_always_returns_toolresult_json(tmp_path,command,fault):
    root=backend_fixture(tmp_path)
    if fault=='registry':(root/'backends/registry.json').write_text('[]')
    runner='''import runpy,sys,subprocess
from pathlib import Path
from nsbi_agent import backend
root,fault=sys.argv.pop(1),sys.argv.pop(1)
backend.ROOT=Path(root)
if fault=='empty_banner':
    backend.subprocess.run=lambda *a,**kw:subprocess.CompletedProcess(a[0],0,'','')
sys.argv[0]='nsbi_agent'
runpy.run_module('nsbi_agent',run_name='__main__')
'''
    path=tmp_path/'input.json'
    path.write_text(json.dumps({'packages':{'six':'1.17.0'},'backends':
        {'madgraph':'2.9.27'} if fault=='registry' else {'make':'4.3'}}))
    done=subprocess.run([sys.executable,'-I','-B','-c',runner,str(root),fault,command,str(path)],
                        capture_output=True,text=True,timeout=30)
    result=json.loads(done.stdout)
    assert done.returncode==1 and not done.stderr and result['status']=='failed'
    assert result['errors'][0]['code']=='ENVIRONMENT_CHECK_FAILED'

