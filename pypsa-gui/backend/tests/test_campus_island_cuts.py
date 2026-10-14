"""
I7 of the campus island plan: the frequency cuts, previewed and applied
(owner Q6: manual, with a preview; QA: one click runs the loop).

- ``get_cuts(project)``: the cuts the last campus run proposes
  (``campus_freq_cuts.csv``, gridspine), each feasible seamless one with its
  primal estimate; the applied cuts; the trials; the limit the panel states.
- ``apply_loop``: apply the proposed cuts, re-solve the hub, rerun the campus
  study, and again with the cuts the rerun proposes, until nothing fails,
  nothing more can be cut, or N = 3 trials. Every trial is kept.
- ``remove_cut`` / ``clear_cuts``: the user takes cuts back.

The solve and the rerun are injected here, so the loop is held to hand
cases; the real ones are the solve queue and the campus Run.
"""
import json
import threading

import pandas as pd
import pytest
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import campus_island_cuts as cic
from services import project_registry
from tests.test_campus_island_service import _config, project, user_and_db  # noqa: F401


def _row(kind="inertia", required=25.0, current=12.5, hour=0, period=2030, scenario="seamless",
         feasible=True, note=""):
    return {"period": period, "hour": hour, "scenario": scenario, "event": "E1", "kind": kind,
            "required": required if feasible else float("inf"), "current": current,
            "delta": (required - current) if feasible else float("inf"),
            "unit": {"inertia": "MW·s", "droop_gain": "MW/Hz"}.get(kind, "MW"),
            "binding": "transition RoCoF", "derivation": "rocof: f0·ΔP/RoCoF_max",
            "heuristic": kind != "droop_gain", "feasible": feasible, "note": note}


def _propose(project, rows):  # noqa: F811
    run = ce.campus_dir(project) / "run"
    run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=list(cic.CUT_COLUMNS)).to_csv(run / cic.FREQ_CUTS_CSV, index=False)


@pytest.fixture
def saved(project):  # noqa: F811
    ce.save_island(project, _config())
    return project


class Script:
    """The injected solve and rerun: each rerun proposes the next scripted
    cuts; each solve returns the next objective."""

    def __init__(self, project, proposals, objectives=None):
        self.project, self.proposals = project, list(proposals)
        self.objectives = list(objectives or [1000.0 * (i + 1) for i in range(len(self.proposals))])
        self.calls = []

    def solve(self, project):
        self.calls.append(("solve", json.loads((project_registry.project_dir(project)
                                                / cic.CUTS_FILE).read_text())["cuts"]))
        return self.objectives.pop(0)

    def rerun(self, project):
        self.calls.append(("rerun",))
        _propose(project, self.proposals.pop(0))


def _loop(project, script, stop=None):
    return cic.apply_loop(project, solve=script.solve, rerun=script.rerun,
                          stop_event=stop or threading.Event())


# ── the preview ─────────────────────────────────────────────────────────────


def test_without_a_run_there_is_nothing_proposed_and_the_limit_is_stated(saved):
    out = cic.get_cuts(saved)
    assert out["proposed"] == [] and out["applied"] == [] and out["trials"] == []
    assert "bridge" in out["limit"] and "no cut" in out["limit"]
    assert out["max_trials"] == 3


def test_a_proposed_cut_carries_its_primal_estimate(saved):
    """The hand hub: the extendable GFM BESS at 100/MW gives 1.25 MW·s per
    MW, so 25 − 12.5 MW·s is 10 MW = 1000; the fixed genset is no candidate."""
    _propose(saved, [_row()])
    (cut,) = cic.get_cuts(saved)["proposed"]
    assert cut["candidate"] == "bess"
    assert cut["estimate"] == pytest.approx(1000.0)
    assert cut["label"].startswith("estimate")


def test_a_cut_that_cannot_be_applied_is_shown_without_an_estimate(saved):
    _propose(saved, [_row(feasible=False, note="collapse: no grid-forming unit"),
                     _row(scenario="planned", hour=1)])
    infeasible, planned = cic.get_cuts(saved)["proposed"]
    assert infeasible["estimate"] is None and infeasible["applicable"] is False
    assert "collapse" in infeasible["reason"]
    assert planned["estimate"] is None and planned["applicable"] is False
    assert "seamless" in planned["reason"]


def test_a_cuts_file_off_the_contract_is_refused_by_name(saved):
    run = ce.campus_dir(saved) / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / cic.FREQ_CUTS_CSV).write_text("period,hour\n0,1\n")
    with pytest.raises(HTTPException) as exc:
        cic.get_cuts(saved)
    assert exc.value.status_code == 422 and cic.FREQ_CUTS_CSV in exc.value.detail


# ── the loop (owner QA) ─────────────────────────────────────────────────────


def test_a_case_that_fails_once_passes_after_one_cut(saved):
    _propose(saved, [_row()])
    script = Script(saved, proposals=[[]], objectives=[2000.0])
    out = _loop(saved, script)
    assert script.calls[0] == ("solve", [{"period": 2030, "hour": 0, "scenario": "seamless",
                                          "kind": "inertia", "required": 25.0}])
    assert [c[0] for c in script.calls] == ["solve", "rerun"]
    assert out["stopped"] == "all pass"
    (trial,) = out["trials"]
    assert trial["trial"] == 1 and trial["objective"] == 2000.0 and trial["verdict"] == "pass"
    assert [c["kind"] for c in trial["added"]] == ["inertia"]
    assert [c["required"] for c in cic.get_cuts(saved)["applied"]] == [25.0]


def test_the_loop_stops_at_n_trials(saved):
    _propose(saved, [_row(required=20.0)])
    script = Script(saved, proposals=[[_row(required=30.0)], [_row(required=40.0)], [_row(required=50.0)]])
    out = _loop(saved, script)
    assert len(out["trials"]) == 3 and out["stopped"] == "N reached"
    assert [t["verdict"] for t in out["trials"]] == ["fails at 1 hour"] * 3
    # the tightest cut per hour and kind is what the solve sees
    assert script.calls[-2] == ("solve", [{"period": 2030, "hour": 0, "scenario": "seamless",
                                           "kind": "inertia", "required": 40.0}])
    assert [c["required"] for c in cic.get_cuts(saved)["applied"]] == [40.0]


def test_the_loop_stops_when_what_still_fails_has_no_cut(saved):
    _propose(saved, [_row()])
    script = Script(saved, proposals=[[_row(feasible=False, note="collapse")]])
    out = _loop(saved, script)
    assert out["stopped"] == "no cut can fix what still fails"
    assert out["trials"][0]["verdict"] == "fails at 1 hour"


def test_with_nothing_to_apply_the_loop_is_refused_before_any_solve(saved):
    for rows in ([], [_row(feasible=False)], [_row(scenario="planned")]):
        _propose(saved, rows)
        script = Script(saved, proposals=[])
        with pytest.raises(HTTPException) as exc:
            _loop(saved, script)
        assert exc.value.status_code == 422 and "nothing to apply" in exc.value.detail
        assert script.calls == []


def test_a_cut_already_met_by_an_applied_one_is_not_added_again(saved):
    _propose(saved, [_row(required=30.0)])
    _loop(saved, Script(saved, proposals=[[]]))
    _propose(saved, [_row(required=25.0), _row(kind="droop_gain", required=9.0, current=5.0)])
    script = Script(saved, proposals=[[]])
    out = _loop(saved, script)
    assert [c["kind"] for c in out["trials"][-1]["added"]] == ["droop_gain"]
    assert sorted((c["kind"], c["required"]) for c in cic.get_cuts(saved)["applied"]) == [
        ("droop_gain", 9.0), ("inertia", 30.0)]
    assert len(cic.get_cuts(saved)["trials"]) == 2           # the earlier loop's trial is kept


def test_a_stop_ends_the_loop_after_the_trial_in_hand(saved):
    _propose(saved, [_row(required=20.0)])
    stop = threading.Event()
    script = Script(saved, proposals=[[_row(required=30.0)], [_row(required=40.0)]])
    real_rerun = script.rerun
    script.rerun = lambda p: (real_rerun(p), stop.set())
    out = _loop(saved, script, stop=stop)
    assert len(out["trials"]) == 1 and out["stopped"] == "stopped by the user"


# ── taking cuts back ────────────────────────────────────────────────────────


def test_a_cut_can_be_removed_and_all_cleared(saved):
    _propose(saved, [_row(), _row(kind="droop_gain", required=9.0, current=5.0, hour=1)])
    _loop(saved, Script(saved, proposals=[[]]))
    cic.remove_cut(saved, period=2030, hour=0, kind="inertia")
    assert [c["kind"] for c in cic.get_cuts(saved)["applied"]] == ["droop_gain"]
    cic.clear_cuts(saved)
    assert not (project_registry.project_dir(saved) / cic.CUTS_FILE).exists()
    assert cic.get_cuts(saved)["applied"] == [] and cic.get_cuts(saved)["trials"] == []


def test_removing_a_cut_that_is_not_applied_is_a_404(saved):
    with pytest.raises(HTTPException) as exc:
        cic.remove_cut(saved, period=2030, hour=0, kind="inertia")
    assert exc.value.status_code == 404


# ── the apply job: the loop in a thread, the solve through the queue ────────


import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _ready(project, island_constraints=True, settings=True):  # noqa: F811
    d = project_registry.project_dir(project)
    (d / "solver_config.json").write_text(json.dumps({"island_constraints": island_constraints}))
    if settings:
        (ce.campus_dir(project) / ce.SETTINGS_FILE).write_text(json.dumps({"k": 1, "invest": False}))


def _wait_done(project, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rec = cic.apply_status(project)
        if rec and rec["state"] != "running":
            return rec
        time.sleep(0.02)
    raise AssertionError("the apply job did not finish")


def test_the_job_is_refused_when_the_solve_would_not_read_the_cuts(saved):
    _propose(saved, [_row()])
    _ready(saved, island_constraints=False)
    with pytest.raises(HTTPException) as exc:
        cic.start_apply_job(saved, user_id=None)
    assert exc.value.status_code == 422 and "island constraints" in exc.value.detail
    assert cic.apply_status(saved) is None


def test_the_job_is_refused_without_a_campus_run_to_rerun(saved):
    _propose(saved, [_row()])
    _ready(saved, settings=False)
    with pytest.raises(HTTPException) as exc:
        cic.start_apply_job(saved, user_id=None)
    assert exc.value.status_code == 422 and "campus study" in exc.value.detail


def test_the_job_is_refused_with_nothing_to_apply(saved):
    _propose(saved, [])
    _ready(saved)
    with pytest.raises(HTTPException) as exc:
        cic.start_apply_job(saved, user_id=None)
    assert exc.value.status_code == 422 and "nothing to apply" in exc.value.detail
    assert cic.apply_status(saved) is None


def test_the_job_runs_the_loop_and_records_its_end(saved, monkeypatch):
    _propose(saved, [_row()])
    _ready(saved)
    reruns = []
    monkeypatch.setattr(cic, "_queue_solve", lambda project, user_id, stop: 2000.0)
    monkeypatch.setattr(cic, "_rerun", lambda project, settings: (reruns.append(settings), _propose(project, [])))
    rec = cic.start_apply_job(saved, user_id=None)
    assert rec["state"] == "running" and "thread" not in rec
    rec = _wait_done(saved)
    assert rec["state"] == "done" and rec["stopped"] == "all pass" and rec["trials"] == 1
    assert reruns == [{"k": 1, "invest": False}]               # the campus run's own settings
    assert cic.get_cuts(saved)["trials"][0]["objective"] == 2000.0


def test_a_second_job_for_the_project_is_a_409_while_one_runs(saved, monkeypatch):
    _propose(saved, [_row()])
    _ready(saved)
    gate, solving = threading.Event(), threading.Event()

    def solve(project, user_id, stop):
        solving.set()                            # the job has written its cuts by now
        return gate.wait(10) and 1.0

    monkeypatch.setattr(cic, "_queue_solve", solve)
    monkeypatch.setattr(cic, "_rerun", lambda project, settings: _propose(project, []))
    cic.start_apply_job(saved, user_id=None)
    assert solving.wait(10)
    try:
        with pytest.raises(HTTPException) as exc:
            cic.start_apply_job(saved, user_id=None)
        assert exc.value.status_code == 409
    finally:
        gate.set()
    _wait_done(saved)


def test_cancel_stops_the_loop_after_the_trial_in_hand(saved, monkeypatch):
    _propose(saved, [_row(required=20.0)])
    _ready(saved)
    gate = threading.Event()
    proposals = iter([[_row(required=30.0)], [_row(required=40.0)]])

    def solve(project, user_id, stop):
        gate.wait(10)
        return 1.0

    monkeypatch.setattr(cic, "_queue_solve", solve)
    monkeypatch.setattr(cic, "_rerun", lambda project, settings: _propose(project, next(proposals)))
    cic.start_apply_job(saved, user_id=None)
    cic.cancel_apply_job(saved)
    gate.set()
    rec = _wait_done(saved)
    assert rec["state"] == "cancelled" and rec["trials"] == 1


def test_a_failed_solve_fails_the_job_with_its_reason(saved, monkeypatch):
    _propose(saved, [_row()])
    _ready(saved)

    def boom(project, user_id, stop):
        raise RuntimeError("the hub solve ended failed: infeasible")

    monkeypatch.setattr(cic, "_queue_solve", boom)
    cic.start_apply_job(saved, user_id=None)
    rec = _wait_done(saved)
    assert rec["state"] == "failed" and "infeasible" in rec["error"]


def test_the_solve_goes_through_the_queue_with_the_projects_config(saved, monkeypatch):
    from services import solve_queue as sq
    from services.study import runner

    _ready(saved)
    seen = {}
    job = SimpleNamespace(status="completed", objective=1234.5, error=None)

    def enqueue_unique(name, **kw):
        seen.update(name=name, **kw)
        return job, True

    monkeypatch.setattr(sq.solve_queue, "enqueue_unique", enqueue_unique)
    monkeypatch.setattr(runner, "_wait", lambda j, stop: None)
    assert cic._queue_solve(saved, 7, threading.Event()) == 1234.5
    d = project_registry.project_dir(saved)
    assert seen["name"] == saved.name and seen["storage_dir"] == str(d)
    assert seen["project_key"] == project_registry.registry_key(saved)
    assert json.loads(seen["solver_config_json"]) == {"island_constraints": True}
    assert seen["enqueued_by_user_id"] == 7
    job.status, job.error = "failed", "infeasible"
    with pytest.raises(RuntimeError, match="infeasible"):
        cic._queue_solve(saved, 7, threading.Event())


# ── the routes ──────────────────────────────────────────────────────────────


from db.models import Project  # noqa: E402
from tests.test_campus_island_service import hub  # noqa: E402,F401
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: E402,F401

_BASE = "/api/campus-electrical/Router Hub/island/cuts"


@pytest.mark.parametrize("method, path, function, status, expected_kw", [
    ("get", "", "get_cuts", 200, {}),
    ("post", "/apply", "start_apply_job", 202, {"user_id": "user"}),
    ("get", "/apply", "apply_status", 200, {}),
    ("post", "/apply/cancel", "cancel_apply_job", 200, {}),
    ("delete", "/2030/3/inertia", "remove_cut", 200, {"period": 2030, "hour": 3, "kind": "inertia"}),
    ("delete", "", "clear_cuts", 200, {}),
])
def test_each_cut_route_calls_its_service_function_with_the_row(client, hub, seeded_identity, monkeypatch,  # noqa: F811
                                                               method, path, function, status, expected_kw):
    calls = []
    monkeypatch.setattr(cic, function, lambda *a, **kw: calls.append((a, kw)) or {"ok": function})
    resp = getattr(client, method)(f"{_BASE}{path}")
    assert resp.status_code == status, resp.text
    assert resp.json() == {"ok": function}
    (args, kw), = calls
    assert isinstance(args[0], Project) and args[0].name == "Router Hub"
    if expected_kw.get("user_id") == "user":
        expected_kw = {"user_id": seeded_identity["user_id"]}
    assert kw == expected_kw


def test_a_cut_kind_off_the_contract_is_422_before_the_service(client, hub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cic, "remove_cut", lambda *a, **kw: pytest.fail("not reached"))
    assert client.delete(f"{_BASE}/2030/3/damping").status_code == 422


def test_the_cut_writes_are_refused_under_another_users_lock_and_the_reads_are_not(
        client, hub, same_org_other_user, monkeypatch):  # noqa: F811
    for function in ("get_cuts", "start_apply_job", "apply_status", "cancel_apply_job", "remove_cut",
                     "clear_cuts"):
        monkeypatch.setattr(cic, function, lambda *a, **kw: {"ok": True})
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    other = same_org_other_user
    for method, path in (("post", "/apply"), ("post", "/apply/cancel"), ("delete", "/2030/3/inertia"),
                         ("delete", "")):
        resp = getattr(other, method)(f"{_BASE}{path}")
        assert _is_lock_refusal(resp), (method, path, resp.status_code, resp.text)
    for path in ("", "/apply"):
        assert other.get(f"{_BASE}{path}").status_code == 200


# ── closing the mutation survivors ──────────────────────────────────────────


def test_a_proposed_cut_equal_to_an_applied_one_is_not_added_again(saved):
    _propose(saved, [_row(required=25.0)])
    _loop(saved, Script(saved, proposals=[[]]))
    _propose(saved, [_row(required=25.0)])
    with pytest.raises(HTTPException) as exc:
        _loop(saved, Script(saved, proposals=[]))
    assert "nothing to apply" in exc.value.detail


def test_of_two_proposed_cuts_at_one_hour_and_kind_the_tighter_is_applied(saved):
    _propose(saved, [_row(required=25.0), _row(required=30.0), _row(required=20.0)])
    script = Script(saved, proposals=[[]])
    _loop(saved, script)
    assert [c["required"] for c in script.calls[0][1]] == [30.0]


def test_the_verdict_counts_hours_not_cuts(saved):
    _propose(saved, [_row()])
    script = Script(saved, proposals=[[_row(required=40.0), _row(kind="droop_gain", required=9.0, current=5.0),
                                       _row(hour=2, required=30.0)]], objectives=[1.0, 2.0])
    script.proposals.append([])
    out = cic.apply_loop(saved, solve=script.solve, rerun=script.rerun, stop_event=threading.Event())
    assert out["trials"][0]["verdict"] == "fails at 2 hours"


def test_the_cut_contract_is_gridspines():
    """pypsa-gui keeps the contract as literals (a frozen build may lack
    gridspine); they are gridspine's own."""
    from gridspine.drivers import campus_study as cs
    from gridspine.static import frequency as F
    from services.solver.island import CUT_KINDS

    assert cic.CUT_COLUMNS == F.CUT_COLUMNS
    assert cic.FREQ_CUTS_CSV == cs.FREQ_CUTS_CSV
    assert set(CUT_KINDS) == set(F.CUT_KINDS)
    assert F.CUT_SCENARIOS == ("seamless",)       # the scenarios the hub LP has 0⁺ rows for
