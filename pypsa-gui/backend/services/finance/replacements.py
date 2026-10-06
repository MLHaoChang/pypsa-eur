"""
The replacement schedule (IC S0b plan S4, S5; decision D9).

`schedule(case, tl)` is the ONE reader of the replacement inputs: the cash
(`cashflow.build_operating`), the tax vintages (`engine._replacement_vintages`),
the storage LCOS (`lcos.storage_lcos`) and the `asset_lifetime_short` check
(`timeline.build_timeline`) all read it. Each `Replacement` is unescalated (the
amount in the base year's money); the readers escalate it by the `capex` class
from `base_year`, with no contingency, and keep it on the operating axis.

Two rules (`FinanceInputs.replacement_rule`):

* `fixed` (the default) — exactly the `replacement_capex` entries, in their
  order (`source="fixed"`, `part=None`: an entry names an asset, not a part).
  A part shorter than the axis whose asset no entry names is flagged
  `part_not_replaced:<asset>:<part>` (a battery inverter at 10 y in a 25-y case).
* `part_lifetimes` (S5) — every owner asset's effective part (S2) with a finite
  lifetime L and an established cost is re-bought at that cost in the LAST
  SERVICE YEAR of the previous purchase, `cod_year + k·L − 1` for every k ≥ 1
  with k·L < `analysis_years` (GS's t = k·L, `proforma.py:362`, with the
  financial close one year before COD — review B1). A non-whole k·L rounds half
  up (`floor(k·L + 0.5)`), flagged `part_lifetime_rounded:<asset>:<part>` (GS
  refuses a non-whole lifetime; this is an approximation). An infinite L is never
  replaced; a missing one is `part_lifetime_missing:<asset>:<part>` (capex not
  established); one below a year is refused `part_lifetime_too_short:<asset>:<part>`.
  The rule governs an asset with at least one finite part lifetime: a
  `replacement_capex` entry for such an asset is refused
  `replacement_rule_conflict:<asset>` (double counting); entries for the other
  assets (every part infinite: the rule never replaces them) still apply.

`remaining_life_terms` (S6, decision D10) values the purchase of each part
still alive at the horizon on the annuity the LP charged for it — the
`remaining_life_annuity` terminal value; it reads the same purchases.

Pure: reads the `FinanceCase` and its `Timeline`, never a network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from services.finance.case import (
    AssetFinance, AssetPart, FinanceCase, FinanceRefused, effective_parts,
)

if TYPE_CHECKING:
    from services.finance.timeline import Timeline

FIXED = "fixed"
PART_LIFETIMES = "part_lifetimes"


@dataclass(frozen=True)
class Replacement:
    """One replacement purchase (S4): in `year` (calendar), of `asset`'s `part`
    (None for a `fixed` entry), `amount` unescalated (the base year's money);
    `source` is `"fixed"` or `"part_lifetimes"`."""

    year: int
    asset: str
    part: str | None
    amount: float
    source: str


def _finite(life: float | None) -> bool:
    return life is not None and math.isfinite(life)


def _repurchases(life: float, analysis_years: int) -> list[int]:
    """The k ≥ 1 with k·L < `analysis_years` (S5)."""
    out, k = [], 1
    while k * life < analysis_years:
        out.append(k)
        k += 1
    return out


def replacement_year(cod_year: int, k: int, life: float) -> int:
    """The k-th re-purchase of a part of lifetime L: the last service year of
    the previous purchase, `cod_year + floor(k·L + 0.5) − 1` (S5, half up)."""
    return cod_year + math.floor(k * life + 0.5) - 1


def governed(a: AssetFinance) -> bool:
    """Whether `part_lifetimes` replaces this asset: some effective part has a
    finite lifetime (S5)."""
    return any(_finite(p.lifetime_years) for p in effective_parts(a))


def check(case: FinanceCase) -> None:
    """The refusals of S5 under `part_lifetimes` (`build_timeline` runs it):
    `replacement_rule_conflict:<asset>`, `part_lifetime_too_short:<asset>:<part>`."""
    if case.inputs.replacement_rule != PART_LIFETIMES:
        return
    by_name = {a.name: a for a in case.assets}
    for year, asset, _amount in case.inputs.replacement_capex:
        a = by_name.get(asset)
        if a is not None and governed(a):
            raise FinanceRefused(f"replacement_rule_conflict:{asset}",
                                 f"a replacement_capex entry ({year}) for {asset}, which "
                                 "replacement_rule part_lifetimes already replaces "
                                 "(double counting)")
    for a in case.assets:
        for p in effective_parts(a):
            if _finite(p.lifetime_years) and p.lifetime_years < 1.0:
                raise FinanceRefused(f"part_lifetime_too_short:{a.name}:{p.name}",
                                     f"lifetime {p.lifetime_years:g} y is below a year")


def _generated(case: FinanceCase, tl: Timeline) -> list[tuple[Replacement, int, AssetPart]]:
    """(the replacement, its k, its part) for every `part_lifetimes` purchase."""
    out = []
    for ai, a in enumerate(case.assets):
        for pi, p in enumerate(effective_parts(a)):
            if p.overnight_cost is None or not _finite(p.lifetime_years):
                continue
            for k in _repurchases(p.lifetime_years, tl.analysis_years):
                out.append(((replacement_year(tl.cod_year, k, p.lifetime_years), ai, pi),
                            Replacement(replacement_year(tl.cod_year, k, p.lifetime_years),
                                        a.name, p.name, p.overnight_cost, PART_LIFETIMES), k, p))
    out.sort(key=lambda x: x[0])
    return [(r, k, p) for _key, r, k, p in out]


def schedule(case: FinanceCase, tl: Timeline) -> tuple[Replacement, ...]:
    """Every replacement of the case (S4), unescalated: the `replacement_capex`
    entries (`fixed`), then under `part_lifetimes` the generated purchases by
    year. Refuses as `check` does."""
    check(case)
    fixed = tuple(Replacement(int(y), asset, None, float(amount), FIXED)
                  for y, asset, amount in case.inputs.replacement_capex)
    if case.inputs.replacement_rule != PART_LIFETIMES:
        return fixed
    return fixed + tuple(r for r, _k, _p in _generated(case, tl))


def notes(case: FinanceCase, tl: Timeline) -> tuple[list[str], list[str]]:
    """(capex reasons, flags) of the schedule (S5): `part_lifetime_missing:<a>:<p>`
    (not established); `part_lifetime_rounded:<a>:<p>`, `part_not_replaced:<a>:<p>`."""
    reasons: list[str] = []
    flags: list[str] = []
    if case.inputs.replacement_rule == PART_LIFETIMES:
        for a in case.assets:
            for p in effective_parts(a):
                if p.overnight_cost is None:
                    continue                    # the capex already reads overnight_cost_missing
                if p.lifetime_years is None:
                    reasons.append(f"part_lifetime_missing:{a.name}:{p.name}")
                elif math.isfinite(p.lifetime_years) and not float(p.lifetime_years).is_integer() \
                        and _repurchases(p.lifetime_years, tl.analysis_years):
                    flags.append(f"part_lifetime_rounded:{a.name}:{p.name}")
        return reasons, flags
    named = {asset for _y, asset, _x in case.inputs.replacement_capex}
    for a in case.assets:
        if a.name in named:
            continue
        for p in effective_parts(a):
            if _finite(p.lifetime_years) and p.lifetime_years < tl.analysis_years:
                flags.append(f"part_not_replaced:{a.name}:{p.name}")
    return reasons, flags


def counts_as_replaced(case: FinanceCase, a: AssetFinance,
                       replacements: tuple[Replacement, ...]) -> bool:
    """For the `asset_lifetime_short` check (S5): a `fixed` replacement names
    the asset, or under `part_lifetimes` every effective part has a known
    lifetime (a finite one is scheduled, an infinite one outlives the axis)."""
    if any(r.asset == a.name and r.source == FIXED for r in replacements):
        return True
    return case.inputs.replacement_rule == PART_LIFETIMES and \
        all(p.lifetime_years is not None for p in effective_parts(a))


def on_axis(tl: Timeline, year: int) -> bool:
    """A replacement must lie on the operating axis."""
    return tl.cod_year <= year < tl.cod_year + tl.analysis_years


# ── the remaining-life terminal value (S6, decision D10) ────────────────────

def annuity(r: float, life: float) -> float | None:
    """The capital recovery factor r / (1 − (1 + r)^−L); 1/L at r = 0 (the
    LP's annuity, `periodized_costs._annuity`). None for L ≤ 0, which has no
    annuity (review r1 B1: never a division by zero)."""
    if life <= 0:
        return None
    return 1.0 / life if r == 0 else r / (1.0 - (1.0 + r) ** -life)


def annuity_pv_factor(r: float, years: float) -> float:
    """(1 − (1 + r)^−y) / r; y at r = 0; 0 when y ≤ 0 (GS's `_annuity_pv_factor`)."""
    if years <= 0:
        return 0.0
    return float(years) if r == 0 else (1.0 - (1.0 + r) ** -years) / r


@dataclass(frozen=True)
class TerminalTerm:
    """One part's remaining-life value (S6): the part's last purchase serves
    from `start_year` (unrounded: `cod_year + k·L`) for `lifetime_years`;
    `remaining_years` of it lie past the horizon; it cost `base` (the part's
    cost for the initial purchase, the escalated amount for a replacement);
    `value` = base × annuity(`asset_rate`, L) × annuity_pv_factor(`rate`,
    remaining) — 0 when nothing remains."""

    asset: str
    part: str
    lifetime_years: float
    start_year: float
    remaining_years: float
    base: float
    asset_rate: float
    rate: float
    annuity: float
    pv_factor: float
    value: float


def remaining_life_terms(case: FinanceCase, tl: Timeline
                         ) -> tuple[tuple[TerminalTerm, ...], list[str]]:
    """The `remaining_life_annuity` terms and the reasons it is not
    established (S6): `terminal_needs_lp_rate` (no `lp_basis` or no rate),
    `terminal_part_unknown:<asset>:<part>` (no cost, or a missing, infinite or
    below-a-year lifetime — GS's `salvage_not_computed`), `terminal_needs_part_lifetimes:<asset>`
    (under `fixed`, `replacement_capex` entries for an asset with more than one
    part: an entry names an asset, not a part), `escalation_missing:capex` (a
    replacement's base cannot be stated). `r_a` is the asset's own discount
    rate, else the LP's; `r` the LP's (GS's; the LP basis, not the WACC)."""
    lp = case.lp_basis
    if lp is None or lp.discount_rate is None:
        return (), ["terminal_needs_lp_rate"]
    r = float(lp.discount_rate)
    fin = case.inputs
    r_capex = fin.escalation.get("capex")
    horizon = tl.cod_year + tl.analysis_years
    entries: dict[str, list[tuple[int, float]]] = {}
    for year, asset, amount in fin.replacement_capex:
        entries.setdefault(asset, []).append((int(year), float(amount)))
    terms: list[TerminalTerm] = []
    reasons: list[str] = []
    for a in case.assets:
        parts = effective_parts(a)
        if fin.replacement_rule == FIXED and len(parts) > 1 and a.name in entries:
            reasons.append(f"terminal_needs_part_lifetimes:{a.name}")
            continue
        own = lp.asset_discount_rates.get(a.name)
        r_a = r if own is None else float(own)
        for p in parts:
            life = p.lifetime_years
            # No cost, no lifetime, an infinite one (GS's `salvage_not_computed`) or
            # one below a year (S5's floor; review r1 B1: a typed 0 crashed the annuity).
            if p.overnight_cost is None or not _finite(life) or life < 1.0:
                reasons.append(f"terminal_part_unknown:{a.name}:{p.name}")
                continue
            start: float = tl.cod_year
            base = p.overnight_cost
            bought = None                                   # (year, unescalated amount)
            if fin.replacement_rule == PART_LIFETIMES:
                ks = _repurchases(life, tl.analysis_years)
                if ks:
                    k = ks[-1]
                    start = tl.cod_year + k * life          # unrounded (GS's last_buy)
                    bought = (replacement_year(tl.cod_year, k, life), p.overnight_cost)
            else:
                mine = [(y, x) for y, x in entries.get(a.name, ()) if on_axis(tl, y)]
                if mine:
                    last = max(y for y, _x in mine)
                    start = last + 1
                    bought = (last, sum(x for y, x in mine if y == last))
            if bought is not None:
                if r_capex is None:
                    reasons.append("escalation_missing:capex")
                    continue
                base = bought[1] * (1.0 + r_capex) ** (bought[0] - tl.base_year)
            remaining = start + life - horizon
            ann, pvf = annuity(r_a, life), annuity_pv_factor(r, remaining)
            terms.append(TerminalTerm(a.name, p.name, float(life), start, float(remaining),
                                      float(base), r_a, r, ann, pvf, float(base) * ann * pvf))
    return tuple(terms), sorted(set(reasons))


__all__ = ["Replacement", "schedule", "notes", "check", "counts_as_replaced", "governed",
           "replacement_year", "on_axis", "FIXED", "PART_LIFETIMES", "TerminalTerm",
           "remaining_life_terms", "annuity", "annuity_pv_factor"]
