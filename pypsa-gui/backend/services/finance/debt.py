"""
Debt (IC P4 plan WP4.2a / WP4.2b; C6, C8, C12).

Every tranche is sized to its debt AT COD (capitalised IDC included):
`amount`; `gearing` × installed capex incl. contingency (`gearing_base=
"capex"`, SAM's `debt_percent` base) or × total uses (capex + IDC + fees +
DSRA — the fixed point, `"total_uses"`); or DSCR sculpting in closed form on
the CFADS left after the tranches before it (senior first), capped by
`max_gearing` × capex × (1 + upfront fee) (SAM `dscr_maximum_debt_fraction`).

Timing on the finance axis (end-of-year cash points, index 0 undiscounted —
the same points the returns discount; a stated amendment to the plan's
"mid-year drawdown"): construction year j's capex is paid, and the debt drawn,
at point j; interest compounds on each draw to the COD point p (the last
construction point; SAM's year 0 → no IDC, as SAM has none) at the tranche's
FIRST rate (a per-year rate list is the tenor's; flagged `idc_at_first_rate`
when a list accrues IDC — review B3). Flagged `idc_axis_point_draws`: the usual
mid-year approximation would accrue more. Operating year k's
service falls at the operating point of year k; balance before the first
service = the debt at COD. With COD in the financial-close year (no
construction year) point 0 is both the draw and the first service (flagged
`first_service_at_draw_point`: the lender's year is compressed, as on the axis).

Debt-funded draws follow capex phasing (`amount`, `gearing` on capex, a
sculpted tranche — their fees and DSRA are equity-funded, SAM) or the timing of
every cash use (`total_uses`). The upfront fee is a share of the debt at COD,
paid at close; the commitment fee accrues on the undrawn commitment between
construction points. Repayment inside the tenor from COD: interest-only grace
years first (SAM `loan_moratorium`; refused under sculpting — SAM ignores it,
plan C8), then `annuity` (re-amortised each year at that year's rate),
`level` principal, or the sculpted service.

CFADS (plan C8) = operating revenue − operating costs − replacement capex
(the major-equipment spend, taken as it occurs; P4 has no equipment
reserve) — never the terminal value, so an unknown terminal (e.g. a book value
awaiting the tax basis) never blocks the debt (WP4.2 review B1). SAM sculpts on
CFADS INCLUDING salvage when the tenor reaches the last year — a recorded
deviation (oracle S2t). DSRA movements and reserve interest sit below it. When
CFADS is unknown an amount / gearing schedule stays established and the DSCR is
flagged `dscr_not_established:<reason>`. DSRA balance at point t =
Σ_tranches dsra_months/12 × the tranche's service at t + 1, funded at the COD
point, adjusted yearly, released at maturity; reserve interest at t =
`reserves_rate` × the balance at t − 1 (taxable).

A missing input (a fee, DSRA months, grace years, `reserves_rate` when a DSRA
is held, CFADS for sculpting) makes the section not established with the
reason — nothing is assumed (plan C12).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from models.finance import DebtTranche, FinanceInputs
from services.finance.cashflow import Operating
from services.finance.timeline import Timeline

TOL = 1e-6              # the fixed point's relative tolerance (plan C8)
MAX_ITER = 50
# The one CFADS definition the report and the workbook disclose (WP4.6b review
# B6) — it is what `build_debt` computes from the incremental operating cash.
CFADS_DEFINITION = ("CFADS = incremental operating revenue − incremental operating costs − "
                    "replacement capex, where incremental = the owner's total operating cash "
                    "− the counterfactual's (plan C13); the terminal value excluded; DSRA "
                    "movements and reserve interest sit below it (plan C8)")


@dataclass
class TrancheResult:
    index: int
    tranche: DebtTranche
    amount: float                      # debt at COD, IDC included
    principal_drawn: float             # Σ cash draws (the commitment)
    draws: np.ndarray
    idc: np.ndarray                    # capitalised interest per point
    upfront_fee: np.ndarray
    commitment_fee: np.ndarray
    balance: np.ndarray                # at each point, after that point's service
    interest: np.ndarray
    principal: np.ndarray
    dsra_target: np.ndarray            # this tranche's DSRA balance per point
    flags: list[str] = field(default_factory=list)

    @property
    def service(self) -> np.ndarray:
        return self.interest + self.principal


@dataclass
class Debt:
    tl: Timeline
    tranches: list[TrancheResult]
    cfads: np.ndarray | None
    interest: np.ndarray
    principal: np.ndarray
    service: np.ndarray
    draws: np.ndarray
    idc: np.ndarray
    fees: np.ndarray                   # upfront + commitment, per point (cash)
    dsra_balance: np.ndarray
    dsra_funding: np.ndarray           # + = cash into the reserve, − = release
    reserve_interest: np.ndarray
    dscr: np.ndarray                   # CFADS / total service (NaN: no service or no CFADS)
    dscr_senior: np.ndarray
    sources: dict[str, float]
    uses: dict[str, float]
    iterations: int = 0
    residual: float | None = None
    status: str = "ok"
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def established(self) -> bool:
        return self.status == "ok"

    @property
    def amount(self) -> float:
        return float(sum(t.amount for t in self.tranches))

    @property
    def idc_total(self) -> float:
        return float(self.idc.sum())

    def min_dscr(self, senior: bool = False) -> float | None:
        d = self.dscr_senior if senior else self.dscr
        d = d[~np.isnan(d)]
        return float(d.min()) if len(d) else None


def _rates(t: DebtTranche) -> list[float]:
    return list(t.rate) if isinstance(t.rate, list) else [float(t.rate)] * t.tenor_years


def _points(tl: Timeline) -> tuple[list[int], int, int]:
    """(construction points, COD point, first operating index)."""
    c = tl.index(tl.cod_year)
    return (list(range(c)) if c else [0]), max(c - 1, 0), c


def _check(fin: FinanceInputs, tl: Timeline) -> list[str]:
    reasons: list[str] = []
    _, p, c = _points(tl)
    held = False
    for i, t in enumerate(fin.debt):
        tag = f"{i}:{t.kind}"
        if t.upfront_fee is None:
            reasons.append(f"input_missing:upfront_fee:{tag}")
        if t.dsra_months is None:
            reasons.append(f"input_missing:dsra_months:{tag}")
        elif t.dsra_months > 0:
            held = True
        if t.grace_years is None:
            reasons.append(f"input_missing:grace_years:{tag}")
        elif t.grace_years > 0 and t.sculpting == "dscr_target":
            reasons.append(f"grace_with_sculpting:{tag}")
        elif t.grace_years >= t.tenor_years:
            reasons.append(f"grace_not_inside_tenor:{tag}")
        if p > 0 and t.commitment_fee is None:
            reasons.append(f"input_missing:commitment_fee:{tag}")
        if isinstance(t.rate, list) and len(t.rate) != t.tenor_years:
            reasons.append(f"rate_list_length:{tag}:{len(t.rate)}!={t.tenor_years}")
        if t.tenor_years > tl.analysis_years:
            reasons.append(f"debt_tenor_beyond_analysis:{tag}")
    if held and fin.reserves_rate is None:
        reasons.append("input_missing:reserves_rate")
    return reasons


def _schedule(t: DebtTranche, amount: float, tl: Timeline, sculpt_service: np.ndarray | None) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Interest, principal and balance over the axis for a debt `amount` at COD."""
    n, (_, _, c) = tl.n, _points(tl)
    rates = _rates(t)
    interest, principal, balance = np.zeros(n), np.zeros(n), np.zeros(n)
    flags: list[str] = []
    bal = amount
    grace = t.grace_years or 0
    for m in range(t.tenor_years):
        i, r = c + m, rates[m]
        it = bal * r
        if t.sculpting == "dscr_target":
            pr = sculpt_service[i] - it
        elif m < grace:
            pr = 0.0
        elif t.sculpting == "level":
            pr = amount / (t.tenor_years - grace)
        else:                                    # annuity on the remaining balance
            left = t.tenor_years - m
            pr = (bal * r / (1.0 - (1.0 + r) ** -left) if r > 0 else bal / left) - it
        if m == t.tenor_years - 1:
            pr = bal                              # close the loan exactly
        if t.sculpting == "dscr_target" and m < t.tenor_years - 1 and pr < -1e-9 * max(amount, 1.0):
            flags.append(f"sculpted_interest_capitalised:{tl.years[i]}")
        interest[i], principal[i] = it, pr
        bal -= pr
        balance[i] = bal
    return interest, principal, balance, flags


def _sculpt(t: DebtTranche, avail: np.ndarray, tl: Timeline) -> tuple[float, np.ndarray, list[str]]:
    """Closed form (plan C8): service_t = max(avail_t, 0) / DSCR; debt = PV at
    the tranche's rates. Negative-CFADS years pay nothing (flagged)."""
    _, _, c = _points(tl)
    rates = _rates(t)
    service = np.zeros(tl.n)
    flags: list[str] = []
    amount, disc = 0.0, 1.0
    for m in range(t.tenor_years):
        i = c + m
        disc /= 1.0 + rates[m]
        a = avail[i]
        if a < 0:
            # CFADS left for this tranche (after the senior ones) is negative:
            # no service (SAM books a negative one — a recorded deviation).
            flags.append(f"sculpt_basis_negative_no_service:{tl.years[i]}")
        service[i] = max(a, 0.0) / t.dscr_target
        amount += service[i] * disc
    return amount, service, flags


def build_debt(fin: FinanceInputs, op: Operating, tl: Timeline) -> Debt:
    """The debt schedule of every tranche, the DSRA, fees, IDC, CFADS and DSCR
    (plan WP4.2a/b). Not established → zeros with the reasons."""
    n = tl.n
    cons, p, c = _points(tl)
    zeros = np.zeros(n)
    cfads = None
    if op.revenue is not None and op.costs is not None:
        cfads = op.revenue - op.costs - op.replacement

    def empty(reasons, flags=(), iterations=0, residual=None) -> Debt:
        nan = np.full(n, np.nan)
        return Debt(tl=tl, tranches=[], cfads=cfads, interest=zeros.copy(),
                    principal=zeros.copy(), service=zeros.copy(), draws=zeros.copy(),
                    idc=zeros.copy(), fees=zeros.copy(), dsra_balance=zeros.copy(),
                    dsra_funding=zeros.copy(), reserve_interest=zeros.copy(), dscr=nan,
                    dscr_senior=nan.copy(), sources={}, uses={}, iterations=iterations,
                    residual=residual, status="not_established" if reasons else "ok",
                    reasons=sorted(set(reasons)), flags=list(flags))

    if not fin.debt:
        d = empty([])
        if op.capex is not None:
            total = float(op.capex.sum())
            d.uses, d.sources = {"capex": total}, {"debt": 0.0, "equity": total}
        return d
    reasons = _check(fin, tl)
    if op.capex is None:
        reasons.append("capex_not_established")
    if cfads is None and any(t.sculpting == "dscr_target" for t in fin.debt):
        reasons.append("cfads_not_established")
    if reasons:
        return empty(reasons)
    capex = op.capex
    capex_total = float(capex.sum())
    rr = fin.reserves_rate or 0.0
    phasing = np.zeros(n)
    phasing[cons] = capex[cons] / capex_total if capex_total else 1.0 / len(cons)

    def one_pass(uses_est: float, weights: np.ndarray):
        results: list[TrancheResult] = []
        avail = cfads.copy() if cfads is not None else None
        for i, t in enumerate(fin.debt):
            flags: list[str] = []
            sculpt_service = None
            if t.sculpting == "dscr_target":
                amount, sculpt_service, flags = _sculpt(t, avail, tl)
                if t.max_gearing is not None:
                    cap = t.max_gearing * capex_total * (1.0 + t.upfront_fee)
                    if amount > cap:
                        sculpt_service = sculpt_service * (cap / amount)
                        amount = cap
                        flags.append(f"max_gearing_binding:{i}")
            elif t.amount is not None:
                amount = float(t.amount)
            elif t.gearing_base == "capex":
                amount = t.gearing * capex_total
            else:
                amount = t.gearing * uses_est
            w = weights if t.gearing_base == "total_uses" and t.gearing is not None else phasing
            r0 = _rates(t)[0]
            growth = sum(w[j] * (1.0 + r0) ** (p - j) for j in cons)
            drawn = amount / growth if growth else amount
            draws = w * drawn
            idc = np.zeros(n)
            for j in cons:
                idc[j] = draws[j] * ((1.0 + r0) ** (p - j) - 1.0)
            upfront = np.zeros(n)
            upfront[0] = t.upfront_fee * amount
            commit = np.zeros(n)
            cum = 0.0
            for j in cons[:-1]:
                cum += draws[j]
                commit[j + 1] = (t.commitment_fee or 0.0) * (drawn - cum)
            interest, principal, balance, sflags = _schedule(t, amount, tl, sculpt_service)
            if c:
                # construction balances: draws + IDC compounding to the COD point
                bal = 0.0
                for j in cons:
                    bal = bal * (1.0 + r0) + draws[j]
                    balance[j] = bal
                balance[p] = amount
            service = interest + principal
            dsra = np.zeros(n)
            months = t.dsra_months or 0
            for k in range(p, n - 1):
                dsra[k] = months / 12.0 * service[k + 1]
            if months and c == 0:
                flags.append(f"dsra_first_service_unreserved:{i}")
            results.append(TrancheResult(index=i, tranche=t, amount=amount, principal_drawn=drawn,
                                         draws=draws, idc=idc, upfront_fee=upfront,
                                         commitment_fee=commit, balance=balance,
                                         interest=interest, principal=principal,
                                         dsra_target=dsra, flags=flags + sflags))
            if avail is not None:
                avail = avail - service
        return results

    def uses_of(results) -> tuple[float, np.ndarray]:
        cash = capex.copy()
        idc = 0.0
        for r in results:
            cash += r.upfront_fee + r.commitment_fee
            idc += float(r.idc.sum())
        dsra0 = sum(r.dsra_target[p] for r in results)
        cash[p] += dsra0
        return float(cash.sum()) + idc, (cash / cash.sum() if cash.sum() else phasing)

    uses_est, weights = capex_total, phasing
    circular = any(t.gearing is not None and t.gearing_base == "total_uses" for t in fin.debt)
    iterations, residual = 0, 0.0
    results = one_pass(uses_est, weights)
    if circular:
        # Fixed-point iteration on total uses, accelerated by Aitken's Δ²
        # (the map is affine in the debt, so the jump lands on the fixed point);
        # a contraction ratio ≥ 1 has no fixed point (review B6).
        history = [uses_est]
        jumped = False
        for iterations in range(1, MAX_ITER + 1):
            total, w_new = uses_of(results)
            residual = abs(total - uses_est) / max(abs(total), 1.0)
            # Converged only when the draw weights have settled too (review r2-2).
            if residual <= TOL and float(np.max(np.abs(w_new - weights))) <= TOL:
                break
            history.append(total)
            nxt = total
            # Aitken from the third plain step on: the first triple still
            # carries the initial weights (review r2 non-binding).
            if len(history) >= 4 or (jumped and len(history) >= 3):
                x0, x1, x2 = history[-3:]
                d1, d2 = x1 - x0, x2 - x1
                if d1 != 0.0:
                    q = d2 / d1
                    if q >= 1.0 - 1e-12:
                        if not jumped:
                            # The plain map does not contract: no fixed point.
                            return empty([f"debt_fixed_point_diverges:contraction={q:.3g}"],
                                         iterations=iterations, residual=residual)
                        history = [total]          # near the root: plain steps
                    else:
                        nxt = x2 + d2 * q / (1.0 - q)
                        history, jumped = [nxt], True
            uses_est, weights = nxt, w_new
            results = one_pass(uses_est, weights)
        else:
            return empty([f"debt_fixed_point_not_converged:residual={residual:.3g}"],
                         iterations=MAX_ITER, residual=residual)

    interest = sum(r.interest for r in results)
    principal = sum(r.principal for r in results)
    service = interest + principal
    draws = sum(r.draws for r in results)
    idc = sum(r.idc for r in results)
    fees = sum(r.upfront_fee + r.commitment_fee for r in results)
    dsra_bal = sum(r.dsra_target for r in results)
    dsra_fund = np.diff(np.concatenate([[0.0], dsra_bal]))
    res_int = np.zeros(n)
    res_int[1:] = rr * dsra_bal[:-1]
    flags = [f for r in results for f in r.flags]
    if cfads is None:
        flags.append("dscr_not_established:operating_not_established")
    if c and float(sum(r.idc.sum() for r in results)) > 0:
        flags.append("idc_axis_point_draws")
        if any(isinstance(r.tranche.rate, list) for r in results if r.idc.sum() > 0):
            flags.append("idc_at_first_rate")
    if not c and float(draws.sum()) > 0:
        flags.append("first_service_at_draw_point")

    def ratio(num, den):
        out = np.full(n, np.nan)
        if num is None:
            return out
        m = den > 1e-9
        out[m] = num[m] / den[m]
        return out

    total_uses = float(capex_total + idc.sum() + fees.sum() + dsra_bal[p])
    debt_total = float(sum(r.amount for r in results))
    reasons = []
    if debt_total > total_uses * (1.0 + TOL):          # the fixed point's tolerance (review r2-1)
        reasons.append(f"debt_exceeds_uses:{debt_total:.2f}>{total_uses:.2f}")
    out = Debt(tl=tl, tranches=results, cfads=cfads, interest=interest, principal=principal,
               service=service, draws=draws, idc=idc, fees=fees, dsra_balance=dsra_bal,
               dsra_funding=dsra_fund, reserve_interest=res_int,
               dscr=ratio(cfads, service), dscr_senior=ratio(cfads, results[0].service),
               uses={"capex": capex_total, "idc": float(idc.sum()), "fees": float(fees.sum()),
                     "dsra": float(dsra_bal[p]), "total": total_uses},
               sources={"debt": debt_total, "equity": total_uses - debt_total},
               iterations=iterations, residual=residual if circular else None,
               flags=sorted(set(flags)))
    if reasons:
        out.status, out.reasons = "not_established", reasons
    return out
