"""Seed the dev app with a solved base and three sensitivity cases (LC-18 shots).

Run from pypsa-gui/backend:  python <scratch>/seed.py <out.json>
Uses the dev server's own database and projects root (no test sandbox).
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd()))

import numpy as np
import pandas as pd
import pypsa
from fastapi.testclient import TestClient

import main
from db.session import SessionLocal
from services import active_project, chat_tools
from services.auth_service import resolve_session_row
from services.pypsa_service import PyPSAService
from settings import get_settings

pw = (pathlib.Path.home() / ".local/share/PyPSA Studio/dev-admin-password").read_text().strip()
client = TestClient(main.app)
client.__enter__()
r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": pw})
assert r.status_code == 200, r.text
client.headers["X-CSRF-Token"] = client.cookies.get(get_settings().csrf_cookie_name)

hours = 24
n = pypsa.Network()
n.set_snapshots(pd.date_range("2026-06-01", periods=hours, freq="h"))
n.add("Carrier", "gas", co2_emissions=0.2)
n.add("Carrier", "solar")
n.add("Carrier", "AC")
for b in ("Grid", "Campus", "Labs"):
    n.add("Bus", b, v_nom=110, carrier="AC")
solar = np.clip(np.sin((np.arange(hours) - 6) / 12 * np.pi), 0, None)
n.add("Generator", "Gas", bus="Grid", carrier="gas", p_nom=300, marginal_cost=50, capital_cost=40000)
n.add("Generator", "Solar", bus="Campus", carrier="solar", p_nom=200, p_max_pu=solar, marginal_cost=0)
n.add("Load", "Labs load", bus="Labs", p_set=150)
n.add("Line", "Grid-Campus", bus0="Grid", bus1="Campus", s_nom=220, x=0.1, r=0.01)
n.add("Line", "Campus-Labs", bus0="Campus", bus1="Labs", s_nom=200, x=0.1, r=0.01)

with SessionLocal() as db:
    row = resolve_session_row(db, client.cookies.get(get_settings().session_cookie_name))
    ctx, _slot = active_project.resolve_for_session(db, row)
binding = PyPSAService.bind_request_context(ctx)
cell = PyPSAService.bind_turn_cell()
user_id = str(row.user_id)
chat_tools.set_acting_user(user_id)

def wait(job_id):
    for _ in range(20):
        t = chat_tools.wait_for_job(job_id, 25)
        if t["terminal"]:
            return t
    raise RuntimeError(job_id)


for old in ("Lines 160", "Gas 65", "Gas 40", "Campus base"):
    d = client.delete(f"/api/projects/{old}")
    print("delete", old, d.status_code)
ctx.network = n
r = client.post("/api/projects/Campus base")
assert r.status_code == 200, r.text
with chat_tools._acting() as (db, user):
    from services import project_registry
    base = project_registry.resolve_project(db, user, "Campus base")
    base_id = str(base.id)

job = chat_tools.solve_queue_enqueue(base_id)
t = wait(job.get("id") or job.get("job_id"))
assert t["completed"], chat_tools.solve_queue_job_log(job.get("id") or job.get("job_id"), 20)

cases = [
    {"new_name": "Gas 40", "changes": [{"component_class": "Generator", "names": ["Gas"], "attribute": "marginal_cost", "value": 40}]},
    {"new_name": "Gas 65", "changes": [{"component_class": "Generator", "names": ["Gas"], "attribute": "marginal_cost", "value": 65}]},
    {"new_name": "Lines 160", "changes": [{"component_class": "Line", "names": ["Grid-Campus", "Campus-Labs"], "attribute": "s_nom", "value": 160}]},
]
out = chat_tools.run_sensitivity_sweep(base_id, cases)
for c in out["cases"]:
    t = wait(c["job"]["id"])
    assert t["completed"], c
ids = {c["name"] if "name" in c else None: c["project_id"] for c in out["cases"]}

# One case edited after its solve: listed as not comparable, in words.
from routers import projects
from services import workflow_tools as wt
with chat_tools._acting() as (db, user):
    lines = project_registry.resolve_project(db, user, out["cases"][2]["project_id"])
    lctx = wt._load_context(lines)
    lctx.network.lines.loc["Campus-Labs", "s_nom"] = 170
    projects._save_context(lctx, lines.name, storage_dir=pathlib.Path(lctx.storage_dir), db=db, user=user,
                           project_row=lines)

report = chat_tools.compare_sensitivities(base_id, rank_by="total_cost_meur")
pathlib.Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=1))
print("seeded", report["status"], [c["name"] for c in report["cases"]], report["rank"]["preference"]["name"])
PyPSAService._turn_cell.reset(cell)
PyPSAService.reset_request_context(binding)
