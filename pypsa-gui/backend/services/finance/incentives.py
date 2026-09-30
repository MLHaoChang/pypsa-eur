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
                     itc_assets=(), grant_basis_reduction=0.0)
    if not fin.incentives:
        return out
    reasons, flags = out.reasons, out.flags
    us = pack is not None and pack.jurisdiction == "us_federal"
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
    for i, inc in enumerate(fin.incentives):
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
            assets = [a for a in assets if a.carrier in el.asset_classes]
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
                if inc.feoc_flag is None:
                    reasons.append(f"feoc_flag_missing:{tag}")
                    continue
            if inc.feoc_flag:
                line.flags.append(f"incentive_ineligible:{tag}:feoc")
                continue
            if any(a.carrier is None for a in assets):
                reasons.extend(f"asset_carrier_missing:{a.name}" for a in assets if a.carrier is None)
                continue
            if cod > date.fromisoformat(term["placed_in_service_by"]) and \
                    cs > date.fromisoformat(term["unless_construction_begins_by"]):
                ended = [a for a in assets if a.carrier in term["carriers"]]
                for a in ended:
                    line.flags.append(f"incentive_ineligible:{tag}:wind_solar_termination:{a.name}")
                assets = [a for a in assets if a not in ended]
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
        elif inc.kind == "grant":
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
            out.grant_basis_reduction += amount
            flags.append("grant_reduces_basis_pro_rata")
    flags.extend(f for ln in out.lines for f in ln.flags)
    out.itc_assets = tuple(sorted(itc_assets))
    out.reasons = sorted(set(reasons))
    out.flags = sorted(set(flags))
    if out.reasons:
        out.status = "not_established"
    return out


__all__ = ["Incentive", "Incentives", "IncentiveLine", "build_incentives", "round_to"]
