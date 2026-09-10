"""
The adequacy-coupled planning loop — controller (coupling-loop spec §2).

The engine under test is PURE CONTROL FLOW: it drives a `solve_at` and an
`evaluate` it is handed and decides what to try next. So every unit test here
drives it with SCRIPTED FAKES — no solver, no MC, no network — because the
questions are all about the decisions ("did it stop at the first infeasible?",
"did it cross the slack region in one step?", "did it ever hand the solver the
≤ 0 sentinel?") and a live solve would only make those questions slower to ask
and harder to place exactly on the iterate that matters.

The fakes mimic the shapes the real bindings produce: `solve_at` returns the
frontier's `_solve_once` read-out (`status`/`condition`/`cost_eur`/`ens_mwh`/
`cap_mwh`/`binding`/`report`), and `evaluate` returns `(plan_hash, metrics)`
with `mc.mc_adequacy`'s metrics dict — extra keys included, so the controller's
projection onto the spec's eight `mc` keys is actually exercised.

One live test (`@pytest.mark.slow`) closes the loop on real HiGHS: a four-hour
network where shedding is economic at a loose cap, so iterate 0 misses the
MC's own LOLE target and only a genuinely different — and strictly more
expensive — plan can meet it.
"""
from __future__ import annotations

import hashlib
import math
import queue

import pandas as pd
import pypsa
import pytest

from services.adequacy.coupling import (
    ENERGY_FLOOR_MWH,
    EPS_FLOOR_PERMYRIAD,
    MAX_LOOP_SOLVES,
    run_coupling_loop,
)

# ── scripted fakes ────────────────────────────────────────────────────────

_SOLVE_KEYS = ("status", "condition", "cost_eur", "ens_mwh", "cap_mwh",
               "binding")


def _solve_result(**kw) -> dict:
    """A `SolveResult` with the frontier's read-out defaults: an optimal
    solve whose cap is slack (`binding="voll"`)."""
    base = {"status": "ok", "condition": None, "cost_eur": 1000.0,
            "ens_mwh": 500.0, "cap_mwh": 500.0, "binding": "voll",
            "report": {"engine": "lp_proxy", "fidelity": "deterministic_scenario"}}
    base.update({k: v for k, v in kw.items() if k in _SOLVE_KEYS})
    return base


def _metrics(lole: float, *, ci=None, eue=None, n_samples=200) -> dict:
    """`mc.mc_adequacy`'s dict. The keys the spec's `mc` block does NOT list
    (`converged`, `time_basis`, `resolution_floor_h`, `warning`) are present
    on purpose: the controller must project, not forward."""
    lole = float(lole)
    eue = float(eue if eue is not None else lole * 100.0)
    return {
        "lole_hours": lole,
        "lole_ci": tuple(ci) if ci is not None else (max(0.0, lole - 0.1),
                                                    lole + 0.1),
        "eue_mwh": eue,
        "eue_ci": (max(0.0, eue - 10.0), eue + 10.0),
        "by_period": {"ALL": {"lole_hours": lole, "eue_mwh": eue}},
        "n_samples": int(n_samples),
        "converged": True,
        "time_basis": "hours_per_horizon",
        "horizon_years": 0.00137,
        "resolution_floor_h": 0.015,
        "warning": "the standing MC warning",
    }


class Fake:
    """A scripted `(solve_at, evaluate)` pair.

    ``steps[i]`` describes the i-th SOLVE and the evaluation that follows it:
    the `_SOLVE_KEYS` go to `solve_at`, and `hash`/`lole`/`ci`/`n_samples` to
    `evaluate`. ``tail`` is repeated once the script runs out — present so a
    BROKEN variant that keeps solving runs off the end into more solves (the
    thing the test asserts about) instead of dying on an exhausted script.
    """

    def __init__(self, steps, *, tail=None):
        self.steps = list(steps)
        self.tail = tail
        self.eps_calls: list[float] = []
        self.eval_hashes: list[str] = []
        self.probe_calls: list[str] = []
        self._optimal = False
        self._pending: dict | None = None

    def _step(self, i: int) -> dict:
        if i < len(self.steps):
            return self.steps[i]
        assert self.tail is not None, (
            f"solve_at called {i + 1} times but the script has "
            f"{len(self.steps)} steps and no tail")
        return self.tail

    def solve_at(self, eps: float) -> dict:
        step = self._step(len(self.eps_calls))
        self.eps_calls.append(eps)
        self._pending = step
        res = _solve_result(**step)
        self._optimal = res["status"] in ("ok", "optimal")
        return res

    def _hash_of_pending(self) -> str:
        assert self._optimal, (
            "the plan hash was asked for after a NON-OPTIMAL solve — "
            "`p_nom_opt` still holds the previous plan there")
        return str(self._pending["hash"])

    def evaluate(self):
        h = self._hash_of_pending()
        self.eval_hashes.append(h)
        step = self._pending
        return h, _metrics(step["lole"], ci=step.get("ci"),
                           n_samples=step.get("n_samples", 200))

    def probing_evaluate(self):
        """The same `evaluate`, carrying the optional cheap plan-hash probe
        (see the module docstring in `coupling.py` on the adjudication)."""
        def _call():
            return self.evaluate()

        def _probe():
            h = self._hash_of_pending()
            self.probe_calls.append(h)
            return h

        _call.plan_hash = _probe
        return _call


class _StopAfter:
    """A `stop_event` that trips after ``n`` checks — a `threading.Event` as
    the controller sees it, with the abort landing BETWEEN iterates."""

    def __init__(self, n: int):
        self.n = int(n)
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > self.n


def _run(fake, *, target, eps0=100.0, max_solves=8, **kw) -> dict:
    return run_coupling_loop(fake.solve_at, fake.evaluate,
                             target_lole_h=target, eps0=eps0,
                             max_solves=max_solves, **kw)


# ── ★ the cheap case ──────────────────────────────────────────────────────

def test_a_met_first_iterate_costs_exactly_one_solve_and_one_evaluation():
    """★ Spec §2 rule 1. The user's current cap may already meet the target;
    that answer costs ONE solve and ONE evaluation, and a loop that "warms up"
    with a tightening step charges the user minutes for a question already
    answered — and returns a needlessly expensive plan as the answer.

    BROKEN VARIANT (bite): always tighten once before testing the verdict.
    """
    fake = Fake([{"hash": "A", "lole": 1.0, "cost_eur": 1000.0}],
                tail={"hash": "B", "lole": 0.5, "cost_eur": 9000.0})
    res = _run(fake, target=3.0, eps0=100.0)

    assert res["status"] == "met"
    assert fake.eps_calls == [100.0]
    assert fake.eval_hashes == ["A"]
    assert res["solves_used"] == 1
    assert res["eps_star"] == 100.0
    assert res["final"] is res["iterations"][0]
    assert res["confident"] is True


# ── ★ the informed jump ───────────────────────────────────────────────────

def test_the_tightening_step_uses_the_slack_the_solve_just_reported():
    """★ Spec §2, plan [B4]. With a high VoLL the cap is SLACK — the plan
    sheds 10 MWh against a 500 MWh ceiling — so a blind ÷4 spends four or five
    solves walking down a region where nothing whatsoever changes. The step
    the solve's own numbers imply crosses it in one.

    BROKEN VARIANT (bite): `eps_next = eps / 4` — the second solve lands on
    25.0 instead of 0.5.
    """
    fake = Fake([{"hash": "A", "lole": 9.0, "ens_mwh": 10.0, "cap_mwh": 1000.0,
                  "binding": "voll"}],
                tail={"hash": "B", "lole": 8.0, "ens_mwh": 10.0,
                      "cap_mwh": 1000.0, "binding": "voll"})
    _run(fake, target=3.0, eps0=100.0, max_solves=2)

    assert len(fake.eps_calls) == 2
    assert fake.eps_calls[0] == 100.0
    # 0.5 · 100 · 10/1000, not 100/4
    assert fake.eps_calls[1] == pytest.approx(0.5)
    assert fake.eps_calls[1] != pytest.approx(25.0)


# ── ★ plan-hash reuse (plan [B3]) ─────────────────────────────────────────

_REUSE_SCRIPT = [
    # cap slack, plan A, misses
    {"hash": "A", "lole": 5.0, "cost_eur": 1000.0, "ens_mwh": 400.0,
     "cap_mwh": 1000.0, "binding": "voll"},
    # the cap moved but the PLAN did not — same hash, so the MC is known
    {"hash": "A", "lole": 5.0, "cost_eur": 1000.0, "ens_mwh": 400.0,
     "cap_mwh": 1000.0, "binding": "voll"},
    # a DIFFERENT plan at exactly the same cost — the v1 design's
    # false positive, which must still be evaluated
    {"hash": "B", "lole": 4.0, "cost_eur": 1000.0, "ens_mwh": 400.0,
     "cap_mwh": 1000.0, "binding": "voll"},
]


def test_a_repeated_plan_reuses_its_metrics_and_an_equal_cost_new_plan_does_not():
    """★ Spec §2, plan [B3]. Equal objective does NOT imply equal plan
    (degenerate optima; with DSR the variable cost moves while the plan stands
    still), so cost equality is unsound as a reuse key in both directions.
    Identity of the plan the MC actually reads is not.

    BROKEN VARIANT (bite): key the reuse on `cost_eur` instead of the plan
    hash — iterate 2 is a different plan at the same cost and would inherit
    iterate 0's LOLE of 5.0 instead of its own 4.0.
    """
    fake = Fake(_REUSE_SCRIPT)
    res = _run(fake, target=3.0, eps0=100.0, max_solves=3)
    rows = res["iterations"]

    assert [r["plateau"] for r in rows] == [False, True, False]
    # the plateau row reports the metrics of the iterate that earned them
    assert rows[1]["mc"] == rows[0]["mc"]
    # …and the equal-cost DIFFERENT plan reports its own
    assert rows[2]["cost_eur"] == rows[0]["cost_eur"]
    assert rows[2]["mc"]["lole_hours"] == 4.0
    # every optimal iterate's hash was established; nothing was evaluated twice
    # for the same plan beyond the first sighting
    assert fake.eval_hashes.count("B") == 1


def test_the_optional_hash_probe_skips_the_evaluation_outright():
    """★ Spec §2's "evaluation skip", second half: with a cheap plan hash in
    hand the plateau iterate costs NO MC at all. See `coupling.py`'s
    docstring — the spec's `evaluate: () -> (hash, metrics)` cannot be asked
    for a hash alone, so the skip is offered as an OPTIONAL `plan_hash`
    attribute and degrades to reuse-after-evaluation when it is absent.

    BROKEN VARIANT (bite): key the reuse on cost — the probe then skips
    iterate 2 as well and `eval_hashes` loses "B".
    """
    fake = Fake(_REUSE_SCRIPT)
    res = run_coupling_loop(fake.solve_at, fake.probing_evaluate(),
                            target_lole_h=3.0, eps0=100.0, max_solves=3)
    rows = res["iterations"]

    assert fake.probe_calls == ["A", "A", "B"]
    # the repeated plan is never simulated a second time
    assert fake.eval_hashes == ["A", "B"]
    assert [r["plateau"] for r in rows] == [False, True, False]
    assert rows[1]["mc"] == rows[0]["mc"]
    assert rows[2]["mc"]["lole_hours"] == 4.0


# ── ★ infeasibility is monotone; other failures are not ───────────────────

def test_the_first_infeasible_solve_stops_the_tightening():
    """★ Spec §2, plan [S1]. The ε-feasible sets are NESTED (F(ε′) ⊆ F(ε) for
    ε′ < ε), so the first infeasible ε proves every tighter ε infeasible:
    continuing burns the whole budget re-proving it, and the honest verdict is
    already available. The failed iterate is never evaluated — `p_nom_opt`
    still holds the PREVIOUS plan, so an MC run there scores the wrong plan
    against this ε.

    BROKEN VARIANT (bite): keep tightening past the infeasible iterate.
    """
    fake = Fake(
        [{"hash": "A", "lole": 5.0},
         {"status": "warning", "condition": "infeasible or unbounded",
          "cost_eur": None, "ens_mwh": None, "cap_mwh": None, "binding": None}],
        tail={"hash": "B", "lole": 4.0})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=8)
    rows = res["iterations"]

    assert len(fake.eps_calls) == 2, fake.eps_calls
    assert res["status"] == "unreachable"
    assert res["solves_used"] == 2
    assert rows[1]["mc"] is None
    assert rows[1]["solve_status"] == "warning"
    assert rows[1]["condition"] == "infeasible or unbounded"
    assert fake.eval_hashes == ["A"]        # the infeasible iterate: never
    assert res["final"] is None
    assert res["eps_star"] is None


def test_a_time_limit_is_not_unreachability_and_does_not_stop_the_loop():
    """★ Spec §2, plan [S1] second clause. A time-limit / numerical /
    validation failure is NOT monotone in ε — the next, tighter ε may solve
    perfectly — so it is recorded (`mc: null`) and the search continues. A
    controller that treats every non-optimal outcome as infeasibility reports
    "no plan meets this standard" when the truth is "the solver ran out of
    time", which is the same words for opposite user actions.

    BROKEN VARIANT (bite): treat any non-optimal status as infeasible.
    """
    fake = Fake(
        [{"hash": "A", "lole": 5.0},
         {"status": "warning", "condition": "time_limit — solver stopped early",
          "cost_eur": None, "ens_mwh": None, "cap_mwh": None, "binding": None},
         {"hash": "B", "lole": 2.0, "cost_eur": 1500.0}],
        tail={"hash": "B", "lole": 2.0, "cost_eur": 1500.0})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=4)
    rows = res["iterations"]

    assert res["status"] == "met"
    assert rows[1]["mc"] is None
    assert rows[1]["solve_status"] == "warning"
    # the step after an informationless failure falls back to the blind ÷4
    assert fake.eps_calls[:3] == [100.0, 25.0, 6.25]
    assert fake.eval_hashes == ["A", "B", "B"]


# ── ★ the energy floor ────────────────────────────────────────────────────

def test_a_miss_at_the_energy_floor_is_unreachable():
    """★ Spec §2's floor clause, plan [S3]. The stopping floor is expressed in
    ENERGY, not in ε: once the cap is under a megawatt-hour the constrained
    plan IS the zero-shed plan, so a miss there cannot be tightened away and
    grinding ε down to 0.01‱ only spends the budget proving it again.

    BROKEN VARIANT (bite): report `met` at the floor — the verdict then
    asserts a standard the evaluated plan demonstrably failed.
    """
    fake = Fake([{"hash": "A", "lole": 5.0, "ens_mwh": 0.4, "cap_mwh": 0.5,
                  "binding": "system_cap"}],
                tail={"hash": "B", "lole": 5.0, "ens_mwh": 0.4,
                      "cap_mwh": 0.5, "binding": "system_cap"})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=8)

    assert res["iterations"][0]["cap_mwh"] < ENERGY_FLOOR_MWH
    assert res["status"] == "unreachable"
    assert res["solves_used"] == 1
    assert res["final"] is None
    assert res["confident"] is False


# ── ★ the budget ──────────────────────────────────────────────────────────

def test_the_budget_is_never_overrun_and_nothing_met_leaves_final_none():
    """★ Spec §2's budget clause. Solves — not evaluations — are the wall
    clock, and the user was promised at most `max_solves` of them.

    BROKEN VARIANT (bite): check the budget AFTER the solve instead of before
    it (`while solves_used <= max_solves`) — a ninth solve on an eight-solve
    budget.
    """
    fake = Fake([{"hash": "A", "lole": 9.0}], tail={"hash": "A", "lole": 9.0})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=3)

    assert len(fake.eps_calls) == 3
    assert res["solves_used"] == 3
    assert res["status"] == "budget_exhausted"
    assert res["final"] is None
    assert res["eps_star"] is None
    assert res["confident"] is False
    assert len(res["iterations"]) == 3


def test_a_budget_spent_refining_still_reports_the_best_verified_iterate():
    """★ Spec §2's budget clause, second half — ADJUDICATED (see
    `coupling.py`'s docstring): a run that VERIFIED a met plan and then ran
    out of solves while trying to make it cheaper has answered the user's
    question, so the verdict is `met` with the best verified iterate as
    `final`; `budget_exhausted` is reserved for a run that never got an
    answer (the test above). Either way `final` is the CHEAPEST verified met
    iterate — never the last one tried, because ε → MC-LOLE is not monotone
    and only evaluated iterates are answers (plan [S5]).

    BROKEN VARIANT (bite): take the LAST met iterate as `final` — the
    refinement probe at 2000.0 € wins over the 1500.0 € plan that met first.
    """
    fake = Fake(
        [{"hash": "A", "lole": 5.0, "cost_eur": 1000.0},
         {"hash": "B", "lole": 2.0, "cost_eur": 1500.0}],
        tail={"hash": "C", "lole": 2.5, "cost_eur": 2000.0})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=3)
    rows = res["iterations"]

    assert res["solves_used"] == 3
    assert len(rows) == 3
    assert rows[2]["mc"]["lole_hours"] == 2.5          # the refinement probe
    assert res["final"] is rows[1], res["final"]
    assert res["eps_star"] == 25.0
    assert res["status"] == "met"


# ── ★ refinement ──────────────────────────────────────────────────────────

def test_refinement_stops_when_the_midpoint_reproduces_the_met_plan():
    """★ Spec §2's refinement clause, plan [S4]. Once a looser cap yields the
    SAME plan as the met endpoint, that endpoint is already the loosest cap
    producing it and no further solve can lower the cost — so the old
    "bisect until the bracket ratio is ≤ 2" spends solves on a question
    already settled. The stop is keyed on the plan, not on the geometry.

    BROKEN VARIANT (bite): bisect to a fixed bracket ratio instead — the run
    burns its whole eight-solve budget.
    """
    fake = Fake(
        [{"hash": "A", "lole": 5.0, "cost_eur": 1000.0, "binding": "system_cap"},
         {"hash": "B", "lole": 2.0, "cost_eur": 1500.0, "binding": "system_cap"}],
        tail={"hash": "B", "lole": 2.0, "cost_eur": 1500.0,
              "binding": "system_cap"})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=8)

    assert fake.eps_calls == [100.0, 25.0, pytest.approx(50.0)]
    assert res["solves_used"] == 3
    assert res["status"] == "met"
    # the midpoint reproduced the met plan, so the LOOSER cap is the answer:
    # same plan, same cost, more headroom
    assert res["final"]["eps_permyriad"] == pytest.approx(50.0)
    assert res["eps_star"] == pytest.approx(50.0)


# ── ★ the stopping band ───────────────────────────────────────────────────

def test_met_is_the_mean_and_the_interval_only_reports_confidence():
    """★ Spec §2's band, plan [S11]. `met` is the MEAN against the target;
    the 95% interval is REPORTED as `confident`, never iterated for — the
    interval's width is a draws decision, and tightening the cap to buy
    confidence spends solves on a question more draws answer.

    BROKEN VARIANT (bite): require `lole_ci[1] <= target` for `met` — the run
    below keeps tightening past an iterate that already meets the standard.
    """
    fake = Fake([{"hash": "A", "lole": 2.9, "ci": (1.0, 4.0)}],
                tail={"hash": "B", "lole": 1.0, "ci": (0.5, 1.5)})
    res = _run(fake, target=3.0, eps0=100.0)

    assert res["status"] == "met"
    assert res["solves_used"] == 1
    assert res["confident"] is False

    tight = Fake([{"hash": "A", "lole": 2.0, "ci": (1.5, 2.5)}],
                 tail={"hash": "B", "lole": 1.0})
    assert _run(tight, target=3.0, eps0=100.0)["confident"] is True


# ── ★ abort ───────────────────────────────────────────────────────────────

def test_an_abort_between_iterates_keeps_what_it_had():
    """★ Spec §2's abort clause, plan [S8]. The study runs for minutes; a user
    who cancels must get the iterates already paid for, not an empty record —
    and, above all, no further solve.

    BROKEN VARIANT (bite): ignore the stop event.
    """
    stop = _StopAfter(1)
    fake = Fake([{"hash": "A", "lole": 5.0}], tail={"hash": "B", "lole": 4.0})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=8, stop_event=stop)

    assert res["status"] == "aborted"
    assert fake.eps_calls == [100.0]
    assert res["solves_used"] == 1
    assert len(res["iterations"]) == 1
    assert res["final"] is None


# ── ★ the ≤ 0 sentinel ────────────────────────────────────────────────────

def test_the_step_never_reaches_the_no_target_sentinel():
    """★ Spec §2, plan [S3]. `_wrap_with_ens_cap` reads `permyriad <= 0` as NO
    TARGET and returns the LOOSEST plan — so a controller that steps to 0
    believes it produced the tightest plan in existence while holding the
    unconstrained one. An informed step is exactly what can reach 0: a plan
    that sheds nothing gives `0.5 · eps · 0/cap`.

    BROKEN VARIANT (bite): allow 0 — drop the `EPS_FLOOR_PERMYRIAD` clamp on
    the step (and the `eps > 0` assertion that backs it up).
    """
    fake = Fake([{"hash": "A", "lole": 5.0, "ens_mwh": 0.0, "cap_mwh": 1000.0,
                  "binding": "voll"}],
                tail={"hash": "B", "lole": 5.0, "ens_mwh": 0.0,
                      "cap_mwh": 1000.0, "binding": "voll"})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=8)

    assert fake.eps_calls, "the loop never solved at all"
    assert all(e > 0 for e in fake.eps_calls), fake.eps_calls
    assert fake.eps_calls[1] == EPS_FLOOR_PERMYRIAD
    # …and nothing below the backstop is ever tried
    assert min(fake.eps_calls) >= EPS_FLOOR_PERMYRIAD
    assert res["status"] == "unreachable"


def test_a_nonpositive_eps0_is_clamped_before_the_first_solve():
    """The same sentinel, arriving from the request rather than from the
    step: an unset `ens_cap_permyriad` reaches the loop as 0 and must not be
    handed to the solver as "no target"."""
    fake = Fake([{"hash": "A", "lole": 1.0}], tail={"hash": "A", "lole": 1.0})
    _run(fake, target=3.0, eps0=0.0)
    assert fake.eps_calls == [EPS_FLOOR_PERMYRIAD]


# ── the observer hook ─────────────────────────────────────────────────────

def test_on_iteration_sees_every_completed_row_once_in_order():
    """Spec §2, plan [S6]. The panel's mid-run table is built from this hook,
    so a row the controller keeps but does not announce is a row the user
    never sees — and that includes the FAILED and the PLATEAU iterates, which
    are the two the user most wants an explanation for."""
    seen: list[dict] = []
    fake = Fake([
        {"hash": "A", "lole": 5.0, "ens_mwh": 400.0, "cap_mwh": 1000.0},
        {"hash": "A", "lole": 5.0, "ens_mwh": 400.0, "cap_mwh": 1000.0},
        {"status": "warning", "condition": "numerical trouble",
         "cost_eur": None, "ens_mwh": None, "cap_mwh": None, "binding": None},
        {"hash": "B", "lole": 2.0, "cost_eur": 1500.0},
    ], tail={"hash": "B", "lole": 2.0, "cost_eur": 1500.0})
    res = _run(fake, target=3.0, eps0=100.0, max_solves=4,
               on_iteration=seen.append)

    assert len(seen) == 4
    assert all(a is b for a, b in zip(seen, res["iterations"]))
    assert [r["plateau"] for r in seen] == [False, True, False, False]
    assert [r["mc"] is None for r in seen] == [False, False, True, False]


def test_a_broken_observer_does_not_destroy_the_study():
    """The hook belongs to the route's storage. A minutes-long study must not
    be thrown away because the record it is being appended to misbehaved —
    the failure is logged and the answer still comes back."""
    def _boom(_row):
        raise RuntimeError("the record went away")

    fake = Fake([{"hash": "A", "lole": 1.0}], tail={"hash": "A", "lole": 1.0})
    res = _run(fake, target=3.0, eps0=100.0, on_iteration=_boom)
    assert res["status"] == "met"
    assert len(res["iterations"]) == 1


# ── failures of the callables themselves ──────────────────────────────────

def test_an_evaluate_that_raises_is_a_loop_failure():
    """Spec §2: `evaluate` raising is a loop failure — the restore stays the
    route's job. The iterate that broke is kept (with `mc: null` and the
    reason on `condition`) rather than vanishing."""
    def _boom():
        raise RuntimeError("the MC snapshot found nothing to sample")

    fake = Fake([{"hash": "A", "lole": 1.0}], tail={"hash": "A", "lole": 1.0})
    res = run_coupling_loop(fake.solve_at, _boom, target_lole_h=3.0,
                            eps0=100.0, max_solves=8)

    assert res["status"] == "failed"
    assert res["solves_used"] == 1
    assert len(res["iterations"]) == 1
    assert res["iterations"][0]["mc"] is None
    assert "the MC snapshot found nothing" in res["iterations"][0]["condition"]
    assert res["final"] is None


def test_metrics_the_loop_cannot_read_are_a_loop_failure_not_a_stack_trace():
    """The loop runs on a worker thread. Whatever `evaluate` hands back, an
    exception escaping this function leaves the route's record `running` for
    ever with no iterates on it — so unusable metrics become a failed iterate
    with the reason on it, like any other evaluation failure."""
    fake = Fake([{"hash": "A", "lole": 1.0}], tail={"hash": "A", "lole": 1.0})

    def _junk():
        return "A", {"lole_ci": (0.0, 1.0)}          # no lole_hours at all

    res = run_coupling_loop(fake.solve_at, _junk, target_lole_h=3.0,
                            eps0=100.0, max_solves=8)
    assert res["status"] == "failed"
    assert res["iterations"][0]["mc"] is None
    assert "unusable metrics" in res["iterations"][0]["condition"]


def test_a_solve_at_that_raises_is_a_loop_failure_too():
    """`solve_at` is specified never to raise (failures surface as
    status/condition), but a controller that takes that on faith turns a
    solver-process death into a stack trace out of a worker thread."""
    def _boom(_eps):
        raise RuntimeError("solver process died")

    def _never():                                         # pragma: no cover
        raise AssertionError("evaluate must not run for a failed solve")

    res = run_coupling_loop(_boom, _never, target_lole_h=3.0, eps0=100.0,
                            max_solves=8)
    assert res["status"] == "failed"
    assert res["solves_used"] == 1
    assert res["iterations"][0]["mc"] is None


# ── the serialised shapes ─────────────────────────────────────────────────

def test_the_rows_and_the_result_carry_exactly_the_spec_keys():
    """The route serialises these VERBATIM, so an extra key is a payload
    change nobody reviewed and a missing one is a panel that renders
    `undefined`. The `mc` block is a PROJECTION of `mc_adequacy`'s dict — the
    aggregator's own extras (`converged`, `warning`, …) belong to the study
    payload, not to a per-iterate row."""
    fake = Fake([{"hash": "A", "lole": 1.0}], tail={"hash": "A", "lole": 1.0})
    res = _run(fake, target=3.0, eps0=100.0)

    assert set(res) == {"status", "iterations", "final", "confident",
                        "eps_star", "solves_used"}
    row = res["iterations"][0]
    assert set(row) == {"eps_permyriad", "solve_status", "condition",
                        "cost_eur", "ens_mwh", "cap_mwh", "binding",
                        "plateau", "mc"}
    assert set(row["mc"]) == {"engine", "fidelity", "lole_hours", "lole_ci",
                              "eue_mwh", "eue_ci", "n_samples", "by_period"}
    assert row["mc"]["engine"] == "mc"
    assert row["mc"]["fidelity"] == "sequential_mc"
    assert row["mc"]["by_period"] == {"ALL": {"lole_hours": 1.0,
                                              "eue_mwh": 100.0}}
    assert row["mc"]["lole_ci"] == [pytest.approx(0.9), pytest.approx(1.1)]


def test_the_budget_is_bounded_by_the_product_cap():
    """[N7]: the route validates `max_solves ≤ MAX_LOOP_SOLVES`, and the
    controller enforces the same bound rather than trusting a caller — a
    50-solve request is an hour of somebody's afternoon."""
    fake = Fake([{"hash": "A", "lole": 9.0}], tail={"hash": "A", "lole": 9.0})
    res = _run(fake, target=3.0, eps0=1000.0, max_solves=50)
    assert res["solves_used"] == MAX_LOOP_SOLVES


# ── the live loop ─────────────────────────────────────────────────────────

DRAWS = 200
SEED = 0
VOLL = 3000.0
WEIGHT = 3.0
LOADS = (70.0, 80.0, 90.0, 100.0)
CHEAP_MW = 60.0


def _live_network() -> pypsa.Network:
    """A rising four-hour load against 60 MW of cheap firm and an expensive
    extendable peaker. Shedding is economic at a loose cap (400 k€/MW against
    a 3 k€/MWh VoLL), so the LP declines to build until the cap makes it —
    and each megawatt built retires one more short HOUR, which is what moves
    the MC's LOLE. Carrier `gas` gives both units the library's 5 % EFORd, so
    the evaluation is a genuine stochastic one, not arithmetic."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=len(LOADS), freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=pd.Series(LOADS, index=n.snapshots))
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=CHEAP_MW,
          marginal_cost=10.0)
    n.add("Generator", "peak", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=40.0,
          capital_cost=400_000.0, marginal_cost=250.0)
    return n


def _plan_hash(inputs) -> str:
    """Spec §3's hash: exactly what the MC reads, and nothing else."""
    h = hashlib.sha256()
    for u in sorted(inputs.units, key=lambda u: u.name):
        h.update(f"{u.name}\x00{u.capacity_mw!r}\x00".encode())
    for s in sorted(inputs.storage, key=lambda s: s.name):
        h.update(f"{s.name}\x00{s.p_nom_mw!r}\x00{s.e_nom_mwh!r}\x00".encode())
    h.update(inputs.residual.tobytes())
    return h.hexdigest()


@pytest.mark.slow
def test_the_live_loop_lands_a_met_plan_that_costs_strictly_more():
    """★ The end-to-end claim, on real HiGHS: iterate 0 MISSES the MC's own
    LOLE target at the user's loose cap, and the loop lands a plan that meets
    it — a genuinely DIFFERENT plan, which is why the cost strictly rises.
    Nothing here is faked: the ε-cap goes through `_solve_once`, the verdict
    through `mc_adequacy` on the solved network, and the two are bound
    together exactly as spec §3 describes.

    A loop whose "verdict flipped" without a cost increase would be measuring
    MC noise, so `>` is asserted rather than `>=`.
    """
    import dataclasses

    from services.adequacy.mc import mc_adequacy, snapshot_inputs
    from services.adequacy.sweep import _solve_once
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = _live_network()
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    cfg = SolverConfig(solver_name="highs", voll=VOLL)
    log_q: queue.SimpleQueue = queue.SimpleQueue()

    def solve_at(eps: float) -> dict:
        sink: dict = {}
        _solve_once(dataclasses.replace(cfg, ens_cap_permyriad=eps), n, lock,
                    log_q, sink)
        status = sink.get("_status")
        rep = sink.get("adequacy_report") if status in ("ok", "optimal") else None
        if not rep:
            return {"status": status or "failed",
                    "condition": sink.get("_condition"), "cost_eur": None,
                    "ens_mwh": None, "cap_mwh": None, "binding": None,
                    "report": None}
        sysblk = rep["target"]["system"]
        return {
            "status": "ok",
            "condition": sink.get("_condition"),
            "cost_eur": float(rep["cost"]["total_system_cost_eur"]),
            "ens_mwh": float(sysblk["achieved_ens_mwh"]),
            "cap_mwh": float(sysblk["cap_mwh"]),
            "binding": rep["target"]["binding"],
            "report": rep,
        }

    def evaluate():
        inputs = snapshot_inputs(n, keep_zero_capacity=True)
        return (_plan_hash(inputs),
                mc_adequacy(inputs, draws=DRAWS, seed=SEED, max_draws=DRAWS))

    res = run_coupling_loop(solve_at, evaluate, target_lole_h=5.0, eps0=5000.0,
                            max_solves=6)

    rows = res["iterations"]
    assert rows[0]["mc"] is not None
    assert rows[0]["mc"]["lole_hours"] > 5.0, rows[0]["mc"]
    assert res["status"] == "met", [
        (r["eps_permyriad"], r["solve_status"],
         r["mc"] and r["mc"]["lole_hours"]) for r in rows]
    final = res["final"]
    assert final is not None
    assert final["mc"]["lole_hours"] <= 5.0
    assert res["solves_used"] >= 2
    assert final["cost_eur"] > rows[0]["cost_eur"], (
        f"the verdict flipped without buying anything: "
        f"{rows[0]['cost_eur']} -> {final['cost_eur']}")
    assert math.isfinite(res["eps_star"]) and res["eps_star"] > 0
    # every solve the loop paid for is on the record
    assert len(rows) == res["solves_used"]
    assert all(r["eps_permyriad"] > 0 for r in rows)
