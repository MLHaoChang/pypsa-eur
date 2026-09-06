"""
Lifted from `routers.results` (get_unit_commitment).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

from services.serialization import (
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)



def compute_unit_commitment(n, from_, to_, *, result_df):
    """
    MILP unit-commitment outputs: status, start-ups, shut-downs.

    Lifted from `routers.results.get_unit_commitment`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import math as _math
    status = result_df(n, "generators_t", "status", "lopf")
    if status is None or status.empty:
        return {"generators": [], "status_grid": None, "n_committable": 0,
                "note": "No unit-commitment results. Set committable=True on at "
                "least one generator and re-solve."}

    start_up = result_df(n, "generators_t", "start_up", "lopf")
    shut_down = result_df(n, "generators_t", "shut_down", "lopf")
    p = result_df(n, "generators_t", "p", "lopf")

    # ENERGY basis (energy_mwh + weighted on-hours): use the `generators`
    # column — PyPSA's energy weighting (matches n.statistics() and the Dispatch
    # tab), falling back generators → objective → None on older netcdf. The UC
    # start/shut COSTS below are count-based (not snapshot-weighted), so this
    # only affects the energy figures. Identical when the columns coincide.
    try:
        weights = n.snapshot_weightings.generators
    except Exception:
        try:
            weights = n.snapshot_weightings.objective
        except Exception:
            weights = None

    # Restrict to actually-committable generators. PyPSA writes the status grid
    # only for those — non-committable units have NaN here and we skip them.
    gens = n.generators
    committable_names = []
    if not gens.empty and "committable" in gens.columns:
        committable_names = [str(name) for name in gens.index[gens["committable"]]]

    rows: list[dict] = []
    for name in committable_names:
        if name not in status.columns:
            continue
        s = status[name].fillna(0)
        on_hours = float(s.sum())  # binary so sum = on-count
        # Apply weights if present for the on-hours metric so representative-day
        # workflows scale correctly.
        if weights is not None:
            on_hours_weighted = float((s * weights).sum())
        else:
            on_hours_weighted = on_hours
        n_starts = int(start_up[name].fillna(0).sum()) if (start_up is not None and name in start_up.columns) else 0
        n_shuts = int(shut_down[name].fillna(0).sum()) if (shut_down is not None and name in shut_down.columns) else 0
        # Energy (MWh) only over hours when on.
        if p is not None and name in p.columns:
            p_on = p[name].fillna(0)
            if weights is not None:
                energy = float((p_on * weights).sum())
            else:
                energy = float(p_on.sum())
        else:
            energy = 0.0
        p_nom = float(gens.at[name, "p_nom"]) if "p_nom" in gens.columns else 0.0
        if not _math.isfinite(p_nom):
            p_nom = 0.0
        # CF when on: energy / (p_nom × on_hours). When the unit was always
        # off, return 0 instead of NaN.
        cf_when_on = (100.0 * energy / (p_nom * on_hours_weighted)) if (p_nom > 0 and on_hours_weighted > 0) else 0.0
        su_cost = float(gens.at[name, "start_up_cost"]) if "start_up_cost" in gens.columns else 0.0
        sd_cost = float(gens.at[name, "shut_down_cost"]) if "shut_down_cost" in gens.columns else 0.0
        total_uc_cost = su_cost * n_starts + sd_cost * n_shuts
        rows.append({
            "name": name,
            "carrier": str(gens.at[name, "carrier"]) if "carrier" in gens.columns else "",
            "p_nom_MW": p_nom,
            "n_starts": n_starts,
            "n_shuts": n_shuts,
            "hours_on": on_hours,
            "energy_mwh": energy,
            "capacity_factor_when_on_pct": cf_when_on,
            "total_uc_cost_eur": total_uc_cost,
        })
    rows.sort(key=lambda r: -r["energy_mwh"])

    # The binary status grid for the heatmap. Restrict to committable
    # generators to keep the payload small. Replace NaN with 0 (off) so the
    # frontend doesn't have to handle three-state cells.
    cols = [c for c in status.columns if c in committable_names]
    if cols:
        grid = status[cols].fillna(0).astype(int)
        range_meta = None
        if _wants_slice(from_, to_):
            grid, range_meta = _slice_ts(grid, from_, to_)
        status_payload = _ts_payload(grid, range_meta=range_meta)
    else:
        status_payload = None

    return {
        "generators": rows,
        "status_grid": status_payload,
        "n_committable": len(committable_names),
    }
