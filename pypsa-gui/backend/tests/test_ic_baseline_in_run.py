# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study-harness issue 52: a run's investment case re-solves its baseline as a
counted reservation. `solve_baseline` reports the LPs it ran (0 on a cache
hit), the case's record keeps them (`baseline_solves`), and `run_ic` settles
the case's reserving audit row from that record, so `solves_used` is the real
LP count: plan + baseline on a miss, plan only on a hit. The concentrator's
@2 profile runs the case in its solve stage on that reservation."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from harness import run_ic
from services import assistant_tasks, run_envelope, run_profiles
from services.results import baseline as B

REF = "eh_concentrator_staged@2"
PLAN_ROW = {"event": "used", "tool": "solve_queue_enqueue", "call_id": "s"}
TICKET = ("t1", "solve.2", "a1")
CASE_ROW = {"event": "used", "tool": run_ic.TOOL, "call_id": "run-solve.2-0a1b2c3d",
            "reserved": run_envelope.CASE_BASELINE_SOLVES, "solves": run_envelope.CASE_BASELINE_SOLVES}


# ── the baseline reports the LPs it ran ─────────────────────────────────────


@pytest.fixture
def fake_solve(monkeypatch):
    """`solve_baseline` on a fake network and solver; each call's statuses
    are popped from `statuses` (one per LP)."""
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(B, "baseline_network", lambda n, plan, supplies: object())
    monkeypatch.setattr(B, "reference_supplies", lambda n, cfg: ())
    monkeypatch.setattr(B, "_key", lambda *a, **k: "key")
    monkeypatch.setattr(B, "_CACHE", {})
    monkeypatch.setattr(PyPSAService, "solve_target_context", staticmethod(lambda: None), raising=False)
    statuses, lps = [], []

    def solve(conf, base, *a, **k):
        lps.append(1)
        status = statuses.pop(0)
        return status, status

    plan = B.BaselinePlan(removed=(), kept=(("Generator", "genset_1"),), flags=())
    run = lambda: B.solve_baseline(SimpleNamespace(), SimpleNamespace(island_constraints=True), plan,  # noqa: E731
                                   solve=solve)
    return run, statuses, lps


def test_a_miss_reports_its_lp_and_a_hit_none(fake_solve):
    run, statuses, lps = fake_solve
    statuses.append("optimal")
    assert run().solves == 1 and len(lps) == 1
    hit = run()
    assert hit.solves == 0 and len(lps) == 1  # the cache: no LP ran
    assert run().solves == 0


def test_the_island_relaxed_retry_reports_both_lps(fake_solve):
    run, statuses, lps = fake_solve
    statuses.extend(["infeasible", "optimal"])
    got = run()
    assert got.solves == 2 == len(lps) <= run_envelope.CASE_BASELINE_SOLVES
    assert "baseline_island_rows_relaxed" in got.plan.flags


def test_a_refused_baseline_reports_the_lps_it_ran(fake_solve):
    run, statuses, lps = fake_solve
    statuses.extend(["infeasible", "infeasible"])
    with pytest.raises(B.BaselineRefused) as err:
        run()
    assert err.value.solves == 2 == len(lps)


# ── the case's build and its record carry them ──────────────────────────────


@pytest.mark.parametrize("outcome", ["miss", "hit", "refused", "no_baseline"])
def test_the_build_reports_the_baseline_solves_of_its_last_call(monkeypatch, outcome):
    """`_ic_build_case`'s build carries the LPs its baseline ran; the runner
    keeps them on the record (`_keep_baseline_solves`)."""
    from routers import results
    from services.finance.case import FinanceRefused
    from services.finance.investment_case_runner import _keep_baseline_solves
    from services.pypsa_service import PyPSAService
    from services.results import finance_case

    plan = B.BaselinePlan(removed=(), kept=() if outcome == "no_baseline" else (("G", "g"),), flags=())

    def solve_baseline(*a, **k):
        if outcome == "refused":
            raise B.BaselineRefused("baseline_not_established:infeasible", "", solves=1)
        return SimpleNamespace(solves=1 if outcome == "miss" else 0)

    monkeypatch.setattr(finance_case, "baseline_plan", lambda n, cfg, owner=None: plan)
    monkeypatch.setattr(B, "solve_baseline", solve_baseline)
    monkeypatch.setattr(finance_case, "build_finance_case", lambda *a, **k: SimpleNamespace(flags=()))
    monkeypatch.setattr("services.workflow_tools._network_copy", lambda n: n)
    monkeypatch.setattr("models.finance.FinanceInputs.model_validate", classmethod(lambda cls, raw: raw))
    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(lambda: threading.Lock()))
    build = results._ic_build_case(object(), SimpleNamespace(finance={}), owner=None, lost_load=None)
    assert build.baseline_solves == 0
    if outcome == "refused":
        with pytest.raises(FinanceRefused):
            build()
    else:
        build()
    record: dict = {}
    _keep_baseline_solves(record, build, threading.Lock())
    assert record == {"baseline_solves": {"miss": 1, "hit": 0, "refused": 1, "no_baseline": 0}[outcome]}


def test_a_build_that_does_not_say_leaves_the_record_alone():
    from services.finance.investment_case_runner import _keep_baseline_solves

    record: dict = {}
    _keep_baseline_solves(record, lambda: None, threading.Lock())
    unknown = lambda: None  # noqa: E731
    unknown.baseline_solves = None  # a solve that raised something unexpected
    _keep_baseline_solves(record, unknown, threading.Lock())
    assert record == {}


# ── run_ic settles the reservation from the record ──────────────────────────


@pytest.fixture
def envelope(monkeypatch):
    """The run's envelope in memory: the plan's solve and the case's
    reserving row, as `admit` records them."""
    held = {"audit": [dict(PLAN_ROW), dict(CASE_ROW)]}
    counted = []
    monkeypatch.setattr(run_envelope, "_load", lambda path: held)
    monkeypatch.setattr(run_envelope, "_save", lambda path, env: None)
    monkeypatch.setattr(run_envelope, "_count_solves", lambda path, env: counted.append(run_envelope.solves_used(env)))
    monkeypatch.setattr(run_envelope, "_pickup_refused", lambda env: set())
    held["counted"] = counted
    return held


@pytest.fixture
def settled(monkeypatch):
    calls = []

    def settle_job(path, ticket, job_id, native, status, result=None):
        calls.append((native, status, result))
        return {"ok": True}

    monkeypatch.setattr(assistant_tasks, "settle_job", settle_job)
    monkeypatch.setattr(run_ic, "_judge", lambda *a, **k: None)
    return calls


def _record(status="done", **extra):
    return {"status": status, "started_at": 1.0, "finished_at": 2.0, "error": None, **extra}


@pytest.mark.parametrize("baseline, used", [(1, 2), (0, 1)])
def test_solves_used_is_the_real_lp_count_on_a_miss_and_on_a_hit(envelope, settled, baseline, used):
    """A miss counts the plan and the baseline; a hit the plan only."""
    assert run_envelope.solves_used(envelope) == 1 + run_envelope.CASE_BASELINE_SOLVES  # reserved
    run_ic.settle("task.json", TICKET, "ad:1", _record(baseline_solves=baseline), 1.0)
    assert settled == [("done", "completed", {"study": run_ic.STUDY, "started_at": 1.0, "solves": baseline})]
    assert envelope["audit"][1]["solves"] == baseline
    assert run_envelope.solves_used(envelope) == used == envelope["counted"][-1]


def test_a_refused_case_counts_the_lps_its_baseline_ran(envelope, settled):
    run_ic.settle("task.json", TICKET, "ad:1",
                  _record("refused", baseline_solves=1, error_code="baseline_not_established:x"), 1.0)
    assert settled[0][1] == "failed"
    assert run_envelope.solves_used(envelope) == 2


@pytest.mark.parametrize("record", [_record(), _record(baseline_solves=None),
                                    {**_record(baseline_solves=0), "started_at": 9.0}])
def test_a_record_that_does_not_say_keeps_the_whole_reservation(envelope, settled, record):
    """No `baseline_solves`, or not the step's own record: the reservation
    stays counted, never refunded."""
    run_ic.settle("task.json", TICKET, "ad:1", record, 1.0)
    assert envelope["audit"][1]["solves"] == run_envelope.CASE_BASELINE_SOLVES
    assert run_envelope.solves_used(envelope) == 1 + run_envelope.CASE_BASELINE_SOLVES


def test_the_settle_is_clamped_to_the_reservation_and_idempotent(envelope):
    assert run_envelope.settle_case_solves("task.json", "solve.2", 7) == run_envelope.CASE_BASELINE_SOLVES
    assert run_envelope.settle_case_solves("task.json", "solve.2", -1) == 0
    assert run_envelope.settle_case_solves("task.json", "solve.2", 0) == 0
    assert envelope["counted"] == [1]  # recounted on a change only


def test_only_the_steps_own_row_is_settled(envelope, settled):
    """A later round's case (another step) or another step's row is never
    settled from this step's record."""
    later = {**CASE_ROW, "call_id": "run-solve.4-ffffffff"}
    envelope["audit"].append(later)
    run_ic.settle("task.json", TICKET, "ad:1", _record(baseline_solves=0), 1.0)
    assert (envelope["audit"][1]["solves"], later["solves"]) == (0, run_envelope.CASE_BASELINE_SOLVES)
    assert run_envelope.settle_case_solves("task.json", "solve.9", 0) is None
    from harness.runner import step_call_prefix
    assert CASE_ROW["call_id"].startswith(step_call_prefix("solve.2"))


def test_a_case_with_nothing_reserved_settles_nothing(envelope, settled):
    envelope["audit"][1] = {"event": "used", "tool": run_ic.TOOL, "call_id": CASE_ROW["call_id"]}
    assert run_envelope.settle_case_solves("task.json", "solve.2", 1) is None
    run_ic.settle("task.json", TICKET, "ad:1", _record(baseline_solves=0), 1.0)
    assert settled[0][2]["solves"] == run_ic.SOLVES == 0
    assert run_envelope.solves_used(envelope) == 1


# ── the concentrator's @2 profile ───────────────────────────────────────────


def test_v2_runs_the_case_after_the_queued_solve_on_a_counted_reservation():
    v2, v1 = run_profiles.resolve(REF), run_profiles.resolve("eh_concentrator_staged@1")
    steps = run_profiles.materialise(v2, "solve", "p")
    assert [s["tool"] for s in steps] == ["solve_queue_enqueue", run_ic.TOOL]
    case = next(r for r in v2["envelope"]["runs"] if r["tool"] == run_ic.TOOL)
    assert case["baseline_solves"] == run_envelope.CASE_BASELINE_SOLVES
    baseline = run_envelope.CASE_BASELINE_SOLVES
    # the plan and its baseline; each L2 round's re-solve and its case's baseline
    assert v2["envelope"]["budget"]["solves"] == (
        run_profiles.CONC_SOLVES + baseline + run_profiles.L2_ROUNDS * (1 + baseline)) == 9
    assert v1["envelope"]["budget"]["solves"] == 3
    assert not any(r["tool"] == run_ic.TOOL for r in v1["envelope"]["runs"])


def test_v2_case_rules_block_and_read_the_runs_own_case():
    rules = {r["id"]: r for r in run_profiles.resolve(REF)["stages"]["result_gate"]["rules"]}
    for rid in ("conc.ic.counterfactual", "conc.ic.basis"):
        assert rules[rid]["severity"] == "block", rid
    assert rules["conc.kpi.npv"]["severity"] == "warn"
    for rid in ("conc.ic.counterfactual", "conc.ic.basis", "conc.kpi.npv"):
        assert "issue 52" not in rules[rid]["basis"] and "the user ran" not in rules[rid]["basis"], rid
    v2 = run_profiles.resolve(REF)
    assert run_profiles.CONC_CASE_OUTSIDE not in v2["missing_tools"]
    assert run_profiles.CONC_CASE_OUTSIDE in run_profiles.resolve("eh_concentrator_staged@1")["missing_tools"]


def test_v2_refuses_a_case_whose_baseline_the_solves_left_cannot_cover():
    """Nothing is solved: the refusal is decided at admission, before the case."""
    budget = run_profiles.resolve(REF)["envelope"]["budget"]["solves"]
    env = {"budget": {"solves": budget},
           "audit": [dict(PLAN_ROW, call_id=f"s{i}") for i in range(budget - 1)]}
    assert run_envelope.case_budget_refusal(env, {"case_solves": run_envelope.CASE_BASELINE_SOLVES}) \
        == run_envelope.IC_BUDGET_KIND
    env["audit"].pop()
    assert run_envelope.case_budget_refusal(env, {"case_solves": run_envelope.CASE_BASELINE_SOLVES}) is None
