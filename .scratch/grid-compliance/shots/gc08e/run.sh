#!/usr/bin/env bash
# GC-08e shots, end to end: build data.json from the backend (make_data.py), start the harness Vite on :5181,
# shoot the 24 PNGs into this folder, make the .small copies, stop Vite.
#   .scratch/grid-compliance/shots/gc08e/run.sh
# The harness page, its entry and the Vite config are copied into pypsa-gui/frontend (untracked) for the run
# and removed on exit; they are never committed there.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../../.." && pwd)
fe=$root/pypsa-gui/frontend
PYTHON=${PYTHON:-/root/.venv-pypsa-gui/bin/python}
copied=(vite.gc08e.config.ts gc08e-harness.html gc08e-harness.tsx)
pid=
cleanup() {
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  pkill -f "vite --config vite.gc08e.config.ts" 2>/dev/null || true  # npx leaves its vite child behind
  for f in "${copied[@]}"; do rm -f "$fe/$f"; done
}
trap cleanup EXIT

(cd "$root/pypsa-gui/backend" && "$PYTHON" "$here/make_data.py" "$here/data.json")
for f in "${copied[@]}"; do cp "$here/$f" "$fe/"; done
(cd "$fe" && exec npx vite --config vite.gc08e.config.ts >"$here/vite.log" 2>&1) &
pid=$!
for _ in $(seq 1 60); do
  curl -sf -o /dev/null http://127.0.0.1:5181/gc08e-harness.html && break
  timeout 1 tail -f /dev/null || true
done
NODE_PATH=$(npm root -g) DATA=$here/data.json OUT=$here node "$here/shoot.mjs"
for f in "$here"/{S,P}[0-9]-*.png; do
  case $f in *.small.png) ;; *) "$root/scripts/coord/shots.sh" "$f" ;; esac
done
