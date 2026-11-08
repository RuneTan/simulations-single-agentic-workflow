#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Isolate Python while preserving site compiler paths and library modules.
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP VIRTUAL_ENV CONDA_PREFIX
export PATH="$project_root/.venv/bin:$project_root/.tools/bin:$PATH"
export XDG_CACHE_HOME="$project_root/.cache"
export PYTHONDONTWRITEBYTECODE=1
export LANGSMITH_TRACING=false
export LANGCHAIN_TRACING_V2=false

if [[ ! -x "$project_root/.venv/bin/python" ]]; then
    echo "Environment missing. Run scripts/setup.sh first." >&2
    exit 1
fi

# -I excludes user packages, PYTHON* settings and the caller's working directory.
if [[ "${1:-}" == test ]]; then
    shift
    cd "$project_root"
    exec "$project_root/.venv/bin/python" -I -B -m pytest "$@"
fi
exec "$project_root/.venv/bin/python" -I -B -m nsbi_agent "$@"
