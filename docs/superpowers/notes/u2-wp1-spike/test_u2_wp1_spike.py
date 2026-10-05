"""
U2 WP1 — facade spike (tests only; THROWAWAY combined copy: IC branch + PR #78
+ GS study modules checked out by path).

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md
(GS branch, 760579c), WP1. Every test names, in its docstring, the plan item
or open question it settles. Nothing here is GS production code: the tariff
compile below is a HAND compile of §3.1 for the two seeds, and the GS demand
wrapper is a verbatim copy of `services/solver/objective.py::_wrap_with_demand_charge`
from the GS branch (the combined copy has IC's `objective.py`, which does not
carry it) — the only shim in this spike.

Facts are asserted; numbers that are only reported are printed with `-s`.
"""
from __future__ import annotations

import dataclasses
import json
import math
import queue
import threading
import time
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tests.golden import site_fixture as SF

YEAR = SF.SITE_YEAR            # the modelled year y_m (2025)
Y0 = YEAR - 1                   # GS year 0 = financial close (row 21)
IMPORT, EXPORT = "grid_import", "grid_export"
DE, TOU = "de_industrial_illustrative", "tou_reference_illustrative"


# ── the hand compile of plan §3.1 (what WP4's compile.py will own) ───────────


def _runs(hours: list[int]) -> list[tuple[int, int]]:
    """Contiguous [start, end) runs of a GS band's hour-starting list."""
    out, start, prev = [], None, None
    for h in sorted(hours):
        if start is None:
            start = prev = h
        elif h == prev + 1:
            prev = h
        else:
            out.append((start, prev + 1))
            start = prev = h
    if start is not None:
        out.append((start, prev + 1))
    return out


def compile_tariff(gs, *, settlement: str = "h", valid_from: str | None = None,
                   jurisdiction: str | None = None) -> dict:
    """GS `Tariff` → IC `Tariff` dict, plan §3.1 rows used by the two seeds:
    bands → item `energy` (one period per band and contiguous hour run, band
    order kept), `per_mwh` network charges → `network:energy:<i>`, a monthly
    peak demand charge → item `demand` (`settlement="h"`), the per-period
    fixed charge → item `fixed` (`per_month`). Export is NOT an item (C3)."""
    items: list[dict] = []
    periods = []
    for b in gs.energy_bands:
        base = {"name": b.label, "rate": b.price_per_mwh / 1000.0,
                "months": list(b.applies.months), "weekdays": list(b.applies.weekdays)}
        runs = _runs(b.applies.hours) if b.applies.hours else [None]
        for r in runs:
            periods.append({**base} if r is None else {**base, "start_hour": r[0],
                                                       "end_hour": r[1]})
    items.append({"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": periods})
    for i, nc in enumerate(gs.network_charges):
        assert nc.basis == "per_mwh"
        items.append({"id": f"network:energy:{i}", "kind": "energy", "unit": "per_kwh",
                      "periods": [{"name": nc.label, "rate": nc.price / 1000.0}]})
    if gs.demand_charge is not None:
        assert gs.billing_period == "month" and gs.demand_charge.basis == "billing_period_peak"
        items.append({"id": "demand", "kind": "demand", "unit": "per_kw_month",
                      "settlement": settlement, "measured_on": "import",
                      "periods": [{"name": "all",
                                   "rate": gs.demand_charge.price_per_mw_per_period / 1000.0}]})
    if gs.fixed_charge_per_period:
        items.append({"id": "fixed", "kind": "fixed", "unit": "per_month",
                      "periods": [{"name": "all", "rate": float(gs.fixed_charge_per_period)}]})
    return {"id": gs.tariff_id, "name": gs.name,
            "jurisdiction": jurisdiction or ("DE" if gs.tariff_id.startswith("de_") else "generic"),
            "valid_from": valid_from or f"{gs.currency_year}-01-01", "valid_to": None,
            "items": items}


def _intake(tariff_id: str = DE) -> dict:
    out = SF.site_intake()
    out["tariff"] = {"tariff_id": tariff_id}
    return out


def _ledger(tariff_id: str = DE):
    from services.study import library as L
    from services.study import questions as Q

    return L.seed_ledger(Q.BESS_AT_SITE, _intake(tariff_id), SF.site_library())


def _gs_tariff(tariff_id: str = DE):
    from services.study import packs

    return packs.effective_tariff(_intake(tariff_id), _ledger(tariff_id), SF.site_library())


def _site_network(option: str, tariff_id: str = DE):
    """The production pack's network, with GS's prices REMOVED (C2: IC prices
    transiently at solve; Link marginal_cost stays 0)."""
    from services.study import packs

    n = packs.build_site_network(_intake(tariff_id), _ledger(tariff_id), option,
                                 library=SF.site_library())
    n.links["marginal_cost"] = 0.0
    mc = n.links_t.marginal_cost
    n.links_t.marginal_cost = mc.drop(columns=[c for c in (IMPORT, EXPORT) if c in mc.columns])
    return n


def _commercial(tariff_id: str = DE, **over) -> dict:
    t = compile_tariff(_gs_tariff(tariff_id))
    out = {"poc_link": IMPORT, "export_link": EXPORT, "import_tariff": t,
           "import_tariff_id": t["id"], "timezone": None, "site_party": "site"}
    out.update(over)
    return out


FAKE_REF = {"id": "study-export-flat", "version": 1, "hash": "f" * 64, "source": "tariff"}


def _flat_export(n, tariff_id: str = DE) -> pd.Series:
    price = _gs_tariff(tariff_id).export.price_per_mwh
    return pd.Series(float(price), index=pd.DatetimeIndex(n.snapshots), name="price")


def _bind(n, commercial: dict, series: pd.Series) -> dict:
    """`binding.bind_commercial` on an in-memory network (no route, no org)."""
    from models.commercial import CommercialConfig
    from services.commercial import binding

    return binding.bind_commercial(n, CommercialConfig.model_validate(commercial),
                                   project_dir=None, resolve_ref=lambda ref: series)


def _single_owner(n, commercial: dict) -> dict:
    from models.commercial import CommercialConfig
    from services.commercial import value_flow_templates as VFT

    return VFT.build("single_owner", n, CommercialConfig.model_validate(commercial)
                     ).config.model_dump(mode="json")


def _solver_config(commercial, **kw):
    from services.solver_service import SolverConfig

    return SolverConfig(solver_name="highs", mode="lopf", multi_investment_periods=False,
                        solve_strategy="full", sclopf=False, run_ac_pf_after_lopf=False,
                        extra_functionality_code="", discount_rate=0.07,
                        default_lifetime=25.0, commercial=commercial, **kw)


def _run(n, commercial):
    """Solve through the production `run_simulation` (IC's commercial chain)."""
    import routers.simulation as sim_router
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n)
    cfg = _solver_config(commercial)
    sim_router._state["solver_config"] = cfg
    live = PyPSAService.get_network()
    log = queue.SimpleQueue()
    status, cond = run_simulation(cfg, live, PyPSAService.get_lock(), threading.Event(), log,
                                  state_update=lambda **k: None)
    lines = []
    while not log.empty():
        lines.append(str(log.get()))
    assert status in ("ok", "optimal"), (status, cond, lines[-15:])
    return live, cfg


_SOLVED: dict = {}


def _solved(option: str = "bess_2h", tariff_id: str = DE):
    """(solved network, SolverConfig) of one IC-priced site option, once per
    process: export series bound, single_owner value flows, run_simulation."""
    key = (option, tariff_id)
    if key not in _SOLVED:
        n = _site_network(option, tariff_id)
        com = _commercial(tariff_id, export_price_ref=FAKE_REF)
        com = _bind(n, com, _flat_export(n, tariff_id))
        com["value_flows"] = _single_owner(n, com)
        t0 = time.perf_counter()
        n, cfg = _run(n, com)
        _SOLVED[key] = (n, cfg, time.perf_counter() - t0)
    n, cfg, _t = _SOLVED[key]
    return n, cfg


def _owned(cfg) -> list[tuple[str, str]]:
    from services.commercial import participants as P

    vf = P.parse_value_flows(cfg.commercial["value_flows"])
    return [(o.component, o.asset_id) for o in vf.asset_owners]


def _rows_21_34(owned_names, *, p_mw: float, pv: bool = False, terminal: float = 0.0,
                **over):
    """FinanceInputs from plan §1.2 rows 21–34 (+ rows 1, 4, 20 for the
    replacements and rates). The terminal value is a placeholder `fixed`
    (C2's annuity-PV salvage is WP7's compile)."""
    from models.finance import ESCALATION_CLASSES, FinanceInputs
    from services.study import packs

    led = _ledger()
    v = packs.ledger_values(led)
    inv = v["battery_inverter_eur_per_kw"] * 1000.0 * p_mw
    life_inv = int(v["battery_inverter_lifetime_years"])
    horizon = int(round(v["battery_storage_lifetime_years"]))
    repl = [(Y0 + k * life_inv, "battery", inv) for k in range(1, horizon // life_inv + 1)
            if k * life_inv < horizon]
    kw = dict(currency="EUR", financial_close=date(Y0, 1, 1), capex_phasing=[1.0],
              cod_by_asset={a: date(YEAR, 1, 1) for a in owned_names},
              contingency_share=0.0, escalation={c: 0.0 for c in ESCALATION_CLASSES},
              inflation=None, cost_of_equity=v["discount_rate"],
              wacc_nominal=v["discount_rate"], analysis_years=horizon,
              terminal_value={"method": "fixed", "value": terminal},
              degradation_by_asset={"pv": 0.0} if pv else {}, tax_pack_id=None,
              incentives=[], replacement_capex=repl, annualise=False, debt=[])
    kw.update(over)
    return FinanceInputs(**kw)


def _case(n, cfg, fin, **kw):
    import routers.results as R
    from services.results.finance_case import build_finance_case

    return build_finance_case(n, cfg, fin, result_df=R._result_df, **kw)


def _p_battery(n) -> float:
    return float(n.storage_units.at["battery", "p_nom_opt"])


# ════════════════════════════════════════════════════════════════════════════
# Q5 / R3 — owner assets of single_owner on the site pack
# ════════════════════════════════════════════════════════════════════════════


def test_q5_single_owner_owns_the_poc_meter_links():
    """WP1 item 1 / Q5 / R3 (plan §1.2 row 28): `value_flow_templates.build
    ("single_owner")` on the site pack assigns the PoC meter Links
    (`grid_import`, `grid_export`) to the owner as assets, beside the battery
    (and PV); the grid-side supply Generator is not owned."""
    for option, expect in (("bess_2h", {("Link", IMPORT), ("Link", EXPORT),
                                        ("StorageUnit", "battery")}),
                           ("bess_pv_2h", {("Link", IMPORT), ("Link", EXPORT),
                                           ("StorageUnit", "battery"), ("Generator", "pv")}),
                           ("none", {("Link", IMPORT), ("Link", EXPORT)})):
        n = _site_network(option)
        vf = _single_owner(n, _commercial())
        owned = {(o["component"], o["asset_id"]) for o in vf["asset_owners"]}
        assert owned == expect, (option, owned)
        assert {p["id"] for p in vf["participants"]} == {"site"}
        assert vf["template_version"] == "single_owner@2"


@pytest.mark.live_solve
def test_q5_meter_links_make_capex_not_established_from_overnight_cost_none():
    """WP1 item 1 / Q5 (§4.5, C1): with the meter Links owned, `finance_case
    ._assets` reads their typed `overnight_cost` (NaN on the pack's Links) as
    None, so the engine's capex is `not_established` with
    `overnight_cost_missing:grid_import` / `grid_export` — and the battery
    (no typed overnight_cost, C1) adds `overnight_cost_missing:battery`.
    Two GS-side ways out are measured: (a) typed `overnight_cost = 0` on the
    meter Links; (b) dropping them from `asset_owners` (a template edit)."""
    from services.finance.engine import run_case

    n, cfg = _solved()
    owned = _owned(cfg)
    names = [a for _c, a in owned]
    case = _case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n)))
    by = {a.name: a for a in case.assets}
    assert by[IMPORT].overnight_cost is None and by[EXPORT].overnight_cost is None
    assert by["battery"].overnight_cost is None
    res = run_case(case)
    assert res.op.reasons["capex"] == sorted(
        ["overnight_cost_missing:battery", f"overnight_cost_missing:{EXPORT}",
         f"overnight_cost_missing:{IMPORT}"])
    assert res.metrics["project_pre_tax_npv"] is None

    # (a) typed 0 on the meter Links: they read 0.0, only the battery is missing.
    saved, saved_life = n.links["overnight_cost"].copy(), n.links["lifetime"].copy()
    try:
        n.links.loc[[IMPORT, EXPORT], "overnight_cost"] = 0.0
        case_a = _case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n)))
        by_a = {a.name: a for a in case_a.assets}
        assert by_a[IMPORT].overnight_cost == 0.0 and by_a[EXPORT].overnight_cost == 0.0
        res_a = run_case(case_a)
        assert res_a.op.reasons["capex"] == ["overnight_cost_missing:battery"]
        # The pack's Links keep PyPSA's lifetime = inf → `asset_lifetime_unknown:*`
        # flags, unless a finite lifetime is typed too.
        assert math.isinf(float(saved_life[IMPORT]))
        assert f"asset_lifetime_unknown:{IMPORT}" in res_a.flags
        n.links.loc[[IMPORT, EXPORT], "lifetime"] = 25.0
        res_a2 = run_case(_case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n))))
        assert not [f for f in res_a2.flags if f.startswith("asset_lifetime_unknown")]
    finally:
        n.links["overnight_cost"] = saved
        n.links["lifetime"] = saved_life

    # (b) meter Links dropped from asset_owners: the case builds (the counterfactual
    # does not need them owned), the template reads `template_edited`.
    from models.commercial import CommercialConfig, ValueFlowConfig
    from services.commercial import value_flow_templates as VFT

    com_b = json.loads(json.dumps(cfg.commercial))
    com_b["value_flows"]["asset_owners"] = [o for o in com_b["value_flows"]["asset_owners"]
                                            if o["component"] != "Link"]
    cfg_b = dataclasses.replace(cfg, commercial=com_b)
    case_b = _case(n, cfg_b, _rows_21_34(["battery"], p_mw=_p_battery(n)))
    assert [a.name for a in case_b.assets] == ["battery"]
    assert run_case(case_b).op.reasons["capex"] == ["overnight_cost_missing:battery"]
    status = VFT.template_status(ValueFlowConfig.model_validate(com_b["value_flows"]), n,
                                 CommercialConfig.model_validate(com_b))
    assert "template_edited" in status
    assert not [f for f in case_b.flags if f.startswith("counterfactual_not_established")]
    assert {ln.key for t in case_b.counterfactual for ln in t.lines} == \
        {ln.key for t in case.counterfactual for ln in t.lines}
    print("\nQ5 (b) template_status after dropping meter Links:", status)
    print("Q5 counterfactual lines (b):",
          sorted({ln.key for t in case_b.counterfactual for ln in t.lines}))


# ════════════════════════════════════════════════════════════════════════════
# C4 (bill) — rate_meter on the unsolved baseline pack
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tariff_id", [DE, TOU])
def test_c4_rate_meter_rates_the_unsolved_baseline_pack(tariff_id):
    """WP1 item 2 / C4 (preview over `rate_meter`, not
    `compute_billing_preview`): `billing.rate_meter(n, commercial, load, 0)`
    rates the UNSOLVED `none` pack (no `p_nom_opt`, no solve record): one
    `RatingResult` for the flat period, no drift flags, capacity on the PoC
    `p_nom`. Its energy + network, demand and fixed equal GS's
    `BillCalculator` on the same meter to 1e-9 relative."""
    from services.commercial import billing
    from services.study import tariff as study_tariff

    n = _site_network("none", tariff_id)
    assert not n.links_t.p0.columns.size or n.links_t.p0.empty   # unsolved
    load = n.loads_t.p_set["site_load"].to_numpy(dtype=float)
    bill = billing.rate_meter(n, _commercial(tariff_id), load, np.zeros_like(load))
    assert set(bill.per_period) == {None}
    res = bill.per_period[None]
    assert res is not None and res.total is not None
    assert bill.flags == [], bill.flags
    gs = study_tariff.BillCalculator().bill(
        pd.Series(load, index=n.snapshots), pd.Series(0.0, index=n.snapshots),
        _gs_tariff(tariff_id), n.snapshot_weightings)
    c = gs.by_component
    ic = res.per_item
    ic_energy = ic["energy"]
    ic_network = sum(v for k, v in ic.items() if k.startswith("network:"))
    assert math.isclose(ic_energy, c.energy, rel_tol=1e-9), (ic_energy, c.energy)
    assert math.isclose(ic_network, c.network, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(ic.get("demand", 0.0), c.demand, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(ic.get("fixed", 0.0), c.fixed, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(res.total, gs.total, rel_tol=1e-9), (res.total, gs.total)
    print(f"\n{tariff_id}: rate_meter total {res.total:,.2f} = GS {gs.total:,.2f}; "
          f"notes {res.notes}; provenance keys {sorted(bill.provenance)}")
    # §3.1 correction: energy / network items left at IC's default settlement
    # ("15min") carry `resolution:dispatch_1h_settlement_0.25h` notes on the
    # hourly axis (→ `bill_resolution_differs_from_settlement`, §3.2);
    # compiled with settlement "h" they carry none, and the bill is unchanged.
    assert any(k == "energy" and any(x.startswith("resolution:") for x in v)
               for k, v in res.notes.items())
    com_h = _commercial(tariff_id)
    for it in com_h["import_tariff"]["items"]:
        it["settlement"] = "h"
    res_h = billing.rate_meter(n, com_h, load, np.zeros_like(load)).per_period[None]
    assert not any(x.startswith("resolution:") for v in res_h.notes.values() for x in v), \
        res_h.notes
    assert math.isclose(res_h.total, res.total, rel_tol=1e-12)


# ════════════════════════════════════════════════════════════════════════════
# Q9 / §3.1 — preflight on the compiled config
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tariff_id", [DE, TOU])
def test_q9_preflight_on_the_compiled_config_is_clean(tariff_id):
    """WP1 item 3 / Q9 / §3.1: IC's preflight (`commercial_findings`, the
    `_check_commercial` source) on the hand-compiled DE and TOU configs, export
    series bound: no error; no `commercial.demand_resolution` with
    `settlement="h"` on the hourly axis; no `commercial.tariff_out_of_validity`
    with `valid_from = date(currency_year, 1, 1)`, `valid_to = None`. Controls:
    `settlement="15min"` raises the resolution warning; `valid_from` after the
    modelled year raises the validity warning (both checks are live). Also
    run through `validate_for_run` (the solve's preflight)."""
    from services.commercial.preflight import commercial_findings
    from services.validation_service import validate_for_run

    n = _site_network("bess_pv_2h", tariff_id)
    com = _bind(n, _commercial(tariff_id, export_price_ref=FAKE_REF), _flat_export(n, tariff_id))
    found = commercial_findings(n, com)
    codes = [f[1] for f in found]
    assert not [f for f in found if f[0] == "error"], found
    assert "commercial.demand_resolution" not in codes
    assert "commercial.tariff_out_of_validity" not in codes
    issues = validate_for_run(n, _solver_config(com))
    assert not [i for i in issues if i.severity == "error"], [(i.code, i.message) for i in issues]
    print(f"\n{tariff_id} preflight findings: {codes}; validate_for_run: "
          f"{sorted({i.code for i in issues})}")

    gs = _gs_tariff(tariff_id)
    if gs.demand_charge is not None:
        com15 = {**com, "import_tariff": compile_tariff(gs, settlement="15min")}
        assert "commercial.demand_resolution" in [f[1] for f in commercial_findings(n, com15)]
    late = {**com, "import_tariff": compile_tariff(gs, valid_from=f"{YEAR + 1}-01-01")}
    assert "commercial.tariff_out_of_validity" in [f[1] for f in commercial_findings(n, late)]


# ════════════════════════════════════════════════════════════════════════════
# rows 21–34 — build_finance_case refusals and flags
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.live_solve
def test_rows_21_34_build_finance_case_refusals_then_flags():
    """WP1 item 4 (§1.2 rows 21–34): the refusal sequence of
    `build_finance_case` / `run_case` for a minimal single-owner case, and the
    sections, reasons and flags once rows 21–34 are supplied (pv row 30 on
    `bess_pv_2h`, the `degradation_missing:pv` question of row 30)."""
    from models.finance import FinanceInputs
    from services.finance.case import FinanceRefused
    from services.finance.engine import run_case

    n, cfg = _solved()
    names = [a for _c, a in _owned(cfg)]
    bare = FinanceInputs(financial_close=date(Y0, 1, 1))

    # 0. No value flows → refused before anything else.
    com0 = {k: v for k, v in cfg.commercial.items() if k != "value_flows"}
    with pytest.raises(FinanceRefused) as e0:
        _case(n, dataclasses.replace(cfg, commercial=com0), bare)
    assert e0.value.code == "value_flows_not_configured"
    # 1. Bare inputs: no COD for the owner's assets (row 22).
    with pytest.raises(FinanceRefused) as e1:
        _case(n, cfg, bare)
    assert e1.value.code == "cod_missing"
    # 2. COD for the battery only: the meter Links still need one (Q5).
    with pytest.raises(FinanceRefused) as e2:
        _case(n, cfg, FinanceInputs(financial_close=date(Y0, 1, 1),
                                    cod_by_asset={"battery": date(YEAR, 1, 1)}))
    assert e2.value.code == "cod_missing" and IMPORT in str(e2.value)
    # 3. Every owner COD: the case BUILDS; run_case refuses without analysis_years (row 27).
    only_cod = FinanceInputs(financial_close=date(Y0, 1, 1),
                             cod_by_asset={a: date(YEAR, 1, 1) for a in names})
    case3 = _case(n, cfg, only_cod)
    with pytest.raises(FinanceRefused) as e3:
        run_case(case3)
    assert e3.value.code == "analysis_years_missing"
    # 4. + analysis_years: runs; every other missing row is a reason, not a refusal.
    res4 = run_case(_case(n, cfg, only_cod.model_copy(update={"analysis_years": 25})))
    print("\nrows 21-34 absent (analysis_years only): sections", res4.sections,
          "\n reasons", res4.reasons, "\n op.reasons", res4.op.reasons)
    assert res4.sections["operating"] == "not_established"
    assert "contingency_share_missing" in res4.op.reasons["capex"]
    assert any(r.startswith("escalation_missing:") for r in res4.op.reasons["operating"])

    # 5. Rows 21–34 supplied.
    fin = _rows_21_34(names, p_mw=_p_battery(n))
    case = _case(n, cfg, fin)
    res = run_case(case)
    print("rows 21-34 supplied: sections", res.sections, "\n reasons", res.reasons,
          "\n op.reasons", res.op.reasons, "\n case flags", list(case.flags),
          "\n result flags", res.flags, "\n gate", res.gate,
          "\n metrics", {k: v for k, v in res.metrics.items() if v is not None})
    assert res.op.reasons["operating"] == [] and res.op.reasons["terminal"] == []
    # Capex is not established ONLY because of the overnight costs (Q5 + C1):
    assert res.op.reasons["capex"] == sorted(
        ["overnight_cost_missing:battery", f"overnight_cost_missing:{EXPORT}",
         f"overnight_cost_missing:{IMPORT}"])
    assert res.reasons.get("tax") and "tax_pack_missing" in res.reasons["tax"]
    # C4 gate: consistent; inflation leg n/a (auto_discount_periods off).
    assert res.gate["wacc_vs_discount_rate_consistent"] is True
    assert res.gate["legs"] == {"discount_rate": "ok", "asset_rates": "ok", "inflation": "n/a"}
    # The owned meter Links (Q5) also put lifetime flags on the case.
    assert {f"asset_lifetime_unknown:{IMPORT}", f"asset_lifetime_unknown:{EXPORT}"} <= \
        set(res.flags)
    assert list(case.flags) == []

    # 6. The same with the battery's overnight cost typed (stand-in for S0b) and the
    # meter Links at 0: the headline is established.
    saved_l, saved_s = n.links["overnight_cost"].copy(), n.storage_units["overnight_cost"].copy()
    from services.study import packs
    try:
        n.links.loc[[IMPORT, EXPORT], "overnight_cost"] = 0.0
        n.storage_units.loc["battery", "overnight_cost"] = \
            packs.battery_upfront_eur_per_mw(_ledger(), 2.0)["total"]
        res6 = run_case(_case(n, cfg, fin))
    finally:
        n.links["overnight_cost"] = saved_l
        n.storage_units["overnight_cost"] = saved_s
    print("with typed overnight (stand-in for S0b): sections", res6.sections,
          "\n reasons", res6.reasons, "\n gate", res6.gate,
          "\n project_pre_tax npv/irr", res6.metrics["project_pre_tax_npv"],
          res6.metrics["project_pre_tax_irr"], "\n flags", res6.flags)
    assert res6.cash["project_pre_tax"] is not None
    assert res6.metrics["project_pre_tax_npv"] is not None
    assert res6.metrics["equity_post_tax_npv"] is None          # pre-tax basis
    assert res6.metrics["lcoe_nominal_per_mwh"] is None         # storage: no generation


@pytest.mark.live_solve
def test_row_30_pv_degradation_is_required_on_bess_pv():
    """WP1 item 4, row 30 (§1.2, 'unverified that the bess_pv case fails'):
    on `bess_pv_2h`, without `degradation_by_asset["pv"]` the operating
    section is `not_established` with `degradation_missing:pv`; with 0.0 it
    is established."""
    from services.finance.engine import run_case

    n, cfg = _solved("bess_pv_2h")
    names = [a for _c, a in _owned(cfg)]
    assert "pv" in names
    without = run_case(_case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n), pv=False)))
    assert "degradation_missing:pv" in without.op.reasons["operating"], without.op.reasons
    with_ = run_case(_case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n), pv=True)))
    assert with_.op.reasons["operating"] == [], with_.op.reasons
    print("\nbess_pv_2h: PV p_nom_opt", float(n.generators.at["pv", "p_nom_opt"]),
          "battery", _p_battery(n), "case flags",
          list(_case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n), pv=True)).flags))


@pytest.mark.live_solve
def test_q14_export_revenue_is_an_energy_export_line_in_actual_and_counterfactual():
    """Q14: the export revenue in the P3 ledger reaches the owner's actual
    template as a line with stream `energy_export` and escalation class
    `export`; the counterfactual (export 0) carries no export revenue line
    of non-zero amount."""
    n, cfg = _solved("bess_pv_2h")
    names = [a for _c, a in _owned(cfg)]
    case = _case(n, cfg, _rows_21_34(names, p_mw=_p_battery(n), pv=True))
    act = [ln for t in case.templates for ln in t.lines if ln.stream == "energy_export"]
    cf = [ln for t in case.counterfactual for ln in t.lines if ln.stream == "energy_export"]
    exported = float((n.links_t.p0[EXPORT] * n.snapshot_weightings.objective).sum())
    print(f"\nQ14: exported {exported:.3f} MWh; actual export lines "
          f"{[(ln.key, ln.esc_class, ln.amount) for ln in act]}; counterfactual "
          f"{[(ln.key, ln.esc_class, ln.amount) for ln in cf]}")
    print("Q14 all actual streams:", sorted({(ln.stream, ln.esc_class) for t in case.templates
                                             for ln in t.lines}))
    print("Q14 all counterfactual streams:", sorted({(ln.stream, ln.esc_class)
                                                     for t in case.counterfactual
                                                     for ln in t.lines}))
    if exported > 1e-6:
        assert act and all(ln.esc_class == "export" for ln in act)
        assert sum(ln.amount for ln in act) > 0
    assert all((ln.amount or 0.0) == 0.0 for ln in cf)


# ════════════════════════════════════════════════════════════════════════════
# C1 — the battery's two upfront parts (S0, PR #78) vs finance_case._assets
# ════════════════════════════════════════════════════════════════════════════


def test_c1_apply_parts_reproduces_the_two_annuity_capital_cost_and_upfront():
    """WP1 item 5 / C1 (§4.5 S0 facade): writing the battery through
    `derive.apply_parts` with the ledger's two parts gives a `capital_cost`
    equal to `packs.battery_capital_cost_eur_per_mw` to 1e-9 at the ledger
    rate, the same `fom_cost` as `packs.battery_fom_eur_per_mw`, lifetime 25
    and `overnight_cost` NaN; `sum(upfront_per_unit)` of
    `access.upfront_parts` equals `packs.battery_upfront_eur_per_mw` total."""
    from services.asset_schema import access, derive
    from services.study import packs

    led = _ledger()
    v = packs.ledger_values(led)
    for h, option in ((1.0, "bess_1h"), (2.0, "bess_2h"), (4.0, "bess_4h")):
        n = _site_network(option)
        assert float(n.storage_units.at["battery", "max_hours"]) == h
        parts = {"inv_power_overnight": v["battery_inverter_eur_per_kw"] * 1000.0,
                 "inv_power_lifetime": v["battery_inverter_lifetime_years"],
                 "inv_power_fom_share": v["battery_inverter_fom_pct_per_year"] / 100.0,
                 "inv_energy_overnight": v["battery_storage_eur_per_kwh"] * 1000.0,
                 "inv_energy_lifetime": v["battery_storage_lifetime_years"],
                 "inv_energy_fom_share": 0.0}
        derive.apply_parts(n, "StorageUnit", "battery", parts, discount_rate=v["discount_rate"])
        row = n.storage_units.loc["battery"]
        want = packs.battery_capital_cost_eur_per_mw(led, h)
        assert abs(row["capital_cost"] - want) <= 1e-9 * want, (h, row["capital_cost"], want)
        assert math.isclose(row["fom_cost"], packs.battery_fom_eur_per_mw(led), rel_tol=1e-12)
        assert row["lifetime"] == 25.0 and math.isnan(row["overnight_cost"])
        got = access.upfront_parts(n, "StorageUnit", "battery")
        assert [p.name for p in got] == ["power", "energy"]
        assert not any(p.derived_from_capital_cost for p in got)
        up = packs.battery_upfront_eur_per_mw(led, h)
        assert math.isclose(sum(p.upfront_per_unit for p in got), up["total"], rel_tol=1e-12)
        assert math.isclose(got[0].upfront_per_unit, up["inverter"], rel_tol=1e-12)
        assert math.isclose(got[1].upfront_per_unit, up["storage"], rel_tol=1e-12)
        assert [p.lifetime for p in got] == [10.0, 25.0]


def test_c1_finance_case_assets_do_not_read_upfront_parts_yet():
    """WP1 item 5 / C1 / R1: on the combined copy (IC + #78) `finance_case
    ._assets` reads ONLY the typed `overnight_cost` — a battery written through
    `apply_parts` reads `overnight_cost=None` (`overnight_cost_missing:battery`
    in the engine), although `upfront_parts` knows both parts. So C1 needs
    S0b (IC's follow-up: `_assets` via `upfront_parts`). Also pins that
    nothing in `services/results/finance_case.py` or `services/finance/`
    imports `asset_schema` yet."""
    import inspect

    from services.asset_schema import access, derive
    from services.results import finance_case as FC
    from services.study import packs

    v = packs.ledger_values(_ledger())
    n = _site_network("bess_2h")
    derive.apply_parts(n, "StorageUnit", "battery",
                       {"inv_power_overnight": v["battery_inverter_eur_per_kw"] * 1000.0,
                        "inv_power_lifetime": 10.0,
                        "inv_energy_overnight": v["battery_storage_eur_per_kwh"] * 1000.0,
                        "inv_energy_lifetime": 25.0}, discount_rate=0.07)
    n.storage_units.loc["battery", "p_nom_opt"] = 1.0
    assets, _rates = FC._assets(n, [("StorageUnit", "battery")])
    assert assets[0].overnight_cost is None
    assert access.upfront_parts(n, "StorageUnit", "battery") is not None
    src = inspect.getsource(FC)
    assert "asset_schema" not in src and "upfront_parts" not in src
    import pathlib
    fin_dir = pathlib.Path(FC.__file__).resolve().parents[1] / "finance"
    hits = [p.name for p in fin_dir.rglob("*.py") if "asset_schema" in p.read_text()]
    assert hits == [], hits


@pytest.mark.live_solve
def test_c1_parts_leave_the_solved_objective_and_size_unchanged():
    """C1 / §4.5 (WP7 test, measured early on the combined copy): the
    `bess_2h` site option solved through `run_simulation` with the battery
    written by `apply_parts` (S0's solve-time fill re-derives `capital_cost`
    at the live rate) gives the same objective and `p_nom_opt` as the pack's
    direct two-annuity `capital_cost` (1e-9 relative / 1e-6 MW)."""
    from services.asset_schema import derive
    from services.study import packs

    n_ref, _cfg = _solved()
    v = packs.ledger_values(_ledger())
    n = _site_network("bess_2h")
    derive.apply_parts(n, "StorageUnit", "battery",
                       {"inv_power_overnight": v["battery_inverter_eur_per_kw"] * 1000.0,
                        "inv_power_lifetime": v["battery_inverter_lifetime_years"],
                        "inv_power_fom_share": v["battery_inverter_fom_pct_per_year"] / 100.0,
                        "inv_energy_overnight": v["battery_storage_eur_per_kwh"] * 1000.0,
                        "inv_energy_lifetime": v["battery_storage_lifetime_years"],
                        "inv_energy_fom_share": 0.0}, discount_rate=v["discount_rate"])
    com = _commercial(export_price_ref=FAKE_REF)
    com = _bind(n, com, _flat_export(n))
    com["value_flows"] = _single_owner(n, com)
    n, _cfg2 = _run(n, com)
    print(f"\nC1 LP: objective ref {n_ref.objective:,.6f} parts {n.objective:,.6f}; "
          f"p_nom_opt ref {_p_battery(n_ref):.6f} parts {_p_battery(n):.6f}")
    assert math.isclose(n.objective, n_ref.objective, rel_tol=1e-9)
    assert abs(_p_battery(n) - _p_battery(n_ref)) <= 1e-6


# ════════════════════════════════════════════════════════════════════════════
# C3 / C6 / Q15 / R2 — minting the export series and binding on a fork
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def local_mode_db(_auth_db, monkeypatch, tmp_path):
    """Local (desktop) mode: the one seeded org + user (`local_mode`)."""
    import local_mode
    from settings import get_settings

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    _engine, session_local = _auth_db
    with session_local() as db:
        user = local_mode.ensure_local_identity(db)
        db.commit()
        user_id = user.id
    yield session_local, user_id, local_mode.LOCAL_ORG_ID


def test_c3_c6_q15_mint_in_local_org_and_bind_on_a_study_owned_fork(local_mode_db):
    """WP1 item 6 / C3 / C6 / Q15 / R2.
    (1) A study-owned fork made by `forks.create_option_fork` in local mode
    has `org_id = LOCAL_ORG_ID` (the base's org), so a series minted with
    `series_store.put_series(db, fork.org_id, …)` resolves there:
    `library_org_unknown` cannot arise for a study project (it needs a
    context with no org AND a non-User caller). Minting is idempotent on
    content.
    (2) `routers.simulation._bind_commercial(commercial, user)` takes no
    project/context argument: it binds the ACTIVE context's network
    (`PyPSAService.get_active_context()` / `get_network()`), so pointed at a
    runner fork off the foreground it writes the FOREGROUND network, never
    the fork.
    (3) `binding.bind_commercial(fork_net, …, project_dir=<fork dir>,
    resolve_ref=<series_store.resolve in the fork's org>)` binds the fork's
    in-memory network (before the runner writes it), and the bound
    `links_t["ic_export_price"]` survives the fork's netCDF round trip."""
    import inspect

    import routers.simulation as sim_router
    from db.models import Project, User
    from models.commercial import CommercialConfig, PriceSeriesRef
    from services import project_registry
    from services.commercial import binding
    from services.library import series_store
    from services.pypsa_service import PyPSAService
    from services.study import forks
    from tests.conftest import install_network_into_backend

    session_local, user_id, local_org = local_mode_db
    with session_local() as db:
        user = db.get(User, user_id)
        base = project_registry.create_root(db, user, "u2-wp1-site")
        base_dir = project_registry.ensure_project_dir(base)
        (base_dir / "metadata.json").write_text("{}")
        assert base.org_id == local_org

        fork_net = _site_network("bess_2h")
        fork = forks.create_option_fork(db, user_id, base_row=base, study_id="s" * 32,
                                        option_id="bess_2h", network=fork_net,
                                        solver_config=_solver_config(None))
        assert fork.org_id == local_org and fork.parent_project_id == base.id
        fork_dir = project_registry.project_dir(fork)

        # (1) Mint the flat export series in the fork's (the study's) org.
        series = _flat_export(fork_net)
        meta = {"source": "tariff:de_industrial_illustrative", "unit": "EUR/MWh",
                "description": "study export price (flat)"}
        name = "study-" + "s" * 12 + "-export"
        ref = series_store.put_series(db, fork.org_id, name, series, meta)
        again = series_store.put_series(db, fork.org_id, name, series, meta)
        assert (again.version, again.hash) == (ref.version, ref.hash) == (1, ref.hash)
        assert isinstance(ref, PriceSeriesRef)
        got = series_store.resolve(db, fork.org_id, ref)
        assert np.allclose(got.to_numpy(dtype=float), 40.0) and len(got) == 8760

        com = _commercial(export_price_ref=ref.model_dump(mode="json"))

        # (2) The route helper binds the ACTIVE network, not the fork.
        params = list(inspect.signature(sim_router._bind_commercial).parameters)
        assert params == ["commercial", "user"]
        foreground = _site_network("none")
        install_network_into_backend(foreground)
        assert PyPSAService.get_network() is foreground
        from unittest import mock
        with mock.patch("db.session.SessionLocal", session_local):
            out = sim_router._bind_commercial(CommercialConfig.model_validate(com), user)
        assert out is not None
        assert EXPORT in foreground.links_t["ic_export_price"].columns   # foreground written
        assert EXPORT not in getattr(fork_net.links_t, "ic_export_price",
                                     pd.DataFrame()).columns             # fork untouched

        # (3) The service binds the fork's network directly.
        def resolve(r):
            return series_store.resolve(db, fork.org_id, r)
        bound = binding.bind_commercial(fork_net, CommercialConfig.model_validate(com),
                                        project_dir=fork_dir, resolve_ref=resolve)
        assert bound["export_price_ref"]["hash"] == ref.hash
        assert np.allclose(fork_net.links_t["ic_export_price"][EXPORT].to_numpy(), 40.0)
        # No org → the documented refusal comes from the resolver, not the service.
        def no_org(r):
            raise binding.BindingRefusal(409, "library_org_unknown", "no org")
        fresh = _site_network("bess_2h")
        with pytest.raises(binding.BindingRefusal) as exc:
            binding.bind_commercial(fresh, CommercialConfig.model_validate(com),
                                    project_dir=None, resolve_ref=no_org)
        assert exc.value.code == "library_org_unknown"

        # netCDF round trip of the bound fork (the runner writes forks to disk).
        forks._write_network(fork_dir, fork_net)
        import pypsa
        back = pypsa.Network()
        PyPSAService.import_network_from_netcdf(back, fork_dir / "network.nc")
        assert EXPORT in back.links_t["ic_export_price"].columns
        assert np.allclose(back.links_t["ic_export_price"][EXPORT].to_numpy(), 40.0)
        print(f"\nC3/C6: fork org {fork.org_id} (LOCAL {local_org}); ref {ref.id} v{ref.version} "
              f"{ref.hash[:12]}; route bound the foreground; service bound the fork; "
              f"meta axis {back.meta.get('ic_export_price_axis')}")


# ════════════════════════════════════════════════════════════════════════════
# §3.2 export line, Q12 — value-flow export revenue vs commercial_cost_terms
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.live_solve
@pytest.mark.parametrize("option", ["bess_2h", "bess_pv_2h"])
def test_s32_export_line_cross_checks_with_commercial_cost_terms(option):
    """WP1 item 7 / §3.2 `export_credit`: the value-flow export line
    (`results/value_flows.py::_export_revenue` = Σ w·p0·`ic_export_price`)
    equals − the `energy_export` row of `commercial_cost_terms` to 1e-9 (the
    row exists separately). Q12: `commercial_cost_terms` reports the demand
    charge as `demand_charge` items (per period, the item named in
    `ic_demand_peaks`), which equals the bill's `demand` item; master's
    `objective_decomposition` closes (`residual_gap_eur` ≈ 0) with the
    Commercial component in `cost_breakdown`."""
    from services.commercial import billing
    from services.commercial import lp_bindings as LP
    from services.commercial.cost_rows import commercial_cost_terms
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from services.results.value_flows import _export_revenue

    n, cfg = _solved(option)
    parsed = LP._parse(cfg.commercial)
    rev, _intervals = _export_revenue(n, parsed)
    terms = commercial_cost_terms(n, cfg.commercial)
    labels = sorted({lab for lab, *_ in terms["items"]})
    exp_items = sum(cx + ox for lab, _p, cx, ox in terms["items"] if lab == "energy_export")
    print(f"\n{option}: export revenue {rev}; cost-term labels {labels}; block "
          f"energy_export {terms['block'].get('energy_export')}; flags {terms['flags']}")
    assert set(rev) == {"_"} and rev["_"] is not None
    assert math.isclose(-terms["block"]["energy_export"], rev["_"], rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(-exp_items, rev["_"], rel_tol=1e-9, abs_tol=1e-9)

    # Q12: the demand amount from the committed record = the bill's demand item.
    peaks = n.meta.get(LP.META_DEMAND) or {}
    assert peaks and {v.get("item") for v in peaks.values()} == {"demand"}
    dem_terms = sum(cx + ox for lab, _p, cx, ox in terms["items"] if lab == "demand_charge")
    bill = billing.bill_site(n, cfg.commercial)
    assert math.isclose(dem_terms, bill.per_period[None].per_item["demand"], rel_tol=1e-9)
    assert len(peaks) == 12

    cb = compute_cost_breakdown(n, cfg)
    dec = compute_objective_decomposition(n, cb, cfg)
    print(f"{option}: decomposition residual {dec['residual_gap_eur']}, lp_total "
          f"{dec.get('lp_total')}, cb commercial {cb.get('commercial')}")
    assert abs(dec["residual_gap_eur"]) <= 1e-6 * max(1.0, abs(dec["lp_total"]))


# ════════════════════════════════════════════════════════════════════════════
# R9 — run time of build_finance_case on 8,760 h
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.live_solve
def test_r9_build_finance_case_run_time_on_8760h():
    """WP1 item 8 / R9 / §4.9: wall time of `build_finance_case` (value-flow
    ledger + counterfactual) and of `run_case` on the 8,760-h site options;
    the solve time is reported beside it. Bound asserted loosely (< 30 s per
    build) — the number is the finding."""
    from services.finance.engine import run_case

    out = {}
    for option, pv in (("bess_2h", False), ("bess_pv_2h", True)):
        n, cfg = _solved(option)
        names = [a for _c, a in _owned(cfg)]
        fin = _rows_21_34(names, p_mw=_p_battery(n), pv=pv)
        ts = []
        for _ in range(3):
            t0 = time.perf_counter()
            case = _case(n, cfg, fin)
            ts.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        run_case(case)
        t_run = time.perf_counter() - t0
        out[option] = {"build_s": [round(t, 3) for t in ts], "run_case_s": round(t_run, 4),
                       "solve_s": round(_SOLVED[(option, DE)][2], 2)}
        assert min(ts) < 30.0
    print("\nR9 timings:", out)


# ════════════════════════════════════════════════════════════════════════════
# §4.7 — the demand formulation: GS wrapper vs IC add_demand_terms
# ════════════════════════════════════════════════════════════════════════════


class _DemandChargeRefused(Exception):
    pass


def _gs_wrap_with_demand_charge(network, user_fn, cfg):
    """VERBATIM copy (refusal branches trimmed, logging dropped) of the GS
    branch's `services/solver/objective.py::_wrap_with_demand_charge` — the
    combined copy's `objective.py` is IC's and does not carry it."""
    raw = getattr(cfg, "demand_charge", None)
    if not raw:
        return user_fn
    from services.study.tariff import billing_period_labels, parse_demand_charge_config

    spec = parse_demand_charge_config(raw)
    price = spec.price_per_mw_per_period

    def demand_charge_fn(n, snapshots):
        import xarray as xr

        m = n.model
        link_p = m.variables["Link-p"].sel(name=list(spec.import_links))
        snap = pd.DatetimeIndex(link_p.coords["snapshot"].values)
        labels = billing_period_labels(snap, spec.billing_period)
        periods = pd.Index(list(dict.fromkeys(labels.tolist())), name="billing_period")
        peak = m.add_variables(lower=0.0, coords=[periods], name="peak_import")
        per_snapshot = xr.DataArray(labels, dims=["snapshot"],
                                    coords={"snapshot": link_p.coords["snapshot"]})
        peak_t = peak.sel(billing_period=per_snapshot)
        m.add_constraints(link_p.sum("name") - peak_t <= 0.0, name="peak_import_le")
        m.objective += (price * peak).sum()

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        demand_charge_fn(n, snapshots)

    return wrapper


def _toy(months: int = 3):
    """A 3-month hourly site: grid supply, PoC import Link, a load with a
    weekday evening spike, an extendable 2-h battery (annuity-priced)."""
    import pypsa

    idx = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-{months + 1:02d}-01", freq="h",
                        inclusive="left")
    n = pypsa.Network()
    n.set_snapshots(idx)
    for c in ("AC", "grid", "battery"):
        n.add("Carrier", c)
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=5.0, marginal_cost=0.0)
    n.add("Link", IMPORT, bus0="grid", bus1="site", p_nom=5.0, efficiency=1.0)
    load = 0.6 + 0.8 * ((idx.weekday < 5) & (idx.hour == 17)) + \
        0.3 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi) ** 2
    n.add("Load", "site_load", bus="site", p_set=pd.Series(load, index=idx))
    n.add("StorageUnit", "battery", bus="site", carrier="battery", p_nom_extendable=True,
          p_nom_max=4.0, max_hours=2.0, efficiency_store=0.96, efficiency_dispatch=0.96,
          cyclic_state_of_charge=True, capital_cost=63_042.66 * months / 12.0)
    return n


_TOU_RATE = {"peak": 160.0, "off": 90.0}


def _toy_prices(idx) -> np.ndarray:
    wk = idx.weekday < 5
    peak = wk & (idx.hour >= 8) & (idx.hour < 20)
    return np.where(peak, _TOU_RATE["peak"], _TOU_RATE["off"]) + 20.0


@pytest.mark.live_solve
def test_s47_demand_formulation_gs_wrapper_equals_ic_add_demand_terms():
    """WP1 item 9 / §4.7 (WP6's toy, measured now): one 3-month toy solved
    with GS's `_wrap_with_demand_charge` (prices on the Link) and with IC's
    `add_demand_terms` (via `apply_commercial_for_solve` +
    `_wrap_with_commercial_bindings`, items `energy` TOU + `network:energy:0`
    + `demand` at `settlement="h"`): objectives ≤ 1e-7 relative, equal
    monthly peaks, equal battery size."""
    from services.commercial import connection as conn
    from services.commercial import lp_bindings as LP

    price_dc = 9000.0
    # GS
    g = _toy()
    g.links_t.marginal_cost[IMPORT] = _toy_prices(g.snapshots)
    gcfg = SimpleNamespace(demand_charge={"price_per_mw_per_period": price_dc,
                                          "basis": "billing_period_peak",
                                          "billing_period": "month",
                                          "import_links": [IMPORT]},
                           solve_strategy="full", sclopf=False, multi_investment_periods=False)
    st, cond = g.optimize(solver_name="highs",
                          extra_functionality=_gs_wrap_with_demand_charge(g, None, gcfg))
    assert (st, cond) == ("ok", "optimal")
    g_peaks = g.model.variables["peak_import"].solution.to_series()
    g_obj = float(g.objective)

    # IC
    i = _toy()
    tariff = {"id": "toy", "name": "toy", "jurisdiction": "DE", "valid_from": "2020-01-01",
              "items": [
                  {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
                      {"name": "peak", "rate": 0.160, "weekdays": [0, 1, 2, 3, 4],
                       "start_hour": 8, "end_hour": 20},
                      {"name": "off", "rate": 0.090}]},
                  {"id": "network:energy:0", "kind": "energy", "unit": "per_kwh",
                   "periods": [{"name": "all", "rate": 0.020}]},
                  {"id": "demand", "kind": "demand", "unit": "per_kw_month",
                   "settlement": "h", "periods": [{"name": "all", "rate": price_dc / 1000}]}]}
    com = {"poc_link": IMPORT, "import_tariff": tariff, "import_tariff_id": "toy"}
    icfg = SimpleNamespace(commercial=com)
    applied = conn.apply_commercial_for_solve(i, com)
    try:
        st, cond = i.optimize(solver_name="highs",
                              extra_functionality=LP._wrap_with_commercial_bindings(i, None, icfg))
        assert (st, cond) == ("ok", "optimal")
        i_obj = float(i.objective)
        i_peak = i.model.variables["ic_peak_import"].solution.to_series()
        i_billed = i.model.variables["ic_billed_demand"].solution.to_series()
    finally:
        applied.undo()
    applied.commit()
    rec = i.meta.get(LP.META_DEMAND) or {}

    g_by_month = {k: float(v) for k, v in g_peaks.items()}
    i_by_month = {}
    for key, v in i_peak.items():
        month = next(m for m in g_by_month if m in key)
        i_by_month[month] = float(v)
    p0g = g.links_t.p0[IMPORT].groupby(g.snapshots.strftime("%Y-%m")).max()
    p0i = i.links_t.p0[IMPORT].groupby(i.snapshots.strftime("%Y-%m")).max()
    print(f"\n§4.7 objectives GS {g_obj:,.6f} IC {i_obj:,.6f} rel "
          f"{abs(g_obj - i_obj) / abs(g_obj):.2e}; battery GS "
          f"{float(g.storage_units.p_nom_opt.iloc[0]):.6f} IC "
          f"{float(i.storage_units.p_nom_opt.iloc[0]):.6f}\n peaks GS {g_by_month}\n peaks IC "
          f"{i_by_month}\n IC keys {list(i_peak.index)}\n billed-peak max diff "
          f"{float((i_billed - i_peak).abs().max()):.2e}; committed record keys "
          f"{list(rec)[:3]}")
    assert abs(g_obj - i_obj) <= 1e-7 * abs(g_obj)
    assert set(i_by_month) == set(g_by_month) and len(g_by_month) == 3
    for m in g_by_month:
        assert abs(g_by_month[m] - i_by_month[m]) <= 1e-6, m
        assert abs(p0g[m] - g_by_month[m]) <= 1e-6 and abs(p0i[m] - i_by_month[m]) <= 1e-6
    assert abs(float(g.storage_units.p_nom_opt.iloc[0])
               - float(i.storage_units.p_nom_opt.iloc[0])) <= 1e-4



# ════════════════════════════════════════════════════════════════════════════
# §4.6 identity on the combined copy (C1 stand-in + C2 + C4), measured early
# ════════════════════════════════════════════════════════════════════════════


def _gs_salvage(n, ledger, *, pv: bool) -> float:
    """GS's annuity-PV salvage (`proforma.build_investment_case`, C2's V)."""
    from services.solver.periodized_costs import _annuity
    from services.study import packs
    from services.study.proforma import _annuity_pv_factor

    v = packs.ledger_values(ledger)
    r, horizon = v["discount_rate"], int(round(v["battery_storage_lifetime_years"]))
    inv_life = int(v["battery_inverter_lifetime_years"])
    p = _p_battery(n)
    up = packs.battery_upfront_eur_per_mw(ledger, float(n.storage_units.at["battery",
                                                                           "max_hours"]))
    last_buy = max(range(0, horizon, inv_life))
    out = up["inverter"] * p * _annuity(r, inv_life) * _annuity_pv_factor(
        r, last_buy + inv_life - horizon)
    if pv:
        q = float(n.generators.at["pv", "p_nom_opt"])
        life = float(n.generators.at["pv", "lifetime"])
        capex = float(n.generators.at["pv", "overnight_cost"]) * q
        out += capex * _annuity(r, life) * _annuity_pv_factor(r, life - horizon)
    return out


@pytest.mark.live_solve
@pytest.mark.parametrize("option,pv", [("bess_2h", False), ("bess_pv_2h", True)])
def test_s46_identity_npv_equals_lp_saving_times_af_on_the_ic_engine(option, pv):
    """§4.6 (WP7's test, measured early): on IC-solved site options, with
    rows 21–34, C2's `fixed` terminal value = GS's annuity-PV salvage, C4
    (escalation 0, inflation None, wacc = the real rate), the meter Links at
    typed 0 (Q5 way a) and the battery's ledger upfront typed post-solve as a
    STAND-IN for S0b's `upfront_parts` read: the engine's `project_pre_tax_npv`
    equals (obj_none − obj_option) × AF(0.07, 25) to 1e-6 relative, where
    obj_none is the IC-solved grid-only option. Also reports the IRR."""
    from services.finance.engine import run_case
    from services.study import packs
    from services.study.proforma import _annuity_pv_factor

    n, cfg = _solved(option)
    n_none, _c = _solved("none")
    led = _ledger()
    names = [a for _c2, a in _owned(cfg)]
    V = _gs_salvage(n, led, pv=pv)
    fin = _rows_21_34(names, p_mw=_p_battery(n), pv=pv, terminal=V)
    saved_l, saved_s = n.links["overnight_cost"].copy(), n.storage_units["overnight_cost"].copy()
    try:
        n.links.loc[[IMPORT, EXPORT], "overnight_cost"] = 0.0
        n.storage_units.loc["battery", "overnight_cost"] = packs.battery_upfront_eur_per_mw(
            led, float(n.storage_units.at["battery", "max_hours"]))["total"]
        res = run_case(_case(n, cfg, fin))
    finally:
        n.links["overnight_cost"] = saved_l
        n.storage_units["overnight_cost"] = saved_s
    af = _annuity_pv_factor(0.07, 25)
    want = (float(n_none.objective) - float(n.objective)) * af
    got = res.metrics["project_pre_tax_npv"]
    print(f"\n§4.6 {option}: NPV engine {got:,.4f}; LP saving x AF {want:,.4f}; rel "
          f"{abs(got - want) / abs(want):.2e}; V {V:,.2f}; IRR {res.metrics['project_pre_tax_irr']}"
          f"; flags {[f for f in res.flags if 'irr' in f]}")
    assert abs(got - want) <= 1e-6 * abs(want)
    # …and equals GS's S5 golden pro forma (MVP-1 findings note / S5 gate note:
    # NPV 371,681.602775077 / 1,566,950.761638796, IRR 15.5637 % / 12.8690 %),
    # bess_2h's IRR flagged for its multiple sign changes (§4.1).
    golden = {"bess_2h": (371_681.602775077, 0.155637),
              "bess_pv_2h": (1_566_950.761638796, 0.128690)}[option]
    assert abs(got - golden[0]) <= 1e-6 * golden[0]
    assert abs(res.metrics["project_pre_tax_irr"] - golden[1]) <= 5e-7
    assert ("project_pre_tax:irr_multiple_sign_changes" in res.flags) == (option == "bess_2h")


def test_q2_q3_q4_q6_engine_shapes_on_the_combined_copy():
    """Q2, Q3, Q4, Q6 (facts from the models and modules as merged):
    Q2 `FinanceInputs.replacement_capex` is `list[tuple[int, str, float]]`
    (year, asset, ABSOLUTE amount); Q3 `TerminalValueRule.method` has no
    remaining-life-annuity method; Q6 `FinanceInputs` has no currency-year
    field; Q4 the names exist (`finance.engine.payback`, `finance.metrics.irr`
    / `npv`, `finance.report.assemble_finance_sections`,
    `tariff_engine.RatingResult`) but the export line is only the PRIVATE
    `results.value_flows._export_revenue`, and the IC branch at 9b3f65a has
    no facade module, no defaults-pack loader and no flat-export-series helper
    (U1 follow-up a/c/e not landed)."""
    import pathlib
    import typing

    from models.finance import FinanceInputs, TerminalValueRule
    from services.commercial import tariff_engine
    from services.finance import engine, metrics, report
    from services.results import value_flows

    ann = FinanceInputs.model_fields["replacement_capex"].annotation
    assert typing.get_args(ann)[0] == tuple[int, str, float]
    methods = typing.get_args(TerminalValueRule.model_fields["method"].annotation)
    assert set(methods) == {"none", "book_value", "multiple_of_ebitda", "fixed"}
    assert not [f for f in FinanceInputs.model_fields if "currency_year" in f or
                f == "money_year"]
    assert callable(engine.payback) and callable(metrics.irr) and callable(metrics.npv)
    assert callable(report.assemble_finance_sections) and tariff_engine.RatingResult
    assert hasattr(value_flows, "_export_revenue")
    assert not [n for n in dir(value_flows) if "export" in n and not n.startswith("_")]
    root = pathlib.Path(engine.__file__).resolve().parents[1]
    blob = "\n".join(p.read_text() for p in root.rglob("*.py")
                     if "study" not in p.parts)
    assert "flat_export" not in blob and "defaults_pack" not in blob
