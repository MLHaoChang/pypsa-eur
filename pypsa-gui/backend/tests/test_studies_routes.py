"""
Decision-study routes (MVP-1 S1; review v1 B7, review v2 S3-v2 / S7-v2).

`/api/projects/{name}/studies[/{study_id}]`:

* every handler authorises the PATH project through `ProjectAccessDep`, so a
  non-member gets 404 — never 403, which would be an existence oracle;
* `{study_id}` resolves only inside that project's `studies/` directory, so a
  real study id addressed through another project is 404 (no IDOR);
* writes run the check-only foreign-lock test against the PATH project;
* the routes are exempt from the solver-in-flight middleware by one anchored
  pattern (BC-3): a study write never touches the resident network, so the
  active project solving must not 409 it — while every other `/api/projects/`
  write stays gated exactly as before;
* in auth (multi-user) mode the routes refuse unconditionally until
  OPEN-ITEMS 1 closes (BC-6). `PYPSAGUI_DECISION_STUDIES=1` enables them in
  local mode only; it is not an operator override. The functional tests below
  run in auth mode (that is where membership and locks exist), so they
  replace the refusal through FastAPI's `dependency_overrides` — the "in
  tests" door, which no deployment can open.
"""
from __future__ import annotations

import inspect
import threading
import uuid as _uuid
from datetime import datetime, UTC

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import local_mode
import main
from routers import studies as studies_router
from routers.deps import ProjectAccessDep
from tests.conftest import attach_session, build_network

BODY = {"question_id": "bess_site", "name": "Site A battery",
        "intake": {"site": {"connection_mw": 5.0}}}


@pytest.fixture
def studies_on(monkeypatch):
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    dep = studies_router.require_decision_studies_enabled
    main.app.dependency_overrides[dep] = lambda: None
    # The in-handler repeat of the same check (SB-3) looks the function up on
    # the module at call time; `Depends` captured the original above.
    monkeypatch.setattr(studies_router, "require_decision_studies_enabled", lambda: None)
    try:
        yield
    finally:
        main.app.dependency_overrides.pop(dep, None)


@pytest.fixture
def same_org_other_user(_auth_db, seeded_identity):
    """A second user in the SAME org — the realistic intruder for a lock test."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"colleague-{_uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=UTC),
        )
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        uid = u.id
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, uid)


def _create(client, name, body=BODY):
    r = client.post(f"/api/projects/{name}/studies/", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _is_lock_refusal(r) -> bool:
    if r.status_code != 409:
        return False
    detail = r.json().get("detail")
    return isinstance(detail, dict) and detail.get("error_kind") == "project_locked"


# ── create / read / list / patch / delete ────────────────────────────────

def test_create_returns_201_and_writes_the_sidecar(
        client, api_project, studies_on, project_row, project_storage_dir):
    name = api_project("st-create")
    s = _create(client, name)
    assert len(s["study_id"]) == 32
    assert s["name"] == BODY["name"]
    assert s["question_id"] == "bess_site"
    assert s["base_project"] == str(project_row(name).id)
    assert s["intake"] == BODY["intake"]
    assert s["perspective"] == "site_owner"
    assert s["created_by"] is not None
    sidecar = project_storage_dir(name) / "studies" / f"{s['study_id']}.json"
    assert sidecar.is_file()


def test_read_and_list(client, api_project, studies_on):
    name = api_project("st-read")
    s = _create(client, name)
    r = client.get(f"/api/projects/{name}/studies/{s['study_id']}")
    assert r.status_code == 200, r.text
    assert r.json() == s
    r = client.get(f"/api/projects/{name}/studies/")
    assert r.status_code == 200, r.text
    assert [x["study_id"] for x in r.json()] == [s["study_id"]]


def test_patch_applies_one_step_and_persists(client, api_project, studies_on):
    name = api_project("st-patch")
    s = _create(client, name)
    url = f"/api/projects/{name}/studies/{s['study_id']}"
    r = client.patch(url, json={"step": "load",
                                "intake": {"load": {"source": "upload",
                                                    "file_id": "f1"}}})
    assert r.status_code == 200, r.text
    # Per-step apply merges the step's answers; earlier steps survive.
    assert r.json()["intake"] == {"site": {"connection_mw": 5.0},
                                  "load": {"source": "upload", "file_id": "f1"}}
    r = client.patch(url, json={"name": "Renamed",
                                "settings": {"currency_year": 2026}})
    assert r.status_code == 200, r.text
    got = client.get(url).json()
    assert got["name"] == "Renamed"
    assert got["currency_year"] == 2026
    assert got["intake"]["load"]["file_id"] == "f1"
    assert got["updated_at"] >= s["updated_at"]
    assert got["created_at"] == s["created_at"]


def test_patch_refuses_what_mvp1_cannot_honour(client, api_project, studies_on):
    """
    A basis or perspective nothing downstream implements is refused, not
    stored and silently ignored (spec decision 7: mixing bases is a defect).
    """
    name = api_project("st-patch-scope")
    s = _create(client, name)
    url = f"/api/projects/{name}/studies/{s['study_id']}"
    for bad in ({"settings": {"basis": {"terms": "nominal", "tax": "pre",
                                        "subsidy": "excl"}}},
                {"settings": {"perspective": "investor"}},
                {"study_id": "0" * 32},
                {"option_projects": ["x"]}):
        r = client.patch(url, json=bad)
        assert r.status_code == 422, (bad, r.status_code, r.text)
    assert client.get(url).json() == s


def test_delete_removes_the_study(client, api_project, studies_on,
                                  project_storage_dir):
    name = api_project("st-delete")
    s = _create(client, name)
    url = f"/api/projects/{name}/studies/{s['study_id']}"
    r = client.delete(url)
    assert r.status_code == 204, r.text
    assert client.get(url).status_code == 404
    assert not (project_storage_dir(name) / "studies"
                / f"{s['study_id']}.json").exists()


# ── authorisation: 404, never 403 ────────────────────────────────────────

def test_non_member_gets_404_on_every_route(client, other_org_client,
                                            api_project, studies_on,
                                            project_storage_dir):
    name = api_project("st-tenant")
    s = _create(client, name)
    base = f"/api/projects/{name}/studies"
    item = f"{base}/{s['study_id']}"
    sidecar = project_storage_dir(name) / "studies" / f"{s['study_id']}.json"
    before = sidecar.read_bytes()
    for r in (other_org_client.get(f"{base}/"),
              other_org_client.post(f"{base}/", json=BODY),
              other_org_client.get(item),
              other_org_client.patch(item, json={"name": "pwned"}),
              other_org_client.delete(item)):
        assert r.status_code == 404, (r.request.method, r.request.url,
                                      r.status_code, r.text)
    assert sidecar.read_bytes() == before


def test_a_foreign_study_id_is_404(client, api_project, studies_on,
                                   project_storage_dir):
    """
    A REAL study id from project A, addressed through project B that the
    same caller may see, resolves to nothing: the id is looked up only inside
    B's directory.
    """
    a = api_project("st-idor-a")
    b = api_project("st-idor-b")
    s = _create(client, a)
    item_b = f"/api/projects/{b}/studies/{s['study_id']}"
    assert client.get(item_b).status_code == 404
    assert client.patch(item_b, json={"name": "x"}).status_code == 404
    assert client.delete(item_b).status_code == 404
    # A's study is untouched.
    assert client.get(f"/api/projects/{a}/studies/{s['study_id']}").json() == s
    # Unknown and malformed ids are 404 too.
    # (A `..` segment is not tried: the HTTP client normalises it away before
    # the request leaves, so it would test httpx, not this router.)
    for sid in (_uuid.uuid4().hex, "not-an-id", "network.nc"):
        assert client.get(f"/api/projects/{a}/studies/{sid}").status_code == 404


# ── foreign lock ──────────────────────────────────────────────────────────

def test_writes_are_refused_under_a_foreign_lock(client, api_project,
                                                 studies_on,
                                                 same_org_other_user):
    name = api_project("st-lock")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    s = _create(client, name)  # the holder may write
    item = f"/api/projects/{name}/studies/{s['study_id']}"

    b = same_org_other_user
    assert _is_lock_refusal(b.post(f"/api/projects/{name}/studies/", json=BODY))
    assert _is_lock_refusal(b.patch(item, json={"name": "intruder"}))
    assert _is_lock_refusal(b.delete(item))
    # Reading is not writing.
    assert b.get(item).status_code == 200
    assert client.get(item).json()["name"] == BODY["name"]


def test_a_write_to_a_free_project_does_not_take_the_lock(
        client, api_project, studies_on, same_org_other_user):
    name = api_project("st-lock-free")
    assert client.delete(f"/api/projects/{name}/lock").status_code in (200, 204)
    r = same_org_other_user.post(f"/api/projects/{name}/studies/", json=BODY)
    assert r.status_code == 201, r.text
    assert client.post(f"/api/projects/{name}/lock").status_code == 200


# ── solver-in-flight gate (BC-3) ─────────────────────────────────────────

def test_study_patch_succeeds_while_the_active_project_is_solving(
        client, api_project, studies_on, session_ctx):
    name = api_project("st-solving")
    s = _create(client, name)
    item = f"/api/projects/{name}/studies/{s['study_id']}"

    started, release = threading.Event(), threading.Event()

    def _spin():
        started.set()
        release.wait(timeout=10)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    started.wait(timeout=5)
    session_ctx(client).solver_state["thread"] = t
    try:
        # Control: an ordinary /api/projects/ write IS gated right now, so the
        # assertion below is about the exemption, not an unarmed gate.
        ctl = client.put(f"/api/projects/{name}/worksheet",
                         json={"manual_rows": [], "overlays": {}})
        assert ctl.status_code == 409, ctl.text
        assert ctl.json().get("code") == "solver_in_flight"

        r = client.patch(item, json={"step": "tariff",
                                     "intake": {"tariff": {"seed": "ci"}}})
        assert r.status_code == 200, r.text
        assert r.json()["intake"]["tariff"] == {"seed": "ci"}
    finally:
        release.set()
        t.join(timeout=5)
        session_ctx(client).solver_state["thread"] = None


@pytest.mark.parametrize("path,exempt", [
    ("/api/projects/p/studies", True),
    ("/api/projects/p/studies/", True),
    ("/api/projects/p/studies/0123456789abcdef0123456789abcdef", True),
    ("/api/projects/p/studiesx", False),
    ("/api/projects/p/x/studies", False),
    ("/api/projects/studies", False),
    ("/api/projects/p", False),
    ("/api/projects/p/worksheet", False),
    ("/api/network/p/studies", False),
    # Pre-existing behaviour, unchanged.
    ("/api/projects/p/activate", True),
])
def test_solver_blocking_exemption_is_anchored(path, exempt):
    assert main._solver_blocking_exempt(path) is exempt


def test_neither_prefix_list_names_the_study_routes():
    assert "/api/projects/" in main._SOLVER_BLOCKING_PREFIXES
    assert not any("studies" in p for p in main._SOLVER_BLOCKING_PREFIXES)
    assert not any("studies" in p for p in main._FOREIGN_LOCK_GATE_PREFIXES)


# ── route inventory ──────────────────────────────────────────────────────

def test_every_study_handler_declares_project_access_dep():
    routes = [r for r in studies_router.router.routes if isinstance(r, APIRoute)]
    assert len(routes) >= 5, [r.path for r in routes]
    missing = [
        f"{sorted(r.methods)} {r.path} -> {r.endpoint.__name__}"
        for r in routes
        if not any(p.default is ProjectAccessDep
                   for p in inspect.signature(r.endpoint).parameters.values())
    ]
    assert not missing, f"handlers without ProjectAccessDep: {missing}"


def _effective_routes(route):
    """
    Every route under `route`, through this FastAPI's included-router
    wrappers (see `tests/test_network_time_axis_surface.py::_iter_route_paths`
    for why the `effective_candidates` property is the one to read).
    """
    yield route
    children = list(getattr(route, "routes", None) or [])
    candidates = getattr(route, "effective_candidates", None)
    if candidates is not None:
        children += list(candidates() if callable(candidates) else candidates)
    for child in children:
        yield from _effective_routes(child)


def test_study_router_is_mounted_under_the_project_path_with_the_fs_guard():
    from routers.deps import require_project_access
    from services import fs_permission

    mounted = []
    for top in main.app.routes:
        for r in _effective_routes(top):
            path = getattr(r, "path_format", None) or getattr(r, "path", "")
            if (isinstance(path, str)
                    and path.startswith("/api/projects/{name}/studies")
                    and getattr(r, "dependant", None) is not None):
                mounted.append((path, [d.call for d in r.dependant.dependencies]))
    assert len(mounted) >= 5, mounted
    gate = studies_router.require_decision_studies_enabled
    for path, calls in mounted:
        assert fs_permission.require_file_access in calls, path
        # The auth-mode refusal runs BEFORE the project is resolved, so it
        # cannot answer differently for a name that exists.
        assert calls.index(gate) < calls.index(require_project_access), path


# ── auth-mode refusal (BC-6, OPEN-ITEMS 1) ───────────────────────────────

def _all_routes(client, name):
    base = f"/api/projects/{name}/studies"
    item = f"{base}/{_uuid.uuid4().hex}"
    return (client.get(f"{base}/"), client.post(f"{base}/", json=BODY),
            client.get(item), client.patch(item, json={"name": "x"}),
            client.delete(item))


@pytest.mark.parametrize("flag", [None, "1"])
def test_auth_mode_refuses_unconditionally(client, api_project, monkeypatch,
                                           project_storage_dir, flag):
    if flag is None:
        monkeypatch.delenv("PYPSAGUI_DECISION_STUDIES", raising=False)
    else:
        monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", flag)
    assert not local_mode.is_local_mode()
    name = api_project(f"st-auth-{flag or 'unset'}")
    for r in _all_routes(client, name):
        assert r.status_code == 404, (r.request.method, r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["code"] == "decision_studies_unavailable"
        assert "OPEN-ITEMS 1" in detail["message"]
    assert not (project_storage_dir(name) / "studies").exists()


def test_auth_mode_refusal_is_not_an_existence_oracle(client, monkeypatch):
    """
    The refusal fires before project resolution: a name that exists
    nowhere reads exactly like one that exists.
    """
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    r = client.get("/api/projects/no-such-project-anywhere/studies/")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "decision_studies_unavailable"


@pytest.fixture
def local_client(_auth_db, monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


def _local_project(local_client, install_network, name):
    install_network(build_network(), name=name)
    r = local_client.post(f"/api/projects/{name}",
                          params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    return name


def test_local_mode_with_the_flag_serves_the_routes(local_client,
                                                    install_network,
                                                    monkeypatch):
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    name = _local_project(local_client, install_network, "st-local-on")
    r = local_client.post(f"/api/projects/{name}/studies/", json=BODY)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert local_client.get(
        f"/api/projects/{name}/studies/{sid}").status_code == 200


def test_local_mode_without_the_flag_refuses(local_client, install_network,
                                             monkeypatch):
    monkeypatch.delenv("PYPSAGUI_DECISION_STUDIES", raising=False)
    name = _local_project(local_client, install_network, "st-local-off")
    for r in _all_routes(local_client, name):
        assert r.status_code == 404, (r.request.method, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "decision_studies_disabled"


# ── Gate S1 binding conditions ─────────────────────────────────────────────

def test_a_handler_called_as_a_plain_function_still_refuses_in_auth_mode(monkeypatch):
    """
    SB-3. Router dependencies protect HTTP only; a handler called directly
    (a chat tool, a script) skips them. The refusal is repeated inside every
    handler, before any project or study is touched.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException

    monkeypatch.setattr(local_mode, "is_local_mode", lambda: False)
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    project = SimpleNamespace(uuid=str(_uuid.uuid4()), name="p", directory=None)
    calls = {
        "list": lambda: studies_router.list_studies(project=project),
        "get": lambda: studies_router.get_study("x", project=project),
        "create": lambda: studies_router.create_study(
            studies_router.StudyCreate(**BODY), project=project, db=None, user=None),
        "patch": lambda: studies_router.patch_study(
            "x", studies_router.StudyPatch(name="n"), project=project, db=None, user=None),
        "delete": lambda: studies_router.delete_study("x", project=project, db=None, user=None),
    }
    for label, call in calls.items():
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404, label
        assert exc.value.detail["code"] == "decision_studies_unavailable", label


def test_a_copied_record_drops_its_origin_fork_references(
        client, api_project, studies_on, project_storage_dir):
    """
    SB-4. `option_projects` rides along with every copy of the sidecar
    (Save-As, scenarios, snapshots, bundle import). A record whose stored
    `base_project` is not the containing project is a copy, and a copy owns
    no forks: the routes answer with the containing project and an empty
    fork list.
    """
    from models.study import DecisionStudy
    from services.study import store

    name = api_project("copied")
    now = datetime.now(tz=UTC)
    s = DecisionStudy(study_id=store.new_study_id(), name="Origin's study",
                      question_id="bess_site", base_project="not-this-project",
                      option_projects=["fork-a", "fork-b"],
                      created_at=now, updated_at=now)
    store.save_study(project_storage_dir(name), s)
    r = client.get(f"/api/projects/{name}/studies/{s.study_id}")
    assert r.status_code == 200, r.text
    assert r.json()["option_projects"] == []
    assert r.json()["base_project"] != "not-this-project"
    listed = client.get(f"/api/projects/{name}/studies/").json()
    assert [x["option_projects"] for x in listed if x["study_id"] == s.study_id] == [[]]
