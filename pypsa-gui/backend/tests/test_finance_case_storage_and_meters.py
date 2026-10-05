"""
The finance adapter on a small PV + battery site (IC U1 follow-up):

* GS Q5 — under the `single_owner` template the PoC meter Links (import,
  export) are owner-assigned assets; uncosted, they are not investments: no
  capex, no COD needed, flagged `meter_link_not_investment:<name>`. A meter
  Link with a typed `overnight_cost` stays an asset.
* Owner decision 6 — the storage LCOS's inputs: per owner storage asset the
  year's discharge and charge (objective-weighted) and the charging cost at
  what the site actually paid: the grid share of each interval's charge at
  the committed import price (TOU + the grid supply's price), the rest
  (on-site PV surplus) at the export revenue forgone.

The site (24 hourly snapshots, each weighted 365 h — a year; the SOC on the
1 h step): 1 MW load; 2 MW PV 10–14 h; a 1 MW / 2 h BESS (η 0.95 each way);
TOU 60 €/MWh 0–6 h, 180 €/MWh otherwise; the grid supply at 20 €/MWh; export
at a flat 10 €/MWh into an uncosted sink. So a MWh charged at night costs
60 + 20 = 80 €; a MWh of midday PV surplus would have earned 10 €.
"""
from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.finance import ESCALATION_CLASSES, FinanceInputs
from tests.test_finance_case_adapter import COD
from tests.test_value_flow_reconciliation import REF, TOU, VF, _solve

W = 365.0


def _site():
    import pypsa

    n = pypsa.Network()
    idx = pd.date_range("2030-01-01", periods=24, freq="h")
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, "objective"] = W
    n.snapshot_weightings.loc[:, "generators"] = W
    n.snapshot_weightings.loc[:, "stores"] = 1.0
    for b in ("grid", "site"):
        n.add("Bus", b, carrier="AC")
    for c in ("AC", "grid", "solar", "battery"):
        n.add("Carrier", c)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=50.0, marginal_cost=20.0)
    n.add("Generator", "grid_sink", bus="grid", carrier="grid", p_nom=50.0, p_max_pu=0.0,
          p_min_pu=-1.0, marginal_cost=0.0)
    n.add("Link", "import", bus0="grid", bus1="site", p_nom=10.0, carrier="AC")
    n.add("Link", "export", bus0="site", bus1="grid", p_nom=10.0, carrier="AC")
    hour = np.arange(24)
    midday = ((hour >= 10) & (hour < 14)).astype(float)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=2.0, p_max_pu=midday,
          overnight_cost=800_000.0, lifetime=25.0, discount_rate=0.07)
    n.add("StorageUnit", "bess", bus="site", carrier="battery", p_nom=1.0, max_hours=2.0,
          efficiency_store=0.95, efficiency_dispatch=0.95, cyclic_state_of_charge=True,
          marginal_cost=0.5, overnight_cost=600_000.0, lifetime=15.0, discount_rate=0.07)
    n.add("Load", "load", bus="site", p_set=1.0)
    n.links_t["ic_export_price"] = pd.DataFrame({"export": np.full(24, 10.0)}, index=idx)
    return n


def _commercial(vf) -> dict:
    return {"poc_link": "import", "export_link": "export", "export_price_ref": REF,
            "import_tariff": {"id": "t", "name": "t", "jurisdiction": "US",
                              "valid_from": "2029-01-01", "items": [copy.deepcopy(TOU)]},
            "value_flows": vf}


def _single_owner(n) -> dict:
    """The `single_owner` template's own config: the meter Links owned too."""
    from models.commercial import CommercialConfig
    from services.commercial import value_flow_templates as T

    res = T.build("single_owner", n, CommercialConfig.model_validate(_commercial(VF)))
    vf = res.config.model_dump(mode="json")
    return vf


def _fin(**over) -> FinanceInputs:
    kw = dict(financial_close=date(2029, 1, 1), cod_by_asset={"pv": COD, "bess": COD},
              analysis_years=15, contingency_share=0.0,
              escalation={c: 0.02 for c in ESCALATION_CLASSES},
              degradation_by_asset={"pv": 0.0, "bess": 0.0}, tax_losses="offset_other_income",
              financing_fee_tax="not_deducted", wacc_nominal=0.07, cost_of_equity=0.10,
              inflation=0.02)
    kw.update(over)
    return FinanceInputs(**kw)


def _build(n, cfg, fin=None):
    import routers.results as R
    from services.results.finance_case import build_finance_case

    return build_finance_case(n, cfg, fin or _fin(), result_df=R._result_df)


@pytest.fixture
def solved(reset_backend):
    n = _site()
    vf = _single_owner(n)
    owned = {(o["component"], o["asset_id"]) for o in vf["asset_owners"]}
    assert {("Link", "import"), ("Link", "export"), ("Generator", "pv"),
            ("StorageUnit", "bess")} <= owned
    return _solve(n, _commercial(vf))


@pytest.mark.live_solve
def test_single_owner_meter_links_are_not_investments(solved):
    from services.finance.engine import run_case

    n, cfg = solved
    case = _build(n, cfg)                    # no COD for the meter Links: not refused
    assert {a.name for a in case.assets} == {"pv", "bess"}
    assert {"meter_link_not_investment:import", "meter_link_not_investment:export"} <= \
        set(case.flags)
    assert set(case.lp_basis.asset_discount_rates) == {"pv", "bess"}
    r = run_case(case)
    assert r.op.status["capex"] == "ok"
    assert r.op.capex.sum() == pytest.approx(800_000.0 * 2 + 600_000.0 * 1)


@pytest.mark.live_solve
def test_a_typed_meter_link_cost_stays_an_asset(solved):
    from services.finance.case import FinanceRefused

    n, cfg = solved
    n.links.loc["import", "overnight_cost"] = 1_000.0
    with pytest.raises(FinanceRefused) as exc:
        _build(n, cfg)
    assert exc.value.code == "cod_missing"
    case = _build(n, cfg, _fin(cod_by_asset={"pv": COD, "bess": COD, "import": COD}))
    assets = {a.name: a for a in case.assets}
    assert assets["import"].overnight_cost == pytest.approx(1_000.0 * 10.0)
    assert "meter_link_not_investment:import" not in case.flags
    assert "meter_link_not_investment:export" in case.flags


@pytest.mark.live_solve
def test_the_storage_charging_cost_is_what_the_site_paid(solved):
    from services.finance.engine import run_case

    n, cfg = solved
    case = _build(n, cfg)
    (t,) = case.templates
    sy = t.storage["bess"]
    hour = np.arange(24)
    ch = n.storage_units_t.p_store["bess"].to_numpy(float)
    dis = n.storage_units_t.p_dispatch["bess"].to_numpy(float)
    night, midday = hour < 6, (hour >= 10) & (hour < 14)
    # The battery cycles twice: at night from the grid, at midday from the PV.
    assert (ch[night] > 1e-6).any() and (ch[midday] > 1e-6).any()
    assert ch[~(night | midday)] == pytest.approx(0.0, abs=1e-6)
    imp = n.links_t.p0["import"].to_numpy(float)
    assert imp[midday] == pytest.approx(0.0, abs=1e-6)          # the midday charge is PV
    grid_mwh = W * ch[night].sum()
    pv_mwh = W * ch[midday].sum()
    assert sy.charge_mwh == pytest.approx(grid_mwh + pv_mwh, rel=1e-9)
    assert sy.discharge_mwh == pytest.approx(W * dis.sum(), rel=1e-9)
    assert sy.charge_import_cost == pytest.approx(80.0 * grid_mwh, rel=1e-9)
    assert sy.charge_surplus_cost == pytest.approx(10.0 * pv_mwh, rel=1e-9)
    assert sy.om_keys == ("asset:opex:StorageUnit:bess",)
    assert not t.storage.keys() - {"bess"}
    assert any(f.startswith("lcos_charge_from_surplus_at_export_price:bess:")
               for f in case.flags)

    # The engine's LCOS on it, by the hand formula: capex 600,000 at close
    # (index 0 = 2029), the BESS's vom (0.5 €/MWh discharged) and the charging
    # cost escalating 2 % from 2030, discharge flat; WACC 7 %, 15 years.
    r = run_case(case)
    vom = 0.5 * W * dis.sum()
    num, den = 600_000.0, 0.0
    for k in range(1, 16):
        d = 1.07 ** k
        num += (vom + sy.charge_import_cost + sy.charge_surplus_cost) * 1.02 ** (k - 1) / d
        den += sy.discharge_mwh / d
    assert r.metrics["lcos_nominal_per_mwh"] == pytest.approx(num / den, rel=1e-9)
    assert r.lcos["assets"]["bess"]["reasons"] == []


@pytest.mark.live_solve
def test_pv_charging_that_cannot_be_priced_is_none(solved):
    from services.commercial import lp_bindings as _lp
    from services.finance.engine import run_case

    n, cfg = solved
    prices = n.links_t[_lp.ENERGY_PRICE_ATTR]
    n.links_t[_lp.ENERGY_PRICE_ATTR] = prices.drop(columns=["export"])
    case = _build(n, cfg)
    sy = case.templates[0].storage["bess"]
    assert sy.charge_surplus_cost is None
    assert sy.charge_import_cost is not None
    assert "lcos_charge_price_not_established:surplus:bess" in case.flags
    r = run_case(case)
    assert r.metrics["lcos_nominal_per_mwh"] is None
    assert "charging_cost_not_established:bess" in r.lcos["reasons"]


@pytest.mark.live_solve
def test_export_revenue_is_energy_export_in_the_export_class_and_absent_counterfactual(solved):
    """GS Q14 pin: the ledger's export-price revenue reaches the template as
    `energy_export` lines in the `export` escalation class (split per owner
    generator or not); the counterfactual meters the served load with export
    0, so it has no export line; the export rate moves only those lines."""
    from services.finance.engine import run_case

    n, cfg = solved
    case = _build(n, cfg)
    (t,), (cf,) = case.templates, case.counterfactual
    exp = [ln for ln in t.lines if ln.source == "export_price"]
    assert exp and sum(ln.amount for ln in exp) > 0
    assert {(ln.stream, ln.esc_class) for ln in exp} == {("energy_export", "export")}
    assert not [ln for ln in cf.lines if ln.source == "export_price"
                or ln.stream == "energy_export"]
    base = run_case(case)
    faster = run_case(_build(n, cfg, _fin(escalation={**{c: 0.02 for c in ESCALATION_CLASSES},
                                                       "export": 0.05})))
    keys = {ln.key for ln in exp}
    for key, arr in base.op.lines.items():
        moved = not np.allclose(arr, faster.op.lines[key], rtol=0, atol=1e-9)
        assert moved == (key in keys), key


@pytest.mark.live_solve
def test_the_public_export_revenue_accessor(solved):
    """GS Q4: `value_flows.export_revenue` is the ledger's export line."""
    from services.results.value_flows import export_revenue

    n, cfg = solved
    w = n.snapshot_weightings.objective.to_numpy(float)
    flow = n.links_t.p0["export"].to_numpy(float)
    got = export_revenue(n, cfg.commercial)
    assert set(got) == {"_"}
    assert got["_"] == pytest.approx(float((w * flow * 10.0).sum()), rel=1e-12) and got["_"] > 0
    unpriced = {k: v for k, v in cfg.commercial.items() if k != "export_price_ref"} \
        if isinstance(cfg.commercial, dict) else \
        cfg.commercial.model_copy(update={"export_price_ref": None})
    assert export_revenue(n, unpriced) == {}
    n.links_t["ic_export_price"] = n.links_t["ic_export_price"].drop(columns=["export"])
    assert export_revenue(n, cfg.commercial) == {"_": None}
