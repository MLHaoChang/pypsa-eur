"""Read-back through the action layer (increment 6, spec stage 6).

The bundle is made by hand — the stage is pandas on CSVs, so no solve is
needed to hold the seam: the upload lands under the study's `gridspine/uploads/
h<hour>/` under the client's (sanitised) filename, the driver compares it with
the bundle's own load flow and records the outcome in the bundle, and the
three reads answer from those files. Refusals carry the driver's reason as
422; an hour with no bundle is 404; a capacity-expansion project is 409 like
every other action.
"""
import json
import uuid

import pandas as pd
import pytest
from fastapi import HTTPException

from db.models import Project, User
from services import gridspine_service as gs
from services import project_registry

CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False}
HOUR = 19
BUS_OK = b"bus_name,vm_pu,va_degree\nBUS_01,1.0305,0.01\nBUS_02,0.9846,-5.15\n"
BUS_BAD = b"bus_name,vm_pu,va_degree\nBUS_01,1.0305,0.01\nBUS_02,1.100,-5.15\n"
BRANCH_OK = b"from_bus,to_bus,ckt,p_from_mw,q_from_mvar,loading_percent\nBUS_01,BUS_02,1,120.5,11.0,40.2\n"


def make_bundle(run_dir, hour=HOUR):
    b = run_dir / f"bundle_h{hour}"
    b.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"vm_pu": [1.030, 0.985], "va_degree": [0.0, -5.2]},
                 index=pd.Index(["BUS_01", "BUS_02"], name="bus")).to_csv(b / "lf_bus.csv", index_label="bus")
    pd.DataFrame({"from_bus": ["BUS_01"], "to_bus": ["BUS_02"], "ckt": ["1"],
                  "p_from_mw": [120.0], "q_from_mvar": [10.0], "loading_percent": [40.0]}).to_csv(b / "lf_branch_flow.csv", index=False)
    (b / "manifest.json").write_text(json.dumps({"case": "case39", "hour": hour, "converged": True, "files": []}))
    return b


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


@pytest.fixture
def study(user_and_db):
    db, user = user_and_db
    row = db.get(Project, uuid.UUID(gs.create_study(db, user, "Readback Study", config=CONFIG)["id"]))
    make_bundle(gs.run_dir(row))
    return row


def test_an_upload_is_kept_under_the_study_compared_and_recorded(study):
    summary = gs.upload_readback(study, HOUR, BUS_OK, "case39_h19.csv", BRANCH_OK, "case39_h19_branches.csv")
    assert summary["pass"] is True and summary["hour"] == HOUR
    assert summary["sources"]["bus_csv"]["filename"] == "case39_h19.csv"
    assert summary["sources"]["branch_csv"]["filename"] == "case39_h19_branches.csv"
    kept = gs.gridspine_dir(study) / "uploads" / "h19"
    assert (kept / "case39_h19.csv").read_bytes() == BUS_OK
    assert (gs.run_dir(study) / "bundle_h19" / "readback.json").is_file()
    assert gs.get_readback(study) == {"19": summary}
    assert gs.get_stage_status(study)["readback"]["19"]["pass"] is True


def test_a_client_filename_cannot_escape_the_uploads_directory(study):
    kept = gs.gridspine_dir(study) / "uploads" / "h19"
    gs.upload_readback(study, HOUR, BUS_OK, "../../../etc/evil.csv", None, None)
    assert sorted(p.name for p in kept.iterdir()) == ["evil.csv"]       # the basename, inside the uploads dir
    gs.upload_readback(study, HOUR, BUS_OK, "../../../etc/passwd", None, None)
    assert "pf_bus.csv" in {p.name for p in kept.iterdir()}             # nothing usable → the fallback name
    projects_root = gs.gridspine_dir(study).parent.parent
    assert not list(projects_root.rglob("passwd")) and not list(projects_root.parent.glob("etc"))


def test_a_failing_export_is_recorded_as_a_failure_not_refused(study):
    summary = gs.upload_readback(study, HOUR, BUS_BAD, "b.csv")
    assert summary["pass"] is False and summary["bus"]["worst"] == "BUS_02"
    assert summary["branches"] is None


@pytest.mark.parametrize("hour, bus, status, detail", [
    (7, BUS_OK, 422, "no handoff bundle for hour 7"),
    (HOUR, b"bus_name,vm_pu\nBUS_01,1.0\n", 422, "missing required columns"),
    (HOUR, b"bus_name,vm_pu,va_degree\nBUS_01,1.0,0.0\n", 422, "bus set mismatch"),
])
def test_the_drivers_refusals_are_422_with_its_reason(study, hour, bus, status, detail):
    with pytest.raises(HTTPException) as exc:
        gs.upload_readback(study, hour, bus, "b.csv")
    assert exc.value.status_code == status
    assert detail in exc.value.detail


def test_figures_answer_per_hour_and_name(study):
    assert gs.fetch_result_figure(study, "vm", HOUR)["available"] is False
    gs.upload_readback(study, HOUR, BUS_OK, "b.csv")
    fig = gs.fetch_result_figure(study, "vm", HOUR)
    assert fig["available"] is True and [r["element"] for r in fig["rows"]] == ["BUS_01", "BUS_02"]
    assert gs.fetch_result_figure(study, "branch_p", HOUR)["available"] is False      # no branch export
    with pytest.raises(HTTPException) as exc:
        gs.fetch_result_figure(study, "nope", HOUR)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        gs.fetch_result_figure(study, "vm", 7)
    assert exc.value.status_code == 404


def test_every_read_back_action_refuses_a_capacity_expansion_project(user_and_db):
    db, user = user_and_db
    plain = project_registry.create_root(db, user, "Plain Readback")
    for call in (
        lambda: gs.upload_readback(plain, HOUR, BUS_OK, "b.csv"),
        lambda: gs.get_readback(plain),
        lambda: gs.fetch_result_figure(plain, "vm", HOUR),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 409


def test_the_router_uploads_and_reads_back_end_to_end(client, _auth_db, seeded_identity):
    resp = client.post("/api/gridspine/projects", json={"name": "Router Readback", "config": CONFIG})
    assert resp.status_code == 200, resp.text
    _engine, session_local = _auth_db
    with session_local() as db:
        make_bundle(gs.run_dir(db.get(Project, uuid.UUID(resp.json()["id"]))))
    resp = client.post("/api/gridspine/Router Readback/readback/19",
                       files={"bus": ("case39_h19.csv", BUS_OK, "text/csv")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pass"] is True
    assert client.get("/api/gridspine/Router Readback/readback").json()["19"]["bus"]["n_ok"] == 2
    assert client.get("/api/gridspine/Router Readback/figures/19/va").json()["available"] is True
    bad = client.post("/api/gridspine/Router Readback/readback/19",
                      files={"bus": ("x.csv", b"bus_name,vm_pu\nBUS_01,1\n", "text/csv")})
    assert bad.status_code == 422 and "missing required columns" in bad.json()["detail"]
