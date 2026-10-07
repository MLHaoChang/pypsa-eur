"""The joint optimisation (MILP) of the campus electrical study as a
background job (plan C12).

The MILP takes minutes on a real site, so the panel runs it as a job, in
the repo's report-job shape: one job at a time, a record the status route
serves, a stop event the loop honours between iterations.

What is checked here:
* starting answers a running record and refuses a second job (409), for
  this project or any other, while one runs;
* the least-cost run is made first, or reused when it is fresh;
* the job writes into ``campus_electrical/run_milp/`` and never touches the
  least-cost files in ``run/``;
* cancel ends the job as "cancelled", keeping its best result;
* ``get_state`` carries the job and its results, stale once the least-cost
  inputs it was based on change;
* a failed job leaves no results and frees the slot.

Most tests replace the engine (``ce._milp_engine``) by a fake that runs the
real least-cost pick and calls ``progress`` and ``should_stop`` like the
loop, so they run in seconds. One test runs the real MILP on the mini hub.
"""
import hashlib
import threading
import time

import pandas as pd
import pytest
import yaml
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import project_registry
from tests.test_campus_electrical_service import hub_network, user_and_db  # noqa: F401
from db.models import User  # noqa: F401

SETTINGS = {"k": 1, "pf": 0.95}


@pytest.fixture
def hub(user_and_db):  # noqa: F811
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Milp Hub")
    hub_network(priced=True).export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    ce.draft(row)
    return row


@pytest.fixture
def other_hub(user_and_db):  # noqa: F811
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Other Hub")
    hub_network(priced=True).export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    ce.draft(row)
    return row


@pytest.fixture(autouse=True)
def _clean_slot():
    yield
    for rec in list(ce._MILP_JOBS.values()):
        if rec.get("stop_event") is not None:
            rec["stop_event"].set()
        t = rec.get("thread")
        if t is not None:
            t.join(timeout=60)
    ce._MILP_JOBS.clear()
    ce._MILP_SLOT["active"] = None


class FakeEngine:
    """The MILP loop's contract, fast: the real least-cost pick writes C8's
    files, then ``iterations`` rows of history are reported, one per
    ``step`` the test releases. ``should_stop`` is asked before each
    iteration after the warm start, as the loop asks it."""

    def __init__(self, iterations=3, fail=False):
        self.iterations, self.fail = iterations, fail
        self.step = threading.Semaphore(0)
        self.calls = []
        self.netcdf_free = None

    def __call__(self, run_dir, library, *, progress, should_stop, **kw):
        from gridspine.drivers import campus_study as cs
        self.calls.append({"run_dir": run_dir, "library": library, **kw})
        lock = ce._netcdf_lock()
        self.netcdf_free = lock.acquire(blocking=False)
        if self.netcdf_free:
            lock.release()
        out = cs.invest_campus(run_dir, library, **kw)
        c8 = float(out["cost"]["annualised_eur_per_a"].sum())
        rows, stop = [], "converged"
        for i in range(self.iterations):
            if i > 0:
                assert self.step.acquire(timeout=30), "the test never released the step"
                if should_stop():
                    stop = "cancelled"
                    break
            if self.fail and i == 1:
                raise RuntimeError("the MILP solver gave up")
            # the trial's own cost is not the best so far: the record must carry the best
            rows.append({"iteration": i, "cost": c8 - i + 0.5, "feasible": True, "accepted": True})
            progress(i, 20, {"cost": c8 - i + 0.5, "feasible": True, "accepted": True, "best_cost": c8 - i,
                             "c8_cost": c8, "delta": 1.0, "choice": "x"})
        best = c8 - (len(rows) - 1)
        (run_dir / cs.MILP_HISTORY_CSV).write_text(pd.DataFrame(rows).to_csv(index=False))
        (run_dir / cs.MILP_COMPARISON_CSV).write_text(pd.DataFrame([
            {"need": "transformer X", "c8_choice": "1 x A", "milp_choice": "1 x B",
             "c8_annualised_eur_per_a": c8, "milp_annualised_eur_per_a": best}]).to_csv(index=False))
        fallback = None if best < c8 else "no AC-feasible point is cheaper than C8's"
        out.update(fallback=fallback, summary={
            "method": "milp", "fallback": fallback is not None, "reason": fallback or "", "stop": stop,
            "c8_cost": c8, "milp_cost": best, "c8_feasible": True, "iterations": len(rows) - 1})
        return out


def record_of(project):
    return ce._MILP_JOBS[ce._milp_key(project)]


def wait_until(cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def finish(project, engine=None, steps=10):
    if engine is not None:
        for _ in range(steps):
            engine.step.release()
    record_of(project)["thread"].join(timeout=120)
    assert not record_of(project)["thread"].is_alive()


def digests(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}


# ── start, status, the one-job slot ─────────────────────────────────────────

def test_start_answers_a_running_record_without_the_thread_or_the_event(hub, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(ce, "_milp_engine", engine)
    rec = ce.start_milp_job(hub, SETTINGS)
    assert rec["state"] == "running" and rec["started_at"] > 0 and rec["finished_at"] is None
    assert {"iteration", "max_iter", "best_cost", "c8_cost", "message"} <= set(rec)
    assert "thread" not in rec and "stop_event" not in rec
    lc = ce.get_state(hub)["results"]["investment"]
    c8 = sum(r["annualised_eur_per_a"] for r in lc if r["status"] in ("chosen", "kept"))
    assert rec["c8_cost"] == pytest.approx(c8)
    wait_until(lambda: record_of(hub)["iteration"] == 0)
    status = ce.milp_job_status(hub)
    assert status["state"] == "running" and status["iteration"] == 0 and status["max_iter"] == 20
    assert status["best_cost"] == pytest.approx(c8)
    assert "thread" not in status and "stop_event" not in status
    engine.step.release()
    wait_until(lambda: record_of(hub)["iteration"] == 1)
    assert ce.milp_job_status(hub)["best_cost"] == pytest.approx(c8 - 1)
    finish(hub, engine)
    done = ce.milp_job_status(hub)
    assert done["state"] == "done" and done["finished_at"] >= done["started_at"]
    assert done["iteration"] == 2 and done["best_cost"] == pytest.approx(c8 - 2) and done["stop"] == "converged"


def test_the_status_of_a_project_that_never_ran_the_job_is_none(hub):
    assert ce.milp_job_status(hub) is None
    assert ce.get_state(hub)["milp"] == {"status": None, "results": None, "stale": False}


def test_a_second_job_is_refused_for_this_project_and_any_other_while_one_runs(hub, other_hub, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    for project in (hub, other_hub):
        with pytest.raises(HTTPException) as exc:
            ce.start_milp_job(project, SETTINGS)
        assert exc.value.status_code == 409 and "already running" in exc.value.detail
    assert ce._milp_key(other_hub) not in ce._MILP_JOBS
    finish(hub, engine)
    engine2 = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine2)
    assert ce.start_milp_job(other_hub, SETTINGS)["state"] == "running"      # the slot is free again
    finish(other_hub)
    assert ce.milp_job_status(other_hub)["state"] == "done"
    assert ce.milp_job_status(hub)["state"] == "done"                        # each project keeps its own record


def test_the_job_refuses_settings_that_do_not_invest_and_bad_settings(hub, monkeypatch):
    monkeypatch.setattr(ce, "_milp_engine", FakeEngine())
    for bad, match in (({"invest": False}, "invest"), ({"k": 0}, "k"), ({"profile": "nowhere"}, "nowhere")):
        with pytest.raises(HTTPException) as exc:
            ce.start_milp_job(hub, {**SETTINGS, **bad})
        assert exc.value.status_code == 422 and match in exc.value.detail
    assert ce.milp_job_status(hub) is None and ce._MILP_SLOT["active"] is None


def test_a_failed_least_cost_run_frees_the_slot_and_leaves_no_record(hub, monkeypatch):
    monkeypatch.setattr(ce, "_milp_engine", FakeEngine())
    (ce.campus_dir(hub) / ce.CAMPUS_FILE).unlink()
    with pytest.raises(HTTPException) as exc:
        ce.start_milp_job(hub, SETTINGS)
    assert exc.value.status_code == 422
    assert ce.milp_job_status(hub) is None and ce._MILP_SLOT["active"] is None


# ── the least-cost run first, then a separate run directory ─────────────────

def test_the_least_cost_run_is_made_first_when_there_is_none(hub, monkeypatch):
    engine = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    assert ce.get_state(hub)["results"] is None
    ce.start_milp_job(hub, SETTINGS)
    state = ce.get_state(hub)
    assert state["results"]["investment"] and state["settings"]["pf"] == 0.95
    finish(hub, engine)


def test_a_fresh_least_cost_run_with_the_same_settings_is_reused_and_otherwise_rerun(hub, monkeypatch):
    ce.run(hub, SETTINGS)
    runs = []
    real_run = ce.run
    monkeypatch.setattr(ce, "run", lambda p, s: runs.append(s) or real_run(p, s))
    for settings, expect in ((SETTINGS, 0), ({**SETTINGS, "margin": 0.3}, 1)):
        engine = FakeEngine(iterations=1)
        monkeypatch.setattr(ce, "_milp_engine", engine)
        ce.start_milp_job(hub, settings)
        finish(hub, engine)
        assert len(runs) == expect, settings
    # a stale least-cost run is run again, even with the same settings
    spec = yaml.safe_load(ce.get_state(hub)["campus_yaml"])
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 2500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    engine = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, {**SETTINGS, "margin": 0.3})
    finish(hub, engine)
    assert len(runs) == 2


def test_the_job_writes_into_run_milp_and_never_touches_the_least_cost_files(hub, monkeypatch):
    from gridspine.drivers import campus_study as cs
    ce.run(hub, SETTINGS)
    run = ce.campus_dir(hub) / "run"
    before = digests(run)
    engine = FakeEngine()
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub, engine)
    assert digests(run) == before
    milp = ce.campus_dir(hub) / ce.MILP_SUBDIR
    assert engine.calls[0]["run_dir"] == milp
    for name in (cs.INVESTMENT_CSV, cs.COST_CSV, cs.COMPLIANCE_INVESTED_CSV, cs.MILP_HISTORY_CSV,
                 cs.MILP_COMPARISON_CSV, ce.MILP_SUMMARY_FILE):
        assert (milp / name).is_file(), name
    # the MILP buys from the library the least-cost run used, with its settings
    assert engine.calls[0]["library"] == milp / ce.USED_LIBRARY_FILE
    assert (milp / ce.USED_LIBRARY_FILE).read_bytes() == (run / ce.USED_LIBRARY_FILE).read_bytes()
    kw = engine.calls[0]
    assert kw["pf"] == 0.95 and kw["profile"] == "eu_rfg_dcc_ce" and kw["pcc_switchgear"] is True
    assert kw["criteria"].margin == 0.2 and kw["criteria"].n_minus_1 is True
    assert engine.netcdf_free is True                         # the thread never holds the NetCDF lock


def test_the_operator_owned_pcc_switchgear_setting_reaches_the_milp(hub, monkeypatch):
    engine = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, {**SETTINGS, "pcc_switchgear_by_operator": True})
    finish(hub, engine)
    assert engine.calls[0]["pcc_switchgear"] is False


# ── cancel ──────────────────────────────────────────────────────────────────

def test_cancel_ends_the_job_as_cancelled_and_keeps_its_best_result(hub, monkeypatch):
    engine = FakeEngine(iterations=5)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    engine.step.release()
    wait_until(lambda: record_of(hub)["iteration"] == 1)
    out = ce.cancel_milp_job(hub)
    assert out == {"state": "running", "cancelling": True}
    finish(hub, engine, steps=1)
    status = ce.milp_job_status(hub)
    assert status["state"] == "cancelled" and status["stop"] == "cancelled" and status["iteration"] == 1
    milp = ce.get_state(hub)["milp"]
    assert milp["results"]["stop"] == "cancelled"
    assert milp["results"]["summary"]["iterations"] == 1
    c8 = milp["results"]["summary"]["c8_cost"]
    assert milp["results"]["summary"]["milp_cost"] == pytest.approx(c8 - 1)         # the best so far
    assert [r["iteration"] for r in milp["results"]["history"]] == [0, 1]
    assert ce.cancel_milp_job(hub) == {"state": "cancelled", "cancelling": False}  # idempotent once ended


def test_cancel_without_a_job_is_404(hub):
    with pytest.raises(HTTPException) as exc:
        ce.cancel_milp_job(hub)
    assert exc.value.status_code == 404


def test_a_failed_job_leaves_no_results_and_frees_the_slot(hub, monkeypatch):
    engine = FakeEngine(fail=True)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub, engine)
    status = ce.milp_job_status(hub)
    assert status["state"] == "failed" and "gave up" in status["error"]
    assert ce.get_state(hub)["milp"]["results"] is None
    from gridspine.drivers import campus_study as cs
    milp = ce.campus_dir(hub) / ce.MILP_SUBDIR
    assert not (milp / cs.INVESTMENT_CSV).exists() and not (milp / cs.COST_CSV).exists()   # no half a result
    engine2 = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine2)
    assert ce.start_milp_job(hub, SETTINGS)["state"] == "running"
    finish(hub)


def test_a_new_job_clears_the_previous_results_until_it_has_its_own(hub, monkeypatch):
    engine = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub)
    assert ce.get_state(hub)["milp"]["results"] is not None
    engine2 = FakeEngine(iterations=3)
    monkeypatch.setattr(ce, "_milp_engine", engine2)
    ce.start_milp_job(hub, SETTINGS)
    assert ce.get_state(hub)["milp"]["results"] is None
    finish(hub, engine2)
    assert ce.get_state(hub)["milp"]["results"]["summary"]["iterations"] == 2


# ── the state: results beside the least-cost ones, and staleness ────────────

def test_the_state_carries_the_milp_results_beside_the_least_cost_ones(hub, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    running = ce.get_state(hub)["milp"]
    assert running["status"]["state"] == "running" and running["results"] is None
    finish(hub, engine)
    state = ce.get_state(hub)
    m = state["milp"]
    assert m["status"]["state"] == "done" and m["stale"] is False
    res = m["results"]
    assert set(res) == {"investment", "cost", "compliance_invested", "history", "comparison", "summary",
                        "fallback", "stop"}
    assert res["investment"] and res["cost"] and res["compliance_invested"]
    assert [r["iteration"] for r in res["history"]] == [0, 1, 2]
    assert res["comparison"][0]["milp_choice"] == "1 x B"
    assert res["fallback"] is None and res["stop"] == "converged"
    assert res["summary"]["milp_cost"] == pytest.approx(res["summary"]["c8_cost"] - 2)
    # the least-cost results are still the least-cost run's
    assert state["results"]["investment"] == ce._read_csv(ce.campus_dir(hub) / "run" / "campus_investment.csv")


def test_a_fallback_reaches_the_state_with_its_reason(hub, monkeypatch):
    engine = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub)
    res = ce.get_state(hub)["milp"]["results"]
    assert res["fallback"] == "no AC-feasible point is cheaper than C8's" and res["summary"]["fallback"] is True


@pytest.fixture
def milp_done(hub, monkeypatch):
    engine = FakeEngine(iterations=1)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub)
    assert ce.get_state(hub)["milp"]["stale"] is False
    return hub


def test_the_milp_turns_stale_when_the_campus_changes(milp_done):
    spec = yaml.safe_load(ce.get_state(milp_done)["campus_yaml"])
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 2500.0
    ce.save_campus(milp_done, yaml.safe_dump(spec))
    assert ce.get_state(milp_done)["milp"]["stale"] is True


def test_the_milp_turns_stale_when_the_library_changes(milp_done):
    from tests.test_campus_electrical_service import library_with, scaled_capex
    ce.save_library(milp_done, library_with(scaled_capex(2.0)))
    assert ce.get_state(milp_done)["milp"]["stale"] is True


def test_the_milp_turns_stale_when_the_least_cost_run_is_made_again_with_other_settings(milp_done):
    ce.run(milp_done, {**SETTINGS, "margin": 0.3})
    state = ce.get_state(milp_done)
    assert state["stale"] is False and state["milp"]["stale"] is True


def test_the_milp_stays_fresh_when_the_least_cost_run_is_made_again_alike(milp_done):
    ce.run(milp_done, SETTINGS)
    assert ce.get_state(milp_done)["milp"]["stale"] is False


def test_the_milp_turns_stale_when_its_basis_is_missing(milp_done):
    (ce.campus_dir(milp_done) / ce.MILP_SUBDIR / ce.MILP_BASIS_FILE).unlink()
    assert ce.get_state(milp_done)["milp"]["stale"] is True


def test_the_milp_job_refuses_a_project_of_another_kind(user_and_db):  # noqa: F811
    import uuid
    from db.models import Project
    from services import gridspine_service as gs
    db, user = user_and_db
    created = gs.create_study(db, user, "Planning Study", config={"hours": 24, "k": 1, "window": 24, "overlap": 0})
    study = db.get(Project, uuid.UUID(created["id"]))
    for action in (lambda p: ce.start_milp_job(p, {}), ce.milp_job_status, ce.cancel_milp_job):
        with pytest.raises(HTTPException) as exc:
            action(study)
        assert exc.value.status_code == 409


# ── one real MILP on the mini hub ───────────────────────────────────────────

def test_the_real_milp_runs_as_a_job_on_the_mini_hub(hub):
    t0 = time.time()
    rec = ce.start_milp_job(hub, SETTINGS)
    assert rec["state"] == "running"
    record_of(hub)["thread"].join(timeout=240)
    elapsed = time.time() - t0
    status = ce.milp_job_status(hub)
    assert status["state"] == "done", status
    res = ce.get_state(hub)["milp"]["results"]
    assert res["stop"] in ("converged", "stalled", "delta_floor", "max_iter")
    assert res["history"][0]["iteration"] == 0 and status["iteration"] == res["history"][-1]["iteration"]
    assert res["summary"]["milp_cost"] <= res["summary"]["c8_cost"] + 1e-6
    assert set(r["status_with_measures"] for r in res["compliance_invested"]) <= {"pass", "not_rated"}
    print(f"\nreal MILP job on the mini hub: {elapsed:.1f} s, {status['iteration']} iteration(s), stop {res['stop']}")


# ── the copilot's read (campus_get_milp) ────────────────────────────────────

def test_the_copilots_read_carries_the_status_the_summary_and_the_caveats(hub, monkeypatch):
    empty = ce.get_milp(hub)
    assert empty["status"] is None and empty["summary"] is None and empty["stale"] is False
    engine = FakeEngine(iterations=2)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub, engine)
    out = ce.get_milp(hub)
    assert out["status"]["state"] == "done" and "thread" not in out["status"]
    assert out["summary"]["iterations"] == 1 and out["stop"] == "converged" and out["fallback"] is None
    assert out["comparison"][0]["milp_choice"] == "1 x B" and out["stale"] is False
    assert any("inverter" in n for n in out["notes"]) and any("placeholder" in n for n in out["notes"])
