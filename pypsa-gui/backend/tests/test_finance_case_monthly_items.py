"""
A monthly-billed item on a template that represents a year with fewer than 12
billing months (IC U1 follow-up, item g; owner decision).

A week weighted to a year (Σw = 8,760 h) passes the C3 annual check with
factor 1, yet a demand charge is billed only for the month(s) the week
touches: the ledger's `per_item_sampled` is that month's charge. The rule:

1. f == 1 and fewer than 12 billing months present → each monthly item's line
   is None, flagged `monthly_item_months_missing:<item>:<months present>`, on
   the actual and the counterfactual side; the operating cash is then not
   established (plan C12).
2. With `FinanceInputs.annualise` the lines are scaled by 12 / months instead,
   flagged `template_annualised_monthly:<item>:<factor>` (the unweighted-week
   path's flag); an item restricted to some months stays None.
3. Twelve weeks, one per month, weighted to their months: no change, no flag.

Hand numbers: the site's load peaks at 45 MW every evening, so the
counterfactual (the site without the PV and BESS) bills 45,000 kW × 9 €/kW =
405,000 € a month: × 12 = 4,860,000 € a year. The BESS shaves the actual peak
to 35.5 MW: 319,500 € a month, × 12 = 3,834,000 €.

The network is the probe's (`edge_hourly_year` cut to hourly weeks, the SOC
weighted by the 1 h step — the representative-period convention).
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.investment_case import edge_hourly_year as EY
from tests.test_finance_case_adapter import OWNED, _case, _fin
from tests.test_value_flow_reconciliation import DEMAND, FIXED, REF, TOU, _solve

WEEK = 8760.0 / 168
CF_YEAR = 45_000.0 * 9.0 * 12                  # 4,860,000
ACT_YEAR = 35_500.0 * 9.0 * 12                 # 3,834,000


def _m12_index():
    parts = []
    for m in range(1, 13):
        d = pd.Timestamp(2030, m, 1)
        d += pd.Timedelta(days=(7 - d.weekday()) % 7)
        parts.append(pd.date_range(d, periods=168, freq="h"))
    return parts[0].append(parts[1:])


def _build(start: str):
    import pypsa

    n = pypsa.Network()
    if start == "M12":
        idx = _m12_index()
        weight = np.array([pd.Period(f"2030-{m:02d}", freq="M").days_in_month * 24.0 / 168
                           for m in idx.month])
    else:
        idx = pd.date_range(start, periods=168, freq="h")
        weight = WEEK
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, "objective"] = weight
    n.snapshot_weightings.loc[:, "generators"] = weight
    n.snapshot_weightings.loc[:, "stores"] = 1.0
    for b in ("grid", "poc", "site"):
        n.add("Bus", b, carrier="AC")
    for c in ("AC", "grid", "solar", "battery"):
        n.add("Carrier", c)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=200.0, marginal_cost=60.0)
    n.add("Generator", "grid_sink", bus="grid", carrier="grid", p_nom=200.0, p_max_pu=0.0,
          p_min_pu=-1.0, marginal_cost=0.0)
    n.add("Link", "import", bus0="grid", bus1="poc", p_nom=80.0, eh_role="grid_import",
          carrier="AC")
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC", marginal_cost=0.0)
    n.add("Link", "poc_site", bus0="poc", bus1="site", p_nom=200.0, p_min_pu=-1.0, carrier="AC")
    pv, load = EY._profiles(idx)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=40.0, p_max_pu=pv,
          marginal_cost=0.0, overnight_cost=EY.PV_OVERNIGHT_PER_MW,
          discount_rate=EY.DISCOUNT_RATE, lifetime=EY.PV_LIFETIME)
    n.add("StorageUnit", "bess", bus="site", carrier="battery", p_nom=10.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95, cyclic_state_of_charge=True,
          marginal_cost=0.5, overnight_cost=EY.BESS_OVERNIGHT_PER_MW,
          discount_rate=EY.DISCOUNT_RATE, lifetime=EY.BESS_LIFETIME)
    n.add("Load", "site_load", bus="site", p_set=load)
    n.links_t["ic_export_price"] = pd.DataFrame({"export": np.full(len(idx), 30.0)}, index=idx)
    return n


def _commercial(items=(TOU, DEMAND, FIXED)) -> dict:
    return {"poc_link": "import", "export_link": "export", "export_price_ref": REF,
            "import_tariff": {"id": "t", "name": "t", "jurisdiction": "US",
                              "valid_from": "2029-01-01", "items": copy.deepcopy(list(items))},
            "contracts": [], "value_flows": copy.deepcopy(OWNED)}


def _lines(t) -> dict:
    return {ln.key: ln for ln in t.lines}


def _solved(start, items=(TOU, DEMAND, FIXED)):
    return _solve(_build(start), _commercial(items))


@pytest.mark.live_solve
def test_a_weighted_january_week_makes_the_demand_line_none_with_the_flag(reset_backend):
    from services.finance.engine import run_case

    n, cfg = _solved("2030-01-07 00:00")
    assert float(n.snapshot_weightings.objective.sum()) == pytest.approx(8760.0)
    case = _case(n, cfg)
    (t,), (cf,) = case.templates, case.counterfactual
    act, cfl = _lines(t), _lines(cf)
    assert act["bill:demand"].amount is None and cfl["bill:demand"].amount is None
    assert "monthly_item_months_missing:demand:1" in case.flags
    assert not any(f.startswith("template_annualised") for f in case.flags)
    # Energy and the fixed charge are a represented year already: unchanged.
    assert act["bill:standing"].amount == pytest.approx(-1_800.0)
    assert act["bill:energy"].amount is not None
    r = run_case(case)
    assert r.op_incremental["net"] is None
    assert "line_not_established:bill:demand" in r.op.reasons["operating"]


@pytest.mark.live_solve
def test_the_same_week_with_annualise_scales_by_twelve(reset_backend):
    n, cfg = _solved("2030-01-07 00:00")
    case = _case(n, cfg, _fin(annualise=True))
    (t,), (cf,) = case.templates, case.counterfactual
    assert _lines(cf)["bill:demand"].amount == pytest.approx(-CF_YEAR, abs=0.01)
    assert _lines(t)["bill:demand"].amount == pytest.approx(-ACT_YEAR, abs=0.01)
    assert "template_annualised_monthly:demand:12" in case.flags
    assert not any(f.startswith("monthly_item_months_missing") for f in case.flags)
    # f == 1: nothing but the monthly item is scaled.
    assert _lines(t)["bill:standing"].amount == pytest.approx(-1_800.0)
    assert not any(f.startswith("template_annualised:") for f in case.flags)


@pytest.mark.live_solve
def test_a_week_straddling_two_months_with_annualise_scales_by_six(reset_backend):
    n, cfg = _solved("2030-01-28 00:00")
    case = _case(n, cfg, _fin(annualise=True))
    (t,), (cf,) = case.templates, case.counterfactual
    # Two months billed at 405,000 each, × 6.
    assert _lines(cf)["bill:demand"].amount == pytest.approx(-CF_YEAR, abs=0.01)
    assert _lines(t)["bill:demand"].amount == pytest.approx(-ACT_YEAR, abs=0.01)
    assert "template_annualised_monthly:demand:6" in case.flags
    no_ann = _case(n, cfg)
    assert _lines(no_ann.templates[0])["bill:demand"].amount is None
    assert "monthly_item_months_missing:demand:2" in no_ann.flags


@pytest.mark.live_solve
def test_a_month_restricted_item_stays_none_with_annualise(reset_backend):
    winter = {**DEMAND, "id": "winter", "periods": [{"name": "w", "rate": 9.0,
                                                     "months": [1, 2, 12]}]}
    n, cfg = _solved("2030-01-07 00:00", items=(TOU, winter, FIXED))
    case = _case(n, cfg, _fin(annualise=True))
    assert _lines(case.templates[0])["bill:winter"].amount is None
    assert _lines(case.counterfactual[0])["bill:winter"].amount is None
    assert "annualise_monthly_item_not_established:winter" in case.flags


@pytest.mark.live_solve
def test_twelve_weighted_weeks_one_per_month_change_nothing(reset_backend):
    n, cfg = _solved("M12")
    for fin in (_fin(), _fin(annualise=True)):
        case = _case(n, cfg, fin)
        (t,), (cf,) = case.templates, case.counterfactual
        assert _lines(cf)["bill:demand"].amount == pytest.approx(-CF_YEAR, abs=0.01)
        assert _lines(t)["bill:demand"].amount is not None
        assert not any(f.startswith(("monthly_item_months_missing", "template_annualised"))
                       for f in case.flags)
