"""
Loading comparison: line and transformer utilisation.

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
    CarrierPeriodValue,
    LineLoadingEntry,
    LoadingComparison,
)
from services.compare.support import (
    _build_snapshot_weights,
)


def _compute_loading_summary(n, periods, is_multi, has_solve) -> LoadingComparison:
    """
    Per-line / per-transformer loading. Loading = ``|p0| / s_nom_opt``,
    averaged or peak-aggregated by period. Returns the entries sorted by
    horizon-wide peak loading (descending) so the frontend can render top-N
    of the most congested branches first.

    Operates on both lines AND transformers — bundled into a single list
    with ``is_transformer`` distinguishing them. Each entry carries three
    metrics: peak loading, snapshot-weighted mean loading, and weighted
    "binding hours" at ≥99 % of capacity (the canonical N-0 thermal
    proxy).
    """
    import math as _math


    out: list[LineLoadingEntry] = []
    if not has_solve:
        return LoadingComparison()

    # ENERGY/HOURS basis, not COST — binding_hours is a raw hours COUNT
    # (Task 15 / suspect S2: measured wrong on the "objective" basis, see
    # docs/superpowers/findings/2026-08-03-compare-tab-correctness.md §S2).
    # mean_loading is a weighted MEAN and is unaffected by this choice (the
    # weight series cancels under a uniform rescaling) — measured, not
    # merely assumed.
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots

    def _walk_branches(df, t_df, is_transformer: bool, is_link: bool = False, nom_field: str = "s_nom") -> None:
        if df is None or df.empty or t_df is None or t_df.empty:
            return
        # *_nom_opt is the LP-optimised rating; for non-extendable branches
        # it equals *_nom. For passive branches `s_nom` is the field;
        # Links use `p_nom`. Guard zero / NaN to avoid div-by-zero.
        nom_opt_col = f"{nom_field}_opt"
        nom_col = nom_opt_col if nom_opt_col in df.columns else nom_field
        carrier_col_present = is_link and "carrier" in df.columns
        for name in df.index:
            try:
                s_nom = float(df.at[name, nom_col])
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(s_nom) or s_nom <= 1e-9:
                continue
            if name not in t_df.columns:
                continue
            link_carrier: str | None = None
            if carrier_col_present:
                try:
                    raw = df.at[name, "carrier"]
                    link_carrier = str(raw) if raw not in (None, "", float("nan")) else None
                except Exception:
                    link_carrier = None
            # IMPORTANT: do NOT fillna(0) here. A partial AC-PF run (rolling
            # horizon, or representative-week dispatch fix) populates p0 for
            # only a subset of snapshots; the rest are NaN. Treating NaN as
            # zero would *correctly* compute peak (max ignores zero baseline)
            # but *dramatically* understate the snapshot-weighted MEAN —
            # dividing the sum-of-real-values by the sum-of-all-weights
            # (26 280 h) instead of by the populated-only weights (e.g.
            # 168 h) → a 150× error. Use pandas' default skipna=True for
            # max / sum, and mask the weight series to the populated set
            # for the mean denominator.
            try:
                p0 = t_df[name].reindex(sns).astype(float)
            except Exception:
                continue
            loading = p0.abs() / s_nom  # NaN propagates through arithmetic
            present_mask = loading.notna()
            if not present_mask.any():
                # Whole branch was never written to. Skip — surfaces as a
                # missing entry in the comparison view instead of a row of
                # spurious zeros.
                continue

            # Horizon-wide aggregates over the populated subset.
            peak_total = float(loading.max(skipna=True))
            weights_present = weights.where(present_mask, other=0.0)
            wsum_total = float(weights_present.sum())
            if wsum_total > 0:
                mean_total = float((loading.fillna(0.0) * weights_present).sum() / wsum_total)
            else:
                mean_total = 0.0
            binding_total = float(
                (loading.fillna(0.0) >= 0.99).astype(float).mul(weights_present).sum()
            )

            peak_pp: dict[str, float] = {}
            mean_pp: dict[str, float] = {}
            binding_pp: dict[str, float] = {}
            if is_multi:
                try:
                    period_lvl = sns.get_level_values(0)
                    by_period_loading = loading.groupby(period_lvl)
                    by_period_w_present = weights_present.groupby(period_lvl)
                    by_period_binding = (
                        (loading.fillna(0.0) >= 0.99).astype(float)
                        .mul(weights_present)
                        .groupby(period_lvl).sum()
                    )
                    for p in periods:
                        try:
                            group_load = by_period_loading.get_group(p)
                            group_w = by_period_w_present.get_group(p)
                        except KeyError:
                            continue
                        # Period-local peak — skipna ignores NaN. If the
                        # period is entirely NaN, peak is NaN (skip).
                        peak_val = group_load.max(skipna=True)
                        if peak_val is None or (isinstance(peak_val, float) and not _math.isfinite(peak_val)):
                            continue
                        peak_pp[str(p)] = float(peak_val)
                        wsum = float(group_w.sum())
                        mean_pp[str(p)] = (
                            float((group_load.fillna(0.0) * group_w).sum() / wsum)
                            if wsum > 0 else 0.0
                        )
                        try:
                            binding_pp[str(p)] = float(by_period_binding.loc[p])
                        except KeyError:
                            binding_pp[str(p)] = 0.0
                except Exception:
                    pass
            out.append(LineLoadingEntry(
                name=str(name),
                s_nom_opt=s_nom,
                is_transformer=is_transformer,
                is_link=is_link,
                carrier=link_carrier,
                peak_loading=CarrierPeriodValue(total=peak_total, by_period=peak_pp),
                mean_loading=CarrierPeriodValue(total=mean_total, by_period=mean_pp),
                binding_hours=CarrierPeriodValue(total=binding_total, by_period=binding_pp),
            ))

    _walk_branches(n.lines,        getattr(n.lines_t, "p0", None) if hasattr(n, "lines_t") else None,        False)
    _walk_branches(n.transformers, getattr(n.transformers_t, "p0", None) if hasattr(n, "transformers_t") else None, True)
    # Sector-coupling Links (heat pumps, electrolysers, H2 pipelines, P2X)
    # — same loading metric on |p0|/p_nom_opt. Carrier exposes the energy
    # type so the frontend can group / filter by carrier in the loading tab
    # just like in dispatch / capacity tabs. Multi-port Links (bus2 set)
    # still report a single p_nom; the bus2 reverse flow is implicit via
    # efficiency2 and not separately rate-limited at the LP layer.
    _walk_branches(n.links,        getattr(n.links_t, "p0", None) if hasattr(n, "links_t") else None,        False, is_link=True, nom_field="p_nom")
    # Worst-first ordering across all branch types.
    out.sort(key=lambda e: e.peak_loading.total, reverse=True)
    return LoadingComparison(lines=out)
