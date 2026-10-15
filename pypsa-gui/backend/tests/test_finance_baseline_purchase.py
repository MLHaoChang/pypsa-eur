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
