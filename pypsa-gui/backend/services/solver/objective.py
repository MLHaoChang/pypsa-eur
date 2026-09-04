"""
`extra_functionality` wrappers and objective scaling.

Carved out of `services/solver_service.py`. Everything here composes or rewrites
the callable handed to `n.optimize()`, or rescales the results that come back,
so this is the highest-consequence module in the package after `myopic`: a
defect here moves numbers the frontend serves rather than lines in a log.

Depends only on `services/solver/runtime.py` for `_safe_log`, and never on
`solver_service`.
"""
import math

import pandas as pd
import pypsa  # noqa: F401 — resolves the `"pypsa.Network"` annotations below

from services.solver.runtime import _safe_log


def _wrap_with_capex_budget(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Compose the extra_functionality callback with a per-period CAPEX
    budget constraint.

    For each period P with `cfg.capex_budget_per_period[str(P)]` set:
      Σ over extendable assets with build_year == P:
        overnight_cost_g × Δp_nom_g     ≤     budget[P]

    where Δp_nom_g = p_nom_var_g − p_nom_g (the LP's NEW build above any
    pre-existing capacity). Falls back to capital_cost when overnight_cost
    is NaN/zero — preserves the constraint shape even on legacy datasets.

    Surfaces a `[BUDGET]` log line per period showing the configured cap and
    the assets contributing to that period's CAPEX. Returns ``user_fn``
    unchanged when no budget is set, so the LP stays untouched for runs
    that don't need this.
    """
    budgets = getattr(cfg, "capex_budget_per_period", None) or {}
    if not budgets:
        return user_fn

    # Normalize keys to int periods, drop entries with non-finite or
    # non-positive budgets (those are no-ops). Survive both
    # `{"2026": 5e8}` (JSON) and `{2026: 5e8}` (Python).
    normalized: dict[int, float] = {}
    for k, v in budgets.items():
        try:
            ik = int(k)
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not (fv > 0 and math.isfinite(fv)):
            continue
        normalized[ik] = fv
    if not normalized:
        return user_fn

    # Component → (static-attr, p_nom-field) for every cost-bearing
    # capacity decision. Same tuple shape vintage_service uses.
    capacity_attrs = (
        ("Generator",   "generators",    "p_nom"),
        ("StorageUnit", "storage_units", "p_nom"),
        ("Store",       "stores",        "e_nom"),
        ("Link",        "links",         "p_nom"),
        ("Line",        "lines",         "s_nom"),
        ("Transformer", "transformers",  "s_nom"),
    )

    def _emit(msg: str) -> None:
        _safe_log(log_queue, f"[BUDGET] {msg}")

    def capex_budget_fn(n, snapshots):
        for period, budget in normalized.items():
            # Find every extendable asset whose build_year matches this
            # period AND whose capacity variable lives in the LP.
            terms: list = []  # list of (name, coef, p_nom_var, p_nom_existing)
            for comp_class, attr, pnom in capacity_attrs:
                df = getattr(n, attr, None)
                if df is None or df.empty:
                    continue
                ext_col = f"{pnom}_extendable"
                if ext_col not in df.columns or "build_year" not in df.columns:
                    continue
                by = df["build_year"]
                # Some inputs leave build_year NaN — treat NaN as "not in
                # any period" (skip rather than match arbitrarily).
                mask = df[ext_col].astype(bool) & (by.fillna(-1).astype("Int64") == period)
                if not mask.any():
                    continue
                # The LP variable for p_nom is `{ComponentClass}-{p_nom}` in
                # linopy; same naming convention the curtailment wrapper
                # uses for Generator-p_nom.
                var_name = f"{comp_class}-{pnom}"
                if var_name not in n.model.variables:
                    continue
                p_nom_var = n.model.variables[var_name]
                # Effective per-MW cost: overnight_cost first, fall back to
                # capital_cost when overnight_cost is missing. Both are
                # in EUR per MW (overnight_cost is upfront, capital_cost is
                # annualised — for a budget cap we want upfront, but this is
                # a defensive fallback for incomplete data).
                for name in df.index[mask]:
                    oc = df.at[name, "overnight_cost"] if "overnight_cost" in df.columns else float("nan")
                    cc = df.at[name, "capital_cost"] if "capital_cost" in df.columns else float("nan")
                    try:
                        coef = float(oc) if (oc is not None and math.isfinite(float(oc)) and float(oc) > 0) else float(cc or 0.0)
                    except (TypeError, ValueError):
                        coef = 0.0
                    if coef <= 0 or not math.isfinite(coef):
                        continue
                    p_nom_existing = float(df.at[name, pnom] or 0.0)
                    terms.append((name, coef, p_nom_var.sel(name=name), p_nom_existing))
            if not terms:
                _emit(
                    f"Period {period}: budget €{budget:,.0f} set but no "
                    f"extendable assets with build_year={period} were found. "
                    f"Constraint skipped."
                )
                continue
            # Build the linopy expression: Σ coef × (p_nom_var − p_nom_existing) ≤ budget
            # Equivalent: Σ coef × p_nom_var ≤ budget + Σ coef × p_nom_existing
            existing_capex = sum(coef * p_nom_existing for _, coef, _, p_nom_existing in terms)
            rhs = budget + existing_capex
            # Linopy `merge` builds the linear combination from individual var terms.
            expr = sum(coef * var for _, coef, var, _ in terms)
            cname = f"capex_budget_{period}"
            try:
                n.model.add_constraints(expr <= rhs, name=cname)
                _emit(
                    f"Period {period}: cap = €{budget:,.0f} on "
                    f"{len(terms)} extendable asset(s); LP enforces "
                    f"Σ overnight_cost × Δp_nom ≤ €{budget:,.0f}."
                )
            except Exception as exc:
                _emit(
                    f"Period {period}: failed to add LP constraint: {exc}. "
                    f"Skipping this period's budget."
                )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        capex_budget_fn(n, snapshots)

    return wrapper


def _wrap_with_curtailment_cost(network: "pypsa.Network", user_fn, log_queue=None):
    """
    Build the final extra_functionality callable.

    If any generator carries a non-zero `curtailment_cost` (a custom column we
    surface in the GUI), return a wrapper that adds the LP term
        Σ curtailment_cost × (p_max_pu × nom − p)
    to the objective. When the user also supplied an extra_functionality, both
    callbacks run (user's first, ours second — order doesn't matter for
    objective terms).

    `log_queue` (optional): the SSE-bound queue used by ``run_simulation``.
    When provided, the wrapper pushes per-generator diagnostic lines onto it
    directly — bypassing Python's `logging` module, which by default filters
    INFO from the root logger and silently drops our diagnostics.

    Returns ``user_fn`` unchanged when no generator opts in, so we don't pay
    the import cost or burden the LP graph for runs that don't need it.
    """
    # The column may not exist on networks created before this feature shipped;
    # fall back gracefully if so.
    gens = getattr(network, "generators", None)
    if gens is None or "curtailment_cost" not in gens.columns:
        return user_fn
    # Capture the pre-wrapper `_objective_constant` baseline ONCE so re-solves
    # don't accumulate the curtailment constant on top of itself. PyPSA
    # persists `_objective_constant` through netcdf round-trips; without
    # this baseline, each re-solve would add the recomputed constant ON TOP
    # of the previous solve's constant, drifting the reported objective
    # upward by N×constant after N solves on the same network. The closure
    # below reads `n._baseline_objective_constant` and writes
    # `_objective_constant = baseline + new_constant`, idempotent across
    # re-solves.
    try:
        network._baseline_objective_constant = float(
            getattr(network, "_objective_constant", 0.0) or 0.0
        )
    except Exception:
        network._baseline_objective_constant = 0.0
    active = gens.index[gens["curtailment_cost"].fillna(0.0) > 0.0]
    if len(active) == 0:
        return user_fn

    def curtailment_cost_fn(n, snapshots):
        # Recompute the active set at solve time from n.generators directly.
        # The outer-scope `active` snapshot only captures parents — by the
        # time this callback fires, `vintage_service.apply_vintage_bounds`
        # has already added per-period extendable vintage rows that inherit
        # `curtailment_cost` from their parent. Those vintages are the only
        # sizing variables in multi-period mode (the parent's _extendable
        # was flipped to False), so missing them was silently disabling
        # the entire capacity-penalty term and letting the LP overbuild
        # renewables free of curtailment penalty.
        gens_now = getattr(n, "generators", None)
        if gens_now is None or "curtailment_cost" not in gens_now.columns:
            return
        live = [g for g in gens_now.index
                if float(gens_now.at[g, "curtailment_cost"] or 0.0) > 0.0]
        if not live:
            return
        n_ext = sum(1 for g in live if bool(gens_now.at[g, "p_nom_extendable"]))

        # Push diagnostics to the SSE log_queue directly so they show up
        # in the user-facing Log tab. Python's `logging` module filters
        # INFO at the root logger by default (PHASE markers are pushed
        # directly to the queue — see solver_service.run_simulation), so
        # logger.info(...) calls would be silently dropped.
        def _emit(msg: str) -> None:
            _safe_log(log_queue, f"[CURT] {msg}")

        _emit(
            f"Curtailment-cost wrapper active: {len(live)} generator(s) "
            f"({n_ext} extendable, including vintages) — penalty "
            f"Σ cc × (p_max_pu × p_nom − p) added to objective for extendables; "
            f"skipped for non-extendables (would cause one-sided dispatch subsidy)."
        )
        # nyears comes straight from PyPSA (Σ snapshot_w / 8760 per period
        # in multi-period mode) — this is what scales overnight_cost into
        # the LP via periodized_cost. nyears << 1 is the smoking gun for
        # the silent snapshot_weightings reset.
        try:
            ny = n.nyears
            if hasattr(ny, "items"):
                ny_str = ", ".join(f"{p}:{float(v):.3f}" for p, v in ny.items())
            else:
                ny_str = f"{float(ny):.3f}"
            _emit(f"n.nyears = {ny_str}")
        except Exception:
            pass

        import xarray as xr

        p_max_pu = n.get_switchable_as_dense("Generator", "p_max_pu")
        # Restrict p_max_pu to the LP's iteration snapshots. Without this,
        # myopic mode (which passes `snapshots=<this period's slice>`) sees
        # a capacity-penalty term summing p_max_pu over the FULL
        # n.snapshots index (e.g. 26280 snapshots for a 3-period model),
        # while the dispatch-subsidy term only sums p over the iteration's
        # subset (8760). The 3× imbalance over-penalises p_nom, the LP
        # under-builds renewables, and the result diverges sharply from a
        # full-horizon LP.
        try:
            p_max_pu = p_max_pu.loc[snapshots]
        except KeyError:
            pass  # defensive — keep the full frame if the slice mismatches

        # Pull the LP's actual per-snapshot weights so the extra term lines
        # up with PyPSA's own objective. PyPSA scales every operational
        # cost term by `snapshot_weightings.objective × period_years`; if
        # we don't match, the curtailment-cost term is in different units
        # from marginal_cost × p and the trade-off the LP makes is wrong.
        # Critical for Phase-2 limited-foresight runs where representative
        # future snapshots carry weights up to ~50 (one rep stands in for
        # one cluster of weeks).
        sw = getattr(n, "snapshot_weightings", None)
        if sw is not None and "objective" in sw.columns:
            try:
                w_obj = sw.loc[snapshots, "objective"].astype(float)
            except Exception:
                w_obj = pd.Series(1.0, index=snapshots, dtype=float)
        else:
            w_obj = pd.Series(1.0, index=snapshots, dtype=float)

        # Period-years weighting — PyPSA multiplies operational costs by
        # `investment_period_weightings.years[period]` in multi-period
        # mode. Mirror that so a snapshot in a 4-year-weighted period
        # contributes 4× to both the dispatch subsidy and the capacity
        # penalty, the same way it does to marginal_cost × p.
        ipw = getattr(n, "investment_period_weightings", None)
        years_map: dict[int, float] = {}
        if ipw is not None and "years" in ipw.columns:
            for pp, yy in ipw["years"].items():
                try:
                    years_map[int(pp)] = float(yy)
                except (TypeError, ValueError):
                    continue
        is_mp = isinstance(snapshots, pd.MultiIndex)
        if is_mp and years_map:
            period_level = snapshots.get_level_values(0)
            year_mul = pd.Series(
                [years_map.get(int(pp), 1.0) for pp in period_level],
                index=snapshots, dtype=float,
            )
            weights = w_obj * year_mul
        else:
            weights = w_obj

        # PyPSA 1.x linopy variables use `name` as the per-component dimension
        # (not the component class name). So Generator-p has dims (snapshot,
        # name) and Generator-p_nom has dim (name,). Also: linopy's Variables
        # collection has no `.get()`; use `in` for membership then `[]` for
        # retrieval.
        p = n.model.variables["Generator-p"]      # (snapshot, name) MW
        has_p_nom_var = "Generator-p_nom" in n.model.variables
        p_nom_var = n.model.variables["Generator-p_nom"] if has_p_nom_var else None

        # xarray DataArray for the dispatch-term linopy multiplication.
        # Reuse `p`'s OWN snapshot coord directly — guaranteed to align for
        # broadcast against `p.sel(name=g)`. Building from list(MultiIndex)
        # via `coords={"snapshot": list(snapshots)}` doesn't work because
        # xarray sees a list of (period, timestep) tuples as 2-D data and
        # rejects it for a 1-D coord. linopy's internal representation of
        # the snapshot dim already encodes the multi-period MultiIndex in a
        # broadcast-compatible way; defer to it.
        try:
            snap_coord = p.coords["snapshot"]
            w_xr = xr.DataArray(
                weights.values, dims=["snapshot"],
                coords={"snapshot": snap_coord},
            )
        except Exception:
            # Fallback: positional alignment only (no coord). Works for
            # flat snapshot indexes and avoids crashing the LP build if
            # linopy doesn't expose the coord under the name we expect.
            w_xr = xr.DataArray(weights.values, dims=["snapshot"])

        # Per-generator loop — simpler than wrestling xarray broadcasting for
        # mixed fixed/extendable cases, and the active set is typically small
        # (one entry per renewable carrier, not per snapshot).
        #
        # Math we're adding to the objective (weighted form):
        #   cost × Σ_t w_t × (p_max_pu_t × nom − p_t)
        # Linopy rejects pure-constant terms in the objective, so we split it
        # into ONLY the variable parts:
        #   • dispatch-incentive term: −cost × Σ_t w_t × p_t   (always variable)
        #   • capacity-penalty term:   +cost × (Σ_t w_t × p_max_pu_t) × p_nom_var
        #     (only when extendable; otherwise it's a constant we drop)
        # The dropped constant doesn't affect the optimum — the user's
        # frontend reconstructs the true curtailment cost from the result
        # series anyway (Dispatch.tsx's `curtailmentCost` memo).
        # Build a per-asset "effective capital cost" lookup so the diagnostic
        # log can show the LP-visible €/MW alongside the curtailment-penalty
        # coefficient. PyPSA's c.capital_cost = periodized_cost(...) — same
        # function the LP itself calls. We compute lazily; failures (e.g.
        # network without overnight_cost column) fall back to NaN.
        try:
            eff_cap_cost = n.c.generators.capital_cost  # pd.Series
        except Exception:
            eff_cap_cost = None

        # Track the IMPLIED capacity penalty for non-extendable gens —
        # we can't add the constant `cost × Σw × pmp × p_nom_fixed` to the
        # linopy objective (rejected), but we still need the reported
        # objective to include it so the user sees an accurate total cost.
        # PyPSA's `n._objective_constant` is exactly the offset added to
        # `solver_obj` to get the user-visible `n.objective`. We add to it
        # after the wrapper loop so PyPSA's own constant calculation is
        # preserved.
        nonext_capacity_constant = 0.0

        for g in live:
            cost = float(n.generators.at[g, "curtailment_cost"])
            extendable = bool(n.generators.at[g, "p_nom_extendable"])

            # ── Dispatch subsidy — applied UNIFORMLY to every renewable
            #    that opts into curtailment_cost, regardless of whether
            #    its capacity is extendable or frozen. This is the key
            #    merit-order fix: without it, a new extendable vintage
            #    gets effective mc=−cost while frozen vintages of the
            #    same asset get mc=0, so the LP curtails the OLDER
            #    vintages preferentially. Applying the subsidy uniformly
            #    ties them at the same effective marginal cost — the LP
            #    distributes curtailment proportionally across all
            #    same-bus same-profile renewables.
            n.model.objective += -cost * (w_xr * p.sel(name=g)).sum()

            # ── Capacity penalty — extendable: linear LP term; non-
            #    extendable: constant accumulated for the reported
            #    objective. Together they balance the dispatch subsidy
            #    (net contribution is `cost × curtailment` regardless of
            #    whether p_nom was decided by the LP or was a fixed input).
            weighted_pmp_sum = 0.0
            if g in p_max_pu.columns:
                weighted_pmp_sum = float((weights * p_max_pu[g]).sum())
            if extendable and p_nom_var is not None and weighted_pmp_sum > 0:
                n.model.objective += cost * weighted_pmp_sum * p_nom_var.sel(name=g)
            elif not extendable and weighted_pmp_sum > 0:
                # Constant: `cost × Σw × pmp × p_nom_fixed`. Doesn't affect
                # LP decisions (LP optimum is invariant to additive
                # constants in the objective) but DOES affect the reported
                # objective value. Added to _objective_constant below.
                p_nom_fixed = float(n.generators.at[g, "p_nom"] or 0.0)
                nonext_capacity_constant += cost * weighted_pmp_sum * p_nom_fixed

            # Per-generator diagnostic. Surfaces:
            #   • capex_eff: LP-visible €/MW (annuitised, scaled by nyears).
            #   • cap_penalty_coef: cost × weighted_pmp_sum, LP coefficient
            #     on extendable's p_nom_var (0 for non-extendable — that
            #     part is tracked in nonext_capacity_constant instead).
            #   • dispatch_subsidy_max: cost × Σ w × p_max_pu × (p_nom_max or
            #     p_nom_fixed) — the maximum the LP could deduct via the
            #     dispatch term. Same effective marginal cost for ALL
            #     renewables, so merit order stays neutral between
            #     vintages.
            try:
                capex_eff = (
                    float(eff_cap_cost.get(g, float("nan")))
                    if eff_cap_cost is not None else float("nan")
                )
                cap_penalty_coef = cost * weighted_pmp_sum if extendable else 0.0
                _emit(
                    f"{g} | ext={extendable} | wrapper_applied=True | "
                    f"cc={cost:.0f} €/MWh | capex_eff={capex_eff:.0f} €/MW | "
                    f"cap_penalty_coef={cap_penalty_coef:.0f} €/MW"
                )
            except Exception:
                pass

        # Inject the non-extendable capacity constant into PyPSA's
        # `_objective_constant`. PyPSA's `n.objective` (post-solve) is
        # `solver_obj + _objective_constant` — adding here keeps the
        # reported total accurate (LP's variable-only objective sees the
        # subsidy reducing it, the constant restores the offsetting
        # capacity term so net effect is `cost × actual_curtailment` per
        # non-extendable, matching the math).
        #
        # The constant is recomputed FROM SCRATCH per solve, but PyPSA
        # persists `_objective_constant` through netcdf round-trips —
        # without resetting to the baseline first, each re-solve would
        # ADD on top of the previous solve's constant, drifting the
        # reported objective upward by N×constant after N solves.
        # `_baseline_objective_constant` was captured BEFORE the LP run
        # (see baseline-capture comment further up the file) so the
        # subtract-then-add here is idempotent across re-solves.
        if nonext_capacity_constant != 0.0:
            try:
                # Reset to the pre-wrapper baseline FIRST so re-solves
                # don't accumulate. The baseline lives on `n` so it
                # survives between the wrapper-factory call and the
                # closure invocation (PyPSA may call the closure once
                # per `n.optimize` cycle).
                baseline = float(getattr(n, "_baseline_objective_constant", 0.0) or 0.0)
                n._objective_constant = baseline + nonext_capacity_constant
                _emit(
                    f"Implied capacity constant for non-extendable renewables: "
                    f"+€{nonext_capacity_constant:,.0f} added to reported objective "
                    f"(baseline €{baseline:,.0f} preserved)."
                )
            except Exception as exc:
                _emit(f"Couldn't inject implied capacity constant: {exc}")

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        curtailment_cost_fn(n, snapshots)

    return wrapper


# Healthy band for the geometric mean of objective cost coefficients. Inside
# this band the model is well-conditioned and auto-scale is a no-op; outside,
# the recommended scale pulls the geomean back toward _COND_TARGET.
_COND_GEOMEAN_LOW = 1e-1
_COND_GEOMEAN_HIGH = 1e4
_COND_TARGET = 1e2
# Warn when the coefficient spread exceeds this many orders of magnitude — a
# uniform objective scale CANNOT fix spread (only HiGHS's internal per-element
# scaling / normalising the outlier costs can), so the diagnostic calls it out.
_COND_SPREAD_WARN_LOG10 = 9.0


def _objective_conditioning(network: "pypsa.Network") -> dict | None:
    """
    Summarise the magnitude spread of the LP's objective cost coefficients.

    The objective is roughly ``Σ weight·marginal_cost·dispatch +
    Σ capital_cost·capacity``, so the per-variable coefficient magnitudes are
    ``|marginal_cost|·max_weight`` (operating) and ``|capital_cost|`` on
    extendable assets (investment). A very wide spread, or a geometric mean far
    from O(1–1e3), is what trips solver "numerical trouble" / "objective too
    large" warnings.

    Returns ``None`` when there are fewer than two nonzero coefficients (nothing
    to condition), else a dict with ``count / min / max / geomean /
    spread_log10 / recommended_scale``. ``recommended_scale`` is a power of 10
    (1.0 when already healthy) that, applied to the whole objective, pulls the
    geometric mean toward ``_COND_TARGET``. Pure + read-only.
    """
    import numpy as _np

    coeffs: list[float] = []
    try:
        sw = network.snapshot_weightings
        max_w = float(sw["objective"].abs().max()) if "objective" in sw else 1.0
    except Exception:
        max_w = 1.0
    if not _np.isfinite(max_w) or max_w <= 0:
        max_w = 1.0

    def _nonzero_abs(df, col):
        if df is None or getattr(df, "empty", True) or col not in df.columns:
            return None
        v = df[col].abs()
        return v[(v > 0) & _np.isfinite(v)]

    # Operating coefficients: |marginal_cost| × representative weight.
    for comp in ("generators", "links", "storage_units", "stores"):
        v = _nonzero_abs(getattr(network, comp, None), "marginal_cost")
        if v is not None and not v.empty:
            coeffs.extend((v * max_w).tolist())
    # Investment coefficients: |capital_cost| on EXTENDABLE assets only.
    for comp, ext_col in (("generators", "p_nom_extendable"),
                          ("links", "p_nom_extendable"),
                          ("lines", "s_nom_extendable"),
                          ("storage_units", "p_nom_extendable"),
                          ("stores", "e_nom_extendable")):
        df = getattr(network, comp, None)
        if df is None or getattr(df, "empty", True) or "capital_cost" not in df.columns:
            continue
        sub = df[df[ext_col]] if ext_col in df.columns else df
        v = _nonzero_abs(sub, "capital_cost")
        if v is not None and not v.empty:
            coeffs.extend(v.tolist())

    coeffs = [c for c in coeffs if c > 0 and _np.isfinite(c)]
    if len(coeffs) < 2:
        return None

    arr = _np.asarray(coeffs, dtype=float)
    cmin, cmax = float(arr.min()), float(arr.max())
    geomean = float(_np.exp(_np.log(arr).mean()))
    spread_log10 = float(_np.log10(cmax / cmin)) if cmin > 0 else float("inf")

    if geomean > 0 and _np.isfinite(geomean) and (
        geomean < _COND_GEOMEAN_LOW or geomean > _COND_GEOMEAN_HIGH
    ):
        rec = 10.0 ** round(_np.log10(_COND_TARGET / geomean))
        rec = float(min(max(rec, 1e-6), 1e6))
    else:
        rec = 1.0

    return {
        "count": len(coeffs),
        "min": cmin,
        "max": cmax,
        "geomean": geomean,
        "spread_log10": spread_log10,
        "recommended_scale": rec,
    }


def _log_objective_conditioning(network: "pypsa.Network", log_queue) -> None:
    """
    Emit a one-line ``[NUMERICS]`` pre-solve report on the objective's
    conditioning, and a WARN with an actionable remedy when it's poor.

    Read-only — never mutates the network or the LP. Cheap (vectorised over the
    component DataFrames). Runs AFTER modelling assumptions so VOLL slacks /
    vintage rows are included in the magnitude analysis. Writes raw ``[NUMERICS]``
    lines (the tag convention shared with ``[VALIDATION]`` / ``[OBJ-SCALE]``) so
    the frontend can colour the WARN amber.
    """
    if log_queue is None:
        return
    try:
        cond = _objective_conditioning(network)
    except Exception:
        return  # diagnostics must never break a solve
    if cond is None:
        return

    def _put(msg: str) -> None:
        _safe_log(log_queue, msg)

    _put(
        f"[NUMERICS] Objective cost coefficients: {cond['count']} terms, "
        f"range {cond['min']:.3g}…{cond['max']:.3g} "
        f"(geom. mean {cond['geomean']:.3g}, spread {cond['spread_log10']:.1f} "
        "orders of magnitude)."
    )
    poor_geomean = cond["recommended_scale"] != 1.0
    wide_spread = cond["spread_log10"] > _COND_SPREAD_WARN_LOG10
    if poor_geomean or wide_spread:
        bits = []
        if wide_spread:
            bits.append(
                "coefficients span a very wide magnitude range — normalising "
                "outlier cost/capacity values is the most robust fix"
            )
        if poor_geomean:
            bits.append(
                f"set user_objective_scale ≈ {cond['recommended_scale']:g} "
                "(or enable Auto-scale objective) to recentre the magnitudes"
            )
        _put("[NUMERICS] WARN: the objective may be ill-conditioned — "
             + "; ".join(bits) + ".")


def _wrap_with_objective_scale(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Apply `cfg.user_objective_scale` to the linopy model's objective.

    Runs AFTER `n.optimize` builds the model but BEFORE `model.solve()` —
    PyPSA's ``extra_functionality`` callback is the canonical hook for that
    boundary. Multiplies ``model.objective`` by ``scale`` in place, which is
    invariant to the LP's argmin (only the solver's INTERNAL numerics shift).

    Post-solve responsibilities (rescaling reported `n.objective` and LP
    duals back to user-facing units) live in `_rescale_results_for_objective`,
    invoked from the outer solve driver after `n.optimize(...)` returns.

    Returns ``user_fn`` unchanged when ``scale ~= 1.0`` so the LP graph
    isn't touched for runs that don't need this.
    """
    import math as _math

    raw = getattr(cfg, "user_objective_scale", 1.0)
    # Auto-scale: when enabled, the solver picks the scale itself from the
    # model's cost-coefficient spread, overriding the manual value. A no-op
    # (recommended_scale == 1.0) for well-conditioned models, so it's safe to
    # leave on. Computed on the pre-modelling-assumptions network — the base
    # cost structure already fixes the power-of-10 decision; the later VOLL/
    # vintage rows don't shift it. Falls back to the manual value if the
    # analyser can't run.
    if getattr(cfg, "auto_objective_scale", False):
        try:
            cond = _objective_conditioning(network)
        except Exception:
            cond = None
        if cond is not None:
            raw = cond["recommended_scale"]
            if log_queue is not None and raw != 1.0:
                try:
                    log_queue.put(
                        f"[NUMERICS] Auto-scale engaged: objective × {raw:g} "
                        f"(geom. mean coeff {cond['geomean']:.3g} → ~{_COND_TARGET:g}); "
                        "reported € and duals are divided back post-solve."
                    )
                except Exception:
                    pass
    try:
        scale = float(raw)
    except (TypeError, ValueError):
        scale = 1.0
    if not _math.isfinite(scale) or scale <= 0.0:
        if log_queue is not None:
            try:
                log_queue.put(
                    f"[OBJ-SCALE] Ignoring invalid user_objective_scale={raw!r}: "
                    "must be a positive finite number. Using 1.0."
                )
            except Exception:
                pass
        scale = 1.0
    # Stash the scale on the network so the post-solve rescaler can find
    # it. Survives the `try/finally` restore because we ALSO clear it
    # explicitly in `_rescale_results_for_objective`. Use a sentinel
    # attribute name that won't collide with anything PyPSA owns.
    network._pypsa_gui_objective_scale = scale  # type: ignore[attr-defined]

    if abs(scale - 1.0) < 1e-12:
        return user_fn

    def _emit(msg: str) -> None:
        _safe_log(log_queue, msg)

    def objective_scale_fn(n, snapshots):
        try:
            model = getattr(n, "model", None)
            if model is None or not hasattr(model, "objective"):
                _emit("[OBJ-SCALE] Model.objective unavailable — scaling skipped.")
                return
            try:
                model.objective.expression = model.objective.expression * scale
            except AttributeError:
                # linopy versions where `.objective` is itself the expression.
                try:
                    model.objective = model.objective * scale
                except Exception as exc:
                    _emit(f"[OBJ-SCALE] Could not scale objective: {exc}")
                    return
            _emit(
                f"[OBJ-SCALE] LP objective × {scale:g} for numerical conditioning "
                "(optimal x* is invariant; n.objective and duals are divided back "
                "post-solve to keep user-facing € unchanged)."
            )
        except Exception as exc:
            _emit(f"[OBJ-SCALE] Scaling failed silently: {exc}")

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        objective_scale_fn(n, snapshots)

    return wrapper


def _rescale_results_for_objective(network: "pypsa.Network", log_queue=None) -> None:
    """
    Reverse the LP-objective scaling on reported result fields.

    Called once per solve, right after `n.optimize(...)` returns (success
    OR failure — duals from a failed solve are usually empty, but the
    call is no-op safe). Multiplies every value the user reads back in
    EUR / €/MWh by ``1/scale`` so the GUI labels stay correct.

    Touches:
      • n.objective       — scalar
      • buses_t.marginal_price
      • lines_t.mu_upper, mu_lower
      • transformers_t.mu_upper, mu_lower
      • global_constraints.mu (per-constraint shadow price)
    """
    import math as _math

    scale = getattr(network, "_pypsa_gui_objective_scale", 1.0)
    try:
        scale_f = float(scale)
    except (TypeError, ValueError):
        scale_f = 1.0
    # Clear the sentinel regardless so the next solve starts fresh.
    try:
        del network._pypsa_gui_objective_scale
    except AttributeError:
        pass
    if not _math.isfinite(scale_f) or scale_f <= 0.0 or abs(scale_f - 1.0) < 1e-12:
        return

    inv = 1.0 / scale_f

    def _emit(msg: str) -> None:
        _safe_log(log_queue, msg)

    # n.objective is a read-only property; the underlying value lives in
    # `_objective`. Write to that field directly so the property reflects
    # the rescaled total.
    try:
        cur = getattr(network, "_objective", None)
        if cur is None:
            cur = getattr(network, "objective", None)
        if cur is not None:
            network._objective = float(cur) * inv
    except Exception as exc:
        _emit(f"[OBJ-SCALE] post-solve n.objective rescale failed: {exc}")
    # `_objective_constant` carries the curtailment-wrapper's
    # non-extendable capacity term — added by the wrapper at LP-build
    # time, BEFORE the objective scale multiplication runs. PyPSA's
    # property `n.objective` returns `_objective + _objective_constant`,
    # so if we only rescale `_objective` here and leave the constant
    # unscaled, the displayed total mixes scaled (variable part) with
    # unscaled (constant part) units. Rescale both so the user-facing
    # total stays in € regardless of `user_objective_scale`.
    try:
        cur_const = getattr(network, "_objective_constant", None)
        if cur_const is not None:
            network._objective_constant = float(cur_const) * inv
    except Exception as exc:
        _emit(f"[OBJ-SCALE] post-solve _objective_constant rescale failed: {exc}")
    # Dual-bearing _t accessors:
    for comp_attr, attrs in (
        ("buses_t",       ["marginal_price"]),
        ("lines_t",       ["mu_upper", "mu_lower"]),
        ("transformers_t", ["mu_upper", "mu_lower"]),
        ("links_t",       ["mu_upper", "mu_lower"]),
    ):
        acc = getattr(network, comp_attr, None)
        if acc is None:
            continue
        for a in attrs:
            df = getattr(acc, a, None)
            if df is None or getattr(df, "empty", True):
                continue
            try:
                setattr(acc, a, df * inv)
            except Exception as exc:
                _emit(f"[OBJ-SCALE] could not rescale {comp_attr}.{a}: {exc}")
    # Global constraint duals (`mu`) — €/(constraint-unit), e.g. €/tCO2 on
    # primary_energy CO2 caps.
    try:
        gc = getattr(network, "global_constraints", None)
        if gc is not None and not gc.empty and "mu" in gc.columns:
            network.global_constraints["mu"] = gc["mu"] * inv
    except Exception as exc:
        _emit(f"[OBJ-SCALE] could not rescale global_constraints.mu: {exc}")
    _emit(
        f"[OBJ-SCALE] Rescaled n.objective + LP duals by 1/{scale_f:g} "
        f"= {inv:g} (back to user-facing units)."
    )
