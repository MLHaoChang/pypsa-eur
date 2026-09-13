"""
Lifted from `routers.results` (get_lost_load).

The handler keeps the capture lookup from `_state` and the HTTP 204 map;
this module gets the arithmetic (VoLL recovery, bus carriers, shed-hours,
optional snapshot window) and returns the payload, or `None` where the
endpoint answers 204. The capture dict is injected so this runs with no
router import — see `tests/test_results_seam.py`.

pandas / numpy are used only through the injected frame and helpers.
"""
from __future__ import annotations

from services.serialization import (
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)


def compute_lost_load(n, capture, from_, to_):
    """
    Per-bus lost-load time series + horizon aggregates.

    Lifted from `routers.results.get_lost_load`, which keeps the
    `_state["last_lost_load"]` read and the None -> 204 mapping. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    if not capture or capture.get("lost_load_t") is None:
        return None
    df = capture["lost_load_t"]
    if df is None or df.empty:
        return None
    # total_mwh / total_cost are whole-horizon aggregates captured by the
    # solver, not per-snapshot arrays — a `from`/`to` window below narrows
    # `data` but does NOT recompute these; `range.complete` tells the
    # frontend whether the window covers the whole series.
    total_mwh = float(capture.get("lost_load_total_mwh", 0))
    total_cost = float(capture.get("lost_load_cost_eur", 0))
    # Surface VOLL directly so the frontend doesn't infer it via division
    # (which crashes on zero-MWh edge cases). Cost / MWh recovers the
    # per-MWh VOLL price the solver used.
    # Prefer the capture's explicit VoLL (present since the weighted-totals
    # change); older captures lack it — fall back to the cost/energy ratio.
    voll = float(capture.get("voll_eur_per_mwh") or 0.0) or (
        (total_cost / total_mwh) if total_mwh > 0 else 0.0
    )

    # Per-column bus carrier. solver_service adds a VOLL slack on EVERY bus
    # (not just electricity), so `lost_load_t.columns` carries bus names
    # across all energy carriers — H2, heat, gas, etc. Surface the bus
    # carrier so the frontend can split lost-load by carrier (the user's
    # ask is to see H2 / heat lost load separately from electrical).
    bus_carriers: dict[str, str] = {}
    if hasattr(n, "buses") and not n.buses.empty and "carrier" in n.buses.columns:
        for col in df.columns:
            try:
                bus_carriers[str(col)] = str(n.buses.at[col, "carrier"] or "")
            except KeyError:
                bus_carriers[str(col)] = ""
    range_meta = None
    full_df = df   # bind BEFORE slicing — shed-hours is horizon-scope
    if _wants_slice(from_, to_):
        df, range_meta = _slice_ts(df, from_, to_)
    # Shed-hours (spec §5.1) — electrical buses only, weighted on the same
    # energy basis as dispatch. Computed on the FULL frame, not the sliced
    # range: it is a horizon reliability number, not a window statistic.
    from services.adequacy.metrics import electrical_columns, shed_hours
    from services.period_utils import snapshot_weights
    sh = shed_hours(
        full_df[electrical_columns(n, list(full_df.columns))],
        weights=snapshot_weights(n, "generators", sns=full_df.index),
    )
    return _ts_payload(df, extra={
        "total_mwh": total_mwh,
        "total_cost_eur": total_cost,
        "voll_eur_per_mwh": voll,
        "bus_carriers": bus_carriers,
        "shed_hours": {
            "total": sh["total"],
            "by_period": {str(k): v for k, v in sh["by_period"].items()},
        },
    }, range_meta=range_meta)
