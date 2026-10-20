# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Private loopback backend; no external model, optional finite decision wire."""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--decisions", action="store_true")
    parser.add_argument("--no-model", action="store_true", help="configure no model profile (the no-key path)")
    parser.add_argument("--journey", choices=["oj01", "oj05"],
                        help="script a finite owner journey on the loopback wire (LC-03 OJ-01)")
    args = parser.parse_args()
    storage = args.storage.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    storage.chmod(0o700)
    repository = Path(__file__).resolve().parents[3]
    backend_dir = repository / "pypsa-gui/backend"
    dist = repository / "pypsa-gui/frontend/dist"
    if (backend_dir / ".env").exists():
        raise RuntimeError("Use an isolated checkout without backend/.env; fixture credentials must stay isolated")
    if not (dist / "spa.html").is_file():
        raise RuntimeError("Build the current frontend with npm run build first")

    class LocalModelBoundary(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _stream(self, delta, finish):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()

        def _journey(self, body):
            """OJ-01 as a model would play it: every argument and every number in
            the answer comes from the native results of this turn."""
            messages = body["messages"]
            last = max(i for i, m in enumerate(messages) if m["role"] == "user")
            said = json.dumps(messages[last].get("content"))
            results = []
            for message in messages[last + 1:]:
                if message["role"] == "tool":
                    text = message.get("content") if isinstance(message.get("content"), str) else json.dumps(message.get("content"))
                    try:
                        results.append(json.JSONDecoder().raw_decode(text[text.index("{"):])[0])
                    except ValueError:
                        results.append({"unparsed": text[:200]})
            if "Run the simulation" in said:
                steps = [("run_simulation", {})]
            elif "this line: s_nom 400" in said:
                # LC-04 OJ-05: "this line" from the selection, then one edit with its card.
                target = (results[0].get("target") or {}) if results else {}
                steps = [("resolve_reference", {"target": "component", "component_class": "Line"}),
                         ("update_component", {"component_class": target.get("component_class"),
                                               "name": target.get("name"), "attrs": {"s_nom": 400}})]
            elif "Undo that change" in said:
                steps = [("undo_last", {})]
            elif "NH3 tank" in said:
                steps = [("update_component", {"component_class": "Store", "name": "NH3 tank",
                                               "attrs": {"e_nom": 80}})]
            elif "load flow results" in said:
                steps = [("list_project_runs", {"limit": 1, "status": "completed"}),
                         ("get_loadflow_summary", {"run_id": ((results[0].get("items") or [{}])[0].get("run_id")
                                                              if results else None)}),
                         ("ui_open_panel", {"panel_id": "results", "results_tab": "loadflow"})]
            elif "something else" in said:
                listing = results[0] if results else {}
                picked = next((v for v in listing.get("views", []) if v.get("data") == "available"), None)
                steps = [("list_result_views", {"exclude_shown": True})]
                if picked:
                    read = picked["read"][0]
                    steps += [(read["tool"], read["args"]),
                              ("ui_open_panel", {"panel_id": "results", "results_tab": picked["id"]})]
            else:
                return False
            if len(results) < len(steps):
                name, arguments = steps[len(results)]
                arguments = {k: v for k, v in arguments.items() if v is not None}
                self._stream({"tool_calls": [{"index": 0, "id": f"oj01-{len(results)}", "type": "function",
                              "function": {"name": name, "arguments": json.dumps(arguments)}}]}, "tool_calls")
                return True
            if "something else" in said:
                listing = results[0]
                offered = [v["id"] for v in listing.get("views", []) if v.get("data") == "available"]
                verb = "Showing" if (results[-1].get("display") or {}).get("say") == "showing" else "Opened"
                text = (f"OJ03_ANSWER {verb} {offered[0] if offered else 'nothing'}; "
                        f"also with data: {', '.join(offered[1:]) or 'none'}; "
                        f"already shown: {', '.join(listing.get('shown', []))}.")
            elif steps[-1][0] in ("update_component", "undo_last"):
                receipt = results[-1].get("_edit_receipt") or {}
                text = (f"OJ05_ANSWER {receipt.get('says') or results[-1]}; "
                        f"{'read back' if receipt.get('outcome') == 'verified' else 'not confirmed'}; "
                        f"results stale: {receipt.get('results_stale')}; "
                        f"undo: {(receipt.get('undo') or {}).get('available')}.")
            elif len(steps) == 1:
                text = f"The solve was started (job {results[0].get('job_id')})."
            else:
                run = (results[0].get("items") or [{}])[0]
                lf, shown = results[1], results[2]
                top = ", ".join(f"{r['name']} {r['util_pct']} % ({r['peak_flow']} MW of {r['rating']['value']} MVA)"
                                for r in lf.get("top", [])[:3])
                verb = "Showing" if (shown.get("display") or {}).get("say") == "showing" else "Opened"
                text = (f"OJ01_ANSWER {verb} the Load flow tab for run {run.get('run_id')} "
                        f"(finished {run.get('finished_at')}), current: {(lf.get('run') or {}).get('is_current')}. "
                        f"Highest loading: {top}. Active-power proxy, not an AC thermal check.")
            self._stream({"content": text}, "stop")
            return True

        def do_POST(self):
            if args.journey and self.path == "/v1/chat/completions":
                if self._journey(json.loads(self.rfile.read(int(self.headers["Content-Length"])))):
                    return
            if args.decisions and self.path == "/v1/chat/completions":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                messages = body["messages"]
                origins = [(i, "choice" if "NARROW_CHOICE" in json.dumps(message.get("content")) else "confirmation")
                           for i, message in enumerate(messages) if message["role"] == "user"
                           and any(marker in json.dumps(message.get("content"))
                                   for marker in ("NARROW_CHOICE", "NARROW_CONFIRMATION"))]
                latest_user = next((message for message in reversed(messages) if message["role"] == "user"), {})
                expected_input = any(marker in json.dumps(latest_user.get("content")) for marker in (
                    "NARROW_CHOICE", "NARROW_CONFIRMATION", "First result; Second result", "A different local result"))
                if origins and expected_input:
                    origin, kind = origins[-1]
                    subsequent = any(message["role"] == "tool" for message in messages[origin + 1:])
                    if not subsequent:
                        tool_name = "ask_user" if kind == "choice" else "bulk_update_components"
                        offered = {tool["function"]["name"] for tool in body.get("tools", [])}
                        if tool_name not in offered:
                            self.send_error(503, "Fixture tool absent from admitted catalogue")
                            return
                        arguments = ({"title": "Narrow decision", "question": "Which result should be shown?",
                            "options": [{"label": "First result", "recommended": True,
                                         "description": "The first local option."},
                                        {"label": "Second result", "description": "The other local option."}],
                            "multi_select": True, "allow_free_text": True,
                            "detail": "Review this local decision.\n\n" * 12}
                            if kind == "choice" else {"component_class": "Generator", "names": ["G1"],
                                                     "updates": {"marginal_cost": 20}})
                        delta = {"tool_calls": [{"index": 0, "id": "narrow-" + kind, "type": "function",
                            "function": {"name": tool_name, "arguments": json.dumps(arguments)}}]}
                    else:
                        delta = {"content": "Local fixture completed; inspect the native outcome."}
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    chunk = {"choices": [{"index": 0, "delta": delta,
                        "finish_reason": "stop" if subsequent else "tool_calls"}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
                    self.wfile.flush()
                    return
            # A layout action must not send inference or start media.
            (storage / "unexpected-provider-call").touch()
            self.send_error(503, "No inference is allowed in this layout fixture")

    model = ThreadingHTTPServer(("127.0.0.1", 0), LocalModelBoundary)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("PYPSA_GUI_LLM_KEY__", "ANTHROPIC_", "OPENAI_", "MOONSHOT_",
                           "KIMI_", "DEEPGRAM_", "LIVEKIT_", "PYPSA_GUI_OPENAI_")):
            env.pop(key)
    app_data = storage / "app-data"
    app_data.mkdir()
    env.update(
        PYPSAGUI_APP_DATA_DIR=str(app_data), PYPSAGUI_LOCAL_MODE="0",
        DATABASE_URL="sqlite:///" + str(app_data / "fixture.sqlite"),
        PROJECTS_ROOT=str(storage / "projects"), LEGACY_ROOT=str(storage / "legacy"),
        FLAT_PROJECTS_ROOT=str(storage / "flat-projects"),
        SECRET_KEY=secrets.token_urlsafe(32), PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join((str(backend_dir), str(repository))),
        FRONTEND_DIST=str(dist), CORS_ALLOWED_ORIGINS=f"http://127.0.0.1:{port}",
        OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
        MPLCONFIGDIR=str(storage / "mpl"), XDG_CACHE_HOME=str(storage / "cache"),
        PYPSA_GUI_ANSWER_ASSURANCE_MODE="pilot",
        PYPSA_GUI_ANSWER_ASSURANCE_PROFILES="generator-dispatch/1,generator-cost/1",
    )
    env["NO_PROXY"] = env.get("NO_PROXY", "") + ",127.0.0.1,localhost"
    credentials = storage / "credentials.json"
    credentials.write_text(json.dumps({"email": "composer@example.test", "password": secrets.token_urlsafe(24)}))
    credentials.chmod(0o600)
    if not args.no_model:
        (app_data / "llm-profiles.json").write_text(json.dumps({
            "active_profile_id": "composer-local", "profiles": [{
                "id": "composer-local", "label": "Offline layout fixture", "preset": "custom",
                "wire": "openai", "base_url": f"http://127.0.0.1:{model.server_port}/v1",
                "model": "no-inference", "tools": True, "vision": False, "auth": "none",
            }],
        }))
    seed = """
import json, sys
from pathlib import Path
from local_bootstrap import ensure_app_dirs, ensure_schema
from settings import get_settings
ensure_app_dirs(); ensure_schema(get_settings().database_url)
from tools.bootstrap_super_admin import bootstrap_super_admin
from db.session import SessionLocal
from db.models import User
from services.tenancy_service import ensure_personal_org
user = bootstrap_super_admin(**json.loads(Path(sys.argv[1]).read_text()))
with SessionLocal() as db:
    ensure_personal_org(db, db.get(User, user.id))
"""
    backend = None
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with (storage / "seed.log").open("w") as log:
            subprocess.run([sys.executable, "-c", seed, str(credentials)], cwd=backend_dir,
                           env=env, stdout=log, stderr=log, check=True)
        with (storage / "backend.log").open("w") as log:
            backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host",
                                        "127.0.0.1", "--port", str(port)], cwd=backend_dir,
                                       env=env, stdout=log, stderr=log)
            for _ in range(120):
                if backend.poll() is not None:
                    raise RuntimeError("Backend exited; inspect private backend.log")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request("GET", "/api/health")
                    if connection.getresponse().status == 200:
                        break
                except OSError:
                    pass
                finally:
                    connection.close()
                time.sleep(0.5)
            else:
                raise RuntimeError("Backend readiness timeout")
            ready = {"base": f"http://127.0.0.1:{port}", "credentials_file": str(credentials)}
            if args.journey in ("oj01", "oj05"):
                # The same oj-grid the Tier A tests use (tests/oj_grid.py), as NetCDF.
                network = storage / "oj-grid.nc"
                subprocess.run([sys.executable, "-c", "import sys; from tests.oj_grid import build_oj_grid; "
                                "build_oj_grid().export_to_netcdf(sys.argv[1])", str(network)],
                               cwd=backend_dir, env=env, check=True, capture_output=True)
                ready["network_file"] = str(network)
            print(json.dumps(ready), flush=True)
            backend.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=8)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait()
        model.shutdown()
        model.server_close()


if __name__ == "__main__":
    main()
