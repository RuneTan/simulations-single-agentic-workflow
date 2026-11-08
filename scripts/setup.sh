#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
mode="${1:---all}"
if [[ "$mode" != --all && "$mode" != --python-only ]]; then
    echo "Usage: scripts/setup.sh [--all|--python-only]" >&2
    exit 2
fi
if [[ "$(uname -sm)" != "Linux x86_64" ]]; then
    echo "This installer targets Linux x86_64." >&2
    exit 2
fi

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV CONDA_PREFIX
export UV_NO_CONFIG=1
export XDG_CACHE_HOME="$project_root/.cache"
export UV_PROJECT_ENVIRONMENT="$project_root/.venv"
export UV_CACHE_DIR="$project_root/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$project_root/.tools/python"
export UV_PYTHON_BIN_DIR="$project_root/.tools/bin"
export UV_LINK_MODE=copy

uv_version=0.9.28
archive="$project_root/.cache/downloads/uv.tar.gz"
archive_sha256=66ad1822dd9cf96694b95c24f25bc05cff417a65351464da01682a91796d1f2b
mkdir -p .tools/bin .cache/downloads

if [[ ! -x .tools/bin/uv ]]; then
    if [[ ! -f "$archive" ]]; then
        curl --fail --location --retry 2 \
            "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-x86_64-unknown-linux-gnu.tar.gz" \
            --output "$archive"
    fi
    printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check -
    tar -xzf "$archive" --strip-components=1 -C .tools/bin uv-x86_64-unknown-linux-gnu/uv
fi
export PATH="$project_root/.tools/bin:$PATH"
.tools/bin/uv --version
.tools/bin/uv python install "$(cat .python-version)"
.tools/bin/uv sync --frozen --managed-python --python "$(cat .python-version)"
if [[ "$mode" == --all ]]; then
    .tools/bin/uv python install 3.10.12
    mg5_python="$(.tools/bin/uv python find --no-project --managed-python 3.10.12)"
    if [[ ! -x .tools/mg5-venv/bin/python ]]; then
        .tools/bin/uv venv --no-project .tools/mg5-venv --python "$mg5_python"
    fi
    .tools/bin/uv pip sync --python .tools/mg5-venv/bin/python --require-hashes scripts/backend-requirements.txt
    .venv/bin/python -I -B scripts/install_official_models.py
    .venv/bin/python -I -B scripts/install_reference_model.py
    .venv/bin/python -I -B scripts/install_backend.py
    .venv/bin/python -I -B scripts/check_deployment.py
fi
