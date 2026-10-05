"""
The finance adapter: a solved network and its P3 ledger → a `FinanceCase`
(Edge Investment Case P4, plan C1, C3, C5, C6, C13, C14; WP4.6a).

- `rate_meter` factored out of `bill_site`: every P2 bill unchanged (a copy
  of the pre-refactor body is the oracle).
- F6: the counterfactual on a toy site by hand (1 MW load, 2 MW PV, TOU +
  demand charge, a grid-side supply generator at 50/MWh).
- The integration fixture (`build_edge_hourly_year`): the template is the
  ledger's owner net to the cent, the year-1 incremental EBITDA identity, a
  finite equity IRR (pinned); the BESS cycles and the site exports.
- C3 on the 7-day fixture (`template_not_annual:168`, `annualise`), a load-free
  site (incremental = total), the shed exclusion, the commodity cross-check
  refusals, the connection / supply flags, a lossy PoC, staged builds, owner
  resolution, contract lines and overnight costs.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.finance import ESCALATION_CLASSES, FinanceInputs
from services.commercial.lp_bindings import same_party
from services.finance.case import CONTRACT_CLASS, FinanceRefused
from tests.test_value_flow_reconciliation import (
    DEMAND, FEE, FEED_IN, FIXED, LEASE, LEVY, PPA, REF, TOU, VF, _commercial, _ledger, _network,
    _solve,
)

OWNED = {**copy.deepcopy(VF),
         "asset_owners": [{"asset_id": "pv", "component": "Generator", "owner": "site"},
                          {"asset_id": "bess", "component": "StorageUnit", "owner": "site"}]}
COD = date(2030, 1, 1)


def _fin(**over) -> FinanceInputs:
    kw = dict(financial_close=date(2029, 1, 1), cod_by_asset={"pv": COD, "bess": COD},
              analysis_years=15, contingency_share=0.0,
              escalation={c: 0.02 for c in ESCALATION_CLASSES},
              degradation_by_asset={"pv": 0.005}, tax_losses="offset_other_income",
              financing_fee_tax="not_deducted", wacc_nominal=0.07, cost_of_equity=0.10,
              inflation=0.02)
    kw.update(over)
    return FinanceInputs(**kw)


def _case(n, cfg, fin=None, **kw):
    import routers.results as R
    from services.results.finance_case import build_finance_case

    return build_finance_case(n, cfg, fin or _fin(), result_df=R._result_df, **kw)


def _refused(n, cfg, fin=None, **kw) -> FinanceRefused:
    with pytest.raises(FinanceRefused) as exc:
        _case(n, cfg, fin, **kw)
    return exc.value


def _edge7(**_):
    """The 7-day P1 edge site of the P3 fixtures, its grid supply made
    supply-only and export absorbed by an uncosted sink (WP4.0 review B1)."""
    n = _network()
    n.generators.loc["grid_supply", "p_min_pu"] = 0.0
    n.add("Generator", "grid_sink", bus="grid", carrier="grid", p_nom=200.0, p_max_pu=0.0,
          p_min_pu=-1.0, marginal_cost=0.0)
    n.links.loc["export", "marginal_cost"] = 0.0
    return n


def _owner_net(ledger, p, owner="site") -> float:
    total = 0.0
    for ln in ledger.periods[p]:
        if ln.basis != "cash":
            continue
        if same_party(ln.payee, owner):
            total += ln.amount
        elif same_party(ln.payer, owner):
            total -= ln.amount
    return total


def _lines(t) -> dict:
    return {ln.key: ln for ln in t.lines}


def _net(t) -> float:
    return sum(ln.amount for ln in t.lines)


def _flag_num(flags, prefix) -> float:
    got = [f for f in flags if f.startswith(prefix)]
    assert len(got) == 1, flags
    return float(got[0][len(prefix):])


# ── the rate_meter refactor pins every P2 bill ──────────────────────────────


def _legacy_bill_site(n, commercial, *, meter_history=None):
    """`bill_site` as it was before `rate_meter` was factored out (50cd1b5^),
    verbatim but for the module prefix: the oracle of the refactor."""
    from services.commercial import billing as B
    from services.commercial import hashing as _H
    from services.commercial import lp_bindings as _lp
    from services.commercial.tariff_engine import rate

    cfg = _lp._parse(commercial)
    tariff = cfg.import_tariff
    links = _lp.import_links(cfg)
    needed = links + ([cfg.export_link] if cfg.export_link else [])
    if tariff is None or not B._solved(n, needed):
        return B.SiteBill(per_period={}, flags=["not_solved"] if tariff is not None
                          else ["no_import_tariff"])
    flags: list[str] = []
    history = meter_history if meter_history is not None else (cfg.meter_history_peaks_kw or None)
    p0 = n.links_t.p0
    imp_all = np.sum([B._flow(p0, link, flags) for link in links], axis=0)
    exp_all = (B._flow(p0, cfg.export_link, flags) if cfg.export_link
               else np.zeros(len(n.snapshots)))
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    periods = list(n.snapshots.get_level_values(0).unique()) if multi else [None]
    poc = cfg.poc_link
    pn = n.links.at[poc, "p_nom_opt"] if "p_nom_opt" in n.links.columns else np.nan
    p_nom_mw = float(pn) if np.isfinite(pn) else float(n.links.at[poc, "p_nom"])
    years_w = (n.investment_period_weightings["years"] if multi else None)
    cap_rec = n.meta.get(_lp.META_CAPACITY) or {}
    cap_periods = None
    if cap_rec.get("contracted") or cap_rec.get("fixed"):
        cap_periods = set()
        for c in (cap_rec.get("contracted") or {}).values():
            cap_periods |= set(c.get("eur_per_mw_by_period") or {})
        for c in (cap_rec.get("fixed") or {}).values():
            cap_periods |= set(c.get("eur_by_period") or {})
    per_period, calendar, errors = {}, {}, {}
    for i, p in enumerate(periods):
        key = None if p is None else int(p)
        sel = (n.snapshots.get_level_values(0) == p) if multi else np.ones(len(n.snapshots), bool)
        ts = pd.DatetimeIndex(n.snapshots[sel].get_level_values(-1) if multi
                              else n.snapshots[sel])
        idx = ts.tz_localize("UTC") if cfg.timezone and ts.tz is None else ts
        step = B._step_hours(ts)
        represents, billing_period = B._represented(ts, w_all[sel], step)
        calendar[key] = billing_period
        active = True
        if multi:
            active = bool(n.get_active_assets("Link", p).reindex([poc]).fillna(False).iloc[0])
            if cap_periods is not None:
                active = str(key) in cap_periods
        dispatch = pd.DataFrame({"import_mw": imp_all[sel], "export_mw": exp_all[sel]},
                                index=idx)
        try:
            per_period[key] = rate(
                dispatch, tariff, step_hours=step, timezone=cfg.timezone,
                billing_period=billing_period, represents_hours=represents,
                meter_history=history if i == 0 else None,
                capacity_kw=p_nom_mw * B._KW_PER_MW if active else 0.0,
                power_factor=cfg.power_factor)
        except ValueError as exc:
            per_period[key] = None
            flags.append(f"period_not_billed:{'_' if key is None else key}:"
                         f"{'billing_period_unknown' if 'billing_period' in str(exc) else 'invalid_dispatch'}")
            errors[key] = str(exc)[:300]
    for res in per_period.values():
        for f in (res.flags.get("_tariff") if res is not None else None) or []:
            if f not in flags:
                flags.append(f)
    drift, solve = B._drift_flags(n, cfg)
    flags += drift
    provenance = {
        "tariff_hash": _H.digest(tariff), "tariff_hash_version": _H.HASH_VERSION, **solve,
        "period_years": ({int(p): float(years_w.loc[p]) for p in periods} if multi else None),
        "billing_calendar": calendar, "capacity_basis": {"link": poc, "p_nom_mw": p_nom_mw},
        "timezone": cfg.timezone, "period_errors": errors,
    }
    return B.SiteBill(per_period=per_period, flags=flags, provenance=provenance)


def _same_bill(a, b):
    assert a.flags == b.flags
    assert a.provenance == b.provenance
    assert set(a.per_period) == set(b.per_period)
    for k, ra in a.per_period.items():
        rb = b.per_period[k]
        assert (ra is None) == (rb is None)
        if ra is None:
            continue
        assert ra.per_item == rb.per_item and ra.per_item_sampled == rb.per_item_sampled
        assert ra.total == rb.total and ra.flags == rb.flags
        pd.testing.assert_frame_equal(ra.lines, rb.lines)
        pd.testing.assert_frame_equal(ra.monthly, rb.monthly)
        pd.testing.assert_frame_equal(ra.demand_lines, rb.demand_lines)


@pytest.mark.live_solve
@pytest.mark.parametrize("multi", [False, True], ids=["flat", "multi"])
def test_rate_meter_leaves_every_p2_bill_unchanged(reset_backend, multi):
    from services.commercial import billing as B
    from services.commercial import lp_bindings as _lp

    n, cfg = _solve(_network(multi=multi), _commercial(), multi=multi)
    _same_bill(B.bill_site(n, cfg.commercial), _legacy_bill_site(n, cfg.commercial))
    hist = {"2029-12": 30_000.0}
    _same_bill(B.bill_site(n, cfg.commercial, meter_history=hist),
               _legacy_bill_site(n, cfg.commercial, meter_history=hist))
    # rate_meter on the solved meter IS the site bill.
    parsed = _lp._parse(cfg.commercial)
    imp = n.links_t.p0["import"].clip(lower=0.0).to_numpy(float)
    exp = n.links_t.p0["export"].clip(lower=0.0).to_numpy(float)
    metered = B.rate_meter(n, parsed, imp, exp)
    site = B.bill_site(n, cfg.commercial)
    for k, res in site.per_period.items():
        assert metered.per_period[k].per_item_sampled == pytest.approx(res.per_item_sampled,
                                                                       abs=1e-6)
    no_tariff = {**cfg.commercial, "import_tariff": None}
    assert B.rate_meter(n, no_tariff, imp, exp).flags == ["no_import_tariff"]


# ── F6: the counterfactual on a toy site, by hand ───────────────────────────


def _f6_network():
    import pypsa

    n = pypsa.Network()
    idx = pd.date_range("2030-01-01", periods=8760, freq="h")
    n.set_snapshots(idx)
    for b in ("grid", "poc", "site"):
        n.add("Bus", b, carrier="AC")
    hour = np.asarray(idx.hour)
    midday = (hour >= 10) & (hour < 14)
    n.add("Generator", "grid_supply", bus="grid", p_nom=10.0, marginal_cost=50.0)
    n.add("Link", "import", bus0="grid", bus1="poc", p_nom=5.0, eh_role="grid_import")
    n.add("Link", "poc_site", bus0="poc", bus1="site", p_nom=10.0, p_min_pu=-1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=2.0,
          p_max_pu=midday.astype(float), marginal_cost=0.0, overnight_cost=1_000_000.0,
          discount_rate=0.07, lifetime=25.0)
    n.add("Load", "load", bus="site", p_set=1.0 + 0.5 * midday)
    return n


F6_VF = {**copy.deepcopy(VF),
         "asset_owners": [{"asset_id": "pv", "component": "Generator", "owner": "site"}]}


@pytest.mark.live_solve
def test_f6_the_counterfactual_on_a_toy_site_by_hand(reset_backend):
    """1 MW load (+0.5 MW 10–14 h), 2 MW PV (10–14 h, curtailed to the load:
    no export), TOU 60 / 180 $/MWh (night 0–6 h), 9 $/kW-month demand, supply
    at 50 $/MWh. Per day: counterfactual energy 6·60 + 14·180 + 4·1.5·180 =
    3,960, actual 6·60 + 14·180 = 2,880; demand 1,500 vs 1,000 kW a month;
    commodity 50 × 26 vs 50 × 20 MWh a day."""
    from services.finance.engine import run_case
    from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year

    com = {"poc_link": "import", "value_flows": F6_VF,
           "import_tariff": {"id": "t", "name": "t", "jurisdiction": "US",
                             "valid_from": "2029-01-01", "items": [TOU, DEMAND]}}
    n, cfg = _solve(_f6_network(), com)
    case = _case(n, cfg, _fin(cod_by_asset={"pv": COD}))
    (t,), (cf,) = case.templates, case.counterfactual
    act, cfl = _lines(t), _lines(cf)
    assert act["bill:energy"].amount == pytest.approx(-2_880.0 * 365, abs=0.01)
    assert cfl["bill:energy"].amount == pytest.approx(-3_960.0 * 365, abs=0.01)
    assert act["bill:demand"].amount == pytest.approx(-1_000 * 9.0 * 12, abs=0.01)
    assert cfl["bill:demand"].amount == pytest.approx(-1_500 * 9.0 * 12, abs=0.01)
    assert act["asset:opex:Generator:grid_supply"].amount == pytest.approx(-50.0 * 20 * 365,
                                                                           abs=0.01)
    # The counterfactual commodity is keyed like the actual one (review B1):
    # a shared key stays netted as a saving, never an "asset cost" of the PV.
    commodity = "asset:opex:Generator:grid_supply"
    assert cfl[commodity].amount == pytest.approx(-50.0 * 26 * 365, abs=0.01)
    assert (cfl[commodity].source, cfl[commodity].source_id) == ("counterfactual", "commodity")
    assert "counterfactual:commodity" not in cfl
    incremental = (3_960 - 2_880) * 365 + 500 * 9.0 * 12 + 50.0 * 6 * 365      # 557,700
    assert _net(t) - _net(cf) == pytest.approx(incremental, abs=0.01)
    # C5 first order: S = the avoided ENERGY-volume value (energy items +
    # commodity; the demand charge saving is not degraded — plan C5 review),
    # g = 1 (the only generator): 557,700 − 500 × 9 × 12 = 503,700.
    s_energy = incremental - 500 * 9.0 * 12
    assert act["bill_degradation:pv"].amount == pytest.approx(s_energy, abs=0.01)
    assert act["bill_degradation:pv"].degrades_with == "pv"
    assert act["bill_degradation_base:pv"].amount == pytest.approx(-s_energy, abs=0.01)
    assert act["bill_degradation_base:pv"].degrades_with is None
    assert {act[k].source for k in ("bill_degradation:pv", "bill_degradation_base:pv")} == \
        {"degradation"}
    assert t.energy_mwh == pytest.approx({"pv": 1.5 * 4 * 365})
    assert "degradation_bill_first_order" in case.flags
    assert not any(f.startswith("counterfactual_exceeds") for f in case.flags)
    assert case.assets[0].overnight_cost == pytest.approx(2_000_000.0)
    layer = (TaxLayer(name="corp", rate=0.25,
                      depreciation=(DepreciationClass("all", 1.0, sl_half_year(10)),)),)
    r = run_case(case, layers=layer)
    assert r.op_incremental["net"][1] == pytest.approx(incremental, abs=0.01)      # COD year
    # Year 2: the PV's energy-volume bill value degraded by 0.5 %, everything
    # escalated 2 % (the demand saving does not degrade).
    assert r.op_incremental["net"][2] == pytest.approx(
        incremental * 1.02 - s_energy * 0.005 * 1.02, abs=0.01)
    # LCOE (review B1): the PV's own costs are 0 (no fom / vom), so the value
    # is the incremental cash and LCOE = (PV capex + PV tax) / PV energy at the
    # cost of equity (10 %), all equity, offset losses:
    #   inc_k = 1.02^(k−1) · (557,700 − 503,700 · (1 − 0.995^(k−1))), k = 1…15
    #   tax_k = 0.25 · (inc_k − 2,000,000 · SL-10 half-year_k)
    #   LCOE = (2,000,000 + Σ tax_k / 1.1^k) / Σ 2,190 · 0.995^(k−1) / 1.1^k
    #        = 176.18 $/MWh (was 629.79 with the key mismatch).
    from services.finance.tax import sl_half_year as _sl

    dep = list(_sl(10)) + [0.0] * 5
    num, den = 2_000_000.0, 0.0
    for k in range(1, 16):
        inc_k = 1.02 ** (k - 1) * (incremental - s_energy * (1.0 - 0.995 ** (k - 1)))
        num += 0.25 * (inc_k - 2_000_000.0 * dep[k - 1]) / 1.1 ** k
        den += 2_190.0 * 0.995 ** (k - 1) / 1.1 ** k
    assert num / den == pytest.approx(176.178, abs=1e-3)
    assert r.metrics["lcoe_nominal_per_mwh"] == pytest.approx(num / den, rel=1e-9)


# ── the integration fixture ─────────────────────────────────────────────────


def _hourly_year(**vf_over):
    from tests.fixtures.investment_case.edge_hourly_year import build_edge_hourly_year

    n = build_edge_hourly_year()
    h = np.arange(len(n.snapshots))
    n.links_t["ic_export_price"] = pd.DataFrame(
        {"export": 30.0 + 20.0 * np.sin(h / 24 * 2 * np.pi)}, index=n.snapshots)
    vf = {**copy.deepcopy(OWNED), "export_revenue_to": "asset_owner", **vf_over}
    com = {"poc_link": "import", "export_link": "export", "export_price_ref": REF,
           "import_tariff": {"id": "t", "name": "t", "jurisdiction": "US",
                             "valid_from": "2029-01-01", "items": [TOU, DEMAND, FIXED]},
           "connection": FEE, "value_flows": vf}
    return n, com


# Pinned (HiGHS 1.x): 15 operating years, all equity, one 25 % layer on SL-10,
# every class escalating 2 %, PV degrading 0.5 %/yr. The LP's optimum is
# degenerate in dispatch (money can move between lines that degrade
# differently), hence 1e-5.
EQUITY_IRR_POST_TAX = 0.36207517      # energy-item S (plan C5 review)


@pytest.mark.live_solve
def test_the_integration_fixture_end_to_end(reset_backend):
    from services.commercial import billing as B
    from services.finance.engine import run_case
    from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year
    from services.results.finance_case import finance_case_hash

    n, com = _hourly_year()
    n, cfg = _solve(n, com)
    # The demand-charge commercial solve cycles the BESS and exports.
    w = n.snapshot_weightings.objective.to_numpy(float)
    assert float((w * n.storage_units_t.p_dispatch["bess"]).sum()) > 0
    export = n.links_t.p0["export"].to_numpy(float)
    assert float((w * export).sum()) > 0
    inputs, vf, ledger, _res = _ledger(n, cfg)
    case = _case(n, cfg)
    (t,), (cf,) = case.templates, case.counterfactual
    assert (t.first_year, t.money_year, case.base_year, case.cod) == (2030, 2030, 2030, COD)
    # The template is the ledger's owner net to the cent (the C5 pair nets 0).
    assert _net(t) == pytest.approx(_owner_net(ledger, "_"), abs=0.005)
    lines = _lines(t)
    assert lines["export_price:export_price:pv"].degrades_with == "pv"
    assert lines["bill_degradation:pv"].degrades_with == "pv"
    assert set(t.energy_mwh) == {"pv"}                     # generators only, never storage
    assert _lines(cf)["connection:fee"] == lines["connection:fee"]       # identical: they cancel
    assert t.energy_mwh["pv"] == pytest.approx(float((w * n.generators_t.p["pv"]).sum()))
    # The uncosted grid_sink passes the commodity check; the BESS shaved the peak.
    assert not any("commodity_not_established" in f for f in case.flags)
    assert "counterfactual_exceeds_connection" in case.flags
    assert "counterfactual_exceeds_supply" not in case.flags

    # Year-1 incremental EBITDA identity, each term from its own source.
    bill = B.bill_site(n, cfg.commercial).per_period[None].per_item_sampled
    load = n.loads_t.p_set["site_load"].to_numpy(float)
    imp = n.links_t.p0["import"].clip(lower=0.0).to_numpy(float)
    cf_bill = B.rate_meter(n, cfg.commercial, load, np.zeros(len(load))).per_period[None]
    price = n.links_t["ic_export_price"]["export"].to_numpy(float)
    export_value = float((w * export * price).sum())
    asset_opex = float((w * n.storage_units_t.p_dispatch["bess"]).sum()) * 0.5
    identity = ((sum(cf_bill.per_item_sampled.values()) + 60.0 * float((w * load).sum()))
                - (sum(bill.values()) + 60.0 * float((w * imp).sum()))
                + export_value - asset_opex)
    layer = (TaxLayer(name="corp", rate=0.25,
                      depreciation=(DepreciationClass("all", 1.0, sl_half_year(10)),)),)
    r = run_case(case, layers=layer)
    assert r.op_incremental["net"][1] == pytest.approx(identity, abs=0.01)
    assert r.op_incremental["net"][1] > 0
    irr = r.metrics["equity_post_tax_irr"]
    assert irr is not None and np.isfinite(irr)
    assert irr == pytest.approx(EQUITY_IRR_POST_TAX, abs=1e-5)
    # LCOE (review B1): the PV + BESS's own costs are the BESS's vom only (the
    # commodity and the C5 pair are no longer "asset costs"): 143.23 $/MWh,
    # was 609.46 with the key mismatch.
    assert r.metrics["lcoe_nominal_per_mwh"] == pytest.approx(143.23, abs=0.01)

    # C6 / C10 inputs and the hash.
    assets = {a.name: a for a in case.assets}
    assert assets["pv"].overnight_cost == pytest.approx(700_000.0 * 40)
    assert assets["bess"].overnight_cost == pytest.approx(1_000_000.0 * 10)
    assert (assets["pv"].carrier, assets["bess"].lifetime_years) == ("solar", 15.0)
    assert case.lp_basis.discount_rate == pytest.approx(0.07)
    assert case.lp_basis.asset_discount_rates == {"pv": 0.07, "bess": 0.07}
    assert _refused(n, cfg, _fin(cod_by_asset={"pv": COD, "bess": date(2031, 1, 1)})).code == \
        "cod_mismatch"
    h = finance_case_hash(case)
    assert len(h) == 16 and h == finance_case_hash(_case(n, cfg))
    assert h != finance_case_hash(_case(n, cfg, _fin(wacc_nominal=0.08)))


# ── C3: the template must be a year ─────────────────────────────────────────


@pytest.mark.live_solve
def test_a_seven_day_template_is_refused_or_annualised(reset_backend):
    from services.commercial import billing as B

    n, cfg = _solve(_edge7(), _commercial(OWNED, contracts=[]))
    assert _refused(n, cfg).code == "template_not_annual:168"
    # The bill is established on the partial year (demand items with no
    # billing period, not `period_not_billed`; plan round 2 R6).
    bill = B.bill_site(n, cfg.commercial)
    assert bill.per_period[None] is not None and bill.provenance["billing_calendar"][None] is None
    assert not any(f.startswith("period_not_billed") for f in bill.flags)
    inputs, _vf, ledger, _ = _ledger(n, cfg)
    case = _case(n, cfg, _fin(annualise=True))
    assert "template_annualised:52.14" in case.flags
    (t,), (cf,) = case.templates, case.counterfactual
    f = 8760.0 / 168.0
    assert _net(t) == pytest.approx(_owner_net(ledger, "_") * f
                                    - inputs.bill["_"]["demand"] * (12.0 - f), abs=0.01)
    # A monthly-billed item (the demand charge) scales by 12 / the months
    # present (one January week: 12), not 8,760 / 168 (review B5); the rest by f.
    assert _lines(t)["bill:demand"].amount == pytest.approx(
        -inputs.bill["_"]["demand"] * 12.0, abs=1e-6)
    assert "template_annualised_monthly:demand:12" in case.flags
    w = n.snapshot_weightings.objective.to_numpy(float)
    assert t.energy_mwh["pv"] == pytest.approx(float((w * n.generators_t.p["pv"]).sum()) * f)
    # The commodity is established on the meter basis, annualised too.
    served = n.loads_t.p_set["site_load"].to_numpy(float)
    assert _lines(cf)["asset:opex:Generator:grid_supply"].amount == pytest.approx(
        -50.0 * float((w * served).sum()) * f, abs=0.01)
    peak_cf = float(served.max()) * 1000.0
    assert _lines(cf)["bill:demand"].amount == pytest.approx(-9.0 * peak_cf * 12.0, rel=1e-9)


def test_the_period_year_and_the_leap_year_hours():
    import pypsa

    from services.results.finance_case import _period_year_and_hours

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2032-01-01", periods=8784, freq="h"))
    assert _period_year_and_hours(n, None) == (2032, 8784.0, 2032)
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-12-31 12:00", periods=48, freq="h"))
    n.snapshot_weightings["objective"] = [1.0] * 12 + [3.0] * 36
    assert _period_year_and_hours(n, None) == (2031, 120.0, 2031)       # the weight majority


# ── C13: the counterfactual's edges ─────────────────────────────────────────


@pytest.mark.live_solve
def test_a_load_free_generator_site_is_incremental_equals_total(reset_backend):
    from services.finance.engine import run_case
    from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year

    n = _edge7()
    n.remove("Load", "site_load")
    n.remove("StorageUnit", "bess")
    n.generators.loc["pv", "p_nom_extendable"] = False
    n.generators.loc["pv", "p_nom"] = 30.0
    vf = {**copy.deepcopy(VF), "asset_owners": [{"asset_id": "pv", "component": "Generator",
                                                 "owner": "site"}]}
    com = _commercial(vf, contracts=[])
    com.pop("connection")
    com["import_tariff"]["items"] = [TOU, DEMAND, FEED_IN]
    n, cfg = _solve(n, com)
    case = _case(n, cfg, _fin(annualise=True, cod_by_asset={"pv": COD}))
    (cf,) = case.counterfactual
    assert cf.lines and all(ln.amount == pytest.approx(0.0, abs=1e-9) for ln in cf.lines)
    assert not any(f.startswith("load_shed_excluded") for f in case.flags)
    layer = (TaxLayer(name="corp", rate=0.25,
                      depreciation=(DepreciationClass("all", 1.0, sl_half_year(10)),)),)
    r = run_case(case, layers=layer)
    total = r.op.revenue - r.op.costs
    assert np.allclose(r.op_incremental["counterfactual"], 0.0)
    assert np.allclose(r.op_incremental["net"], total)
    assert total[1] > 0                                     # it earns the export


@pytest.mark.live_solve
def test_shed_load_is_excluded_on_both_sides_and_disclosed(reset_backend):
    """The served load = demand − the DSR shed (per bus) − the VoLL shed (per
    load): the counterfactual is rated on it, and the actual meter carries the
    same shed (it never served it). Never valued (the P2 carry-in)."""
    from services.commercial import billing as B

    n = _edge7()
    n.links.loc["import", "p_nom_extendable"] = False
    n.links.loc["import", "p_nom"] = 1.0                   # scarce enough to shed
    n.generators.loc["pv", "p_nom_extendable"] = False
    n.generators.loc["pv", "p_nom"] = 5.0
    com = _commercial(OWNED, contracts=[])
    com.pop("connection")
    state: dict = {}
    n, cfg = _solve(n, com, state=state, dsr_price_eur_per_mwh=40.0, dsr_share_of_load=0.1,
                    dsr_buses=["site"], voll=1000.0)
    ll = state["last_lost_load"]
    w = n.snapshot_weightings.objective.to_numpy(float)
    dsr = n.buses_t["ic_dsr_p"]["site"].to_numpy(float)
    voll = ll["lost_load_t"]["site_load"].reindex(n.snapshots).fillna(0.0).to_numpy(float)
    assert (w * dsr).sum() > 0 and (w * voll).sum() > 0
    demand = n.loads_t.p_set["site_load"].to_numpy(float)
    served = demand - dsr - voll
    # The actual side carries the same shed: its supply balances the served load.
    supply = (n.links_t.p0["import"] - n.links_t.p0["export"] + n.generators_t.p["pv"]
              + n.storage_units_t.p["bess"]).to_numpy(float)
    assert float((w * supply).sum()) == pytest.approx(float((w * served).sum()), rel=1e-6)
    case = _case(n, cfg, _fin(annualise=True), lost_load=ll)
    assert _flag_num(case.flags, "load_shed_excluded:") == pytest.approx(
        float((w * (dsr + voll)).sum()), abs=0.01)
    (cf,) = case.counterfactual
    f = 8760.0 / 168.0
    want = B.rate_meter(n, cfg.commercial, served, np.zeros(len(served))).per_period[None]
    assert _lines(cf)["bill:energy"].amount == pytest.approx(
        -want.per_item_sampled["energy"] * f, abs=1e-6)
    (t,) = case.templates
    assert not any("dsr" in k or "voll" in k for k in _lines(t))      # disclosed, never cash


@pytest.mark.live_solve
@pytest.mark.parametrize("extra, reason", [
    ("peaker", "several_priced_generators"),
    ("grid_load", "ledger_cross_check"),
], ids=["several_grid_side_generators", "grid_side_load"])
def test_the_commodity_is_not_established_off_the_meter_basis(reset_backend, extra, reason):
    n = _edge7()
    if extra == "peaker":
        n.add("Generator", "grid_peaker", bus="grid", carrier="grid", p_nom=10.0,
              marginal_cost=90.0)
    else:
        n.add("Load", "grid_load", bus="grid", p_set=5.0)
    n, cfg = _solve(n, _commercial(OWNED, contracts=[]))
    case = _case(n, cfg, _fin(annualise=True))
    assert "counterfactual_commodity_not_established" in case.flags
    assert f"counterfactual_commodity_not_established:{reason}" in case.flags
    (cf,) = case.counterfactual
    assert _lines(cf)["asset:opex:Generator:grid_supply"].amount is None
    (t,) = case.templates
    assert all(ln.amount is not None for ln in t.lines if ln.source == "asset")


@pytest.mark.live_solve
def test_a_counterfactual_above_the_poc_and_the_supply_is_flagged(reset_backend):
    n = _edge7()
    n.links.loc["import", "p_nom_extendable"] = False
    n.links.loc["import", "p_nom"] = 42.0          # the BESS shaves the 45 MW evening peak
    n.generators.loc["grid_supply", "p_nom"] = 43.0
    com = _commercial(OWNED, contracts=[])
    com.pop("connection")
    n, cfg = _solve(n, com)
    case = _case(n, cfg, _fin(annualise=True))
    assert {"counterfactual_exceeds_connection", "counterfactual_exceeds_supply"} <= \
        set(case.flags)
    (cf,) = case.counterfactual
    assert all(ln.amount is not None for ln in cf.lines)       # flagged, rated as metered


@pytest.mark.live_solve
def test_a_lossy_poc_chain_refuses_the_case(reset_backend):
    n, cfg = _solve(_edge7(), _commercial(OWNED, contracts=[]))
    n.links.loc["poc_site", "efficiency"] = 0.98       # the structure check reads the network
    assert _refused(n, cfg, _fin(annualise=True)).code == \
        "counterfactual_not_established:lossy_poc"


# ── multi-period, staged builds ─────────────────────────────────────────────


@pytest.mark.live_solve
def test_multi_period_templates_and_a_staged_build(reset_backend):
    n, cfg = _solve(_network(multi=True), _commercial(OWNED), multi=True)
    case = _case(n, cfg, _fin(annualise=True))
    assert [(t.first_year, t.money_year) for t in case.templates] == [(2030, 2030), (2040, 2040)]
    assert case.base_year == 2030
    keys = [set(_lines(t)) for t in case.templates]
    assert "contract:ppa1:ppa_energy" in keys[0] and keys[0] == keys[1]     # stable keys
    # Money years (review B4): contracts in the period year (P2 indexes them
    # to it), every other line in the base year (P2 does not escalate them).
    for t in case.templates:
        for ln in t.lines:
            assert ln.money_year == (None if ln.source == "contract" else 2030), ln.key
    from services.finance.cashflow import build_operating
    from services.finance.timeline import build_timeline

    op = build_operating(case, build_timeline(case))
    tl = op.tl
    standing = op.lines["bill:standing"]                 # the same fixed charge each period
    assert standing[tl.index(2040)] / standing[tl.index(2039)] == pytest.approx(1.02)
    f = 8760.0 / 168.0
    for t in case.templates:
        p = n.generators_t.p.loc[t.first_year, "pv"].to_numpy(float)
        assert t.energy_mwh["pv"] == pytest.approx(0.25 * p.sum() * f)      # one year, not ×10
    n.generators.loc["pv", "build_year"] = 2040
    assert _refused(n, cfg, _fin(annualise=True)).code == "staged_build_not_supported"


# ── owners, contracts, overnight costs, dates ───────────────────────────────


@pytest.mark.live_solve
def test_owners_contract_lines_and_overnight_costs(reset_backend):
    vf = {**copy.deepcopy(VF), "export_revenue_to": "asset_owner",
          "participants": [{"id": "site", "name": "Site", "role": "offtaker"},
                           {"id": "developer", "name": "Dev", "role": "developer"}],
          "asset_owners": [{"asset_id": "pv", "component": "Generator", "owner": "developer"},
                           {"asset_id": "bess", "component": "StorageUnit", "owner": "site"}]}
    ppa = {**PPA, "seller": "developer", "changes_dispatch": False, "indexation_pct_per_year": 2.0,
           "base_year": 2028}
    n, cfg = _solve(_edge7(), _commercial(vf, contracts=[ppa, LEASE]))
    fin = _fin(annualise=True)
    assert _refused(n, cfg, fin).code == "owner_ambiguous"
    assert _refused(n, cfg, fin, owner="nobody").code == "owner_has_no_assets"

    # The developer: its PPA and export parts degrade with its PV; no bill, so
    # no counterfactual (incremental = its total).
    dev = _case(n, cfg, _fin(annualise=True, cod_by_asset={"pv": COD}), owner=" Developer")
    assert dev.owner == "developer" and dev.counterfactual == ()
    (t,) = dev.templates
    lines = _lines(t)
    ln = lines["contract:ppa1:ppa_energy"]
    assert (ln.esc_class, ln.indexation, ln.tenor_years, ln.contract_id) == \
        (CONTRACT_CLASS, 0.02, 10, "ppa1")
    assert ln.price == pytest.approx(20.0 * 1.02 ** 2) and ln.changes_dispatch is False
    assert ln.degrades_with == "pv" and ln.amount > 0
    assert lines["export_price:export_price:pv"].degrades_with == "pv"
    assert [a.name for a in dev.assets] == ["pv"]
    assert dev.assets[0].overnight_cost is None           # capital_cost only: never back-calculated

    # The site: its lease (no indexation field → None, the engine's `ppa`);
    # the developer's PV is site generation it does not own → the
    # counterfactual is not established (a None line, flagged).
    n.storage_units.loc["bess", "overnight_cost"] = 900_000.0       # typed
    site = _case(n, cfg, _fin(annualise=True, cod_by_asset={"bess": COD}), owner="site")
    (t,), (cf,) = site.templates, site.counterfactual
    lease = _lines(t)["contract:lease1:lease_payment"]
    assert (lease.indexation, lease.tenor_years, lease.amount < 0) == (None, 10, True)
    assert "contract:lease1:lease_payment" not in _lines(cf)       # on the owner's asset
    assert _lines(cf)["contract:ppa1:ppa_energy"].amount == _lines(t)["contract:ppa1:ppa_energy"].amount
    assert "counterfactual_keeps_contract:ppa1" in site.flags
    assert "counterfactual_not_established:non_owner_site_assets" in site.flags
    assert _lines(cf)["counterfactual:non_owner_site_assets"].amount is None
    assert site.assets[0].overnight_cost == pytest.approx(900_000.0 * 10.0)
    assert set(t.energy_mwh) == set()                          # storage never generates here

    # Dates and configuration refusals.
    assert _refused(n, cfg, _fin(annualise=True, cod_by_asset={}), owner="site").code == \
        "cod_missing"
    bare = dataclasses.replace(cfg, commercial={**cfg.commercial, "value_flows": None})
    assert _refused(n, bare, fin).code == "value_flows_not_configured"
    unowned = dataclasses.replace(cfg, commercial={
        **cfg.commercial, "value_flows": {**vf, "asset_owners": []}})
    assert _refused(n, unowned, fin).code == "owner_has_no_assets"


# ── WP4.6a review round 1 ───────────────────────────────────────────────────


def _f6_com(items=None, vf=None):
    return {"poc_link": "import", "value_flows": vf or F6_VF,
            "import_tariff": {"id": "t", "name": "t", "jurisdiction": "US",
                              "valid_from": "2029-01-01", "items": items or [TOU, DEMAND]}}


def _layer():
    from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year

    return (TaxLayer(name="corp", rate=0.25,
                     depreciation=(DepreciationClass("all", 1.0, sl_half_year(10)),)),)


@pytest.mark.live_solve
@pytest.mark.parametrize("site", ["heat_pump", "boiler", "owner_heat_pump"])
def test_b2_site_conversion_load_and_non_owner_asset_costs(reset_backend, site):
    """Review B2. (a) A non-owner heat pump on the F6 site (site → heat,
    efficiency 3, a 3 MW heat load: 1 MWe always) is load that exists without
    the PV: the served load includes its electric draw (stated, disclosed).
    By hand, per day: actual import 6·2 (night) + 14·2 + 4·0.5 (midday, the
    2 MW PV under a 2.5 MW load), counterfactual 6·2 + 14·2 + 4·2.5: energy
    4·2·180 = 1,440, commodity 50·8 = 400; demand 2.5 vs 2.0 MW: 500 kW·9·12 =
    54,000 a year → incremental 1,440·365 + 54,000 + 400·365 = 725,600.
    (b) A non-owner gas boiler with its own gas supply: its cost line exists
    without the investment, so it is copied into the counterfactual and the
    incremental is F6's 557,700. (c) An OWNER heat pump serving a heat load:
    the site without it has no stated heat source — not established."""
    n = _f6_network()
    vf, cod = F6_VF, {"pv": COD}
    if site in ("heat_pump", "owner_heat_pump"):
        n.add("Bus", "heat", carrier="heat")
        n.add("Link", "hp", bus0="site", bus1="heat", p_nom=5.0, efficiency=3.0)
        n.add("Load", "heat_load", bus="heat", p_set=3.0)
        if site == "owner_heat_pump":
            vf = {**copy.deepcopy(F6_VF), "asset_owners": [
                {"asset_id": "pv", "component": "Generator", "owner": "site"},
                {"asset_id": "hp", "component": "Link", "owner": "site"}]}
            cod = {"pv": COD, "hp": COD}
    else:
        n.add("Bus", "gas", carrier="gas")
        n.add("Bus", "heat", carrier="heat")
        n.add("Generator", "gas_supply", bus="gas", carrier="gas", p_nom=10.0,
              marginal_cost=20.0)
        n.add("Link", "boiler", bus0="gas", bus1="heat", p_nom=5.0, efficiency=0.9)
        n.add("Load", "heat_load", bus="heat", p_set=0.9)
    n, cfg = _solve(n, _f6_com(vf=vf))
    case = _case(n, cfg, _fin(cod_by_asset=cod))
    (t,), (cf,) = case.templates, case.counterfactual
    if site == "owner_heat_pump":
        assert "counterfactual_not_established:owner_conversion_load" in case.flags
        assert _lines(cf)["counterfactual:owner_conversion_load"].amount is None
        # S is unknown with it: the C5 pair carries no number (WP4.6a review
        # round 2 — S·g was −1,080,400, "raising" the owner's cash as pv degrades).
        assert _lines(t)["bill_degradation:pv"].amount is None
        assert _lines(t)["bill_degradation_base:pv"].amount is None
        assert not any(f.startswith("degradation_bill_value_negative") for f in case.flags)
        return
    act, cfl = _lines(t), _lines(cf)
    if site == "heat_pump":
        assert _flag_num(case.flags, "counterfactual_includes_conversion_load:") == \
            pytest.approx(8_760.0, abs=0.01)
        assert cfl["bill:demand"].amount == pytest.approx(-2_500 * 9.0 * 12, abs=0.01)
        assert _net(t) - _net(cf) == pytest.approx(725_600.0, abs=0.01)
        # S (energy-volume items + commodity) = 1,440·365 + 400·365.
        assert act["bill_degradation:pv"].amount == pytest.approx(1_840.0 * 365, abs=0.01)
    else:
        gas = [k for k in act if k.startswith("asset:") and k.endswith(":gas_supply")]
        assert gas and all(cfl[k] == act[k] for k in gas)
        assert sum(act[k].amount for k in gas) == pytest.approx(-20.0 * 8_760, abs=0.01)
        assert _net(t) - _net(cf) == pytest.approx(557_700.0, abs=0.01)
    assert not any(f.startswith("counterfactual_not_established") for f in case.flags)


@pytest.mark.live_solve
def test_b3_a_blocking_p3_input_flag_makes_the_operating_cash_unknown(reset_backend):
    """Review B3: the demand rate changed after the solve → P3's
    `config_changed_since_solve` (blocking: the money is unknown) → a None
    template line per period and the flag in the case; the ledger's flags too."""
    from services.finance.engine import run_case

    n, cfg = _solve(_f6_network(), _f6_com())
    d2 = copy.deepcopy(DEMAND)
    d2["periods"][0]["rate"] = 30.0
    cfg2 = dataclasses.replace(cfg, commercial=_f6_com(items=[TOU, d2]))
    case = _case(n, cfg2, _fin(cod_by_asset={"pv": COD}))
    assert "config_changed_since_solve" in case.flags
    assert "input_not_established:config_changed_since_solve" in case.flags
    (t,) = case.templates
    assert _lines(t)["ledger_input_not_established:config_changed_since_solve"].amount is None
    r = run_case(case, layers=_layer())
    assert r.op.status["operating"] == "not_established"
    assert r.metrics["equity_post_tax_irr"] is None
    assert r.metrics["lcoe_nominal_per_mwh"] is None


def test_b6_a_ledger_line_with_an_unknown_party_is_none_never_dropped():
    from types import SimpleNamespace as NS

    from services.commercial import participants as P
    from services.results.finance_case import _owner_lines

    def line(cid, payer, payee, amount, idx):
        return P.ValueFlowLine(period="_", payer=payer, payee=payee,
                               value_stream="cfd_settlement", source="contract",
                               source_id=f"{cid}:{idx}", amount=amount, contract_id=cid)

    contracts = {
        "cfd1": NS(type="cfd", asset_ids=["pv"], generator_owner=None, counterparty="State"),
        "dr1": NS(type="dr", asset_ids=["bess"], counterparty="Aggregator"),
        "far": NS(type="cfd", asset_ids=["other_pv"], generator_owner=None,
                  counterparty="State"),
    }
    ledger = NS(periods={"_": [
        line("cfd1", "State", None, 12_345.0, 0),           # on the owner's PV, payee unknown
        line("dr1", "Aggregator", None, None, 1),           # on the owner's BESS, unknown amount
        line("far", "State", None, 99.0, 0),                # another party's asset: not ours
        line("cfd1", "State", "Neighbour", 7.0, 0),         # both parties known, neither owner
    ]})
    inputs = NS(settlement=[{"value_stream": "cfd_difference"}, {"value_stream": "dr_activation"}])
    flags: list[str] = []
    out = _owner_lines(ledger, inputs, "site", "_", flags, contracts=contracts,
                       owned_names={"pv", "bess"})
    assert set(out) == {"contract:cfd1:cfd_difference", "contract:dr1:dr_activation"}
    assert all(a.amount is None for a in out.values())
    assert {"ledger_party_unknown:contract:cfd1:cfd_difference",
            "ledger_party_unknown:contract:dr1:dr_activation"} <= set(flags)
    assert not any("far" in f for f in flags)


@pytest.mark.live_solve
def test_b7_s_counts_every_per_kwh_import_item_levies_included(reset_backend):
    """Review B7: F6 + a 0.02 $/kWh levy on import. The levy is billed per kWh
    on import, so the PV's energy avoids it: S = 503,700 + 20 $/MWh · 6 MWh ·
    365 = 547,500 (the stream `tax` no longer excludes it)."""
    n, cfg = _solve(_f6_network(), _f6_com(items=[TOU, DEMAND, LEVY]))
    case = _case(n, cfg, _fin(cod_by_asset={"pv": COD}))
    (t,), (cf,) = case.templates, case.counterfactual
    act, cfl = _lines(t), _lines(cf)
    assert act["bill:levy"].amount - cfl["bill:levy"].amount == pytest.approx(43_800.0, abs=0.01)
    assert act["bill_degradation:pv"].amount == pytest.approx(547_500.0, abs=0.01)
    assert _net(t) - _net(cf) == pytest.approx(557_700.0 + 43_800.0, abs=0.01)
    assert "degradation_bill_volume_items_only" in case.flags


def test_b7_the_s_items_and_a_negative_s_is_flagged():
    from types import SimpleNamespace as NS

    from services.results.finance_case import _c5_pair, _s_items

    items = [NS(id="e", kind="energy", unit="per_kwh", measured_on="import", direction="cost"),
             NS(id="levy", kind="tax_levy", unit="per_kwh", measured_on="import",
                direction="cost"),
             NS(id="cert", kind="certificate", unit="per_kwh", measured_on="net",
                direction="cost"),
             NS(id="feed", kind="energy", unit="per_kwh", measured_on="export",
                direction="revenue"),
             NS(id="d", kind="demand", unit="per_kw_month", measured_on="import",
                direction="cost"),
             NS(id="std", kind="fixed", unit="per_month", measured_on="import",
                direction="cost")]
    assert _s_items(NS(items=items)) == {"e", "levy", "cert"}
    assert _s_items(None) == set()
    flags: list[str] = []
    up, base = _c5_pair("pv", -10.0, 0.5, "_", 2030, flags)
    assert (up.amount, base.amount, up.degrades_with, up.money_year) == (-5.0, 5.0, "pv", 2030)
    assert "degradation_bill_value_negative:_" in flags


@pytest.mark.live_solve
def test_b8_degradation_links_never_an_undegraded_number(reset_backend):
    """Review B8: a single-asset `as_consumed_btm` PPA degrades with its
    asset; export with no split when the owner owns all site generation
    degrades by generation (one generator: all of it, stated); a per-MWh
    EaaS on several assets is a None line with `degradation_link_unknown`."""
    from tests.test_value_flow_reconciliation import _BTM, _VF_DEV

    # The developer sells the PV's consumed output to the site.
    n, cfg = _solve(_edge7(), _commercial(copy.deepcopy(_VF_DEV), contracts=[_BTM]))
    dev = _case(n, cfg, _fin(annualise=True, cod_by_asset={"pv": COD}), owner="developer")
    btm = _lines(dev.templates[0])["contract:btm:ppa_energy"]
    assert btm.degrades_with == "pv" and btm.amount is not None and btm.amount > 0
    assert "degradation_link_unknown:contract:btm:ppa_energy" not in dev.flags

    # The site owns the PV and the BESS; export revenue stays the site's (no
    # split): degraded by the PV's generation share (all of it).
    eaas = {"type": "eaas", "id": "e1", "provider": "om_contractor", "customer": "site",
            "fee_eur_per_mwh": 5.0, "tenor_years": 10, "asset_ids": ["pv", "bess"]}
    n, cfg = _solve(_edge7(), _commercial(OWNED, contracts=[eaas]))
    inputs, _vf, ledger, _ = _ledger(n, cfg)
    assert inputs.export_split is None
    case = _case(n, cfg, _fin(annualise=True))
    lines = _lines(case.templates[0])
    exp = lines["export_price:export_price:pv"]
    assert exp.degrades_with == "pv" and exp.amount > 0
    assert "export_price:export_price" not in lines
    assert "export_degrades_by_generation_share:export_price:export_price" in case.flags
    assert lines["bill:feed_in:pv"].degrades_with == "pv"
    e = lines["contract:e1:eaas_fee"]
    assert e.amount is None and "degradation_link_unknown:contract:e1:eaas_fee" in case.flags
    # With no degradation on its generator, the link does not matter: a number.
    zero = _case(n, cfg, _fin(annualise=True, degradation_by_asset={"pv": 0.0}))
    assert _lines(zero.templates[0])["contract:e1:eaas_fee"].amount is not None


@pytest.mark.live_solve
def test_b9_the_counterfactual_hash(reset_backend):
    from services.finance.engine import run_case
    from services.finance.report import _counterfactual_block

    n, cfg = _solve(_f6_network(), _f6_com())
    case = _case(n, cfg, _fin(cod_by_asset={"pv": COD}))
    h = case.counterfactual_hash
    assert isinstance(h, str) and len(h) == 16
    assert _case(n, cfg, _fin(cod_by_asset={"pv": COD})).counterfactual_hash == h
    # Not the finance inputs: the tariff, the served load, the connection, the commodity.
    assert _case(n, cfg, _fin(cod_by_asset={"pv": COD}, wacc_nominal=0.08)) \
        .counterfactual_hash == h
    d2 = copy.deepcopy(DEMAND)
    d2["periods"][0]["rate"] = 30.0
    other = dataclasses.replace(cfg, commercial=_f6_com(items=[TOU, d2]))
    assert _case(n, other, _fin(cod_by_asset={"pv": COD})).counterfactual_hash != h
    block = _counterfactual_block(run_case(case, layers=_layer()), case)
    assert block["hash"] == h


def test_generation_with_no_solved_column_is_unknown_never_zero():
    import pypsa

    from services.results.finance_case import _generation

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "b")
    n.add("Generator", "pv", bus="b", p_nom=1.0)
    n.add("Generator", "ghost", bus="b", p_nom=1.0)
    p = pd.DataFrame({"pv": [0.5, -0.1, np.nan]}, index=n.snapshots)
    mask = np.ones(3, dtype=bool)
    got = _generation(n, lambda _n, _c, _a: p, ["pv", "ghost"], mask)
    assert got == {"pv": None, "ghost": None}
    p.loc[p.index[2], "pv"] = 1.0
    assert _generation(n, lambda _n, _c, _a: p, ["pv"], mask) == {"pv": pytest.approx(1.5)}
