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
    parser.add_argument("--journey", choices=["oj01", "oj05", "oj21", "oj22", "lc16c", "lc03h", "lc06d24"],
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
            results, texts = [], []
            for message in messages[last + 1:]:
                if message["role"] == "tool":
                    text = message.get("content") if isinstance(message.get("content"), str) else json.dumps(message.get("content"))
                    texts.append(text)
                    try:
                        results.append(json.JSONDecoder().raw_decode(text[text.index("{"):])[0])
                    except ValueError:
                        results.append({"unparsed": text[:200]})
            if "Run the simulation" in said:
                # OJ-01 solves twice on purpose: the second is a re-run of current
                # results, which LC-19 asks about first; the user's yes is the flag.
                steps = [("run_simulation", {"rerun_current": True})]
            elif "this line: s_nom 400" in said:
                # LC-04 OJ-05: "this line" from the selection, then one edit with its card.
                target = (results[0].get("target") or {}) if results else {}
                steps = [("resolve_reference", {"target": "component", "component_class": "Line"}),
                         ("update_component", {"component_class": target.get("component_class"),
                                               "name": target.get("name"), "attrs": {"s_nom": 400}})]
            elif "and the battery's overnight power cost" in said:
                # LC-16 OJ-21: one preview for three assets of two classes, then one apply card.
                steps = [("preview_project_changes", {"changes": [
                    {"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": 500},
                    {"component_class": "Line", "names": ["L2"], "attribute": "s_nom", "value": 450},
                    {"component_class": "StorageUnit", "names": ["Battery"], "attribute": "inv_power_overnight",
                     "value": 150000}]}),
                         ("apply_project_changes", {"preview_id": results[0].get("preview_id") if results else None})]
                if results and not results[0].get("preview_id"):
                    steps = steps[:1]  # a refused preview ends the turn; nothing to apply
            elif "solar capacity by 20 %" in said or "Halve the gas capacity" in said:
                # LC-16 OJ-22: the server resolves the set and computes every value; one apply card.
                carrier, factor = ("solar", 1.2) if "solar" in said else ("gas", 0.5)
                steps = [("preview_project_changes", {"changes": [
                    {"component_class": "Generator", "select": {"carrier": carrier}, "attribute": "p_nom",
                     "operation": {"scale": factor}}]}),
                         ("apply_project_changes", {"preview_id": results[0].get("preview_id") if results else None})]
                if results and not results[0].get("preview_id"):
                    steps = steps[:1]
            elif "wind capacity by 20 %" in said:
                # LC-16 OJ-22: an empty set is a refusal in words; nothing is previewed.
                steps = [("preview_project_changes", {"changes": [
                    {"component_class": "Generator", "select": {"carrier": "wind"}, "attribute": "p_nom",
                     "operation": {"scale": 1.2}}]})]
            elif "and L3 to 400" in said:
                # LC-16 OJ-23: one member does not exist; nothing is previewed.
                steps = [("preview_project_changes", {"changes": [
                    {"component_class": "Line", "names": ["L1", "L2"], "attribute": "s_nom", "value": 500},
                    {"component_class": "Line", "names": ["L3"], "attribute": "s_nom", "value": 400}]})]
            elif "rolling with a 48-snapshot horizon" in said:
                # LC-16 slice C: settings edits get the same before/after card as assets.
                steps = [("update_solver_config", {"partial": {"solve_strategy": "rolling", "rolling_horizon": 48}})]
            elif "discount rate to 5 %" in said:
                steps = [("update_solver_config", {"partial": {"discount_rate": 0.05, "default_lifetime": 30}})]
            elif "Rename the network to Harbour grid" in said:
                steps = [("update_meta", {"name": "Harbour grid"})]
            elif "Weight 2030 and 2040" in said:
                steps = [("set_investment_period_weightings",
                          {"updates": {"2040": {"years": 10, "objective": 0.5}, "2030": {"years": 10}}})]
            elif "Set L1 and L2 to 500 MVA" in said:
                # LC-16 slice C: a numeric bulk edit in chat is refused with the preview form;
                # the model re-sends those changes as one preview, then one apply card.
                refusal = texts[0] if texts else ""
                changes = (json.JSONDecoder().raw_decode(refusal[refusal.index("changes=") + 8:])[0]
                           if "changes=" in refusal else None)
                steps = [("bulk_update_components", {"component_class": "Line", "names": ["L1", "L2"],
                                                     "updates": {"s_nom": 500}}),
                         ("preview_project_changes", {"changes": changes}),
                         ("apply_project_changes", {"preview_id": results[1].get("preview_id")
                                                    if len(results) > 1 else None})]
                if len(results) > 1 and not results[1].get("preview_id"):
                    steps = steps[:2]  # a refused preview ends the turn; nothing to apply
            elif "LC06D24 select" in said:
                steps = [("ui_select_component", {"component_class": "Line", "name": "L2"})]
            elif "LC06D24 unknown" in said:
                steps = [("ui_select_component", {"component_class": "Line", "name": "Nope"})]
            elif "LC06D24 detail" in said:
                steps = [("ui_open_asset_detail", {"component_class": "Line", "name": "L2"})]
            elif "LC06D24 panel" in said:
                steps = [("ui_open_panel", {"panel_id": "results", "results_tab": "capex"})]
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
            elif "capacity results of the first run" in said:
                # LC-03 LC-T09: a past run is opened from its kept section, never the current results.
                items = (results[0].get("items") or [{}]) if results else [{}]
                first = items[-1].get("run_id")
                steps = [("list_project_runs", {"limit": 10, "status": "completed"}),
                         ("discuss_project_run", {"run_id": first, "results_tab": "capex",
                                                  "section": "results_capacity"}),
                         ("get_results", {"result_kind": "economics_by_carrier", "run_id": first})]
                if results and "items" not in results[0]:
                    steps = steps[:1]  # the runs are refused (LC-T08): say why, read nothing else
            elif "capacity results of the latest run" in said:
                # LC-T10: a newer turn about the current results supersedes the past-run view.
                steps = [("get_results", {"result_kind": "economics_by_carrier"}),
                         ("ui_open_panel", {"panel_id": "results", "results_tab": "capex"})]
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
            elif steps[-1][0] == "preview_project_changes" and "wind" in said:
                # A refusal comes back as "kind\n<untrusted_data>\nmessage\n</untrusted_data>": say the message.
                raw = results[0].get("message") or results[0].get("unparsed") or json.dumps(results[0])
                text = f"OJ22_EMPTY {raw.split('<untrusted_data>')[-1].split('</untrusted_data>')[0].strip()[:600]}"
            elif "LC06D24" in said:
                text = f"LC06D24_ANSWER {json.dumps(results[-1])[:300]}"
            elif steps[-1][0] == "preview_project_changes":
                text = f"OJ23_ANSWER {json.dumps(results[0])[:600]}"
            elif steps[-1][0] in ("update_component", "undo_last", "apply_project_changes", "update_solver_config",
                                  "update_meta", "set_investment_period_weightings"):
                receipt = results[-1].get("_edit_receipt") or {}
                text = (f"OJ05_ANSWER {receipt.get('says') or results[-1]}; "
                        f"{'read back' if receipt.get('outcome') == 'verified' else 'not confirmed'}; "
                        f"results stale: {receipt.get('results_stale')}; "
                        f"undo: {(receipt.get('undo') or {}).get('available')}.")
            elif steps == [("list_project_runs", {"limit": 10, "status": "completed"})]:
                raw = texts[0] if texts else ""
                text = f"LC03H_REFUSED {raw.split('<untrusted_data>')[-1].split('</untrusted_data>')[0].strip()[:400]}"
            elif steps[0][0] == "list_project_runs" and steps[-1][0] == "get_results":
                read = results[2]
                gas = ((read.get("data") or {}).get("by_carrier") or {}).get("gas") or {}
                text = (f"LC03H_PAST Run {steps[1][1]['run_id']} ({read.get('scope')}): "
                        f"gas LCOE {(gas.get('lcoe_eur_per_mwh') or {}).get('total')} EUR/MWh.")
            elif steps[0] == ("get_results", {"result_kind": "economics_by_carrier"}):
                gas = (results[0].get("by_carrier") or {}).get("gas") or {}
                text = (f"LC03H_LATEST Current results: "
                        f"gas LCOE {(gas.get('lcoe_eur_per_mwh') or {}).get('total')} EUR/MWh.")
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
            if args.journey == "lc03h" and self.path in ("/fixture/revoke-access", "/fixture/restore-access"):
                # LC-T08 in the browser: the signed-in user loses (then regains) their
                # organisation membership, a real ACL change made in the database.
                subprocess.run([sys.executable, "-c", MEMBERSHIP, self.path.rsplit("/", 1)[1],
                                str(storage / "membership.json"), str(credentials)],
                               cwd=backend_dir, env=env, check=True, capture_output=True)
                self.send_response(204)
                self.end_headers()
                return
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
    MEMBERSHIP = """
import json, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from db.session import SessionLocal
from db.models import OrgMembership, Project, User
action, kept, credentials = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
with SessionLocal() as db:
    user = db.scalar(select(User).where(User.email == json.loads(credentials.read_text())["email"]))
    row = db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id))
    if action == "revoke-access":
        # oj-grid belongs to a colleague; the user is demoted from org admin to member,
        # with no membership of that project: they can no longer open it.
        owner = User(id=uuid.uuid4(), email="colleague@example.test", password_hash=None,
                     status="active", is_super_admin=False, created_at=datetime.now(tz=timezone.utc))
        db.add(owner)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=owner.id, org_id=row.org_id, role="admin"))
        project = db.scalar(select(Project).where(Project.name == "oj-grid", Project.org_id == row.org_id))
        kept.write_text(json.dumps({"role": row.role, "created_by": str(project.created_by)}))
        project.created_by = owner.id
        row.role = "member"
    else:
        # Restored as it was: their role, and oj-grid theirs again, so they can reopen it.
        was = json.loads(kept.read_text())
        row.role = was["role"]
        project = db.scalar(select(Project).where(Project.name == "oj-grid", Project.org_id == row.org_id))
        project.created_by = uuid.UUID(was["created_by"])
    db.commit()
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
            if args.journey == "lc03h":
                ready["control"] = f"http://127.0.0.1:{model.server_port}/fixture"
            if args.journey in ("oj01", "oj05", "oj21", "oj22", "lc16c", "lc03h", "lc06d24"):
                # The same oj-grid the Tier A tests use (tests/oj_grid.py), as NetCDF.
                network = storage / "oj-grid.nc"
                # OJ-22 adds two more solar units, one inactive, so "all solar" is a set of three.
                more = ("n.add('Generator', 'Solar roof', bus='B2', p_nom=10, carrier='solar'); "
                        "n.add('Generator', 'Solar old', bus='B1', p_nom=5, carrier='solar', active=False); "
                        if args.journey == "oj22" else
                        # LC-16 slice C: two investment periods, so the weightings card has 2+ rows.
                        "import pandas as pd; n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], n.snapshots])); "
                        "n.investment_periods = [2030, 2040]; "
                        if args.journey == "lc16c" else "")
                subprocess.run([sys.executable, "-c", "import sys; from tests.oj_grid import build_oj_grid; "
                                f"n = build_oj_grid(); {more}n.export_to_netcdf(sys.argv[1])", str(network)],
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
