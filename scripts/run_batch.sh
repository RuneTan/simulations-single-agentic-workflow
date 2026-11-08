#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# != 1 ]]; then
    echo "Usage: scripts/run_batch.sh <request.txt>" >&2
    exit 2
fi
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP VIRTUAL_ENV CONDA_PREFIX
export PATH="$project_root/.venv/bin:$project_root/.tools/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
export XDG_CACHE_HOME="$project_root/.cache"
export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
cd "$project_root"
exec .venv/bin/python -I -B scripts/run_task.py --request-file "$1" --cores 2
