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
from tests.test_study_runner import FakeSolver
from tests.test_study_tornado_routes import _run as _study_done
from tests.test_study_tornado_routes import _variant_rows, wait_tornado
from tests import test_homer_supply_architecture_routes as sa_fixtures
from tests import test_viewer_role_gate as gate_fixtures
from tests.test_viewer_role_gate import _grant, _member, _set_role

# Fixtures borrowed by assignment, as `test_viewer_role_gate` borrows its own.
arch_solver = sa_fixtures.arch_solver
as_user = gate_fixtures.as_user
baseline = gate_fixtures.baseline
helpers_state = gate_fixtures.helpers_state
world = gate_fixtures.world


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
            project = (
                db.get(Project, uuid.UUID(str(project_id)))
                if project_id
                else db.query(Project).filter_by(name=project_name).one()
            )
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
                project_id=self.root_id, user_id=self.id
            ).delete()
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
    assert (
        project_write_gate.requester_may_not_write(world["editor"], world["project"])
        is True
    )


# ── decision study run ───────────────────────────────────────────────────────


def _study_as_member(client, api_project, sl_, name):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name)
    assert r.status_code == 201, r.text
    _released(client, sl_, name)
    return r.json()["study_id"], Member(sl_, name)


def _run_as(client, as_user, member, name, sid):
    """Start the run as the member; read it as the admin (a removed member
    can no longer open the project)."""
    c = as_user(member.id)
    r = c.post(f"/api/projects/{name}/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    return c, wait_run(client, name, sid)


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_a_study_run_whose_requester_loses_edit_mid_run_writes_nothing(
    change,
    client,
    api_project,
    studies_on,
    monkeypatch,
    project_row,
    project_storage_dir,
    sl,
    as_user,
):
    from services import solver_service
    from services.study import store

    name = f"vr-run-{change}"
    sid, member = _study_as_member(client, api_project, sl, name)
    monkeypatch.setattr(
        solver_service, "run_simulation", FakeSolver(getattr(member, change))
    )
    _c, rec = _run_as(client, as_user, member, name, sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert rec["error"].startswith(KIND) and rec["solved"] == []
    # Nothing of the run is in the project: no findings, run record or study
    # change; the forks it made are gone.
    base_dir = project_storage_dir(name)
    assert store.load_aux(base_dir, sid, "findings") is None
    assert store.load_aux(base_dir, sid, "run") is None
    study = store.load_study(base_dir, sid)
    assert study.findings_ref is None
    # The option list the run saved while its requester could still write is
    # put back as it was before the run (a new study: empty).
    assert study.option_projects == []
    assert all(project_row(f"{name}-opt-{o}") is None for o in OPTIONS)
    assert PyPSAService._study_owned == set()
    # The refusal reads back (the base context is gone by now).
    again = client.get(f"/api/projects/{name}/studies/{sid}/run").json()
    assert again["error_code"] == KIND


def test_a_study_run_of_an_editor_who_stays_one_is_saved(
    client,
    api_project,
    studies_on,
    monkeypatch,
    project_storage_dir,
    sl,
    as_user,
):
    from services import solver_service
    from services.study import store

    sid, member = _study_as_member(client, api_project, sl, "vr-run-ok")
    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    _c, rec = _run_as(client, as_user, member, "vr-run-ok", sid)
    assert rec["status"] == "done", rec
    assert store.load_aux(project_storage_dir("vr-run-ok"), sid, "findings") is not None


def test_a_refused_rerun_puts_back_the_option_projects_of_the_last_run(
    client,
    api_project,
    studies_on,
    monkeypatch,
    project_storage_dir,
    sl,
    as_user,
):
    """GK B4 condition 5: the run starts from a non-empty `option_projects`
    (an earlier run's solved forks). The second run replaces those forks as
    it makes its own, so a refusal puts back only the prior forks that still
    exist: the list never names a project that is gone."""
    from services import solver_service
    from services.study import store

    name = "vr-rerun"
    sid, member = _study_as_member(client, api_project, sl, name)
    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    _c, rec = _run_as(client, as_user, member, name, sid)
    assert rec["status"] == "done", rec
    base_dir = project_storage_dir(name)
    prior = list(store.load_study(base_dir, sid).option_projects)
    assert prior, "the first run saved its solved forks"

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver(member.demote))
    _c, rec = _run_as(client, as_user, member, name, sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    study = store.load_study(base_dir, sid)
    with sl() as db:
        still = [pid for pid in prior if db.get(Project, uuid.UUID(pid)) is not None]
        assert all(db.get(Project, uuid.UUID(pid)) is not None
                   for pid in study.option_projects)
    assert list(study.option_projects) == still


def test_a_study_run_refused_before_its_first_fork_creates_none(
    client,
    api_project,
    studies_on,
    monkeypatch,
    project_row,
    sl,
    as_user,
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
    _c, rec = _run_as(client, as_user, member, "vr-pre", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert solver.calls == []
    assert all(project_row(f"vr-pre-opt-{o}") is None for o in OPTIONS)


# ── tornado ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_a_tornado_whose_requester_loses_edit_mid_run_saves_nothing(
    change,
    client,
    api_project,
    studies_on,
    monkeypatch,
    project_storage_dir,
    sl,
    as_user,
):
    from services import solver_service
    from services.study import findings as F
    from services.study import store

    name = f"vr-tor-{change}"
    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _study_done(client, api_project, name)
    _released(client, sl, name)
    member = Member(sl, name)
    solver.on_call = getattr(member, change)  # the tornado's first solve demotes
    c = as_user(member.id)
    r = c.post(f"/api/projects/{name}/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    rec = wait_tornado(client, name, sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert store.load_aux(project_storage_dir(name), sid, F.TORNADO_AUX) is None
    # The first variant's fork was deleted; no second one was made.
    assert _variant_rows(sl, name) == []
    assert rec["solves_charged"] == 1


# ── supply architectures ─────────────────────────────────────────────────────


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_supply_architectures_whose_requester_loses_edit_mid_run_save_nothing(
    change,
    client,
    studies_on,
    install_network,
    arch_solver,
    sl,
    as_user,
    monkeypatch,
):
    from services import project_registry, solver_service
    from services.study import supply_architecture_runner as SAR

    name = f"vr-sa-{change}"
    sid = _sa_project(client, install_network, name)
    _released(client, sl, name)
    member = Member(sl, name)

    def demote_then_solve(*a, **kw):
        getattr(member, change)()
        return arch_solver(*a, **kw)

    monkeypatch.setattr(solver_service, "run_simulation", demote_then_solve)
    assert as_user(member.id).post(_sa_url(name, sid), json={}).status_code == 202
    rec = _sa_wait(client, name, sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    with sl() as db:
        base_dir = project_registry.project_dir(
            db.query(Project).filter_by(name=name).one()
        )
    assert SAR._load_saved(base_dir, sid) is None
    assert _var_rows(sl, name) == []
    assert len(arch_solver.calls) == 1  # stopped before the second fork


# ── investment case and business case ────────────────────────────────────────


_NO_REQUESTER = object()


def _ic_run(
    world, change: str | None, before_store=None, *, requester=None, refuse=False
):
    from services.finance.investment_case_runner import start_investment_case
    from tests.fixtures.investment_case.sam import sam_case as S
    from tests.test_investment_case_runner import _Cfg

    member = Member(world["session_local"], project_id=world["project"])
    state: dict = {
        "solver_config": _Cfg(S.to_finance_case("s2").inputs.model_dump(mode="json"))
    }

    def build():
        if change:
            getattr(member, change)()
        if before_store is not None:
            before_store()
        if refuse:
            from services.finance.case import FinanceRefused

            raise FinanceRefused("vr_refused", "the case cannot be valued")
        return S.to_finance_case("s2")

    def publish(key, record, thread):
        state[key] = record
        thread.start()
        thread.join(timeout=60)

    start_investment_case(
        None,
        build_case=build,
        solver_state=state,
        state_update=state.update,
        publish_study=publish,
        requester=None
        if requester is _NO_REQUESTER
        else (requester or (member.id, member.project_id)),
    )
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


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_a_business_case_whose_requester_loses_edit_stores_no_report(
    change,
    client,
    api_project,
    install_network,
    monkeypatch,
    session_state,
    sl,
):
    import routers.results as RR
    from tests.test_business_case_routes import BC_URL, _bc, _bc_poll, _put, _with_ic

    api_project(f"vr-bc-{change}")  # the requester's project (a real membership row)
    member = Member(sl, f"vr-bc-{change}")
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    monkeypatch.setattr(RR, "_run_requester", lambda _u: (member.id, member.project_id))
    real_read = RR._bc_read_solve

    def demote_then_read(ic_hash):
        read = real_read(ic_hash)

        def wrapped(*a, **kw):
            getattr(member, change)()
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
    route,
    client,
    install_network,
    monkeypatch,
    sl,
):
    import routers.results as RR
    from db.models import User
    from tests.test_business_case_routes import BC_URL, _bc, _put, _with_ic

    seen = []
    real = RR._run_requester
    monkeypatch.setattr(
        RR, "_run_requester", lambda u: seen.append(real(u)) or seen[-1]
    )
    from tests.test_investment_case_routes import STUDY_URL as IC_URL
    from tests.test_investment_case_routes import _poll

    name = f"vr-fin-{route}"
    _with_ic(client, install_network, monkeypatch)
    # Saved as a real project, so the active context carries its id.
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    seen.clear()
    if route == "business_case":
        assert _put(client, _bc()).status_code == 200
        assert client.post(BC_URL).status_code == 200
    else:
        assert client.post(IC_URL, json={}).status_code == 200
        _poll(client)
    uid, pid = seen[-1]
    with sl() as db:
        assert db.get(User, uuid.UUID(str(uid))) is not None
        project = db.query(Project).filter_by(name=name).one()
    assert pid is not None and str(pid) == str(project.id)


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


def test_an_investment_case_refusal_report_is_not_stored_for_a_viewer(world):
    """The refused-case report is a project write too (the build refused)."""
    state = _ic_run(world, "demote", refuse=True)
    rec = state["investment_case"]
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert "investment_case_report" not in state


def test_an_engine_refusal_report_is_not_stored_for_a_viewer(world, monkeypatch):
    from services.finance import engine
    from services.finance.case import FinanceRefused

    def refuse(*_a, **_kw):
        raise FinanceRefused("vr_engine_refused", "no")

    monkeypatch.setattr(engine, "run_case", refuse)
    state = _ic_run(world, "demote")
    rec = state["investment_case"]
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert "investment_case_report" not in state


def test_an_investment_case_with_no_requester_is_not_judged(world):
    """Local mode records no requester: the run stores its report."""
    state = _ic_run(world, "demote", requester=_NO_REQUESTER)
    assert state["investment_case"]["status"] == "done"
    assert state["investment_case_report"] is not None


def test_a_business_case_with_no_requester_is_not_judged(
    client, install_network, monkeypatch, session_state
):
    import routers.results as RR
    from tests.test_business_case_routes import BC_URL, _bc, _bc_poll, _put, _with_ic

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    monkeypatch.setattr(RR, "_run_requester", lambda _u: None)
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "done"
    assert session_state(client).get("business_case_report") is not None


# ── late demotion: after the last solve, before the save ─────────────────────


def test_a_tornado_demoted_after_its_last_solve_saves_nothing(
    client, api_project, studies_on, monkeypatch, project_storage_dir, sl, as_user
):
    """The final re-read before the save refuses on its own: every variant
    solved, then the requester is demoted, and nothing is saved."""
    from services import solver_service
    from services.study import findings as F
    from services.study import store

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    sid = _study_done(client, api_project, "vr-tor-late")
    _released(client, sl, "vr-tor-late")
    member = Member(sl, "vr-tor-late")
    real = F.run_tornado

    def run_then_demote(*a, **kw):
        out = real(*a, **kw)
        member.demote()
        return out

    monkeypatch.setattr(F, "run_tornado", run_then_demote)
    c = as_user(member.id)
    r = c.post(f"/api/projects/vr-tor-late/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    rec = wait_tornado(client, "vr-tor-late", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert rec["solves_charged"] == r.json()["solves_estimated"]  # all solved
    assert (
        store.load_aux(project_storage_dir("vr-tor-late"), sid, F.TORNADO_AUX) is None
    )


def test_supply_architectures_demoted_after_the_last_solve_save_nothing(
    client, studies_on, install_network, arch_solver, sl, as_user, monkeypatch
):
    from services import project_registry, solver_service
    from services.study import supply_architecture_runner as SAR
    from services.study import supply_architectures as SA

    monkeypatch.setattr(solver_service, "run_simulation", arch_solver)
    sid = _sa_project(client, install_network, "vr-sa-late")
    _released(client, sl, "vr-sa-late")
    member = Member(sl, "vr-sa-late")
    real = SA.compare

    def compare_then_demote(*a, **kw):
        out = real(*a, **kw)
        member.demote()
        return out

    monkeypatch.setattr(SA, "compare", compare_then_demote)
    r = as_user(member.id).post(_sa_url("vr-sa-late", sid), json={})
    assert r.status_code == 202, r.text
    rec = _sa_wait(client, "vr-sa-late", sid)
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert len(arch_solver.calls) == r.json()["solves_estimated"]  # all solved
    with sl() as db:
        base_dir = project_registry.project_dir(
            db.query(Project).filter_by(name="vr-sa-late").one()
        )
    assert SAR._load_saved(base_dir, sid) is None


def test_a_run_whose_role_check_fails_refuses_and_logs(world, monkeypatch, caplog):
    """A database error inside the check fails closed: the run ends refused,
    nothing is stored, the error is logged, and the status is not left running."""
    from db import session as db_session

    def broken():
        raise RuntimeError("database gone")

    def break_db():
        monkeypatch.setattr(db_session, "SessionLocal", broken)

    import logging

    caplog.set_level(logging.ERROR, logger="services.project_write_gate")
    state = _ic_run(world, None, before_store=break_db)
    rec = state["investment_case"]
    assert rec["status"] == "failed" and rec["error_code"] == KIND, rec
    assert "investment_case_report" not in state
    assert any("refusing" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("finished_at", ["yesterday", None, float("nan"), True])
def test_a_malformed_saved_timestamp_reads_as_older_than_a_refusal(finished_at):
    from services.study import runner as R

    base, sid = str(uuid.uuid4()), "s-malformed"
    R.remember_refusal(base, sid, "run", {"status": "failed", "finished_at": 5.0})
    got = R.refused_or_saved(
        base, sid, "run", {"status": "done", "finished_at": finished_at}
    )
    assert got["status"] == "failed"
    newer = R.refused_or_saved(base, sid, "run", {"status": "done", "finished_at": 9.0})
    assert newer["status"] == "done"
