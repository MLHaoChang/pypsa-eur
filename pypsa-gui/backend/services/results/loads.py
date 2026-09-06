"""
Lifted from `routers.results` (get_load_results).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

import logging

from services.results.load_frames import lp_scaled_load_frame
from services.serialization import (
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)

# The SAME logger the router uses, not a child of it: `logger.exception(...)`
# text inside the lifted bodies must produce byte-identical log records.
logger = logging.getLogger("pypsa_gui.results")



def compute_load_results(n, cfg, source, from_, to_, *, result_df):
    """
    Load power as the LP saw it, as a (windowable) time-series payload.

    Lifted from `routers.results.get_load_results`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    try:
        df = lp_scaled_load_frame(n, cfg, source, result_df=result_df)
        if df is None or df.empty:
            return None
        range_meta = None
        if _wants_slice(from_, to_):
            df, range_meta = _slice_ts(df, from_, to_)
        return _ts_payload(df, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return None
