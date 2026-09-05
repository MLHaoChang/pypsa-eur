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

from services.compare.economics import _compute_economics_summary


def compute_economics_by_carrier(n, cfg, lost_load_cap, *, result_df):
    """
    Return ``{"by_carrier": {carrier: {...}}}``, or an ``{"error", "trace"}``
    dict if the roll-up raises — the same graceful degradation the endpoint has
    always had. Never returns ``None``: the not-solved case is ``{}`` and is
    decided by the router's gate.
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
        return {
            "by_carrier": {k: v.model_dump() for k, v in result.by_carrier.items()},
        }
    except Exception as exc:
        import traceback
        return {"error": str(exc), "trace": traceback.format_exc().splitlines()[-5:]}
