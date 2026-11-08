#!/usr/bin/env bash
# Shared interpreter selection for process export and event generation.
mg5_python="${MG5_PYTHON:-$project_root/.tools/mg5-venv/bin/python}"
if [[ ! -x "$mg5_python" ]]; then
    echo "MadGraph Python missing. Run scripts/setup.sh or set MG5_PYTHON to a Python 3.10 executable." >&2
    exit 2
fi
"$mg5_python" -I -c 'import sys; assert sys.version_info[:2] == (3, 10), "MadGraph requires Python 3.10"'
unset PYTHONHOME
export PYTHONNOUSERSITE=1
