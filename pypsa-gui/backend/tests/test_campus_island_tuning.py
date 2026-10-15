"""Recommended GFM settings on demand (E2E demo plan 2026-10-14 §7.2, slice 5,
GFM-b): gridspine's ``gfm_settings_campus`` run on the last campus run as a
``campus`` job of the solve queue (the gatekeeper: on the solver's job and
progress path), read back with whether it is still the run's.

The recommender is replaced by a fast stand-in that writes what gridspine
writes, so the queue, the claim and the persisted status are real while the
search is not (``tests/gridspine/test_gfm_tuning.py`` covers it).
"""
import json
import os
import time
import uuid

import pytest
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import solve_queue as sq
from tests.test_campus_run_queue import _clean_queue, _job, _wait, hub  # noqa: F401

REC = {"label": "heuristic screening", "ledger": ["droop_pct 1-10 % (assumed)"],
       "status": "not_achievable", "achievable": False, "changed": False,
       "settings": {"bess_new_2030": {"model": "gfm_droop",
                                      "current": {"droop_pct": 4.0, "tau_f_s": 0.1, "i_max_pu": 1.2},
                                      "recommended": {"droop_pct": 2.0, "tau_f_s": 0.5,
                                                      "i_max_pu": 1.2}}},
       "cases": [{"key": "2030 h5 seamless E1", "ok_before": False, "binding_before": "rocof",
                  "ok_after": False, "binding_after": "rocof"}],
       "capacity": [{"case": "2030 h5 seamless E1", "kind": "inertia", "delta": 40.0,
                     "unit": "MW·s", "binding": "rocof", "derivation": "f0·ΔP/RoCoF",
                     "feasible": True, "note": ""}],
       "comparison": [], "simulations": 32, "max_simulations": 400, "margin": 0.1,
       "bounds": {}, "allow_i_max": False, "notes": [], "excluded": ["E4"]}


def _run_dir(project):
    d = ce.campus_dir(project) / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _frequency_checked(project):
    (_run_dir(project) / ce.cs.FREQ_EVENTS_CSV).write_text("period,hour,event\n")


def fake_recommend(seen, *, fail=None):
    def recommend(run_dir, *, allow_i_max=False, max_sims=400):
        seen.append({"run_dir": run_dir, "allow_i_max": allow_i_max})
        if fail:
            raise ce.ContractError(fail)
        (run_dir / ce.cs.GFM_SETTINGS_JSON).write_text(json.dumps(REC))
        return REC
    return recommend


def test_without_a_recommendation_it_is_not_run(hub):  # noqa: F811
    _db, _user, project = hub
    out = cit.get_settings(project)
    assert out["state"] == "not_run" and out["recommendation"] is None
    assert "Recommend" in out["reason"]


def test_it_needs_the_runs_frequency_check(hub, monkeypatch):  # noqa: F811
    _db, user, project = hub
    monkeypatch.setattr(cit, "recommend", lambda *a, **k: pytest.fail("never queued"))
    with pytest.raises(HTTPException) as exc:
        cit.enqueue_settings(project, user)
    assert exc.value.status_code == 422 and "frequency check" in exc.value.detail
    assert sq.solve_queue.list_jobs() == []


def test_the_recommendation_is_a_campus_job_and_reads_back(hub, monkeypatch):  # noqa: F811
    _db, user, project = hub
    _frequency_checked(project)
    seen = []
    monkeypatch.setattr(cit, "recommend", fake_recommend(seen))
    job = cit.enqueue_settings(project, user, allow_i_max=True)
    assert job["kind"] == sq.KIND_CAMPUS
    assert _wait(lambda: _job(job["id"])["status"] == "completed"), _job(job["id"])
    (call,) = seen
    assert call["allow_i_max"] is True
    assert str(call["run_dir"]) == str(_run_dir(project))
    state = ce.run_job_state(project)
    assert state["stage"] == cit.STAGE and state["stage_done"] == state["stage_total"]
    out = cit.get_settings(project)
    assert out["state"] == "ready" and out["recommendation"] == REC
    assert out["recommendation"]["label"] == "heuristic screening"


def test_a_newer_frequency_check_makes_it_stale(hub, monkeypatch):  # noqa: F811
    _db, _user, project = hub
    _frequency_checked(project)
    run = _run_dir(project)
    (run / ce.cs.GFM_SETTINGS_JSON).write_text(json.dumps(REC))
    later = time.time() + 5
    os.utime(run / ce.cs.FREQ_EVENTS_CSV, (later, later))
    out = cit.get_settings(project)
    assert out["state"] == "stale" and out["recommendation"] == REC
    assert "campus run" in out["reason"]


def test_a_campus_rerun_drops_an_old_recommendation():
    assert ce.cs.GFM_SETTINGS_JSON in ce._island_hours_files()


def test_a_second_job_while_one_is_queued_is_a_409(hub, monkeypatch):  # noqa: F811
    import threading

    _db, user, project = hub
    _frequency_checked(project)
    gate = threading.Event()

    def slow(run_dir, **kw):
        gate.wait(5)
        return fake_recommend([])(run_dir, **kw)
    monkeypatch.setattr(cit, "recommend", slow)
    first = cit.enqueue_settings(project, user)
    with pytest.raises(HTTPException) as exc:
        cit.enqueue_settings(project, user)
    assert exc.value.status_code == 409
    gate.set()
    assert _wait(lambda: _job(first["id"])["status"] == "completed")


def test_a_failing_recommendation_is_a_failed_job_with_its_reason(hub, monkeypatch):  # noqa: F811
    _db, user, project = hub
    _frequency_checked(project)
    monkeypatch.setattr(cit, "recommend", fake_recommend([], fail="the run has no island units"))
    job = cit.enqueue_settings(project, user)
    assert _wait(lambda: _job(job["id"])["status"] == "failed")
    assert "the run has no island units" in _job(job["id"])["error"]
    assert cit.get_settings(project)["state"] == "not_run"


def test_the_campus_run_itself_is_unchanged(hub, monkeypatch):  # noqa: F811
    """A campus job without the action is the run, as before."""
    _db, user, project = hub
    seen = []
    monkeypatch.setattr(cit, "recommend", lambda *a, **k: pytest.fail("not a GFM job"))
    monkeypatch.setattr(ce, "run", lambda p, s, **kw: seen.append(s) or {})
    job = ce.enqueue_run(project, {}, user)
    assert _wait(lambda: _job(job["id"])["status"] == "completed")
    assert len(seen) == 1 and "action" not in seen[0]
    assert uuid.UUID(job["id"])


# ── the routes ──────────────────────────────────────────────────────────────


from db.models import Project  # noqa: E402
from tests.test_campus_island_service import hub as router_hub  # noqa: E402,F401
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: E402,F401

_BASE = "/api/campus-electrical/Router Hub/island/gfm-settings"


def test_the_read_route_answers_the_settings(client, router_hub, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(cit, "get_settings", lambda *a, **kw: calls.append((a, kw)) or {"ok": 1})
    resp = client.get(_BASE)
    assert resp.status_code == 200 and resp.json() == {"ok": 1}
    (args, kw), = calls
    assert isinstance(args[0], Project) and args[0].name == "Router Hub" and kw == {}


@pytest.mark.parametrize("body, allow", [(None, False), ({"allow_i_max": True}, True)])
def test_the_start_route_queues_the_job(client, router_hub, seeded_identity, monkeypatch,  # noqa: F811
                                        body, allow):
    calls = []
    monkeypatch.setattr(cit, "enqueue_settings", lambda *a, **kw: calls.append((a, kw)) or {"id": "j"})
    resp = client.post(_BASE, json=body) if body is not None else client.post(_BASE)
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"id": "j"}
    (args, kw), = calls
    assert isinstance(args[0], Project) and args[1].id == seeded_identity["user_id"]
    assert kw == {"allow_i_max": allow}


def test_the_start_is_refused_under_another_users_lock_and_the_read_is_not(
        client, router_hub, same_org_other_user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cit, "get_settings", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(cit, "enqueue_settings", lambda *a, **kw: {"ok": True})
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    assert _is_lock_refusal(same_org_other_user.post(_BASE))
    assert same_org_other_user.get(_BASE).status_code == 200
