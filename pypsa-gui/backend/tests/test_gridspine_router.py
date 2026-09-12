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
    ("get", "/config", None, "get_config", {"hours": 24}),
    ("put", "/config", {"k": 3}, "update_config", {"k": 3}),
    ("get", "/readback", None, "get_readback", {"19": {"pass": True}}),
    ("get", "/figures/19/vm", None, "fetch_result_figure", {"available": False}),
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


@pytest.mark.parametrize("body, source", [
    ({"source": "from_dispatch", "from_dispatch": "/tmp/somewhere"}, {"from_dispatch": "/tmp/somewhere"}),
    ({"source": "from_project", "from_project": "Solved 39"}, {"from_project": "Solved 39"}),
    ({"from_project": "Solved 39"}, {"from_project": "Solved 39"}),
    ({"source": "generate"}, "generate"),
])
def test_a_dispatch_source_is_passed_through_with_the_acting_user(client, study, monkeypatch, body, source):
    seen = {}
    monkeypatch.setattr(gs, "set_dispatch_source",
                        lambda db, project, source, user=None: seen.update(source=source, user=user) or {"ok": True})
    resp = client.post("/api/gridspine/Router Study/dispatch-source", json=body)
    assert resp.status_code == 200, resp.text
    assert seen["source"] == source
    assert seen["user"] is not None        # from_project resolves under the caller, never anonymously


def test_the_config_read_carries_the_db_so_the_source_project_can_be_named(client, study, monkeypatch):
    seen = {}
    monkeypatch.setattr(gs, "get_config", lambda project, db=None: seen.update(db=db) or {"hours": 24})
    assert client.get("/api/gridspine/Router Study/config").status_code == 200
    assert seen["db"] is not None


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


def test_the_config_patch_carries_only_the_fields_that_were_sent(client, study, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        gs, "update_config",
        lambda project, patch, *, db=None, user=None: seen.update(patch=patch, db=db, user=user) or patch,
    )
    resp = client.put("/api/gridspine/Router Study/config", json={"k": 3, "screen": False})
    assert resp.status_code == 200, resp.text
    assert seen["patch"] == {"k": 3, "screen": False}
    # `from_dispatch` is authorization-bearing, so the route must hand the
    # service a `db` and a `user` to check it against — without them every
    # patch carrying one is refused.
    assert seen["db"] is not None and seen["user"] is not None
    resp = client.put("/api/gridspine/Router Study/config", json={"set_from_dispatch": True, "from_dispatch": None})
    assert seen["patch"] == {"from_dispatch": None}



def test_a_read_back_upload_reaches_the_service_with_both_files_and_their_names(client, study, monkeypatch):
    seen = {}

    def fake(project, hour, bus_csv, bus_name=None, branch_csv=None, branch_name=None):
        seen.update(hour=hour, bus=bus_csv, bus_name=bus_name, branches=branch_csv, branch_name=branch_name)
        return {"pass": True}

    monkeypatch.setattr(gs, "upload_readback", fake)
    resp = client.post(
        "/api/gridspine/Router Study/readback/19",
        files={"bus": ("case39_h19.csv", b"bus_name,vm_pu,va_degree\n", "text/csv"),
               "branches": ("case39_h19_branches.csv", b"from_bus,to_bus,ckt\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert seen["hour"] == 19 and seen["bus_name"] == "case39_h19.csv"
    assert seen["bus"].startswith(b"bus_name") and seen["branches"].startswith(b"from_bus")
    # the branch export is optional
    resp = client.post("/api/gridspine/Router Study/readback/19",
                       files={"bus": ("b.csv", b"bus_name,vm_pu,va_degree\n", "text/csv")})
    assert resp.status_code == 200, resp.text
    assert seen["branches"] is None and seen["branch_name"] is None


def _running_on_the_event_loop() -> bool:
    """True when the caller is executing on a thread that is running an asyncio
    event loop — i.e. the service call was made directly from an `async def`
    handler rather than handed to a worker thread. A precise discriminator: a
    thread from Starlette's threadpool has no running loop, so the answer is
    False there whichever thread the test harness happens to use for the loop.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_an_upload_is_parsed_off_the_event_loop(client, study, monkeypatch):
    """A client's workbook is parsed by openpyxl, whose cost the CLIENT chooses.

    The handler is `async`, so anything synchronous in it runs ON the event loop:
    for as long as `pd.read_excel` is chewing through an attacker-sized sheet,
    every other request to this process — health checks, the SSE log and queue
    streams, other engineers' work — waits. A 4.5 MB `.xlsx` decompressing to
    400k rows measured 14 s; the upload cap permits ~115x that. The fix is to
    hand the synchronous service call to a worker thread, and the observable
    property is exactly this: it does not run on the main thread.
    """
    seen = {}

    def fake(project, dispatch_bytes, dispatch_name=None, loads_bytes=None, loads_name=None):
        seen["on_the_loop"] = _running_on_the_event_loop()
        return {"hours": 2, "units": 2}

    monkeypatch.setattr(gs, "upload_external_dispatch", fake)
    resp = client.post(
        "/api/gridspine/Router Study/dispatch-source/external",
        files={"dispatch": ("d.csv", b"unit_id,hour,p_mw,q_mvar,status\n", "text/csv"),
               "loads": ("l.csv", b"bus,hour,p_mw,q_mvar\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert seen["on_the_loop"] is False


def test_a_readback_upload_is_parsed_off_the_event_loop(client, study, monkeypatch):
    """The same for the read-back upload: it compares a client CSV against the
    bundle, and it is the other endpoint that parses attacker-sized files."""
    seen = {}
    monkeypatch.setattr(
        gs, "upload_readback",
        lambda *a, **k: seen.update(on_the_loop=_running_on_the_event_loop()) or {"ok": True},
    )
    resp = client.post(
        "/api/gridspine/Router Study/readback/19",
        files={"bus": ("b.csv", b"bus_name,vm_pu,va_degree\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert seen["on_the_loop"] is False


def test_an_external_dispatch_upload_reaches_the_service_with_both_files(client, study, monkeypatch):
    """Increment 7: two tables, or one workbook. The router's only jobs are the
    size cap and passing the client's filenames through — the suffix matters,
    because the producer picks its reader from it."""
    seen = {}

    def fake(project, dispatch_bytes, dispatch_name=None, loads_bytes=None, loads_name=None):
        seen.update(dispatch=dispatch_bytes, dispatch_name=dispatch_name,
                    loads=loads_bytes, loads_name=loads_name)
        return {"hours": 2, "units": 2}

    monkeypatch.setattr(gs, "upload_external_dispatch", fake)
    resp = client.post(
        "/api/gridspine/Router Study/dispatch-source/external",
        files={"dispatch": ("market_dispatch.csv", b"unit_id,hour,p_mw,q_mvar,status\n", "text/csv"),
               "loads": ("market_loads.csv", b"bus,hour,p_mw,q_mvar\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hours": 2, "units": 2}
    assert seen["dispatch_name"] == "market_dispatch.csv"
    assert seen["loads_name"] == "market_loads.csv"
    assert seen["dispatch"].startswith(b"unit_id") and seen["loads"].startswith(b"bus,")

    # One workbook carrying both sheets: the loads part is genuinely absent, not
    # an empty file the producer would then refuse for the wrong reason.
    resp = client.post(
        "/api/gridspine/Router Study/dispatch-source/external",
        files={"dispatch": ("both.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 200, resp.text
    assert seen["loads"] is None and seen["loads_name"] is None
    assert seen["dispatch_name"] == "both.xlsx"
