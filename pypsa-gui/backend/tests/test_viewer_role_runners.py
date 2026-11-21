"""Project-viewer-role 02: the in-process runners read the requester's role
again before they write, so an editor made a viewer, or removed, mid-run gets
a ``project_view_only`` failure and nothing saved into the project.

Every demotion here is a real membership change (the project's tree-root
`ProjectMembership` row), made from a deterministic hook inside the run: the
fake solver's call, the investment case's build, the business case's output
read. Studies run as a real editor member through the routes; the finance
runs' requester is the route's `_run_requester`, which the last tests pin.
"""

from __future__ import annotations

import uuid

import pytest

from db.models import Project, ProjectMembership
from services import project_acl, project_write_gate
from services.project_write_gate import KIND
from services.pypsa_service import PyPSAService
from tests.study_s4_support import OPTIONS, create_pack_study, enable_studies, wait_run
from tests.test_homer_supply_architecture_routes import _project as _sa_project
from tests.test_homer_supply_architecture_routes import _url as _sa_url
from tests.test_homer_supply_architecture_routes import _var_rows
from tests.test_homer_supply_architecture_routes import _wait as _sa_wait
from tests.test_homer_supply_architecture_routes import (  # noqa: F401 (fixture)
    arch_solver,
)
from tests.test_study_runner import FakeSolver
from tests.test_study_tornado_routes import _run as _study_done
from tests.test_study_tornado_routes import _variant_rows, wait_tornado
from tests.test_viewer_role_gate import (  # noqa: F401 (fixtures)
    _grant,
    _member,
    _set_role,
    as_user,
    baseline,
    helpers_state,
    world,
)


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def sl(_auth_db):
    return _auth_db[1]


class Member:
    """A real editor member of a project's tree root, demoted or removed on cue."""

    def __init__(self, session_local, project_name: str | None = None, project_id=None):
        self.sl = session_local
        with session_local() as db:
            project = (db.get(Project, uuid.UUID(str(project_id))) if project_id
                       else db.query(Project).filter_by(name=project_name).one())
            root = project_acl.resolve_tree_root(db, project)
            self.project_id, self.root_id = project.id, root.id
            org, by = project.org_id, project.created_by
        self.id = _member(session_local, org)
        _grant(session_local, self.root_id, self.id, "editor", by=by)

    def demote(self, *_a) -> None:
        _set_role(self.sl, self.root_id, self.id, "viewer")

    def remove(self, *_a) -> None:
        with self.sl() as db:
            db.query(ProjectMembership).filter_by(
                project_id=self.root_id, user_id=self.id).delete()
            db.commit()


def _released(client, session_local, name) -> None:
    """Drop the admin's edit lock, so the member's write meets the role gate."""
    with session_local() as db:
        pid = db.query(Project).filter_by(name=name).one().id
    assert client.delete(f"/api/projects/{pid}/lock").status_code in (200, 204)


# ── the gate: fresh from the database, a lost membership fails closed ────────


def test_the_requesters_role_is_read_fresh_each_call(world):
    sl_, project, editor = world["session_local"], world["project"], world["editor"]
    assert project_write_gate.requester_may_not_write(editor, project) is False
    _set_role(sl_, project, editor, "viewer")
    assert project_write_gate.requester_may_not_write(editor, project) is True
    with pytest.raises(project_write_gate.RequesterViewOnly) as refused:
        project_write_gate.check_requester(editor, project)
    assert refused.value.code == KIND and str(refused.value).startswith(KIND)
    _set_role(sl_, project, editor, "editor")
    project_write_gate.check_requester(editor, project)  # passes again


def test_a_removed_member_or_a_deleted_account_may_not_write(world):
    sl_, project, editor = world["session_local"], world["project"], world["editor"]
    with sl_() as db:
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


def test_a_role_that_cannot_be_read_refuses(world, monkeypatch):
    from db import session as db_session

    def broken():
        raise RuntimeError("database gone")

    monkeypatch.setattr(db_session, "SessionLocal", broken)
    assert project_write_gate.requester_may_not_write(world["editor"], world["project"]) is True


# ── decision study run ───────────────────────────────────────────────────────


def _study_as_member(client, api_project, sl_, name):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name)
    assert r.status_code == 201, r.text
    _released(client, sl_, name)
    return r.json()["study_id"], Member(sl_, name)


def _run_as(as_user, member, name, sid):
    c = as_user(member.id)
    r = c.post(f"/api/projects/{name}/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    return c, wait_run(c, name, sid)


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_a_study_run_whose_requester_loses_edit_mid_run_writes_nothing(
    change, client, api_project, studies_on, monkeypatch, project_row,
    project_storage_dir, sl, as_user,
):
    from services import solver_service
    from services.study import store

    name = f"vr-run-{change}"
    sid, member = _study_as_member(client, api_project, sl, name)
    monkeypatch.setattr(solver_service, "run_simulation",
                        FakeSolver(getattr(member, change)))
    _c, rec = _run_as(as_user, member, name, sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert rec["error"].startswith(KIND) and rec["solved"] == []
    # Nothing of the run is in the project: no findings, run record or study
    # change; the forks it made are gone.
    base_dir = project_storage_dir(name)
    assert store.load_aux(base_dir, sid, "findings") is None
    assert store.load_aux(base_dir, sid, "run") is None
    assert store.load_study(base_dir, sid).findings_ref is None
    assert all(project_row(f"{name}-opt-{o}") is None for o in OPTIONS)
    assert PyPSAService._study_owned == set()
    # The refusal reads back (the base context is gone by now).
    again = client.get(f"/api/projects/{name}/studies/{sid}/run").json()
    assert again["error_code"] == KIND


def test_a_study_run_of_an_editor_who_stays_one_is_saved(
    client, api_project, studies_on, monkeypatch, project_storage_dir, sl, as_user,
):
    from services import solver_service
    from services.study import store

    sid, member = _study_as_member(client, api_project, sl, "vr-run-ok")
    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    _c, rec = _run_as(as_user, member, "vr-run-ok", sid)
    assert rec["status"] == "done", rec
    assert store.load_aux(project_storage_dir("vr-run-ok"), sid, "findings") is not None


def test_a_study_run_refused_before_its_first_fork_creates_none(
    client, api_project, studies_on, monkeypatch, project_row, sl, as_user,
):
    from services import solver_service
    from services.study import runner as R

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid, member = _study_as_member(client, api_project, sl, "vr-pre")
    # Demoted after the route's gate let the run start, before its first write.
    real_worker = R._worker

    def demoted_first(**kw):
        member.demote()
        return real_worker(**kw)

    monkeypatch.setattr(R, "_worker", demoted_first)
    _c, rec = _run_as(as_user, member, "vr-pre", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert solver.calls == []
    assert all(project_row(f"vr-pre-opt-{o}") is None for o in OPTIONS)


# ── tornado ──────────────────────────────────────────────────────────────────


def test_a_tornado_whose_requester_is_demoted_mid_run_saves_nothing(
    client, api_project, studies_on, monkeypatch, project_storage_dir, sl, as_user,
):
    from services import solver_service
    from services.study import findings as F
    from services.study import store

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _study_done(client, api_project, "vr-tor")
    _released(client, sl, "vr-tor")
    member = Member(sl, "vr-tor")
    solver.on_call = member.demote  # the tornado's first solve demotes
    c = as_user(member.id)
    r = c.post(f"/api/projects/vr-tor/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    rec = wait_tornado(client, "vr-tor", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert store.load_aux(project_storage_dir("vr-tor"), sid, F.TORNADO_AUX) is None
    # The first variant's fork was deleted; no second one was made.
    assert _variant_rows(sl, "vr-tor") == []
    assert rec["solves_charged"] == 1


# ── supply architectures ─────────────────────────────────────────────────────


def test_supply_architectures_whose_requester_is_demoted_mid_run_save_nothing(
    client, studies_on, install_network, arch_solver, sl, as_user, monkeypatch,  # noqa: F811
):
    from services import project_registry, solver_service
    from services.study import supply_architecture_runner as SAR

    sid = _sa_project(client, install_network, "vr-sa")
    _released(client, sl, "vr-sa")
    member = Member(sl, "vr-sa")

    def demote_then_solve(*a, **kw):
        member.demote()
        return arch_solver(*a, **kw)

    monkeypatch.setattr(solver_service, "run_simulation", demote_then_solve)
    assert as_user(member.id).post(_sa_url("vr-sa", sid), json={}).status_code == 202
    rec = _sa_wait(client, "vr-sa", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    with sl() as db:
        base_dir = project_registry.project_dir(
            db.query(Project).filter_by(name="vr-sa").one())
    assert SAR._load_saved(base_dir, sid) is None
    assert _var_rows(sl, "vr-sa") == []
    assert len(arch_solver.calls) == 1  # stopped before the second fork


# ── investment case and business case ────────────────────────────────────────


def _ic_run(world, change: str | None):
    from services.finance.investment_case_runner import start_investment_case
    from tests.fixtures.investment_case.sam import sam_case as S
    from tests.test_investment_case_runner import _Cfg

    member = Member(world["session_local"], project_id=world["project"])
    state: dict = {"solver_config": _Cfg(S.to_finance_case("s2").inputs.model_dump(mode="json"))}

    def build():
        if change:
            getattr(member, change)()
        return S.to_finance_case("s2")

    def publish(key, record, thread):
        state[key] = record
        thread.start()
        thread.join(timeout=60)

    start_investment_case(None, build_case=build, solver_state=state,
                          state_update=state.update, publish_study=publish,
                          requester=(member.id, member.project_id))
    return state


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_an_investment_case_whose_requester_loses_edit_stores_no_report(world, change):
    state = _ic_run(world, change)
    rec = state["investment_case"]
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert "investment_case_report" not in state


def test_an_investment_case_of_an_editor_who_stays_one_is_stored(world):
    state = _ic_run(world, None)
    assert state["investment_case"]["status"] == "done"
    assert state["investment_case_report"] is not None


def test_a_business_case_whose_requester_is_demoted_stores_no_report(
    client, install_network, monkeypatch, session_state, sl,
):
    import routers.results as RR
    from tests.test_business_case_routes import BC_URL, _bc, _bc_poll, _put, _with_ic

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    with sl() as db:
        project = db.query(Project).first()
    member = Member(sl, project_id=project.id)
    monkeypatch.setattr(RR, "_run_requester", lambda _u: (member.id, member.project_id))
    real_read = RR._bc_read_solve

    def demote_then_read(ic_hash):
        read = real_read(ic_hash)

        def wrapped(*a, **kw):
            member.demote()
            return read(*a, **kw)

        return wrapped

    monkeypatch.setattr(RR, "_bc_read_solve", demote_then_read)
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body["status"] == "failed" and body["error_code"] == KIND, body
    assert session_state(client).get("business_case_report") is None


# ── the routes and chat tools name the requester ─────────────────────────────


@pytest.mark.parametrize("route", ["investment_case", "business_case"])
def test_the_finance_routes_name_the_caller_and_the_project_at_start(
    route, client, install_network, monkeypatch, sl,
):
    import routers.results as RR
    from db.models import User
    from tests.test_business_case_routes import BC_URL, _bc, _put, _with_ic

    seen = []
    real = RR._run_requester
    monkeypatch.setattr(RR, "_run_requester", lambda u: seen.append(real(u)) or seen[-1])
    _with_ic(client, install_network, monkeypatch)
    if route == "business_case":
        assert _put(client, _bc()).status_code == 200
        assert client.post(BC_URL).status_code == 200
    uid, pid = seen[-1]
    with sl() as db:
        assert db.get(User, uuid.UUID(str(uid))) is not None
    assert pid == PyPSAService.get_active_context().project_uuid


def test_a_chat_run_names_the_acting_user():
    import routers.results as RR
    from services import chat_tools

    who = str(uuid.uuid4())
    chat_tools.set_acting_user(who)
    try:
        uid, _pid = RR._run_requester(object())  # a chat call: no HTTP user
    finally:
        chat_tools.set_acting_user(None)
    assert str(uid) == who
