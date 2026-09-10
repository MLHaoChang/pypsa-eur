"""
Periodized capital costs: annuity, present-value factors, and the transient
cost-default fill.

Carved out of `services/solver_service.py`, which keeps `run_simulation`,
`SolverConfig`, and the solve orchestration. This module is a LEAF: it imports
nothing from `solver_service` and nothing from the rest of the `solver`
package, so there is no cycle to defer — unlike `services/ac_pf_service.py`,
the earlier carve-out, which imports three names back and is itself imported
lazily from a function body for exactly that reason.

`cfg: "SolverConfig"` is annotated as a string throughout, and string
annotations are never evaluated at runtime, so typing against the config costs
this module no import.

Callers reach every name here through `services.solver_service`, which
re-exports them — `routers/results.py`, `routers/compare.py`,
`services/cost_totals.py`, `services/asset_results/compute.py` and the golden
economics fixtures all import from there, and none of them changed when this
module appeared. `tests/test_solver_facade_surface.py` is what keeps that true.
"""
from collections.abc import Callable
from contextlib import contextmanager

import pandas as pd


def _annuity(rate: float, lifetime: float) -> float:
    """
    Standard CRF / annuity factor.

    r × (1+r)^L / ((1+r)^L − 1), with the degenerate rate→0 case falling back
    to 1/L (straight-line). lifetime ≤ 0 returns 0 (no annualisation).
    """
    if lifetime <= 0:
        return 0.0
    if rate == 0.0:
        return 1.0 / lifetime
    factor = (1.0 + rate) ** lifetime
    return rate * factor / (factor - 1.0)


def fill_periodized_cost_defaults(
    n, cfg: "SolverConfig", *, for_back_calculation: bool = False,
) -> Callable[[], None]:
    """
    Fill per-asset `discount_rate` / `lifetime` from the global config for
    any asset that has `overnight_cost` set but a blank discount_rate or a
    non-finite lifetime, and return a `revert` callable that undoes the fill.

    Used in two places:
      • solver_service.run_simulation — before n.optimize() so the LP sees
        a valid annuitization. PyPSA's consistency check otherwise raises
        "overnight_cost set but missing discount_rate".
      • routers/simulation.py — wrapped around `n.statistics()` calls.
        PyPSA computes the periodized capital_cost via
        `periodized_cost(capital_cost, overnight_cost, discount_rate, ...)`;
        with discount_rate=NaN that returns NaN for assets carrying
        overnight_cost, which collapses to 0 in our cost_breakdown and
        makes the "Investment (new only)" KPI under-report.

    Both sites need the same fill, so it lives here once. Caller MUST invoke
    the returned revert() in a finally block — otherwise the on-disk network
    state keeps the fill and a later global-rate change won't propagate.

    ``for_back_calculation`` adds a SECOND, narrower fill for the mirror-image
    population: assets that carry a non-zero `capital_cost` and NO
    `overnight_cost`. Those need `discount_rate` for the OPPOSITE direction —
    PyPSA's `comp.overnight_cost` recovering an upfront cost from
    `capital_cost / (annuity x nyears)` — and it raises ValueError for the
    WHOLE component class when the rate is NaN. That is exactly what happened
    to Lines: PyPSA-Eur prices them through `capital_cost` alone, so the fill
    above never touched them, `n.c["Line"].overnight_cost` raised, and
    `get_cost_breakdown` reported a lifetime CAPEX of 0.00 for 95.8% of the
    system. Pass it only where an upfront cost is being READ.

    It fills `discount_rate` and DELIBERATELY NOT `lifetime`, which is not
    symmetric and not an oversight:

      • `discount_rate` cannot move any existing number for this population.
        `pypsa.costs.periodized_cost` computes
        `base = annuitized.where(has_overnight, capital_cost)`, so an asset
        with no `overnight_cost` keeps its raw `capital_cost` whatever the rate
        says — `n.statistics()` and the LP objective are untouched.
      • `lifetime` is NOT a cost input alone. On a multi-period network PyPSA
        derives asset ACTIVITY from `build_year + lifetime`, so substituting
        the config default retires assets. Measured on the golden fixture: a
        Line at the default `build_year=0` and `lifetime=inf` went to
        `lifetime=25`, fell outside both 2030 and 2035, and its Capital
        Expenditure dropped from EUR 500 M per period to 0.00 — reintroducing
        the very defect this change exists to fix, one layer down. A blank
        lifetime therefore stays blank; PyPSA raises, and the caller reports
        the class as unavailable rather than quietly retiring it.

    (PyPSA's default `lifetime` is `inf`, not NaN, and `annuity(r, inf)` is
    `max(r, 0)` — so the common "user never typed a lifetime" case still
    back-calculates fine on the rate alone.)
    """
    import numpy as np

    undo: list[tuple[str, str, pd.Index, pd.Series]] = []
    for comp_attr in ("generators", "storage_units", "stores", "links", "lines", "transformers"):
        df = getattr(n, comp_attr, None)
        if df is None or df.empty or "overnight_cost" not in df.columns:
            continue
        has_overnight = df["overnight_cost"].notna() & (df["overnight_cost"] != 0)
        # PyPSA's own back-calculation predicate: no overnight_cost AND a
        # capital_cost worth converting. Mirrors `Components.overnight_cost`
        # (`needs_back_calc = ~has_overnight & (capital != 0)`); an asset at
        # capital_cost 0 needs nothing and is left alone.
        if for_back_calculation and "capital_cost" in df.columns:
            back_calc = ~df["overnight_cost"].notna() & (df["capital_cost"].fillna(0) != 0)
        else:
            back_calc = pd.Series(False, index=df.index)
        if not (has_overnight.any() or back_calc.any()):
            continue
        if "discount_rate" in df.columns:
            # Both populations: the rate is a pure cost input either way.
            missing_dr = (has_overnight | back_calc) & df["discount_rate"].isna()
            if missing_dr.any():
                idx = df.index[missing_dr]
                original = df.loc[idx, "discount_rate"].copy()
                df.loc[idx, "discount_rate"] = cfg.discount_rate
                undo.append((comp_attr, "discount_rate", idx, original))
        if "lifetime" in df.columns:
            lt = df["lifetime"]
            # `has_overnight` ONLY — see the docstring on why the
            # back-calculation population must keep its lifetime.
            # Treat both NaN and inf as "user didn't pick a number" — PyPSA's
            # default lifetime is +inf, so we can't distinguish "explicit
            # perpetuity" from "unset" anyway.
            missing_lt = has_overnight & (lt.isna() | ~np.isfinite(lt))
            if missing_lt.any():
                idx = df.index[missing_lt]
                original = df.loc[idx, "lifetime"].copy()
                df.loc[idx, "lifetime"] = cfg.default_lifetime
                undo.append((comp_attr, "lifetime", idx, original))

    def revert() -> None:
        for comp_attr, col, idx, original in reversed(undo):
            df = getattr(n, comp_attr, None)
            if df is None:
                continue
            valid = [i for i in idx if i in df.index]
            if valid:
                df.loc[valid, col] = original.loc[valid]

    return revert


@contextmanager
def with_periodized_cost_defaults(
    n, cfg: "SolverConfig", *, for_back_calculation: bool = False,
):
    """
    Context-manager wrapper around fill_periodized_cost_defaults — for
    callers that prefer `with` over an explicit try/finally pair.

    See that function for what ``for_back_calculation`` widens and why it is
    opt-in; pass it when the block READS an upfront (overnight) cost.
    """
    revert = fill_periodized_cost_defaults(
        n, cfg, for_back_calculation=for_back_calculation,
    )
    try:
        yield
    finally:
        revert()


def _reference_build_year(n) -> float:
    """
    Reference year for discounting future-year investment to present value.

    Multi-period: the FIRST investment period. PV factors then discount each
    asset from its build_year back to the start of the planning horizon — and
    a build_year at or before the first period (including the GUI's 0 default
    for pre-existing assets) collapses to a PV factor of 1.0, as it should.

    Using ``min(build_year)`` here instead silently breaks multi-period runs:
    one pre-existing asset with build_year=0 drops the reference to year 0,
    and every asset built in a real year (2026, 2027, …) then gets discounted
    by (1 + r)^-2026 ≈ 0 — its PV investment rounds to zero (PV factor ~3e-60
    in practice). That zeroed every new build's CAPEX / "Investment (new, PV)"
    KPI and the expansion-by-class chart.

    Single-period: the earliest finite build_year across all cost-bearing
    components (0.0 when none carry one) — every PV factor then collapses to
    1.0 for the common overnight run, the original behaviour.
    """
    import numpy as _np
    import pandas as _pd
    # Multi-period → anchor PV discounting on the first investment period.
    try:
        if isinstance(n.snapshots, _pd.MultiIndex) and len(n.investment_periods) > 0:
            return float(min(int(p) for p in n.investment_periods))
    except (TypeError, ValueError, AttributeError):
        pass
    ref: float | None = None
    for comp_attr in ("generators", "storage_units", "stores", "links",
                      "lines", "transformers"):
        df = getattr(n, comp_attr, None)
        if df is None or df.empty or "build_year" not in df.columns:
            continue
        bys = df["build_year"]
        finite = bys[_np.isfinite(bys)]
        if len(finite) > 0:
            mn = float(finite.min())
            ref = mn if ref is None else min(ref, mn)
    return ref if ref is not None else 0.0


def _pv_factor_series(df, cfg: "SolverConfig", reference_year: float):
    """
    Per-asset present-value factor ``(1+r)^-(build_year - reference)``.

    ``r`` is the per-asset ``discount_rate`` column (which the LP-time fill
    populated from the global config for any blank entries). ``build_year``
    falls back to the reference year so an asset without a build_year just
    contributes a PV factor of 1.0. Negative deltas (asset built before the
    reference) are clipped to 0 too — pre-reference investment is already
    sunk; no compounding-up to present value here.
    """
    import numpy as _np
    import pandas as _pd
    if "build_year" in df.columns:
        bys = df["build_year"].where(_np.isfinite(df["build_year"]), reference_year)
    else:
        bys = _pd.Series(reference_year, index=df.index, dtype=float)
    years_future = (bys - reference_year).clip(lower=0)
    if "discount_rate" in df.columns:
        drs = df["discount_rate"].where(_np.isfinite(df["discount_rate"]), cfg.discount_rate)
    else:
        drs = _pd.Series(cfg.discount_rate, index=df.index, dtype=float)
    return (1.0 + drs) ** (-years_future)


# Moved here in the 2026-09-10 merge with master. It was added to
# `solver_service.py` on this branch INSIDE the line range the
# decomposition had already carved into this module, so it belongs
# here with its only caller; `solver_service` re-exports it so the
# import in `routers/results.py` (and the three tests that patch it)
# keep working against the facade, which is the decomposition's
# stated contract.
def upfront_cost_series(n, comp_class: str) -> pd.Series:
    """
    Upfront (overnight) investment cost per unit of capacity, per asset.

    A one-line named wrapper around PyPSA's `n.c[<class>].overnight_cost`, on
    purpose: that property has two behaviours a caller must know about, and
    naming the operation gives them one place to live.

      • It returns the user-typed `overnight_cost` where there is one, and
        back-calculates `capital_cost / (annuity x nyears)` where there is not.
      • It raises ValueError for the WHOLE component class if any asset needs
        the back-calculation and is missing `discount_rate` or `lifetime`. Wrap
        the call in `with_periodized_cost_defaults(..., for_back_calculation=
        True)` so the config defaults are in place, and treat a raise as "this
        class's upfront cost is unknown" — never as zero.

    Callers decide what unknown means for their response; this function has no
    opinion and swallows nothing.
    """
    return n.c[comp_class].overnight_cost


def periodized_capital_costs(n, cfg: "SolverConfig") -> dict[str, dict[str, dict[str, float | bool | None]]]:
    """
    Return per-asset cost facts for every cost-bearing component, keyed as
    ``{component_attr: {name: {"capital_cost": float, "overnight_cost": float | None,
    "overnight_cost_available": bool, "lifetime": float}}}``.

    Two different cost numbers per asset:

      * ``capital_cost`` — PyPSA's annualised cost (`comp.capital_cost`), i.e.
        ``overnight × annuity × nyears`` for assets parameterised via
        overnight_cost, or the raw `capital_cost` column otherwise. This is
        what the LP objective sees and what the "Annualised" toggle on the
        frontend displays. Always a real, finite number — unaffected by
        whether the upfront cost below resolves (see
        `with_periodized_cost_defaults`'s docstring: filling `discount_rate`
        for the back-calculation population cannot move this value).

      * ``overnight_cost`` — the upfront lump-sum investment per unit of
        capacity (`comp.overnight_cost`, via `upfront_cost_series`). Returned
        as-typed when the user set `overnight_cost`; back-calculated from
        `capital_cost ÷ annuity` otherwise — which is why this call passes
        ``for_back_calculation=True`` below, letting assets priced through
        `capital_cost` alone (e.g. Lines, which PyPSA-Eur never sets
        `overnight_cost` on) resolve a real figure instead of raising for the
        whole component class. This is what "Total over lifetime" should
        multiply by Δcapacity to get the user's expected upfront build cost
        (e.g. a battery with 1000 €/MW × 71.3 MW Δ ⇒ €71.3 k, not the
        annuity-times-lifetime figure which mixes scaling assumptions and
        confused users in earlier iterations).

        ``None`` when it genuinely cannot be resolved even with the fill
        (e.g. the resolved value is still NaN/inf — a zero `discount_rate`
        against a `lifetime`-unset asset divides by zero). NEVER silently
        substituted with `capital_cost`: that field is an ANNUALISED rate
        (EUR/MW/yr), not an upfront lump sum (EUR/MW) — off by roughly the
        annuity factor (~15-20x for a 40-year asset), not a conservative
        estimate. ``overnight_cost_pv`` (present-value-adjusted) is `None`
        under the same condition.

      * ``overnight_cost_available`` — `False` exactly when `overnight_cost`
        / `overnight_cost_pv` are `None` for this asset. Callers must branch
        on this (or on the nulls directly) and render an "unavailable" state
        — never fall back to a raw/zero column, which is indistinguishable
        from a genuinely free asset.

      * ``lifetime`` — years, kept for tooltips and CSV exports.

    Returns NaN-free numbers everywhere except the two upfront-cost fields,
    which are `None` (never NaN — NaN is not valid JSON) exactly when
    `overnight_cost_available` is `False`.
    """
    import math

    out: dict[str, dict[str, dict[str, float | bool | None]]] = {}
    # for_back_calculation=True: this function READS the upfront (overnight)
    # cost via `upfront_cost_series` below, which is precisely the case that
    # fill needs (see its docstring). Safe for `capital_cost` (v_ann, above):
    # the fill only ever supplies `discount_rate`, never `lifetime`, so it
    # cannot retire an asset out of a multi-period run — see
    # `fill_periodized_cost_defaults`'s docstring for why that split matters.
    with with_periodized_cost_defaults(n, cfg, for_back_calculation=True):
        reference_year = _reference_build_year(n)
        for comp_attr, comp_class in (
            ("generators", "Generator"),
            ("storage_units", "StorageUnit"),
            ("stores", "Store"),
            ("links", "Link"),
            ("lines", "Line"),
            ("transformers", "Transformer"),
        ):
            df = getattr(n, comp_attr, None)
            if df is None or df.empty:
                continue
            try:
                ann_series = n.c[comp_class].capital_cost
            except Exception:
                continue
            try:
                # upfront_cost_series back-calculates upfront cost from
                # capital_cost / annuity / nyears for assets that didn't
                # type one in. Can still raise ValueError for the whole class
                # (e.g. `lifetime` genuinely NaN rather than PyPSA's +inf
                # default — the fill above never touches lifetime). Per-asset
                # fallback below covers the narrower case where the class
                # resolves but one asset's own value is still non-finite.
                upfront_series = upfront_cost_series(n, comp_class)
            except Exception:
                upfront_series = None
            # PV factor per asset based on (build_year − reference_year).
            pv_series = _pv_factor_series(df, cfg, reference_year)
            mapping: dict[str, dict[str, float | bool | None]] = {}
            raw_cc = df["capital_cost"] if "capital_cost" in df.columns else None
            raw_lt = df["lifetime"] if "lifetime" in df.columns else None
            raw_by = df["build_year"] if "build_year" in df.columns else None
            for name in df.index:
                # Annualised
                try:
                    v_ann = float(ann_series.loc[name])
                except (KeyError, TypeError, ValueError):
                    v_ann = float("nan")
                if math.isnan(v_ann) or math.isinf(v_ann):
                    v_ann = float(raw_cc.loc[name]) if raw_cc is not None and name in raw_cc.index else 0.0
                # Upfront (overnight). NaN if PyPSA couldn't back-calculate
                # AND the user didn't set overnight_cost. NEVER substituted
                # with the annualised number below — `capital_cost` is
                # EUR/MW/yr, `overnight_cost` is EUR/MW, and conflating them
                # is a unit error of roughly the annuity factor, not a
                # conservative estimate. Genuinely unresolved is reported as
                # such via `overnight_cost_available` / `None`.
                v_upf = float("nan")
                if upfront_series is not None and name in upfront_series.index:
                    try:
                        v_upf = float(upfront_series.loc[name])
                    except (TypeError, ValueError):
                        v_upf = float("nan")
                upfront_available = not (math.isnan(v_upf) or math.isinf(v_upf))
                # Present value of the upfront cost. For year-0 builds the
                # factor is 1; for future-year builds it shrinks the nominal
                # spend by (1+r)^-(years out). Meaningless when the upfront
                # cost itself didn't resolve.
                try:
                    pv = float(pv_series.loc[name])
                except (KeyError, TypeError, ValueError):
                    pv = 1.0
                if math.isnan(pv) or math.isinf(pv) or pv <= 0:
                    pv = 1.0
                v_upf_pv = v_upf * pv if upfront_available else float("nan")
                # Lifetime — used for display only (tooltip/CSV), so still
                # report after the global-default fill.
                lt = (float(raw_lt.loc[name])
                      if raw_lt is not None and name in raw_lt.index else float("nan"))
                if math.isnan(lt) or math.isinf(lt) or lt <= 0:
                    lt = float(cfg.default_lifetime)
                # Build year — float so the JSON serialises cleanly even
                # if pandas hands us numpy.int64 / NaN. Drop NaN to None.
                if raw_by is not None and name in raw_by.index:
                    try:
                        by_val = float(raw_by.loc[name])
                        if math.isnan(by_val) or math.isinf(by_val):
                            by_val = None  # type: ignore[assignment]
                    except (TypeError, ValueError):
                        by_val = None  # type: ignore[assignment]
                else:
                    by_val = None  # type: ignore[assignment]
                entry: dict[str, float | bool | None] = {
                    "capital_cost": v_ann,
                    "overnight_cost": v_upf if upfront_available else None,
                    "overnight_cost_pv": v_upf_pv if upfront_available else None,
                    "overnight_cost_available": upfront_available,
                    "lifetime": lt,
                }
                if by_val is not None:
                    entry["build_year"] = by_val
                mapping[str(name)] = entry
            out[comp_attr] = mapping
    return out


