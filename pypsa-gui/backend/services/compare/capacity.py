"""
Capacity comparison: installed / expanded capacity and annuitised CAPEX per carrier and period.

Moved from `routers/compare.py`. `routers.compare` re-exports every name here
under the same name — or wraps it, where the function now takes the solver
config / result lookup as keyword-only arguments instead of reading router
state — so no call site changed. See the decomposition spec, Phase 3 addendum.

math / pandas are imported locally inside functions where the router did the
same; module-level imports below are only what the bodies reference at module
scope.
"""
from __future__ import annotations

from models.schemas import (
    CapacityComparison,
)
from services.compare.support import (
    _CLS_TO_ATTR,
    _bucket_add,
    _bucket_replicate_per_period,
    _classify_build_year,
    _periodized_lookup,
    _safe_capital_cost,
    _to_pv_dict,
)
from services import period_utils


def _compute_capacity_summary(n, periods, is_multi, has_solve, *, cfg=None) -> CapacityComparison:
    """
    Capacity-expansion side of the comparison payload.

    Returns two CAPEX views:
      • ``capex_meur_by_carrier`` — TOTAL annuitised CAPEX per carrier
        (existing + new) per period, matching ``/results/cost_breakdown``.
        This is the headline number that users can reconcile with the live
        Results panel.
      • ``new_capex_meur_by_carrier`` — increment-only via the vintage walk
        (``vintage_p_nom_opt × annuitised_capital_cost``) and non-vintage
        ``max(0, p_nom_opt − p_nom) × annuitised_capital_cost`` for plain
        assets. Useful for "what additional investment did this scenario
        commit?" but can be misleading when the existing fleet dominates.
    """
    import math as _math
    # Per-asset annuitised capital_cost, resolved once via the SAME
    # `periodized_capital_costs` path `asset_economics` / `cost_breakdown` /
    # `asset_costs` use — see `_periodized_lookup`'s docstring. This reads
    # `_state["solver_config"]` (the currently-active singleton's config, not
    # necessarily the project this network came from — a pre-existing
    # limitation, not addressed here).
    pcc = _periodized_lookup(n, cfg=cfg)

    # ipw.years per period — multiplier for annuitised €/yr → period total.
    # Empty on a flat network; `years_for_period` then answers 1.0 for every
    # lookup, which is the flat-network semantic.
    years_map = period_utils.period_years_map(n)

    # Generator total capacity (brownfield + built), keyed by carrier. Links
    # used to land here too, but that conflated link MW with generator MW in
    # the Compare View's generator table — they now have their own
    # `link_cap_by_carrier` below.
    cap_by_carrier: dict = {}
    capex_by_carrier: dict = {}
    storage_mw_by_carrier: dict = {}
    storage_mwh_by_carrier: dict = {}
    # New (built) capacity in MW per carrier — GENERATORS ONLY. Storage and
    # link builds have their own maps so each Compare View table (generator /
    # storage / links) pairs a total and a built column over the SAME set of
    # components. Previously this was a shared gen+storage+link bucket, which
    # produced impossible rows (battery total=0 MW / built=427 MW) because the
    # generator table's total column is generators-only.
    new_cap_by_carrier: dict = {}
    # New (built) storage increments — MW (storage_units) and MWh (storage_units
    # × max_hours + stores' e_nom delta).
    new_storage_mw_by_carrier: dict = {}
    new_storage_mwh_by_carrier: dict = {}
    # Link capacity (MW) — total (brownfield + built) and built-only. Feeds a
    # dedicated Links table; heat-pumps / electrolyzers / datacenters / P2X.
    link_cap_by_carrier: dict = {}
    new_link_cap_by_carrier: dict = {}

    # Vintage results live in n.meta after solve; they survive the netcdf
    # round-trip and carry the per-period (build_year) breakdown that the
    # collapsed-onto-parent dataframe rows lose. Structure:
    #   n.meta["vintage_results"][cls][parent_name] = {
    #     "initial_capacity": float,
    #     "capacity_field": "p_nom" | "e_nom" | "s_nom",
    #     "periods": [{"build_year": int, "p_nom_opt": float, ...}],
    #   }
    # Walked BEFORE the plain df pass so we can mark parent_name as "handled
    # via vintages" and avoid double-counting from the parent row.
    vintage_root = (n.meta or {}).get("vintage_results", {})
    if not isinstance(vintage_root, dict):
        vintage_root = {}

    def _vintage_handled(cls_name: str, parent: str) -> bool:
        block = vintage_root.get(cls_name)
        return isinstance(block, dict) and parent in block

    def _walk_vintages(cls_name: str, df, target_cap: dict, target_capex: dict,
                      also_mwh: dict | None = None,
                      target_new_cap: dict | None = None,
                      target_new_mwh: dict | None = None) -> None:
        block = vintage_root.get(cls_name)
        if not isinstance(block, dict) or df is None or df.empty:
            return
        for parent_name, payload in block.items():
            if parent_name not in df.index:
                continue
            row = df.loc[parent_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            # Initial (pre-build) capacity contributes to the carrier total
            # under no specific period — it's "already there".
            try:
                ini = float(payload.get("initial_capacity") or 0)
            except (TypeError, ValueError):
                ini = 0.0
            if _math.isfinite(ini) and ini > 0:
                # Brownfield contributes to total ONCE (None branch) and is
                # also replicated into every period's by_period bucket so the
                # Compare View period-filter shows operational fleet, not just
                # incremental builds. Otherwise a 400 MW pre-existing asset
                # vanishes when the user picks a specific period.
                _bucket_add(target_cap, carrier, ini, None)
                _bucket_replicate_per_period(target_cap, carrier, ini, periods)
                if also_mwh is not None:
                    try:
                        mh = float(row["max_hours"]) if "max_hours" in df.columns else 0.0
                    except (TypeError, ValueError):
                        mh = 0.0
                    if _math.isfinite(mh) and mh > 0:
                        _bucket_add(also_mwh, carrier, ini * mh, None)
                        _bucket_replicate_per_period(also_mwh, carrier, ini * mh, periods)
            periods_list = payload.get("periods") or []
            if not isinstance(periods_list, list):
                continue
            # Per-period capacity = vintage p_nom_opt under that build_year.
            # CAPEX = vintage p_nom_opt × annuitised capital_cost from parent.
            cc_per_mw = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
            for entry in periods_list:
                if not isinstance(entry, dict):
                    continue
                by_int = _classify_build_year(entry.get("build_year"))
                try:
                    opt = float(entry.get("p_nom_opt") or 0)
                except (TypeError, ValueError):
                    continue
                if not _math.isfinite(opt) or opt <= 1e-9:
                    continue
                _bucket_add(target_cap, carrier, opt, by_int)
                # Mirror the per-period contribution into the "new (built)" map
                # so the Compare View can show JUST the expansion separately
                # from the total operational fleet. Brownfield (`ini` above)
                # is NOT added to this map — only vintage builds.
                if target_new_cap is not None:
                    _bucket_add(target_new_cap, carrier, opt, by_int)
                if cc_per_mw > 0:
                    _bucket_add(target_capex, carrier, cc_per_mw * opt / 1e6, by_int)
                if also_mwh is not None:
                    try:
                        mh = float(row["max_hours"]) if "max_hours" in df.columns else 0.0
                    except (TypeError, ValueError):
                        mh = 0.0
                    if _math.isfinite(mh) and mh > 0:
                        _bucket_add(also_mwh, carrier, opt * mh, by_int)
                        # New (built) energy increment — vintage builds only,
                        # never brownfield, so the storage table's "built MWh"
                        # column shows just the expansion.
                        if target_new_mwh is not None:
                            _bucket_add(target_new_mwh, carrier, opt * mh, by_int)

    def _walk_plain(df, cls_name: str, nom: str, target_cap: dict, target_capex: dict,
                   also_mwh: dict | None = None,
                   target_new_cap: dict | None = None,
                   target_new_mwh: dict | None = None) -> None:
        """
        For assets WITHOUT a vintage_results entry — read p_nom / p_nom_opt
        straight from the dataframe row. Treats the row's build_year as the
        attribution period if it sits within the planning horizon, otherwise
        the contribution goes only into the aggregated total.

        Brownfield split: the row's `p_nom` is treated as PRE-EXISTING (active
        in every period) and replicated across periods. The delta `p_nom_opt −
        p_nom` is treated as expansion, attributed to the row's build_year if
        it falls within the planning horizon. Without this split, fixed assets
        with build_year outside the horizon (e.g. dc_lowT_dump with build_year=0)
        showed total=500 MW but by_period={} → the Compare View per-period
        bar vanished when the user selected an individual investment period.
        """
        if df is None or df.empty:
            return
        opt_col = f"{nom}_opt"
        if opt_col not in df.columns:
            return
        for asset in df.index:
            if _vintage_handled(cls_name, asset):
                continue  # vintage path already counted this one
            row = df.loc[asset]
            try:
                opt = float(row[opt_col])
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(opt) or opt <= 1e-9:
                continue
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            by_int = _classify_build_year(row.get("build_year"))
            try:
                ini = float(row[nom])
            except (TypeError, ValueError):
                ini = 0.0
            if not _math.isfinite(ini) or ini < 0:
                ini = 0.0
            delta = max(0.0, opt - ini)
            # Brownfield: contributes once to total, replicated to every period.
            if ini > 1e-9:
                _bucket_add(target_cap, carrier, ini, None)
                _bucket_replicate_per_period(target_cap, carrier, ini, periods)
            # Expansion: attribute to the build year (single-period bucket).
            if delta > 1e-9:
                _bucket_add(target_cap, carrier, delta, by_int)
                # Mirror the expansion delta into the "new (built)" map; the
                # brownfield branch above is excluded here so the Compare View
                # can show only NEW builds separately from total operational
                # fleet (fixes the "heat-dump 500/500/Δ=0 reads as built" UX bug).
                if target_new_cap is not None:
                    _bucket_add(target_new_cap, carrier, delta, by_int)
                cc = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
                if cc > 0:
                    _bucket_add(target_capex, carrier, cc * delta / 1e6, by_int)
            if also_mwh is not None:
                try:
                    mh = float(row["max_hours"]) if "max_hours" in df.columns else 0.0
                except (TypeError, ValueError):
                    mh = 0.0
                if _math.isfinite(mh) and mh > 0:
                    # Same split for storage MWh capacity.
                    if ini > 1e-9:
                        _bucket_add(also_mwh, carrier, ini * mh, None)
                        _bucket_replicate_per_period(also_mwh, carrier, ini * mh, periods)
                    if delta > 1e-9:
                        _bucket_add(also_mwh, carrier, delta * mh, by_int)
                        # New (built) energy increment — expansion only.
                        if target_new_mwh is not None:
                            _bucket_add(target_new_mwh, carrier, delta * mh, by_int)

    # Vintage path first, then plain. Vintage covers extendable assets with
    # per-period bounds; plain covers everything else (pre-existing fixed
    # capacity, lines/transformers without vintages, simple extendables).
    # Generators → generator capacity + generator-only "built" map.
    _walk_vintages("Generator",   n.generators,    cap_by_carrier,         capex_by_carrier,
                  target_new_cap=new_cap_by_carrier)
    # Storage units → storage MW + MWh, with storage-specific "built" maps.
    _walk_vintages("StorageUnit", n.storage_units, storage_mw_by_carrier,  capex_by_carrier,
                  also_mwh=storage_mwh_by_carrier,
                  target_new_cap=new_storage_mw_by_carrier,
                  target_new_mwh=new_storage_mwh_by_carrier)
    # Stores → energy-only (e_nom is MWh); built MWh into the storage new-mwh map.
    _walk_vintages("Store",       n.stores,        storage_mwh_by_carrier, capex_by_carrier,
                  target_new_cap=new_storage_mwh_by_carrier)
    # Links → dedicated link maps (heat-pumps, electrolyzers, datacenters,
    # P2X). Kept OUT of the generator capacity map so the generator table's
    # total/built columns cover only generators. The Compare View renders a
    # separate Links table from these.
    _walk_vintages("Link",        n.links,         link_cap_by_carrier,    capex_by_carrier,
                  target_new_cap=new_link_cap_by_carrier)
    _walk_plain(n.generators,    "Generator",   "p_nom", cap_by_carrier,         capex_by_carrier,
                target_new_cap=new_cap_by_carrier)
    _walk_plain(n.storage_units, "StorageUnit", "p_nom", storage_mw_by_carrier,  capex_by_carrier,
                also_mwh=storage_mwh_by_carrier,
                target_new_cap=new_storage_mw_by_carrier,
                target_new_mwh=new_storage_mwh_by_carrier)
    _walk_plain(n.stores,        "Store",       "e_nom", storage_mwh_by_carrier, capex_by_carrier,
                target_new_cap=new_storage_mwh_by_carrier)
    _walk_plain(n.links,         "Link",        "p_nom", link_cap_by_carrier,    capex_by_carrier,
                target_new_cap=new_link_cap_by_carrier)

    # ── Total annuitised CAPEX per carrier (existing + new) ──────────────────
    # Headline metric — matches `/results/cost_breakdown` so the compare view
    # reconciles with the live Results panel. For each cost-bearing asset:
    #   annuitised_per_yr = p_nom_opt × cc_per_MW_per_yr
    # cc_per_MW_per_yr falls back from capital_cost → overnight_cost ×
    # annuity(dr, lt). Per-period total = annuitised_per_yr × ipw.years[P].
    # The single-period case collapses to one total entry with no by_period
    # split.
    total_capex_by_carrier: dict = _compute_total_annuitised_capex(
        n, periods, is_multi, years_map, pcc,
    )

    return CapacityComparison(
        capacity_mw_by_carrier=_to_pv_dict(cap_by_carrier),
        capex_meur_by_carrier=_to_pv_dict(total_capex_by_carrier),
        new_capex_meur_by_carrier=_to_pv_dict(capex_by_carrier),
        new_capacity_mw_by_carrier=_to_pv_dict(new_cap_by_carrier),
        storage_mw_by_carrier=_to_pv_dict(storage_mw_by_carrier),
        storage_mwh_by_carrier=_to_pv_dict(storage_mwh_by_carrier),
        new_storage_mw_by_carrier=_to_pv_dict(new_storage_mw_by_carrier),
        new_storage_mwh_by_carrier=_to_pv_dict(new_storage_mwh_by_carrier),
        link_capacity_mw_by_carrier=_to_pv_dict(link_cap_by_carrier),
        new_link_capacity_mw_by_carrier=_to_pv_dict(new_link_cap_by_carrier),
    )


def _compute_total_annuitised_capex(
    n, periods, is_multi, years_map, pcc,
) -> dict:
    """
    Walk every cost-bearing component, accumulate ``p_nom_opt × cc_per_MW``
    per carrier as M€/yr, then expand into per-period values via
    ``ipw.years[P]``. Mirrors the per-period aggregation in
    ``cost_breakdown`` so the two views show the same gas / solar / battery
    CAPEX numbers. ``pcc`` is ``_periodized_lookup(n)``'s output, built once
    by the caller (``_compute_capacity_summary``) and passed through here
    rather than recomputed.
    """
    import math as _math

    out: dict = {}

    def _walk(df, nom_col: str, comp_attr: str, *, extendable_only: bool = False) -> None:
        if df is None or df.empty:
            return
        if extendable_only:
            ext_col = f"{nom_col}_extendable"
            if ext_col not in df.columns:
                return
            df = df[df[ext_col].astype(bool)]
            if df.empty:
                return
        opt_col = f"{nom_col}_opt"
        capacity_col = opt_col if opt_col in df.columns else (nom_col if nom_col in df.columns else None)
        if capacity_col is None:
            return
        for asset in df.index:
            row = df.loc[asset]
            try:
                opt = float(row[capacity_col])
            except (TypeError, ValueError, KeyError):
                continue
            if not _math.isfinite(opt) or opt <= 1e-9:
                continue
            cc_per_mw = _safe_capital_cost(row, pcc, comp_attr)
            if cc_per_mw <= 0:
                continue
            per_year_meur = (opt * cc_per_mw) / 1e6  # M€/yr annuitised
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            b = out.setdefault(carrier, {"total": 0.0, "by_period": {}})
            if is_multi and periods:
                # Each period contributes annuitised_per_yr × ipw.years[P].
                # We assume the asset is active in every period — which is
                # what PyPSA's `n.statistics()` does for capex by default
                # (period-aware costing happens at LP time, not at stats
                # time). The horizon total is the sum across periods.
                for p in periods:
                    years = period_utils.years_for_period(years_map, p)
                    contrib = per_year_meur * years
                    b["by_period"][str(p)] = b["by_period"].get(str(p), 0.0) + contrib
                b["total"] = sum(b["by_period"].values())
            else:
                b["total"] += per_year_meur

    # Generation / storage / stores unconditionally, plus links.
    #
    # Lines and transformers stay omitted: a passive branch that the LP
    # cannot resize contributes nothing to the objective, and reporting its
    # notional CAPEX here produced a "line CAPEX" carrier value nobody could
    # reconcile with the Results panel. Branch expansion is visible in the
    # Line loading tab; that is where it belongs.
    #
    # An EXTENDABLE link is a different animal, and excluding it was a defect:
    # the LP does size it, its capital_cost does enter the objective, and
    # `_compute_economics_summary` has always counted it. So the two tabs of
    # one comparison reported different CAPEX for one network — MEASURED at
    # 25.154535 vs 25.320785 M€ on the golden fixture (exactly the
    # electrolyzer's CAPEX) and at a 56.192453 M€ gap on a real three-period
    # project. See docs/superpowers/findings/
    # 2026-08-03-compare-tab-correctness.md §S1.
    #
    # Every link that carries a capital_cost, not only the extendable ones.
    # `cost_breakdown` is built from `n.statistics()`, which charges
    # capital_cost x p_nom_opt for EVERY asset — so restricting this walk to
    # extendables made a fixed link with a capital_cost show up in Economics
    # and vanish from the Capacity tab, contradicting this function's
    # documented contract of mirroring cost_breakdown.
    _walk(n.generators,    "p_nom", "generators")
    _walk(n.storage_units, "p_nom", "storage_units")
    _walk(n.stores,        "e_nom", "stores")
    _walk(n.links,         "p_nom", "links")
    return out
