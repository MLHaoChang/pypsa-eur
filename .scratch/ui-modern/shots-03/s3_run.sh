#!/usr/bin/env bash
# Start API + Vite, shoot, stop. Usage: s3_run.sh [tag-substring]
# S3_BACKEND=<dir> runs the API from another checkout of pypsa-gui/backend (e.g. a `git archive HEAD` copy).
root=/home/user/pypsa-eur
(cd ${S3_BACKEND:-$root/pypsa-gui/backend} && exec /root/.venv-pypsa-gui/bin/python -m uvicorn main:app --port 8000 >/tmp/s3-api.log 2>&1) &
(cd $root/pypsa-gui/frontend && exec npx vite --port 5173 --strictPort >/tmp/s3-vite.log 2>&1) &
for i in $(seq 60); do curl -sf -o /dev/null http://127.0.0.1:5173/ && curl -s -o /dev/null http://127.0.0.1:8000/api/auth/me && break; sleep 2; done
cd $root/.scratch/ui-modern/shots-03/s3work && NODE_PATH=${NODE_PATH:-$(npm root -g)} node ${S3_JS:-../s3_shoot.js} "$@"
pkill -f "uvicorn main:app --port 8000"; pkill -f "vite --port 5173"
