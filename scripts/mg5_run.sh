#!/usr/bin/env bash
# Project wrapper for MadGraph 2.9.27 event generation in a generated process
# directory (``./bin/generate_events``).
#
# Why this wrapper exists: the pinned SMEFTsim 3.0.2 UFO is Python-2 syntax,
# and MG5's event generator otherwise runs that UFO's own
# ``write_param_card.py`` with Python 3 as a standalone script.  The UFO is
# pinned read-only, so this wrapper instead reuses the process-local default
# parameter card that MG5 itself wrote at ``output`` time
# (``Cards/param_card_default.dat``).  MG5 then skips the Python-2 subprocess
# and merges the task's ``Cards/param_card.dat`` against that default card.
#
# setup.sh installs an independent Python 3.10.12 for the backend.
#
# Usage:  scripts/mg5_run.sh <process_dir> [generate_events args...]
#         scripts/mg5_run.sh <process_dir> -f
set -euo pipefail
if [[ $# -lt 1 ]]; then
    echo "usage: $0 <process_dir> [generate_events args...]" >&2
    exit 2
fi
process_dir="$(cd -- "$1" && pwd)"
shift
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_root/scripts/backend_env.sh"
# MadGraph starts Python subprocesses that may import pinned UFO files.
export PYTHONDONTWRITEBYTECODE=1

ufomodel="$process_dir/bin/internal/ufomodel"
if [[ -d "$ufomodel" && ! -f "$ufomodel/param_card.dat" ]]; then
    if [[ ! -f "$process_dir/Cards/param_card_default.dat" ]]; then
        echo "missing $process_dir/Cards/param_card_default.dat" >&2
        exit 3
    fi
    cp "$process_dir/Cards/param_card_default.dat" "$ufomodel/param_card.dat"
fi

cd "$process_dir"
exec "$mg5_python" ./bin/generate_events "$@"
