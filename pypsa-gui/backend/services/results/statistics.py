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



def _with_fixed_om(n, stats):
    """
    Append PyPSA's `n.statistics.fom()` to the statistics table as a
    "Fixed O&M" column (one per period on a multi-period network).

    PyPSA's "Capital Expenditure" column is investment-only — `statistics.capex`
    multiplies capacity by `comp.capital_cost`, the accessor that passes
    `fom_cost=None` — while the LP objective charged `capital_cost + fom_cost`.
    This surface is a raw pass-through and keeps PyPSA's column as PyPSA
    defines it, so the fixed cost the objective paid is
    `Capital Expenditure + Fixed O&M`; without the second column that number
    could not be recovered from this endpoint at all. Zero where PyPSA reports
    NaN (no FOM). Leaves the table untouched if the accessor is unavailable or
    the two shapes cannot be aligned — the raw table is never worse than before.
    """
    import pandas as pd

    try:
        fom = n.statistics.fom()
    except Exception:  # noqa: BLE001 — optional upstream accessor
        return stats
    try:
        if isinstance(fom, pd.DataFrame):
            if not isinstance(stats.columns, pd.MultiIndex):
                return stats
            fom = fom.copy()
            fom.columns = pd.MultiIndex.from_tuples(
                [("Fixed O&M", c) for c in fom.columns], names=stats.columns.names,
            )
        elif isinstance(fom, pd.Series):
            if isinstance(stats.columns, pd.MultiIndex):
                return stats
            fom = fom.rename("Fixed O&M").to_frame()
        else:
            return stats
        fom = fom.reindex(stats.index).fillna(0.0)
        return pd.concat([stats, fom], axis=1)
    except Exception:  # noqa: BLE001 — never let the breakout break the table
        logger.exception("could not append Fixed O&M to /results/statistics; raw table returned")
        return stats


def compute_statistics(n, cfg):
    """
    PyPSA `n.statistics()` under the periodized cost fill.

    Lifted from `routers.results.get_statistics`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    try:
        with with_periodized_cost_defaults(n, cfg):
            stats = _with_fixed_om(n, n.statistics())
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
