# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""DC template review 02: the CO2 limit design goal.

The goal is PyPSA's own CO2 cap (one ``primary_energy`` row on
``co2_emissions`` per period, `services/solver/co2_cap.py`), written for the
solve only, with a per-snapshot grid intensity counted at the shared helper's
figure. Hand networks carry literal figures; the DC templates show the goal
binds on the real thing.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import queue
import sys
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest
from pydantic import ValidationError

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from models.schemas import SolverConfigSchema  # noqa: E402
from services import fuel_curve  # noqa: E402
from services.co2_override import OVERRIDE_ATTR  # noqa: E402
from services.pypsa_service import PyPSAService  # noqa: E402
from services.reliability_goal import goal_free_config  # noqa: E402
from services.solver import co2_cap  # noqa: E402
from services.solver_service import SolverConfig, run_simulation  # noqa: E402


def _solve(n, **cfg_kw):
    PyPSAService.set_network(n)
    sink: dict = {}
    q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        SolverConfig(**cfg_kw),
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        q,
        state_update=lambda **kw: sink.update(kw),
    )
    logs = []
    while True:
        try:
            logs.append(str(q.get_nowait()))
        except queue.Empty:
            break
    return status, condition, sink, logs


def _hand(grid_ovr=None, clean_mw=20.0, grid_mw=20.0, load=10.0, weight=1.0):
    """1 bus, 2 snapshots: a 10 MW load, a cheap grid at 0.5 t/MWh and a dear
    zero-CO2 source; no slack, so the cap can make it infeasible."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Carrier", "grid", co2_emissions=0.5)
    n.add("Carrier", "clean", co2_emissions=0.0)
    n.add("Bus", "b")
    n.add("Load", "d", bus="b", p_set=load)
    n.add(
        "Generator", "grid", bus="b", carrier="grid", p_nom=grid_mw, marginal_cost=10.0
    )
    n.add(
        "Generator",
        "clean",
        bus="b",
        carrier="clean",
        p_nom=clean_mw,
        marginal_cost=100.0,
    )
    if grid_ovr is not None:
        n.generators_t[OVERRIDE_ATTR] = pd.DataFrame(
            {"grid": grid_ovr}, index=n.snapshots
        )
    return n


# Annual limit ↔ tonnes in the 2-hour row: × 2 / 8760.
TO_ROW = 2.0 / 8760.0


# ── the input (C5) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_the_schema_refuses_a_limit_that_is_not_a_finite_non_negative_number(bad):
    with pytest.raises(ValidationError):
        SolverConfigSchema(co2_limit_t_per_yr=bad)


@pytest.mark.parametrize("ok", [0.0, 1234.5])
def test_the_schema_takes_zero_and_a_positive_limit(ok):
    assert SolverConfigSchema(co2_limit_t_per_yr=ok).co2_limit_t_per_yr == ok


def test_zero_is_a_limit_and_none_is_off():
    assert co2_cap.target_of(SolverConfig(co2_limit_t_per_yr=0.0)) == 0.0
    assert co2_cap.target_of(SolverConfig()) is None


# sha256 of master's (84a2d1a1) `goal_free_config(SolverConfig())`, the input
# to the network fingerprint and the investment-case digest.
MASTER_DEFAULT_CONFIG_SHA = (
    "9b89ba730791f6d73b395db5f74240d82204cc3c58ba5109b8212cd5385fb281"
)


def _cfg_sha(cfg) -> str:
    return hashlib.sha256(
        json.dumps(goal_free_config(cfg), sort_keys=True, default=str).encode()
    ).hexdigest()


def test_goal_off_keeps_masters_fingerprint_input():
    assert _cfg_sha(SolverConfig(co2_limit_t_per_yr=None)) == MASTER_DEFAULT_CONFIG_SHA


@pytest.mark.parametrize("v", [0.0, 100.0])
def test_a_limit_zero_included_changes_the_fingerprint_input(v):
    assert _cfg_sha(SolverConfig(co2_limit_t_per_yr=v)) != MASTER_DEFAULT_CONFIG_SHA


# ── the rows (C1, C2) ─────────────────────────────────────────────────────


def test_goal_off_writes_no_rows():
    n = _hand()
    status, _, sink, _ = _solve(n)
    assert status == "ok"
    assert n.global_constraints.empty
    assert sink.get("last_co2_cap") is None


def test_the_single_period_constant_annualises_the_snapshots():
    n = _hand(weight=438.0)  # 2 × 438 h = 0.1 yr
    rows = []
    undo = co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1000.0), rows.append, {})
    gc = n.global_constraints
    assert list(gc.index) == ["co2_cap"]
    assert gc.at["co2_cap", "type"] == "primary_energy"
    assert gc.at["co2_cap", "carrier_attribute"] == "co2_emissions"
    assert gc.at["co2_cap", "sense"] == "<="
    assert gc.at["co2_cap", "constant"] == pytest.approx(100.0, rel=1e-12)
    assert fuel_curve.co2_caps(n) == ["co2_cap"]
    undo()
    assert n.global_constraints.empty


def test_the_staged_rows_are_per_period_and_scaled_by_years_and_weights():
    n = T.build_eh_datacenter_staged()
    yrs = n.investment_period_weightings.years
    w = n.snapshot_weightings.generators
    co2_cap.apply(
        n,
        SolverConfig(co2_limit_t_per_yr=1000.0, multi_investment_periods=True),
        lambda _m: None,
        {},
    )
    gc = n.global_constraints
    for p in n.investment_periods:
        name = f"co2_cap_{p}"
        share = w[w.index.get_level_values(0) == p].sum() / 8760.0
        assert gc.at[name, "investment_period"] == p
        assert gc.at[name, "constant"] == pytest.approx(
            1000.0 * yrs[p] * share, rel=1e-12
        )


def test_the_rebuilt_row_counts_the_override_and_falls_back_on_a_nan_snapshot():
    # Snapshot 0 at the override 0.1, snapshot 1 has none: carrier 0.5 / eff 0.5 = 1.0.
    n = _hand(grid_ovr=[0.1, np.nan])
    n.generators.loc["grid", "efficiency"] = 0.5
    co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1.0), lambda _m: None, {})
    n.optimize.create_model()
    co2_cap.rebuild_rows(n, n.snapshots)
    lhs = n.model.constraints["GlobalConstraint-co2_cap"].lhs
    labels = n.model["Generator-p"].sel(name="grid").labels.values
    assert co2_cap._row_coefficients(lhs, labels).tolist() == pytest.approx([0.1, 1.0])
    clean = n.model["Generator-p"].sel(name="clean").labels.values
    assert co2_cap._row_coefficients(lhs, clean).tolist() == [0.0, 0.0]


def test_the_staged_grid_row_carries_weight_times_years_times_the_override():
    n = T.build_eh_datacenter_staged()
    cfg = SolverConfig(co2_limit_t_per_yr=1e9, multi_investment_periods=True)
    co2_cap.apply(n, cfg, lambda _m: None, {})
    n.optimize.create_model(multi_investment_periods=True)
    co2_cap.rebuild_rows(n, n.snapshots)
    w = n.snapshot_weightings.generators
    yrs = n.investment_period_weightings.years
    for p, ovr in T.DC_GRID_CO2_T_PER_MWH.items():
        sns = n.snapshots[n.snapshots.get_level_values(0) == p]
        lhs = n.model.constraints[f"GlobalConstraint-co2_cap_{p}"].lhs
        labels = (
            n.model["Generator-p"].sel(name="grid_supply", snapshot=sns).labels.values
        )
        got = co2_cap._row_coefficients(lhs, labels)
        assert got == pytest.approx((w.loc[sns] * yrs[p] * ovr).to_numpy(), rel=1e-12)


# ── duals (C3) ────────────────────────────────────────────────────────────


def test_the_dual_is_the_rebuilt_rows_not_the_flat_one():
    # Override 0.1: the grid emits 2 t in the 2 h, under the 3 t cap, so the
    # cap is slack (mu 0). The flat row (0.5 → 10 t) would bind and buy clean.
    n = _hand(grid_ovr=[0.1, 0.1])
    status, _, sink, _ = _solve(n, co2_limit_t_per_yr=3.0 / TO_ROW)
    assert status == "ok"
    assert n.generators_t.p["clean"].sum() == pytest.approx(0.0, abs=1e-6)
    row = sink["last_co2_cap"]["rows"][0]
    assert row["mu"] == pytest.approx(0.0, abs=1e-9)
    (cap,) = co2_cap.cap_entries(sink["last_co2_cap"], 2.0, {})
    assert cap["binding"] is False and cap["cost_eur_per_t"] is None


@pytest.mark.parametrize("scale", [1.0, 1e-3])
def test_a_binding_cap_prices_the_marginal_switch_at_any_objective_scale(scale):
    # Cap 6 t of the grid's 10: 8 MWh must move to clean at +90 EUR/MWh and
    # each MWh saves 0.5 t, so a tonne costs 180 EUR.
    n = _hand()
    status, _, sink, _ = _solve(
        n, co2_limit_t_per_yr=6.0 / TO_ROW, user_objective_scale=scale
    )
    assert status == "ok"
    assert n.generators_t.p["clean"].sum() == pytest.approx(8.0, rel=1e-6)
    (cap,) = co2_cap.cap_entries(sink["last_co2_cap"], 6.0, {})
    assert cap["binding"] is True
    assert cap["cost_eur_per_t"] == pytest.approx(180.0, rel=1e-6)


# ── state restored (C4) ───────────────────────────────────────────────────

# A solve stamps its own columns with or without the goal: results (*_opt),
# the slack bus's `control` on success, `p_max_pu_includes_outages`.
_SOLVE_STAMPS = ("control", "p_max_pu_includes_outages")


def _frozen(n):
    """The input frames the goal could touch, and the dynamic inputs."""
    gens = n.generators[
        [
            c
            for c in n.generators.columns
            if not c.endswith("_opt") and c not in _SOLVE_STAMPS
        ]
    ]
    dyn = {k: v.copy() for k, v in n.generators_t.items() if not v.empty}
    return (n.global_constraints.copy(), gens.copy(), n.carriers.copy(), dyn)


def _same(a, b):
    """``b`` (after) equals ``a`` (before); of the dynamic frames, the inputs
    ``a`` had (the solve adds its results: p, mu_upper, ...)."""
    for x, y in zip(a[:3], b[:3]):
        pd.testing.assert_frame_equal(x, y)
    for k, v in a[3].items():
        pd.testing.assert_frame_equal(v, b[3][k])


def test_rows_are_gone_after_an_ok_solve():
    n = _hand()
    before = _frozen(n)
    assert _solve(n, co2_limit_t_per_yr=6.0 / TO_ROW)[0] == "ok"
    _same(before, _frozen(n))


def test_rows_are_gone_after_an_infeasible_solve():
    n = _hand(clean_mw=4.0)
    before = _frozen(n)
    status, condition, _, _ = _solve(n, co2_limit_t_per_yr=1.0 / TO_ROW)
    assert status != "ok" and "infeasible" in str(condition).lower()
    _same(before, _frozen(n))


def test_rows_are_gone_when_the_lp_build_raises():
    n = _hand()
    before = _frozen(n)  # the LP build raises before any result is written
    status, _, _, _ = _solve(
        n,
        co2_limit_t_per_yr=6.0 / TO_ROW,
        extra_functionality_code="def extra_functionality(n, snapshots):\n    raise RuntimeError('boom')\n",
    )
    assert status != "ok"
    _same(before, _frozen(n))


def test_a_hand_made_cap_is_left_alone_and_the_goal_refused():
    n = _hand()
    n.add(
        "GlobalConstraint",
        "my_cap",
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=9.0,
    )
    with pytest.raises(co2_cap.Co2CapRefusal) as exc:
        co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1.0), lambda _m: None, {})
    assert exc.value.code == co2_cap.DUPLICATE
    before = _frozen(n)
    status, _, _, logs = _solve(n, co2_limit_t_per_yr=1.0)
    assert status != "ok"
    assert any("my_cap" in line for line in logs)
    _same(before, _frozen(n))


def test_a_fuel_curve_genset_is_still_refused_under_the_goal(monkeypatch):
    n = _hand()
    monkeypatch.setattr(
        fuel_curve,
        "co2_cap_refusal",
        lambda n, name, caps=None: "curve" if name == "grid" and caps else None,
    )
    with pytest.raises(fuel_curve.FuelCurveError):
        co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1.0), lambda _m: None, {})
    assert n.global_constraints.empty


# ── unreachable (C7) ──────────────────────────────────────────────────────


def test_an_unreachable_cap_names_the_least_emissions_reachable():
    # Clean is 4 MW: the grid must give 6 MWh × 2 h = 12 MWh, 6 t in the 2 h,
    # 6 × 8760 / 2 = 26,280 t/yr.
    n = _hand(clean_mw=4.0)
    status, condition, sink, _ = _solve(n, co2_limit_t_per_yr=10_000.0)
    assert "infeasible" in str(condition).lower()
    ref = sink["last_co2_cap"]["refusal"]
    assert ref["code"] == "co2_cap_unreachable"
    assert ref["extra_solves"] == 1
    assert ref["least_t_per_yr"] == {"all": pytest.approx(26_280.0, rel=1e-9)}
    assert ref["message"] == (
        "CO₂ limit: no design meets 10,000 t CO₂/yr. "
        "The least reachable is 26,280 t CO₂/yr."
    )


def test_a_network_infeasible_without_the_cap_says_so():
    n = _hand(clean_mw=4.0, grid_mw=3.0)
    status, condition, sink, _ = _solve(n, co2_limit_t_per_yr=10_000.0)
    ref = sink["last_co2_cap"]["refusal"]
    assert ref["least_t_per_yr"] is None and ref["without_limit"] == "infeasible"
    assert "the limit is not the cause" in ref["message"]


# ── the DC templates ──────────────────────────────────────────────────────


def _gas_mwh(n):
    w = n.snapshot_weightings.generators
    gas = [g for g in n.generators.index if n.generators.at[g, "carrier"] == "gas"]
    return float(n.generators_t.p[gas].mul(w, axis=0).sum().sum())


def _annual_t(n):
    w = n.snapshot_weightings.generators
    co2 = n.carriers.co2_emissions
    t = 0.0
    for g in n.generators.index:
        f = (
            co2.get(n.generators.at[g, "carrier"], 0.0)
            / n.generators.at[g, "efficiency"]
        )
        t += float((n.generators_t.p[g] * w).sum()) * f
    return t / (w.sum() / 8760.0)


def test_on_the_single_year_dc_template_a_cap_binds_and_cuts_genset_energy():
    n0 = T.build_eh_datacenter()
    assert _solve(n0, voll=5000.0)[0] == "ok"
    free_t, free_gas = _annual_t(n0), _gas_mwh(n0)
    limit = round(0.95 * free_t)
    n = T.build_eh_datacenter()
    status, _, sink, _ = _solve(n, voll=5000.0, co2_limit_t_per_yr=limit)
    assert status == "ok"
    assert _annual_t(n) == pytest.approx(limit, rel=1e-6)
    assert _gas_mwh(n) < free_gas
    a = co2_cap.annual(
        sink["last_co2_cap"], _annual_t(n) * co2_cap.year_fraction(n), {}
    )
    assert a["binding"] is True and a["shadow_price_eur_per_t"] > 0
    assert a["t_per_yr"] == pytest.approx(limit, rel=1e-6)
    assert n.global_constraints.empty


def _live(n, accessor, attr, source="lopf", **_kw):
    return getattr(getattr(n, accessor), attr)


def test_on_the_staged_template_the_cap_and_the_emissions_view_count_the_same_tonnes():
    # The grid counts at 0.35 / 0.30 / 0.22 in the cap row, the figure the
    # Emissions view reports: at the flat 0.35 the 2030 and 2035 rows would
    # over-count and the view would show a period well under a binding cap.
    from services.results.emissions import compute_emissions

    cfg = dict(voll=5000.0, multi_investment_periods=True)
    n0 = T.build_eh_datacenter_staged()
    assert _solve(n0, **cfg)[0] == "ok"
    free = {
        r["period"]: r["total_tCO2"]
        for r in compute_emissions(n0, "lopf", result_df=_live)["by_period"]
    }
    scale = {p: co2_cap.row_scale(n0, p) for p in n0.investment_periods}
    limit = round(0.9 * min(free[p] / scale[p] for p in n0.investment_periods))
    n = T.build_eh_datacenter_staged()
    status, _, sink, _ = _solve(n, co2_limit_t_per_yr=limit, **cfg)
    assert status == "ok"
    got = {
        r["period"]: r["total_tCO2"]
        for r in compute_emissions(n, "lopf", result_df=_live)["by_period"]
    }
    for p in n.investment_periods:
        assert got[p] == pytest.approx(limit * scale[p], rel=1e-6)
    assert all(
        c["binding"] for c in co2_cap.cap_entries(sink["last_co2_cap"], None, got)
    )


# ── results (C8) ──────────────────────────────────────────────────────────

_GOAL = {
    "limit_t_per_yr": 1000.0,
    "rows": [
        {
            "name": "co2_cap_2030",
            "period": 2030,
            "cap_t": 500.0,
            "row_scale": 0.5,
            "mu": -180.0,
            "shadow_price_eur_per_t": 180.0,
            "binding": True,
        },
        {
            "name": "co2_cap_2035",
            "period": 2035,
            "cap_t": 2000.0,
            "row_scale": 2.0,
            "mu": 0.0,
            "shadow_price_eur_per_t": None,
            "binding": False,
        },
    ],
}


def test_a_multi_period_figure_is_its_highest_periods_and_names_it():
    a = co2_cap.annual(_GOAL, 2000.0, {2030: 500.0, 2035: 1500.0})
    assert a["by_period"] == {2030: pytest.approx(1000.0), 2035: pytest.approx(750.0)}
    assert a["t_per_yr"] == pytest.approx(1000.0) and a["period"] == 2030
    assert a["binding"] is True and a["shadow_price_eur_per_t"] == 180.0


def test_a_single_period_figure_is_the_tonnes_over_the_year_share():
    one = {
        "limit_t_per_yr": 1000.0,
        "rows": [
            {
                "name": "co2_cap",
                "period": None,
                "cap_t": 100.0,
                "row_scale": 0.1,
                "mu": 0.0,
                "shadow_price_eur_per_t": None,
                "binding": False,
            }
        ],
    }
    a = co2_cap.annual(one, 80.0, {None: 80.0})
    assert a["t_per_yr"] == pytest.approx(800.0) and a["period"] is None
    assert a["binding"] is False and a["shadow_price_eur_per_t"] is None


def test_the_card_carries_the_limit_and_the_price_only_when_reached():
    from services.results.headline_kpis import _co2

    assert _co2(None) is None
    card = _co2(co2_cap.annual(_GOAL, 2000.0, {2030: 500.0, 2035: 1500.0}))
    assert card["id"] == "co2_annual" and card["unit"] == "tCO2/yr"
    assert card["value"] == pytest.approx(1000.0) and card["period"] == 2030
    assert card["limit_t_per_yr"] == 1000.0
    assert card["reached"] is True and card["shadow_price_eur_per_t"] == 180.0
    slack = _co2(
        {
            "limit_t_per_yr": 1000.0,
            "t_per_yr": 0.0,
            "binding": False,
            "shadow_price_eur_per_t": 5.0,
            "period": None,
        }
    )
    assert slack["reached"] is False and slack["shadow_price_eur_per_t"] is None
    assert slack["state"] == "zero"


def test_a_card_whose_co2_cannot_be_counted_is_not_zero():
    from services.results.headline_kpis import _co2

    card = _co2(
        {
            "limit_t_per_yr": 1000.0,
            "t_per_yr": None,
            "binding": False,
            "shadow_price_eur_per_t": None,
            "period": None,
        }
    )
    assert card["value"] is None and card["state"] == "unavailable"


def test_expert_emissions_lists_the_goals_cap_like_a_stored_one():
    from services.results.emissions import compute_emissions

    n = _hand()
    status, _, sink, _ = _solve(n, co2_limit_t_per_yr=6.0 / TO_ROW)
    assert status == "ok"
    out = compute_emissions(n, "lopf", result_df=_live, goal_cap=sink["last_co2_cap"])
    (cap,) = out["caps"]
    assert cap["goal"] is True and cap["name"] == "co2_cap"
    assert cap["cap_tCO2"] == pytest.approx(6.0) and cap["used_tCO2"] == pytest.approx(
        6.0
    )
    assert cap["binding"] is True
    assert cap["shadow_price_eur_per_tCO2"] == pytest.approx(-180.0, rel=1e-6)
    assert out["cap"]["name"] == "co2_cap"
    assert compute_emissions(n, "lopf", result_df=_live)["caps"] == []


def test_a_period_with_no_counted_tonnes_reads_not_available_never_zero():
    a = co2_cap.annual(_GOAL, 2000.0, {2030: 500.0})  # 2035 withheld
    assert a["t_per_yr"] is None and a["by_period"] == {}
    entries = co2_cap.cap_entries(_GOAL, 2000.0, {2030: 500.0})
    assert entries[1]["used_tCO2"] is None and entries[1]["slack_tCO2"] is None
    assert co2_cap.annual(_GOAL, None, {})["t_per_yr"] is None


def test_the_goal_and_a_stored_cap_share_the_binding_verdict():
    # Used up (slack 0) binds even with a zero dual, as `_cap_binding` says.
    from services.results.emissions import _cap_binding

    row = {
        "name": "co2_cap",
        "period": None,
        "cap_t": 100.0,
        "row_scale": 1.0,
        "mu": 0.0,
        "shadow_price_eur_per_t": 0.0,
    }
    (cap,) = co2_cap.cap_entries({"rows": [row]}, 100.0, {})
    assert cap["binding"] is True is _cap_binding(0.0, 0.0, 100.0)


def test_an_extra_lp_that_cannot_run_does_not_blame_the_network():
    n = _hand()
    goal = {
        "limit_t_per_yr": 1.0,
        "rows": [
            {
                "name": "co2_cap",
                "period": None,
                "cap_t": 1.0,
                "row_scale": 1.0,
                "mu": None,
                "shadow_price_eur_per_t": None,
            }
        ],
    }
    ref = co2_cap.unreachable(n, goal, {}, "highs")
    assert ref["without_limit"] == "not_found" and ref["least_t_per_yr"] is None
    assert ref["message"] == (
        "CO₂ limit: no design meets 1 t CO₂/yr. The least "
        "emissions reachable could not be found."
    )


# ── a hand-made multi-period network ──────────────────────────────────────


def _hand_staged():
    """`_hand` over two periods: 2 snapshots × 438 h = 0.1 yr per period,
    years 5 and 10, objective weights 5 and 4 (2040 discounted)."""
    n = _hand(weight=438.0)
    n.set_investment_periods([2030, 2040])
    n.investment_period_weightings.loc[:, "years"] = [5.0, 10.0]
    n.investment_period_weightings.loc[:, "objective"] = [5.0, 4.0]
    return n


_MULTI = {"multi_investment_periods": True}


def test_a_periods_constant_is_the_limit_times_its_years_times_its_year_share():
    n = _hand_staged()
    co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1000.0, **_MULTI), print, {})
    gc = n.global_constraints
    assert gc["constant"].to_dict() == {
        "co2_cap_2030": pytest.approx(500.0, rel=1e-12),  # 1000 × 5 × 0.1
        "co2_cap_2040": pytest.approx(1000.0, rel=1e-12),  # 1000 × 10 × 0.1
    }
    assert co2_cap.row_scale(n, 2040) == pytest.approx(1.0, rel=1e-12)
    assert co2_cap.row_scale(n, None) == pytest.approx(0.2, rel=1e-12)


def test_rows_are_per_period_only_with_the_flag_and_period_snapshots():
    on, off = SolverConfig(**_MULTI), SolverConfig()
    assert co2_cap.periods(_hand_staged(), on) == [2030, 2040]
    assert co2_cap.periods(_hand_staged(), off) == [None]
    assert co2_cap.periods(_hand(), on) == [None]


def test_a_periods_price_undoes_the_objective_discount():
    n = _hand_staged()
    assert co2_cap.shadow_price(n, 2040, -72.0) == pytest.approx(180.0)  # × 10 / 4
    assert co2_cap.shadow_price(n, None, -72.0) == 72.0
    assert co2_cap.shadow_price(n, 2040, float("nan")) is None
    n.investment_period_weightings.loc[2040, "objective"] = 0.0
    assert co2_cap.shadow_price(n, 2040, -72.0) is None


def test_a_binding_multi_period_cap_prices_each_periods_switch_alike():
    # Cap 60 % of the grid's 0.5 t × 10 MW all year: 40 % moves to clean at
    # +90 EUR/MWh, 0.5 t a MWh, 180 EUR/t in each period whatever its discount.
    n = _hand_staged()
    status, _, sink, _ = _solve(n, co2_limit_t_per_yr=0.6 * 43_800.0, **_MULTI)
    assert status == "ok"
    rows = {r["name"]: r for r in sink["last_co2_cap"]["rows"]}
    assert rows["co2_cap_2030"]["mu"] == pytest.approx(-180.0, rel=1e-6)
    assert rows["co2_cap_2040"]["mu"] == pytest.approx(-72.0, rel=1e-6)
    for r in rows.values():
        assert r["shadow_price_eur_per_t"] == pytest.approx(180.0, rel=1e-6)


def test_capture_reads_no_dual_from_a_missing_or_nan_mu():
    n = _hand()
    co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1.0), print, {})
    built = getattr(n, co2_cap._BUILT)["rows"]
    gc = n.c.global_constraints
    gc.static.loc["co2_cap", "mu"] = -3.0
    assert co2_cap.capture(n, 1.0, built)["rows"][0]["mu"] == -3.0
    gc.static.loc["co2_cap", "mu"] = np.nan
    assert co2_cap.capture(n, 1.0, built)["rows"][0]["mu"] is None
    gc.static = gc.static.drop(columns="mu")
    (row,) = co2_cap.capture(n, 1.0, built)["rows"]
    assert row["mu"] is None and row["shadow_price_eur_per_t"] is None
    gc.static = gc.static.drop(index="co2_cap")
    assert co2_cap.capture(n, 1.0, built)["rows"][0]["mu"] is None


def test_the_extra_lp_counts_only_an_optimal_solve(monkeypatch):
    # The model holds a solution from a first solve; a second that stops at a
    # time limit must not report it as the least reachable.
    n = _hand()
    co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1.0), print, {})
    n.optimize.create_model()
    lhs = co2_cap.rebuild_rows(n, n.snapshots)
    n.model.solve(solver_name="highs")
    monkeypatch.setattr(
        type(n.model), "solve", lambda *_a, **_kw: ("warning", "time_limit")
    )
    goal = {
        "limit_t_per_yr": 1.0,
        "rows": [dict(_GOAL["rows"][1], name="co2_cap", period=None, row_scale=TO_ROW)],
    }
    ref = co2_cap.unreachable(n, goal, lhs, "highs")
    assert ref["without_limit"] == "not_found" and ref["least_t_per_yr"] is None


def test_the_rebuild_leaves_a_period_outside_the_window_alone():
    n = _hand_staged()
    n.generators_t[OVERRIDE_ATTR] = pd.DataFrame({"grid": 0.1}, index=n.snapshots)
    co2_cap.apply(n, SolverConfig(co2_limit_t_per_yr=1e9, **_MULTI), print, {})
    n.optimize.create_model(multi_investment_periods=True)
    first = n.snapshots[n.snapshots.get_level_values(0) == 2030]
    lhs = co2_cap.rebuild_rows(n, first)
    p = n.model["Generator-p"].sel(name="grid")
    for period, want in ((2030, 438.0 * 5 * 0.1), (2040, 438.0 * 10 * 0.5)):
        sns = n.snapshots[n.snapshots.get_level_values(0) == period]
        got = co2_cap._row_coefficients(
            lhs[f"co2_cap_{period}"], p.sel(snapshot=sns).labels.values
        )
        assert got.tolist() == pytest.approx([want, want])


def test_the_headline_price_is_a_binding_rows_and_a_zero_scale_row_is_unread():
    slack = dict(_GOAL["rows"][1], shadow_price_eur_per_t=99.0)
    a = co2_cap.annual({"rows": [slack]}, None, {2035: 10.0})
    assert a["binding"] is False and a["shadow_price_eur_per_t"] is None
    zero = dict(_GOAL["rows"][1], row_scale=0.0)
    a = co2_cap.annual({"rows": [zero]}, None, {2035: 10.0})
    assert a["t_per_yr"] is None and a["by_period"] == {}
