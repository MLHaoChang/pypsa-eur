#!/usr/bin/env bash
# GC-09 shots, end to end: seed a scratch app-data dir, start the API on :8010
# and Vite on :5180 (proxying /api to :8010), shoot, then stop both.
#   .scratch/grid-compliance/shots/run.sh <out-dir> [light|dark]
# PYTHON defaults to the backend venv. The Vite config is copied into
# pypsa-gui/frontend for the run (a config outside it cannot resolve its
# packages) and removed on exit; it is never committed there.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
out=$(mkdir -p "$1" && cd "$1" && pwd)
PYTHON=${PYTHON:-/root/.venv-pypsa-gui/bin/python}
data=$(mktemp -d)
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  rm -f "$root/pypsa-gui/frontend/vite.shots-8010.config.ts"
  rm -rf "$data"
}
trap cleanup EXIT

export PYPSAGUI_APP_DATA_DIR=$data
"$PYTHON" "$here/setup.py" 2>&1 | tail -1
(cd "$root/pypsa-gui/backend" && CORS_ALLOWED_ORIGINS=http://127.0.0.1:5180 \
  exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port 8010 >"$out/backend.log" 2>&1) &
pids+=($!)
cp "$here/vite.shots-8010.config.ts" "$root/pypsa-gui/frontend/"
(cd "$root/pypsa-gui/frontend" && PYPSA_GUI_API_ORIGIN=http://127.0.0.1:8010 \
  exec npx vite --config vite.shots-8010.config.ts >"$out/vite.log" 2>&1) &
pids+=($!)
for _ in $(seq 1 60); do
  curl -sf -o /dev/null http://127.0.0.1:8010/api/health && curl -sf -o /dev/null http://127.0.0.1:5180/ && break
  timeout 1 tail -f /dev/null || true
done
NODE_PATH=$(npm root -g) PWFILE=$data/dev-admin-password OUT=$out THEME=${2:-} \
  node "${SHOOT:-$here/grid-compliance-shots.mjs}"
