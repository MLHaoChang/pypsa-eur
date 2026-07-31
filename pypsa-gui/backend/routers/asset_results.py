"""Read-only per-asset results. Two endpoints; all logic lives in the service."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from services.asset_results import service as svc
from services.asset_results.registry import ALL_CLASSES, CATEGORY_IDS
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.asset_results")

router = APIRouter()


@router.get("/assets", operation_id="list_asset_results_assets")
def list_assets():
    """Every selectable asset, transient rows filtered out."""
    return {"assets": svc.list_assets(PyPSAService.get_network())}


@router.get("/{component_class}/{name}", operation_id="get_asset_results")
def get_asset_results(
    component_class: str,
    name: str,
    category: str = Query("summary"),
    metrics: str = Query(""),
    source: str = Query("lopf"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    period: str | None = Query(None),
    mode: str = Query("chronological"),
):
    if component_class not in ALL_CLASSES:
        raise HTTPException(404, f"Unknown component class '{component_class}'")
    if category not in CATEGORY_IDS:
        raise HTTPException(422, f"Unknown category '{category}'")
    if mode not in svc.VIEW_MODES:
        raise HTTPException(422, f"Unknown view mode '{mode}'")
    if source not in ("lopf", "ac_pf"):
        source = "lopf"  # fail soft, matching every other results endpoint

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise HTTPException(404, f"No {component_class} named '{name}'")

    metric_ids = [m for m in (metrics.split(",") if metrics else []) if m]
    try:
        return svc.build_response(
            n, component_class, name, category=category, metric_ids=metric_ids,
            source=source, from_iso=from_, to_iso=to, period=period, mode=mode,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("asset results failed for %s/%s", component_class, name)
        raise HTTPException(500, "Failed to compute asset results")
