"""
S9 integration carries for the decision study's forks (plan 2026-09-28
guided investment study MVP-1, S9; gate S4 nits, gate S6 [N7], gate S8):

* a forced Save-As (or Save-a-Copy) over an option fork's directory must not
  leave the user's network marked study-owned, where a re-run or the study's
  delete would remove it (gate S4 [N]);
* the runner's wait on a queued solve has a deadline, and passing it is a
  typed failure, not a worker pinned for ever (gate S4 nit);
* at startup, leftover throw-away variant forks (a crash mid-tornado) and
  option forks whose study record is gone are swept, and only what is
  provably study-owned (M2: the fork's metadata names the study and the base
  AND its database row is a child of that base) (gate S6 [N7], S8 carry).

The runs use the fake solver of `tests/test_study_runner.py` (the queue, the
forks, the campaign and the reads are real; only `run_simulation` is
replaced).
"""
from __future__ import annotations

import json
import threading

import pytest

from services.pypsa_service import PyPSAService
from services.study import forks as study_forks
from services.study import runner as R
from services.study import store
from tests.study_s4_support import create_pack_study, enable_studies, wait_run
from tests.test_study_runner import FakeSolver


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _run(client, api_project, name) -> str:
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, name, sid)["status"] == "done"
    return sid


def _meta(project_storage_dir, name) -> dict:
    return json.loads((project_storage_dir(name) / "metadata.json").read_text())


def _owned(project_row, name, sid, base) -> bool:
    return study_forks.is_study_owned(project_row(name), study_id=sid,
                                      base_uuid=str(project_row(base).id))


# ── a forced save over a fork does not inherit its ownership ─────────────

@pytest.mark.parametrize("params", [{"force": True}, {"rebind": True}],
                         ids=["save_a_copy_forced", "save_as"])
def test_a_forced_save_over_an_option_fork_drops_its_owner_keys(
        client, api_project, studies_on, fake, project_row, project_storage_dir,
        params):
    """
    The session's own project (`sa-src`) is saved over the fork's name. The
    directory now holds the USER's network, so it must stop reading as the
    study's: the owner keys go, ownership no longer verifies, and deleting
    the study leaves it where it is.
    """
    import pypsa

    sid = _run(client, api_project, "sa")
    fork = "sa-opt-bess_2h"
    assert _owned(project_row, fork, sid, "sa")
    r = client.post(f"/api/projects/{fork}", params={**params, "clear_undo": False})
    assert r.status_code == 200, r.text
    meta = _meta(project_storage_dir, fork)
    assert not set(study_forks.OWNER_KEYS) & set(meta), meta
    assert not _owned(project_row, fork, sid, "sa")
    # the user's network is what the directory holds, and the study's delete
    # leaves it
    saved = pypsa.Network(str(project_storage_dir(fork) / "network.nc"))
    assert "battery" not in saved.storage_units.index
    assert client.delete(f"/api/projects/sa/studies/{sid}").status_code == 204
    assert project_row(fork) is not None
    assert (project_storage_dir(fork) / "network.nc").is_file()
    assert project_row("sa-opt-bess_1h") is None  # still owned: removed


def test_a_forks_own_save_keeps_its_owner_keys(
        client, api_project, studies_on, fake, project_row, project_storage_dir):
    """
    The positive control: the fork opened in the Expert view and saved to
    itself stays the study's (the queue's post-solve save is the same path).
    """
    sid = _run(client, api_project, "own")
    fork = "own-opt-bess_2h"
    assert client.get(f"/api/projects/{fork}").status_code == 200
    r = client.post(f"/api/projects/{fork}", params={"expect": fork, "clear_undo": False})
    assert r.status_code == 200, r.text
    meta = _meta(project_storage_dir, fork)
    assert meta["owner_study_id"] == sid and meta["owner_option_id"] == "bess_2h"
    assert _owned(project_row, fork, sid, "own")


# ── the runner's wait has a deadline ─────────────────────────────────────

class _HangsOnFirstBattery(FakeSolver):
    """
    The first battery option's solve does not return until it is aborted
    (or 20 s pass, bounding the red before the deadline existed).
    """

    def __call__(self, config, n, lock, stop_event, log_queue, state_update=None):
        if len(n.storage_units) and not getattr(self, "hung", False):
            self.hung = True
            self.calls.append((n.name, config, len(n.storage_units)))
            if stop_event.wait(timeout=20.0):
                return "aborted", None
        return super().__call__(config, n, lock, stop_event, log_queue, state_update)


def test_a_solve_that_outlives_the_deadline_fails_the_run_with_a_typed_error(
        client, api_project, studies_on, monkeypatch, project_row, registry_key_for):
    from services import solver_service

    solver = _HangsOnFirstBattery()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    # 5 s, not 1 s: since U2 WP6 an option fork also carries the engine's
    # bound export price and each fake solve materialises the PoC price, so
    # the baseline's queue round trip alone exceeded 1 s in a suite run; the hang is
    # 20 s, so the deadline still decides.
    monkeypatch.setattr(R, "SOLVE_WAIT_DEADLINE_S", 5.0)
    api_project("dl-src")
    r = create_pack_study(client, "dl-src", "dl")
    sid = r.json()["study_id"]
    assert client.post(f"/api/projects/dl/studies/{sid}/run", json={}).status_code == 202
    rec = wait_run(client, "dl", sid, timeout=60.0)
    assert rec["status"] == "failed", rec
    assert rec["error"].startswith("solve_deadline_exceeded:"), rec["error"]
    assert "bess_1h" in rec["error"]
    status = {o: d.get("error") for o, d in rec["details"].items()}
    assert status["bess_1h"].startswith("solve_deadline_exceeded")
    # the run stops there: the rest never started and are named
    assert rec["solved"] == ["none"]
    assert set(rec["pending"]) == {"bess_2h", "bess_4h", "bess_pv_2h"}
    assert len(solver.calls) == 2
    # the timed-out option's fork was aborted in the queue and removed;
    # nothing is left exempt from the cap or loaded
    assert project_row("dl-opt-bess_1h") is None
    assert PyPSAService._study_owned == set(), sorted(PyPSAService._study_owned)
    assert registry_key_for("dl") not in PyPSAService._contexts


def test_the_wait_raises_the_typed_error_and_aborts_the_job(monkeypatch):
    """
    Unit level: a job that never ends is aborted at the deadline and the
    wait raises `SolveDeadlineExceeded` naming it (run on a thread so the red
    before the deadline existed is a failure, not a hang).
    """
    from services.solve_queue import solve_queue

    class Job:
        id = "job-1"
        status = "running"
        project_id = "p-opt-bess_1h"

    aborted: list = []
    monkeypatch.setattr(solve_queue, "abort", lambda job_id: aborted.append(job_id))
    monkeypatch.setattr(R, "_ABORT_GRACE_S", 0.2)
    out: dict = {}

    def wait():
        try:
            R._wait(Job(), threading.Event(), deadline_s=0.2)
        except Exception as exc:  # noqa: BLE001 — inspected below
            out["exc"] = exc

    t = threading.Thread(target=wait, daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "the wait has no deadline"
    exc = out.get("exc")
    assert isinstance(exc, R.SolveDeadlineExceeded), exc
    assert exc.code == "solve_deadline_exceeded" and aborted == ["job-1"]
    assert exc.stuck is True  # the job never reached a terminal state


def test_a_tornado_variant_past_the_deadline_is_a_failed_row_not_a_hang(monkeypatch):
    """
    The tornado's `ForkSolver` shares `runner._wait`; a deadline there is a
    typed `VariantFailed` (the row is not established), never an untyped
    crash of the tornado worker.
    """
    from services.study import findings as F
    from services.study import tornado_runner as T

    def expired(job, stop_event, **_kw):
        raise R.SolveDeadlineExceeded(job, 1.0, stuck=False)

    monkeypatch.setattr(R, "_wait", expired)
    with pytest.raises(F.VariantFailed) as info:
        T._wait_variant(object(), threading.Event())
    assert info.value.code == "solve_deadline_exceeded"


# ── the startup sweep ────────────────────────────────────────────────────

def _leftover_variant(db, user_id, base_row, sid, variant_id, project_storage_dir):
    import pypsa

    from services.solver_service import SolverConfig

    n = pypsa.Network(str(project_storage_dir(base_row.name) / "network.nc"))
    return study_forks.create_variant_fork(db, user_id, base_row=base_row, study_id=sid,
                                           variant_id=variant_id, network=n,
                                           solver_config=SolverConfig())


def test_the_startup_sweep_removes_only_provably_study_owned_leftovers(
        client, api_project, studies_on, fake, project_row, project_storage_dir,
        seeded_identity):
    from db.session import SessionLocal

    live = _run(client, api_project, "sw-live")
    gone = _run(client, api_project, "sw-gone")
    user_id = seeded_identity["user_id"]
    with SessionLocal() as db:
        from db.models import Project

        live_base = db.get(Project, project_row("sw-live").id)
        # a crash mid-tornado leaves a throw-away fork behind
        _leftover_variant(db, user_id, live_base, live, "v01", project_storage_dir)
    # the study record of `sw-gone` is removed out from under its forks
    (project_storage_dir("sw-gone") / "studies" / f"{gone}.json").unlink()
    # a user project whose metadata CLAIMS the study's throw-away fork but
    # whose row is a child of ANOTHER project (a copy carried elsewhere): not
    # provably the study's
    api_project("sw-copy-var-v09")
    with SessionLocal() as db:
        from db.models import Project

        copy = db.get(Project, project_row("sw-copy-var-v09").id)
        copy.parent_project_id = project_row("sw-live-src").id
        db.commit()
    meta_path = project_storage_dir("sw-copy-var-v09") / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.update({"owner_study_id": live, "owner_base_project": str(project_row("sw-live").id),
                 "owner_variant_id": "v09", "throwaway": True})
    meta_path.write_text(json.dumps(meta))
    before_live = {o: project_row(f"sw-live-opt-{o}") is not None
                   for o in ("none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h")}
    assert all(before_live.values())
    assert project_row("sw-live-var-v01") is not None
    var_dir = project_storage_dir("sw-live-var-v01")
    assert var_dir.is_dir()

    with SessionLocal() as db:
        swept = study_forks.sweep_leftover_forks(db)

    assert sorted(swept) == sorted(
        ["sw-live-var-v01"] + [f"sw-gone-opt-{o}" for o in
                               ("none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h")])
    assert project_row("sw-live-var-v01") is None
    assert not var_dir.exists()
    # the live study's option forks, both bases and the user's copy stay
    for o in before_live:
        assert project_row(f"sw-live-opt-{o}") is not None, o
        assert project_row(f"sw-gone-opt-{o}") is None, o
    for name in ("sw-live", "sw-gone", "sw-copy-var-v09", "sw-live-src", "sw-gone-src"):
        assert project_row(name) is not None, name
    # the live study still reads its cases
    r = client.get(f"/api/projects/sw-live/studies/{live}/options/bess_2h/case")
    assert r.status_code == 200, r.text
    # idempotent
    with SessionLocal() as db:
        assert study_forks.sweep_leftover_forks(db) == []


def test_the_startup_sweep_leaves_a_fork_whose_study_record_is_unreadable(
        client, api_project, studies_on, fake, project_row, project_storage_dir):
    """
    An unreadable record is not an absent one: nothing is provable, so
    nothing is deleted.
    """
    from db.session import SessionLocal

    sid = _run(client, api_project, "sw-bad")
    (project_storage_dir("sw-bad") / "studies" / f"{sid}.json").write_text("{not json")
    with SessionLocal() as db:
        assert study_forks.sweep_leftover_forks(db) == []
    assert project_row("sw-bad-opt-bess_2h") is not None


def test_startup_runs_the_sweep_before_the_queue_is_reconciled(monkeypatch):
    """
    `main.lifespan` sweeps first: a re-enqueued job on a leftover fork
    would otherwise make the fork undeletable (`fork_solving`).
    """
    from fastapi.testclient import TestClient

    import main
    from services import solve_job_store

    order: list[str] = []
    monkeypatch.setattr(study_forks, "sweep_leftover_forks",
                        lambda db: order.append("sweep") or [])
    real = solve_job_store.reconcile_on_boot
    monkeypatch.setattr(solve_job_store, "reconcile_on_boot",
                        lambda *a, **k: order.append("reconcile") or real(*a, **k))
    with TestClient(main.app):
        pass
    assert order[:2] == ["sweep", "reconcile"], order


def test_a_failing_sweep_never_fails_startup(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    def boom(db):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(study_forks, "sweep_leftover_forks", boom)
    with TestClient(main.app) as c:
        assert c.get("/api/health").status_code in (200, 401, 404)


def test_the_sweep_uses_the_same_ownership_rule_as_delete():
    """
    One rule (M2): the sweep deletes through `delete_fork`, which verifies
    ownership again and refuses an active queue job or a child project.
    """
    import inspect

    src = inspect.getsource(study_forks.sweep_leftover_forks)
    assert "delete_fork(" in src and "is_study_owned(" in src
    assert store.SIDECAR_DIR == "studies"


class _HangsOnFirstVariant(FakeSolver):
    """The tornado's first re-dispatch never returns until aborted."""

    def __call__(self, config, n, lock, stop_event, log_queue, state_update=None):
        if "-var-" in str(n.name) and not getattr(self, "hung", False):
            self.hung = True
            self.calls.append((n.name, config, len(n.storage_units)))
            if stop_event.wait(timeout=20.0):
                return "aborted", None
        return super().__call__(config, n, lock, stop_event, log_queue, state_update)


def test_a_tornado_stops_at_the_first_solve_past_the_deadline(
        client, api_project, studies_on, monkeypatch, _auth_db):
    """
    Gate S9 [N2]: like the run, the tornado stops at the first solve that
    outlives the deadline (a hung solver pins the queue, so every later
    re-dispatch would wait out the deadline too). The bars already computed
    are kept, the timed-out row says why, the rest are named as pending, and
    the record carries the typed error.
    """
    from services import solver_service
    from tests.test_study_tornado_routes import _run, _variant_rows, wait_tornado

    solver = _HangsOnFirstVariant()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    _e, session_local = _auth_db
    sid = _run(client, api_project, "tdl")
    run_calls = len(solver.calls)
    monkeypatch.setattr(R, "SOLVE_WAIT_DEADLINE_S", 1.0)
    r = client.post(f"/api/projects/tdl/studies/{sid}/findings/tornado", json={})
    assert r.status_code == 202, r.text
    assert r.json()["solves_estimated"] == 4
    rec = wait_tornado(client, "tdl", sid, timeout=60.0)
    assert rec["status"] == "aborted", rec
    assert rec["error"].startswith("solve_deadline_exceeded:"), rec["error"]
    assert len(solver.calls) - run_calls == 1, solver.calls[run_calls:]
    rob = rec["robustness"]
    assert rob["note"] == "tornado_stopped_at_solve_deadline", rob
    assert rob["pending"], rob
    failed = [row for row in rob["tornado"] if "solve_deadline_exceeded" in
              (row.get("unavailable") or {}).values()]
    assert len(failed) == 1, rob["tornado"]
    assert _variant_rows(session_local, "tdl") == []
    assert PyPSAService._study_owned == set()
