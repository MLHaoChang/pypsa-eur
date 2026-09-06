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


def fill_periodized_cost_defaults(n, cfg: "SolverConfig") -> Callable[[], None]:
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
    """
    import numpy as np

    undo: list[tuple[str, str, pd.Index, pd.Series]] = []
    for comp_attr in ("generators", "storage_units", "stores", "links", "lines", "transformers"):
        df = getattr(n, comp_attr, None)
        if df is None or df.empty or "overnight_cost" not in df.columns:
            continue
        has_overnight = df["overnight_cost"].notna() & (df["overnight_cost"] != 0)
        if not has_overnight.any():
            continue
        if "discount_rate" in df.columns:
            missing_dr = has_overnight & df["discount_rate"].isna()
            if missing_dr.any():
                idx = df.index[missing_dr]
                original = df.loc[idx, "discount_rate"].copy()
                df.loc[idx, "discount_rate"] = cfg.discount_rate
                undo.append((comp_attr, "discount_rate", idx, original))
        if "lifetime" in df.columns:
            lt = df["lifetime"]
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
def with_periodized_cost_defaults(n, cfg: "SolverConfig"):
    """
    Context-manager wrapper around fill_periodized_cost_defaults — for
    callers that prefer `with` over an explicit try/finally pair.
    """
    revert = fill_periodized_cost_defaults(n, cfg)
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


def periodized_capital_costs(n, cfg: "SolverConfig") -> dict[str, dict[str, dict[str, float]]]:
    """
    Return per-asset cost facts for every cost-bearing component, keyed as
    ``{component_attr: {name: {"capital_cost": float, "overnight_cost": float, "lifetime": float}}}``.

    Two different cost numbers per asset:

      * ``capital_cost`` — PyPSA's annualised cost (`comp.capital_cost`), i.e.
        ``overnight × annuity × nyears`` for assets parameterised via
        overnight_cost, or the raw `capital_cost` column otherwise. This is
        what the LP objective sees and what the "Annualised" toggle on the
        frontend displays.

      * ``overnight_cost`` — the upfront lump-sum investment per unit of
        capacity (`comp.overnight_cost`). Returned as-typed when the user
        set `overnight_cost`; back-calculated from `capital_cost ÷ annuity`
        otherwise. This is what "Total over lifetime" should multiply by
        Δcapacity to get the user's expected upfront build cost (e.g. a
        battery with 1000 €/MW × 71.3 MW Δ ⇒ €71.3 k, not the annuity-
        times-lifetime figure which mixes scaling assumptions and confused
        users in earlier iterations).

      * ``lifetime`` — years, kept for tooltips and CSV exports.

    Returns NaN-free numbers — non-finite intermediates fall back to
    sensible defaults (raw column / global config / 0).
    """
    import math

    out: dict[str, dict[str, dict[str, float]]] = {}
    with with_periodized_cost_defaults(n, cfg):
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
                # comp.overnight_cost back-calculates upfront cost from
                # capital_cost / annuity / nyears for assets that didn't
                # type one in. Raises ValueError if discount_rate/lifetime
                # are still NaN despite the fill — fall back to per-asset
                # capital_cost in that case so we don't 500.
                upfront_series = n.c[comp_class].overnight_cost
            except Exception:
                upfront_series = None
            # PV factor per asset based on (build_year − reference_year).
            pv_series = _pv_factor_series(df, cfg, reference_year)
            mapping: dict[str, dict[str, float]] = {}
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
                # AND the user didn't set overnight_cost — fall back to the
                # annualised number so the lifetime toggle still shows a
                # finite (if conservative) value.
                v_upf = float("nan")
                if upfront_series is not None and name in upfront_series.index:
                    try:
                        v_upf = float(upfront_series.loc[name])
                    except (TypeError, ValueError):
                        v_upf = float("nan")
                if math.isnan(v_upf) or math.isinf(v_upf):
                    v_upf = v_ann
                # Present value of the upfront cost. For year-0 builds the
                # factor is 1; for future-year builds it shrinks the nominal
                # spend by (1+r)^-(years out).
                try:
                    pv = float(pv_series.loc[name])
                except (KeyError, TypeError, ValueError):
                    pv = 1.0
                if math.isnan(pv) or math.isinf(pv) or pv <= 0:
                    pv = 1.0
                v_upf_pv = v_upf * pv
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
                entry: dict[str, float] = {
                    "capital_cost": v_ann,
                    "overnight_cost": v_upf,
                    "overnight_cost_pv": v_upf_pv,
                    "lifetime": lt,
                }
                if by_val is not None:
                    entry["build_year"] = by_val
                mapping[str(name)] = entry
            out[comp_attr] = mapping
    return out


