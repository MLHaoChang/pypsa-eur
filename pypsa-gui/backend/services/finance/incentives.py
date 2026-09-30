"""
Incentives with dated rules (IC P4 plan WP4.4; C2, C11, C12).

`build_incentives(case, tl, op, pack)` values each `FinanceInputs.incentives`
entry for the owner's eligible assets:

- **ITC**: rate × eligible basis (installed capex incl. contingency, C6 — IDC
  and fees are not in it), capped by `amount`, × the phase-out share; credited
  to AFTER-TAX cash in the first operating year (SAM, verified), never netted
  in the tax line; the basis reduction (50 % of the credit on the layers that
  take it, §50(c)) is the tax engine's, fed `itc_amount` and `itc_assets`.
- **PTC** (a pack rule): the calendar year's rate × the asset's degraded
  generation for the pack's term from COD (operating years — stated), × the
  phase-out share. The US §45Y rate is the statutory amount × the published
  inflation adjustment factor of the calendar year, ROUNDED each year to the
  statutory step; a year after the last published factor projects it at
  `inflation` (flagged `ptc_factor_projected:<year>`).
- **Grant**: `amount`, or `rate` × eligible basis, as cash at the COD point;
  it reduces the depreciable basis (flagged — the reduction is spread pro rata
  over the classes).
- `accelerated_depreciation` is a depreciation class
  (`depreciation_class_by_asset`); `cfd` / `capacity_payment` are contracts
  (spec §6.5) — refused here, never silently dropped.

Eligibility against the case dates: `begin_construction_by` needs
`construction_start`; `placed_in_service_by` reads COD. An incentive's own
`phase_out` [(date, share)] applies the share of the latest date on or before
construction start. Under the `us_federal` pack the statutory rules also apply
to ITC / PTC: the PWA multiplier (`pwa_met` None → not established, never an
assumed 30 %), the phase-out after the applicable year, the 2025 act's wind /
solar termination, and FEOC (`feoc_flag` None → not established where the rule
applies, True → ineligible, False → eligible).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from models.finance import Incentive
from services.finance.case import AssetFinance, FinanceCase
from services.finance.cashflow import Operating
from services.finance.packs.base import JurisdictionPack
from services.finance.timeline import Timeline

ELSEWHERE = {"accelerated_depreciation": "depreciation_class_by_asset",
             "cfd": "a contract (P2)", "capacity_payment": "a contract (P2)"}


@dataclass
class IncentiveLine:
    index: int
    kind: str
    assets: tuple[str, ...]
    cash: np.ndarray                     # per axis year
    share: float = 1.0                   # the phase-out share applied
    rate: float | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class Incentives:
    itc: np.ndarray                      # after-tax credit cash per year
    ptc: np.ndarray                      # after-tax credit cash per year
    grant: np.ndarray                    # pre-tax cash per year
    itc_amount: float                    # Σ ITC — the basis reduction's base
    itc_assets: tuple[str, ...]          # the assets whose basis the ITC reduces
    grant_basis_reduction: float
    # Grants taxed as income when received (grant_tax_treatment="taxable").
    grant_taxable: np.ndarray | None = None
    lines: list[IncentiveLine] = field(default_factory=list)
    status: str = "ok"
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def established(self) -> bool:
        return self.status == "ok"


def round_to(x: float, step: float) -> float:
    """Round to the nearest multiple of `step`, halves up (§45Y(c)(2))."""
    return math.floor(x / step + 0.5 + 1e-12) * step


def _share_by_date(schedule: list[tuple[date, float]], when: date) -> float:
    share = 1.0
    for d, s in sorted(schedule):
        if d <= when:
            share = s
    return share


def _basis(assets: list[AssetFinance], contingency: float) -> float | None:
    if any(a.overnight_cost is None for a in assets):
        return None
    return sum(a.overnight_cost for a in assets) * (1.0 + contingency)


def build_incentives(case: FinanceCase, tl: Timeline, op: Operating,
                     pack: JurisdictionPack | None = None) -> Incentives:
    fin = case.inputs
    n = tl.n
    c = tl.index(tl.cod_year)
    p = max(c - 1, 0)
    out = Incentives(itc=np.zeros(n), ptc=np.zeros(n), grant=np.zeros(n), itc_amount=0.0,
                     itc_assets=(), grant_basis_reduction=0.0, grant_taxable=np.zeros(n))
    if not fin.incentives:
        return out
    reasons, flags = out.reasons, out.flags
    us = pack is not None and pack.jurisdiction == "us_federal"
    ca = pack is not None and pack.jurisdiction == "ca_federal"
    cs = fin.construction_start
    cod = case.cod

    def rule(name):
        r = pack.rule(name)
        if r.status != "ok":
            reasons.append(f"pack_rule:{name}")
            return None
        out.sources[name] = r.source
        return r.value

    itc_assets: set[str] = set()
    ptc_assets: set[str] = set()
    grant_on: dict[str, float] = {}       # basis-reducing grant per asset (reduces the ITC base)
    # Grants first: a basis-reducing grant lowers the ITC base of its assets.
    order = sorted(range(len(fin.incentives)), key=lambda j: fin.incentives[j].kind != "grant")
    for i in order:
        inc = fin.incentives[i]
        tag = f"{i}:{inc.kind}"
        if inc.kind in ELSEWHERE:
            reasons.append(f"incentive_expressed_elsewhere:{tag}:{ELSEWHERE[inc.kind]}")
            continue
        el = inc.eligibility
        assets = list(case.assets)
        if el.asset_classes:
            unknown = [a.name for a in assets if a.carrier is None]
            if unknown:
                reasons.extend(f"asset_carrier_missing:{a}" for a in unknown)
                continue
            want = {c.lower() for c in el.asset_classes}
            assets = [a for a in assets if a.carrier.lower() in want]
        line = IncentiveLine(index=i, kind=inc.kind, assets=tuple(a.name for a in assets),
                             cash=np.zeros(n))
        out.lines.append(line)
        # Eligibility against the case dates (C2).
        if el.begin_construction_by is not None:
            if cs is None:
                reasons.append(f"input_missing:construction_start:{tag}")
                continue
            if cs > el.begin_construction_by:
                line.flags.append(f"incentive_ineligible:{tag}:begin_construction_by")
                continue
        if el.placed_in_service_by is not None and cod > el.placed_in_service_by:
            line.flags.append(f"incentive_ineligible:{tag}:placed_in_service_by")
            continue
        share = 1.0
        if inc.phase_out:
            if cs is None:
                reasons.append(f"input_missing:construction_start:{tag}")
                continue
            share = _share_by_date(inc.phase_out, cs)
        statutory = us and inc.kind in ("itc", "ptc")
        if statutory:
            if cs is None:
                reasons.append(f"input_missing:construction_start:{tag}")
                continue
            po, term, feoc = (rule("clean_electricity_phase_out"), rule("wind_solar_termination"),
                              rule("feoc_material_assistance"))
            if None in (po, term, feoc):
                continue
            k = cs.year - po["applicable_year"]
            share *= po["shares_after"][k - 1] if 1 <= k <= len(po["shares_after"]) else (
                1.0 if k < 1 else po["later"])
            if cs > date.fromisoformat(feoc["construction_begins_after"]):
                # The FEOC rule reaches only construction after its date.
                if inc.feoc_flag is None:
                    reasons.append(f"feoc_flag_missing:{tag}")
                    continue
                if inc.feoc_flag:
                    line.flags.append(f"incentive_ineligible:{tag}:feoc")
                    continue
            if any(a.carrier is None for a in assets):
                reasons.extend(f"asset_carrier_missing:{a.name}" for a in assets if a.carrier is None)
                continue
            tech = rule("clean_electricity_technology")
            if tech is None:
                continue
            # Fail closed (WP4.4 review B1/B2): a carrier the pack does not
            # classify is not established; a non-qualifying one is ineligible;
            # storage takes the ITC, never the PTC.
            # Carriers match case-insensitively (review r2 #4).
            tech = {k: {c.lower() for c in v} for k, v in tech.items()}
            unknown = [a for a in assets if not any(
                a.carrier.lower() in tech[k] for k in ("wind_solar", "other_zero_emission",
                                                       "storage", "not_qualifying"))]
            if unknown:
                reasons.extend(f"incentive_technology_unclassified:{a.name}:{a.carrier}"
                               for a in unknown)
                continue
            keep = []
            for a in assets:
                cr = a.carrier.lower()
                if cr in tech["not_qualifying"]:
                    line.flags.append(f"incentive_ineligible:{tag}:technology:{a.name}")
                elif inc.kind == "ptc" and cr in tech["storage"]:
                    line.flags.append(f"incentive_ineligible:{tag}:ptc_not_for_storage:{a.name}")
                elif cr in tech["wind_solar"] and \
                        cod > date.fromisoformat(term["placed_in_service_by"]) and \
                        cs > date.fromisoformat(term["unless_construction_begins_by"]):
                    line.flags.append(f"incentive_ineligible:{tag}:wind_solar_termination:{a.name}")
                else:
                    keep.append(a)
            assets = keep
            line.assets = tuple(a.name for a in assets)
        elif inc.feoc_flag:
            line.flags.append(f"incentive_ineligible:{tag}:feoc")
            continue
        line.share = share
        if not assets:
            line.flags.append(f"incentive_no_eligible_asset:{tag}")
            continue
        names = {a.name for a in assets}
        if inc.kind == "itc":
            if names & ptc_assets:
                reasons.append(f"itc_and_ptc_same_asset:{tag}")
                continue
            if names & itc_assets:
                reasons.append(f"itc_twice_same_asset:{tag}")      # review B5
                continue
            rate = inc.rate
            if rate is None and statutory:
                r = rule("clean_electricity_itc")
                if r is None:
                    continue
                if fin.pwa_met is None:
                    reasons.append(f"input_missing:pwa_met:{tag}")
                    continue
                rate = r["alternative"] if fin.pwa_met else r["base"]
                flags.append("itc_bonus_adders_not_modelled")
            if rate is None and ca:
                # The Clean Technology ITC (s. 127.45) on Class 43.1 / 43.2
                # property, by the year it becomes available for use (COD).
                r = rule("clean_technology_itc")
                if r is None:
                    continue
                classes = {a.name: (fin.depreciation_class_by_asset.get(a.name) or "")
                           for a in assets}
                off = [a for a in assets if not classes[a.name].startswith(("cca_43.1", "cca_43.2"))]
                for a in off:
                    line.flags.append(f"incentive_ineligible:{tag}:not_class_43:{a.name}")
                assets = [a for a in assets if a not in off]
                line.assets = tuple(a.name for a in assets)
                if not assets:
                    continue
                if cod < date.fromisoformat(r["available_from"]):
                    line.flags.append(f"incentive_ineligible:{tag}:before_available_from")
                    continue
                by = {int(k): v for k, v in r["rate_by_available_for_use_year"].items()}
                rate = by[max(y for y in by if y <= cod.year)] if cod.year >= min(by) else 0.0
                if rate > 0:
                    if fin.pwa_met is None:
                        reasons.append(f"input_missing:pwa_met:{tag}")   # the labour requirements
                        continue
                    if not fin.pwa_met:
                        rate = max(0.0, rate - r["labour_requirements_reduction"])
                names = {a.name for a in assets}
            if rate is None:
                reasons.append(f"incentive_rate_missing:{tag}")
                continue
            if fin.contingency_share is None:
                reasons.append("contingency_share_missing")
                continue
            base = _basis(assets, fin.contingency_share)
            if base is None:
                reasons.append(f"overnight_cost_missing:{tag}")
                continue
            # A basis-reducing grant on these assets reduces the ITC base (SAM
            # `ibi/cbi_*_deprbas_fed`, review B3).
            base -= sum(grant_on.get(a.name, 0.0) for a in assets)
            if base < 0:
                reasons.append(f"grant_exceeds_itc_base:{tag}")        # review r2-1
                continue
            amount = rate * base * share
            if inc.amount is not None and amount > inc.amount:
                amount = inc.amount
                line.flags.append(f"itc_capped:{tag}")
            line.rate = rate
            if c < n:
                line.cash[c] = amount
            out.itc += line.cash
            out.itc_amount += amount
            itc_assets |= names
        elif inc.kind == "ptc":
            if names & itc_assets:
                reasons.append(f"itc_and_ptc_same_asset:{tag}")
                continue
            if names & ptc_assets:
                reasons.append(f"ptc_twice_same_asset:{tag}")      # review r2-2
                continue
            if not us:
                reasons.append(f"ptc_needs_a_pack_rule:{tag}")
                continue
            r = rule("clean_electricity_ptc")
            if r is None:
                continue
            if fin.currency != "USD":
                reasons.append(f"ptc_currency_mismatch:{fin.currency}")
                continue
            if inc.rate is None and fin.pwa_met is None:
                reasons.append(f"input_missing:pwa_met:{tag}")
                continue
            which = "alternative" if fin.pwa_met else "base"
            factors = {int(y): v for y, v in r["inflation_adjustment_factor"].items()}
            last = max(factors)
            ok = True
            for kk in range(1, int(r["term_years"]) + 1):
                i_ax = c + kk - 1
                if i_ax >= n:
                    break
                year = int(tl.years[i_ax])
                if inc.rate is not None:
                    # A stated rate: currency/MWh in COD-year money, escalated
                    # at `inflation`, not rounded (stated).
                    if fin.inflation is None:
                        reasons.append(f"input_missing:inflation:{tag}")
                        ok = False
                        break
                    per_mwh = inc.rate * (1.0 + fin.inflation) ** (year - tl.cod_year)
                else:
                    if year in factors:
                        f = factors[year]
                    elif year > last:
                        if fin.inflation is None:
                            reasons.append(f"input_missing:inflation:{tag}")
                            ok = False
                            break
                        f = factors[last] * (1.0 + fin.inflation) ** (year - last)
                        flags.append(f"ptc_factor_projected:{year}")
                    else:
                        reasons.append(f"ptc_factor_unpublished:{year}")
                        ok = False
                        break
                    cents = round_to(r[f"{which}_cents_per_kwh"] * f, r["rounding_cents"][which])
                    per_mwh = cents * 10.0                   # ¢/kWh → $/MWh
                gen = 0.0
                for a in assets:
                    e = op.energy_mwh.get(a.name)
                    if e is None:
                        reasons.append(f"generation_missing:{a.name}")
                        ok = False
                        break
                    gen += e[i_ax]
                if not ok:
                    break
                line.cash[i_ax] = per_mwh * gen * share
            if not ok:
                continue
            out.ptc += line.cash
            ptc_assets |= names
            flags.extend(["ptc_term_in_operating_years", "ptc_on_all_generation"])
        elif inc.kind == "grant":
            if inc.grant_tax_treatment is None:
                reasons.append(f"input_missing:grant_tax_treatment:{tag}")
                continue
            if inc.amount is not None:
                amount = inc.amount * share
            elif inc.rate is not None:
                if fin.contingency_share is None:
                    reasons.append("contingency_share_missing")
                    continue
                base = _basis(assets, fin.contingency_share)
                if base is None:
                    reasons.append(f"overnight_cost_missing:{tag}")
                    continue
                amount = inc.rate * base * share
            else:
                reasons.append(f"incentive_rate_missing:{tag}")
                continue
            line.cash[p] = amount
            out.grant += line.cash
            if inc.grant_tax_treatment == "taxable":
                out.grant_taxable += line.cash
            else:
                out.grant_basis_reduction += amount
                flags.append("grant_reduces_basis_pro_rata")
                if any(a.overnight_cost is None for a in assets):
                    reasons.append(f"overnight_cost_missing:{tag}")
                    continue
                bases = {a.name: a.overnight_cost for a in assets}
                tot = sum(bases.values())
                cont = 1.0 + (fin.contingency_share or 0.0)
                # The running total per asset never exceeds its cost (review r3-2).
                new = {a: grant_on.get(a, 0.0) + (amount * b / tot if tot else amount)
                       for a, b in bases.items()}
                if any(new[a] > bases[a] * cont + 1e-9 for a in bases):
                    reasons.append(f"grant_exceeds_cost:{tag}")   # never a negative basis
                    continue
                grant_on.update(new)
    flags.extend(f for ln in out.lines for f in ln.flags)
    out.itc_assets = tuple(sorted(itc_assets))
    out.reasons = sorted(set(reasons))
    out.flags = sorted(set(flags))
    if out.reasons:
        out.status = "not_established"
    return out


__all__ = ["Incentive", "Incentives", "IncentiveLine", "build_incentives", "round_to"]
