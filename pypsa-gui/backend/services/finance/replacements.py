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

A campus lineage (campus-IC 04: an extra asset stating `service_to`) is the
grid study's to replace, under both rules: each `CampusRenewal` is bought in
`build_year − stage_capex_lead_years` (`source="campus_study"`, the rule of a
stage's capex, so an existing asset's first renewal, a stage, and the renewals
folded into it agree for any lead); one at or after the horizon is dropped and
flagged `campus_renewal_after_horizon:<asset>:<year>`; one paid before the
asset's COD year is refused `campus_renewal_before_cod:<asset>:<year>`; a
`replacement_capex` entry naming it is refused `replacement_rule_conflict:<asset>`.
When the life of a part's last purchase (its build year, or the asset's COD for
the first, + L) ends inside the horizon, the asset is flagged
`campus_life_ends_unreplaced:<asset>`; under `fixed` nothing more is bought,
under `part_lifetimes` the IC's own rule resumes there: a purchase serving from
each `end + j·L` before the horizon, paid in its first service year (half up)
less the lead. The study's `service_to` of a purchase with no successor is its
horizon's end, not an end of life (one year for a single-period project), so
the lifetime, not `service_to`, says when a life ends.

Pure: reads the `FinanceCase` and its `Timeline`, never a network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from services.finance.case import (
    AssetFinance, AssetPart, FinanceCase, FinanceRefused, cod_year_of, effective_parts,
)

if TYPE_CHECKING:
    from services.finance.case import ExtraOwnerAsset
    from services.finance.timeline import Timeline

FIXED = "fixed"
PART_LIFETIMES = "part_lifetimes"
CAMPUS = "campus_study"


@dataclass(frozen=True)
class Replacement:
    """One replacement purchase (S4): in `year` (calendar), of `asset`'s `part`
    (None for a `fixed` entry), `amount` unescalated (the base year's money);
    `source` is `"fixed"`, `"part_lifetimes"` or `"campus_study"` (a campus
    renewal, campus-IC 04)."""

    year: int
    asset: str
    part: str | None
    amount: float
    source: str


def _finite(life: float | None) -> bool:
    return life is not None and math.isfinite(life)


def _clock(case: FinanceCase, tl: Timeline, name: str) -> tuple[int, int]:
    """(COD year, operating years on the axis) of owner asset `name`: a later
    stage's clock starts at its own COD (P5)."""
    cod = cod_year_of(case, name, tl.cod_year)
    return cod, tl.analysis_years - (cod - tl.cod_year)


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


def lineages(case: FinanceCase) -> dict[str, ExtraOwnerAsset]:
    """The campus lineages of the case by asset name (campus-IC 04): the extra
    assets that state a `service_to`. An extra without one is an asset like any
    other (the case before 04)."""
    return {e.name: e for e in case.extra_assets if e.service_to is not None}


def _horizon(tl: Timeline) -> int:
    return tl.cod_year + tl.analysis_years


def _resumed(case: FinanceCase, tl: Timeline, e: ExtraOwnerAsset, a: AssetFinance,
             p: AssetPart) -> list[float]:
    """The first service years of the purchases after the life of the part's last
    campus purchase ends, before the horizon: `end + j·L`, j ≥ 0, `end` = that
    purchase's build year (the asset's COD year for the first) + L. Empty when
    that life outlives the axis."""
    if not _finite(p.lifetime_years):
        return []
    years = [r.build_year for r in e.renewals if r.part == p.name]
    end = (years[-1] if years else cod_year_of(case, a.name, tl.cod_year)) + p.lifetime_years
    out, j = [], 0
    while end + j * p.lifetime_years < _horizon(tl):
        out.append(end + j * p.lifetime_years)
        j += 1
    return out


def _paid(case: FinanceCase, first_service_year: float) -> int:
    """The cash year of a purchase serving from `first_service_year` (half up):
    that year less `stage_capex_lead_years`, the rule of a stage's capex (P5)."""
    return math.floor(first_service_year + 0.5) - case.inputs.stage_capex_lead_years


def _campus(case: FinanceCase, tl: Timeline) -> list[tuple[tuple, Replacement]]:
    """(sort key, purchase) for each campus renewal on the axis, and under
    `part_lifetimes` each resumed purchase; refuses `campus_renewal_before_cod`."""
    out = []
    by = lineages(case)
    for ai, a in enumerate(case.assets):
        e = by.get(a.name)
        if e is None:
            continue
        cod = cod_year_of(case, a.name, tl.cod_year)
        for pi, p in enumerate(effective_parts(a)):
            for r in e.renewals:
                if r.part != p.name or r.build_year >= _horizon(tl):
                    continue
                year = _paid(case, r.build_year)
                if year < cod:
                    raise FinanceRefused(f"campus_renewal_before_cod:{a.name}:{r.build_year}",
                                         f"a renewal built in {r.build_year} is paid in {year}, "
                                         f"before the asset's COD year {cod}")
                out.append(((year, ai, pi), Replacement(year, a.name, p.name, r.amount, CAMPUS)))
            if case.inputs.replacement_rule == PART_LIFETIMES and p.overnight_cost is not None:
                for start in _resumed(case, tl, e, a, p):
                    year = _paid(case, start)
                    out.append(((year, ai, pi), Replacement(year, a.name, p.name,
                                                            p.overnight_cost, PART_LIFETIMES)))
    return out


def governed(a: AssetFinance) -> bool:
    """Whether `part_lifetimes` replaces this asset: some effective part has a
    finite lifetime (S5)."""
    return any(_finite(p.lifetime_years) for p in effective_parts(a))


def check(case: FinanceCase) -> None:
    """The refusals of S5 under `part_lifetimes` (`build_timeline` runs it):
    `replacement_rule_conflict:<asset>`, `part_lifetime_too_short:<asset>:<part>`."""
    campus = lineages(case)
    for year, asset, _amount in case.inputs.replacement_capex:
        if asset in campus:
            raise FinanceRefused(f"replacement_rule_conflict:{asset}",
                                 f"a replacement_capex entry ({year}) for {asset}: the grid "
                                 "study already replaces it (double counting)")
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


def _generated(case: FinanceCase, tl: Timeline) -> list[tuple[tuple, Replacement]]:
    """(sort key, the replacement) for every `part_lifetimes` purchase of an
    asset that is no campus lineage."""
    out = []
    campus = lineages(case)
    for ai, a in enumerate(case.assets):
        if a.name in campus:
            continue                    # the grid study's, then `_campus` (campus-IC 04)
        cod, years = _clock(case, tl, a.name)
        for pi, p in enumerate(effective_parts(a)):
            if p.overnight_cost is None or not _finite(p.lifetime_years):
                continue
            for k in _repurchases(p.lifetime_years, years):
                out.append(((replacement_year(cod, k, p.lifetime_years), ai, pi),
                            Replacement(replacement_year(cod, k, p.lifetime_years),
                                        a.name, p.name, p.overnight_cost, PART_LIFETIMES)))
    return out


def schedule(case: FinanceCase, tl: Timeline) -> tuple[Replacement, ...]:
    """Every replacement of the case (S4), unescalated: the `replacement_capex`
    entries (`fixed`), then by year the campus renewals (both rules) and under
    `part_lifetimes` the generated purchases. Refuses as `check` does, and
    `campus_renewal_before_cod:<asset>:<year>`."""
    check(case)
    fixed = tuple(Replacement(int(y), asset, None, float(amount), FIXED)
                  for y, asset, amount in case.inputs.replacement_capex)
    campus = _campus(case, tl)
    if case.inputs.replacement_rule != PART_LIFETIMES:
        return fixed + tuple(r for _key, r in sorted(campus, key=lambda x: x[0]))
    return fixed + tuple(r for _key, r in sorted(campus + _generated(case, tl),
                                                 key=lambda x: x[0]))


def notes(case: FinanceCase, tl: Timeline) -> tuple[list[str], list[str]]:
    """(capex reasons, flags) of the schedule (S5): `part_lifetime_missing:<a>:<p>`
    (not established); `part_lifetime_rounded:<a>:<p>`, `part_not_replaced:<a>:<p>`;
    for a campus lineage (campus-IC 04), under both rules,
    `campus_renewal_after_horizon:<a>:<year>` and `campus_life_ends_unreplaced:<a>`."""
    reasons: list[str] = []
    flags: list[str] = []
    campus = lineages(case)
    for a in case.assets:
        e = campus.get(a.name)
        if e is None:
            continue
        flags += sorted({f"campus_renewal_after_horizon:{a.name}:{r.build_year}"
                         for r in e.renewals if r.build_year >= _horizon(tl)})
        if any(_resumed(case, tl, e, a, p) for p in effective_parts(a)):
            flags.append(f"campus_life_ends_unreplaced:{a.name}")
    if case.inputs.replacement_rule == PART_LIFETIMES:
        for a in case.assets:
            _cod, years = _clock(case, tl, a.name)
            for p in effective_parts(a):
                if p.overnight_cost is None:
                    continue                    # the capex already reads overnight_cost_missing
                if p.lifetime_years is None:
                    reasons.append(f"part_lifetime_missing:{a.name}:{p.name}")
                elif math.isfinite(p.lifetime_years) and not float(p.lifetime_years).is_integer() \
                        and (_resumed(case, tl, campus[a.name], a, p) if a.name in campus
                             else _repurchases(p.lifetime_years, years)):
                    flags.append(f"part_lifetime_rounded:{a.name}:{p.name}")
        return reasons, flags
    named = {asset for _y, asset, _x in case.inputs.replacement_capex} | set(campus)
    for a in case.assets:
        if a.name in named:
            continue
        _cod, years = _clock(case, tl, a.name)
        for p in effective_parts(a):
            if _finite(p.lifetime_years) and p.lifetime_years < years:
                flags.append(f"part_not_replaced:{a.name}:{p.name}")
    return reasons, flags


def counts_as_replaced(case: FinanceCase, a: AssetFinance,
                       replacements: tuple[Replacement, ...]) -> bool:
    """For the `asset_lifetime_short` check (S5): a `fixed` replacement names
    the asset, or under `part_lifetimes` every effective part has a known
    lifetime (a finite one is scheduled, an infinite one outlives the axis), or
    a campus renewal replaces it (campus-IC 04)."""
    if any(r.asset == a.name and r.source in (FIXED, CAMPUS) for r in replacements):
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
    rate, else the LP's; `r` the LP's (GS's; the LP basis, not the WACC). A part
    typed at an overnight cost of 0 with no `fixed` entry (a sunk, existing
    asset) has no term and no reason, whatever its lifetime (slice D4).

    A campus lineage (campus-IC 04), under both rules: the IC's own last purchase
    when its rule resumed (`part_lifetimes`), else the part's last renewal on the
    axis (start = its build year, its first service year; base = its amount
    escalated to its cash year), else the first purchase as for any asset. A part
    without a renewal in a lineage that renews another part is
    `terminal_part_unknown:<asset>:<part>`."""
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
    campus = lineages(case)
    for a in case.assets:
        parts = effective_parts(a)
        if fin.replacement_rule == FIXED and len(parts) > 1 and a.name in entries:
            reasons.append(f"terminal_needs_part_lifetimes:{a.name}")
            continue
        own = lp.asset_discount_rates.get(a.name)
        r_a = r if own is None else float(own)
        for p in parts:
            life = p.lifetime_years
            if p.overnight_cost == 0.0 and not (fin.replacement_rule == FIXED and a.name in entries):
                # A typed 0 (a sunk, existing asset, slice D4): nothing was bought,
                # so nothing remains to credit, whatever the lifetime.
                continue
            # No cost, no lifetime, an infinite one (GS's `salvage_not_computed`) or
            # one below a year (S5's floor; review r1 B1: a typed 0 crashed the annuity).
            if p.overnight_cost is None or not _finite(life) or life < 1.0:
                reasons.append(f"terminal_part_unknown:{a.name}:{p.name}")
                continue
            cod, years = _clock(case, tl, a.name)
            start: float = cod
            base = p.overnight_cost
            bought = None                                   # (year, unescalated amount)
            if cod > tl.cod_year:
                # A later stage's first purchase is paid in its stage-capex
                # year, escalated like the stage capex (P5; no contingency, as
                # a replacement's base).
                bought = (cod - fin.stage_capex_lead_years, p.overnight_cost)
            e = campus.get(a.name)
            if e is not None:
                mine = [r for r in e.renewals if r.part == p.name]
                if e.renewals and not mine:
                    reasons.append(f"terminal_part_unknown:{a.name}:{p.name}")
                    continue
                resumed = _resumed(case, tl, e, a, p) \
                    if fin.replacement_rule == PART_LIFETIMES else []
                alive = [r for r in mine if r.build_year < horizon]
                if resumed:
                    start = resumed[-1]
                    bought = (_paid(case, start), p.overnight_cost)
                elif alive:
                    start = alive[-1].build_year
                    bought = (_paid(case, start), alive[-1].amount)
            elif fin.replacement_rule == PART_LIFETIMES:
                ks = _repurchases(life, years)
                if ks:
                    k = ks[-1]
                    start = cod + k * life                  # unrounded (GS's last_buy)
                    bought = (replacement_year(cod, k, life), p.overnight_cost)
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
           "lineages", "CAMPUS",
           "replacement_year", "on_axis", "FIXED", "PART_LIFETIMES", "TerminalTerm",
           "remaining_life_terms", "annuity", "annuity_pv_factor"]
