"""
The one reviewed mapping from a SAM "Single Owner" oracle case to P4's units
and quantities (IC P4 plan, "Fixtures and oracles"; WP4.0).

`sam_params(name)` reads a case's committed inputs and returns them in P4
units (currency, MWh, currency/MWh, fractions per year). `sam_expected(name)`
returns SAM's outputs in the same units, keyed by P4's quantity names (the
output mapping table: SAM's "project" return is P4's EQUITY return). Every
parity test builds its `FinanceCase` from `sam_params` and compares to
`sam_expected`; nothing else reads the JSON.

Conventions it encodes (each verified against PySAM 7.1.1.post1 in the plan
review, rounds 1–3):
- base year = the COD year; operating year k = 1 … analysis_years;
- O&M escalates ADDITIVELY: factor (1 + inflation + escal)^(k-1), i.e. a
  nominal rate inflation + escal — refused unless every O&M line escalates
  alike (P4 has one `opex` class);
- the PPA price escalates at `ppa_escalation` (nominal);
- degradation (1 - d)^(k-1);
- the equity discount rate = SAM's `nominal_discount_rate`
  = (1 + real)(1 + inflation) - 1, year 0 undiscounted.
"""
from __future__ import annotations

import csv
import json
import pathlib
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
CASES = ("s1", "s1b", "s2", "s3", "s3f")

# SAM's depreciation classes (the `depr_alloc_*_percent` suffixes).
DEPR_CLASSES = ("macrs_5", "macrs_15", "sl_5", "sl_15", "sl_20", "sl_39", "custom")

# Money arrays compared as is ($); energy arrays kWh → MWh.
_ENERGY_ARRAYS = ("cf_energy_net", "cf_energy_sales", "cf_energy_value")
_NO_DEBT_DSCR = 1e300          # SAM reports min_dscr as DBL_MAX without debt


class SamMappingError(ValueError):
    """The case uses a SAM input P4 cannot express."""


@dataclass(frozen=True)
class SamParams:
    name: str
    analysis_years: int
    inflation: float
    real_discount_rate: float
    nominal_discount_rate: float
    installed_cost: float
    capacity_kw: float
    energy_year1_mwh: float
    degradation: float
    ppa_price_per_mwh: float
    ppa_escalation: float
    ppa_solve: dict | None                   # {"target_irr", "target_year"} when ppa_soln_mode == 0
    opex_year1: float                        # O&M in COD-year money
    opex_escalation: float                   # nominal, additive (inflation + escal)
    federal_rate: float
    state_rate: float
    depreciation_alloc: dict[str, float]     # class → share of basis
    bonus_federal: dict[str, float]          # class → bonus share
    bonus_state: dict[str, float]
    itc_federal_percent: float
    itc_basis_reduction: dict[str, bool]     # layer ("federal"/"state") → 50 % reduction applies
    itc_reduction_classes: dict[str, list[str]]   # layer → classes whose basis the ITC reduces
    salvage_share: float
    debt: dict = field(default_factory=dict)


def load_case(name: str) -> dict:
    return json.loads((HERE / f"{name}.json").read_text())


def load_profile() -> list[float]:
    with (HERE / "gen_profile.csv").open() as fh:
        return [float(r["gen_kw"]) for r in csv.DictReader(fh)]


def _one(v):
    return v[0] if isinstance(v, list) else v


def sam_params(name: str) -> SamParams:
    d = load_case(name)
    i = d["sam_inputs"]
    fp, sc, rv, dep, tc = (i["FinancialParameters"], i["SystemCosts"], i["Revenue"],
                           i["Depreciation"], i["TaxCreditIncentives"])
    inflation = fp["inflation_rate"] / 100.0
    real = fp["real_discount_rate"] / 100.0
    escal = {sc["om_capacity_escal"], sc["om_fixed_escal"]}
    if _one(sc["om_production"]) != 0.0:
        escal.add(sc["om_production_escal"])
    if len(escal) != 1:
        raise SamMappingError(f"{name}: unequal O&M escalations {sorted(escal)} (one opex class)")
    for k in ("property_tax_rate", "insurance_rate", "months_working_reserve",
              "months_receivables_reserve", "construction_financing_cost", "cost_debt_closing",
              "cost_other_financing", "equip1_reserve_cost", "equip2_reserve_cost",
              "equip3_reserve_cost"):
        if fp[k]:
            raise SamMappingError(f"{name}: {k}={fp[k]} is not modelled in P4")
    for k in ("ptc_fed_amount", "ptc_sta_amount", "itc_sta_percent", "itc_sta_amount",
              "itc_fed_amount"):
        if _one(tc[k]):
            raise SamMappingError(f"{name}: {k}={tc[k]} is not in the P4 oracle cases")
    cap_kw = float(fp["system_capacity"])
    profile = load_profile()
    alloc = {c: dep[f"depr_alloc_{c}_percent"] / 100.0 for c in DEPR_CLASSES
             if dep[f"depr_alloc_{c}_percent"]}
    bonus_fed = {c: dep["depr_bonus_fed"] / 100.0 * dep[f"depr_bonus_fed_{c}"] for c in alloc}
    bonus_sta = {c: dep["depr_bonus_sta"] / 100.0 * dep[f"depr_bonus_sta_{c}"] for c in alloc}
    itc = _one(tc["itc_fed_percent"]) / 100.0
    debt = {"option": "dscr" if fp["debt_option"] == 1 else "percent",
            "percent": fp["debt_percent"] / 100.0, "dscr": fp["dscr"],
            "tenor_years": int(fp["term_tenor"]), "rate": fp["term_int_rate"] / 100.0,
            "fee_share": fp["cost_debt_fee"] / 100.0, "dsra_months": fp["dscr_reserve_months"],
            "reserves_rate": fp["reserves_interest"] / 100.0,
            "payment": "annuity" if fp["payment_option"] == 0 else "level",
            "grace_years": int(fp["loan_moratorium"])}
    if debt["option"] == "percent" and debt["percent"] == 0.0:
        debt = {}
    solve = None
    if rv["ppa_soln_mode"] == 0:
        solve = {"target_irr": rv["flip_target_percent"] / 100.0,
                 "target_year": int(rv["flip_target_year"])}
    return SamParams(
        name=name, analysis_years=int(fp["analysis_period"]), inflation=inflation,
        real_discount_rate=real, nominal_discount_rate=(1 + real) * (1 + inflation) - 1,
        installed_cost=float(sc["total_installed_cost"]), capacity_kw=cap_kw,
        energy_year1_mwh=sum(profile) / 1000.0,
        degradation=_one(i["SystemOutput"]["degradation"]) / 100.0,
        ppa_price_per_mwh=_one(rv["ppa_price_input"]) * 1000.0,
        ppa_escalation=rv["ppa_escalation"] / 100.0, ppa_solve=solve,
        opex_year1=_one(sc["om_capacity"]) * cap_kw + _one(sc["om_fixed"]),
        opex_escalation=inflation + escal.pop() / 100.0,
        federal_rate=_one(fp["federal_tax_rate"]) / 100.0,
        state_rate=_one(fp["state_tax_rate"]) / 100.0,
        depreciation_alloc=alloc, bonus_federal=bonus_fed, bonus_state=bonus_sta,
        itc_federal_percent=itc,
        itc_basis_reduction={"federal": bool(tc["itc_fed_percent_deprbas_fed"]) and itc > 0,
                             "state": bool(tc["itc_fed_percent_deprbas_sta"]) and itc > 0},
        itc_reduction_classes={"federal": [c for c in alloc if dep.get(f"depr_itc_fed_{c}")],
                               "state": [c for c in alloc if dep.get(f"depr_itc_sta_{c}")]},
        salvage_share=fp["salvage_percentage"] / 100.0, debt=debt)


def sam_expected(name: str) -> dict:
    """SAM's outputs in P4 units: `arrays` (year 0 … analysis_years) and
    `scalars` keyed by P4's names (the plan's output mapping table)."""
    out = load_case(name)["outputs"]
    s, a = out["scalars"], out["arrays"]
    arrays = {k: ([x / 1000.0 for x in v] if k in _ENERGY_ARRAYS else list(v))
              for k, v in a.items()}
    arrays["cf_ppa_price"] = [x * 10.0 for x in a["cf_ppa_price"]]      # ¢/kWh → $/MWh
    min_dscr = s["min_dscr"]
    return {
        "arrays": arrays,
        "scalars": {
            "equity_irr_post_tax": s["project_return_aftertax_irr"] / 100.0,
            "equity_npv_post_tax": s["project_return_aftertax_npv"],
            "equity_discount_rate": s["nominal_discount_rate"],
            "debt_size": s["size_of_debt"],
            "equity_size": s["size_of_equity"],
            "min_dscr": None if min_dscr is None or min_dscr > _NO_DEBT_DSCR else min_dscr,
            "ppa_price_per_mwh": s["ppa_price"] * 10.0,
            "lcoe_nominal_per_mwh": s["lcoe_nom"] * 10.0,
            "lcoe_real_per_mwh": s["lcoe_real"] * 10.0,
            "itc_total": s["itc_total"],
        },
        "deviations": load_case(name)["deviations"],
    }


# ── SAM → FinanceCase (grows with the WPs that can reach each quantity) ─────

SAM_Y0 = 2030          # SAM's year 0 (the investment year) — a calendar anchor for P4's axis


def to_finance_case(name: str):
    """A `FinanceCase` for the SAM case: one construction year (SAM's year 0),
    COD on 1 January of the next year = the base year (plan C4), one asset
    `pv` carrying the installed cost, a PPA line (the contract's own
    indexation = `ppa_escalation`, degrading with `pv`) and an O&M line
    (class `opex`, nominal rate inflation + escal)."""
    from datetime import date

    from models.finance import FinanceInputs, TerminalValueRule
    from services.finance.case import (
        CONTRACT_CLASS, AssetFinance, FinanceCase, Template, TemplateLine,
    )

    p = sam_params(name)
    # S1b solves the price: its cashflows are SAM's at the solved price.
    price = sam_expected(name)["scalars"]["ppa_price_per_mwh"] if p.ppa_solve else p.ppa_price_per_mwh
    fin = FinanceInputs(
        currency="USD", financial_close=date(SAM_Y0, 1, 1), cod_by_asset={"pv": date(SAM_Y0 + 1, 1, 1)},
        capex_phasing=[1.0], contingency_share=0.0, analysis_years=p.analysis_years,
        escalation={"opex": p.opex_escalation, "ppa": p.ppa_escalation, "capex": 0.0},
        degradation_by_asset={"pv": p.degradation},
        terminal_value=TerminalValueRule(method="fixed", value=p.salvage_share * p.installed_cost)
        if p.salvage_share else TerminalValueRule(),
        wacc_nominal=p.nominal_discount_rate, cost_of_equity=p.nominal_discount_rate,
        inflation=p.inflation, tax_losses="offset_other_income",
        financing_fee_tax="not_deducted")
    lines = (
        TemplateLine(key="ppa", stream="ppa_settlement", amount=p.energy_year1_mwh * price,
                     esc_class=CONTRACT_CLASS, indexation=p.ppa_escalation, contract_id="ppa",
                     degrades_with="pv", counterparty="offtaker", source="contract"),
        TemplateLine(key="om", stream="fom", amount=-p.opex_year1, esc_class="opex",
                     counterparty="om_contractor", source="asset"),
    )
    return FinanceCase(
        inputs=fin, owner="owner", base_year=SAM_Y0 + 1, cod=date(SAM_Y0 + 1, 1, 1),
        templates=(Template(first_year=SAM_Y0 + 1, lines=lines,
                            energy_mwh={"pv": p.energy_year1_mwh}),),
        assets=(AssetFinance(name="pv", component="Generator", overnight_cost=p.installed_cost,
                             lifetime_years=float(p.analysis_years)),))


def sam_tax_layers(name: str):
    """SAM's two tax layers (plan C7): state first, deductible from federal;
    each with its own depreciation profile (SAM's `depr_alloc_*` shares, the
    layer's bonus per class, the ITC basis-reduction flag per layer). MACRS
    schedules come from the `us_federal` pack's Table A-1 (a cross-check of the
    pack's data against SAM); SL-n is the half-year convention."""
    from services.finance.packs.base import load_pack
    from services.finance.packs.us_federal import macrs_fractions
    from services.finance.tax import DepreciationClass, TaxLayer, sl_half_year

    p = sam_params(name)
    pack = load_pack("us_federal", as_of=__import__("datetime").date(2026, 1, 1))

    def schedule(cls: str):
        kind, years = cls.split("_")
        if kind == "macrs":
            return macrs_fractions(pack, years)
        if kind == "sl":
            return sl_half_year(int(years))
        raise SamMappingError(f"{name}: depreciation class {cls!r} not mapped")

    def classes(bonus: dict, layer: str):
        return tuple(DepreciationClass(name=c, share=s, schedule=schedule(c), bonus=bonus[c],
                                       itc_reduces=c in p.itc_reduction_classes[layer])
                     for c, s in p.depreciation_alloc.items())

    state = TaxLayer(name="state", rate=p.state_rate, depreciation=classes(p.bonus_state, "state"),
                     deductible_in_later_layers=True,
                     itc_basis_reduction=p.itc_basis_reduction["state"])
    federal = TaxLayer(name="federal", rate=p.federal_rate,
                       depreciation=classes(p.bonus_federal, "federal"),
                       itc_basis_reduction=p.itc_basis_reduction["federal"])
    return (state, federal)
