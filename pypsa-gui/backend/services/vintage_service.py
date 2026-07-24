"""
Per-period capacity bounds for multi-investment-period capacity expansion.

PyPSA stores `p_nom_min` / `p_nom_max` (and the s/e/r analogues) as scalar
attributes on the static component DataFrame — one value per asset, not
indexed by investment period. To express "the optimiser can build at most
50 MW in 2026 but up to 300 MW in 2030", PyPSA's native idiom is to create
one extendable vintage row per period with `build_year=period` and its own
bounds.

This service keeps the user-facing model simple — one logical asset, with
per-period bounds entered through a small dict — and expands those bounds
into vintage rows transiently inside `_apply_modelling_assumptions` right
before `n.optimize()`. The expansion is reverted on exit so the on-disk
network never carries the vintage clones.

Storage layout: `n.meta["vintage_bounds"]` is a dict
    { component_class: { asset_name: { period_str: { p_nom_min, p_nom_max } } } }

`component_class` is the PyPSA class name ("Generator", "StorageUnit",
"Store", "Link"). `period_str` is the year as a string (JSON object keys are
always strings). Either bound may be omitted; missing → uses the asset's
existing scalar value. Periods without an entry use the scalar value too.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Callable
from typing import Any

import pandas as pd

from services.pypsa_service import PyPSAService

# Component-class → static-attribute-name → nom-field triple.
# `pnom_field` is the capacity-decision attribute the user is bounding
# (Generator uses p_nom, Line/Transformer/Link share p/s_nom semantics —
# we support Generator, StorageUnit, Store, Link initially because those
# are the typical capacity-expansion targets in the GUI).
SUPPORTED_COMPONENTS: dict[str, dict[str, str]] = {
    "Generator":    {"attr": "generators",    "pnom": "p_nom"},
    "StorageUnit":  {"attr": "storage_units", "pnom": "p_nom"},
    "Store":        {"attr": "stores",        "pnom": "e_nom"},
    "Link":         {"attr": "links",         "pnom": "p_nom"},
    # Transmission assets: PyPSA uses s_nom for apparent-power capacity on
    # Lines / Transformers. Each vintage row models a parallel addition on
    # the same bus0–bus1 corridor with its own decision variable — the
    # standard capacity-expansion idiom for transmission reinforcement.
    "Line":         {"attr": "lines",         "pnom": "s_nom"},
    "Transformer":  {"attr": "transformers",  "pnom": "s_nom"},
}


# ── Storage helpers (read/write the n.meta dict) ────────────────────────────

def _ensure_meta_bucket(n) -> dict:
    """
    Return the live vintage_bounds dict on n.meta, creating it if absent.

    PyPSA's n.meta survives `n.export_to_netcdf` → `pypsa.Network(...)`
    round-trips (it's JSON-serialised into a single netcdf attribute), so no
    extra persistence wiring is needed: writing here is enough.
    """
    if not hasattr(n, "meta") or n.meta is None:
        n.meta = {}
    bucket = n.meta.get("vintage_bounds")
    if not isinstance(bucket, dict):
        bucket = {}
        n.meta["vintage_bounds"] = bucket
    return bucket


def get_all_bounds(n) -> dict:
    """Return a deep copy of the full vintage_bounds dict — safe to JSON."""
    bucket = _ensure_meta_bucket(n)
    return copy.deepcopy(bucket)


def set_bounds_for_asset(
    n,
    component_class: str,
    name: str,
    bounds: dict[str, dict[str, float | None]],
) -> dict:
    """
    Overwrite the per-period bounds for a single asset.

    `bounds` is `{period_str: {p_nom_min?: float, p_nom_max?: float}}`.
    Empty dict (or all-empty rows) clears the entry. Returns the updated
    bounds dict for this asset (may be empty).
    """
    if component_class not in SUPPORTED_COMPONENTS:
        raise ValueError(f"Unsupported component class for vintage bounds: {component_class}")
    bucket = _ensure_meta_bucket(n)
    # Drop rows that have neither min nor max — empty rows are noise.
    cleaned: dict[str, dict[str, float]] = {}
    for period, vals in (bounds or {}).items():
        if not isinstance(vals, dict):
            continue
        row: dict[str, float] = {}
        for k in ("p_nom_min", "p_nom_max"):
            v = vals.get(k)
            if v is None:
                continue
            try:
                row[k] = float(v)
            except (TypeError, ValueError):
                continue
        if row:
            cleaned[str(period)] = row
    by_class = bucket.setdefault(component_class, {})
    if cleaned:
        by_class[name] = cleaned
    else:
        by_class.pop(name, None)
        if not by_class:
            bucket.pop(component_class, None)
    return cleaned


def delete_bounds_for_asset(n, component_class: str, name: str) -> bool:
    """Remove all per-period bounds for an asset. Returns True if anything was removed."""
    bucket = _ensure_meta_bucket(n)
    by_class = bucket.get(component_class)
    found = bool(by_class and name in by_class)
    if found:
        del by_class[name]
        if not by_class:
            bucket.pop(component_class, None)
    # Also drop any stale solve-time breakdown so the Capacity Expansion view
    # doesn't keep showing the previous run's vintages after the user cleared
    # the bounds.
    results_root = (n.meta or {}).get("vintage_results")
    if isinstance(results_root, dict):
        by_class_r = results_root.get(component_class)
        if isinstance(by_class_r, dict) and name in by_class_r:
            del by_class_r[name]
            if not by_class_r:
                results_root.pop(component_class, None)
            found = True
    return found


def rename_asset(n, component_class: str, old_name: str, new_name: str) -> None:
    """
    Re-key any saved bounds AND any stored vintage results on rename.
    Quiet no-op if no entry exists.
    """
    if old_name == new_name:
        return
    bucket = _ensure_meta_bucket(n)
    by_class = bucket.get(component_class)
    if by_class and old_name in by_class:
        by_class[new_name] = by_class.pop(old_name)
    results_root = (n.meta or {}).get("vintage_results")
    if isinstance(results_root, dict):
        by_class_r = results_root.get(component_class)
        if isinstance(by_class_r, dict) and old_name in by_class_r:
            by_class_r[new_name] = by_class_r.pop(old_name)


# ── Apply-at-solve-time ─────────────────────────────────────────────────────

def apply_vintage_bounds(
    n,
    undo_actions: list,
    phase: Callable[[str], None],
) -> int:
    """
    Expand each asset that has per-period bounds into one extendable vintage
    row per period. Returns the number of vintages created.

    Preconditions: caller has already set up `n.investment_periods` (this is
    step 7 in `_apply_modelling_assumptions`, after step 4 sets the periods).
    No-op when (a) no bounds saved, (b) flat snapshots, (c) no investment
    periods configured.

    Undo strategy: each created vintage is removed in restore(); the original
    asset's `p_nom_extendable` flag (which we flip to False so it doesn't
    *also* get sized) is restored via the "col" undo entry.
    """
    bucket = _ensure_meta_bucket(n)
    if not bucket:
        return 0
    if not isinstance(n.snapshots, pd.MultiIndex):
        return 0
    try:
        periods = sorted(int(p) for p in n.investment_periods)
    except (TypeError, ValueError):
        periods = []
    if not periods:
        return 0

    # Clear stale breakdown from previous solves — the user may have removed
    # bounds for some assets since last solve, and the Capacity Expansion view
    # should only show vintages for assets we actually expanded THIS run.
    if isinstance(n.meta, dict):
        n.meta["vintage_results"] = {}

    total_added = 0
    # Separately counts Line/Transformer assets that took the single-extendable
    # path — these aren't really "vintages" so the summary message distinguishes
    # them. Keeps the log accurate when the user has a mix of vintage and
    # single-extendable assets.
    single_extendable_assets = 0
    summary: list[str] = []
    for component_class, by_asset in bucket.items():
        spec = SUPPORTED_COMPONENTS.get(component_class)
        if spec is None:
            continue
        attr = spec["attr"]
        pnom = spec["pnom"]
        ext_col = f"{pnom}_extendable"
        min_col = f"{pnom}_min"
        max_col = f"{pnom}_max"
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        # Input-only column allow-list — PyPSA's `n.add()` rejects unknown
        # kwargs and writing to OUTPUT columns (p_nom_opt, p, mu_*) at add
        # time leaks stale solver state from the previous LP into the new
        # vintages. Use PyPSA's defaults metadata to filter.
        #
        # AND include custom GUI-added columns (curtailment_cost, etc.) by
        # treating any column on the parent's DataFrame that isn't listed in
        # PyPSA's defaults as a custom input column. Without this, vintages
        # silently lose curtailment_cost — the wrapper then sees only the
        # parent (which is non-extendable, so the wrapper skips it) and
        # NEVER applies the curtailment penalty to the actual sizing
        # variables. Result: LP over-builds renewables freely.
        input_cols: set[str] = set()
        try:
            comp_defaults = getattr(n.components, attr).defaults
            mask = comp_defaults["status"].astype(str).str.startswith("Input", na=False)
            input_cols = set(comp_defaults.index[mask])
            # Custom columns: present on df but absent from PyPSA's defaults.
            known_defaults = set(comp_defaults.index)
            for c in df.columns:
                if c not in known_defaults:
                    input_cols.add(c)
        except Exception:
            input_cols = set(df.columns)
        # Line / Transformer get a different treatment than other components.
        # Adding parallel vintage rows with their own `x` and `s_nom_max`
        # was producing artificial flow-distribution bottlenecks:
        #   • Same-x parallel lines force DC PF to split flow inversely
        #     with susceptance, so corridor capacity is limited to
        #     `N × min(s_nom)` — adding vintage capacity can REDUCE the
        #     usable corridor MW.
        #   • Scaling vintage x by `s_nom_max` (the LP variable's ceiling)
        #     is approximate and tends to over- or under-weight the parent.
        #   • Cumulative ghost capacity: vintages frozen in early periods
        #     stay forever (lifetime=25), even after no longer needed.
        # Switch transmission expansion to a single extendable line:
        #     • Parent stays in-place as a single LP variable (s_nom in
        #       [s_nom_min, s_nom_max])
        #     • Per-period bounds are summed into ONE horizon bound:
        #         s_nom_max = parent.s_nom + Σ over periods (p_nom_max)
        #         s_nom_min = parent.s_nom + Σ over periods (p_nom_min)
        #     • The LP picks ONE optimal corridor size for the whole horizon
        #       — physically additive, no impedance distortion, no ghost
        #       capacity, and ~N fewer LP variables vs. the vintage path.
        # Trade-off: line build can no longer be phased per period. The
        # whole expansion is committed at horizon start. Per-period CAPEX
        # budgets (capex_budget_per_period) on lines now apply against the
        # parent's build_year only.
        if component_class in ("Line", "Transformer"):
            for asset_name, periods_dict in by_asset.items():
                if asset_name not in df.index:
                    continue
                # Aggregate per-period bounds into a single horizon-wide
                # min/max. Missing entries contribute 0. Caps the sums by
                # the asset's existing s_nom_max if user already set one.
                sum_min = 0.0
                sum_max = 0.0
                any_bound = False
                for period in periods:
                    pkey = str(period)
                    bnds = periods_dict.get(pkey) or {}
                    if "p_nom_min" in bnds and bnds["p_nom_min"] is not None:
                        try:
                            sum_min += float(bnds["p_nom_min"])
                            any_bound = True
                        except (TypeError, ValueError):
                            pass
                    if "p_nom_max" in bnds and bnds["p_nom_max"] is not None:
                        try:
                            sum_max += float(bnds["p_nom_max"])
                            any_bound = True
                        except (TypeError, ValueError):
                            pass
                if not any_bound:
                    continue
                parent_s_nom = float(df.at[asset_name, pnom] or 0.0)
                new_min = parent_s_nom + sum_min
                new_max = parent_s_nom + sum_max if sum_max > 0 else float("inf")
                # Flip extendable + write bounds. Snapshot originals so the
                # restore path puts them back exactly as the user set them.
                orig_ext = bool(df.at[asset_name, ext_col]) if ext_col in df.columns else False
                orig_min = float(df.at[asset_name, min_col] or 0.0) if min_col in df.columns else 0.0
                orig_max = (
                    float(df.at[asset_name, max_col])
                    if max_col in df.columns and math.isfinite(df.at[asset_name, max_col])
                    else float("inf")
                )
                df.at[asset_name, ext_col] = True
                df.at[asset_name, min_col] = new_min
                df.at[asset_name, max_col] = new_max
                undo_actions.append((
                    "col", attr, ext_col,
                    pd.Index([asset_name]), pd.Series({asset_name: orig_ext}),
                ))
                undo_actions.append((
                    "col", attr, min_col,
                    pd.Index([asset_name]), pd.Series({asset_name: orig_min}),
                ))
                undo_actions.append((
                    "col", attr, max_col,
                    pd.Index([asset_name]), pd.Series({asset_name: orig_max}),
                ))
                summary.append(
                    f"{component_class} '{asset_name}': single extendable, "
                    f"s_nom in [{new_min:.0f}, "
                    f"{'inf' if not math.isfinite(new_max) else f'{new_max:.0f}'}] "
                    f"MW (parent {parent_s_nom:.0f} + sum period max {sum_max:.0f})"
                )
                # Count toward the summary, but DON'T claim it as a "vintage" —
                # transmission single-extendables show up as separate entries
                # in the summary string so the user sees them as line/transformer
                # build-outs, not as N vintage rows.
                total_added += 1
                single_extendable_assets += 1
            continue  # skip the vintage-row path below for this component class

        for asset_name, periods_dict in by_asset.items():
            if asset_name not in df.index:
                continue
            # Template = the full attribute row, dropped to a plain dict so we
            # can hand it to n.add() per vintage with overrides per period.
            row = df.loc[asset_name].to_dict()
            # Disable the parent so PyPSA doesn't ALSO create a non-vintage
            # capacity variable — the vintages own all the sizing now.
            if ext_col in df.columns:
                orig_ext = bool(df.at[asset_name, ext_col])
                if orig_ext:
                    df.at[asset_name, ext_col] = False
                    undo_actions.append((
                        "col", attr, ext_col,
                        pd.Index([asset_name]), pd.Series({asset_name: orig_ext}),
                    ))
            added_for_asset: list[str] = []
            for period in periods:
                pkey = str(period)
                bnds = periods_dict.get(pkey) or {}
                # No bound for this period → skip the vintage (no sizing
                # variable created for that period either). Users who want
                # the asset available in a period must specify at least an
                # upper bound (or 0 to forbid).
                if "p_nom_min" not in bnds and "p_nom_max" not in bnds:
                    continue
                vintage_name = f"{asset_name}@{period}"
                if vintage_name in df.index:
                    # Pre-existing collision — leave it alone, the user (or a
                    # previous incomplete restore) is managing it manually.
                    continue
                kwargs: dict[str, Any] = {}
                # Carry over scalar INPUT fields only — output columns
                # (p_nom_opt, p, mu_*) belong to the previous solve and
                # mustn't leak into a fresh vintage. Skip NaN / None too so
                # PyPSA's own defaults take over.
                for k, v in row.items():
                    if k not in input_cols:
                        continue
                    if k in (ext_col, min_col, max_col, "build_year", "lifetime", pnom):
                        continue
                    if isinstance(v, float) and not math.isfinite(v):
                        continue
                    if v is None:
                        continue
                    kwargs[k] = v
                kwargs[ext_col] = True
                kwargs["build_year"] = period
                # Vintage starts from zero installed — its sizing is fully
                # bounded by p_nom_min / p_nom_max below. Without this, a
                # parent with existing capacity (e.g. p_nom=80) would
                # duplicate that 80 MW into every vintage.
                kwargs[pnom] = 0.0
                # Lifetime: inherit the original if finite, otherwise default
                # to "covers the rest of the horizon" so the vintage is active
                # from build_year through the last period.
                orig_lifetime = row.get("lifetime")
                if isinstance(orig_lifetime, (int, float)) and math.isfinite(orig_lifetime) and orig_lifetime > 0:
                    kwargs["lifetime"] = float(orig_lifetime)
                else:
                    kwargs["lifetime"] = float(periods[-1] - period + 1)
                if "p_nom_min" in bnds:
                    kwargs[min_col] = float(bnds["p_nom_min"])
                if "p_nom_max" in bnds:
                    kwargs[max_col] = float(bnds["p_nom_max"])

                # ── Line / Transformer specifics: scale reactance with
                #    capacity. The user expectation for a transmission
                #    vintage is "add useful corridor capacity". Inheriting
                #    `x` directly from the parent (current default) means
                #    every parallel vintage has identical impedance — DC
                #    power-flow forces flow to split 1/x equally across
                #    parallel branches, so the corridor's binding capacity
                #    becomes N × min(s_nom). A 100-MW vintage attached to
                #    a 500-MW parent then CAPS the corridor at 2×100 = 200
                #    MW instead of adding 100 MW useful (effectively
                #    REDUCING corridor capacity).
                #
                #    Physical fix: a smaller-rating parallel conductor has
                #    proportionally higher reactance per length:
                #        x_vintage = x_parent × s_nom_parent / s_nom_max
                #    With this scaling, flow splits proportionally to
                #    s_nom — both lines bind at their own s_nom
                #    simultaneously, and total corridor capacity =
                #    s_nom_parent + s_nom_max (truly additive). The LP
                #    then has the right economic signal: bigger vintage
                #    s_nom = more corridor capacity, proportional cost.
                #
                #    Applies to Line and Transformer only (the two
                #    transmission types with `x` impedance). Skipped when
                #    s_nom_max is missing or zero (defaults to inheriting
                #    parent's x — fallback to old behaviour).
                if component_class in ("Line", "Transformer"):
                    parent_s_nom = row.get("s_nom")
                    vintage_s_nom_max = bnds.get("p_nom_max")
                    if (
                        isinstance(parent_s_nom, (int, float))
                        and math.isfinite(parent_s_nom) and parent_s_nom > 0
                        and isinstance(vintage_s_nom_max, (int, float))
                        and math.isfinite(vintage_s_nom_max) and vintage_s_nom_max > 0
                    ):
                        scale = float(parent_s_nom) / float(vintage_s_nom_max)
                        # x: reactance (DC PF flow-distribution driver). r:
                        # resistance (only used when transmission_losses is
                        # on, but cheap to scale consistently).
                        for impedance_col in ("x", "r"):
                            parent_val = row.get(impedance_col)
                            if (
                                isinstance(parent_val, (int, float))
                                and math.isfinite(parent_val) and parent_val > 0
                            ):
                                kwargs[impedance_col] = float(parent_val) * scale

                # Mark the row as transient BEFORE n.add() so a concurrent
                # GET hitting between n.add() and a later mark would
                # still see the row filtered. Marking an absent row is
                # harmless — the filter just removes a name that isn't
                # in df.index, no-op. If n.add() then fails we unmark
                # in the except block so the registry stays consistent.
                PyPSAService.mark_transient(component_class, vintage_name)
                try:
                    n.add(component_class, vintage_name, **kwargs)
                except Exception as exc:
                    PyPSAService.unmark_transient(component_class, vintage_name)
                    phase(f"Vintage expansion: failed to add {vintage_name}: {exc}")
                    continue
                # Belt-and-suspenders: write CUSTOM columns (those not in
                # PyPSA's defaults) directly onto the new vintage row, in
                # case PyPSA's n.add() silently dropped them as unknown
                # kwargs. The kwarg path above is preferred (validation,
                # dtype coercion), but a direct DataFrame write guarantees
                # the value lands regardless of n.add()'s strictness.
                try:
                    known_defaults = set(
                        getattr(n.components, attr).defaults.index
                    )
                    df_now = getattr(n, attr)
                    for k, v in kwargs.items():
                        if k in known_defaults:
                            continue
                        if k not in df_now.columns:
                            continue
                        if vintage_name not in df_now.index:
                            continue
                        df_now.at[vintage_name, k] = v
                except Exception:
                    # Non-critical — the kwarg path covered the typical case.
                    pass
                added_for_asset.append(vintage_name)
                total_added += 1

            # Replicate time-varying attributes (p_max_pu, p_min_pu, etc.) by
            # reading the parent's _t columns AFTER add() — n.add() initialises
            # the vintage's default constant; if the parent has a profile we
            # need to clone it onto each vintage column too. Track the cloned
            # (input) cols per vintage so the post-solve aggregation step can
            # tell them apart from solver outputs (p, p_dispatch, e, …).
            #
            # On exception inside `_clone_time_varying_to_vintage`, any
            # vintages already added at line 452 would be left in the
            # network WITHOUT a registered undo (the
            # `_capture_and_drop_vintages` registration happens further
            # down at line 636). Catch + roll back here so the restore
            # chain doesn't leak orphan vintage rows. Final-QA-flagged 🟡.
            cloned_per_vintage: dict[str, set[str]] = {}
            try:
                for v_name in added_for_asset:
                    cloned_per_vintage[v_name] = _clone_time_varying_to_vintage(
                        n, attr, asset_name, v_name, undo_actions, input_cols,
                    )
            except Exception as exc:
                phase(
                    f"Vintage expansion: clone failed for {asset_name!r} ({exc}); "
                    f"rolling back {len(added_for_asset)} vintage row(s)."
                )
                for v_name in added_for_asset:
                    try:
                        if v_name in getattr(n, attr).index:
                            n.remove(component_class, v_name)
                    except Exception:
                        pass
                    PyPSAService.unmark_transient(component_class, v_name)
                total_added -= len(added_for_asset)
                added_for_asset.clear()
                continue

            # Queue the post-solve capture-then-drop. PyPSA writes `p_nom_opt`
            # and the output `_t` columns onto each vintage row after the LP;
            # restore() runs in reverse, so without this the vintages get
            # deleted and the user sees `parent.p_nom_opt = 0` (the parent ran
            # with extendable=False so its own p_nom_opt is just its fixed
            # p_nom). Aggregate vintage outputs → parent here, mirroring the
            # VOLL slack `_capture_and_remove_slacks` pattern.
            if added_for_asset:
                # Snapshot original parent fields BEFORE capture (capture runs
                # post-solve, by then PyPSA has overwritten the parent's _opt
                # column to its fixed p_nom). This breakdown is what the
                # CapacityExpansion view consumes to render one row per period.
                parent_initial_capacity = float(row.get(pnom, 0) or 0)
                vintage_meta_for_asset: list[dict[str, Any]] = []
                for v_name in added_for_asset:
                    period_for_v = int(v_name.split("@")[-1])
                    bnds_v = periods_dict.get(str(period_for_v)) or {}
                    vintage_meta_for_asset.append({
                        "vintage_name": v_name,
                        "build_year": period_for_v,
                        "p_nom_min": float(bnds_v.get("p_nom_min", 0.0)),
                        "p_nom_max": (
                            float(bnds_v["p_nom_max"])
                            if "p_nom_max" in bnds_v else None
                        ),
                    })

                def _capture_and_drop_vintages(
                    parent=asset_name,
                    names=tuple(added_for_asset),
                    cls=component_class,
                    a=attr,
                    p_field=pnom,
                    cloned=cloned_per_vintage,
                    vintage_meta=vintage_meta_for_asset,
                    parent_initial=parent_initial_capacity,
                ):
                    cur_df = getattr(n, a, None)
                    if cur_df is None:
                        return
                    # 1) Aggregate capacity onto the parent. Parent ran with
                    #    extendable=False so its own p_nom_opt is just its
                    #    fixed p_nom (existing installed capacity). Total
                    #    operational capacity = parent's existing p_nom +
                    #    sum of vintage p_nom_opt. Overwriting (instead of
                    #    adding) the existing p_nom would silently lose the
                    #    pre-existing capacity from every downstream KPI.
                    opt_col = f"{p_field}_opt"
                    per_vintage_opt: dict[str, float] = {}
                    # Prefer the freeze-time snapshot stored in the solver
                    # module's side-store over the live DataFrame's
                    # p_nom_opt — PyPSA's `n.optimize()` resets `n.meta`
                    # between myopic iterations, so the stash lives on the
                    # solver's per-thread freeze store
                    # (`solver_service._frozen_vintage_store()`). This
                    # closure runs inside `restore_modelling`, synchronously
                    # on the SAME solve worker thread that wrote the store,
                    # so the thread-local resolves to the writer's dict (B4's
                    # per-context locks allow concurrent solves on other
                    # threads — those get separate stores, no collision).
                    # The live read remains as a fallback for codepaths that
                    # bypass the freeze (e.g. non-myopic single-LP runs).
                    # Imported lazily inside the closure to avoid a
                    # solver_service ↔ vintage_service circular import.
                    try:
                        from services.solver_service import _frozen_vintage_store
                        frozen_store: dict[tuple[str, str], float] = _frozen_vintage_store()
                    except Exception:
                        frozen_store = {}
                    if opt_col in cur_df.columns and parent in cur_df.index:
                        parent_existing = float(cur_df.at[parent, p_field] or 0)
                        if not math.isfinite(parent_existing):
                            parent_existing = 0.0
                        total = parent_existing
                        for nm in names:
                            frozen_val = frozen_store.get((cls, str(nm)))
                            if frozen_val is not None and math.isfinite(frozen_val) and abs(frozen_val) > 1e-9:
                                per_vintage_opt[nm] = float(frozen_val)
                                total += float(frozen_val)
                                continue
                            if nm in cur_df.index:
                                v = cur_df.at[nm, opt_col]
                                if v is not None and not (isinstance(v, float) and math.isnan(v)) and abs(float(v)) > 1e-9:
                                    per_vintage_opt[nm] = float(v)
                                    total += float(v)
                                else:
                                    per_vintage_opt[nm] = 0.0
                            else:
                                per_vintage_opt[nm] = 0.0
                        # Dispatch-based fallback for Link vintages whose
                        # per-vintage p_nom_opt came out 0/-0.0. The LP
                        # constraint p[t] <= p_nom * p_max_pu[t] guarantees
                        # p_nom_opt >= max(|p|) / max(p_max_pu), so reading
                        # peak |p0| from the dispatch table gives a sound
                        # lower-bound recovery that matches the LP's actual
                        # sizing decision. This catches the PyPSA 1.1.2
                        # multi-port Link writeback bug where assign_solution
                        # writes -0.0 to p_nom_opt AND n.model.solution itself
                        # returns 0 for the vintage variable — both diagnosed
                        # by parallel Explore agents on 2026-05-25. Only
                        # applied to Links and only when dispatch is
                        # non-trivial; genuine "no build" vintages stay zero.
                        # Same dispatch-based recovery applies to any asset
                        # class where the LP-build solved with capacity that
                        # didn't propagate back to per-vintage p_nom_opt:
                        # - Link: dispatch on `p0` (canonical; multi-port writeback bug)
                        # - StorageUnit: dispatch on `p` (parent-only writeback observed
                        #   on Battery 1 2028 vintage 422 MW with all vintage p_nom_opt=0
                        #   yet dispatch peaks at 477 MW — same writeback pattern as Links)
                        # - Store / Generator: same logic, kept off until observed in the wild
                        _RECOVERY_DISPATCH_ATTR = {
                            "Link": "p0",
                            "StorageUnit": "p",
                            "Store": "p",
                            "Generator": "p",
                        }
                        if cls in _RECOVERY_DISPATCH_ATTR and any(
                            abs(per_vintage_opt.get(str(nm), 0.0)) < 1e-9 for nm in names
                        ):
                            dispatch_attr = _RECOVERY_DISPATCH_ATTR[cls]
                            dyn_t = getattr(n, f"{a}_t", None)
                            p0_t = getattr(dyn_t, dispatch_attr, None) if dyn_t is not None else None
                            p_max_pu_t = getattr(dyn_t, "p_max_pu", None) if dyn_t is not None else None

                            def _resolve_p_max_pu(_name: str) -> float:
                                pmpu = 1.0
                                if p_max_pu_t is not None and _name in p_max_pu_t.columns:
                                    try:
                                        sm = float(p_max_pu_t[_name].max())
                                        if math.isfinite(sm) and sm > 1e-9:
                                            return sm
                                    except Exception:
                                        pass
                                if "p_max_pu" in cur_df.columns and _name in cur_df.index:
                                    try:
                                        sv = float(cur_df.at[_name, "p_max_pu"])
                                        if math.isfinite(sv) and sv > 1e-9:
                                            return sv
                                    except (TypeError, ValueError):
                                        pass
                                return pmpu

                            if p0_t is not None and not p0_t.empty:
                                # Pass 1: try per-vintage dispatch columns
                                # (single-port behaviour — PyPSA writes
                                # dispatch under each vintage's name).
                                vintage_recovered = False
                                for nm in names:
                                    if abs(per_vintage_opt.get(str(nm), 0.0)) > 1e-9:
                                        continue
                                    if nm not in p0_t.columns:
                                        continue
                                    try:
                                        peak_abs = float(p0_t[nm].abs().max())
                                    except Exception:
                                        peak_abs = 0.0
                                    if not math.isfinite(peak_abs) or peak_abs < 1e-6:
                                        continue
                                    inferred = peak_abs / _resolve_p_max_pu(nm)
                                    if math.isfinite(inferred) and inferred > 1e-6:
                                        per_vintage_opt[nm] = inferred
                                        total += inferred
                                        vintage_recovered = True

                                # Pass 2: parent-dispatch fallback for the
                                # multi-port Link case where PyPSA writes
                                # dispatch to the PARENT name (not vintage
                                # rows). Observed for heat pumps with bus2:
                                # the LP dispatches 10 MW on `dc_heatpump`
                                # itself despite parent being non-extendable
                                # with p_nom=0. Attribute the inferred
                                # capacity to the EARLIEST vintage (LP-typical
                                # build-once-and-keep behaviour).
                                if (not vintage_recovered
                                        and parent in p0_t.columns
                                        and vintage_meta
                                        and all(abs(per_vintage_opt.get(str(nm), 0.0)) < 1e-9 for nm in names)):
                                    try:
                                        parent_peak = float(p0_t[parent].abs().max())
                                    except Exception:
                                        parent_peak = 0.0
                                    if math.isfinite(parent_peak) and parent_peak > 1e-6:
                                        inferred_parent = parent_peak / _resolve_p_max_pu(parent)
                                        if math.isfinite(inferred_parent) and inferred_parent > 1e-6:
                                            earliest = min(vintage_meta, key=lambda e: e["build_year"])
                                            earliest_name = earliest["vintage_name"]
                                            per_vintage_opt[earliest_name] = inferred_parent
                                            total += inferred_parent

                                # Pass 3 (post-recovery sanity check): even
                                # when SOME vintage came back with a non-zero
                                # p_nom_opt (so Pass 1 / Pass 2 were skipped),
                                # the multi-port writeback bug can still
                                # under-report. Symptom: parent's dispatch
                                # peak exceeds (Σ reported vintage p_nom_opt)
                                # × max(p_max_pu). When detected, top up the
                                # EARLIEST vintage with the missing capacity
                                # so the parent's aggregate p_nom_opt matches
                                # the LP's actual sizing. Confirmed bug on
                                # 2026-05-26 verification: dc_heatpump
                                # reported 42.13 MW but dispatch peak 47.5 MW
                                # — 12.7% under-report invisible to current
                                # Passes 1/2.
                                if parent in p0_t.columns and vintage_meta:
                                    try:
                                        parent_peak = float(p0_t[parent].abs().max())
                                    except Exception:
                                        parent_peak = 0.0
                                    if math.isfinite(parent_peak) and parent_peak > 1e-6:
                                        # Total reported capacity (incl. any
                                        # Pass 1/2 recovery + parent existing).
                                        reported_total = total
                                        pmax = _resolve_p_max_pu(parent)
                                        inferred_total = parent_peak / pmax
                                        # 1% tolerance for float rounding (HiGHS
                                        # numerical precision on large LPs).
                                        if (math.isfinite(inferred_total)
                                                and inferred_total > reported_total * 1.01):
                                            deficit = inferred_total - reported_total
                                            earliest = min(vintage_meta, key=lambda e: e["build_year"])
                                            earliest_name = earliest["vintage_name"]
                                            per_vintage_opt[earliest_name] = (
                                                per_vintage_opt.get(earliest_name, 0.0) + deficit
                                            )
                                            total += deficit
                        cur_df.at[parent, opt_col] = total
                    # 1b) Persist the per-period breakdown into n.meta so the
                    #     frontend can render one row per (asset, period) build
                    #     year. Without this the parent's build_year column
                    #     keeps the user's original value (e.g. 2025) and the
                    #     three real 2026/2027/2028 vintages get hidden in the
                    #     sum on the parent row.
                    results_root = n.meta.setdefault("vintage_results", {})
                    by_class = results_root.setdefault(cls, {})
                    by_class[parent] = {
                        "initial_capacity": parent_initial,
                        "capacity_field": p_field,
                        "periods": [
                            {
                                "build_year": entry["build_year"],
                                "p_nom_opt": per_vintage_opt.get(entry["vintage_name"], 0.0),
                                "p_nom_min": entry["p_nom_min"],
                                "p_nom_max": entry["p_nom_max"],
                            }
                            for entry in vintage_meta
                        ],
                    }
                    # 2) Aggregate per-snapshot output columns. PyPSA writes
                    #    p / p_dispatch / p_store / state_of_charge / spill /
                    #    e onto each vintage column post-solve; we sum them
                    #    into the parent column (creating it if absent).
                    #    Cloned INPUT columns (p_max_pu, p_set, inflow) are
                    #    skipped — they already match the parent and summing
                    #    would double-count.
                    dyn_attr = f"{a}_t"
                    dyn = getattr(n, dyn_attr, None)
                    if dyn is not None:
                        for series_name in list(dyn.keys()):
                            ts = dyn[series_name]
                            if ts is None or ts.empty:
                                continue
                            vintage_cols = [nm for nm in names if nm in ts.columns]
                            if not vintage_cols:
                                continue
                            # Skip vintages whose (series_name) was a cloned
                            # INPUT — summing those would double-count the
                            # parent's profile. PyPSA-written OUTPUT columns
                            # (p, p_dispatch, e, …) aren't in the cloned set.
                            output_cols = [
                                c for c in vintage_cols
                                if series_name not in cloned.get(c, set())
                            ]
                            if not output_cols:
                                continue
                            agg = ts[output_cols].sum(axis=1)
                            if parent in ts.columns:
                                ts[parent] = ts[parent].fillna(0) + agg
                            else:
                                ts[parent] = agg
                    # 3) Now drop the vintage rows. After this the parent
                    #    row carries the aggregated results and the LP's
                    #    intermediate per-vintage rows are gone. Wrap each
                    #    n.remove individually so one bad row doesn't strand
                    #    the rest — the alternative (one unrecoverable
                    #    failure mid-loop) leaves orphans that the
                    #    Capacity-Bounds editor and downstream views all
                    #    have to handle defensively.
                    for nm in names:
                        if nm not in cur_df.index:
                            # Row already gone — clear the transient mark
                            # too so we don't leave it filtering a future
                            # row that happens to reuse the name.
                            PyPSAService.unmark_transient(cls, nm)
                            continue
                        try:
                            n.remove(cls, nm)
                            PyPSAService.unmark_transient(cls, nm)
                        except Exception:
                            # PyPSA's remove may raise on edge cases (e.g.
                            # already-detached static row). Skip and rely on
                            # the orphan-cleanup endpoint as a safety net.
                            # Leave the transient mark in place so the row
                            # (if it's still in the DataFrame) stays hidden
                            # from the UI until the next clear_transient().
                            continue
                undo_actions.append(("call", _capture_and_drop_vintages))
                summary.append(f"{component_class} '{asset_name}' → {len(added_for_asset)} vintages")

    if total_added:
        preview = ", ".join(summary[:3]) + ("…" if len(summary) > 3 else "")
        true_vintages = total_added - single_extendable_assets
        if single_extendable_assets > 0:
            phase(
                f"Capacity expansion configured: {true_vintages} per-period vintage(s) + "
                f"{single_extendable_assets} single-extendable Line/Transformer "
                f"({preview})."
            )
        else:
            phase(
                f"Vintage expansion: created {total_added} per-period capacity "
                f"vintage(s) ({preview}). Each vintage carries its own p_nom_min "
                f"/ p_nom_max for that investment period."
            )
    return total_added


def _clone_time_varying_to_vintage(
    n,
    attr: str,
    parent_name: str,
    vintage_name: str,
    undo_actions: list,
    input_cols: set[str],
) -> set[str]:
    """
    Copy INPUT per-snapshot columns on `n.<attr>_t` from parent → vintage so
    the vintage inherits the same profile (p_max_pu shape, p_set, etc.). Each
    cloned column is queued for removal on restore.

    OUTPUT columns from a previous solve (p, p_dispatch, state_of_charge, e,
    mu_*) are skipped — copying them would (a) leak stale solver state into
    the LP and (b) make the post-solve capture step treat those columns as
    "inputs already on parent" and miss the fresh sums, leaving the parent's
    dispatch frozen at the previous solve.

    Returns the set of `_t` series names actually cloned (always a subset of
    `input_cols`). The capture callback uses this set to skip aggregation of
    truly-cloned input columns.
    """
    dyn_attr = f"{attr}_t"
    dyn = getattr(n, dyn_attr, None)
    if dyn is None:
        return set()
    cloned_cols: list[tuple[str, str]] = []
    cloned_series: set[str] = set()
    for series_name in list(dyn.keys()):
        if series_name not in input_cols:
            continue
        ts = dyn[series_name]
        if ts is None or ts.empty:
            continue
        if parent_name not in ts.columns:
            continue
        if vintage_name in ts.columns:
            continue
        ts[vintage_name] = ts[parent_name]
        cloned_cols.append((series_name, vintage_name))
        cloned_series.add(series_name)
    if cloned_cols:
        def _drop_cloned_cols(cols=cloned_cols):
            d = getattr(n, dyn_attr, None)
            if d is None:
                return
            for sname, vname in cols:
                ts = d.get(sname)
                if ts is not None and vname in ts.columns:
                    del ts[vname]
        undo_actions.append(("call", _drop_cloned_cols))
    return cloned_series
