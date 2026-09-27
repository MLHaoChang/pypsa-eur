#!/bin/bash
# SessionStart hook for Claude Code on the web: a pixi-free environment in
# which the pypsa-gui suites run —
#   cd pypsa-gui/backend && python -m pytest -m "not slow"
#   cd pypsa-gui/backend && python tests/run_qa_drivers.py
#   cd pypsa-gui/frontend && npx vitest run
# Versions mirror pixi.toml / pixi.lock (see
# docs/superpowers/findings/2026-09-26-eh-wire-skipped-stages.md for why each
# pin matters). Idempotent: re-runs are no-ops once the venv is satisfied.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
VENV="${HOME}/.venv-pypsa-gui"

# gridspine uses Python-3.12-only f-strings; pixi pins 3.12.12.
PY312="$(command -v python3.12 || true)"
if [ -z "$PY312" ]; then
  echo "session-start: python3.12 not found; pypsa-gui backend needs 3.12" >&2
  exit 1
fi

UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "${HOME}/.local/bin/uv" ] && UV="${HOME}/.local/bin/uv"

if [ ! -x "${VENV}/bin/python" ]; then
  if [ -n "$UV" ]; then
    "$UV" venv --python "$PY312" "$VENV" -q
  else
    "$PY312" -m venv "$VENV"
  fi
fi

PKGS=(
  "pypsa==1.1.2" "linopy==0.8.0" "highspy==1.14.0" "xarray<2025.7"
  # pandas 3.x breaks network.copy() / frozen re-solves (StringDtype).
  "pandas==2.3.3" "numpy==2.4.6" "scipy==1.17.1"
  netcdf4 openpyxl geopandas pytest pytest-asyncio python-dotenv ruff
  # gridspine + desktop suites; lightsim2grid 1.x lacks LSGrid.get_lineor_res.
  pandapower pywebview "lightsim2grid==0.10.1"
)
if [ -n "$UV" ]; then
  "$UV" pip install --python "${VENV}/bin/python" -q "${PKGS[@]}" \
    -r "${REPO}/pypsa-gui/backend/requirements.txt"
else
  "${VENV}/bin/python" -m pip install -q "${PKGS[@]}" \
    -r "${REPO}/pypsa-gui/backend/requirements.txt"
fi

# Frontend (npm install, not ci, so the cached container state is reused).
if [ -f "${REPO}/pypsa-gui/frontend/package.json" ]; then
  (cd "${REPO}/pypsa-gui/frontend" && npm install --no-audit --no-fund --loglevel=error)
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"${VENV}\""
    echo "export PATH=\"${VENV}/bin:\$PATH\""
    # Backend imports gridspine from the repo root.
    echo "export PYTHONPATH=\"${REPO}:${REPO}/pypsa-gui/backend\${PYTHONPATH:+:\$PYTHONPATH}\""
  } >> "$CLAUDE_ENV_FILE"
fi
