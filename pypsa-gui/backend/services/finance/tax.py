"""
Corporate tax and depreciation (IC P4 plan WP4.3a; C6, C7, C12).

Tax is an ordered list of LAYERS (SAM: state, then federal with the state tax
deductible; Germany: GewSt and KSt, GewSt not deductible, with SolZ on KSt).
Each layer has its own depreciation profile (SAM's federal and state
depreciation differ — bonus is commonly federal only), its own rate (constant
or a per-tax-year schedule, plan C11), its own base adjustments (the German
GewSt add-back) and its own loss pool.

Loss treatment (plan C7): `offset_other_income` — the owner uses a loss at once,
a negative liability is a benefit (SAM Single Owner); `carryforward` — losses
carry into later years under the layer's loss rule (a limit share above an
allowance, e.g. the German Mindestbesteuerung or the US 80 % NOL limit).

Signs: `liability[y]` > 0 is tax owed; the cash effect is −liability.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from services.finance.timeline import Timeline

# ── depreciation schedules (fractions of basis per operating year, k = 1 …) ──


def sl_half_year(years: int) -> tuple[float, ...]:
    """Straight-line with the half-year convention (SAM's SL-n: ½, 1 … 1, ½ of
    1/n — n + 1 years; verified on the S1 oracle)."""
    if years < 1:
        raise ValueError("straight-line needs years ≥ 1")
    return tuple([0.5 / years] + [1.0 / years] * (years - 1) + [0.5 / years])


def sl_pro_rata(years: int, months_first_year: int = 12) -> tuple[float, ...]:
    """Straight-line with the first year pro rata by month (§7 Abs. 1 EStG):
    `months_first_year`/12 of a year's share in year 1, the remainder in year
    n + 1."""
    if not 1 <= months_first_year <= 12:
        raise ValueError("months_first_year must be 1..12")
    first = months_first_year / 12.0 / years
    out = [first] + [1.0 / years] * (years - 1)
    rest = 1.0 - sum(out)
    if rest > 1e-15:
        out.append(rest)
    return tuple(out)


def declining_balance(rate: float, years: int, months_first_year: int = 12) -> tuple[float, ...]:
    """Declining balance at `rate` per year with the switch to straight-line on
    the remaining life when that is larger (the German degressive AfA's
    `Wechsel`, §7 Abs. 3 EStG). The year of acquisition is pro rata by month
    (§7 Abs. 2 Satz 3 → Abs. 1 Satz 4 EStG — WP4.3a review B1): year 1 takes
    `months_first_year`/12 of a year; the life of `years` × 12 months then
    ends in year n + 1 when the first year is short."""
    if years < 1 or not 0.0 < rate <= 1.0 or not 1 <= months_first_year <= 12:
        raise ValueError(f"declining balance needs years ≥ 1, 0 < rate ≤ 1, 1..12 months "
                         f"(got {rate!r}, {years!r}, {months_first_year!r})")
    remaining, out = 1.0, []
    life_left = years * 12.0                     # months
    months = float(months_first_year)
    while remaining > 1e-12 and life_left > 1e-9:
        frac = months / 12.0
        sl = remaining * min(1.0, months / life_left)
        d = min(remaining, max(remaining * rate * frac, sl))
        out.append(d)
        remaining -= d
        life_left -= months
        months = min(12.0, life_left)
    if remaining > 1e-12:
        out[-1] += remaining
    return tuple(out)


def normalised(schedule, tol: float = 1e-9) -> tuple[float, ...]:
    """A schedule checked: non-empty, every entry ≥ 0, summing to 1 within
    `tol` (WP4.3a review B7)."""
    s = tuple(float(x) for x in schedule)
    if not s or any(x < 0 for x in s):
        raise ValueError(f"a depreciation schedule needs entries ≥ 0 (got {s[:4]!r}…)")
    if abs(sum(s) - 1.0) > tol:
        raise ValueError(f"a depreciation schedule must sum to 1 (sums to {sum(s)!r})")
    return s


@dataclass(frozen=True)
class DepreciationClass:
    """A share of the depreciable basis on a schedule, with a first-year bonus
    share (the remainder follows the schedule) and whether an ITC basis
    reduction applies to it (plan WP4.4)."""

    name: str
    share: float
    schedule: tuple[float, ...]
    bonus: float = 0.0
    itc_reduces: bool = True


@dataclass(frozen=True)
class LossRule:
    """Carryforward: a loss offsets later income up to `allowance` in full
    plus `limit_share` of the income above it (`limit_share=1`, allowance 0 =
    unlimited; a `{from_year: share}` schedule is read per tax year). `years`
    None = indefinite."""

    allowance: float = 0.0
    limit_share: float | dict[int, float] = 1.0
    years: int | None = None


@dataclass(frozen=True)
class InterestCap:
    """A cap on deductible net interest at `share` of the layer's tax EBITDA
    (§163(j): ATI on an EBITDA basis; §4h EStG: steuerliches EBITDA). With a
    `freigrenze` the cap applies only when net interest exceeds it — and then
    to ALL net interest (a Freigrenze, not an allowance). Disallowed interest
    carries forward when `carryforward` (§163(j)(2); the German Zinsvortrag,
    §4h Abs. 1 Satz 5 EStG). The cap applies from the Freigrenze on (§4h Abs. 2
    Satz 1 Buchst. a: "weniger als drei Millionen Euro" is exempt — review B4c).
    `simplified_flag` is raised whenever the claim reaches the Freigrenze (the
    German EBITDA-Vortrag and the escape / stand-alone clauses are not
    modelled). Applied only in `carryforward` mode (plan C7)."""

    share: float
    freigrenze: float | None = None
    carryforward: bool = True
    simplified_flag: str | None = None


@dataclass(frozen=True)
class TaxLayer:
    name: str
    rate: float | dict[int, float]                  # constant, or {from_year: rate} schedule
    depreciation: tuple[DepreciationClass, ...]
    deductible_in_later_layers: bool = True
    loss: LossRule = field(default_factory=LossRule)
    # A share of interest added back to this layer's base above an allowance
    # (the German GewSt §8 Nr. 1: 25 % above €200k). None = no add-back.
    interest_addback_share: float | None = None
    interest_addback_allowance: float = 0.0
    # A surcharge on this layer's liability folded into it (SolZ: 5.5 % of KSt).
    surcharge_share: float = 0.0
    itc_basis_reduction: bool = False               # this layer's basis is reduced by ½ ITC
    interest_cap: InterestCap | None = None


@dataclass
class TaxResult:
    depreciation: dict[str, np.ndarray]             # per layer
    taxable: dict[str, np.ndarray]
    liability: dict[str, np.ndarray]                # tax owed (> 0) per layer
    loss_pool: dict[str, np.ndarray]                # carried loss at year end (carryforward)
    total_liability: np.ndarray
    # The basis not yet depreciated at the end of the axis, per layer (after
    # the ITC reduction and bonus) — WP4.5's `book_value` terminal reads it.
    remaining_basis: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def rate_in(rate: float | dict[int, float], year: int) -> float:
    if not isinstance(rate, dict):
        return float(rate)
    keys = sorted(k for k in rate if k <= year)
    if not keys:
        raise ValueError(f"no rate in the schedule for {year}")
    return float(rate[keys[-1]])


def depreciation(tl: Timeline, basis: float, classes: tuple[DepreciationClass, ...], *,
                 itc_reduction: float = 0.0) -> np.ndarray:
    """Depreciation per year of `basis` (the depreciable capex) across the
    classes, from the first operating year; `itc_reduction` (an amount) is
    taken off the basis of the classes it applies to, pro rata to their shares."""
    out = np.zeros(tl.n)
    start = tl.index(tl.cod_year)
    if sum(c.share for c in classes) > 1.0 + 1e-9:
        raise ValueError("depreciation class shares sum above 1")
    reducible = sum(c.share for c in classes if c.itc_reduces)
    for c in classes:
        b = basis * c.share
        if itc_reduction and c.itc_reduces and reducible:
            b -= itc_reduction * c.share / reducible
        if b < -1e-9:
            raise ValueError(f"the ITC basis reduction exceeds class {c.name!r}'s basis")
        bonus = b * c.bonus
        rest = b - bonus
        if start < tl.n:
            out[start] += bonus
        for j, f in enumerate(c.schedule):
            i = start + j
            if i < tl.n:
                out[i] += rest * f
    return out


def compute_tax(tl: Timeline, layers: tuple[TaxLayer, ...], *, ebitda: np.ndarray,
                basis: float, interest: np.ndarray | None = None,
                other_income: np.ndarray | None = None,
                other_deductions: np.ndarray | None = None,
                itc_amount: float = 0.0, losses: str) -> TaxResult:
    """Tax per layer (plan C7). The base of every layer is
    EBITDA − its depreciation − interest + `other_income` (reserve interest) −
    `other_deductions` (amortised fees) − the liabilities of earlier layers
    marked deductible; `losses` is `offset_other_income` or `carryforward`."""
    if losses not in ("offset_other_income", "carryforward"):
        raise ValueError("losses must be offset_other_income or carryforward")
    interest = np.zeros(tl.n) if interest is None else interest
    other_income = np.zeros(tl.n) if other_income is None else other_income
    other_deductions = np.zeros(tl.n) if other_deductions is None else other_deductions
    dep, taxable, liab, pools = {}, {}, {}, {}
    flags: list[str] = []
    deductible_before = np.zeros(tl.n)
    for layer in layers:
        d = depreciation(tl, basis, layer.depreciation,
                         itc_reduction=0.5 * itc_amount if layer.itc_basis_reduction else 0.0)
        deductible_interest = interest
        if layer.interest_cap is not None and losses == "carryforward":
            deductible_interest, capped, reached = _capped_interest(layer.interest_cap, ebitda,
                                                                    interest)
            if capped:
                flags.append(f"interest_capped:{layer.name}")
            if reached and layer.interest_cap.simplified_flag:
                flags.append(layer.interest_cap.simplified_flag)
        base = (ebitda - d - deductible_interest + other_income - other_deductions
                - deductible_before)
        if layer.interest_addback_share is not None:
            # Only interest deducted in the profit is added back (§8 GewStG
            # chapeau — WP4.3a review B5).
            base = base + layer.interest_addback_share * np.clip(
                deductible_interest - layer.interest_addback_allowance, 0.0, None)
        pool = np.zeros(tl.n)
        if losses == "offset_other_income":
            use = base.copy()
        else:
            use = np.zeros(tl.n)
            carried: list[list] = []          # [amount, year_index] per vintage, FIFO
            for i in range(tl.n):
                income = base[i]
                if income < 0:
                    carried.append([-income, i])
                    use[i] = 0.0
                else:
                    # Expire vintages older than the rule allows.
                    if layer.loss.years is not None:
                        carried = [v for v in carried if i - v[1] <= layer.loss.years]
                    share = rate_in(layer.loss.limit_share, int(tl.years[i]))
                    cap = min(income, layer.loss.allowance) + share * max(
                        0.0, income - layer.loss.allowance)
                    offset = 0.0
                    for v in carried:
                        take = min(v[0], cap - offset)
                        if take <= 0:
                            break
                        v[0] -= take
                        offset += take
                    carried = [v for v in carried if v[0] > 1e-9]
                    use[i] = income - offset
                pool[i] = sum(v[0] for v in carried)
        rate = np.array([rate_in(layer.rate, int(y)) for y in tl.years])
        lb = use * rate * (1.0 + layer.surcharge_share)
        dep[layer.name], taxable[layer.name], liab[layer.name] = d, base, lb
        pools[layer.name] = pool
        if layer.deductible_in_later_layers:
            deductible_before = deductible_before + lb
    total = np.sum(list(liab.values()), axis=0) if liab else np.zeros(tl.n)
    remaining = {}
    for layer in layers:
        red = 0.5 * itc_amount if layer.itc_basis_reduction else 0.0
        reducible = any(c.itc_reduces for c in layer.depreciation)
        depreciable = basis * sum(c.share for c in layer.depreciation) - (red if reducible else 0.0)
        remaining[layer.name] = float(depreciable - dep[layer.name].sum())
    return TaxResult(depreciation=dep, taxable=taxable, liability=liab, loss_pool=pools,
                     total_liability=total, remaining_basis=remaining, flags=sorted(set(flags)))


def _capped_interest(cap: InterestCap, ebitda: np.ndarray,
                     interest: np.ndarray) -> tuple[np.ndarray, bool, bool]:
    """Deductible interest per year under the cap, with the disallowed part
    carried forward (FIFO into later years' headroom) when allowed. Returns
    (deductible, capped in some year, the Freigrenze reached in some year)."""
    out = np.zeros(len(interest))
    carried, capped, reached = 0.0, False, False
    for i, (e, x) in enumerate(zip(ebitda, interest)):
        claim = x + carried
        if cap.freigrenze is not None:
            if claim < cap.freigrenze:
                out[i], carried = claim, 0.0
                continue
            reached = True
        limit = max(0.0, cap.share * e)
        allowed = min(claim, limit)
        if allowed < claim - 1e-9:
            capped = True
        out[i] = allowed
        carried = (claim - allowed) if cap.carryforward else 0.0
    return out, capped, reached
