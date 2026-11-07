"""`GET /api/results/operational_gap`: the plan-vs-operation gap of the last
operational run (DC grid readiness 07, slice S3), read from
`services/solver/operational_gap.py`. It never solves: the comparison is run
inside the operational run and kept with it."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

router = APIRouter()
_log = logging.getLogger(__name__)


def _live_fingerprint() -> str | None:
    """The active project's input fingerprint now; None when it cannot be
    computed, which reads as stale, never fresh (C26)."""
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    try:
        return workflow_tools._network_input_fingerprint(PyPSAService.get_active_context())
    except Exception:  # noqa: BLE001 — unknown reads stale, never fresh
        _log.warning("operational gap: input fingerprint not computed", exc_info=True)
        return None


@router.get("/operational_gap")
def get_operational_gap():
    """The operational run against the full-horizon dispatch on the same sizes:
    each metric with its definition, the windows counted, and a flag (fresh,
    missing, stale, operational_window_failed, reference_not_solved). Metrics
    are None unless the flag is fresh."""
    from routers.results import _solver_in_flight, _state
    from services.pypsa_service import PyPSAService
    from services.solver.operational_gap import read

    if _solver_in_flight():
        raise HTTPException(
            409,
            {
                "code": "solver_in_flight",
                "error_kind": "solver_in_flight",
                "message": "a solve is running; read the gap after it ends",
            },
        )
    n = PyPSAService.get_network()
    return read(n, _state.get("solver_config"), live_fingerprint=_live_fingerprint())
