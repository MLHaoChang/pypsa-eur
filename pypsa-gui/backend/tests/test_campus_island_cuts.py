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
