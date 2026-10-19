#!/usr/bin/env bash
# Launcher for process.py.
# Linux counterpart of run_processing.bat.
#
# Select the interpreter (first match wins):
#   1. METASHAPE_PYTHON=/path/to/python   -> use that interpreter directly
#   2. METASHAPE_VENV=/path/to/env        -> run through uv in that env
#   3. default: global python3 on the PATH
#
# Examples:
#   ./run_processing.sh --print-config
#   ./run_processing.sh --preset draft
#   METASHAPE_PYTHON=/usr/local/bin/python3.13 ./run_processing.sh
#   METASHAPE_VENV=~/venvs/other ./run_processing.sh --depth-downscale 1 --force
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${METASHAPE_PYTHON:-}" ]]; then
    echo "Using interpreter: $METASHAPE_PYTHON"
    exec "$METASHAPE_PYTHON" "$here/process.py" "$@"
elif [[ -n "${METASHAPE_VENV:-}" ]]; then
    echo "Using uv env: $METASHAPE_VENV"
    exec uv run --no-project --python "$METASHAPE_VENV" python "$here/process.py" "$@"
else
    py="$(command -v python3 || command -v python)"
    echo "Using default interpreter: $py"
    exec "$py" "$here/process.py" "$@"
fi
