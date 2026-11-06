"""`GET /api/results/reserves`: reserve products on backup storage (DC grid
readiness 06), `services/results/reserves.py`. 204 without reserve products."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

router = APIRouter()


@router.get("/reserves")
def get_reserves():
    """Per backup storage: its floor, energy, state of charge and committed MW
    per product; the capacity-payment lines. 204 without reserve products."""
    from routers.results import _dispatch_ready, _solver_in_flight, _state
    from services.pypsa_service import PyPSAService
    from services.results.reserves import reserve_view

    if _solver_in_flight():
        raise HTTPException(409, {"code": "solver_in_flight", "error_kind": "solver_in_flight",
                                  "message": "a solve is running; read the reserves after it ends"})
    n = PyPSAService.get_network()
    ctx = PyPSAService.solve_target_context()
    payload = reserve_view(n, _state.get("solver_config"), solved=_dispatch_ready(n),
                           storage_dir=getattr(ctx, "storage_dir", None) if ctx else None)
    return Response(status_code=204) if payload is None else payload
