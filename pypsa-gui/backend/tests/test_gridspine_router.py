"""`/api/gridspine` is a wrapper layer, and these tests hold it to that.

Increment 4, task 5. Each handler resolves and authorizes a project and then
calls ONE function in `services/gridspine_service.py`. So the tests check two
things and deliberately not a third: that the right service function is called
with the right arguments, and that authorization behaves like every other
project-scoped router. What the service DOES is `test_gridspine_service.py`'s
business — asserting it again here would be the second implementation this
layer exists to avoid.

The cross-org case is the one worth spelling out: a project belonging to
another org must 404, never 403, because a 403 tells the caller the name
exists somewhere — the existence oracle `routers/deps.py` closes.
"""
import uuid
import zipfile

import pytest

from db.models import Project, User
from services import gridspine_service as gs

CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False}


@pytest.fixture
def study(client, _auth_db, seeded_identity):
    """A planning → dynamics project, created through the API."""
    resp = client.post("/api/gridspine/projects", json={"name": "Router Study", "config": CONFIG})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def row(_auth_db, seeded_identity, study):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(Project, uuid.UUID(study["id"]))


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------

def test_create_returns_the_study_and_its_config(study):
    assert study["kind"] == gs.PLANNING_DYNAMICS
    assert study["name"] == "Router Study"
    assert study["config"]["hours"] == 24
    assert study["status"]["status"] == "not started"


def test_create_refuses_an_invalid_config_with_422(client):
    resp = client.post("/api/gridspine/projects", json={"name": "Bad", "config": {"hours": 0}})
    assert resp.status_code == 422, resp.text


def test_create_needs_an_authenticated_user(anon_client):
    resp = anon_client.post("/api/gridspine/projects", json={"name": "Anon", "config": CONFIG})
    assert resp.status_code in (401, 403), resp.text


# --------------------------------------------------------------------------
# authorization
# --------------------------------------------------------------------------

def test_another_orgs_study_is_404_not_403(other_org_client, study):
    """404 for both 'no such project' and 'not yours' — a 403 would leak that
    the name exists in some other org."""
    for path in (
        "/api/gridspine/Router Study/status",
        "/api/gridspine/Router Study/snapshots",
        "/api/gridspine/Router Study/ledger",
    ):
        resp = other_org_client.get(path)
        assert resp.status_code == 404, (path, resp.status_code)


def test_a_project_that_does_not_exist_is_404(client):
    assert client.get("/api/gridspine/No Such Project/status").status_code == 404


def test_a_capacity_expansion_project_is_409_through_the_api(client, api_project):
    """The kind check is the service's, and the router surfaces it unchanged."""
    name = api_project("capacity demo")
    resp = client.get(f"/api/gridspine/{name}/status")
    assert resp.status_code == 409, resp.text
    assert "planning" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------
# every handler is a wrapper
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method, path, payload, function, expected", [
    ("get", "/status", None, "get_stage_status", {"status": "not started"}),
    ("get", "/snapshots", None, "list_ranked_snapshots", [{"hour": 7}]),
    ("get", "/ledger", None, "get_assumption_ledger", {"entries": []}),
    ("post", "/run", None, "run_pipeline", {"id": "job-1", "kind": "gridspine"}),
    ("post", "/dispatch-source", {"source": "generate"}, "set_dispatch_source", {"from_dispatch": None}),
])
def test_each_endpoint_calls_its_service_function_and_returns_its_answer(
    client, study, monkeypatch, method, path, payload, function, expected
):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(gs, function, fake)
    resp = getattr(client, method)(f"/api/gridspine/Router Study{path}", **({"json": payload} if payload else {}))
    assert resp.status_code == 200, resp.text
    assert resp.json() == expected
    assert len(calls) == 1
    # the project ROW reached the service, not a name from the URL
    passed = [a for a in calls[0][0] if isinstance(a, Project)]
    assert passed and passed[0].name == "Router Study"


def test_a_template_edit_reaches_the_service_with_its_provenance(client, study, monkeypatch):
    seen = {}

    def fake(project, unit_id, param, value, source, edited_by):
        seen.update(unit_id=unit_id, param=param, value=value, source=source, edited_by=edited_by)
        return {"ok": True}

    monkeypatch.setattr(gs, "edit_template_param", fake)
    resp = client.put(
        "/api/gridspine/Router Study/templates/G_BUS_32/h_s",
        json={"value": 4.25, "source": "datasheet"},
    )
    assert resp.status_code == 200, resp.text
    assert seen == {"unit_id": "G_BUS_32", "param": "h_s", "value": 4.25,
                    "source": "datasheet", "edited_by": "user"}


def test_a_dispatch_source_pointing_at_a_run_is_passed_through(client, study, monkeypatch):
    seen = {}
    monkeypatch.setattr(gs, "set_dispatch_source",
                        lambda db, project, source: seen.update(source=source) or {"ok": True})
    resp = client.post(
        "/api/gridspine/Router Study/dispatch-source",
        json={"source": "from_dispatch", "from_dispatch": "/tmp/somewhere"},
    )
    assert resp.status_code == 200, resp.text
    assert seen["source"] == {"from_dispatch": "/tmp/somewhere"}


# --------------------------------------------------------------------------
# the download
# --------------------------------------------------------------------------

def test_the_bundle_endpoint_streams_the_zip_the_service_wrote(client, study, row, tmp_path, monkeypatch):
    _db, project = row
    target = gs.gridspine_dir(project) / "bundle_h7.zip"
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("bundle_h7/manifest.json", "{}")
    monkeypatch.setattr(gs, "export_handoff_bundle", lambda project, hour: target)

    resp = client.get("/api/gridspine/Router Study/bundles/7")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert "bundle_h7.zip" in resp.headers.get("content-disposition", "")
    assert resp.content[:2] == b"PK"


def test_an_unselected_hour_is_404_through_the_api(client, study):
    assert client.get("/api/gridspine/Router Study/bundles/4242").status_code == 404
