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
from pathlib import Path

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
    def recommend(run_dir, *, allow_i_max=False, max_sims=400, should_stop=None):
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


def test_a_campus_run_stale_against_its_inputs_makes_it_stale(hub, monkeypatch):  # noqa: F811
    """#303 review: a campus file, network or library changed since the run
    (``ce._stale``) makes the recommendation stale too, without a rerun."""
    _db, _user, project = hub
    _frequency_checked(project)
    (_run_dir(project) / ce.cs.GFM_SETTINGS_JSON).write_text(json.dumps(REC))
    later = time.time() + 5
    os.utime(_run_dir(project) / ce.cs.GFM_SETTINGS_JSON, (later, later))
    assert cit.get_settings(project)["state"] == "ready"
    monkeypatch.setattr(ce, "_stale", lambda p, d: True)
    out = cit.get_settings(project)
    assert out["state"] == "stale" and "campus file" in out["reason"]


def test_an_unreadable_recommendation_is_stale_not_a_500(hub):  # noqa: F811
    _db, _user, project = hub
    _frequency_checked(project)
    (_run_dir(project) / ce.cs.GFM_SETTINGS_JSON).write_text('{"label": "heuri')
    out = cit.get_settings(project)
    assert out["state"] == "stale" and out["recommendation"] is None
    assert "could not be read" in out["reason"]


def test_a_collapse_on_disk_reads_back_as_null_not_a_500(hub):  # noqa: F811
    """Guided smoke 2026-10-16 (H2 hub): a recommendation written before
    gridspine wrote strict JSON holds ``Infinity`` (a collapse); it reads back
    with null there, so the route can answer it."""
    _db, _user, project = hub
    _frequency_checked(project)
    rec = {**REC, "capacity": [{**REC["capacity"][0], "delta": float("inf"), "feasible": False}],
           "comparison": [{"unit": "bess_new_2030", "model": "gfm_droop", "worst_rocof_hz_per_s": float("inf"),
                           "worst_nadir_hz": float("-inf"), "worst_rocof0_hz_per_s": 0.4, "ok": False,
                           "note": None}]}
    text = json.dumps(rec)
    assert "Infinity" in text
    (_run_dir(project) / ce.cs.GFM_SETTINGS_JSON).write_text(text)
    got = cit.get_settings(project)["recommendation"]
    json.dumps(got, allow_nan=False)
    assert got["capacity"][0]["delta"] is None
    assert got["comparison"][0]["worst_rocof_hz_per_s"] is None and got["comparison"][0]["worst_nadir_hz"] is None


#: The live H2 hub run's recommendation as gridspine wrote it before the fix
#: (Guided smoke 2026-10-16; master 622149df, k=3), trimmed to three
#: collapsing cases, two passing ones and their capacity rows: ``Infinity``
#: in capacity[].delta and comparison[].worst_rocof_hz_per_s / worst_nadir_hz.
H2_PRE_FIX = Path(__file__).parent / "fixtures" / "gfm_settings_h2_collapse_pre_fix.json"


def test_the_live_h2_collapse_reads_back_renders_and_answers_200(hub):  # noqa: F811
    from starlette.responses import JSONResponse

    from services.reports import chapters

    _db, _user, project = hub
    _frequency_checked(project)
    text = H2_PRE_FIX.read_text()
    assert "Infinity" in text
    (_run_dir(project) / ce.cs.GFM_SETTINGS_JSON).write_text(text)
    later = time.time() + 5
    os.utime(_run_dir(project) / ce.cs.GFM_SETTINGS_JSON, (later, later))
    got = cit.get_settings(project)
    rec = got["recommendation"]
    assert rec["status"] == "not_achievable"
    # what routers/campus_electrical.get_gfm_settings answers: it rendered a 500 before
    body = json.loads(JSONResponse(content=got).body, parse_constant=lambda c: pytest.fail(c))
    assert body["recommendation"]["capacity"][0]["delta"] is None
    assert {c["worst_nadir_hz"] for c in body["recommendation"]["comparison"]} == {None}
    # the report reads the same get_settings (routers/reports.py) and names the collapse
    tables = chapters.island_tables(chapters.island_payload({"gfm": got}), "island")
    rows = tables["island_gfm_capacity"].rows
    assert rows and all("needs a former" in r[2] and "+" not in r[2] for r in rows)
    assert all("frequency collapse" in r[3] for r in rows)


def test_no_job_while_the_cuts_are_being_applied(hub, monkeypatch):  # noqa: F811
    """#303 review: the cuts' trials rerun the campus study outside the queue,
    replacing the frequency check the search would read."""
    from services import campus_island_cuts as cic

    _db, user, project = hub
    _frequency_checked(project)
    monkeypatch.setattr(cic, "_running", lambda record: True)
    with pytest.raises(HTTPException) as exc:
        cit.enqueue_settings(project, user)
    assert exc.value.status_code == 409 and "frequency cuts" in exc.value.detail
    assert sq.solve_queue.list_jobs() == []


def test_an_abort_mid_search_stops_it_and_writes_nothing(hub, monkeypatch):  # noqa: F811
    """#303 review: the abort reaches the search (asked before every
    simulation); the job reads aborted and there is no recommendation."""
    import threading

    from gridspine.static.gfm_tuning import SearchStopped

    _db, user, project = hub
    _frequency_checked(project)
    started, sims = threading.Event(), []

    def searching(run_dir, *, allow_i_max=False, max_sims=400, should_stop=None):
        started.set()
        for _ in range(max_sims):
            if should_stop():
                raise SearchStopped("stopped")
            sims.append(1)
            time.sleep(0.01)
        (run_dir / ce.cs.GFM_SETTINGS_JSON).write_text(json.dumps(REC))
    monkeypatch.setattr(cit, "recommend", searching)
    job = cit.enqueue_settings(project, user)
    assert started.wait(10)
    sq.solve_queue.abort(uuid.UUID(job["id"]))
    assert _wait(lambda: _job(job["id"])["status"] == "aborted"), _job(job["id"])
    assert len(sims) < cit.MAX_SIMS
    assert cit.get_settings(project)["state"] == "not_run"


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


# ── UX review N7: does the hub solve read island rows? ───────────────────────


def _set_island_constraints(project, on):
    from services import project_registry
    (project_registry.project_dir(project) / "solver_config.json").write_text(
        json.dumps({"island_constraints": on}))


def test_the_state_says_whether_the_hubs_island_rows_are_on_now_in_every_state(hub, monkeypatch):  # noqa: F811
    """"Not achievable: capacity is needed" means nothing to a user who does not know the hub solve
    ignores island constraints. The flag is read from the solver settings as they are now (the
    gatekeeper: it says "now", not "when the hub was last solved") and sits in every state."""
    _db, _user, project = hub
    _set_island_constraints(project, False)
    assert cit.get_settings(project)["island_rows_now"] is False            # not_run
    _set_island_constraints(project, True)
    assert cit.get_settings(project)["island_rows_now"] is True

    _frequency_checked(project)
    (_run_dir(project) / ce.cs.GFM_SETTINGS_JSON).write_text(json.dumps(REC))
    ready = cit.get_settings(project)
    assert ready["state"] in ("ready", "stale") and ready["island_rows_now"] is True
    _set_island_constraints(project, False)
    assert cit.get_settings(project)["island_rows_now"] is False


def test_a_missing_solver_config_means_the_rows_are_off(hub):  # noqa: F811
    _db, _user, project = hub
    from services import project_registry
    (project_registry.project_dir(project) / "solver_config.json").unlink(missing_ok=True)
    assert cit.get_settings(project)["island_rows_now"] is False
