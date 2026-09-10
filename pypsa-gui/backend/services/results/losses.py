"""
Transmission-loss summary for `/results/losses`.

Lifted from `routers.results` in the Phase 3 follow-up: Phase 2 deferred this
handler because its body lazily imported `_build_snapshot_weights` from
`routers.compare`. That helper is now `services.compare.support`, so the
dependency is a plain service import and the layering rule holds.
"""
from __future__ import annotations

from services.compare.support import _build_snapshot_weights


def compute_losses_summary(n, source, has_ac_pf_snapshot, *, result_df):
    """
    Transmission-loss summary: per-branch MWh and peak MW, and the share of
    served demand.

    Lifted from `routers.results.get_losses_summary`. Phase 2 deferred this one
    because it reached into `routers.compare` for the snapshot weights; that
    helper now lives in `services/compare/support.py`, so the lazy router import
    is a module-level service import here.

    `has_ac_pf_snapshot` is the router's `_state.get("ac_pf_results") is not
    None` — whether Stage 2 has ever run. It decides both which frames are read
    and what `enabled` means for `source="ac_pf"`.
    """
    import math
    # Per-snapshot ENERGY weight = generators column × investment-period years.
    # The shared helper applies the generators→objective→1.0 fallback AND the
    # multi-period years scaling (the raw-column read used before omitted years).
    # Returns a Series indexed by n.snapshots, aligned with the _t loss tables
    # below. Lazy import avoids the projects<->simulation import cycle.
    weights = _build_snapshot_weights(n, "generators")

    def _branch_loss(df_t, df_static, comp_name: str):
        """Returns (per_branch_rows, snapshot_total_mw, total_mwh, peak_mw)."""
        rows = []
        total_mwh = 0.0
        peak_mw = 0.0
        snap_total = None
        if df_t is None or df_t.empty:
            return rows, snap_total, total_mwh, peak_mw
        # Replace NaN / inf with 0 so JSON serialises cleanly. PyPSA emits NaN
        # for snapshots when the loss var was masked (e.g. inactive lines).
        clean = df_t.fillna(0.0)
        # Per-line MWh = sum_t (loss_t × weight_t)
        if weights is not None:
            mwh = clean.multiply(weights, axis=0).sum(axis=0)
        else:
            mwh = clean.sum(axis=0)
        peak = clean.abs().max(axis=0)
        snap_total = clean.sum(axis=1)  # per-snapshot total across this comp
        for name in clean.columns:
            v_mwh = float(mwh.get(name, 0.0))
            v_peak = float(peak.get(name, 0.0))
            if not math.isfinite(v_mwh): v_mwh = 0.0
            if not math.isfinite(v_peak): v_peak = 0.0
            rows.append({
                "component": comp_name,
                "name": str(name),
                "loss_mwh": v_mwh,
                "peak_mw": v_peak,
            })
            total_mwh += v_mwh
            if v_peak > peak_mw:
                peak_mw = v_peak
        return rows, snap_total, total_mwh, peak_mw

    if source == "ac_pf" and has_ac_pf_snapshot:
        # Real losses from AC PF: loss(t, branch) = p0 + p1. PyPSA's p0/p1
        # are signed; their sum is the resistive loss (both are positive
        # injections away from the buses). For lines that didn't converge
        # the snapshot will contain NaN, which `_branch_loss` masks to 0.
        line_p0  = result_df(n, "lines_t",        "p0", "ac_pf") if not n.lines.empty        else None
        line_p1  = result_df(n, "lines_t",        "p1", "ac_pf") if not n.lines.empty        else None
        trafo_p0 = result_df(n, "transformers_t", "p0", "ac_pf") if not n.transformers.empty else None
        trafo_p1 = result_df(n, "transformers_t", "p1", "ac_pf") if not n.transformers.empty else None
        line_t  = (line_p0  + line_p1)  if line_p0  is not None and line_p1  is not None else None
        trafo_t = (trafo_p0 + trafo_p1) if trafo_p0 is not None and trafo_p1 is not None else None
    else:
        # source='lopf' OR source='ac_pf' before Stage 2 has ever run — read
        # the LP loss variables. Returns empty when transmission_losses was
        # off on the last solve.
        line_t  = result_df(n, "lines_t",        "loss", "lopf") if not n.lines.empty        else None
        trafo_t = result_df(n, "transformers_t", "loss", "lopf") if not n.transformers.empty else None
    line_rows,  line_snap,  line_mwh,  line_peak  = _branch_loss(line_t,  n.lines,        "Line")
    trafo_rows, trafo_snap, trafo_mwh, trafo_peak = _branch_loss(trafo_t, n.transformers, "Transformer")

    # `enabled` reflects whether we actually have meaningful loss data:
    # for source='ac_pf' it means a Stage 2 snapshot exists; for source='lopf'
    # it means the LP solve modelled transmission_losses. Avoids the
    # misleading "enabled:true, all zeros" surface when source=ac_pf is
    # requested before Stage 2 has run (loss = p0 + p1 = 0 in DC OPF).
    if source == "ac_pf":
        enabled = has_ac_pf_snapshot and (
            (line_t is not None and not line_t.empty) or
            (trafo_t is not None and not trafo_t.empty)
        )
    else:
        enabled = (line_t is not None and not line_t.empty) or \
                  (trafo_t is not None and not trafo_t.empty)

    total_mwh = line_mwh + trafo_mwh
    peak_mw   = max(line_peak, trafo_peak)

    # Per-branch share of total (for sorting / "where do losses come from").
    rows = line_rows + trafo_rows
    if total_mwh > 0:
        for r in rows:
            r["share_pct"] = 100.0 * r["loss_mwh"] / total_mwh
    else:
        for r in rows:
            r["share_pct"] = 0.0
    rows.sort(key=lambda r: r["loss_mwh"], reverse=True)

    # Total served demand for the "% of demand" KPI. NaN-safe — on multi-period
    # networks an unsolved snapshot fraction leaves `loads_t.p` with NaN cells;
    # without `.fillna(0.0)` the sum produces NaN, JSONResponse.render then
    # 500s with allow_nan=False (same trap CLAUDE.md flags for /results/storage).
    # Belt-and-suspenders: also coerce the final scalar through
    # `_safe_isfinite` so any residual non-finite value collapses to 0.
    import math as _math
    total_demand_mwh = 0.0
    try:
        if hasattr(n.loads_t, "p") and not n.loads_t.p.empty:
            p = n.loads_t.p.fillna(0.0)
            if weights is not None:
                raw_total = float(p.multiply(weights, axis=0).sum().sum())
            else:
                raw_total = float(p.sum().sum())
            total_demand_mwh = raw_total if _math.isfinite(raw_total) else 0.0
    except Exception:
        total_demand_mwh = 0.0

    loss_pct_raw = (100.0 * total_mwh / total_demand_mwh) if total_demand_mwh > 0 else 0.0
    loss_pct = loss_pct_raw if _math.isfinite(loss_pct_raw) else 0.0
    total_mwh_safe = total_mwh if _math.isfinite(total_mwh) else 0.0
    peak_mw_safe = peak_mw if _math.isfinite(peak_mw) else 0.0

    return {
        "enabled": bool(enabled),
        "total_mwh": float(total_mwh_safe),
        "peak_mw": float(peak_mw_safe),
        "total_demand_mwh": float(total_demand_mwh),
        "loss_pct_of_demand": float(loss_pct),
        "by_branch": rows,
    }
