"""
STUDY-GATE-1: a study start refuses an unlocated BASE project with 422 typed
`site_location_missing`, on the base's own network and site anchor (not the
active project's), before any run record, ownership mark or thread exists.

Covers the four study routes (`POST .../run`, `/findings/tornado`,
`/supply-architectures`, `/system-type-map`) and the chat sweep
(`workflow_tools.run_sensitivity_sweep`). The solver is the fakes of the
existing study tests; the gate itself is the real predicate.
"""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from services import chat_tools, project_registry
from services import workflow_tools as wt
from services.pypsa_service import PyPSAService
from services.site_scene import anchor as site_anchor
from services.study import runner as R
from tests.study_s4_support import INTAKE, create_pack_study, enable_studies, wait_run
from tests.test_homer_supply_architecture_routes import ArchSolver  # noqa: F401
from tests.test_homer_supply_architecture_routes import _project as _sa_project
from tests.test_homer_supply_architecture_routes import _url as _sa_url
from tests.test_homer_system_type_map_routes import _body as _tm_body
from tests.test_homer_system_type_map_routes import _url as _tm_url
from tests.test_study_runner import FakeSolver
from tests.test_study_tornado_routes import NO_PV_TOU
from tests.test_workflow_tools import baseline, case, helpers_state  # noqa: F401

CODE = "site_location_missing"


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


@pytest.fixture
def arch_solver(monkeypatch):
    from services import solver_service

    solver = ArchSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


# ── helpers ──────────────────────────────────────────────────────────────


def _put_anchor(client, name, confirmed: bool):
    rev = client.get(f"/api/projects/{name}/site-anchor").json()["revision"]
    r = client.put(f"/api/projects/{name}/site-anchor", json={
        "anchor": {"lat": 52.52, "lng": 13.4, "source": "manual", "confirmed": confirmed},
        "expected_revision": rev})
    assert r.status_code == 200, r.text


def _drop_anchor(client, name):
    """The project with no anchor file at all."""
    with chat_tools._acting() as (db, user):
        path = project_registry.project_dir(project_registry.resolve_project(db, user, name))
    (path / site_anchor.ANCHOR_FILE).unlink(missing_ok=True)


def _assert_refused(r):
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == d["error_kind"] == CODE and d["field"] == "x", d


def _not_the_location_code(r):
    detail = r.json().get("detail") if r.status_code == 422 else None
    assert not (isinstance(detail, dict) and detail.get("code") == CODE), r.text


def _threads():
    return sorted(t.name for t in threading.enumerate())


def _owned():
    return set(PyPSAService._study_owned)


class Started:
    """What a refused start must leave untouched: the study-owned marks, the
    worker threads and the base context's study slot."""

    def __init__(self, base):
        self.base, self.threads, self.owned = base, _threads(), _owned()

    def nothing(self, get_url=None, client=None):
        assert _owned() == self.owned, "a study-owned mark was left"
        assert _threads() == self.threads, "a worker thread started"
        with chat_tools._acting() as (db, user):
            row = project_registry.resolve_project(db, user, self.base)
            ctx = PyPSAService.get_context(project_registry.registry_key(row))
        if ctx is not None:
            assert not ctx.solver_state.get(R.STUDY_KEY), "a run record exists"
        if get_url is not None:
            r = client.get(get_url)
            assert r.status_code == 404, ("a run record is readable", r.text)


# ── per-start setups: (base, study id, POST, GET url) ───────────────────


def _run_setup(client, api_project, name="gate-run"):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name, intake=NO_PV_TOU)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    return name, sid, lambda: client.post(f"/api/projects/{name}/studies/{sid}/run", json={}), \
        f"/api/projects/{name}/studies/{sid}/run"


def _tornado_setup(client, api_project, name="gate-torn"):
    """A study with a finished run, made while located; the location is then
    taken away so that only the tornado start is under test."""
    from services import site_location

    base, sid, _post, _get = _run_setup(client, api_project, name)
    original = site_location.anchor_confirmed
    site_location.anchor_confirmed = lambda _d: True  # the run is not under test
    try:
        assert client.post(f"/api/projects/{base}/studies/{sid}/run", json={}).status_code == 202
        assert wait_run(client, base, sid)["status"] == "done"
    finally:
        site_location.anchor_confirmed = original
    return base, sid, \
        lambda: client.post(f"/api/projects/{base}/studies/{sid}/findings/tornado"), \
        f"/api/projects/{base}/studies/{sid}/findings/tornado"


def _sa_setup(client, install_network, name="gate-sa"):
    sid = _sa_project(client, install_network, name)
    return name, sid, lambda: client.post(_sa_url(name, sid), json={}), _sa_url(name, sid)


def _tm_setup(client, install_network, name="gate-tm"):
    sid = _sa_project(client, install_network, name)
    return name, sid, lambda: client.post(_tm_url(name, sid), json=_tm_body()), _tm_url(name, sid)


@pytest.fixture(params=["run", "tornado", "supply_architectures", "system_type_map"])
def start(request, client, api_project, install_network, studies_on):
    """The study start under test, with its base project unlocated. Each kind
    takes its own solver fake (both replace `run_simulation`)."""
    kind = request.param
    request.getfixturevalue("fake" if kind in ("run", "tornado") else "arch_solver")
    if kind == "run":
        return _run_setup(client, api_project)
    if kind == "tornado":
        return _tornado_setup(client, api_project)
    if kind == "supply_architectures":
        return _sa_setup(client, install_network)
    return _tm_setup(client, install_network)


# ── the gate on each study start ────────────────────────────────────────


@pytest.mark.unlocated
def test_an_unlocated_base_refuses_the_study_start_and_starts_nothing(client, start):
    base, _sid, post, get_url = start
    before = Started(base)
    _assert_refused(post())
    before.nothing(get_url if "tornado" not in get_url else None, client)


@pytest.mark.unlocated
def test_a_draft_anchor_does_not_locate_the_base(client, start):
    base, _sid, post, get_url = start
    _put_anchor(client, base, False)
    before = Started(base)
    _assert_refused(post())
    before.nothing(get_url if "tornado" not in get_url else None, client)


@pytest.mark.unlocated
def test_a_confirmed_anchor_locates_the_base_and_the_start_is_not_refused_for_it(client, start):
    base, _sid, post, _get = start
    _put_anchor(client, base, True)
    r = post()
    _not_the_location_code(r)
    assert r.status_code == 202, r.text


# ── the base's location, not the active project's ───────────────────────


@pytest.mark.unlocated
def test_a_located_active_project_does_not_locate_an_unlocated_base(
        client, api_project, studies_on, fake):
    base, sid, post, get_url = _run_setup(client, api_project)
    _put_anchor(client, f"{base}-src", True)  # the active project is located
    before = Started(base)
    _assert_refused(post())
    before.nothing(get_url, client)


@pytest.mark.unlocated
def test_an_unlocated_active_project_does_not_refuse_a_located_base(
        client, api_project, studies_on, fake):
    base, sid, post, _get = _run_setup(client, api_project)
    _drop_anchor(client, f"{base}-src")  # the active project is unlocated
    _put_anchor(client, base, True)
    r = post()
    _not_the_location_code(r)
    assert r.status_code == 202, r.text


# ── the chat tools ──────────────────────────────────────────────────────


@pytest.mark.unlocated
def test_the_chat_comparison_start_maps_the_refusal_to_its_error_kind(
        client, install_network, studies_on, arch_solver):
    base, sid, _post, get_url = _sa_setup(client, install_network, "gate-sa-chat")
    before = Started(base)
    with pytest.raises(HTTPException) as exc:
        chat_tools.compare_supply_architectures(base, sid)
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == CODE and exc.value.detail["field"] == "x"
    before.nothing(get_url, client)


@pytest.mark.unlocated
def test_the_sensitivity_sweep_refuses_an_unlocated_baseline_and_creates_nothing(
        client, baseline):
    _ctx, project_id, _path = baseline
    jobs_before, owned = _projects(), _owned()
    with pytest.raises(HTTPException) as exc:
        wt.run_sensitivity_sweep(project_id, [case()])
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == exc.value.detail["error_kind"] == CODE
    assert exc.value.detail["field"] == "x"
    assert _projects() == jobs_before and _owned() == owned  # no case project was forked


@pytest.mark.unlocated
def test_a_draft_anchor_does_not_locate_the_sweep_baseline(client, baseline):
    _ctx, project_id, _path = baseline
    _put_anchor(client, "Tool Baseline", False)
    with pytest.raises(HTTPException) as exc:
        wt.run_sensitivity_sweep(project_id, [case()])
    assert exc.value.status_code == 422 and exc.value.detail["error_kind"] == CODE


@pytest.mark.unlocated
def test_a_confirmed_anchor_locates_the_sweep_baseline(client, baseline):
    _ctx, project_id, _path = baseline
    _put_anchor(client, "Tool Baseline", True)
    out = wt.run_sensitivity_sweep(project_id, [case()])
    assert out["status"] == "submitted", out


def _projects():
    with chat_tools._acting() as (db, user):
        from sqlalchemy import select

        from db.models import Project

        return sorted(p.name for p in db.scalars(select(Project)).all())
