"""
Per-carrier economic roll-up for the LIVE network (`/results/economics_by_carrier`).

Lifted from `routers.results` in the Phase 3 follow-up: Phase 2 deferred this
handler because it delegated to `routers.compare._compute_economics_summary`.
That engine is now `services/compare/economics.py`, which takes the solver
config and the result lookup as keyword arguments, so the router state the old
body read inline (`_state["solver_config"]`, `_state["last_lost_load"]`)
arrives here as plain parameters.
"""
from __future__ import annotations

import logging

from services.compare.economics import _compute_economics_summary

logger = logging.getLogger("pypsa_gui.results")


def compute_economics_by_carrier(n, cfg, lost_load_cap, *, result_df):
    """
    Return ``{"by_carrier": {carrier: {...}}}``, or an ``{"error"}`` dict if the
    roll-up raises — the same graceful degradation the endpoint has always had.
    The traceback that used to ride along in a ``"trace"`` key goes to the log
    instead; see the except-branch. Never returns ``None``: the not-solved case
    is ``{}`` and is decided by the router's gate.
    """
    try:
        import pandas as _pd

        # _compute_economics_summary needs (n, periods, is_multi, has_solve).
        is_multi = isinstance(n.snapshots, _pd.MultiIndex)
        try:
            periods = sorted(int(p) for p in n.investment_periods) if is_multi else []
        except Exception:
            periods = []
        # Foreground project: the VOLL capture lives in the live solver
        # state, not on the network (solver_service strips the slacks).
        result = _compute_economics_summary(
            n, periods, is_multi, True,
            lost_load_cap=lost_load_cap, cfg=cfg, result_df=result_df,
        )
        # Return just the by_carrier dict — that's what the Results tab needs.
        # Drop per_asset_lcoh (lives in /api/results/lcoh) to keep the payload small.
        # Forward `available` alongside by_carrier. Dropping it here stopped
        # the whole Compare-side availability fix at Compare: the Results tab
        # received figures with no way to tell a real zero from one that was
        # never resolved, which is the exact conflation ADR-0001 forbids.
        # Still drops per_asset_lcoh (lives in /api/results/lcoh) to keep the
        # payload small.
        return {
            "available": bool(result.available),
            "by_carrier": {k: v.model_dump() for k, v in result.by_carrier.items()},
        }
    except Exception:
        # The graceful degradation is deliberate — one bad carrier must not
        # blank the Results tab — but it used to degrade into
        # `{"error": str(exc), "trace": format_exc()[-5:]}`, handing any
        # signed-in caller five frames of traceback: absolute paths, module
        # layout, library versions. CodeQL's `py/stack-trace-exposure`, and
        # correct; a `FileNotFoundError` alone renders as its path.
        #
        # Nothing consumed it. `frontend/src/api/simulation.ts` types the
        # response with `error?: string` and no `trace`, and both readers take
        # `econByCarrier?.by_carrier ?? null`. So the detail goes to the log,
        # where whoever is debugging can actually find it, and the response
        # says only that it failed.
        logger.exception("economics_by_carrier roll-up failed")
        return {"error": "the per-carrier economics roll-up failed; see the server log"}
