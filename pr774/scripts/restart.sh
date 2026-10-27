# usage: restart.sh <repo root>
for p in $(pgrep -f "uvicor[n] main:app") $(pgrep -f "node_modules/.bin/vit[e]") $(pgrep -f "npm exec vit[e]") $(pgrep -f "sh -c vit[e]"); do kill $p; done
sleep 2
cd "$1/pypsa-gui/backend"
PYTHONPATH="$1:$1/pypsa-gui/backend" nohup uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/claude-0/uvicorn.log 2>&1 &
cd ../frontend
nohup npx vite --host 127.0.0.1 --port 5173 > /tmp/claude-0/vite.log 2>&1 &
