"""`/api/campus-electrical` is a wrapper layer (plan C6).

These tests check two things:
* each route calls its ONE service function, with the project row and the
  request's arguments;
* authorization behaves like every other project-scoped router: another
  org's project is a 404, never a 403.

What the service does is ``test_campus_electrical_service.py``'s business.
"""
import re

import pytest

from db.models import Project, User
from services import project_registry
from services import campus_electrical_service as ce
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401


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
                                                    "margin": 0.2, "n_minus_1": True, "invest": True,
                                                    "pcc_switchgear_by_operator": False},)),
    ("post", "/run", "run", {"invest": False}, ({"k": 3, "pf": None, "profile": "eu_rfg_dcc_ce",
                                                  "margin": 0.2, "n_minus_1": True, "invest": False,
                                                  "pcc_switchgear_by_operator": False},)),
    ("post", "/run", "run", {"pcc_switchgear_by_operator": True}, ({"k": 3, "pf": None, "profile": "eu_rfg_dcc_ce",
                                                                   "margin": 0.2, "n_minus_1": True, "invest": True,
                                                                   "pcc_switchgear_by_operator": True},)),
    ("get", "/library", "get_library", None, ()),
    ("put", "/library", "save_library", {"yaml": "discount_rate: {}"}, ("discount_rate: {}",)),
    ("post", "/library/reset", "reset_library", None, ()),
    ("get", "/owner-assets", "owner_assets_response", None, ()),
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


def test_an_oversized_library_is_413_from_the_service_not_a_422_from_the_model(client, hub):
    from services import campus_electrical_service as real
    resp = client.put("/api/campus-electrical/Router Hub/library", json={"yaml": "x" * (real.MAX_LIBRARY_BYTES + 1)})
    assert resp.status_code == 413, resp.text


def test_the_library_round_trips_over_http_and_a_bad_one_is_422_naming_the_field(client, hub):
    base = "/api/campus-electrical/Router Hub/library"
    default = client.get(base).json()
    assert default["is_default"] is True and "transformers:" in default["yaml"]
    bad = re.sub(r"^(\s+)opex_frac:", r"\1opex_fraction:", default["yaml"], count=1, flags=re.M)
    assert bad != default["yaml"]
    resp = client.put(base, json={"yaml": bad})
    assert resp.status_code == 422 and "opex_fraction" in resp.json()["detail"], resp.text
    assert client.put(base, json={"yaml": default["yaml"] + "\n# mine\n"}).json()["is_default"] is False
    assert client.get(base).json()["yaml"].endswith("# mine\n")
    assert client.post(base + "/reset").json() == default
    assert client.put(base, json={}).status_code == 422


def test_every_write_is_refused_under_another_users_lock_and_a_read_is_not(client, hub, same_org_other_user,  # noqa: F811
                                                                          monkeypatch):
    for function in ("save_library", "reset_library", "run", "draft", "save_campus", "get_library"):
        monkeypatch.setattr(ce, function, lambda *a: {"ok": True})
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    other = same_org_other_user
    for method, path, payload in [("put", "/library", {"yaml": "x"}), ("post", "/library/reset", None),
                                  ("post", "/run", {}), ("post", "/draft", {}), ("put", "/campus", {"yaml": "x"})]:
        kw = {"json": payload} if payload is not None else {}
        resp = getattr(other, method)(f"/api/campus-electrical/Router Hub{path}", **kw)
        assert _is_lock_refusal(resp), (method, path, resp.status_code, resp.text)
    assert other.get("/api/campus-electrical/Router Hub/library").status_code == 200


def test_another_orgs_project_is_404_not_403(other_org_client, hub):
    for method, path in (("get", ""), ("post", "/draft"), ("post", "/run"), ("get", "/library"),
                         ("put", "/library"), ("post", "/library/reset"), ("get", "/owner-assets")):
        kw = {"json": {}} if method in ("post", "put") else {}
        resp = getattr(other_org_client, method)(f"/api/campus-electrical/Router Hub{path}", **kw)
        assert resp.status_code == 404, (path, resp.status_code)


def test_the_owner_assets_are_a_read_that_a_lock_does_not_refuse_and_a_write_verb_cannot_reach(
        client, hub, same_org_other_user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ce, "owner_assets_response", lambda *a: {"assets": [], "source_hash": None, "stale": False})
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    base = "/api/campus-electrical/Router Hub/owner-assets"
    assert same_org_other_user.get(base).status_code == 200
    assert client.get(base).json() == {"assets": [], "source_hash": None, "stale": False}
    for verb in ("post", "put", "delete"):
        assert getattr(client, verb)(base).status_code == 405, verb


def test_the_owner_assets_of_a_project_with_no_run_are_empty_with_no_hash_over_http(client, hub):
    resp = client.get("/api/campus-electrical/Router Hub/owner-assets")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"assets": [], "source_hash": None, "stale": False}


# ── the joint optimisation as a background job (plan C12) ──────────────────

RUNNING = {"state": "running", "iteration": None, "max_iter": None, "best_cost": None, "c8_cost": 1.0,
           "message": "the least-cost run first"}


def test_the_milp_routes_answer_202_200_and_200_and_call_their_service_functions(client, hub, monkeypatch):
    calls = []
    monkeypatch.setattr(ce, "start_milp_job", lambda p, s: calls.append(("start", p.name, s)) or RUNNING)
    monkeypatch.setattr(ce, "milp_job_status", lambda p: calls.append(("status", p.name)) or RUNNING)
    monkeypatch.setattr(ce, "cancel_milp_job",
                        lambda p: calls.append(("cancel", p.name)) or {"state": "running", "cancelling": True})
    base = "/api/campus-electrical/Router Hub/milp"
    resp = client.post(base, json={"k": 2, "pf": 0.95})
    assert resp.status_code == 202 and resp.json() == RUNNING, resp.text
    resp = client.get(base)
    assert resp.status_code == 200 and resp.json() == RUNNING
    resp = client.post(base + "/cancel")
    assert resp.status_code == 200 and resp.json() == {"state": "running", "cancelling": True}
    assert calls == [("start", "Router Hub", {"k": 2, "pf": 0.95, "profile": "eu_rfg_dcc_ce", "margin": 0.2,
                                              "n_minus_1": True, "invest": True, "pcc_switchgear_by_operator": False}),
                     ("status", "Router Hub"), ("cancel", "Router Hub")]


def test_the_milp_status_of_a_project_that_never_ran_it_is_null_and_cancel_is_404(client, hub):
    base = "/api/campus-electrical/Router Hub/milp"
    resp = client.get(base)
    assert resp.status_code == 200 and resp.json() is None
    assert client.post(base + "/cancel").status_code == 404


def test_a_second_milp_job_is_409_and_bad_settings_are_422_before_the_service(client, hub, monkeypatch):
    from fastapi import HTTPException

    def busy(p, s):
        raise HTTPException(status_code=409, detail="a joint optimisation (MILP) is already running")

    monkeypatch.setattr(ce, "start_milp_job", busy)
    base = "/api/campus-electrical/Router Hub/milp"
    assert client.post(base, json={}).status_code == 409
    monkeypatch.setattr(ce, "start_milp_job", lambda *a: pytest.fail("the service must not be reached"))
    assert client.post(base, json={"k": 0}).status_code == 422


def test_starting_and_cancelling_the_milp_are_refused_under_another_users_lock_and_the_status_is_not(
        client, hub, same_org_other_user, monkeypatch):  # noqa: F811
    for function in ("start_milp_job", "cancel_milp_job", "milp_job_status"):
        monkeypatch.setattr(ce, function, lambda *a: {"ok": True})
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    base = "/api/campus-electrical/Router Hub/milp"
    assert _is_lock_refusal(same_org_other_user.post(base, json={}))
    assert _is_lock_refusal(same_org_other_user.post(base + "/cancel"))
    assert same_org_other_user.get(base).status_code == 200
    assert client.post(base, json={}).status_code == 202                      # the lock holder may


def test_another_orgs_project_cannot_reach_the_milp_routes(other_org_client, hub):
    base = "/api/campus-electrical/Router Hub/milp"
    for method, path in (("post", ""), ("get", ""), ("post", "/cancel")):
        kw = {"json": {}} if method == "post" else {}
        assert getattr(other_org_client, method)(base + path, **kw).status_code == 404, path
