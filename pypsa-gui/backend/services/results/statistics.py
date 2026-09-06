"""
Lifted from `routers.results` (get_statistics).

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

from services.serialization import df_to_json
from services.solver_service import with_periodized_cost_defaults

# The SAME logger the router uses, not a child of it: `logger.exception(...)`
# text inside the lifted bodies must produce byte-identical log records.
logger = logging.getLogger("pypsa_gui.results")



def compute_statistics(n, cfg):
    """
    PyPSA `n.statistics()` under the periodized cost fill.

    Lifted from `routers.results.get_statistics`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    try:
        with with_periodized_cost_defaults(n, cfg):
            stats = n.statistics()
            # `df_to_json` runs `reset_index` internally + applies `_clean`
            # to coerce NaN/Inf → None. The previous code called
            # `stats.reset_index()` BEFORE handing off, producing a DOUBLE
            # reset_index that left a stray `level_0` / `index` column on
            # the records — AND the fallback `else` branch skipped `_clean`
            # entirely, so a single NaN in `n.statistics()` (common for
            # missing metrics) would crash Starlette's `JSONResponse.render`
            # at `json.dumps(allow_nan=False)` with a 500 plain-text error.
            # Same class of bug as `/results/storage` 500s `_safe_values`
            # was added to fix. Single code path; pass `stats` straight in.
            return df_to_json(stats)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return None
