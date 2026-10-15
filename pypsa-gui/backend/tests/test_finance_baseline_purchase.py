"""H2e on the site-today basis (owner decision 2026-10-15, (a)).

The baseline re-solve removes the plan's new electrolyser, so the site as it
stands cannot serve its H2 offtake. With a reference purchase for that load the
baseline buys what it sheds over the actual solve (grey H2 at the stated price)
and a CO2 price puts a certificate cost on it, as on the analytic basis.
Without one, the comparison is not established, as on the analytic basis.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.results import baseline as B
from tests.test_finance_baseline import _existing
from tests.test_finance_case_adapter import _case, _lines
from tests.test_finance_counterfactual_purchase import (CO2_KG, H2_MW, LHV, PRICE_KG, PURCHASE,
                                                        _fin_h2, _h2_site, _solved)

VOLL = 5000.0


@pytest.fixture(autouse=True)
def _fresh_cache():
    B.clear_cache()
    yield
    B.clear_cache()


def _site_today(**com_extra):
    """The H2 site with an existing PV and battery (kept) and a new, priced
    electrolyser (removed by the baseline), solved with VoLL slacks."""
    from services.results.finance_case import baseline_plan

    n = _h2_site(multi=False)
    n.generators.loc["pv", "p_nom_extendable"] = False
    n.generators.loc["pv", "p_nom"] = 5.0
    _existing(n, "pv")
    _existing(n, "bess", comp="StorageUnit")
    n.links.loc["electrolyser", ["overnight_cost", "lifetime", "discount_rate"]] = \
        [1_000_000.0, 20.0, 0.07]
    state: dict = {}
    n, cfg = _solved(multi=False, solve_kw={"voll": VOLL, "state": state}, network=n,
                     **com_extra)
    ll = state.get("last_lost_load")
    base = B.solve_baseline(n, cfg, baseline_plan(n, cfg))
    assert "electrolyser" in {name for _c, name, _k in base.plan.removed}
    return n, cfg, ll, base


def _kg(n) -> float:
    """The year's H2 in kg: the week's, annualised (`annualise`)."""
    w = n.snapshot_weightings.objective.to_numpy(float)
    return float((w * H2_MW).sum()) * 1000.0 / LHV * 8760.0 / float(w.sum())


@pytest.mark.live_solve
def test_the_baseline_buys_the_h2_it_cannot_serve(reset_backend):
    n, cfg, ll, base = _site_today(reference_purchases=[PURCHASE])
    case = _case(n, cfg, _fin_h2(), baseline=base, lost_load=ll)
    assert "counterfactual_basis:site_today" in case.flags
    assert "counterfactual_buys:h2_load" in case.flags
    assert not any(f.startswith("counterfactual_not_established") for f in case.flags)
    (cf,) = case.counterfactual
    buy = _lines(cf)["counterfactual:purchase:h2_load"]
    assert buy.stream == "fuel" and buy.amount == pytest.approx(-_kg(n) * PRICE_KG, rel=1e-6)
    # The H2 shed is bought, never priced at VoLL as well (it is not an
    # electric site load).
    assert "counterfactual:shed_at_voll" not in _lines(cf)


@pytest.mark.live_solve
def test_without_a_purchase_the_site_today_comparison_is_not_established(reset_backend):
    n, cfg, ll, base = _site_today()
    case = _case(n, cfg, _fin_h2(), baseline=base, lost_load=ll)
    assert "counterfactual_not_established:owner_conversion_load" in case.flags
    assert "counterfactual_baseline_unserved:h2_load" in case.flags
    (cf,) = case.counterfactual
    assert _lines(cf)["counterfactual:owner_conversion_load"].amount is None


@pytest.mark.live_solve
def test_the_co2_price_applies_to_the_purchase_and_both_sides_imports(reset_backend):
    from services.finance.engine import run_case
    from services.finance.report import _counterfactual_block

    co2 = {"eur_per_t_by_year": {2030: 80.0}, "money_year": 2030, "source": "test",
           "grid_t_per_mwh": 0.3, "grid_source": "test"}
    n, cfg, ll, base = _site_today(reference_purchases=[PURCHASE], co2_price=co2)
    case = _case(n, cfg, _fin_h2(), baseline=base, lost_load=ll)
    (cf,) = [t for t in case.counterfactual if t.first_year == min(
        t.first_year for t in case.counterfactual)]
    keys = {ln.key for t in case.counterfactual for ln in t.lines}
    assert {"co2:purchase:h2_load", "co2:grid_import"} <= keys
    assert any(ln.key == "co2:grid_import" for t in case.templates for ln in t.lines)
    got = _lines(cf)["co2:purchase:h2_load"].amount
    assert got == pytest.approx(-_kg(n) * CO2_KG * 80.0, rel=1e-6)
    block = _counterfactual_block(run_case(case), case)
    assert block["co2_priced"] is True
    assert block["avoided_co2_cost"] is not None
    assert np.isfinite(sum(block["avoided_co2_cost"]))


@pytest.mark.live_solve
def test_solver_noise_in_the_shed_is_not_unmet_load(reset_backend):
    """Gatekeeper review of #312: a per-snapshot noise floor, as
    `_excess_above`; 1e-9 MW on every snapshot is no load to buy or block."""
    import dataclasses

    n, cfg, ll, base = _site_today()
    noisy = dict(base.lost_load)
    shed = noisy["lost_load_t"].copy()
    shed["h2_load"] = 1e-9
    noisy["lost_load_t"] = shed
    case = _case(n, cfg, _fin_h2(), baseline=dataclasses.replace(base, lost_load=noisy),
                 lost_load=ll)
    assert not any(f.startswith(("counterfactual_not_established", "counterfactual_buys",
                                 "counterfactual_baseline_unserved")) for f in case.flags)


@pytest.mark.live_solve
def test_a_missing_shed_capture_is_unknown_not_unserved(reset_backend):
    """Gatekeeper review of #312: with VoLL on and no shed record, whether the
    site today serves the H2 is not known; never "cannot serve", never zero."""
    import dataclasses

    n, cfg, ll, base = _site_today(reference_purchases=[PURCHASE])
    case = _case(n, cfg, _fin_h2(), baseline=dataclasses.replace(base, lost_load=None),
                 lost_load=ll)
    assert "counterfactual_baseline_shed_unknown:h2_load" in case.flags
    assert "counterfactual_not_established:baseline_shed_unknown" in case.flags
    assert not any(f.startswith("counterfactual_baseline_unserved") for f in case.flags)
    (cf,) = case.counterfactual
    assert _lines(cf)["counterfactual:baseline_shed_unknown"].amount is None


def test_the_supply_is_priced_unescalated_plus_the_periods_co2_in_base_year_money():
    """Gatekeeper review of #312 (option 1): the LP cost sits on the footing
    of the LP's other costs, never escalated across periods; the CO2 price in
    force at the period's start, converted from its money year to the base
    year at the inflation."""
    import pandas as pd
    import pypsa
    from types import SimpleNamespace

    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2027, 2030], range(2)]))
    n.set_investment_periods([2027, 2030])
    n.add("Bus", "h2")
    n.add("Load", "h2_load", bus="h2", p_set=3.0)
    cfg = SimpleNamespace(
        commercial={"poc_link": "x", "reference_purchases": [PURCHASE],
                    "co2_price": {"eur_per_t_by_year": {"2027": 80.0, "2030": 120.0},
                                  "money_year": 2020, "source": "s"}},
        finance={"currency": "EUR", "price_basis": "nominal", "inflation": 0.02,
                 "financial_close": "2026-01-01"})
    from services.results.baseline import reference_supplies

    (sp,) = reference_supplies(n, cfg)
    per_mwh = 1000.0 / LHV
    conv = 1.02 ** (2027 - 2020)
    assert sp["mc"][2027] == pytest.approx(PRICE_KG * per_mwh + CO2_KG * per_mwh * 80.0 * conv)
    assert sp["mc"][2030] == pytest.approx(PRICE_KG * per_mwh + CO2_KG * per_mwh * 120.0 * conv)
    assert sp["bus"] == "h2" and sp["p_nom"] > 3.0


@pytest.mark.live_solve
def test_h2_the_plan_sheds_is_never_avoided_cost(reset_backend):
    """Gatekeeper review of #312: the avoided purchase nets what the ACTUAL
    solve sheds of the load, per snapshot; 1 MW shed by the plan in every hour
    lowers the purchase by exactly that energy."""
    n, cfg, ll, base = _site_today(reference_purchases=[PURCHASE])
    shed = dict(ll or {})
    t = shed["lost_load_t"].copy()
    t["h2_load"] = 1.0
    shed["lost_load_t"] = t
    full = _lines(_case(n, cfg, _fin_h2(), baseline=base, lost_load=ll).counterfactual[0])
    net = _lines(_case(n, cfg, _fin_h2(), baseline=base, lost_load=shed).counterfactual[0])
    w = n.snapshot_weightings.objective.to_numpy(float)
    one_mw = float(w.sum()) * 1000.0 / LHV * 8760.0 / float(w.sum()) * PRICE_KG
    got = net["counterfactual:purchase:h2_load"].amount - full[
        "counterfactual:purchase:h2_load"].amount
    assert got == pytest.approx(one_mw, rel=1e-6)
