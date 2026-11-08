"""Relocation, site-toolchain and fresh-checkout examples."""
import json
from pathlib import Path
import subprocess
import sys

from nsbi_agent import backend
from nsbi_agent.workflow import prompts
from nsbi_agent.workflow.state import PROJECT_ROOT


def test_stage_commands_follow_current_project_root(monkeypatch, tmp_path):
    root = tmp_path/'relocated-project'
    monkeypatch.setattr(prompts, 'PROJECT_ROOT', root)
    request = {'simulation': {'process': 'p p > e+ e-'}}
    prepare = prompts.stage_prepare_cards(request, str(root/'workspace/task1'), 1, None)
    analysis = prompts.stage_analysis('task1', request, None, str(root/'workspace/task1'),
                                      1, str(root/'events.lhe'), {}, '')
    assert str(root/'scripts/mg5.sh') in prepare
    assert str(root/'scripts/mg5_run.sh') in prepare
    assert str(root/'src/nsbi_agent/workflow/templates/analysis.py') in analysis
    assert str(root/'.venv/bin/python') in analysis


def test_compiler_inventory_uses_site_path(monkeypatch, tmp_path):
    compiler = tmp_path/'gfortran'
    compiler.write_text('#!/bin/sh\nprintf "Site Fortran 14.2.0\\n"\n')
    compiler.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path))
    info = backend.software_version('gfortran')
    assert info['version'] == '14.2.0' and info['path'] == str(compiler)


def test_checkout_examples_are_self_contained():
    subprocess.run([sys.executable, '-I', '-B', str(PROJECT_ROOT/'scripts/prepare_examples.py')],
                   check=True, capture_output=True, text=True)
    for command, name in [('check-sample', 'check_sample'), ('validate-cards', 'check_existing_cards'),
                          ('check-experience', 'check_experience')]:
        done = subprocess.run([str(PROJECT_ROOT/'run.sh'), command,
                               str(PROJECT_ROOT/'.cache/examples'/(name+'.json'))],
                              capture_output=True, text=True)
        result = json.loads(done.stdout)
        assert done.returncode == 0, result
        assert result['status'] == 'succeeded'
