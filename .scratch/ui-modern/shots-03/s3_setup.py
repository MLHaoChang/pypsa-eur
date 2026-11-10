"""ui-modern 03 S3 shots: build real projects in-process (API server stopped).

Run from pypsa-gui/backend:  /root/.venv-pypsa-gui/bin/python ../../.scratch/ui-modern/shots-03/s3_setup.py
"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
import main

def wait(job_id):
    from services import chat_tools
    for _ in range(40):
        t = chat_tools.wait_for_job(job_id, 25)
        if t.get("terminal"):
            return t
    return t

with TestClient(main.app) as c:
    from services import chat_tools, project_registry, workflow_tools as wt
    from services.pypsa_service import PyPSAService
    pw = (Path.home() / ".local/share/PyPSA Studio/dev-admin-password").read_text().strip()
    r = c.post("/api/auth/login", json={"email": "admin@example.com", "password": pw}); print("login", r.status_code)
    h = {"X-CSRF-Token": c.cookies.get("pypsa_gui_csrf")}
    me = c.get("/api/auth/me").json(); print("me", str(me)[:300])
    uid = (me.get("user") or me).get("id")
    chat_tools.set_acting_user(uid)
    ids = {}
    for tpl, name in [("eh_datacenter", "Alpha site"), ("eh_datacenter", "Bravo site"),
                      ("eh_datacenter_staged", "Staged site"), ("eh_datacenter", "Solo site")]:
        if not os.environ.get("S3_SKIP_CREATE"):  # set on a second run: the projects exist and are solved
            r = c.post(f"/api/projects/from_template/{tpl}", params={"name": name}, headers=h)
            print(tpl, r.status_code, r.text[:200])
            r = c.post("/api/simulation/queue", headers=h, json={"project_id": name})
            print("queue", r.status_code, r.text[:300])
            job = r.json(); t = wait(job.get("id") or job.get("job_id"))
            print(name, "solve", {k: t.get(k) for k in ("terminal", "completed", "status")})
        with chat_tools._acting() as (db, user):
            p = project_registry.resolve_project(db, user, name); ids[name] = str(p.id)
            if name == "Alpha site":
                n = wt._load_context(p).network
                print("GENS\n", n.generators[["carrier", "p_nom", "p_nom_extendable", "marginal_cost", "capital_cost"]])
                print("LINKS\n", n.links[["carrier", "p_nom_extendable", "marginal_cost"]] if len(n.links) else "no links")
    print(ids)
    gens = json.loads(os.environ.get("S3_NAMES", "[]"))
    if gens:
        for base, cases in [("Alpha site", [("Alpha more rooftop PV", 10), ("Alpha less rooftop PV", 0.25)]), ("Bravo site", [("Bravo more rooftop PV", 10)])]:
            if os.environ.get("S3_ONLY") and os.environ["S3_ONLY"] != base: continue
            with chat_tools._acting() as (db, user):
                n = wt._load_context(project_registry.resolve_project(db, user, base)).network
            mc = float(n.generators.loc[gens[0], "p_nom"])
            try:
                out = chat_tools.run_sensitivity_sweep(ids[base], [{"new_name": nm, "changes": [{"component_class": "Generator", "names": gens, "attribute": "p_nom", "value": round(mc * f, 2)}]} for nm, f in cases])
            except Exception as e:
                print("SWEEP FAILED", base, repr(e), getattr(e, "detail", None)); continue
            for case in out["cases"]:
                print("case", str(case)[:300]); t = wait(case["job"]["id"]); print(" ->", {k: t.get(k) for k in ("terminal", "completed", "status")})
        # Bravo: change the base after the sweep and save it (as an edit + save in the app).
        from routers import projects
        if os.environ.get("S3_ONLY", "Bravo site") != "Bravo site": sys.exit(0)
        with chat_tools._acting() as (db, user):
            p = project_registry.resolve_project(db, user, "Bravo site")
            active = PyPSAService.get_active_context()
            ctx = active if str(active.project_uuid) == str(p.id) else wt._load_context(p)
            ctx.network.generators.loc[gens[0], "p_nom"] *= 1.5
            projects._save_context(ctx, p.name, storage_dir=Path(ctx.storage_dir), db=db, user=user, project_row=p)
        print("bravo base edited")
