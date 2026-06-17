#!/usr/bin/env sh
# setup.sh -- one-shot setup for the PSO engine (Linux/macOS).
#
# Creates TWO environments (conflicting deps -> must stay separate):
#   .venv      PyPSA stack -- builds/transforms the network
#   .venv-pso  aimmspy     -- drives PSO for the solve
# Uses `uv` if present (fast); otherwise falls back to `python -m venv` + `pip`.
#
#   sh setup.sh
#
# Prereqs: Python 3.10+ and AIMMS installed (for the solve). aimmspy is installed
# to match your AIMMS; if AIMMS is older, pin it in pso.local.toml (aimms_version).
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
DEPS="pypsa==0.35.2 numpy pandas matplotlib scipy networkx openpyxl highspy"

if command -v uv >/dev/null 2>&1; then
    echo "==> uv found -- building envs with uv"
    uv sync
    uv venv .venv-pso
    uv pip install --python .venv-pso aimmspy
else
    echo "==> uv not found -- using python -m venv + pip"
    "$PY" -m venv .venv
    ./.venv/bin/python -m pip install --upgrade pip
    ./.venv/bin/python -m pip install $DEPS
    "$PY" -m venv .venv-pso
    ./.venv-pso/bin/python -m pip install --upgrade pip
    ./.venv-pso/bin/python -m pip install aimmspy
fi

# Seed pso.local.toml and wire the solve interpreter.
[ -f pso.local.toml ] || { cp pso.local.toml.example pso.local.toml; \
    echo "==> created pso.local.toml (edit: project, and license_url if academic)"; }
if ! grep -q '^[[:space:]]*python[[:space:]]*=' pso.local.toml; then
    echo "python = \"$(pwd)/.venv-pso/bin/python\"" >> pso.local.toml
    echo "==> wired solve interpreter -> $(pwd)/.venv-pso/bin/python"
fi

echo ""
echo "Setup done. Next:"
echo "  1. Edit pso.local.toml  (project = path to PSO.aimms; license_url if academic)"
echo "  2. ./.venv/bin/python pso/doctor.py     # verify"
echo "  3. ./.venv/bin/python devnet_stress.py  # run (pick PSO in the menu)"
