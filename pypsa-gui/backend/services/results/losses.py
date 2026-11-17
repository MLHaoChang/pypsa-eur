"""
Transmission-loss summary for `/results/losses`.

Lifted from `routers.results` in the Phase 3 follow-up: Phase 2 deferred this
handler because its body lazily imported `_build_snapshot_weights` from
`routers.compare`. That helper is now `services.compare.support`, so the
dependency is a plain service import and the layering rule holds.
"""
from __future__ import annotations

import numpy as np

from services.compare.support import _build_snapshot_weights

VALIDITY_VERSION = "losses-validity/1"


_COUNTS = ("finite", "zero", "missing", "pos_inf", "neg_inf")
_STATES = ("complete", "incomplete", "absent", "misaligned")


def _validity_rows(df_t, names, comp_name: str, snapshots) -> list[dict]:
    """
    Per-entity counts of the RAW values over every snapshot, read before any
    zero-fill.

    The frame is first aligned to ``snapshots``: a snapshot it lacks counts as
    `missing`, so a zero-row or short frame never reads complete. A frame whose
    index repeats a row or holds rows outside ``snapshots`` cannot be aligned,
    and every entity in it is `misaligned`. Otherwise a finite nonzero value, a
    real zero, a missing value (NaN), +inf and -inf are counted apart, and an
    entity with no column at all is `absent`. Only an entity whose every
    snapshot holds a finite value is `complete`, so a later reader can tell a
    real 0 MW from a value the summary filled in.
    """
    aligned = None
    if df_t is not None:
        index = df_t.index
        if index.is_unique and index.isin(snapshots).all():
            aligned = df_t.reindex(snapshots)
    rows = []
    for name in names:
        counts = dict.fromkeys(_COUNTS, 0)
        if df_t is None or name not in df_t.columns:
            state = "absent"
        elif aligned is None:
            state = "misaligned"
        else:
            raw = np.asarray(aligned[name], dtype=float)
            counts["missing"] = int(np.isnan(raw).sum())
            counts["pos_inf"] = int(np.isposinf(raw).sum())
            counts["neg_inf"] = int(np.isneginf(raw).sum())
            counts["zero"] = int((raw == 0.0).sum())
            counts["finite"] = int(np.isfinite(raw).sum()) - counts["zero"]
            bad = counts["missing"] + counts["pos_inf"] + counts["neg_inf"]
            state = "incomplete" if bad else "complete"
        rows.append({"component": comp_name, "name": str(name), "state": state, **counts})
    return rows


def _component_summary(rows) -> dict:
    """Bounded per-component totals: entities per state and values per kind.
    The per-entity rows stay server-side until a reader needs them."""
    out = {}
    for row in rows:
        entry = out.setdefault(row["component"], {
            "entities": dict.fromkeys(_STATES, 0), "values": dict.fromkeys(_COUNTS, 0)})
        entry["entities"][row["state"]] += 1
        for key in _COUNTS:
            entry["values"][key] += row[key]
    return out


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
    load_t = n.loads_t.p if hasattr(n.loads_t, "p") else None
    branch_validity = (_validity_rows(line_t, n.lines.index, "Line", n.snapshots)
                       + _validity_rows(trafo_t, n.transformers.index, "Transformer", n.snapshots))
    load_validity = _validity_rows(load_t, n.loads.index, "Load", n.snapshots)
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
        # Additive (assistant-assurance 10, A1.7): the raw state behind the
        # zero-filled numbers above, so a missing value never reads as 0.
        "validity": {
            "version": VALIDITY_VERSION,
            "snapshots": int(len(n.snapshots)),
            # Nothing to certify is not complete: with no branch there is no
            # loss value to stand behind.
            "complete": bool(branch_validity)
            and all(r["state"] == "complete" for r in branch_validity + load_validity),
            "components": _component_summary(branch_validity + load_validity),
        },
    }
