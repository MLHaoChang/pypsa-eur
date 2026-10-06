"""`/api/campus-electrical/{name}/grid-codes/...` (plan C10).

A wrapper layer over ``services/campus_grid_code_service.py``, like the rest
of ``/api/campus-electrical``. These tests check:
* each route calls its ONE service function with the project row and the
  request's arguments;
* a real multipart upload, and the status codes the service's refusals reach
  the client as;
* every write is refused under another user's edit lock, and a read is not;
* another org's project is a 404.

What the service does is ``test_campus_grid_code_service.py``'s business.
Every Anthropic call is mocked.
"""
import pytest

from db.models import Project, User
from services import campus_grid_code_service as gc
from services import chat_service, project_registry
from tests.test_campus_grid_code_service import PDF, FakeClient, response, tool_input
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

BASE = "/api/campus-electrical/Code Hub/grid-codes"
SHA = "c" * 64


@pytest.fixture
def hub(client, _auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project_registry.create_root(db, user, "Code Hub")


ROUTES = [
    ("get", "", "list_grid_codes", None, ()),
    ("delete", f"/documents/{SHA}", "delete_document", None, (SHA,)),
    ("post", f"/documents/{SHA}/extract", "extract", {}, (SHA, None, False)),
    ("post", f"/documents/{SHA}/extract", "extract", {"profile_id": "tso", "overwrite": True}, (SHA, "tso", True)),
    ("post", "/drafts", "new_draft", {"profile_id": "tso"}, ("tso", None, False)),
    ("post", "/drafts", "new_draft", {"profile_id": "tso", "title": "T", "overwrite": True}, ("tso", "T", True)),
    ("get", "/drafts/tso", "get_draft", None, ("tso",)),
    ("put", "/drafts/tso", "save_draft", {"yaml": "title: x"}, ("tso", "title: x")),
    ("delete", "/drafts/tso", "delete_draft", None, ("tso",)),
    ("post", "/drafts/tso/confirm", "confirm", {"limit": "voltage_bands[0]"}, ("tso", "voltage_bands[0]")),
    ("post", "/drafts/tso/publish", "publish", {}, ("tso", False)),
    ("post", "/drafts/tso/publish", "publish", {"allow_unconfirmed": True}, ("tso", True)),
    ("get", "/published/tso", "get_published", None, ("tso",)),
    ("delete", "/published/tso", "delete_published", None, ("tso",)),
]
WRITES = [(m, p, b) for m, p, _f, b, _a in ROUTES if m != "get"] + [("post", "/documents", None)]


@pytest.mark.parametrize("method, path, function, payload, expected_args", ROUTES)
def test_each_route_calls_its_service_function_with_the_row(client, hub, monkeypatch, method, path, function,
                                                            payload, expected_args):
    calls = []
    monkeypatch.setattr(gc, function, lambda *a: calls.append(a) or {"ok": function})
    resp = getattr(client, method)(BASE + path, **({"json": payload} if payload is not None else {}))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": function}
    (args,) = calls
    project, *rest = args
    assert isinstance(project, Project) and project.name == "Code Hub"
    assert tuple(rest) == expected_args


def test_a_multipart_upload_reaches_the_service_with_its_bytes_name_and_type(client, hub, monkeypatch):
    calls = []
    monkeypatch.setattr(gc, "upload_document", lambda *a: calls.append(a) or {"ok": True})
    resp = client.post(BASE + "/documents", files={"file": ("tso.pdf", PDF, "application/pdf")})
    assert resp.status_code == 200, resp.text
    (_, data, filename, content_type), = calls
    assert data == PDF and filename == "tso.pdf" and content_type == "application/pdf"


def test_a_real_upload_is_stored_and_listed(client, hub):
    resp = client.post(BASE + "/documents", files={"file": ("tso.pdf", PDF, "application/pdf")})
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["pages"] == 2 and doc["filename"] == "tso.pdf"
    listing = client.get(BASE).json()
    assert listing["documents"] == [doc] and "eu_rfg_dcc_ce" in listing["shipped"]
    assert client.delete(f"{BASE}/documents/{doc['id']}").status_code == 200
    assert client.delete(f"{BASE}/documents/{doc['id']}").status_code == 404


@pytest.mark.parametrize("data, ctype, status", [
    (b"not a pdf at all", "application/pdf", 415),
    (PDF, "text/plain", 415),
    (b"%PDF-1.7 broken", "application/pdf", 422),
])
def test_a_refused_upload_answers_its_status(client, hub, data, ctype, status):
    resp = client.post(BASE + "/documents", files={"file": ("tso.pdf", data, ctype)})
    assert resp.status_code == status, resp.text


def test_an_upload_over_the_cap_is_413_before_the_service(client, hub, monkeypatch):
    monkeypatch.setattr(gc, "MAX_PDF_BYTES", 100)
    resp = client.post(BASE + "/documents", files={"file": ("tso.pdf", PDF, "application/pdf")})
    assert resp.status_code == 413


def test_an_upload_without_a_file_is_422(client, hub):
    assert client.post(BASE + "/documents").status_code == 422


def test_the_review_round_trip_over_http(client, hub, monkeypatch):
    doc = client.post(BASE + "/documents", files={"file": ("tso.pdf", PDF, "application/pdf")}).json()
    monkeypatch.setattr(chat_service, "_build_anthropic_client", lambda: (None, "missing_api_key"))
    resp = client.post(f"{BASE}/documents/{doc['id']}/extract", json={"profile_id": "tso"})
    assert resp.status_code == 503 and "ANTHROPIC_API_KEY" in resp.json()["detail"]
    fake = FakeClient(response(tool_input()))
    monkeypatch.setattr(chat_service, "_build_anthropic_client", lambda: (fake, None))
    resp = client.post(f"{BASE}/documents/{doc['id']}/extract", json={"profile_id": "tso"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["unconfirmed"] == ["voltage_bands[0]", "q_range_demand"]
    assert client.post(f"{BASE}/documents/{doc['id']}/extract", json={"profile_id": "tso"}).status_code == 409
    assert client.post(f"{BASE}/drafts/tso/publish", json={}).status_code == 409
    assert client.post(f"{BASE}/drafts/tso/confirm", json={"limit": "rvc_limit_pct"}).status_code == 422
    assert client.post(f"{BASE}/drafts/tso/confirm", json={}).status_code == 422
    for limit in ("voltage_bands[0]", "q_range_demand"):
        assert client.post(f"{BASE}/drafts/tso/confirm", json={"limit": limit}).status_code == 200
    resp = client.post(f"{BASE}/drafts/tso/publish", json={})
    assert resp.status_code == 200 and "tso" in resp.json()["profiles"]
    assert client.get("/api/campus-electrical/Code Hub").json()["profiles"]["tso"]
    assert client.get(f"{BASE}/published/tso").json()["unconfirmed"] == []
    assert client.put(f"{BASE}/drafts/tso", json={"yaml": "title: [x"}).status_code == 422
    assert client.get(f"{BASE}/drafts/nope").status_code == 404
    assert client.get(f"{BASE}/drafts/Bad-Id").status_code == 422
    assert client.post(f"{BASE}/drafts", json={"profile_id": "eu_rfg_dcc_ce"}).status_code == 422
    assert client.post(f"{BASE}/drafts", json={"profile_id": "by_hand"}).status_code == 200
    assert client.post(f"{BASE}/drafts", json={"profile_id": "by_hand"}).status_code == 409
    assert client.delete(f"{BASE}/published/tso").status_code == 200
    assert client.delete(f"{BASE}/drafts/tso").status_code == 200


def test_an_oversized_profile_edit_is_422_before_the_service(client, hub, monkeypatch):
    monkeypatch.setattr(gc, "save_draft", lambda *a: pytest.fail("the service must not be reached"))
    resp = client.put(f"{BASE}/drafts/tso", json={"yaml": "x" * (gc.MAX_PROFILE_BYTES + 1)})
    assert resp.status_code == 422


def test_every_write_is_refused_under_another_users_lock_and_a_read_is_not(client, hub, same_org_other_user,  # noqa: F811
                                                                          monkeypatch):
    for function in {f for _m, _p, f, _b, _a in ROUTES} | {"upload_document"}:
        monkeypatch.setattr(gc, function, lambda *a: {"ok": True})
    assert client.post("/api/projects/Code Hub/lock").status_code == 200
    other = same_org_other_user
    for method, path, payload in WRITES:
        kw = {"json": payload} if payload is not None else {}
        if path == "/documents":
            kw = {"files": {"file": ("tso.pdf", PDF, "application/pdf")}}
        resp = getattr(other, method)(BASE + path, **kw)
        assert _is_lock_refusal(resp), (method, path, resp.status_code, resp.text)
    assert other.get(BASE).status_code == 200
    assert other.get(BASE + "/drafts/tso").status_code == 200


def test_another_orgs_project_is_404_not_403(other_org_client, hub):
    for method, path, payload in [("get", "", None), ("post", "/drafts", {"profile_id": "x"}),
                                  ("post", f"/documents/{SHA}/extract", {}), ("get", "/drafts/x", None)]:
        kw = {"json": payload} if payload is not None else {}
        resp = getattr(other_org_client, method)(BASE + path, **kw)
        assert resp.status_code == 404, (path, resp.status_code)
