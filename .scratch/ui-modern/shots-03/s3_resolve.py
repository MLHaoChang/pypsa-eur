"""
Solve the saved base projects once more through the queue (API server stopped).

A project made from a template and solved once through the queue reads as out of date when it is
loaded from disk (the fingerprint recorded at solve differs from the saved network's); a second
queue solve of the saved project records the saved network's fingerprint. Run from pypsa-gui/backend.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
import main
from fastapi.testclient import TestClient

with TestClient(main.app) as c:
    from services import chat_tools, project_registry
    from services import workflow_tools as wt

    pw = (
        (Path.home() / ".local/share/PyPSA Studio/dev-admin-password")
        .read_text()
        .strip()
    )
    c.post("/api/auth/login", json={"email": "admin@example.com", "password": pw})
    h = {"X-CSRF-Token": c.cookies.get("pypsa_gui_csrf")}
    me = c.get("/api/auth/me").json()
    chat_tools.set_acting_user((me.get("user") or me)["id"])
    names = sys.argv[1:] or ["Alpha site", "Solo site", "Staged site"]
    for name in names:
        r = c.post("/api/simulation/queue", headers=h, json={"project_id": name})
        job = r.json()
        for _ in range(20):
            t = chat_tools.wait_for_job(job.get("id") or job.get("job_id"), 25)
            if t.get("terminal"):
                break
        print("DIAG solve", name, r.status_code, t.get("completed"))
    with chat_tools._acting() as (db, user):
        for name in names + ["Alpha more rooftop PV", "Bravo site"]:
            ctx = wt._load_context(project_registry.resolve_project(db, user, name))
            m = ctx.network.meta
            print(
                "DIAG",
                name,
                m.get("assistant_solve_input_fingerprint", "")[:10],
                wt._network_input_fingerprint(ctx)[:10],
                wt.solve_freshness(ctx),
            )
