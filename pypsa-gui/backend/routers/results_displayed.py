"""Displayed-data views of results tabs (assistant-assurance 06, A5.2 S2).

Each view is the exact payload a results tab renders, read through the same
function its existing `/results/*` route serves, plus a source binding: the
open project, the plain input fingerprint and the SHA-256 of that payload in
one canonical encoding. The tab renders `data` and marks its root with the
binding; `/chat/ui-ack` re-reads the view through the same function and
completes only on an identical binding (`harness/displayed_adapters.py`).

No new figures: a view never re-implements its route. A tab with nothing to
show answers as its route does (204, with `X-Results-State` when out of date).
"""

from __future__ import annotations

import hashlib
import json
import math

from fastapi import APIRouter, Depends, Response
from fastapi.encoders import jsonable_encoder

from db.models import User
from deps import require_user

router = APIRouter()


def _finite(value):
    """NaN and +/-inf become null, as the JSON a browser receives cannot hold them."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def canonical(payload) -> str:
    """The one encoding both the served view and the ACK re-read hash."""
    return json.dumps(
        _finite(jsonable_encoder(payload)),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


def _bound(tab: str, payload) -> dict:
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    encoded = canonical(payload)
    # Served in the route's own key order (a tab may draw dict keys in order);
    # only the hash uses the sorted canonical form.
    data = _finite(jsonable_encoder(payload))
    return {
        "status": "ok",
        "data": data,
        "binding": {
            "version": 1,
            "project_id": str(ctx.project_uuid or ""),
            "project_name": str(ctx.loaded_project or ""),
            "scope": "current",
            "view": tab,
            "input_sha256": workflow_tools._network_input_fingerprint(ctx),
            "data_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        },
    }


def _unavailable(response: Response) -> dict:
    return {
        "status": "unavailable",
        "results_state": response.headers.get("X-Results-State"),
    }


# ── The views: each calls its tab's route function, never a copy of it ─────


def emissions_view() -> dict:
    """Emissions renders `/results/emissions` (LP-stage dispatch, the tab's default)."""
    from harness.durable import network_guard
    from routers import results

    with network_guard():
        payload = results.get_emissions("lopf")
        if isinstance(payload, Response):
            return _unavailable(payload)
        return _bound("emissions", payload)


def grid_risk_view() -> dict:
    """Grid risk to production renders `/results/grid_risk_production` for the open project."""
    from harness.durable import network_guard
    from routers import results

    with network_guard():
        return _bound("grid_risk", results.grid_risk_production_payload(None))


VIEWS = {"emissions": emissions_view, "grid_risk": grid_risk_view}


def _serve(view, actor: User):
    from routers.results import _open_project_read, _result_actor
    from services.pypsa_service import PyPSAService

    with _result_actor(actor):
        # The open project's data only for a reader the project ACL admits
        # (404 `not_authorized`), as the load-flow and grid-risk views do.
        if PyPSAService.get_active_context().project_uuid:
            _open_project_read(None, "Open the project and ask again.")
        out = view()
    if out["status"] != "ok":
        headers = (
            {"X-Results-State": out["results_state"]} if out["results_state"] else None
        )
        return Response(status_code=204, headers=headers)
    return out


@router.get("/emissions")
def get_displayed_emissions(actor: User = Depends(require_user)):
    """The Emissions tab's payload and its source binding."""
    return _serve(emissions_view, actor)


@router.get("/grid_risk")
def get_displayed_grid_risk(actor: User = Depends(require_user)):
    """The Grid risk to production tab's payload and its source binding."""
    return _serve(grid_risk_view, actor)
