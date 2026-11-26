"""Displayed-data views of results tabs (assistant-assurance 06, A5.2 S2, S3).

Each view is the exact payload a results tab renders, read through the same
function its existing `/results/*` route serves, plus a source binding: the
open project, the plain input fingerprint and the SHA-256 of that payload in
one canonical encoding. The tab renders `data` and marks its root with the
binding; `/chat/ui-ack` re-reads the view through the same function and
completes only on an identical binding (`harness/displayed_adapters.py`).

No new figures: a view never re-implements its route. A tab with nothing to
show answers as its route does (204, with `X-Results-State` when out of date).

A view that takes a source, period, window, week or threshold (S3) records the
values its route function was CALLED with in the binding (null: the route's
default), so the ACK re-reads exactly that view; a source the tab does not
offer is refused. A tab that renders several payloads reads them all
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


def _offered(source, offered) -> None:
    """A source the tab does not offer is refused, never served as some default."""
    if source not in offered:
        raise HTTPException(
            422,
            {
                "error_kind": "source_not_offered",
                "message": f"this view offers the source {' or '.join(offered)} only",
            },
        )


# The Prices tab pins the LP duals (an AC power flow writes none); Storage
# cycling asks for the route's default, the LP dispatch.
PRICE_SOURCES = ("lopf",)
STORAGE_SOURCES = ("lopf",)


def prices_view(
    source="lopf", window_from=None, window_to=None, threshold=2000.0, limit=200
) -> dict:
    """Prices renders `/results/prices` for one source and window and, for its
    drivers table, `/results/price_drivers` at one threshold and row cap, both
    read under one guard. On a solve whose prices are not marginal prices (a
    MIP) both routes answer withheld with the reason; the binding records it as
    `price_limit`, so the words say the prices are unavailable, never "showing"."""
    from harness.durable import network_guard
    from routers import results

    _offered(source, PRICE_SOURCES)
    _window(window_from, window_to)
    with network_guard():
        prices = results.get_prices(source, window_from, window_to)
        if isinstance(prices, Response):
            return _unavailable(prices)
        drivers = results.get_price_drivers(threshold, limit)
        parts = {
            "prices": prices,
            "price_drivers": None if isinstance(drivers, Response) else drivers,
        }
        withheld = prices.get("status") == "unavailable"
        return _bound(
            "prices",
            parts,
            # The part the chat read (get_results prices) names, in the same encoding.
            read_sha256=_sha256(prices),
            price_limit=prices.get("reason") if withheld else None,
            source=source,
            window_from=window_from,
            window_to=window_to,
            threshold=threshold,
            limit=limit,
        )


def capex_view() -> dict:
    """Capacity expansion renders `/results/cost_breakdown` and
    `/results/economics_by_carrier`, read under one guard, as the routes serve
    them (no annuity or other figure is recomputed here). A cost-breakdown 204
    is the tab's "not solved", so the view answers 204 too."""
    from harness.durable import network_guard
    from routers import results

    with network_guard():
        cost = results.get_cost_breakdown()
        if isinstance(cost, Response):
            return _unavailable(cost)
        economics = results.get_economics_by_carrier()
        return _bound(
            "capex",
            {"cost_breakdown": cost, "economics_by_carrier": economics},
            # Each part a chat read can name, so a read of one claims only it.
            cost_breakdown_sha256=_sha256(cost),
            economics_by_carrier_sha256=_sha256(economics),
        )


def storage_view(source="lopf", window_from=None, window_to=None) -> dict:
    """Storage cycling renders `/results/storage_dispatch` for one source and window."""
    from harness.durable import network_guard
    from routers import results

    _offered(source, STORAGE_SOURCES)
    _window(window_from, window_to)
    with network_guard():
        payload = results.get_storage_dispatch_results(source, window_from, window_to)
        if isinstance(payload, Response):
            return _unavailable(payload)
        return _bound(
            "storage",
            payload,
            source=source,
            window_from=window_from,
            window_to=window_to,
        )


# Dispatch follows the global result source (Load flow's LP / AC power flow
# toggle), so it offers both; curtailment, lost load and the two aggregates
# have no source and are read as the tab asks for them.
DISPATCH_SOURCES = ("lopf", "ac_pf")
# The per-snapshot parts the tab counts as "solved" (Dispatch.tsx hasResults).
_DISPATCH_SOLVED = (
    "generators",
    "storage",
    "storage_dispatch",
    "store_dispatch",
    "store_energy",
    "loads",
)


def _parts(reads: dict) -> dict:
    """A part whose route answered 204 is null, as the tab reads a 204."""
    return {k: None if isinstance(v, Response) else v for k, v in reads.items()}


def dispatch_view(source="lopf", window_from=None, window_to=None) -> dict:
    """Dispatch renders eleven `/results/*` payloads: the per-snapshot series
    for one source and window, curtailment and lost load for the window, and
    the cost breakdown and economics by carrier. All are read under one guard,
    so they come from one solve. With none of the series the tab counts as
    solved, the view answers 204, as the tab then shows "not solved"."""
    from harness.durable import network_guard
    from routers import results

    _offered(source, DISPATCH_SOURCES)
    _window(window_from, window_to)
    win = (window_from, window_to)
    with network_guard():
        if source == "ac_pf" and not isinstance(
            results._state.get("ac_pf_results"), dict
        ):
            # No AC power flow on record: the old routes fall back to the live
            # LP network, which must never be bound under an ac_pf label.
            raise HTTPException(
                422,
                {
                    "error_kind": "source_not_available",
                    "message": "The AC power flow has not run yet. Run it, or show the LP dispatch.",
                },
            )
        reads = {
            "cost_breakdown": results.get_cost_breakdown(),
            "generators": results.get_generator_results(source, *win),
            "curtailment": results.get_curtailment(*win),
            "storage": results.get_storage_results(source, *win),
            "storage_dispatch": results.get_storage_dispatch_results(source, *win),
            "store_dispatch": results.get_store_dispatch_results(source, *win),
            "store_energy": results.get_store_energy_results(source, *win),
            "loads": results.get_load_results(source, *win),
            "links": results.get_link_results(source, *win),
            "lost_load": results.get_lost_load(*win),
            "economics_by_carrier": results.get_economics_by_carrier(),
        }
        parts = _parts(reads)
        if all(parts[k] is None for k in _DISPATCH_SOLVED):
            return _unavailable(reads["generators"])
        return _bound(
            "dispatch",
            parts,
            # Each part a chat read (get_results <kind>) can name, so a read of
            # one part claims only it.
            **{f"{k}_sha256": _sha256(v) for k, v in parts.items()},
            source=source,
            window_from=window_from,
            window_to=window_to,
        )


def overview_view() -> dict:
    """The multi-period Overview renders the whole-horizon `/results/*`
    payloads its route defaults give (LP dispatch, no window): generators,
    storage dispatch, loads, curtailment, lost load and the cost breakdown,
    read under one guard. With none of generators, loads and the cost
    breakdown the view answers 204, as the tab then shows "not solved"."""
    from harness.durable import network_guard
    from routers import results

    with network_guard():
        reads = {
            "generators": results.get_generator_results("lopf", None, None),
            "storage_dispatch": results.get_storage_dispatch_results(
                "lopf", None, None
            ),
            "loads": results.get_load_results("lopf", None, None),
            "curtailment": results.get_curtailment(None, None),
            "lost_load": results.get_lost_load(None, None),
            "cost_breakdown": results.get_cost_breakdown(),
        }
        parts = _parts(reads)
        if all(parts[k] is None for k in ("generators", "loads", "cost_breakdown")):
            return _unavailable(reads["generators"])
        return _bound("overview", parts)


VIEWS = {
    "dispatch": dispatch_view,
    "overview": overview_view,
    "prices": prices_view,
    "capex": capex_view,
    "storage": storage_view,
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
_SOURCE = Query(None, description="Refused: these views serve the solved network only.")


def _no_source(source) -> None:
    """A `?source=` is refused, never ignored: the binding has no source key, so
    serving the solved view under it would bind data the caller did not ask for."""
    if source is not None:
        raise HTTPException(
            422,
            {
                "error_kind": "source_not_offered",
                "message": "this view has no source choice; it serves the solved network",
            },
        )


@router.get("/flows")
def get_displayed_flows(
    period: int | None = Query(
        None, ge=0, description="Investment period (default: the first)."
    ),
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    source: str | None = _SOURCE,
    actor: User = Depends(require_user),
):
    """The Energy flow tab's payload and its source binding."""
    _no_source(source)
    return _serve(flows_view, actor, period=period, window_from=from_, window_to=to_)


@router.get("/loadshape")
def get_displayed_loadshape(
    period: int | None = Query(
        None, ge=0, description="Investment period (default: the first)."
    ),
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    week: int | None = Query(None, **_INDEX),
    source: str | None = _SOURCE,
    actor: User = Depends(require_user),
):
    """The Load shape tab's payload and its source binding."""
    _no_source(source)
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
    source: str | None = _SOURCE,
    actor: User = Depends(require_user),
):
    """The Curtailment tab's payloads (curtailment and generators) and their one binding."""
    _no_source(source)
    return _serve(curtailment_view, actor, window_from=from_, window_to=to_)


_OFFERED_SOURCE = Query(
    None, description="The tab's one source, the LP solve (default: it)."
)


@router.get("/prices")
def get_displayed_prices(
    source: str | None = _OFFERED_SOURCE,
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    threshold: float = Query(2000.0, allow_inf_nan=False),
    limit: int = Query(200, **_INDEX),
    actor: User = Depends(require_user),
):
    """The Prices tab's payloads (prices and price drivers) and their one binding."""
    return _serve(
        prices_view,
        actor,
        source="lopf" if source is None else source,
        window_from=from_,
        window_to=to_,
        threshold=threshold,
        limit=limit,
    )


@router.get("/capex")
def get_displayed_capex(
    source: str | None = _SOURCE,
    actor: User = Depends(require_user),
):
    """The Capacity expansion tab's payloads (cost breakdown and economics by carrier) and their one binding."""
    _no_source(source)
    return _serve(capex_view, actor)


@router.get("/storage")
def get_displayed_storage(
    source: str | None = _OFFERED_SOURCE,
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    actor: User = Depends(require_user),
):
    """The Storage cycling tab's payload and its source binding."""
    return _serve(
        storage_view,
        actor,
        source="lopf" if source is None else source,
        window_from=from_,
        window_to=to_,
    )


@router.get("/dispatch")
def get_displayed_dispatch(
    source: str | None = Query(
        None, description="The result source, lopf or ac_pf (default: lopf)."
    ),
    from_: int | None = Query(None, alias="from", **_INDEX),
    to_: int | None = Query(None, alias="to", **_INDEX),
    actor: User = Depends(require_user),
):
    """The Dispatch tab's payloads (eleven parts) and their one binding."""
    return _serve(
        dispatch_view,
        actor,
        source="lopf" if source is None else source,
        window_from=from_,
        window_to=to_,
    )


@router.get("/overview")
def get_displayed_overview(
    source: str | None = _SOURCE,
    actor: User = Depends(require_user),
):
    """The multi-period Overview tab's payloads (six parts) and their one binding."""
    _no_source(source)
    return _serve(overview_view, actor)
