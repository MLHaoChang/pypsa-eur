"""
Preflight validation of the commercial layer (Edge Investment Case P1 WP1.8).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.8
`validate_for_run` reports `commercial.*` issues before any solver time is
spent. A config that cannot bind is an error, the same refusal the solve would
make, stated earlier. Everything that binds but is likely to mislead is a
warning: circulation through the grid (`commercial.arbitrage_loop`), a demand
interval finer than the axis (`commercial.demand_resolution`), full monthly
demand charges on partly modelled months (`commercial.demand_partial_months`,
spec §5.2), and a tariff that is not valid for the modelled dates. The PPA and
DR-contract double-count checks need P2's contracts (plan deviation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.solver_service import SolverConfig
from services.validation_service import validate_for_run
from tests.fixtures.investment_case.edge_15min import build_edge_15min

ENERGY = {"id": "e", "kind": "energy", "unit": "per_kwh",
          "periods": [{"name": "all", "rate": 0.10}]}


def _tariff(*items, valid_from="2030-01-01", valid_to=None):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": valid_from,
            "valid_to": valid_to, "items": list(items)}


def _codes(n, commercial, **cfg):
    issues = validate_for_run(n, SolverConfig(commercial=commercial, **cfg))
    return {i.code: i for i in issues if i.code.startswith("commercial.")}


def test_no_commercial_config_adds_nothing():
    assert _codes(build_edge_15min(), None) == {}


def test_a_config_that_cannot_bind_is_an_error():
    codes = _codes(build_edge_15min(), {"poc_link": "ghost", "import_tariff": _tariff(ENERGY)})
    assert codes["commercial.binding_invalid"].severity == "error"
    assert "ghost" in codes["commercial.binding_invalid"].message


def test_a_group_naming_a_non_link_is_an_error():
    codes = _codes(build_edge_15min(), {"poc_link": "import", "group_contract": "g",
                                        "group_members": ["import", "pv"], "group_cap_mw": 10.0})
    assert codes["commercial.binding_invalid"].severity == "error"
    assert "'pv'" in codes["commercial.binding_invalid"].message


def test_export_paying_more_than_import_costs_warns_arbitrage_loop():
    from services.commercial import lp_bindings as L

    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame({"export": np.full(672, 500.0)},
                                                  index=n.snapshots)
    ref = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
    codes = _codes(n, {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
                       "import_tariff": _tariff(ENERGY)})
    issue = codes["commercial.arbitrage_loop"]
    assert issue.severity == "warning" and "672" in issue.message


def test_a_demand_interval_finer_than_the_axis_warns():
    n = build_edge_15min()
    n.set_snapshots(n.snapshots[::4])  # hourly
    n.snapshot_weightings.loc[:, :] = 1.0
    demand = {"id": "d", "kind": "demand", "unit": "per_kw_month", "settlement": "15min",
              "periods": [{"name": "all", "rate": 10.0}]}
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(demand)})
    assert codes["commercial.demand_resolution"].severity == "warning"


def test_partial_demand_months_warn():
    demand = {"id": "d", "kind": "demand", "unit": "per_kw_month",
              "periods": [{"name": "all", "rate": 10.0}]}
    codes = _codes(build_edge_15min(), {"poc_link": "import", "import_tariff": _tariff(demand)})
    assert "2030-01" in codes["commercial.demand_partial_months"].message


@pytest.mark.parametrize("vf,vt", [("2031-01-01", None), ("2029-01-01", "2029-12-31")])
def test_a_tariff_not_valid_for_the_modelled_dates_warns(vf, vt):
    codes = _codes(build_edge_15min(), {"poc_link": "import",
                                        "import_tariff": _tariff(ENERGY, valid_from=vf,
                                                                 valid_to=vt)})
    assert codes["commercial.tariff_out_of_validity"].severity == "warning"


def test_a_clean_config_has_no_commercial_issues():
    assert _codes(build_edge_15min(), {"poc_link": "import", "import_tariff": _tariff(ENERGY)}) == {}


# ── review round 1 (FAIL) ──────────────────────────────────────────────────

DEMAND = {"id": "d", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 10.0}]}
TIERS = {"id": "tiered", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
         "periods": [{"name": "all", "rate": 0.0}],
         "tiers": [{"threshold": 0, "rate": 0.10}, {"threshold": 100_000, "rate": 0.20}]}
FEE = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01",
       "capacity_fee": {"id": "fee", "kind": "capacity", "unit": "per_kw_year",
                        "periods": [{"name": "all", "rate": 60.0}]}}


def _two_periods():
    n = build_edge_15min()
    n.set_investment_periods([2030, 2040])
    return n


@pytest.mark.parametrize("extra", [
    {"import_tariff": _tariff(DEMAND)},
    {"import_tariff": _tariff(TIERS)},
    {"import_tariff": _tariff(ENERGY), "connection": FEE},
])
@pytest.mark.parametrize("strategy", ["rolling", "myopic"])
def test_a_refusal_that_depends_on_the_solve_strategy_is_an_error(extra, strategy):
    """#1: the solve refuses demand, tiers and a capacity fee when it runs
    windowed (rolling, or myopic over periods); preflight must say so first."""
    if strategy == "rolling":
        n, cfg = build_edge_15min(), {"solve_strategy": "rolling"}
    else:
        n, cfg = _two_periods(), {"solve_strategy": "myopic", "multi_investment_periods": True}
    codes = _codes(n, {"poc_link": "import", **extra}, **cfg)
    assert codes["commercial.binding_invalid"].severity == "error", codes
    assert strategy in codes["commercial.binding_invalid"].message


def test_a_full_solve_over_periods_accepts_demand():
    codes = _codes(_two_periods(), {"poc_link": "import", "import_tariff": _tariff(DEMAND)},
                   multi_investment_periods=True)
    assert "commercial.binding_invalid" not in codes


def _with_export(n):
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    return n


def _price(n, value):
    from services.commercial import lp_bindings as L

    n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame({"export": np.full(len(n.snapshots), value)},
                                                  index=n.snapshots)
    return {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}


def test_the_arbitrage_check_sees_every_group_member():
    """#2: a cheap member plus a high export price is a loop, even when the
    PoC itself is expensive."""
    n = _with_export(build_edge_15min())
    n.links.loc["import", "marginal_cost"] = 100.0
    n.add("Link", "import2", bus0="grid", bus1="poc", p_nom=80.0, carrier="AC")
    ref = _price(n, 50.0)
    energy = {**ENERGY, "periods": [{"name": "all", "rate": 0.01}]}
    codes = _codes(n, {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
                       "import_tariff": _tariff(energy), "group_contract": "g",
                       "group_members": ["import", "import2"], "group_cap_mw": 100.0})
    assert "commercial.arbitrage_loop" in codes


def test_a_tiered_import_price_is_counted_in_the_arbitrage_check():
    """#3: convex tiers are LP terms, not adders; their cheapest rate still
    makes import cost more than the export price pays."""
    n = _with_export(build_edge_15min())
    ref = _price(n, 50.0)
    codes = _codes(n, {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
                       "import_tariff": _tariff(TIERS)})
    assert "commercial.arbitrage_loop" not in codes


@pytest.mark.parametrize("commercial,fragment", [
    ({"poc_link": "import", "import_tariff": _tariff(
        {"id": "x", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
         "periods": [{"name": "all", "rate": 0.01}]})}, "export"),
    ({"poc_link": "import", "import_tariff_id": "lib-tariff"}, "import_tariff_id"),
    ({"poc_link": "import", "import_tariff": _tariff(ENERGY),
      "connection": {"kind": "non_firm", "import_cap_mw": 90.0}}, "90"),
])
def test_each_refusal_class_is_an_error(commercial, fragment):
    codes = _codes(build_edge_15min(), commercial)
    issue = codes["commercial.binding_invalid"]
    assert issue.severity == "error" and fragment in issue.message, issue.message


def test_a_two_way_poc_is_an_error():
    n = build_edge_15min()
    n.links.loc["import", "p_min_pu"] = -1.0
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(ENERGY)})
    assert "reverse flow" in codes["commercial.binding_invalid"].message


def test_a_stale_export_price_axis_is_an_error():
    n = _with_export(build_edge_15min())
    ref = _price(n, 50.0)
    n.links_t["ic_export_price"] = n.links_t["ic_export_price"].iloc[:10]
    codes = _codes(n, {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
                       "import_tariff": _tariff(ENERGY)})
    assert codes["commercial.binding_invalid"].severity == "error"


def test_a_coarse_stretch_of_a_mixed_axis_warns_on_resolution():
    """#5: the median step hides an hourly January in a mostly 15-min axis."""
    n = build_edge_15min()
    hourly = pd.date_range("2030-01-01", periods=24, freq="h")
    fine = pd.date_range("2030-01-02", periods=96 * 3, freq="15min")
    idx = hourly.append(fine)
    n.set_snapshots(idx)
    demand = {**DEMAND, "settlement": "15min"}
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(demand)})
    assert "commercial.demand_resolution" in codes


def test_a_crash_after_binding_is_a_warning_not_a_500(monkeypatch):
    """#6: preflight never takes the validation route down."""
    from services.commercial import lp_bindings as L

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(L, "circulation_risk_snapshots", boom)
    codes = _codes(build_edge_15min(), {"poc_link": "import", "import_tariff": _tariff(ENERGY)})
    assert codes["commercial.preflight_incomplete"].severity == "warning"


def test_a_tariff_on_a_non_datetime_axis_is_refused():
    n = build_edge_15min()
    n.set_snapshots(pd.RangeIndex(96))
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(ENERGY)})
    assert "datetime" in codes["commercial.binding_invalid"].message


def test_a_later_investment_period_outside_validity_warns():
    """#7: period 2040 reuses 2030 timestamps; the tariff ends in 2035."""
    codes = _codes(_two_periods(), {"poc_link": "import",
                                    "import_tariff": _tariff(ENERGY, valid_to="2035-12-31")},
                   multi_investment_periods=True)
    assert "2040" in codes["commercial.tariff_out_of_validity"].message


def test_a_binding_error_names_no_link_it_did_not_find():
    """#8: the issue does not point at the PoC for a tariff-level fault."""
    codes = _codes(build_edge_15min(), {"poc_link": "import", "import_tariff_id": "lib"})
    assert codes["commercial.binding_invalid"].component_class == ""


# ── review round 2 ─────────────────────────────────────────────────────────


def test_weather_year_timestamps_under_later_periods_are_not_out_of_validity():
    """B: PyPSA-Eur's layout (2013 weather timestamps, periods 2030/2040); the
    periods decide which tariff years are modelled."""
    n = build_edge_15min()
    n.set_snapshots(pd.date_range("2013-01-07", periods=672, freq="15min"))
    n.set_investment_periods([2030, 2040])
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(ENERGY)},
                   multi_investment_periods=True)
    assert "commercial.tariff_out_of_validity" not in codes
    early = _codes(n, {"poc_link": "import",
                       "import_tariff": _tariff(ENERGY, valid_from="2035-01-01")},
                   multi_investment_periods=True)
    assert "2030" in early["commercial.tariff_out_of_validity"].message


def test_a_very_coarse_axis_still_warns_on_resolution():
    """C: every step above the gap bound is the resolution, not a gap."""
    n = build_edge_15min()
    n.set_snapshots(pd.date_range("2030-01-01", periods=30, freq="2D"))
    codes = _codes(n, {"poc_link": "import", "import_tariff": _tariff(
        {"id": "d", "kind": "demand", "unit": "per_kw_month",
         "periods": [{"name": "all", "rate": 10.0}]})})
    assert "commercial.demand_resolution" in codes


def test_a_connection_on_a_non_datetime_axis_is_refused():
    """D: `available_from` on an integer axis would close the PoC for good."""
    n = build_edge_15min()
    n.set_snapshots(pd.RangeIndex(96))
    codes = _codes(n, {"poc_link": "import", "connection": {
        "kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01"}})
    assert "datetime" in codes["commercial.binding_invalid"].message


def test_demand_items_selection_is_refused_not_ignored():
    """Gate finding #4: a selection the binding does not implement would
    charge every demand item while the user believes some are excluded."""
    codes = _codes(build_edge_15min(), {"poc_link": "import", "import_tariff": _tariff(ENERGY),
                                        "demand_items": ["d"]})
    assert "demand_items" in codes["commercial.binding_invalid"].message
