"""Check a Linux deployment with native compilers; no model API call or simulation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from nsbi_agent.contracts import EnvironmentRequest
from nsbi_agent.model_store import inspect_local_model
from nsbi_agent.tools.environment import inspect_environment

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = inspect_environment(EnvironmentRequest(
        package_names=['langgraph', 'pydantic', 'matplotlib', 'six'],
        backend_names=['madgraph', 'gfortran', 'make']))
    checks = {'environment': result.status, 'compilers': {}, 'models': []}
    errors = [str(item.message) for item in result.errors]
    python = os.environ.get('MG5_PYTHON', str(ROOT/'.tools/mg5-venv/bin/python'))
    try:
        done = subprocess.run([python, '-I', '-c',
                               'import platform,sys,six; assert six.__version__ == "1.17.0"; print(platform.python_version()); '
                               'assert sys.version_info[:2] == (3,10)'],
                              capture_output=True, text=True, check=True, timeout=15)
        checks['madgraph_python'] = done.stdout.strip()
        registry = json.loads((ROOT/'models/official_registry.json').read_text())
        for model in sorted(registry['models']):
            info = inspect_local_model(model, '3.0.2', registry_name='official_registry.json')
            checks['models'].append({'model': model, 'files': info['verified_files']})
        info = inspect_local_model('SMEFTsim_U35_MwScheme_UFO', '3.0.2')
        checks['reference_model_files'] = info['verified_files']
        # TMPDIR is honored on batch nodes; executable/library compatibility is tested.
        with tempfile.TemporaryDirectory(prefix='simulation-deployment-') as folder:
            folder = Path(folder)
            sources = {'gfortran': ('probe.f90', 'program probe\nprint *, 42\nend program probe\n'),
                       'g++': ('probe.cc', '#include <iostream>\nint main(){std::cout << 42;}\n')}
            for compiler, (name, source) in sources.items():
                executable = shutil.which(compiler)
                if executable is None:
                    raise ValueError(compiler + ' missing; load a compiler module or ask the site administrator')
                path = folder/name
                path.write_text(source)
                binary = folder/(compiler+'-probe')
                subprocess.run([executable, str(path), '-o', str(binary)], check=True,
                               capture_output=True, text=True, timeout=60)
                run = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=15)
                if run.stdout.strip() != '42':
                    raise ValueError('Unexpected compiler probe output: ' + compiler)
                checks['compilers'][compiler] = 'compiled and executed'
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    checks.update(status='failed' if errors else 'passed', errors=errors)
    print(json.dumps(checks, indent=2))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
