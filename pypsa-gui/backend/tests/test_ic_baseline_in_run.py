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
CASE_ROW = {
    "event": "used",
    "tool": run_ic.TOOL,
    "call_id": "run-solve.2-0a1b2c3d",
    "reserved": run_envelope.CASE_BASELINE_SOLVES,
    "solves": run_envelope.CASE_BASELINE_SOLVES,
}


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
    monkeypatch.setattr(
        PyPSAService, "solve_target_context", staticmethod(lambda: None), raising=False
    )
    statuses, lps = [], []

    def solve(conf, base, *a, **k):
        lps.append(1)
        status = statuses.pop(0)
        return status, status

    plan = B.BaselinePlan(removed=(), kept=(("Generator", "genset_1"),), flags=())

    def run():
        return B.solve_baseline(
            SimpleNamespace(),
            SimpleNamespace(island_constraints=True),
            plan,
            solve=solve,
        )

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


def test_a_changed_input_after_a_hit_is_a_miss_again(fake_solve, monkeypatch):
    """GK A (38) S3: the count is what ran: a new cache key (the inputs
    changed) solves again and counts its LP, never a hit's 0."""
    run, statuses, lps = fake_solve
    keys = iter(["rev-1", "rev-1", "rev-2"])
    monkeypatch.setattr(B, "_key", lambda *a, **k: next(keys))
    statuses.extend(["optimal", "optimal"])
    assert [run().solves, run().solves, run().solves] == [1, 0, 1]
    assert len(lps) == 2


def test_a_refused_baseline_reports_the_lps_it_ran(fake_solve):
    run, statuses, lps = fake_solve
    statuses.extend(["infeasible", "infeasible"])
    with pytest.raises(B.BaselineRefused) as err:
        run()
    assert err.value.solves == 2 == len(lps)


# ── the case's build and its record carry them ──────────────────────────────


@pytest.fixture
def build_for(monkeypatch):
    """`_ic_build_case` on fakes; `outcomes` pops one baseline outcome per
    call (`miss`, `hit`, `relaxed`, `refused`, `boom`), `kept` False needs none."""
    from routers import results
    from services.pypsa_service import PyPSAService
    from services.results import finance_case

    state = {"kept": True, "outcomes": []}

    def solve_baseline(*a, **k):
        outcome = state["outcomes"].pop(0)
        if outcome == "refused":
            raise B.BaselineRefused("baseline_not_established:infeasible", "", solves=2)
        if outcome == "boom":
            raise RuntimeError("the solver died mid-solve")
        return SimpleNamespace(solves={"miss": 1, "hit": 0, "relaxed": 2}[outcome])

    monkeypatch.setattr(
        finance_case,
        "baseline_plan",
        lambda n, cfg, owner=None: B.BaselinePlan(
            removed=(), kept=(("G", "g"),) if state["kept"] else (), flags=()
        ),
    )
    monkeypatch.setattr(B, "solve_baseline", solve_baseline)
    monkeypatch.setattr(
        finance_case, "build_finance_case", lambda *a, **k: SimpleNamespace(flags=())
    )
    monkeypatch.setattr("services.workflow_tools._network_copy", lambda n: n)
    monkeypatch.setattr(
        "models.finance.FinanceInputs.model_validate", classmethod(lambda cls, raw: raw)
    )
    monkeypatch.setattr(
        PyPSAService, "get_lock", staticmethod(lambda: threading.Lock())
    )
    build = results._ic_build_case(
        object(), SimpleNamespace(finance={}), owner=None, lost_load=None
    )
    return build, state


def _kept(build):
    """One call of `build` as the runner makes it: a per-call stats dict,
    then the record `_keep_baseline_solves` writes from it."""
    from services.finance.case import FinanceRefused
    from services.finance.investment_case_runner import (
        BUILD_STATS,
        _keep_baseline_solves,
    )

    stats: dict = {}
    token = BUILD_STATS.set(stats)
    try:
        build()
    except (FinanceRefused, RuntimeError):
        pass
    finally:
        BUILD_STATS.reset(token)
    record: dict = {}
    _keep_baseline_solves(record, stats, threading.Lock())
    return record.get("baseline_solves", "absent")


@pytest.mark.parametrize(
    "outcome, kept",
    [("miss", 1), ("hit", 0), ("relaxed", 2), ("refused", 2), ("boom", "absent")],
)
def test_each_call_records_the_baseline_lps_it_ran(build_for, outcome, kept):
    """A refusal after the relaxed retry counts both LPs; a raise mid-solve
    leaves the figure out (the whole reservation stays charged)."""
    build, state = build_for
    state["outcomes"].append(outcome)
    assert _kept(build) == kept


def test_a_case_with_no_baseline_records_none_run(build_for):
    build, state = build_for
    state["kept"] = False
    assert _kept(build) == 0


def test_a_build_outside_the_runner_needs_no_stats(build_for):
    """A build no runner worker calls (no `BUILD_STATS`) still builds."""
    from services.finance.investment_case_runner import BUILD_STATS

    build, state = build_for
    state["outcomes"].append("miss")
    assert BUILD_STATS.get() is None
    assert build().flags == ()


def test_two_builds_in_sequence_record_their_own_figures(build_for):
    """GK B (71) 1: a miss then a hit on the same build record 1 then 0,
    never the first call's figure twice."""
    build, state = build_for
    state["outcomes"].extend(["miss", "hit"])
    assert [_kept(build), _kept(build)] == [1, 0]


def test_a_build_that_does_not_say_leaves_the_record_alone():
    from services.finance.investment_case_runner import _keep_baseline_solves

    for stats in (
        {},
        {"baseline_solves": None},
        {"baseline_solves": -1},
        {"baseline_solves": True},
        {"baseline_solves": 1.0},
    ):
        record: dict = {}
        _keep_baseline_solves(record, stats, threading.Lock())
        assert record == {}, stats


def test_the_runner_keeps_the_figure_on_the_record_per_call():
    """The worker gives each build call its own stats dict; a fake that
    ignores it leaves the record without a figure."""
    from services.finance import investment_case_runner as R

    seen = []

    def build():
        stats = R.BUILD_STATS.get()
        seen.append(stats)
        stats["baseline_solves"] = 1
        raise RuntimeError("stop after the build")

    assert R.BUILD_STATS.get() is None
    record: dict = {}
    stats: dict = {}
    token = R.BUILD_STATS.set(stats)
    try:
        with pytest.raises(RuntimeError):
            build()
    finally:
        R.BUILD_STATS.reset(token)
    R._keep_baseline_solves(record, stats, threading.Lock())
    assert (
        seen == [stats]
        and record == {"baseline_solves": 1}
        and R.BUILD_STATS.get() is None
    )


# ── run_ic settles the reservation from the record ──────────────────────────


@pytest.fixture
def envelope(monkeypatch):
    """The run's envelope in memory: the plan's solve and the case's
    reserving row, as `admit` records them."""
    held = {"audit": [dict(PLAN_ROW), dict(CASE_ROW)]}
    counted = []
    monkeypatch.setattr(run_envelope, "_load", lambda path: held)
    monkeypatch.setattr(run_envelope, "_save", lambda path, env: None)
    monkeypatch.setattr(
        run_envelope,
        "_count_solves",
        lambda path, env: counted.append(run_envelope.solves_used(env)),
    )
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
    return {
        "status": status,
        "started_at": 1.0,
        "finished_at": 2.0,
        "error": None,
        **extra,
    }


@pytest.mark.parametrize("baseline, used", [(1, 2), (0, 1)])
def test_solves_used_is_the_real_lp_count_on_a_miss_and_on_a_hit(
    envelope, settled, baseline, used
):
    """A miss counts the plan and the baseline; a hit the plan only."""
    assert (
        run_envelope.solves_used(envelope) == 1 + run_envelope.CASE_BASELINE_SOLVES
    )  # reserved
    run_ic.settle("task.json", TICKET, "ad:1", _record(baseline_solves=baseline), 1.0)
    assert settled == [
        (
            "done",
            "completed",
            {"study": run_ic.STUDY, "started_at": 1.0, "solves": baseline},
        )
    ]
    assert envelope["audit"][1]["solves"] == baseline
    assert run_envelope.solves_used(envelope) == used == envelope["counted"][-1]


@pytest.mark.parametrize("lps", [1, 2])
def test_a_refused_case_counts_the_lps_its_baseline_ran(envelope, settled, lps):
    """A refusal after the island-relaxed retry settles at 2."""
    run_ic.settle(
        "task.json",
        TICKET,
        "ad:1",
        _record(
            "refused", baseline_solves=lps, error_code="baseline_not_established:x"
        ),
        1.0,
    )
    assert settled[0][1] == "failed"
    assert envelope["audit"][1]["solves"] == lps
    assert run_envelope.solves_used(envelope) == 1 + lps


@pytest.mark.parametrize(
    "record",
    [
        _record(),
        _record(baseline_solves=None),
        _record(baseline_solves=-1),
        _record(baseline_solves=True),
        _record(baseline_solves=0.0),
        _record("aborted"),
        _record("failed", error_code="internal_error"),
        # GK A (39) F1: a count kept before the case aborted or failed later
        _record("aborted", baseline_solves=1),
        _record("failed", baseline_solves=0, error_code="internal_error"),
        {**_record(baseline_solves=0), "started_at": 9.0},
    ],
)
def test_a_record_that_does_not_say_keeps_the_whole_reservation(
    envelope, settled, record
):
    """No `baseline_solves`, or not the step's own record: the reservation
    stays counted, never refunded."""
    run_ic.settle("task.json", TICKET, "ad:1", record, 1.0)
    assert envelope["audit"][1]["solves"] == run_envelope.CASE_BASELINE_SOLVES
    assert run_envelope.solves_used(envelope) == 1 + run_envelope.CASE_BASELINE_SOLVES


def test_the_settle_is_clamped_to_the_reservation_and_idempotent(envelope):
    assert (
        run_envelope.settle_case_solves("task.json", "solve.2", 7)
        == run_envelope.CASE_BASELINE_SOLVES
    )
    assert envelope["counted"] == []  # nothing changed
    for no_count in (-1, True, 1.0, None, "1"):
        assert (
            run_envelope.settle_case_solves("task.json", "solve.2", no_count) is None
        ), no_count
    assert envelope["audit"][1]["solves"] == run_envelope.CASE_BASELINE_SOLVES
    assert run_envelope.settle_case_solves("task.json", "solve.2", 1) == 1
    assert run_envelope.settle_case_solves("task.json", "solve.2", 1) == 1
    assert run_envelope.settle_case_solves("task.json", "solve.2", 0) == 0
    assert envelope["counted"] == [2, 1]  # recounted on a change only


def test_only_the_steps_own_row_is_settled(envelope, settled):
    """GK A (38) S1: with two case rows in one envelope, settling the older
    step's leaves the newer row (a later round's case) untouched."""
    later = {**CASE_ROW, "call_id": "run-solve.4-ffffffff"}
    envelope["audit"].append(later)
    run_ic.settle("task.json", TICKET, "ad:1", _record(baseline_solves=0), 1.0)
    assert (envelope["audit"][1]["solves"], later["solves"]) == (
        0,
        run_envelope.CASE_BASELINE_SOLVES,
    )
    assert settled[0][2]["solves"] == 0  # the step's own figure, not the newer row's
    assert run_envelope.settle_case_solves("task.json", "solve.9", 0) is None
    from harness.runner import step_call_prefix

    assert CASE_ROW["call_id"].startswith(step_call_prefix("solve.2"))


def test_a_step_whose_id_extends_another_is_never_its_row(envelope, settled):
    """GK A (39) F2: step `case` never settles step `case-2`'s row, even as
    the latest case row; only the exact call-id shape matches."""
    from harness.runner import is_step_call

    envelope["audit"][1]["call_id"] = "run-case-0a1b2c3d"
    other = {**CASE_ROW, "call_id": "run-case-2-0a1b2c3d"}
    envelope["audit"].append(other)
    run_ic.settle(
        "task.json", ("t1", "case", "a1"), "ad:1", _record(baseline_solves=0), 1.0
    )
    assert (envelope["audit"][1]["solves"], other["solves"]) == (
        0,
        run_envelope.CASE_BASELINE_SOLVES,
    )
    assert is_step_call("run-case-0a1b2c3d", "case")
    for not_mine in (
        "run-case-2-0a1b2c3d",
        "run-case-0a1b2c3",
        "run-case-0a1b2c3dd",
        "run-case-0A1B2C3D",
        "xrun-case-0a1b2c3d",
        None,
        7,
    ):
        assert not is_step_call(not_mine, "case"), not_mine


def test_a_later_refused_admission_of_the_step_leaves_its_used_row_settled(envelope):
    """A retried admission of the same step that was refused carries no
    reservation; the step's admitted row is the one settled."""
    envelope["audit"].append(
        {
            "event": "refused",
            "tool": run_ic.TOOL,
            "call_id": "run-solve.2-99999999",
            "reason": "ic_budget",
        }
    )
    assert run_envelope.settle_case_solves("task.json", "solve.2", 1) == 1
    assert envelope["audit"][1]["solves"] == 1


def test_no_envelope_settles_nothing(monkeypatch):
    monkeypatch.setattr(run_envelope, "_load", lambda path: None)
    assert run_envelope.settle_case_solves("task.json", "solve.2", 1) is None


def test_a_case_with_nothing_reserved_settles_nothing(envelope, settled):
    envelope["audit"][1] = {
        "event": "used",
        "tool": run_ic.TOOL,
        "call_id": CASE_ROW["call_id"],
    }
    assert run_envelope.settle_case_solves("task.json", "solve.2", 1) is None
    run_ic.settle("task.json", TICKET, "ad:1", _record(baseline_solves=0), 1.0)
    assert settled[0][2]["solves"] == run_ic.SOLVES == 0
    assert run_envelope.solves_used(envelope) == 1


# ── the concentrator's @2 profile ───────────────────────────────────────────

# L8 (GK A (38) S4, GK B (71) 4): @1 as on master f15f7c460, hashed in a fresh
# worktree there (`json.dumps(PROFILES[ref], sort_keys=True)`), never regenerated.
PINNED_V1 = "d87844f0d50a991bcc92f55ac3868c648ce78d857b6070b7559110586159d17f"


def test_v1_stays_byte_equal_to_master():
    import hashlib
    import json

    blob = json.dumps(
        run_profiles.PROFILES["eh_concentrator_staged@1"], sort_keys=True
    ).encode()
    assert hashlib.sha256(blob).hexdigest() == PINNED_V1


def test_v2_runs_the_case_after_the_queued_solve_on_a_counted_reservation():
    v2, v1 = run_profiles.resolve(REF), run_profiles.resolve("eh_concentrator_staged@1")
    steps = run_profiles.materialise(v2, "solve", "p")
    assert [s["tool"] for s in steps] == ["solve_queue_enqueue", run_ic.TOOL]
    case = next(r for r in v2["envelope"]["runs"] if r["tool"] == run_ic.TOOL)
    assert case["baseline_solves"] == run_envelope.CASE_BASELINE_SOLVES
    baseline = run_envelope.CASE_BASELINE_SOLVES
    # the plan and its baseline; each L2 round's re-solve and its case's baseline
    assert (
        v2["envelope"]["budget"]["solves"]
        == (
            run_profiles.CONC_SOLVES
            + baseline
            + run_profiles.L2_ROUNDS * (1 + baseline)
        )
        == 9
    )
    assert v1["envelope"]["budget"]["solves"] == 3
    assert not any(r["tool"] == run_ic.TOOL for r in v1["envelope"]["runs"])


def test_v2_names_the_baseline_at_the_bound_the_validator_requires():
    """GK A (38) S4: @2's entry passes `_check_run`; one below the bound fails."""
    entry = next(
        r
        for r in run_profiles.resolve(REF)["envelope"]["runs"]
        if r["tool"] == run_ic.TOOL
    )
    run_envelope._check_run(entry)
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope._check_run({**entry, "baseline_solves": 1})


def test_v2_case_rules_block_and_read_the_runs_own_case():
    rules = {
        r["id"]: r for r in run_profiles.resolve(REF)["stages"]["result_gate"]["rules"]
    }
    for rid in ("conc.ic.counterfactual", "conc.ic.basis"):
        assert rules[rid]["severity"] == "block", rid
    assert rules["conc.kpi.npv"]["severity"] == "warn"
    for rid in ("conc.ic.counterfactual", "conc.ic.basis", "conc.kpi.npv"):
        assert (
            "issue 52" not in rules[rid]["basis"]
            and "the user ran" not in rules[rid]["basis"]
        ), rid
    v2 = run_profiles.resolve(REF)
    assert run_profiles.CONC_CASE_OUTSIDE not in v2["missing_tools"]
    assert (
        run_profiles.CONC_CASE_OUTSIDE
        in run_profiles.resolve("eh_concentrator_staged@1")["missing_tools"]
    )


def test_v2_refuses_a_case_whose_baseline_the_solves_left_cannot_cover():
    """Nothing is solved: the refusal is decided at admission, before the case."""
    budget = run_profiles.resolve(REF)["envelope"]["budget"]["solves"]
    env = {
        "budget": {"solves": budget},
        "audit": [dict(PLAN_ROW, call_id=f"s{i}") for i in range(budget - 1)],
    }
    assert (
        run_envelope.case_budget_refusal(
            env, {"case_solves": run_envelope.CASE_BASELINE_SOLVES}
        )
        == run_envelope.IC_BUDGET_KIND
    )
    env["audit"].pop()
    assert (
        run_envelope.case_budget_refusal(
            env, {"case_solves": run_envelope.CASE_BASELINE_SOLVES}
        )
        is None
    )


@pytest.mark.live_solve
def test_a_hit_and_a_fresh_baseline_give_the_same_case_hash(reset_backend):
    """GK B (71) 3: the LP count is no case input: a case on a fresh
    baseline and one on its cache hit hash alike."""
    from routers.results import _ic_case_hash
    from services.results.finance_case import baseline_plan
    from tests.test_finance_baseline_purchase import _site_today
    from tests.test_finance_case_adapter import _case
    from tests.test_finance_counterfactual_purchase import PURCHASE, _fin_h2

    B.clear_cache()
    try:
        n, cfg, ll, fresh = _site_today(reference_purchases=[PURCHASE])
        hit = B.solve_baseline(n, cfg, baseline_plan(n, cfg))
        assert fresh.solves >= 1 and hit.solves == 0
        hashes = [
            _ic_case_hash(_case(n, cfg, _fin_h2(), baseline=b, lost_load=ll))
            for b in (fresh, hit)
        ]
    finally:
        B.clear_cache()
    assert hashes[0] is not None and hashes[0] == hashes[1]
