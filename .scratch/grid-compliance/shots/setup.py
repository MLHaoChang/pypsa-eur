"""
Seed a scratch app-data dir with a project in the GC-09 states the shots need.

    PYPSAGUI_APP_DATA_DIR=<dir> python setup.py

Runs the real API in-process (TestClient) against <dir>; then start uvicorn on
:8010 with the same PYPSAGUI_APP_DATA_DIR and run grid-compliance-shots.mjs.
States: 13 judged; 14 frozen + submitted; 12 frozen then the campus file edited
(out of date); 11 results in, packed, one answered and one open comment; 15 data
list edited; nothing frozen on a row whose campus is missing is not shot here.
"""

import os
import sys
from pathlib import Path

assert os.environ.get("PYPSAGUI_APP_DATA_DIR"), "set PYPSAGUI_APP_DATA_DIR"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pypsa-gui" / "backend"))
os.chdir(sys.path[0])

from app_paths import app_data_dir  # noqa: E402
from db.models import Project  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from services import campus_electrical_service as ce  # noqa: E402
from sqlalchemy import select  # noqa: E402

NAME = os.environ.get("GC_SHOTS_PROJECT", "Harbour Hub")
BASE = f"/api/grid-compliance/{NAME}"


def ok(resp, code=200):
    assert resp.status_code == code, (
        f"{resp.request.method} {resp.request.url}: {resp.status_code} {resp.text[:300]}"
    )
    return resp


def campus(text):
    from db.session import SessionLocal  # noqa: PLC0415

    with SessionLocal() as db:
        project = db.execute(select(Project).where(Project.name == NAME)).scalar_one()
        db.expunge(project)
    (ce.campus_dir(project) / ce.CAMPUS_FILE).write_text(text)


def install_network():
    """A small network as the live one, bound to NAME (tests/conftest.py's install)."""
    import pandas as pd  # noqa: PLC0415
    import pypsa  # noqa: PLC0415
    from services.pypsa_service import PyPSAService  # noqa: PLC0415

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "Harbour 110 kV", v_nom=110.0)
    n.add("Load", "Electrolyser", bus="Harbour 110 kV", p_set=100.0)
    n.add("Generator", "Grid import", bus="Harbour 110 kV", carrier="grid", p_nom=200.0)
    n.name = NAME
    PyPSAService.set_network(n)
    PyPSAService.set_loaded_project(NAME)


install_network()
with TestClient(app) as c:
    pw = (app_data_dir() / "dev-admin-password").read_text().strip()
    ok(c.post("/api/auth/login", json={"email": "admin@example.com", "password": pw}))
    from settings import get_settings  # noqa: PLC0415

    c.headers["X-CSRF-Token"] = c.cookies.get(get_settings().csrf_cookie_name)
    c.headers["Origin"] = "http://127.0.0.1:5173"
    ok(c.post(f"/api/projects/{NAME}", params={"force": True, "rebind": True}))
    campus("campus: {name: harbour}\n")
    st = lambda code, s: c.put(f"{BASE}/deliverables/{code}/status", json={"status": s})  # noqa: E731
    ok(st("13", "judged"))
    ok(c.post(f"{BASE}/deliverables/14/freeze"))
    ok(st("14", "submitted"))
    ok(c.post(f"{BASE}/deliverables/12/freeze"))
    ok(st("11", "results_in"))
    ok(c.post(f"{BASE}/deliverables/11/pack"))
    for text in ("Add the transformer datasheet.", "Show the recovery time."):
        ok(
            c.post(
                f"{BASE}/deliverables/11/comments",
                json={"date": "2026-11-02", "clause": "4.2", "comment": text},
            ),
            201,
        )
    ok(
        c.patch(
            f"{BASE}/deliverables/11/comments/C-001",
            json={
                "response": "Added in section 3.",
                "revision": "B",
                "status": "responded",
                "pack_version": 1,
            },
        )
    )
    items = ok(c.get(BASE)).json()["deliverables"][14]["data"]
    items[0] |= {"supplier": "TSO", "due": "2026-12-01", "status": "received"}
    ok(c.put(f"{BASE}/deliverables/15/data", json={"items": items}))
    ok(st("15", "data"))
    campus("campus: {name: harbour, edited: true}\n")
    rows = {r["code"]: r for r in ok(c.get(BASE)).json()["deliverables"]}
    print(
        {
            k: (rows[k]["status"], rows[k]["freeze_state"])
            for k in ("11", "12", "13", "14", "15")
        }
    )
