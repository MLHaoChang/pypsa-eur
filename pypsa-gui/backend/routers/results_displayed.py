"""Displayed-data views of results tabs (assistant-assurance 06, A5.2 S2, S3).

Each view is the exact payload a results tab renders, read through the same
function its existing `/results/*` route serves, plus a source binding: the
open project, the plain input fingerprint and the SHA-256 of that payload in
one canonical encoding. The tab renders `data` and marks its root with the
binding; `/chat/ui-ack` re-reads the view through the same function and
completes only on an identical binding (`harness/displayed_adapters.py`).

No new figures: a view never re-implements its route. A tab with nothing to
show answers as its route does (204, with `X-Results-State` when out of date).

A view that takes a period, window or week (S3) records the values its route
function was CALLED with in the binding (null: the route's default), so the ACK
re-reads exactly that view. A tab that renders several payloads reads them all
under one `network_guard()` and binds `{part: payload}` with one hash.
"""

from __future__ import annotations

import hashlib
import json
import math

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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


def _sha256(payload) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def _bound(tab: str, payload, **params) -> dict:
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
            **params,
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


def _window(window_from, window_to) -> None:
    """A window whose start is after its end is refused, never served as some default."""
    if window_from is not None and window_to is not None and window_from > window_to:
        raise HTTPException(
            422,
            {
                "error_kind": "invalid_window",
                "message": "the window starts after it ends",
            },
        )


def flows_view(period=None, window_from=None, window_to=None) -> dict:
    """Energy flow renders `/results/energy_flows` for one period and window."""
    from harness.durable import network_guard
    from routers import results

    _window(window_from, window_to)
    with network_guard():
        payload = results.get_energy_flows(period, window_from, window_to)
        if isinstance(payload, Response):
            return _unavailable(payload)
        return _bound(
            "flows",
            payload,
            period=period,
            window_from=window_from,
            window_to=window_to,
        )


def loadshape_view(period=None, window_from=None, window_to=None, week=None) -> dict:
    """Load shape renders `/results/load_shape` for one period, window and week."""
    from harness.durable import network_guard
    from routers import results

    _window(window_from, window_to)
    with network_guard():
        payload = results.get_load_shape(period, window_from, window_to, week)
        if isinstance(payload, Response):
            return _unavailable(payload)
        return _bound(
            "loadshape",
            payload,
            period=period,
            window_from=window_from,
            window_to=window_to,
            week=week,
        )


def curtailment_view(window_from=None, window_to=None) -> dict:
    """Curtailment renders `/results/curtailment` and, for its plant rows,
    `/results/generators` (LP dispatch, as the tab asks) over the same window.
    Both parts are read under one guard, so they come from one solve. A
    generators 204 leaves that part null, as the tab shows curtailment alone."""
    from harness.durable import network_guard
    from routers import results

    _window(window_from, window_to)
    with network_guard():
        curtailment = results.get_curtailment(window_from, window_to)
        if isinstance(curtailment, Response):
            return _unavailable(curtailment)
        generators = results.get_generator_results("lopf", window_from, window_to)
        parts = {
            "curtailment": curtailment,
            "generators": None if isinstance(generators, Response) else generators,
        }
        return _bound(
            "curtailment",
            parts,
            # The part the chat read (get_results curtailment) names, in the same encoding.
            read_sha256=_sha256(curtailment),
            window_from=window_from,
            window_to=window_to,
        )


VIEWS = {
    "emissions": emissions_view,
    "grid_risk": grid_risk_view,
    "flows": flows_view,
    "loadshape": loadshape_view,
    "curtailment": curtailment_view,
}


def _serve(view, actor: User, **params):
    from routers.results import _open_project_read, _result_actor
    from services.pypsa_service import PyPSAService

    with _result_actor(actor):
        # The open project's data only for a reader the project ACL admits
        # (404 `not_authorized`), as the load-flow and grid-risk views do.
        if PyPSAService.get_active_context().project_uuid:
            _open_project_read(None, "Open the project and ask again.")
        out = view(**params)
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


_INDEX = {"ge": 0}


@router.get("/flows")
def get_displayed_flows(
    period: int | None = Query(
        None, ge=0, description="Investment period (default: the first)."
    ),
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    actor: User = Depends(require_user),
):
    """The Energy flow tab's payload and its source binding."""
    return _serve(flows_view, actor, period=period, window_from=from_, window_to=to_)


@router.get("/loadshape")
def get_displayed_loadshape(
    period: int | None = Query(
        None, ge=0, description="Investment period (default: the first)."
    ),
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    week: int | None = Query(None, **_INDEX),
    actor: User = Depends(require_user),
):
    """The Load shape tab's payload and its source binding."""
    return _serve(
        loadshape_view,
        actor,
        period=period,
        window_from=from_,
        window_to=to_,
        week=week,
    )


@router.get("/curtailment")
def get_displayed_curtailment(
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    actor: User = Depends(require_user),
):
    """The Curtailment tab's payloads (curtailment and generators) and their one binding."""
    return _serve(curtailment_view, actor, window_from=from_, window_to=to_)
