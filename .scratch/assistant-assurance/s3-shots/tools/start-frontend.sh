#!/usr/bin/env bash
# Vite dev server from the throwaway wt-s3-shots checkout (node_modules symlinked from the master checkout).
set -euo pipefail
cd /home/user/wt-s3-shots/pypsa-gui/frontend
exec npx vite --host 127.0.0.1 --port 5173 --strictPort
