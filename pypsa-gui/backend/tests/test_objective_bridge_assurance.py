"""Checked objective-to-cost bridge (`objective-bridge/1`; assistant-assurance 10 S3, GK B 64 G3).

Every expected value is worked by hand from the network below, never read
back from the service under test:

    3 hourly snapshots, every weighting 2.0; load 10 MW on bus b.
    Generator g: p_nom 8 MW, marginal cost 5 EUR/MWh  -> 8 x 3 x 2 x 5   =   240 EUR
    Slack __voll_b (load_shedding): 2 MW at 100 EUR/MWh -> 2 x 3 x 2 x 100 = 1,200 EUR
    Objective = 1,440 EUR. With capital_cost 10 EUR/MW on the non-extendable g,
    the reported total adds the run's share of the annual charge, which the
    objective never sees: 10 x 8 x (6 weighted h / 8,760 h) = 0.0547945... EUR.
    The reported total is rounded to 5 decimals (1,440.05479).
"""

import math
import queue
import threading
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from harness import assurance_scope
from harness import objective_bridge_calculations as ob
from harness import profile_common
from services.results import objective_decomposition as od
from services.solver_service import SolverConfig

SNAPS = pd.date_range("2030-01-01", periods=3, freq="h")
ALL = tuple(ob.METRICS)
OBJECTIVE = 1440.0
PENALTY = 1200.0


@pytest.fixture(autouse=True)
def live_lp(monkeypatch):
    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: True)


def _net(capital_cost=0.0, slack_carrier="load_shedding"):
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.snapshot_weightings.loc[:, :] = 2.0
    n.add("Bus", "b")
    n.add("Load", "d", bus="b", p_set=[10.0, 10.0, 10.0])
    n.add("Generator", "g", bus="b", p_nom=8, marginal_cost=5, capital_cost=capital_cost)
    n.add("Generator", "__voll_b", bus="b", carrier=slack_carrier, p_nom=1e3, marginal_cost=100)
    return n


def _solved(**kw):
    n = _net(**kw)
    status = n.optimize(solver_name="highs")
    assert status == ("ok", "optimal")
    return n


def _run(n, state=None, **window):
    raw = ob.source(SimpleNamespace(network=n, solver_state=state or {}), "network")
    scope = {"profile": ob.PROFILE, "entity_id": "network", "metrics": list(ALL), **window}
    facts, slots = ob.calculate(raw, scope)
    out = {f["metric"]: f["value"] for f in facts}
    out.update({s["metric"]: s["reason"] for s in slots if s["disposition"] == "unavailable"})
    return out, raw, scope


# ── the bridge rows, worked by hand ───────────────────────────────────────


def test_a_plain_solve_reconciles_with_its_penalty_inside_the_total():
    out, raw, scope = _run(_solved())
    assert out == pytest.approx({
        "objective_eur": OBJECTIVE,
        "reported_total_cost_eur": OBJECTIVE,
        "penalty_cost_eur": PENALTY,
        "nonextendable_fixed_cost_eur": 0.0,
        "period_weighting_adjustment_eur": 0.0,
        "reserve_revenue_eur": 0.0,
        "residual_eur": 0.0,
    }, abs=1e-6)
    assert ob.notes(raw, scope) == [f"Matches within {1440e-6:.6g} EUR."]


def test_fixed_cost_of_an_asset_the_solve_could_not_size_bridges_the_gap():
    out, raw, scope = _run(_solved(capital_cost=10.0))
    assert out["objective_eur"] == pytest.approx(OBJECTIVE)
    assert out["reported_total_cost_eur"] == 1440.05479
    assert out["nonextendable_fixed_cost_eur"] == pytest.approx(80 * 6 / 8760, rel=1e-12)
    assert out["period_weighting_adjustment_eur"] == 0.0
    # Only the 5-decimal rounding of the total is left: 1440.05479 - 0.0547945... .
    assert out["residual_eur"] == pytest.approx(-(1440.05479 - 1440 - 80 * 6 / 8760), abs=1e-9)
    assert ob.notes(raw, scope)[0].startswith("Matches within")


def test_a_wrong_reported_total_is_shown_as_a_residual_not_absorbed(monkeypatch):
    # G4-style injection: the cost service over-reports by 10 EUR.
    from services.results import cost_breakdown

    real = cost_breakdown.compute_cost_breakdown

    def inflated(n, cfg):
        cb = real(n, cfg)
        return {**cb, "total": cb["total"] + 10.0}

    monkeypatch.setattr(cost_breakdown, "compute_cost_breakdown", inflated)
    out, raw, scope = _run(_solved())
    assert out["reported_total_cost_eur"] == pytest.approx(OBJECTIVE + 10.0)
    assert out["residual_eur"] == pytest.approx(-10.0)
    assert out["period_weighting_adjustment_eur"] == 0.0  # it never absorbs the error
    assert ob.notes(raw, scope) == [
        f"Does not match: the residual is {-10.0:.6g} EUR, above the {1440e-6:.6g} EUR tolerance."
    ]


# ── G3: objective-scale reversal under auto and user scale != 1 ───────────


@pytest.mark.parametrize(
    "config",
    [SolverConfig(user_objective_scale=2.0), SolverConfig(auto_objective_scale=True)],
    ids=["user-scale-2", "auto-scale"],
)
def test_the_objective_is_read_in_eur_after_the_scale_is_reversed(config):
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = _net()
    PyPSAService.set_network(n)
    status, _ = run_simulation(config, n, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal")
    out, raw, _ = _run(n, state={"solver_config": config})
    assert raw["objective_scale"] == 1.0
    assert out["objective_eur"] == pytest.approx(OBJECTIVE, rel=1e-9)
    assert abs(out["residual_eur"]) <= od.residual_tolerance(OBJECTIVE)


def test_an_objective_still_scaled_is_unavailable_never_a_wrong_number():
    n = _solved()
    n._pypsa_gui_objective_scale = 2.0  # the post-solve rescale never ran
    out, raw, scope = _run(n)
    assert out["objective_eur"] == "still_scaled"
    assert out["residual_eur"] == "still_scaled"
    assert out["reported_total_cost_eur"] == pytest.approx(OBJECTIVE)
    assert ob.notes(raw, scope) == ["Not available: the objective is still scaled for the solver."]


# ── G3: residual tolerance constant, boundary ─────────────────────────────


def test_the_residual_tolerance_is_relative_with_a_one_eur_floor():
    assert od.RESIDUAL_TOLERANCE == 1e-6
    assert od.residual_tolerance(1e6) == 1.0
    assert od.residual_tolerance(-1e6) == 1.0
    assert od.residual_tolerance(0.5) == 1e-6


def test_the_tolerance_boundary_matches_and_one_ulp_above_does_not():
    assert ob.reconciled(1e6, 1.0)
    assert ob.reconciled(1e6, -1.0)
    assert not ob.reconciled(1e6, math.nextafter(1.0, 2.0))
    assert not ob.reconciled(1e6, -math.nextafter(1.0, 2.0))


# ── G3: a penalty that can't be identified is unavailable, never 0 ────────


def test_a_slack_under_an_ordinary_carrier_is_not_available_not_zero():
    out, raw, scope = _run(_solved(slack_carrier="AC"))
    assert out["penalty_cost_eur"] == "not_separable"
    assert out["objective_eur"] == pytest.approx(OBJECTIVE)
    assert out["residual_eur"] == pytest.approx(0.0, abs=1e-6)
    assert ob.notes(raw, scope)[-1] == (
        "Not available: a slack generator has an ordinary carrier, so its penalty "
        "cannot be separated from the total."
    )


def test_no_slack_generator_is_a_real_zero_penalty():
    n = _net()
    n.remove("Generator", "__voll_b")
    n.generators.loc["g", "p_nom"] = 10
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    out, _, _ = _run(n)
    assert out["penalty_cost_eur"] == 0.0
    assert out["objective_eur"] == pytest.approx(10 * 3 * 2 * 5)


# ── scope ────────────────────────────────────────────────────────────────


def test_a_rolling_horizon_answers_no_row():
    out, raw, scope = _run(_solved(), state={"solver_config": SolverConfig(solve_strategy="rolling")})
    assert out == dict.fromkeys(ALL, "last_window_only")
    assert ob.notes(raw, scope) == ["Not available: the objective covers only the last window."]


def test_an_unverified_solve_answers_no_row(monkeypatch):
    monkeypatch.setattr(profile_common, "lp_outputs_live", lambda ctx: False)
    out, raw, scope = _run(_solved())
    assert out == dict.fromkeys(ALL, "unverified_solve")
    assert ob.notes(raw, scope) == []


def test_a_partial_window_is_refused_not_pro_rated():
    with pytest.raises(ValueError, match="^unsupported_snapshot_scope$"):
        _run(_solved(), start=SNAPS[0].isoformat(), end=SNAPS[1].isoformat())


def test_the_whole_window_named_explicitly_is_answered():
    out, _, _ = _run(_solved(), start=SNAPS[0].isoformat(), end=SNAPS[-1].isoformat())
    assert out["objective_eur"] == pytest.approx(OBJECTIVE)


def test_only_the_network_is_a_bridge_entity():
    with pytest.raises(ValueError, match="^network_scope_required$"):
        ob.source(SimpleNamespace(network=_solved(), solver_state={}), "g")
    with pytest.raises(ValueError, match="network_scope_required"):
        assurance_scope.validate({
            "version": 1, "profile": ob.PROFILE, "project_id": "p", "entity_id": "g", "retain": False,
        })


def test_an_ac_power_flow_refuses():
    with pytest.raises(ValueError, match="^ac_pf_present$"):
        ob.source(SimpleNamespace(network=_solved(), solver_state={"ac_pf_results": {}}), "network")


def test_the_rows_are_in_bridge_order_and_the_disclosure_is_exact():
    assert list(ob.METRICS) == [
        "objective_eur", "reported_total_cost_eur", "penalty_cost_eur",
        "nonextendable_fixed_cost_eur", "period_weighting_adjustment_eur",
        "reserve_revenue_eur", "residual_eur",
    ]
    assert ob.DISCLOSURE == (
        "The solver objective includes penalty and slack terms and period weightings, "
        "so it is not the total system cost."
    )


def test_a_myopic_objective_reads_last_period_only():
    _, raw, scope = _run(_solved())
    raw["scope"] = "last_period"
    facts, slots = ob.calculate(raw, scope)
    assert facts == []
    assert {s["reason"] for s in slots} == {"last_period_only"}
    assert ob.notes(raw, scope) == ["Not available: the objective covers only the last period."]


def test_an_unreadable_cost_breakdown_reads_plain_reasons_each_explained(monkeypatch):
    from services.results import cost_breakdown

    def broken(n, cfg):
        raise RuntimeError("no statistics")

    monkeypatch.setattr(cost_breakdown, "compute_cost_breakdown", broken)
    out, raw, scope = _run(_solved())
    assert out == {
        "objective_eur": pytest.approx(OBJECTIVE),
        "reported_total_cost_eur": "total_missing",
        "penalty_cost_eur": "not_separable",
        "nonextendable_fixed_cost_eur": "terms_missing",
        "period_weighting_adjustment_eur": "terms_missing",
        "reserve_revenue_eur": "terms_missing",
        "residual_eur": "total_missing",
    }
    assert ob.notes(raw, scope) == [
        ob.EXPLAINED["total_missing"], ob.EXPLAINED["not_separable"], ob.EXPLAINED["terms_missing"],
    ]


def test_only_the_requested_rows_reasons_are_explained():
    _, raw, _ = _run(_solved(slack_carrier="AC"))
    scope = {"profile": ob.PROFILE, "entity_id": "network", "metrics": ["objective_eur"]}
    assert ob.notes(raw, scope) == []  # no residual shown, so no match line either


def test_every_reason_code_is_short_plain_words_with_its_own_line():
    import re

    for code, line in ob.EXPLAINED.items():
        assert re.fullmatch(r"[a-z]+(?:_[a-z]+){0,2}", code), code
        assert line.startswith("Not available: ") and "_" not in line and "{" not in line
