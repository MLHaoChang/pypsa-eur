#!/usr/bin/env bash
# macOS / Linux counterpart to start.bat.
#
# Starts the FastAPI backend (:8000) and the Vite dev server (:5173) in the
# background, streams both logs into this terminal, and tears both down on
# Ctrl-C. Unlike start.bat there are no separate console windows — the two
# services share this one, with each line tagged by origin.
#
# Node and Python both come from the pixi environment (nodejs >=22 is a
# pixi dependency), so there is no separate .nodeenv to activate.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_BIN="$ROOT/.pixi/envs/default/bin"
PYTHON="$ENV_BIN/python"
NPM="$ENV_BIN/npm"
BACKEND="$ROOT/pypsa-gui/backend"
FRONTEND="$ROOT/pypsa-gui/frontend"

if [ ! -x "$PYTHON" ]; then
  echo "error: pixi environment not found at $ENV_BIN" >&2
  echo "       run 'pixi install' from $ROOT first." >&2
  exit 1
fi

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "note: frontend dependencies missing — running npm install once..."
  (cd "$FRONTEND" && PATH="$ENV_BIN:$PATH" "$NPM" install) || exit 1
fi

echo "Starting PyPSA Studio..."
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo

BACKEND_PID=""
FRONTEND_PID=""

# Kill the whole process group of each child. Vite and uvicorn --reload both
# spawn workers that survive a bare kill on the parent, which is how stale
# servers end up holding :8000 and :5173.
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Shutting down..."
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    [ -n "$pid" ] || continue
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  echo "Stopped."
}
trap cleanup INT TERM EXIT

# setsid-equivalent on macOS: run each child in its own process group so the
# negative-PID kill above reaches its children too.
(
  cd "$BACKEND" || exit 1
  exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --reload --port 8000
) 2>&1 | sed $'s/^/\033[36m[backend] \033[0m/' &
BACKEND_PID=$!

sleep 2

(
  cd "$FRONTEND" || exit 1
  export PATH="$ENV_BIN:$PATH"
  exec "$NPM" run dev
) 2>&1 | sed $'s/^/\033[35m[frontend]\033[0m /' &
FRONTEND_PID=$!

echo "Both services starting. Open http://localhost:5173 in your browser."
echo "Press Ctrl-C to stop both."
echo

wait
