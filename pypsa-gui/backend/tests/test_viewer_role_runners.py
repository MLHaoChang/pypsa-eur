"""Project-viewer-role 02: the in-process runners read the requester's role
again before they write, so an editor made a viewer, or removed, mid-run gets
a ``project_view_only`` failure and nothing saved.

The gate itself (`project_write_gate.requester_may_not_write`) is tested
against real memberships. Each runner test starts a run as the signed-in
admin, then flips the gate's answer during the solve (an admin always edits,
so the demotion is the gate's answer), and checks the call names the run's
requester and project.
"""

from __future__ import annotations

import uuid

import pytest

from db.models import Project, ProjectMembership, User
from services import project_write_gate
from services.project_write_gate import KIND
from services.pypsa_service import PyPSAService
from tests.study_s4_support import OPTIONS, create_pack_study, enable_studies, wait_run
from tests.test_homer_supply_architecture_routes import (  # noqa: F401 (fixtures)
    _project as _sa_project,
)
from tests.test_homer_supply_architecture_routes import (  # noqa: F401 (fixtures)
    _url as _sa_url,
)
from tests.test_homer_supply_architecture_routes import (  # noqa: F401 (fixtures)
    _var_rows,
    _wait as _sa_wait,
    arch_solver,
    session_local,
)
from tests.test_study_runner import FakeSolver
from tests.test_study_tornado_routes import _run as _study_done
from tests.test_study_tornado_routes import _variant_rows, wait_tornado
from tests.test_viewer_role_gate import (  # noqa: F401 (fixtures)
    _set_role,
    baseline,
    helpers_state,
    world,
)


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


class Demotion:
    """Stands in for `requester_may_not_write`: False until `demote()`."""

    def __init__(self) -> None:
        self.demoted = False
        self.calls: list[tuple[str, str]] = []

    def demote(self, *_a) -> None:
        self.demoted = True

    def __call__(self, user_id, project_id) -> bool:
        self.calls.append((str(user_id), str(project_id)))
        return self.demoted


@pytest.fixture
def demotion(monkeypatch):
    d = Demotion()
    monkeypatch.setattr(project_write_gate, "requester_may_not_write", d)
    return d


def _a_user(session_local_, user_id) -> bool:
    with session_local_() as db:
        return db.get(User, uuid.UUID(user_id)) is not None


def _project_id(session_local_, name) -> str:
    with session_local_() as db:
        return str(db.query(Project).filter_by(name=name).one().id)


def _assert_requester(demotion, session_local_, name) -> None:
    """The runner's checks named the signed-in requester and the run's base
    project (the queue's own check of each fork's solve job names the fork)."""
    pid = _project_id(session_local_, name)
    users = {u for u, p in demotion.calls if p == pid}
    assert len(users) == 1 and _a_user(session_local_, users.pop())


# ── the gate: fresh from the database, a lost membership fails closed ────────


def test_the_requesters_role_is_read_fresh_each_call(world):
    sl, project, editor = world["session_local"], world["project"], world["editor"]
    assert project_write_gate.requester_may_not_write(editor, project) is False
    _set_role(sl, project, editor, "viewer")
    assert project_write_gate.requester_may_not_write(editor, project) is True
    with pytest.raises(project_write_gate.RequesterViewOnly) as refused:
        project_write_gate.check_requester(editor, project)
    assert refused.value.code == KIND and str(refused.value).startswith(KIND)
    _set_role(sl, project, editor, "editor")
    project_write_gate.check_requester(editor, project)  # passes again


def test_a_removed_member_or_a_deleted_account_may_not_write(world):
    sl, project, editor = world["session_local"], world["project"], world["editor"]
    with sl() as db:
        db.query(ProjectMembership).filter_by(
            project_id=uuid.UUID(str(project)), user_id=editor
        ).delete()
        db.commit()
    assert project_write_gate.requester_may_not_write(editor, project) is True
    assert project_write_gate.requester_may_not_write(uuid.uuid4(), project) is True


def test_no_requester_no_project_or_a_deleted_project_is_not_judged(world):
    project, editor = world["project"], world["editor"]
    assert project_write_gate.requester_may_not_write(None, project) is False
    assert project_write_gate.requester_may_not_write(editor, None) is False
    assert project_write_gate.requester_may_not_write("not-a-uuid", project) is False
    assert project_write_gate.requester_may_not_write(editor, uuid.uuid4()) is False


# ── decision study run ───────────────────────────────────────────────────────


def test_a_study_run_whose_requester_is_demoted_mid_run_writes_nothing(
    client, api_project, studies_on, monkeypatch, demotion, project_row,
    project_storage_dir, _auth_db,
):
    from services import solver_service
    from services.study import store

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver(demotion.demote))
    api_project("vr-run-src")
    r = create_pack_study(client, "vr-run-src", "vr-run")
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    r = client.post(f"/api/projects/vr-run/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    rec = wait_run(client, "vr-run", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert rec["error"].startswith(KIND) and rec["solved"] == []
    # None of the run's results are in the project: no findings, no fork, the
    # study unchanged; only the refusal is on record.
    base_dir = project_storage_dir("vr-run")
    assert store.load_aux(base_dir, sid, "findings") is None
    assert store.load_aux(base_dir, sid, "run")["error_code"] == KIND
    assert store.load_study(base_dir, sid).findings_ref is None
    assert all(project_row(f"vr-run-opt-{o}") is None for o in OPTIONS)
    assert PyPSAService._study_owned == set()
    _assert_requester(demotion, _auth_db[1], "vr-run")


def test_a_study_run_refused_before_its_first_fork_creates_none(
    client, api_project, studies_on, monkeypatch, demotion, project_row,
):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    api_project("vr-pre-src")
    r = create_pack_study(client, "vr-pre-src", "vr-pre")
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    demotion.demote()  # demoted after the route's gate, before the worker writes
    assert client.post(f"/api/projects/vr-pre/studies/{sid}/run", json={}).status_code == 202
    rec = wait_run(client, "vr-pre", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert solver.calls == []
    assert all(project_row(f"vr-pre-opt-{o}") is None for o in OPTIONS)


# ── tornado ──────────────────────────────────────────────────────────────────


def test_a_tornado_whose_requester_is_demoted_mid_run_saves_nothing(
    client, api_project, studies_on, monkeypatch, demotion, project_storage_dir,
    _auth_db,
):
    from services import solver_service
    from services.study import findings as F
    from services.study import store

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _study_done(client, api_project, "vr-tor")
    solver.on_call = demotion.demote  # the tornado's first solve demotes
    r = client.post(f"/api/projects/vr-tor/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    rec = wait_tornado(client, "vr-tor", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    saved = store.load_aux(project_storage_dir("vr-tor"), sid, F.TORNADO_AUX)
    assert saved["error_code"] == KIND and "robustness" not in saved
    assert "attributions" not in saved
    # The first variant's fork was deleted; no second one was made.
    sl = _auth_db[1]
    assert _variant_rows(sl, "vr-tor") == []
    assert rec["solves_charged"] == 1
    _assert_requester(demotion, sl, "vr-tor")


# ── supply architectures ─────────────────────────────────────────────────────


def test_supply_architectures_whose_requester_is_demoted_mid_run_save_nothing(
    client, studies_on, install_network, arch_solver, session_local, demotion,  # noqa: F811
    monkeypatch,
):
    from services.study import supply_architecture_runner as SAR

    from services import solver_service

    sid = _sa_project(client, install_network, "vr-sa")

    def demote_then_solve(*a, **kw):
        demotion.demote()
        return arch_solver(*a, **kw)

    monkeypatch.setattr(solver_service, "run_simulation", demote_then_solve)
    assert client.post(_sa_url("vr-sa", sid), json={}).status_code == 202
    rec = _sa_wait(client, "vr-sa", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    saved = SAR._load_saved(_base_dir("vr-sa"), sid)
    assert saved["error_code"] == KIND and saved["rows"] is None
    assert _var_rows(session_local, "vr-sa") == []
    assert len(arch_solver.calls) == 1  # stopped before the second fork
    _assert_requester(demotion, session_local, "vr-sa")


def _base_dir(name):
    from services import project_registry
    from db.session import SessionLocal

    with SessionLocal() as db:
        return project_registry.project_dir(db.query(Project).filter_by(name=name).one())


# ── investment case and business case ────────────────────────────────────────


def test_an_investment_case_whose_requester_is_demoted_stores_no_report(demotion):
    from tests.fixtures.investment_case.sam import sam_case as S
    from tests.test_investment_case_runner import _Cfg
    from services.finance.investment_case_runner import start_investment_case

    finance = S.to_finance_case("s2").inputs.model_dump(mode="json")
    state: dict = {"solver_config": _Cfg(finance)}

    def build():
        demotion.demote()
        return S.to_finance_case("s2")

    def publish(key, record, thread):
        state[key] = record
        thread.start()
        thread.join(timeout=60)

    start_investment_case(None, build_case=build, solver_state=state,
                          state_update=state.update, publish_study=publish,
                          requester=("u-1", "p-1"))
    rec = state["investment_case"]
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert "investment_case_report" not in state
    assert demotion.calls == [("u-1", "p-1")]


def test_the_investment_case_route_records_its_requester(
    client, install_network, monkeypatch, demotion, _auth_db,
):
    from tests.test_investment_case_routes import STUDY_URL as IC_URL
    from tests.test_investment_case_routes import (
        _fake_build,
        _poll,
        _put_fin,
        _s2_inputs,
        _solved,
    )

    _fake_build(monkeypatch)
    _solved(install_network)
    assert _put_fin(client, _s2_inputs()).status_code == 200
    demotion.demote()
    assert client.post(IC_URL, json={}).status_code == 200
    rec = _poll(client)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert rec["report"]["present"] is False
    uid, pid = demotion.calls[-1]
    assert _a_user(_auth_db[1], uid)
    assert pid == str(PyPSAService.get_active_context().project_uuid)


def test_a_business_case_whose_requester_is_demoted_stores_no_report(
    client, install_network, monkeypatch, session_state, demotion, _auth_db,
):
    from tests.test_business_case_routes import BC_URL, _bc, _bc_poll, _put, _with_ic

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    demotion.demote()
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body["status"] == "failed" and body["error_code"] == KIND, body
    assert session_state(client).get("business_case_report") is None
    uid, pid = demotion.calls[-1]
    assert _a_user(_auth_db[1], uid)
    assert pid == str(PyPSAService.get_active_context().project_uuid)
