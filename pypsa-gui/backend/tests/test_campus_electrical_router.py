"""`/api/campus-electrical` is a wrapper layer (plan C6).

These tests check two things:
* each route calls its ONE service function, with the project row and the
  request's arguments;
* authorization behaves like every other project-scoped router: another
  org's project is a 404, never a 403.

What the service does is ``test_campus_electrical_service.py``'s business.
"""
import pytest

from db.models import Project, User
from services import project_registry
from services import campus_electrical_service as ce


@pytest.fixture
def hub(client, _auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project_registry.create_root(db, user, "Router Hub")


@pytest.mark.parametrize("method, path, function, payload, expected_args", [
    ("get", "", "get_state", None, ()),
    ("post", "/draft", "draft", {"overwrite": True}, (True,)),
    ("put", "/campus", "save_campus", {"yaml": "campus: {}"}, ("campus: {}",)),
    ("post", "/run", "run", {"k": 2, "pf": 0.95}, ({"k": 2, "pf": 0.95, "profile": "eu_rfg_dcc_ce",
                                                    "margin": 0.2, "n_minus_1": True},)),
])
def test_each_route_calls_its_service_function_with_the_row(client, hub, monkeypatch, method, path, function,
                                                            payload, expected_args):
    calls = []

    def fake(*args):
        calls.append(args)
        return {"ok": function}

    monkeypatch.setattr(ce, function, fake)
    resp = getattr(client, method)(f"/api/campus-electrical/Router Hub{path}",
                                   **({"json": payload} if payload is not None else {}))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": function}
    assert len(calls) == 1
    project, *rest = calls[0]
    assert isinstance(project, Project) and project.name == "Router Hub"
    assert tuple(rest) == expected_args


@pytest.mark.parametrize("body", [{"k": 0}, {"pf": 1.5}, {"margin": -1}, {"profile": ""}])
def test_bad_run_settings_are_422_before_the_service(client, hub, monkeypatch, body):
    monkeypatch.setattr(ce, "run", lambda *a: pytest.fail("the service must not be reached"))
    resp = client.post("/api/campus-electrical/Router Hub/run", json=body)
    assert resp.status_code == 422, resp.text


def test_another_orgs_project_is_404_not_403(other_org_client, hub):
    for method, path in (("get", ""), ("post", "/draft"), ("post", "/run")):
        kw = {"json": {}} if method == "post" else {}
        resp = getattr(other_org_client, method)(f"/api/campus-electrical/Router Hub{path}", **kw)
        assert resp.status_code == 404, (path, resp.status_code)
