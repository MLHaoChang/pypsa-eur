#!/usr/bin/env bash
# GC-09 item 6 shots, end to end: start the harness Vite on :5182, shoot the 12 full-size PNGs into a temp dir
# (never committed), keep only their .small copies here, stop Vite.
#   .scratch/grid-compliance/shots/gc09-item6/run.sh
# The harness page, its entry and the Vite config are copied into pypsa-gui/frontend (untracked) for the run
# and removed on exit; they are never committed there.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../../.." && pwd)
fe=$root/pypsa-gui/frontend
full=${FULL:-$(mktemp -d)}
copied=(vite.gc09pf.config.ts gc09pf-harness.html gc09pf-harness.tsx)
pid=
cleanup() {
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  pkill -f "vite --config vite.gc09pf.config.ts" 2>/dev/null || true  # npx leaves its vite child behind
  for f in "${copied[@]}"; do rm -f "$fe/$f"; done
}
trap cleanup EXIT

for f in "${copied[@]}"; do cp "$here/$f" "$fe/"; done
(cd "$fe" && exec npx vite --config vite.gc09pf.config.ts >"$full/vite.log" 2>&1) &
pid=$!
for _ in $(seq 1 60); do
  curl -sf -o /dev/null http://127.0.0.1:5182/gc09pf-harness.html && break
  timeout 1 tail -f /dev/null || true
done
NODE_PATH=$(npm root -g) OUT=$full node "$here/shoot.mjs"
for f in "$full"/*.png; do
  case $f in *.small.png) ;; *) cp "$("$root/scripts/coord/shots.sh" "$f")" "$here/" ;; esac
done
echo "full-size shots in $full"
