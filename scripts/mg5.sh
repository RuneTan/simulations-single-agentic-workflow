#!/usr/bin/env bash
# Project launcher for the pinned MadGraph 5 2.9.27 backend.
#
# Why this wrapper exists: the pinned SMEFTsim 3.0.2 UFO is Python-2 syntax,
# and MG5's `convert model` would edit the pinned model source in place.  This
# launcher runs the unmodified MG5 executable with a project-local
# `sitecustomize.py` import hook that applies MG5's documented Python-3
# syntax conversion in memory (see scripts/mg5_compat/sitecustomize.py).
# The pinned model files stay byte-identical for checkers 2 and 3.
#
# setup.sh installs an independent Python 3.10.12 for the backend.
#
# Usage is identical to MG5 itself, e.g.
#   scripts/mg5.sh -f my_mg5_script.mg5
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mg5_root="$project_root/backends/madgraph/2.9.27/source"
source "$project_root/scripts/backend_env.sh"
# MadGraph starts Python subprocesses that may import pinned UFO files.
export PYTHONDONTWRITEBYTECODE=1

# Scope the shared import hook to the pinned official UFO variants.
shopt -s nullglob
ufo_dirs=("$project_root"/models/smeftsim/official/3.0.2/SMEFTsim_*_UFO/
          "$project_root"/models/smeftsim/3.0.2/SMEFTsim_*_UFO/)
export NSBI_MG5_PY2_UFO_DIRS="$(IFS=:; printf '%s' "${ufo_dirs[*]}")"
export NSBI_MG5_SM_WRITE_PARAM_CARD="$mg5_root/models/sm/write_param_card.py"
export PYTHONPATH="$project_root/scripts/mg5_compat${PYTHONPATH:+:$PYTHONPATH}"

exec "$mg5_python" "$mg5_root/bin/mg5_aMC" "$@"
