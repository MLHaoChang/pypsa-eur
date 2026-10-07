"""
Phase 1 chatbot integration v6 — tool dispatchers.

Async-handler note: FastAPI multipart upload handlers (`upload_load_profile`,
`import_netcdf`, ...) are `async def` because they `await file.read()`. Our
chat-tool dispatchers run synchronously in Phase 1 tests, so we drive any
coroutine return via `_sync` (which uses `asyncio.run` when no loop is
active, and a thread-bridged run when called from inside Phase 3's async
SSE generator). All call sites that touch an async handler funnel through
`_sync(...)` — Phase 3's async chat session can replace it with a direct
`await` after a final-phase refactor without changing the dispatcher API.

Every tool function here calls the underlying FastAPI route handler or service
helper **directly** (NOT via HTTP). Tools inherit the existing lock policy,
audit log, _user_ts cleanup, and vintage-bounds cleanup because they go through
the same _create/_update/_delete_component generic helpers (and dedicated
wrappers for Bus rename / Transformer / GlobalConstraint).

UNDO is the one they cannot inherit, because `push_undo_snapshot` is driven by
the HTTP middleware in `main.py`, which an in-process call never passes
through. For a long time nothing replaced it: a chat edit pushed no snapshot,
and a later `undo_last` either refused or reverted an OLDER canvas edit while
reporting `{"undone": true}`. The dispatcher now pushes it instead — ONE
snapshot per turn, per project, before the turn's first network-changing tool
(`UNDO_CAPTURED_TOOLS` below; `chat_service._snapshot_for_turn_undo`). So one
undo reverts everything the assistant changed in its last turn.

Phase 1 invariants enforced here:
  * F1 — update_component dispatches Bus rename to rename_bus (preserves
    dependent bus0/bus1 references via n.rename_component_names).
  * F2 — update_component dispatches Transformer to update_transformer (runs
    _validate_transformer_voltage + _sanitise_transformer_type).
  * F3 — update_component dispatches GlobalConstraint to update_global_constraint
    (dedicated partial-PUT mitigation; _COMPONENT_ATTRS does NOT include it).
  * Bus non-rename routes to update_bus (preserves coord-change line-length
    recompute via _recompute_lengths_for_bus).
  * v4-MAJOR-3 — upload_*_profile tools take multi-column CSV (columns are
    asset names, index is timestamps); per-asset upload uses upload_timeseries.
  * v4-MAJOR-4 — get_results uses a lookup dict so ac_pf_status routes to
    /ac_pf/status (not /ac_pf_status).
  * v4-MINOR-1 — delete_project takes a cascade param.
  * M1 — save_project_as does a list_projects pre-check and returns
    error_kind='project_exists' BEFORE the destructive POST.

NO Anthropic SDK import. Phase 3 wires that. The tools return plain dicts/
lists which Phase 2 turns into Messages-API content blocks.
"""
from __future__ import annotations

import contextlib
import inspect
import json
import logging
import math
import uuid
from contextvars import ContextVar
from typing import Any, NoReturn

from fastapi import HTTPException, params as fastapi_params

from services.pypsa_service import PyPSAService
from services.redaction import redact_secrets_in_str as _redact_secrets_in_str

logger = logging.getLogger("pypsa_gui.chat_tools")

# ── Dispatch helpers ────────────────────────────────────────────────────────

# Component classes covered by the generic _create/_update/_delete_component
# in routers/network.py. GlobalConstraint is NOT here (dedicated CRUD); Bus
# and Transformer ARE here but their update path has dedicated wrappers
# (see update_component dispatcher below).
_GENERIC_CRUD_ATTRS: dict[str, str] = {
    "Bus": "buses",
    "Carrier": "carriers",
    "Line": "lines",
    "Link": "links",
    "Transformer": "transformers",
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Load": "loads",
    "ShuntImpedance": "shunt_impedances",
}

# Pydantic schema per component class — used by create_component to validate
# kwargs before delegating to the underlying handler (which itself does the
# same validation; doing it twice is cheap and lets us surface a clearer
# error message when the agent passes malformed args).
_COMPONENT_CREATE_SCHEMAS = {
    "Bus": "BusCreate",
    "Carrier": "CarrierCreate",
    "Line": "LineCreate",
    "Link": "LinkCreate",
    "Transformer": "TransformerCreate",
    "Generator": "GeneratorCreate",
    "StorageUnit": "StorageUnitCreate",
    "Store": "StoreCreate",
    "Load": "LoadCreate",
    "ShuntImpedance": "ShuntImpedanceCreate",
    "GlobalConstraint": "GlobalConstraintCreate",
}


def _get_schema(class_name: str):
    """Lazy import the named Pydantic schema from models.schemas."""
    from models import schemas as s
    return getattr(s, class_name)


# Create schemas require identity fields (bus / bus0 / bus1 / …) that agents
# routinely omit on partial updates. Prefill those from the live row so
# `update_component({attrs: {p_nom_max: 500}})` validates like a real PUT
# that already knows the asset's topology.
_UPDATE_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "Line": ("bus0", "bus1"),
    "Link": ("bus0", "bus1"),
    "Transformer": ("bus0", "bus1"),
    "Generator": ("bus",),
    "StorageUnit": ("bus",),
    "Store": ("bus",),
    "Load": ("bus",),
    "ShuntImpedance": ("bus",),
}


def _identity_prefill(component_class: str, name: str) -> dict[str, Any]:
    """Return required identity attrs from the existing component row."""
    fields = _UPDATE_IDENTITY_FIELDS.get(component_class)
    if not fields:
        return {}
    attr = _GENERIC_CRUD_ATTRS.get(component_class)
    if not attr:
        return {}
    n = PyPSAService.get_network()
    df = getattr(n, attr, None)
    if df is None or name not in df.index:
        return {}
    row = df.loc[name]
    out: dict[str, Any] = {}
    for f in fields:
        if f in df.columns:
            val = row[f]
            # pandas / numpy scalars → plain Python for Pydantic
            out[f] = val.item() if hasattr(val, "item") else val
    return out


def _validated_update_payload(
    component_class: str, name: str, attrs: dict[str, Any],
) -> dict[str, Any]:
    """
    Coerce `attrs` through the Create schema without requiring the agent to
    re-send identity fields. Only user-supplied keys land in the payload so
    `_update_component`'s exclude_unset merge still preserves other columns.
    """
    schema_name = _COMPONENT_CREATE_SCHEMAS[component_class]
    Schema = _get_schema(schema_name)
    prefill = _identity_prefill(component_class, name)
    # Prefill first; agent attrs win on conflict. A `name` among the attrs is
    # a rename (as in the PUT body): it replaces the schema's `name` rather
    # than being passed a second time, and stays in the payload so
    # `_update_component` pops it and renames.
    fields = {**prefill, **attrs}
    instance = Schema(name=fields.pop("name", name), **fields)
    dumped = instance.model_dump()
    return {k: dumped[k] for k in attrs if k in dumped}


def _sync(value):
    """
    If `value` is a coroutine, drive it to completion and return its result;
    otherwise return as-is. Used to bridge async FastAPI upload handlers
    (multipart `await file.read()`) into sync chat-tool dispatchers.

    Lookup order for the run strategy:
      * No running event loop → asyncio.run(coroutine) (Phase 1 test path).
      * Inside a running loop → schedule on a one-shot thread pool so we
        never call asyncio.run from inside an existing loop (which raises).
    """
    import asyncio
    if not asyncio.iscoroutine(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    # We are inside a running event loop (Phase 3 async caller).
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, value).result()


# ── Read tools (22) ─────────────────────────────────────────────────────────


# Pagination bounds for the list-shaped read tools (#16).
#
# Rows are the wrong unit on their own. `_truncate_result` serialises any
# dict result and replaces it with a `preview` string past ~4000 chars, so a
# 200-row page is fine for Carriers and 45 KB for Buses — and a page that
# gets previewed is exactly the opaque blob this item exists to remove.
# MAX_PAGE_CHARS is therefore the real bound and the row counts are
# secondary caps; the packing loop below stops at whichever binds first.
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000
# Under _truncate_result's 4000, leaving headroom for the envelope's own
# keys and for JSON escaping of names we did not write.
MAX_PAGE_CHARS = 3000


def _paginate(rows: list[dict], offset: int, limit: int | None) -> dict:
    """
    Wrap `rows` in the page envelope shared by the list-shaped read tools.

    The envelope is returned ALWAYS, not only when a page was requested.
    A bare list cannot answer "did I see everything?" — 200 rows and
    200-of-5000 look identical at the call site — and a shape that changes
    depending on the arguments is harder for a model to reason about than
    one that does not. `total_count` is the field that makes every response
    self-describing.

    Being a dict also matters mechanically: `_truncate_result` replaces any
    list over 200 entries with a `sample`, which is the very truncation this
    exists to replace.
    """
    if offset < 0:
        raise HTTPException(400, f"offset must be >= 0, got {offset}")
    if limit is not None and limit < 1:
        raise HTTPException(400, f"limit must be >= 1, got {limit}")

    requested = DEFAULT_PAGE_SIZE if limit is None else limit
    effective = min(requested, MAX_PAGE_SIZE)
    candidate = rows[offset:offset + effective]

    # Pack by serialised size. Row width varies by an order of magnitude
    # across component classes, so no fixed row count is right for all of
    # them — and overshooting means the whole page comes back as a preview
    # string, which is worse than a short page.
    page: list[dict] = []
    used = 0
    for row in candidate:
        cost = len(json.dumps(row, default=str)) + 2  # +2 for ", "
        # Always take the first row even if it alone busts the budget:
        # returning an empty page would leave `offset` unable to advance and
        # the agent looping forever on a row it can never get past.
        if page and used + cost > MAX_PAGE_CHARS:
            break
        page.append(row)
        used += cost

    out = {
        "items": page,
        "total_count": len(rows),
        "offset": offset,
        "returned": len(page),
        "has_more": offset + len(page) < len(rows),
    }
    # Say so whenever the ask was reduced, by either bound. A model that
    # asked for 10 000 and got 13 with no note would read `has_more` as the
    # network being smaller than it is, or stop early believing it had
    # reached the end of what it requested.
    if len(page) < min(requested, len(candidate)):
        out["limit_clamped_to"] = len(page)
    return out


def list_components(
    component_class: str, *, offset: int = 0, limit: int | None = None,
) -> dict:
    """List one class of component, one page at a time (transient-filtered)."""
    from routers.network import _get_component
    if component_class == "GlobalConstraint":
        attr = "global_constraints"
    elif component_class not in _GENERIC_CRUD_ATTRS:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    else:
        attr = _GENERIC_CRUD_ATTRS[component_class]
    return _paginate(_get_component(component_class, attr), offset, limit)


# How many islands `diagnose_network` describes in full, and how many buses
# it names per island. A 400-bus shrapnel network would otherwise serialise
# past _truncate_result's budget and come back as a preview string — a
# diagnosis the agent cannot read is not a diagnosis.
_MAX_ISLANDS_REPORTED = 12
_MAX_BUSES_PER_ISLAND = 8


def diagnose_network() -> dict:
    """
    Electrical connectivity of the active network (#15).

    Answers the question nothing else in the tool surface does: is this one
    electrical system or several, and is anything stranded? `validate_for_run`
    covers dangling bus references, bounds, costs and solver assumptions, but
    never looks at the graph — and an infeasible solve is most often a load
    sitting in an island with nothing able to serve it.

    Dangling bus refs are deliberately NOT re-checked here: the preflight
    already reports them, and a second differently-worded copy is how two
    sources of truth start disagreeing.

    The graph walk itself is `topology_analyzer.analyse_topology`, the same
    one preflight and the study report read: two walks of one graph is how
    the chat and the preflight start describing different networks. This
    function only reshapes its islands into the tool's payload.
    """
    from services.topology_analyzer import analyse_topology

    n = PyPSAService.get_network()
    report = analyse_topology(n)
    if not report["n_islands"]:
        return {
            "bus_count": 0, "island_count": 0, "islands": [],
            "isolated_buses": [], "islands_without_generation": [],
            "islands_truncated": False, "verdict": "empty",
        }

    # `has_generation` is presence, not size: any generator, storage unit or
    # store makes an island servable in the sense this tool reports. Peak load
    # is the analyser's — summed across loads per snapshot, then maxed — so
    # two loads peaking in different hours do not add into a phantom peak.
    islands = []
    for island in report["islands"]:
        peak = island["peak_load_mw"]
        islands.append({
            "size": island["n_buses"],
            "buses": island["buses"][:_MAX_BUSES_PER_ISLAND],
            "has_generation": island["has_supply_asset"],
            "has_load": peak > 0,
            "peak_load_mw": round(peak, 6),
        })
    # Biggest first: on a fragmented network the large islands are the ones
    # the user recognises, and the truncation below keeps the head.
    islands.sort(key=lambda i: (-i["size"], i["buses"][0] if i["buses"] else ""))

    # A generation-only island is odd but solvable. Only a marooned LOAD is
    # a defect — flagging the rest would train the agent to ignore the field.
    stranded = [i for i in islands if i["has_load"] and not i["has_generation"]]

    # An island of one, which is wider than the analyser's degree-zero
    # `isolated_buses`: a bus whose only branch is a self-loop or points at a
    # missing bus is still stranded on its own.
    isolated = sorted(i["buses"][0] for i in report["islands"]
                      if i["n_buses"] == 1)

    if stranded:
        verdict = "infeasible_topology"
    elif report["n_islands"] > 1:
        verdict = "fragmented"
    else:
        verdict = "connected"

    return {
        "bus_count": len(n.buses.index),
        "island_count": report["n_islands"],
        "islands": islands[:_MAX_ISLANDS_REPORTED],
        "islands_truncated": len(islands) > _MAX_ISLANDS_REPORTED,
        "isolated_buses": isolated[:_MAX_ISLANDS_REPORTED],
        "isolated_buses_truncated": len(isolated) > _MAX_ISLANDS_REPORTED,
        "islands_without_generation": stranded[:_MAX_ISLANDS_REPORTED],
        "verdict": verdict,
    }


def get_component(component_class: str, name: str) -> dict:
    """
    N2: direct df.loc[name].to_dict() — single-row payload, no MB-scale
    dataframe re-fetch. Does NOT route through list_components.
    """
    attr = "global_constraints" if component_class == "GlobalConstraint" \
        else _GENERIC_CRUD_ATTRS.get(component_class)
    if attr is None:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    n = PyPSAService.get_network()
    df = getattr(n, attr, None)
    if df is None:
        raise HTTPException(400, f"No DataFrame for attr {attr!r}")
    if name not in df.index:
        raise HTTPException(404, f"{component_class} '{name}' not found")
    row = df.loc[name].to_dict()
    # Coerce NaN/Inf to None — matches the _df_to_json convention so the
    # agent never sees JSON-incompatible floats.
    cleaned = {}
    for k, v in row.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            cleaned[k] = None
        else:
            cleaned[k] = v
    cleaned["name"] = name
    return cleaned


def get_meta() -> dict:
    from routers.network import get_meta as _h
    return _h()


def list_snapshots() -> dict:
    from routers.network import get_snapshots as _h
    return _h()


def list_carriers() -> list[dict]:
    from routers.network import get_carriers as _h
    return _h()


def list_global_constraints() -> list[dict]:
    from routers.network import get_global_constraints as _h
    return _h()


def list_timeseries_profiles(profile_kind: str) -> dict:
    if profile_kind == "loads":
        from routers.network import get_load_profiles as _h
        return _h()
    if profile_kind == "generators":
        from routers.network import get_generator_profiles as _h
        return _h()
    if profile_kind == "links":
        from routers.network import get_link_profiles as _h
        return _h()
    raise HTTPException(400, f"Unknown profile_kind: {profile_kind!r}")


def list_transformer_types() -> list[dict]:
    from routers.network import list_transformer_types as _h
    return _h()


def download_timeseries_template(kind: str) -> Any:
    """
    Returns the CSV-template StreamingResponse / string the route would emit.
    For chat-tool callers we want the CSV body text; the handler returns a
    StreamingResponse whose body we drain.
    """
    if kind == "loads":
        from routers.network import download_load_profile_template as _h
    elif kind == "generators":
        from routers.network import download_generator_profile_template as _h
    elif kind == "links":
        from routers.network import download_link_profile_template as _h
    else:
        raise HTTPException(400, f"Unknown kind: {kind!r}")
    return _h()


def download_snapshot_weightings_csv() -> Any:
    from routers.network import download_snapshot_weightings_csv as _h
    return _h()


def list_investment_periods() -> dict:
    from routers.network import get_investment_periods as _h
    return _h()


def list_vintage_bounds() -> dict:
    # The handler returns ALL bounds unfiltered; it takes no filter params, so
    # the wrapper exposes none either (the old component_class/name params were
    # silently dropped — a request to filter returned the full dataset).
    from routers.vintage import list_vintage_bounds as _h
    return _h()


def get_vintage_results() -> dict:
    from routers.vintage import list_vintage_results as _h
    return _h()


def get_timeseries(component: str, name: str, attribute: str, period: int | None = None) -> dict:
    """
    Returns one time-series (component, name, attribute).

    `name` is the component INSTANCE name (e.g. a specific load); it maps to the
    route handler's `columns` single-column filter. The handler prefers a
    user-uploaded series and falls back to the network's `<component>_t.<attribute>`
    frame, so a profile baked into the imported .nc is returned too. `period` is
    accepted for schema compatibility but the handler returns the full
    (multi-period) frame with a parallel `periods` array — filter client-side.
    """
    from routers.network import get_timeseries as _h
    return _h(component=component, attribute=attribute, columns=name)


def list_all_timeseries(*, offset: int = 0, limit: int | None = None) -> dict:
    # NOTE: the route handler is `list_timeseries` (GET /api/network/timeseries),
    # not `list_all_timeseries`. It walks every `<component>_t` accessor and
    # reports non-empty frames + columns directly off the network, so time series
    # baked into an imported .nc are surfaced (not just user uploads).
    #
    # Paginated for the same reason as list_components (#16): a sector-coupled
    # network has thousands of profiles, and the blind 200-row cut gave the
    # agent no way to reach the rest.
    from routers.network import list_timeseries as _h
    return _paginate(list(_h()), offset, limit)


def get_aggregate_load(section: str | None = None, names: str | None = None) -> dict:
    """
    Time-aligned sum of load p_set, by explicit names (CSV) or by section.

    The handler returns a plain, already-NaN-safe dict ({index, values,
    total_loads, loads_with_profile, peak, mean}) — no normalization needed.
    """
    from routers.network import aggregate_load_profile as _h
    return _h(section=section, names=names)


def get_solver_config() -> dict:
    from routers.simulation import get_solver_config as _h
    return _h()


def get_solver_capabilities() -> dict:
    from routers.simulation import capabilities as _h
    return _h()


def get_asset_costs() -> dict:
    from routers.simulation import asset_costs as _h
    return _h()


def get_simulation_status() -> dict:
    from routers.simulation import get_status as _h
    return _h()


def get_simulation_lock_status() -> dict:
    from routers.simulation import lock_status as _h
    return _h()


def get_simulation_log_history() -> dict:
    # Handler returns {"lines": [str], "running": bool} — pass it through (the
    # `running` flag tells the model whether a solve is still in progress).
    from routers.simulation import get_log_history as _h
    return _h()


# v4-MAJOR-4: lookup dict so ac_pf_status routes to the actual /ac_pf/status
# path. All other 27 enums map 1:1 to /results/{kind}.
_RESULTS_PATH_LOOKUP: dict[str, str] = {
    "ac_pf_status": "/ac_pf/status",
}


_RESULTS_ENUM = (
    "cost_breakdown", "objective_decomposition", "economics_by_carrier",
    "statistics", "generators", "storage_dispatch", "store_dispatch",
    "store_energy", "storage", "lines", "links", "lcoh", "ac_pf_status",
    "losses", "carrier_kpis", "emissions", "transformers", "unit_commitment",
    "line_duals", "voltages", "line_reactive", "transformer_reactive",
    "prices", "price_drivers", "curtailment", "lost_load", "loads",
    "asset_economics", "billing", "cfe_score", "value_flows",
)


_RESULTS_HANDLER_NAMES: dict[str, str] = {
    # Names verified against `grep ^def routers/results.py` (Phase 1 recon).
    "cost_breakdown": "get_cost_breakdown",
    "objective_decomposition": "get_objective_decomposition",
    "economics_by_carrier": "get_economics_by_carrier",
    "statistics": "get_statistics",
    "generators": "get_generator_results",
    "storage_dispatch": "get_storage_dispatch_results",
    "store_dispatch": "get_store_dispatch_results",
    "store_energy": "get_store_energy_results",
    "storage": "get_storage_results",
    "lines": "get_line_results",
    "links": "get_link_results",
    "lcoh": "get_lcoh",
    "ac_pf_status": "get_ac_pf_status",
    "losses": "get_losses_summary",
    "carrier_kpis": "get_carrier_kpis",
    "emissions": "get_emissions",
    "transformers": "get_transformer_results",
    "unit_commitment": "get_unit_commitment",
    "line_duals": "get_line_duals",
    "voltages": "get_voltages",
    "line_reactive": "get_line_reactive",
    "transformer_reactive": "get_transformer_reactive",
    "prices": "get_prices",
    "price_drivers": "get_price_drivers",
    "curtailment": "get_curtailment",
    "lost_load": "get_lost_load",
    "loads": "get_load_results",
    "asset_economics": "get_asset_economics",
    # Edge Investment Case P2 WP2.5.
    "billing": "get_billing",
    "cfe_score": "get_cfe_score",
    # Edge Investment Case P3 WP3.4.
    "value_flows": "get_value_flows",
}


def _resolve_results_handler(result_kind: str):
    """Resolve a results enum value to the actual handler in routers.results."""
    if result_kind not in _RESULTS_ENUM:
        raise HTTPException(400, f"Unknown result_kind: {result_kind!r}")
    name = _RESULTS_HANDLER_NAMES[result_kind]
    from routers import results as results_router
    handler = getattr(results_router, name, None)
    if handler is None:
        raise HTTPException(500, f"Handler {name!r} missing from routers.results")
    return handler


# A 204 answer — "nothing of this kind exists yet" — is the single most
# common non-error outcome on the results surface, and a `Response` is not
# JSON. `json.dumps(..., default=str)` in the chat layer turns one into
# "<starlette.responses.Response object at 0x…>", which the model reads as
# DATA: it cannot tell an unsolved network from a solved one, and narrates
# whatever it can invent around a repr. This is the same defect class the
# tools audit found in the five binary-export tools; it survived here because
# the audit did not cover the results path.
#
# Every tool that can receive a 204 funnels through `_payload_or_no_data`.

def _no_data(kind: str, message: str) -> dict:
    """The model-facing shape of 'this exists, but is empty right now'."""
    return {"status": "no_data", "kind": kind, "message": message}


def _payload_or_no_data(kind: str, result: Any, message: str) -> Any:
    """
    Map a 204 `Response` to an explicit no_data dict; pass everything else
    through untouched.

    The check is on `status_code`, not `isinstance(result, Response)`: the
    handlers build theirs with `fastapi.Response`, the chat layer must not
    care which Response class that is, and no real payload here is an object
    carrying a `status_code`.
    """
    if getattr(result, "status_code", None) == 204:
        return _no_data(kind, message)
    return result


# What a 204 means on /api/results. Both halves are load-bearing: `lost_load`
# and `adequacy` answer 204 on a perfectly good solve that simply shed nothing
# / carried no target, so "not solved" alone would be a lie.
_RESULTS_NO_DATA_MESSAGE = (
    "no result of this kind: the network has not been solved, its dispatch is "
    "stale relative to the current topology, or this solve produced none of "
    "it. Call dispatch_status and get_simulation_status before reading further "
    "— do NOT report this as a zero."
)


def get_results(result_kind: str, source: str = "lopf", detail: str | None = None,
                offset: int = 0, limit: int | None = None) -> Any:
    """
    v4-MAJOR-4 dispatcher: every results enum routes through the named
    handler, with ac_pf_status mapped to a distinct path via the lookup dict.
    `source` ('lopf' | 'ac_pf') is forwarded where the handler supports it.
    `detail` ('summary' | 'lines') with `offset` / `limit` shapes the kinds
    that have a summary (value_flows, IC P3 WP3.4) and is ignored elsewhere.
    """
    handler = _resolve_results_handler(result_kind)
    # Some handlers take `source` as a query param; pass via kwargs if the
    # function accepts it, else call bare. Inspect via __code__.co_varnames.
    if "source" in handler.__code__.co_varnames:
        result = handler(source=source)
    else:
        result = handler()
    result = _payload_or_no_data(result_kind, result, _RESULTS_NO_DATA_MESSAGE)
    if result_kind == "value_flows" and isinstance(result, dict) and result.get("status") == "ok":
        if detail == "lines":
            return _value_flow_lines_page(result, offset, limit)
        return _value_flows_summary(result)
    return result


# ── value flows for the model (IC P3 WP3.4) ────────────────────────────────
# The ledger is long (a line per bill item, fee, contract and asset cost per
# period): the default answer is a SUMMARY per participant and stream under
# the result cap; `detail="lines"` pages the lines.

_VF_SUMMARY_CHARS = 3500


def _eur(v):
    return None if v is None else round(float(v), 2)


def _value_flows_summary(payload: dict) -> dict:
    ids = [x["id"] for x in payload.get("participants") or []]
    # A flag can carry a long reason (a contract naming every missing asset):
    # each is shortened, so none can outgrow the cap (WP3.4 review R2-1).
    flags = [str(f)[:160] for f in payload.get("flags") or []]
    periods = {}
    for p, per in (payload.get("periods") or {}).items():
        lines = per.get("lines") or []
        parts = {pid: {"paid": _eur(t["paid"]), "received": _eur(t["received"]),
                       "net": _eur(t["net"]),
                       "by_stream": {k: _eur(v) for k, v in sorted(t["by_stream"].items())}}
                 for pid, t in (per.get("by_participant") or {}).items()}
        checks = (per.get("conservation") or {}).get("checks") or []
        periods[p] = {"conservation_ok": (per.get("conservation") or {}).get("ok"),
                      "checks_not_true": [c["name"] for c in checks if c.get("ok") is not True],
                      "lines": len(lines),
                      "unknown_lines": sum(1 for ln in lines if ln.get("amount") is None),
                      "by_participant": parts}
    out = {"status": "ok", "template": payload.get("template"), "participants": ids,
           "conservation_ok": payload.get("conservation_ok"), "periods": periods,
           "flags_total": len(flags), "flags": flags[:15],
           "basis": "EUR per period-year, unweighted; + received, - paid; null = unknown",
           "hint": "get_results(result_kind='value_flows', detail='lines', offset, limit) "
                   "pages the ledger lines"}

    def size() -> int:
        return len(json.dumps(out, default=str))

    # Fit the cap: drop the stream split first, then the externals' rows, then
    # the flags — the participants' own totals are the last thing to go.
    if size() > _VF_SUMMARY_CHARS:
        for per in periods.values():
            for row in per["by_participant"].values():
                row.pop("by_stream", None)
        out["omitted"] = ["by_stream"]
    if size() > _VF_SUMMARY_CHARS:
        for per in periods.values():
            per["by_participant"] = {k: v for k, v in per["by_participant"].items()
                                     if any(k.strip().casefold() == i.strip().casefold()
                                            for i in ids)}
        out["omitted"].append("externals")
    if size() > _VF_SUMMARY_CHARS:
        out["flags"] = flags[:3]
        out["omitted"].append("flags")
    # Many participants (WP3.4 review #1): keep only `net`, then the largest
    # rows by |net| per period, then fewer ids — until it fits, always.
    if size() > _VF_SUMMARY_CHARS:
        for per in periods.values():
            per["by_participant"] = {k: {"net": v.get("net")}
                                     for k, v in per["by_participant"].items()}
        out["omitted"].append("paid_received")
    keep = max((len(per["by_participant"]) for per in periods.values()), default=0)
    while size() > _VF_SUMMARY_CHARS and keep > 1:
        keep = max(1, keep // 2)
        for per in periods.values():
            # Unknown nets first — the rows ADR-0001 most wants seen (R2-2).
            rows = sorted(per["by_participant"].items(),
                          key=lambda kv: (kv[1].get("net") is not None,
                                          -abs(kv[1].get("net") or 0.0)))
            if len(rows) > keep:
                per["participants_omitted"] = len(rows) - keep + per.get(
                    "participants_omitted", 0)
                per["by_participant"] = dict(rows[:keep])
    while size() > _VF_SUMMARY_CHARS and out["participants"]:
        out["participants_total"] = len(ids)
        out["participants"] = out["participants"][:len(out["participants"]) // 2]
        for per in periods.values():
            per["by_participant"] = {k[:60]: v for k, v in per["by_participant"].items()}
    # Last resorts: no flags, then only as many periods as fit.
    if size() > _VF_SUMMARY_CHARS:
        out["flags"] = []
        out["omitted"].append("flags_all")
    if size() > _VF_SUMMARY_CHARS:
        keys = list(periods)
        out["periods_total"] = len(keys)
        while size() > _VF_SUMMARY_CHARS and len(out["periods"]) > 1:
            out["periods"].pop(keys.pop())
    return out


def _value_flow_lines_page(payload: dict, offset: int, limit: int | None) -> dict:
    # Party ids, asset / contract names and flags are user text of any length:
    # cut so one row always fits a page (the summary's 160-character rule;
    # IC P3 gate note).
    def cut(v, n: int = 80):
        return None if v is None else str(v)[:n]

    rows = []
    for p, per in (payload.get("periods") or {}).items():
        for ln in per.get("lines") or []:
            row = {"period": p, "payer": cut(ln.get("payer")), "payee": cut(ln.get("payee")),
                   "stream": ln.get("value_stream"), "amount": _eur(ln.get("amount")),
                   "source": cut(f"{ln.get('source')}:{ln.get('source_id')}", 120)}
            for key in ("contract_id", "tariff_item", "asset"):
                if ln.get(key):
                    row[key] = cut(ln[key])
            if ln.get("basis") and ln["basis"] != "cash":
                row["basis"] = ln["basis"]
            if ln.get("flags"):
                row["flags"] = [cut(f, 160) for f in ln["flags"][:4]]
            rows.append(row)
    page = _paginate(rows, offset, limit)
    page["status"] = "ok"
    page["kind"] = "value_flows_lines"
    return page


def results_path_for(result_kind: str) -> str:
    """
    Return the route path the chat agent / coverage test should use to
    cross-check against route_inventory_phase0.txt. ac_pf_status is the only
    one that differs from /api/results/{kind}.
    """
    if result_kind == "ac_pf_status":
        return "/api/results/ac_pf/status"
    return f"/api/results/{result_kind}"


# ── Component CRUD (4) ──────────────────────────────────────────────────────


def create_component(component_class: str, name: str, attrs: dict) -> dict:
    """
    Generic create — validates against the class's Pydantic schema, then calls
    the dedicated route handler (so create_transformer's voltage validation +
    create_line's haversine auto-fill + create_bus etc. all run unchanged).
    GlobalConstraint routes to the dedicated create_global_constraint.
    """
    schema_name = _COMPONENT_CREATE_SCHEMAS.get(component_class)
    if schema_name is None:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    Schema = _get_schema(schema_name)
    body = Schema(name=name, **attrs)

    # Dispatch to the route handler so dedicated logic runs (voltage validation,
    # haversine auto-fill, carrier auto-create).
    from routers import network as net
    handlers = {
        "Bus": net.create_bus,
        "Carrier": net.create_carrier,
        "Line": net.create_line,
        "Link": net.create_link,
        "Transformer": net.create_transformer,
        "Generator": net.create_generator,
        "StorageUnit": net.create_storage_unit,
        "Store": net.create_store,
        "Load": net.create_load,
        "ShuntImpedance": net.create_shunt,
        "GlobalConstraint": net.create_global_constraint,
    }
    h = handlers[component_class]
    return h(body)


def update_component(
    component_class: str,
    name: str,
    attrs: dict | None = None,
    new_name: str | None = None,
) -> dict:
    """
    v6 F1/F2/F3 dispatcher with EXPLICIT routing:

      Bus + new_name, no attrs → rename_bus  (n.rename_component_names
                                    preserves dependent bus0/bus1 refs)
      Bus otherwise → update_bus  (coord-change line-length recompute; a
                                   rename via attrs["name"] or new_name goes
                                   through the PUT rename path)
      Transformer → update_transformer  (voltage validation + type sanitise)
      GlobalConstraint → update_global_constraint  (partial-PUT mitigation;
                                                   NOT in _COMPONENT_ATTRS)
      Other 7 classes (Carrier/Line/Link/Generator/StorageUnit/Store/Load/
                       ShuntImpedance) → _update_component direct

    On every class, attrs["name"] (or new_name) renames, as the PUT body does.
    """
    attrs = dict(attrs or {})

    # F1: a bare Bus rename has its own endpoint
    if component_class == "Bus" and new_name and not attrs:
        from routers.network import rename_bus
        return rename_bus(name, {"new_name": new_name})

    # A rename can also arrive as attrs["name"], exactly as in the PUT body.
    # Every path below builds the Create schema with the TARGET name and never
    # passes `name` a second time (`Schema(name=name, **attrs)` with a `name`
    # among the attrs was a TypeError on every class); the PUT handler then
    # pops it from the merged row and renames under its own guards (404, 409
    # on an occupied name, the reserved `ic:` bus prefix) and re-points
    # dependents via `_rename_component_safely`.
    if new_name:
        if "name" in attrs and attrs["name"] != new_name:
            raise HTTPException(
                400,
                f"new_name {new_name!r} and attrs.name {attrs['name']!r} "
                "name different targets",
            )
        attrs["name"] = new_name
    if "name" in attrs and not str(attrs["name"] or "").strip():
        raise HTTPException(400, "new name cannot be empty")

    # Bus: dedicated handler preserves coord-change recompute
    if component_class == "Bus":
        from routers.network import update_bus
        target = attrs.pop("name", name)
        bus = _get_schema("BusCreate")(name=target, **attrs)
        return update_bus(name, bus)

    # F2: Transformer needs voltage validation
    if component_class == "Transformer":
        from routers.network import update_transformer
        target = attrs.pop("name", name)
        tr = _get_schema("TransformerCreate")(name=target, **attrs)
        return update_transformer(name, tr)

    # F3: GlobalConstraint dedicated CRUD
    if component_class == "GlobalConstraint":
        from routers.network import update_global_constraint
        target = attrs.pop("name", name)
        gc = _get_schema("GlobalConstraintCreate")(name=target, **attrs)
        return update_global_constraint(name, gc)

    # Bare passthrough classes — direct _update_component
    if component_class not in _GENERIC_CRUD_ATTRS:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    from routers.network import _update_component
    # Partial agent updates omit required Create fields (bus / bus0…). Prefill
    # those from the live row, then keep only the keys the agent sent.
    payload = _validated_update_payload(component_class, name, attrs)
    return _update_component(
        component_class, _GENERIC_CRUD_ATTRS[component_class], name, payload,
    )


def _delete_component_handlers() -> dict[str, Any]:
    """
    The classes `delete_component` accepts, and the route handler for each.

    Extracted from the function body so `_COMPONENT_CLASS_TO_ATTR` (used by
    the #19 pre-dispatch validator) can be checked against it — a class added
    here and missed there would make that component undeletable via chat, the
    validator refusing it before the handler ever saw it.
    """
    from routers import network as net
    return {
        "Bus": net.delete_bus,
        "Carrier": net.delete_carrier,
        "Line": net.delete_line,
        "Link": net.delete_link,
        "Transformer": net.delete_transformer,
        "Generator": net.delete_generator,
        "StorageUnit": net.delete_storage_unit,
        "Store": net.delete_store,
        "Load": net.delete_load,
        "ShuntImpedance": net.delete_shunt,
        "GlobalConstraint": net.delete_global_constraint,
    }


def delete_component(component_class: str, name: str) -> None:
    """Generic delete via the dedicated route handler (so the same lock + audit run)."""
    handlers = _delete_component_handlers()
    h = handlers.get(component_class)
    if h is None:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    h(name)


def cascade_delete_bus(name: str) -> None:
    """Bus + all attached lines/links/transformers/generators/loads/storage/stores."""
    from routers.network import delete_bus_cascade
    delete_bus_cascade(name)


# ── Bulk (1) ────────────────────────────────────────────────────────────────


# One tool call must not be able to wedge the event loop or bury the undo
# stack. Unlike a read, where a short page is fine, a partial write is the
# failure mode — so an oversized batch is refused rather than trimmed.
MAX_BATCH_SIZE = 200


def _check_batch_size(items: list, what: str) -> None:
    if not isinstance(items, list) or not items:
        raise HTTPException(400, f"{what} must be a non-empty list")
    if len(items) > MAX_BATCH_SIZE:
        raise HTTPException(
            400,
            f"{len(items)} {what} exceeds the {MAX_BATCH_SIZE}-item batch "
            f"limit; split the work across several calls",
        )


def batch_create_components(component_class: str, components: list[dict]) -> dict:
    """
    Create many components of one class in a single call (#17).

    Building a 30-bus network was 30 turns — 30 model round-trips, 30 audit
    entries, and 30 chances for the turn's 25-tool-call cap to cut the job
    in half, which is a task the agent cannot finish rather than one it
    finishes slowly.

    Validate-then-apply, refusing the whole batch on any bad entry, per the
    same rule as /_bulk: a half-created network is not a state the agent can
    reason about, and undo unwinds one entry at a time.

    Each entry still goes through `create_component`, so every per-class
    handler runs unchanged — carrier auto-create, line haversine length
    fill, transformer voltage validation. A batch path that wrote rows
    directly would silently skip all of it.
    """
    _check_batch_size(components, "components")
    schema_name = _COMPONENT_CREATE_SCHEMAS.get(component_class)
    if schema_name is None:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")

    # ── Pass 1: validate everything, write nothing. ──
    Schema = _get_schema(schema_name)
    existing = set(_component_index(component_class))
    seen: set[str] = set()
    for i, entry in enumerate(components):
        if not isinstance(entry, dict):
            raise HTTPException(400, f"entry {i} is not an object")
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise HTTPException(400, f"entry {i} has no 'name'")
        if name in existing:
            raise HTTPException(
                409, f"entry {i}: {component_class} {name!r} already exists",
            )
        # Caught here rather than by the second create failing — otherwise
        # entry 1 lands and entry 2 raises, which is the partial state this
        # design exists to avoid.
        if name in seen:
            raise HTTPException(400, f"entry {i}: {name!r} appears twice in the batch")
        seen.add(name)
        attrs = {k: v for k, v in entry.items() if k != "name"}
        try:
            Schema(name=name, **attrs)
        except Exception as exc:  # noqa: BLE001 — pydantic + coercion errors
            raise HTTPException(
                400, f"entry {i} ({name!r}) is invalid: {exc}",
            ) from exc

    # ── Pass 2: apply. ──
    created: list[str] = []
    for entry in components:
        name = entry["name"]
        try:
            create_component(component_class, name,
                             {k: v for k, v in entry.items() if k != "name"})
        except Exception as exc:  # noqa: BLE001
            if not created:
                # Nothing has landed, so the batch is NOT partial and the
                # handler's own error is the accurate one. Re-raise it as-is.
                raise
            # Something HAS landed, so the batch is partial — and this is true
            # whatever raised. The previous shape re-raised an HTTPException
            # bare and kept the honest message for every other exception,
            # which covered the UNLIKELY failure only: pass 1 checks the schema
            # and name uniqueness, but the per-class handler validators (the
            # docstring's "transformer voltage validation", a missing bus) run
            # in `create_component` here, and they raise HTTPException. So the
            # common partial batch reached the agent as a plain refusal, and it
            # concluded nothing had been created.
            #
            # The original status is kept: a voltage mismatch is the caller's
            # data, not a server fault, and forcing 500 would say otherwise.
            prefix = (f"batch partially applied: created {created} before "
                      f"{name!r} failed")
            if isinstance(exc, HTTPException):
                detail = exc.detail
                if isinstance(detail, dict):
                    # Keep the structured error (and any `error_kind` the
                    # frontend routes on); add what landed beside it.
                    merged = {**detail, "partially_applied": True,
                              "created": list(created)}
                    merged["message"] = f"{prefix}: {detail.get('message', '')}".rstrip(": ")
                    raise HTTPException(exc.status_code, merged) from exc
                raise HTTPException(exc.status_code, f"{prefix}: {detail}") from exc
            raise HTTPException(500, f"{prefix}: {exc}") from exc
        created.append(name)
    return {"created": created, "count": len(created)}


def batch_delete_components(component_class: str, names: list[str]) -> dict:
    """
    Delete many components of one class in a single call (#17).

    Same validate-then-apply contract as `batch_create_components`. Each
    delete routes through `delete_component`, so the per-class handlers
    keep running — and with them the `_user_ts` profile cleanup and the
    vintage-bounds cascade that a direct row drop would orphan.
    """
    _check_batch_size(names, "names")
    handlers = _delete_component_handlers()
    if component_class not in handlers:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")

    name_strs = [str(x) for x in names]
    index = set(_component_index(component_class))
    missing = [x for x in name_strs if x not in index]
    if missing:
        sample = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        raise HTTPException(
            404, f"{len(missing)} {component_class}(s) not found: {sample}",
        )
    transient = [x for x in name_strs
                 if x in PyPSAService.get_transient_rows(component_class)]
    if transient:
        sample = ", ".join(transient[:3]) + ("…" if len(transient) > 3 else "")
        raise HTTPException(
            409,
            f"Cannot delete {len(transient)} {component_class}(s) ({sample}) — "
            f"these rows are LP scaffolding from the current solve.",
        )

    deleted: list[str] = []
    for name in name_strs:
        try:
            delete_component(component_class, name)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                500,
                f"batch partially applied: deleted {deleted} before "
                f"{name!r} failed: {exc}",
            ) from exc
        deleted.append(name)
    return {"deleted": deleted, "count": len(deleted)}


def _component_index(component_class: str) -> list[str]:
    """Current row names for one class, straight off the network."""
    attr = ("global_constraints" if component_class == "GlobalConstraint"
            else _GENERIC_CRUD_ATTRS.get(component_class))
    if attr is None:
        return []
    df = getattr(PyPSAService.get_network(), attr, None)
    return [] if df is None else [str(x) for x in df.index]


def bulk_update_components(component_class: str, names: list[str], updates: dict) -> dict:
    """PATCH /api/network/_bulk."""
    # Handler is bulk_update(body: dict) — it reads body.get("component_class"/
    # "names"/"updates"). The old BulkUpdateRequest model was removed; pass a dict.
    from routers.network import bulk_update as _h
    return _h({"component_class": component_class, "names": names, "updates": updates})


# ── Carriers (1) ────────────────────────────────────────────────────────────


def create_carrier(name: str, color: str | None = None,
                   co2_emissions: float | None = None,
                   nice_name: str | None = None) -> dict:
    attrs = {}
    if color is not None:
        attrs["color"] = color
    if co2_emissions is not None:
        attrs["co2_emissions"] = co2_emissions
    if nice_name is not None:
        attrs["nice_name"] = nice_name
    return create_component("Carrier", name, attrs)


# ── Meta (1) ────────────────────────────────────────────────────────────────


def update_meta(name: str) -> dict:
    from routers.network import update_meta as _h
    from models.schemas import NetworkMeta
    body = NetworkMeta(name=name)
    return _h(body)


# ── Topology (2) ────────────────────────────────────────────────────────────


def cluster_network(**kwargs) -> dict:
    """POST /api/network/cluster — passes the kwargs straight to the clustering handler."""
    # Model is ClusterRequest (NOT ClusteringRequest): requires `mode`, optional
    # `algorithm`/`n_clusters`/... The schema mirrors those field names.
    from routers.clustering import apply_clustering, ClusterRequest
    return apply_clustering(ClusterRequest(**kwargs))


def recalculate_line_lengths() -> dict:
    from routers.network import recalculate_line_lengths as _h
    return _h()


# ── Snapshots (4) ───────────────────────────────────────────────────────────


def set_snapshots(start: str, end: str, freq: str = "h") -> dict:
    from routers.network import set_snapshots as _h
    from models.schemas import SnapshotConfig
    body = SnapshotConfig(start=start, end=end, freq=freq)
    return _h(body)


def set_snapshot_weightings(updates: dict) -> dict:
    """PATCH /api/network/snapshot_weightings — body shape per route."""
    from routers.network import update_snapshot_weightings as _h
    return _h({"updates": updates})


def upload_snapshot_weightings_csv(csv_content_b64: str, filename: str = "weightings.csv") -> dict:
    """
    POST /api/network/snapshots/weightings.csv with the CSV body decoded.
    The route takes a multipart UploadFile; we wrap into the same shape.
    """
    import base64
    import io
    from fastapi import UploadFile
    from routers.network import upload_snapshot_weightings_csv as _h
    data = base64.b64decode(csv_content_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(_h(upload))


def sample_representative_weeks(n_weeks: int) -> dict:
    # No weighting_strategy: the handler's days-in-month weighting is the only
    # one that reconstructs the full year (Σ≈8760h) — the defining property of
    # representative-week sampling. The old schema-declared weighting_strategy
    # was dropped (never forwarded, and 'equal'/'user_provided' would break the
    # year-reconstruction invariant), so it's removed from wrapper + schema.
    from routers.network import sample_representative_weeks as _h
    from models.schemas import SampleWeeksConfig
    return _h(SampleWeeksConfig(n_weeks=n_weeks))


# ── Investment periods (3) ──────────────────────────────────────────────────


def set_multi_period_snapshots(periods: list[int], operational_from: str,
                                operational_to: str, freq: str = "h") -> dict:
    # Handler is set_multi_period_snapshots(body: dict) reading the canonical
    # shape {periods, start, end, freq}. The MultiPeriodSnapshotConfig model
    # never existed; build the dict directly, mapping operational_from/to →
    # start/end (the handler's key names).
    from routers.network import set_multi_period_snapshots as _h
    return _h({
        "periods": periods,
        "start": operational_from,
        "end": operational_to,
        "freq": freq,
    })


def set_investment_periods(periods: list[int]) -> dict:
    from routers.network import set_investment_periods as _h
    from models.schemas import InvestmentPeriods
    body = InvestmentPeriods(periods=periods)
    return _h(body)


def set_investment_period_weightings(updates: dict) -> dict:
    """PATCH /api/network/investment_period_weightings."""
    from routers.network import update_investment_period_weightings as _h
    return _h({"updates": updates})


# ── Vintage bounds (3) ──────────────────────────────────────────────────────


def set_vintage_bounds(component_class: str, name: str, period_bounds: dict) -> dict:
    # Handler is update_vintage_bounds(component_class, name, payload:
    # VintageBoundsUpdate) and reads payload.bounds — wrap period_bounds
    # ({period_str: {p_nom_min?, p_nom_max?}}) in the model (Pydantic coerces
    # the inner dicts to PeriodBound).
    from routers.vintage import update_vintage_bounds as _h, VintageBoundsUpdate
    return _h(component_class, name, VintageBoundsUpdate(bounds=period_bounds))


def delete_vintage_bounds(component_class: str, name: str) -> None:
    from routers.vintage import remove_vintage_bounds as _h
    _h(component_class, name)


def cleanup_orphan_vintages() -> dict:
    from routers.vintage import cleanup_orphan_vintages as _h
    return _h()


# ── Time-series (5) ─────────────────────────────────────────────────────────


def upload_timeseries(component: str, name: str, attribute: str, csv_content: str) -> dict:
    """
    PER-ASSET time-series upload (POST /api/network/timeseries/upload).

    The route handler is an async multipart endpoint taking a CSV `file=` whose
    index is the timestamps and whose data column(s) are named after the asset(s)
    — so `csv_content` MUST have a column header equal to `name`. We bridge the
    string into an UploadFile and drive the coroutine via `_sync`, the same idiom
    the import_* tools use; this keeps the endpoint's flat/multi-period handling.

    Prefer ``generate_exemplary_timeseries`` for full-year synthetic profiles —
    inlining ~8760 CSV rows in a tool call exceeds the turn output budget and
    freezes the chat UI while the model streams the tool arguments.
    """
    import io
    from fastapi import UploadFile
    from routers.network import upload_timeseries as _h
    upload = UploadFile(filename=f"{name}.csv", file=io.BytesIO(csv_content.encode("utf-8")))
    return _sync(_h(component=component, attribute=attribute, file=upload))


def generate_exemplary_timeseries(
    component: str,
    name: str,
    attribute: str,
    profile: str = "load_daily",
    peak: float = 1.0,
) -> dict:
    """
    Build a synthetic profile aligned to ``n.snapshots`` and upload it.

    Avoids the agent emitting tens of thousands of CSV tokens for a year of
    hourly data (which exceeds MAX_OUTPUT_TOKENS_PER_TURN and looks "stuck").

    Profiles:
      * ``load_daily`` — weekday/weekend diurnal demand shape (good for p_set).
      * ``pv_solar`` — daytime solar availability with mild seasonality
        (good for generators p_max_pu; peak should be ≤ 1).
      * ``constant`` — flat series at ``peak``.
    """
    import math

    import numpy as np
    import pandas as pd
    from services.pypsa_service import PyPSAService

    component = (component or "").strip().lower()
    attribute = (attribute or "").strip()
    name = (name or "").strip()
    profile_key = (profile or "load_daily").strip().lower()
    if component not in ("loads", "generators", "links", "storage_units", "stores"):
        raise ValueError(f"unsupported component {component!r}")
    if profile_key not in ("load_daily", "pv_solar", "constant"):
        raise ValueError(
            f"unsupported profile {profile!r}; use load_daily|pv_solar|constant"
        )

    n = PyPSAService.get_network()
    static = getattr(n, component, None)
    if static is None or name not in static.index:
        raise ValueError(f"{component!r} has no asset named {name!r}")

    sns = n.snapshots
    if len(sns) == 0:
        raise ValueError("network has no snapshots — call set_snapshots first")

    # Work in wall-clock space even for MultiIndex (period, timestep).
    if isinstance(sns, pd.MultiIndex):
        times = pd.DatetimeIndex(sns.get_level_values(-1))
    else:
        times = pd.DatetimeIndex(sns)

    hour = times.hour.to_numpy(dtype=float)
    doy = times.dayofyear.to_numpy(dtype=float)
    weekday = times.dayofweek.to_numpy(dtype=int)  # Mon=0 … Sun=6
    peak_f = float(peak)

    if profile_key == "constant":
        values = np.full(len(times), peak_f, dtype=float)
    elif profile_key == "load_daily":
        # Simple European-ish load: morning + evening peaks, weekend dip.
        diurnal = (
            0.55
            + 0.20 * np.sin((hour - 8.0) * math.pi / 12.0) ** 2
            + 0.25 * np.sin((hour - 18.0) * math.pi / 10.0) ** 2
        )
        weekend = np.where(weekday >= 5, 0.85, 1.0)
        seasonal = 0.92 + 0.08 * np.cos((doy - 20.0) * 2.0 * math.pi / 365.0)
        values = peak_f * diurnal * weekend * seasonal
        values = np.clip(values, 0.0, None)
    else:  # pv_solar
        # Daylight hump × seasonal amplitude; night ≈ 0. peak is capacity factor scale.
        elev = np.clip(np.sin((hour - 6.0) * math.pi / 12.0), 0.0, None) ** 1.4
        seasonal = 0.55 + 0.45 * np.cos((doy - 172.0) * 2.0 * math.pi / 365.0)
        values = peak_f * elev * seasonal
        values = np.clip(values, 0.0, max(peak_f, 1.0))

    df = pd.DataFrame({name: values}, index=times)
    # Preserve MultiIndex on the wire if the network uses one — upload route
    # accepts DatetimeIndex and broadcasts / stitches; for flat networks this
    # is exact. Reindex labels to the network's snapshot index for CSV dump.
    df.index = sns
    csv_content = df.to_csv(index=True, lineterminator="\n")
    result = upload_timeseries(
        component=component, name=name, attribute=attribute, csv_content=csv_content,
    )
    return {
        **(result if isinstance(result, dict) else {"result": result}),
        "profile": profile_key,
        "peak": peak_f,
        "asset": name,
        "attribute": attribute,
        "component": component,
        "snapshot_count": int(len(sns)),
        "value_min": float(np.min(values)),
        "value_max": float(np.max(values)),
        "value_mean": float(np.mean(values)),
    }


def delete_timeseries(component: str, name: str, attribute: str) -> None:
    from routers.network import delete_timeseries as _h
    _h(component=component, name=name, attribute=attribute)


def _multi_column_upload(csv_content_b64: str, filename: str, handler) -> dict:
    """
    v4-MAJOR-3: multi-column profile uploads. The route handler takes a
    multipart UploadFile whose CSV columns ARE asset names and the index is
    the timestamps. We wrap the b64-decoded bytes into UploadFile to mirror.
    """
    import base64
    import io
    from fastapi import UploadFile
    data = base64.b64decode(csv_content_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(handler(upload))


def upload_load_profile(csv_content_b64: str, filename: str = "loads.csv") -> dict:
    """
    POST /api/network/loads/upload_profile.

    v4-MAJOR-3: MULTI-COLUMN CSV. Columns are load names, index is timestamps.
    Per-load upload uses upload_timeseries with component='loads',
    attribute='p_set'.
    """
    from routers.network import upload_load_profile as _h
    return _multi_column_upload(csv_content_b64, filename, _h)


def upload_generator_profile(csv_content_b64: str,
                              filename: str = "generators.csv",
                              attribute: str = "p_max_pu") -> dict:
    """
    POST /api/network/generators/upload_profile.

    v4-MAJOR-3 MULTI-COLUMN. Columns are generator names. `attribute` defaults
    to 'p_max_pu' but the route handler accepts any time-varying generator
    attribute via query param.
    """
    from routers.network import upload_generator_profile as _h
    import base64
    import io
    from fastapi import UploadFile
    data = base64.b64decode(csv_content_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    if "attribute" in _h.__code__.co_varnames:
        return _sync(_h(attribute=attribute, file=upload))
    return _sync(_h(file=upload))


def upload_link_profile(csv_content_b64: str,
                         filename: str = "links.csv",
                         attribute: str = "p_max_pu") -> dict:
    """POST /api/network/links/upload_profile. v4-MAJOR-3 MULTI-COLUMN."""
    from routers.network import upload_link_profile as _h
    import base64
    import io
    from fastapi import UploadFile
    data = base64.b64decode(csv_content_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    if "attribute" in _h.__code__.co_varnames:
        return _sync(_h(attribute=attribute, file=upload))
    return _sync(_h(file=upload))


# ── Solver config (1) ───────────────────────────────────────────────────────


def update_solver_config(partial: dict) -> dict:
    from routers.simulation import update_solver_config as _h
    from models.schemas import SolverConfigSchema
    body = SolverConfigSchema(**partial)
    # The handler's user-code admin gate needs `db` + `user` (83d50f049; renamed from `actor` in 3e9f17d).
    # Called bare they were `Depends` sentinels (merge review N6). With no
    # acting identity bound, pass None: the gate then refuses user code
    # (fail closed) and every other knob works as before.
    if acting_user_id() is None:
        return _h(body, db=None, user=None)
    return _route(_h, body)


# ── Library (4) — Edge Investment Case P2 WP2.4c ────────────────────────────
# Router handlers called through `_route`, so the acting user's org and the
# Library ACL apply exactly as over HTTP. Results stay compact (the chat's
# per-result cap): a paged list, a tariff SUMMARY unless asked for the full
# payload or one item, a small attach receipt (review M3).

# The chat forwards at most ~1000 characters of an error: the listings are
# compact strings, as many as fit, with the totals stated first (round 2 N2).
_LIBRARY_ERROR_BUDGET = 700
# Route / binding refusals carry `{"code": …}`; the chat's forwarder reads
# `error_kind`. Library tools re-raise them under their code (review L1).
# Written as `error_kind` literals so the manifest guard sees every kind
# (round 2 N1).
_LIBRARY_ERROR_KINDS = (
    {"error_kind": "urdb_refused"}, {"error_kind": "urdb_invalid"},
    {"error_kind": "library_ref_stale"}, {"error_kind": "import_tariff_ref_conflict"},
    {"error_kind": "commercial_binding_invalid"}, {"error_kind": "solver_in_flight"},
)
_LIBRARY_CODES = {d["error_kind"]: d["error_kind"] for d in _LIBRARY_ERROR_KINDS}


def _safe_text(value, limit: int = 60) -> str:
    """Free text from an uploaded file (a rate's name) as the model may see it
    in an error: printable word characters and spaces only (round 3)."""
    import re as _re

    return _re.sub(r"[^\w .,()/-]", "_", str(value))[:limit]


def _fit(entries: list[str], budget: int = _LIBRARY_ERROR_BUDGET) -> list[str]:
    out, used = [], 0
    for e in entries:
        e = e[:budget]   # the first entry is always admitted: never oversized (P2 gate)
        if out and used + len(e) + 4 > budget:
            break
        out.append(e)
        used += len(e) + 4
    return out


def _safe_field(name) -> str:
    """A refused URDB field name as the model may see it: it comes from an
    uploaded file, so it is reduced to an identifier (review L1)."""
    import re as _re

    return _re.sub(r"[^A-Za-z0-9_./]", "_", str(name))[:64]


def _library_call(handler, *args, **kwargs):
    try:
        return _route(handler, *args, **kwargs)
    except HTTPException as exc:
        d = exc.detail
        if isinstance(d, dict) and d.get("code") in _LIBRARY_CODES:
            detail = {"error_kind": _LIBRARY_CODES[d["code"]],
                      "message": str(d.get("message", ""))[:500]}
            if isinstance(d.get("refusals"), list):
                refusals = [r for r in d["refusals"] if isinstance(r, dict)]
                shown = _fit([f"{_safe_field(r.get('field'))}: {str(r.get('reason'))[:60]}"
                              for r in refusals])
                detail = {"error_kind": detail["error_kind"],
                          "refusals_total": len(refusals), "refusals_shown": len(shown),
                          "refusals": shown,
                          "message": detail["message"][:200]}
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc
        raise


def _library_kind(kind: str):
    from routers.library import ItemKind

    try:
        return ItemKind(kind)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "unknown_library_kind",
            "message": f"kind is one of {[k.value for k in ItemKind]}, got {kind!r}"}) from exc


def list_library_items(kind: str, offset: int = 0, limit: int | None = None) -> dict:
    from routers.library import list_items as _h
    rows = [r.model_dump(mode="json") for r in _library_call(_h, _library_kind(kind))]
    return _paginate(rows, offset, limit)


def _tariff_summary(payload: dict) -> dict:
    items = []
    for it in payload.get("items") or []:
        periods = it.get("periods") or []
        rates = [float(p.get("rate", 0.0)) for p in periods]
        rates += [float(r) for p in periods for r in (p.get("tier_rates") or [])]
        rates += [float(t.get("rate", 0.0)) for t in it.get("tiers") or []]
        items.append({"id": it.get("id"), "kind": it.get("kind"), "unit": it.get("unit"),
                      "direction": it.get("direction", "cost"),
                      "periods": len(periods),
                      "windows": sorted({str(p.get("name")) for p in periods}),
                      "tiers": len(it.get("tiers") or []),
                      "ratchet": it.get("ratchet") is not None,
                      "rate_min": min(rates) if rates else None,
                      "rate_max": max(rates) if rates else None})
    return {"id": payload.get("id"), "name": payload.get("name"),
            "jurisdiction": payload.get("jurisdiction"), "valid_from": payload.get("valid_from"),
            "valid_to": payload.get("valid_to"),
            "unsupported_fields": payload.get("unsupported_fields") or [], "items": items}


def get_library_item(kind: str, name: str, version: int | None = None,
                     detail: str = "summary", item_id: str | None = None) -> dict:
    from routers.library import get_item as _h
    out = _library_call(_h, _library_kind(kind), name, version=version).model_dump(mode="json")
    payload = out["payload"]
    if item_id is not None:
        if kind != "tariff":
            raise HTTPException(status_code=422, detail={
                "error_kind": "unknown_library_kind",
                "message": "item_id selects one item of a TARIFF"})
        match = [i for i in payload.get("items") or [] if i.get("id") == item_id]
        if not match:
            raise HTTPException(status_code=404, detail=f"tariff {name!r} has no item {item_id!r}")
        return {"ref": out["ref"], "meta": out["meta"], "item": match[0]}
    if kind == "tariff" and detail != "full":
        return {"ref": out["ref"], "meta": out["meta"], "summary": _tariff_summary(payload)}
    return out


def _urdb_rate(data, item_index: int | None = None):
    """The rate object of an uploaded URDB file: the object itself, one item
    of an OpenEI response, or a REopt scenario's `urdb_response`."""
    def unreadable(message: str):
        return HTTPException(status_code=422, detail={"error_kind": "urdb_upload_unreadable",
                                                      "message": message})

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        if not items:
            raise unreadable("the OpenEI response lists no rates")
        if item_index is None and len(items) > 1:
            # A utility query returns many rates (superseded versions too):
            # never pick one silently (review M4).
            listing = _fit([f"{i}: {_safe_text(r.get('name') or r.get('label'))} "
                            f"({_safe_text(r.get('startdate'), 24)})"
                            for i, r in enumerate(items) if isinstance(r, dict)])
            raise HTTPException(status_code=422, detail={
                "error_kind": "urdb_multiple_rates", "rates_total": len(items),
                "rates_shown": len(listing), "rates": listing,
                "message": f"the upload holds {len(items)} rates; pass item_index"})
        i = item_index or 0
        if not 0 <= i < len(items):
            raise unreadable(f"item_index {i} is outside the {len(items)} rates")
        return items[i]
    if item_index not in (None, 0):
        raise unreadable("item_index applies to an OpenEI response with several rates")
    if isinstance(data, dict) and isinstance(data.get("ElectricTariff"), dict):
        et = data["ElectricTariff"]
        if isinstance(et.get("urdb_response"), dict):
            return et["urdb_response"]
        label = et.get("urdb_label")
        raise unreadable(f"the REopt scenario names URDB rate {_safe_text(label, 40)!r} "
                         "but carries no "
                         "urdb_response; upload the OpenEI rate itself" if label else
                         "the REopt scenario carries no urdb_response")
    return data


def import_urdb_tariff(file_id: str, name: str, cyclic_year: bool = False,
                       accept_partial: bool = False, valid_from: str | None = None,
                       item_index: int | None = None, tariff_id: str | None = None,
                       jurisdiction: str | None = None) -> dict:
    """An UPLOADED URDB JSON file (never an LLM-emitted blob) → a Library tariff."""
    from routers.library import UrdbImportIn, import_urdb as _h
    from services import upload_service

    with _acting():   # identity before the file is read (review L4)
        pass
    project = _require_active_project()
    blob = upload_service.get_upload_path(project, file_id)
    try:
        data = json.loads(blob.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "urdb_upload_unreadable",
            "message": f"upload {file_id!r} is not a JSON file: {type(exc).__name__}"}) from exc
    rate = _urdb_rate(data, item_index)
    if not isinstance(rate, dict):
        raise HTTPException(status_code=422, detail={
            "error_kind": "urdb_upload_unreadable",
            "message": "the upload holds no URDB rate object"})
    try:
        body = UrdbImportIn(urdb_response=rate, name=name, cyclic_year=cyclic_year,
                            accept_partial=accept_partial, valid_from=valid_from,
                            tariff_id=tariff_id, jurisdiction=jurisdiction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={
            "error_kind": "urdb_invalid", "message": _safe_text(exc, 300)}) from exc
    out = _library_call(_h, body).model_dump(mode="json")
    refusals = out.get("refusals") or []
    out["refusals_total"] = len(refusals)
    out["refusals"] = _fit([f"{_safe_field(r.get('field'))}: {_safe_text(r.get('reason'))}"
                            for r in refusals])
    fields = out.get("unsupported_fields") or []
    out["unsupported_fields"] = _fit([_safe_field(f) for f in fields])
    out["unsupported_fields_total"] = len(fields)
    return out


def attach_tariff(name: str, version: int | None = None, replace_inline: bool = False) -> dict:
    """Set `commercial.import_tariff_ref` to a Library tariff through the
    solver-config route (which resolves and pins it). An inline tariff that is
    not this item is never replaced silently (review M2)."""
    from models.schemas import SolverConfigSchema
    from routers.library import ItemKind, get_item as _get
    from routers.simulation import get_solver_config as _cfg, update_solver_config as _put

    ref = _library_call(_get, ItemKind.tariff, name, version=version).ref.model_dump(mode="json")
    commercial = dict((_cfg() or {}).get("commercial") or {})
    # The value-flow config is owned by its own route: omit it so the solver-config
    # route keeps whatever is stored when this PUT lands (IC P3 WP3.0, plan C7).
    commercial.pop("value_flows", None)
    if not commercial.get("poc_link"):
        raise HTTPException(status_code=409, detail={
            "error_kind": "no_commercial_config",
            "message": "set the commercial config's poc_link first "
                       "(update_solver_config), then attach the tariff"})
    inline = commercial.get("import_tariff")
    old_ref = commercial.get("import_tariff_ref")
    replaced = None
    if inline is not None or commercial.get("import_tariff_id"):
        same = isinstance(old_ref, dict) and old_ref.get("hash") == ref["hash"]
        if not same and isinstance(old_ref, dict) and inline is not None:
            # The inline copy IS the old Library item (unedited): a plain
            # switch between Library tariffs, nothing hand-made is lost (L5).
            from models.commercial import Tariff
            from services.commercial import hashing as _H

            try:
                same = _H.library_item_digest(Tariff.model_validate(inline)) == \
                    old_ref.get("hash")
            except ValueError:
                same = False
        replaced = {"id": (inline or {}).get("id") or commercial.get("import_tariff_id"),
                    "name": (inline or {}).get("name"), "had_ref": old_ref is not None}
        if not same and not replace_inline:
            raise HTTPException(status_code=409, detail={
                "error_kind": "inline_tariff_would_be_replaced",
                "message": (f"the project's import tariff {replaced['id']!r} would be "
                            f"replaced by Library tariff {name!r}; confirm with the user, "
                            "then call again with replace_inline=true"),
                "current": replaced})
    commercial.pop("import_tariff", None)
    commercial.pop("import_tariff_id", None)
    commercial["import_tariff_ref"] = ref
    out = _library_call(_put, SolverConfigSchema(commercial=commercial))
    bound = (out.get("commercial") or {}).get("import_tariff") or {}
    return {"import_tariff_ref": ref,
            "import_tariff": {"id": bound.get("id"), "name": bound.get("name"),
                              "items": len(bound.get("items") or [])},
            "replaced": replaced}


# ── Site connection (IC U1 follow-up, item b) ──────────────────────────────
# The commercial root: the meter Links and the site clock. Written through the
# solver-config route (which binds the whole config); refusals as literals for
# the manifest guard.
_SITE_CONNECTION_ERROR_KINDS = (
    {"error_kind": "site_connection_link_missing"},
    {"error_kind": "site_connection_wrong_direction"},
    {"error_kind": "site_connection_invalid"},
)


class _Unset:
    """An omitted argument (distinct from an explicit null, which clears)."""

    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()


def set_site_connection(poc_link: str, export_link: str | None | _Unset = _UNSET,
                        timezone: str | None | _Unset = _UNSET) -> dict:
    """Set the commercial root (`poc_link`, `export_link`, `timezone`) through
    the solver-config route, keeping every other key of a stored commercial
    config (the route keeps the value flows itself). An OMITTED `export_link`
    or `timezone` keeps the stored value; an explicit null clears it. The
    Links are checked first (`binding.check_site_connection`): they exist, are
    one-way and point grid → site (PoC) and site → grid (export)."""
    from pydantic import ValidationError

    from models.schemas import SolverConfigSchema
    from routers.simulation import get_solver_config as _cfg, update_solver_config as _put
    from services.commercial import binding

    with _acting():   # identity before the network is read
        pass
    stored = (_cfg() or {}).get("commercial")
    created = not (isinstance(stored, dict) and stored.get("poc_link"))
    commercial = dict(stored) if isinstance(stored, dict) else {}
    commercial.pop("value_flows", None)   # owned by its own route (plan C7)
    commercial["poc_link"] = poc_link
    if not isinstance(export_link, _Unset):
        commercial["export_link"] = export_link
    if not isinstance(timezone, _Unset):
        commercial["timezone"] = timezone
    try:
        binding.check_site_connection(PyPSAService.get_network(), poc_link,
                                      commercial.get("export_link"),
                                      group_members=commercial.get("group_members"))
        body = SolverConfigSchema(commercial=commercial)
    except binding.BindingRefusal as exc:
        raise HTTPException(status_code=exc.status, detail={
            "error_kind": exc.code, "message": exc.message[:500]}) from exc
    except (ValidationError, ValueError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else []
        message = str(errors[0].get("msg")) if errors else str(exc)
        raise HTTPException(status_code=422, detail={
            "error_kind": "site_connection_invalid", "message": message[:300]}) from exc
    try:
        out = _library_call(_put, body)
    except HTTPException as exc:
        d = exc.detail if isinstance(exc.detail, dict) else {}
        if "error_kind" in d or "code" not in d:
            raise
        # A refusal of the kept config (a tariff, a contract, a connection
        # agreement) with no chat kind of its own (e.g. `fca_needs_saved_project`;
        # `_library_call` already gave `commercial_binding_invalid`,
        # `library_ref_stale` and `solver_in_flight` theirs): say which code.
        raise HTTPException(status_code=exc.status_code, detail={
            "error_kind": "site_connection_invalid", "code": str(d["code"])[:80],
            "message": str(d.get("message", ""))[:500]}) from exc
    bound = out.get("commercial") or {}
    root = {k: bound.get(k) for k in binding.SITE_CONNECTION_KEYS}
    notes = []
    if root["export_link"] is None:
        notes.append("no export_link: export is not priced or billed")
    if root["timezone"] is None:
        notes.append("no timezone: the snapshots are read as the site clock")
    return {"commercial": root, "created": created, "notes": notes}


# ── Participants (IC P3 WP3.4) ─────────────────────────────────────────────
# `define_participants` drives the template and value-flows routes. Their
# refusals carry `{"code": …}`; the forwarder reads `error_kind`, so each is
# re-raised under a fixed kind (written as literals for the manifest guard).
_VALUE_FLOW_ERROR_KINDS = (
    {"error_kind": "value_flows_invalid"}, {"error_kind": "value_flows_changed"},
    {"error_kind": "no_commercial_config"}, {"error_kind": "commercial_config_invalid"},
    {"error_kind": "solver_in_flight"},
)
_VALUE_FLOW_CODES = {d["error_kind"]: d["error_kind"] for d in _VALUE_FLOW_ERROR_KINDS}


def _value_flow_call(handler, *args, **kwargs):
    try:
        return handler(*args, **kwargs)
    except HTTPException as exc:
        d = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        code = str(d.get("code") or "")
        message = str(d.get("message", ""))[:500]
        if code in _VALUE_FLOW_CODES:
            detail = {"error_kind": _VALUE_FLOW_CODES[code], "message": message}
            if isinstance(d.get("problems"), list):
                shown = _fit([str(x)[:200] for x in d["problems"]])
                detail.update(problems_total=len(d["problems"]), problems=shown)
        elif code.startswith("template_"):
            detail = {"error_kind": "template_refused", "code": code, "message": message}
        else:
            raise
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


def define_participants(template: str | None = None, config: dict | None = None,
                        clear: bool = False, replace: bool = False) -> dict:
    """Set the project's participants and value-flow assignment. Exactly one
    of `template` (build it through the template route), `config` (a full
    value-flow config) or `clear`. A template that needs contracts the project
    does not have is NOT saved: its unpriced drafts come back for the user to
    price and save first (never saved silently). A stored config that differs
    is not replaced (or cleared) until the user confirms and `replace=true`
    (the `attach_tariff` guard). Every write sends the digest it read as
    If-Match."""
    from routers.simulation import (
        TemplateIn, ValueFlowsIn, build_value_flow_template, get_value_flows as _get,
        put_value_flows as _put,
    )

    if sum((template is not None, config is not None, bool(clear))) != 1:
        raise HTTPException(status_code=422, detail={
            "error_kind": "value_flows_invalid",
            "message": "pass exactly one of template, config or clear=true"})
    notes: list[str] = []
    if template is not None:
        built = _value_flow_call(build_value_flow_template, TemplateIn(template=template))
        notes = list(built.get("notes") or [])
        if built.get("draft_contracts"):
            # Compact (WP3.4 review #2): the model re-calls with the template,
            # so the config itself is not needed — its parties and sizes are.
            cfg = built["config"]
            return {"saved": False, "status": "drafts_need_pricing", "template": template,
                    "draft_contracts": [_compact_draft(d) for d in built["draft_contracts"]],
                    "participants": _fit([f"{x.get('id')} ({x.get('role')})"
                                          for x in cfg.get("participants") or []]),
                    "asset_owners_total": len(cfg.get("asset_owners") or []),
                    "notes": _fit(notes),
                    "message": ("the template needs these contracts, which the project does "
                                "not have: ask the user for the null fields, save them with "
                                "update_solver_config, then call define_participants again")}
        value = built["config"]
    else:
        value = None if clear else config
    state = _value_flow_call(_get)
    current = state.get("value_flows")
    if current is not None and _vf_normal(current) != _vf_normal(value) and not replace:
        raise HTTPException(status_code=409, detail={
            "error_kind": "value_flows_would_be_replaced",
            "message": ((f"the project already has a value-flow config (template "
                         f"{str(current.get('template'))!r}); "
                         if state.get("status") == "ok" and isinstance(current, dict) else
                         "the project holds a stored value-flow config that does not "
                         "validate; ")
                        + "confirm with the user, then call again with replace=true"),
            "current_participants": [str(x.get("id"))[:60] for x in
                                     (current.get("participants") or [])
                                     if isinstance(x, dict)][:10]
            if isinstance(current, dict) else []})
    out = _value_flow_call(_put, ValueFlowsIn(value_flows=value), if_match=state["digest"])
    stored = out.get("value_flows") or {}
    parts = stored.get("participants") or []
    return {"saved": True, "status": out.get("status"), "digest": out.get("digest"),
            "template": stored.get("template"), "participants_total": len(parts),
            "participants": _fit([f"{x.get('id')} ({x.get('role')})" for x in parts]),
            "notes": _fit(notes)}


def _vf_normal(value):
    """A value-flow config as the server stores it (so the same config sent
    twice is not a replacement, WP3.4 review #5); the raw value when it does
    not validate."""
    from services.commercial import participants as P

    try:
        parsed = P.parse_value_flows(value)
    except P.ValueFlowsInvalid:
        return value
    return None if parsed is None else parsed.model_dump(mode="json")


def _compact_draft(d: dict) -> dict:
    """A draft contract with its id lists fitted (a site with many assets)."""
    out = dict(d)
    for key in ("asset_ids", "load_ids"):
        ids = out.get(key)
        if isinstance(ids, list) and len(ids) > 10:
            out[key] = ids[:10]
            out[f"{key}_total"] = len(ids)
    return out


# ── Investment case (IC P4 WP4.6c) ─────────────────────────────────────────
# The single-owner finance run for the model: start it (campaign-gated like
# the studies; it solves no LP, so it is charged 0 solves), read its status and
# report (a summary under the result cap, or the cashflow lines paged), solve
# an owner-sold PPA price for a target post-tax equity IRR WITHOUT touching the
# stored inputs, and explain the equity IRR and the min-DSCR year by stream and
# year. The routes' refusals carry `{"code": …}`; each is re-raised under a
# fixed kind (written as literals for the manifest guard). An unknown number is
# never written as 0: it reads "not established" (plan C12, ADR-0001).

_IC_ERROR_KINDS = (
    {"error_kind": "investment_case_busy"}, {"error_kind": "investment_case_not_solved"},
    {"error_kind": "finance_inputs_missing"}, {"error_kind": "finance_inputs_invalid"},
    {"error_kind": "tax_pack_not_found"}, {"error_kind": "investment_case_request_invalid"},
)
# Route code → kind (the `start_investment_case` 422 set and the POST's 409).
_IC_CODES = {
    "not_solved": "investment_case_not_solved",
    "finance_inputs_missing": "finance_inputs_missing",
    "finance_inputs_invalid": "finance_inputs_invalid",
    "tax_pack_not_found": "tax_pack_not_found",
    "request_invalid": "investment_case_request_invalid",
}
assert set(_IC_CODES.values()) <= {d["error_kind"] for d in _IC_ERROR_KINDS}

# The C9 solve-for-PPA refusals (`engine.solve_ppa`'s `solve_ppa_status`), and
# an adapter refusal (`FinanceRefused`) while building the case to solve on.
_SOLVE_PPA_ERROR_KINDS = (
    {"error_kind": "solve_ppa_contract_not_found"}, {"error_kind": "solve_ppa_ambiguous_contract"},
    {"error_kind": "solve_ppa_not_owner_sold"}, {"error_kind": "solve_ppa_not_linear"},
    {"error_kind": "solve_ppa_needs_redispatch"}, {"error_kind": "solve_ppa_price_unknown"},
    {"error_kind": "solve_ppa_cash_not_established"}, {"error_kind": "solve_ppa_no_root"},
    {"error_kind": "solve_ppa_irr_ambiguous"},
    {"error_kind": "investment_case_refused"},
)
_SOLVE_PPA_MESSAGES = {
    "solve_ppa_contract_not_found": "no owner-sold PPA settlement line matches: name the "
                                    "contract with contract_id, or add a PPA the owner sells",
    "solve_ppa_ambiguous_contract": "several PPA contracts: name one with contract_id",
    "solve_ppa_not_owner_sold": "the contract is not sold by the owner (its settlement is not "
                                "cash in): only an owner-sold PPA can be solved",
    "solve_ppa_not_linear": "the contract's settlement is not linear in its price (not a PPA "
                            "settlement at its own indexation), so a price cannot be solved",
    "solve_ppa_needs_redispatch": "the contract changes the dispatch: a finance-only solve cannot "
                                  "re-dispatch, so its price cannot be solved here",
    "solve_ppa_price_unknown": "the contract's price is not established in the case",
    "solve_ppa_cash_not_established": "the post-tax equity cash is not established at a zero "
                                      "price (see get_investment_case for the reasons)",
    "solve_ppa_irr_ambiguous": "a price makes the equity NPV zero at the target rate, but the "
                               "equity cash to the target year has several IRRs and the case's "
                               "own IRR at that price is another one (see `code`): the price "
                               "does not give the target IRR as reported, so it is not "
                               "established",
    "solve_ppa_no_root": "no price in the search range reaches the target IRR in the target "
                         "year (see `code` for why)",
}

_NE = "not established"
_IC_SUMMARY_CHARS = 3500
_IC_PAGE_CHARS = 3000
_IC_P4_SECTIONS = ("project", "debt", "tax", "participants", "gates")


def _cut(v, n: int = 80):
    """User text (party ids, contract / asset names, flags) cut so one row or
    entry can never outgrow the cap (the P3 rule: ids 80, flags 160)."""
    return None if v is None else str(v)[:n]


def _ne(v, nd: int | None = None):
    """A number as the model reads it: None / NaN / inf → "not established",
    never 0 (plan C12)."""
    if v is None or isinstance(v, bool):
        return _NE if v is None else v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _NE
    if not math.isfinite(f):
        return _NE
    return round(f, nd) if nd is not None else f


def _json_len(obj) -> int:
    return len(json.dumps(obj, default=str))


def _ic_raise(status: int, kind: str, message: str, **extra) -> NoReturn:
    raise HTTPException(status_code=status,
                        detail={"error_kind": kind, "message": str(message)[:500], **extra})


@contextlib.contextmanager
def _ic_errors():
    """Re-raise the investment-case routes' refusals under a fixed kind. A 409
    without a code is the study mesh (a solve or another study running)."""
    try:
        yield
    except HTTPException as exc:
        d = exc.detail
        if isinstance(d, dict) and "error_kind" in d:
            raise
        code = str(d.get("code") or "") if isinstance(d, dict) else ""
        message = str(d.get("message", "") if isinstance(d, dict) else d)[:500]
        if code in _IC_CODES:
            kind = _IC_CODES[code]
        elif exc.status_code == 409:
            kind = "investment_case_busy"
        else:
            raise
        detail = {"error_kind": kind, "message": message}
        if code:
            detail["code"] = code
        if isinstance(d, dict) and isinstance(d.get("errors"), list):
            detail["errors_total"] = len(d["errors"])
            detail["errors"] = _fit([str(e)[:200] for e in d["errors"]])
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


def run_investment_case(owner: str | None = None) -> dict:
    """Start the single-owner investment-case run (IC P4): build the finance
    case from the solved network and the stored finance inputs, run the
    finance engine and store the report. Campaign-gated like the studies (0
    solves: it solves no LP). Poll `get_investment_case`."""
    from pydantic import ValidationError

    from routers.results import post_investment_case as _h
    from services.finance.investment_case_runner import InvestmentCaseRequest

    try:
        body = InvestmentCaseRequest(owner=owner)
    except ValidationError as exc:
        _ic_raise(422, "investment_case_request_invalid",
                  str(exc.errors()[0].get("msg")) if exc.errors() else str(exc))

    def start():
        with _ic_errors():
            return _h(body)

    out = _campaign_gated("investment_case", start)
    out = {k: (_cut(v) if k in ("owner", "case_id") else v) for k, v in out.items()}
    out["hint"] = ("poll get_investment_case() until status is done / refused / failed; "
                   "then get_investment_case(detail='cashflows') pages the cash lines and "
                   "explain_cashflow() explains the equity IRR and the min-DSCR year")
    return out


def _ic_read() -> tuple[dict | None, dict | None]:
    """(status body, full report) — each None when the route answers 204."""
    from routers.results import get_investment_case as _status, \
        get_investment_case_report as _report

    status = _status()
    report = _report(detail="full")
    status = None if getattr(status, "status_code", None) == 204 else status
    report = None if getattr(report, "status_code", None) == 204 else report
    return status, report


_IC_NO_DATA = ("no investment-case run and no stored report: the finance inputs are set "
               "with PUT /api/simulation/finance (the Investment tab), then call "
               "run_investment_case — do NOT report this as a zero")


def _section(report: dict, name: str) -> dict:
    return ((report.get("sections") or {}).get(name) or {})


def _ic_headlines(report: dict) -> dict:
    p = _section(report, "project").get("payload") or {}
    d = _section(report, "debt").get("payload") or {}

    def pick(key, fallback=None, nd=6):
        v = p.get(key)
        if v is None and fallback is not None:
            v = report.get(fallback)
        return _ne(v, nd)

    return {
        "equity_post_tax_irr": pick("equity_post_tax_irr"),
        "equity_pre_tax_irr": pick("equity_pre_tax_irr"),
        "equity_post_tax_npv_at_cost_of_equity": pick("equity_post_tax_npv", nd=2),
        "project_post_tax_irr": pick("project_post_tax_irr", "project_irr_post_tax"),
        "project_pre_tax_irr": pick("project_pre_tax_irr", "project_irr_pre_tax"),
        "project_post_tax_npv_at_wacc": pick("project_post_tax_npv", "npv_at_wacc", nd=2),
        "lifecycle_npv": pick("lifecycle_npv", nd=2),
        "payback_years": pick("payback_years", nd=2),
        "lcoe_nominal_per_mwh": pick("lcoe_nominal_per_mwh",
                                     "lcoe_finance_consistent_eur_per_mwh", nd=4),
        "lcoe_real_per_mwh": pick("lcoe_real_per_mwh", nd=4),
        "min_dscr": _ne(d.get("min_dscr", report.get("min_dscr")), 4),
        "avg_dscr": _ne(d.get("avg_dscr", report.get("avg_dscr")), 4),
        "llcr": _ne(d.get("llcr", report.get("llcr")), 4),
        "plcr": _ne(d.get("plcr", report.get("plcr")), 4),
        "debt_amount": _ne(d.get("amount"), 2),
    }


def _ic_summary(status: dict | None, report: dict | None) -> dict:
    st = status or {}
    rep_state = st.get("report") or {}
    out: dict[str, Any] = {
        "status": "ok",
        "run": {"status": st.get("status", "idle"), "stage": st.get("stage"),
                "stages_done": list(st.get("stages_done") or []),
                "error_code": _cut(st.get("error_code"), 120),
                "error": _cut(st.get("error"), 300)},
    }
    if report is None:
        out["report"] = {"present": False}
        out["flags"] = _fit([str(f)[:160] for f in st.get("flags") or []])
        out["hint"] = "no stored report yet: poll get_investment_case() while the run is running"
        return out
    stale = rep_state.get("stale")
    out["report"] = {"present": True, "stale": _NE if stale is None else stale,
                     "changed": list(rep_state.get("changed") or [])[:8],
                     "reason": _cut(rep_state.get("reason"), 120)}
    p = _section(report, "project").get("payload") or {}
    out.update(case_id=_cut(report.get("case_id")), owner=_cut(p.get("owner")),
               currency=p.get("currency"), cod_year=p.get("cod_year"),
               analysis_years=p.get("analysis_years"))
    refusal = p.get("refusal")
    if isinstance(refusal, dict):
        out["refusal"] = {"code": _cut(refusal.get("code"), 120),
                          "detail": _cut(refusal.get("detail"), 300)}
    out["headlines"] = _ic_headlines(report)
    if p.get("solve_ppa_status") is not None:
        out["solve_ppa"] = {"status": _cut(p.get("solve_ppa_status"), 120),
                            "price_per_mwh": _ne(p.get("solved_ppa_price"), 4),
                            "money_year": p.get("solved_ppa_price_money_year")}
    gate = _section(report, "gates").get("payload") or {}
    consistent = gate.get("wacc_vs_discount_rate_consistent",
                          (report.get("gates") or {}).get("wacc_vs_discount_rate_consistent"))
    out["wacc_gate"] = {
        "wacc_vs_discount_rate_consistent": _NE if consistent is None else consistent,
        "legs": {k: (_NE if v is None else v) for k, v in (gate.get("legs") or {}).items()},
        "assets_with_other_rates": [_cut(a) for a in (gate.get("assets_with_other_rates")
                                                      or [])[:5]],
        "values": {k: _ne(v, 6) for k, v in (gate.get("values") or {}).items()
                   if not isinstance(v, dict)},
    }
    completeness = report.get("completeness") or {}
    out["completeness"] = {k: completeness.get(k) for k in _IC_P4_SECTIONS if k in completeness}
    out["skipped"] = sorted(k for k, v in completeness.items()
                            if v == "skipped" and k not in _IC_P4_SECTIONS)
    out["reasons"] = {k: str(_section(report, k).get("note") or "")[:300]
                      for k in _IC_P4_SECTIONS if completeness.get(k) == "not_established"}
    flags = [str(f)[:160] for f in p.get("flags") or []]
    out["flags_total"] = len(flags)
    out["flags"] = _fit(flags)
    out["cashflow_lines"] = len(report.get("cashflow_lines") or [])
    out["basis"] = ("IRRs as fractions; money in the case currency (NPVs at financial "
                    "close); returns and DSCR on the incremental cash against the "
                    "counterfactual supply cost; the lifecycle NPV on the owner's total cash")
    out["hint"] = ("get_investment_case(detail='cashflows', page) pages the cash lines; "
                   "explain_cashflow() attributes the equity IRR and the min-DSCR year")

    # Fit the cap: the gate's values first, then shorter reasons, fewer flags.
    if _json_len(out) > _IC_SUMMARY_CHARS:
        out["wacc_gate"].pop("values", None)
        out["omitted"] = ["wacc_gate.values"]
    if _json_len(out) > _IC_SUMMARY_CHARS:
        out["reasons"] = {k: v[:120] for k, v in out["reasons"].items()}
        out["omitted"].append("reasons_long")
    if _json_len(out) > _IC_SUMMARY_CHARS:
        out["flags"] = flags[:3]
        out["omitted"].append("flags")
    if _json_len(out) > _IC_SUMMARY_CHARS:
        out.pop("basis", None)
        out["run"]["error"] = _cut(out["run"]["error"], 100)
        out["reasons"] = {k: v[:60] for k, v in out["reasons"].items()}
        out["flags"] = []
        out["omitted"].append("flags_all")
    return out


def _ic_cashflow_rows(report: dict) -> list[dict]:
    rows = []
    for ln in sorted(report.get("cashflow_lines") or [], key=lambda x: x.get("year") or 0):
        prov = ln.get("provenance") or {}
        src = prov.get("source")
        sid = prov.get("source_id")
        row = {"year": ln.get("year"), "stream": _cut(ln.get("value_stream"), 40),
               "counterparty": _cut(ln.get("counterparty")), "amount": _ne(ln.get("amount"), 2),
               "source": _cut(f"{src}:{sid}" if sid else src, 120)}
        if prov.get("contract_id"):
            row["contract"] = _cut(prov["contract_id"])
        for key in ("asset", "tariff_item"):
            if ln.get(key):
                row[key] = _cut(ln[key])
        if prov.get("period"):
            row["period"] = _cut(prov["period"], 40)
        rows.append(row)
    return rows


def _pack_pages(rows: list[dict], budget: int) -> list[list[dict]]:
    """Deterministic pages packed by serialised size (the `_paginate` rule: a
    page always takes its first row), so page N is the same page every call."""
    pages: list[list[dict]] = []
    cur: list[dict] = []
    used = 0
    for row in rows:
        cost = _json_len(row) + 2
        if cur and used + cost > budget:
            pages.append(cur)
            cur, used = [], 0
        cur.append(row)
        used += cost
    if cur:
        pages.append(cur)
    return pages


def _ic_equity_cash_state(report: dict) -> tuple[bool, list[str]]:
    """(the post-tax equity cash is established, what is not): a None line is
    left out of the lines, so the pages must say so — never sum to a total
    that reads as established (C12; WP4.6c review F1)."""
    proj = _section(report, "project")
    p = proj.get("payload") or {}
    eq = (p.get("cash") or {}).get("equity_post_tax")
    ok = isinstance(eq, list) and bool(eq) and all(_ne(v) != _NE for v in eq)
    missing: list[str] = []
    if proj.get("status") not in (None, "ok") and proj.get("note"):
        missing.append(str(proj["note"]))
    missing += [f"operating:{k}" for k, v in (p.get("operating_status") or {}).items()
                if v != "ok"]
    missing += [f"counterfactual:{k}" for k in
                ((p.get("counterfactual") or {}).get("lines_not_established") or [])]
    if not ok and not missing:
        tax = _section(report, "tax")
        missing.append(f"tax:{tax.get('note') or tax.get('status') or 'not established'}")
    return ok, [_cut(m, 160) for m in missing[:10]]


def _ic_cashflow_page(status: dict | None, report: dict, page: int) -> dict:
    rows = _ic_cashflow_rows(report)
    cash_ok, not_established = _ic_equity_cash_state(report)
    pages = _pack_pages(rows, _IC_PAGE_CHARS)
    items = pages[page - 1] if page <= len(pages) else []
    before = sum(len(pg) for pg in pages[:page - 1])
    p = _section(report, "project").get("payload") or {}
    stale = ((status or {}).get("report") or {}).get("stale")
    out = {"status": "ok", "kind": "investment_case_cashflows", "page": page,
           "pages": len(pages), "total_count": len(rows), "returned": len(items),
           "has_more": before + len(items) < len(rows), "items": items,
           "currency": p.get("currency"), "stale": _NE if stale is None else stale,
           "equity_post_tax_cash": "established" if cash_ok else _NE,
           "basis": ("one line per (year, stream line), + = cash in to the owner: the "
                     "owner's total operating lines plus the counterfactual supply cost's "
                     "lines NEGATED (source counterfactual:*) — do not subtract the "
                     "counterfactual again. " + (
                         "Each year's lines sum to the post-tax equity cash." if cash_ok else
                         "The post-tax equity cash is NOT established: a line that is not "
                         "established is left out, so these lines must not be summed into a "
                         "total (see lines_not_established)."))}
    if not cash_ok:
        out["lines_not_established"] = not_established
    if page > len(pages):
        out["note"] = f"past the last page ({len(pages)})"
    return out


def get_investment_case(detail: str = "summary", page: int = 1) -> dict:
    """The investment-case run and its stored report (IC P4). `summary`: the
    run status, staleness, headlines, the WACC gate, completeness, the top
    flags and reasons, under the result cap. `cashflows`: the cashflow lines,
    paged (1-based)."""
    if detail not in ("summary", "cashflows"):
        _ic_raise(422, "investment_case_request_invalid",
                  f"detail must be 'summary' or 'cashflows', got {_cut(detail, 40)!r}")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        _ic_raise(422, "investment_case_request_invalid", "page must be an integer >= 1")
    status, report = _ic_read()
    if status is None and report is None:
        return _no_data("investment_case", _IC_NO_DATA)
    if detail == "cashflows":
        if report is None:
            return _no_data("investment_case_cashflows",
                            "no stored report yet: the run has not finished")
        return _ic_cashflow_page(status, report, page)
    return _ic_summary(status, report)


def _ic_solve_layers(case):
    """Tests only (the runner's `layers` hook): the tax layers `solve_ppa`
    uses instead of the pack's. None in production — the pack decides."""
    return None


def _ic_default_owner(sim_state) -> str | None:
    """The owner the last run used (its record, else the stored report's
    provenance): `solve_ppa_price` solves the SAME case (WP4.6c review F2)."""
    rec = sim_state.get("investment_case")
    if isinstance(rec, dict) and rec.get("owner"):
        return str(rec["owner"])
    rep = sim_state.get("investment_case_report")
    prov = ((_section(rep, "project").get("payload") or {}).get("provenance") or {}) \
        if isinstance(rep, dict) else {}
    return str(prov["owner"]) if prov.get("owner") else None


def solve_ppa_price(target_irr: float, target_year: int,
                    contract_id: str | None = None, owner: str | None = None) -> dict:
    """The price of an owner-sold PPA for a target post-tax equity IRR in a
    target operating year (IC P4 C9), from the stored finance inputs and the
    current solved network. Builds the case through the router's adapter seam
    and solves on a COPY whose `inputs.solve_ppa` is set: the stored inputs and
    the stored report are never touched."""
    import dataclasses

    from pydantic import ValidationError

    from models.finance import SolvePpa
    from routers.results import _dispatch_ready, _ic_build_case, _study_mesh_blocker
    from routers.simulation import _state as sim_state
    from services.finance.case import FinanceRefused
    from services.finance.engine import solve_ppa
    from services.finance.investment_case_runner import finance_inputs_or_422
    from services.finance.packs.base import PackNotFound, load_pack

    try:
        sp = SolvePpa(contract_id=contract_id, target_irr=target_irr, target_year=target_year)
    except ValidationError as exc:
        _ic_raise(422, "investment_case_request_invalid",
                  "; ".join(f"{'.'.join(map(str, e.get('loc', ())))}: {e.get('msg')}"
                            for e in exc.errors()[:3]))
    blocked = _study_mesh_blocker("solve_ppa_price")
    if blocked:
        _ic_raise(409, "investment_case_busy", blocked)
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        _ic_raise(409, "investment_case_not_solved",
                  "solve the network first: the PPA price is solved on the solved dispatch")
    cfg = sim_state["solver_config"]
    raw = getattr(cfg, "finance", None)
    if raw is None:
        _ic_raise(422, "finance_inputs_missing",
                  "set the finance inputs first (PUT /api/simulation/finance)")
    with _ic_errors():
        finance_inputs_or_422(raw)
    if owner is not None and (not isinstance(owner, str) or not owner.strip()):
        _ic_raise(422, "investment_case_request_invalid", "owner must be a non-empty string")
    owner = owner if owner is not None else _ic_default_owner(sim_state)
    build = _ic_build_case(n, cfg, owner=owner, lost_load=sim_state.get("last_lost_load"))
    try:
        case = build()
    except FinanceRefused as exc:
        _ic_raise(422, "investment_case_refused", exc.detail or exc.code,
                  code=_cut(exc.code, 120))
    pack = None
    if case.inputs.tax_pack_id:
        try:
            pack = load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
        except PackNotFound as exc:
            _ic_raise(422, "tax_pack_not_found", str(exc))
    trial = dataclasses.replace(case, inputs=case.inputs.model_copy(update={"solve_ppa": sp}))
    try:
        out = solve_ppa(trial, pack, layers=_ic_solve_layers(trial))
    except FinanceRefused as exc:
        _ic_raise(422, "investment_case_refused", exc.detail or exc.code,
                  code=_cut(exc.code, 120))
    code = str(out.get("solve_ppa_status") or "")
    if code != "ok":
        kind = code.split(":", 1)[0]
        if kind not in _SOLVE_PPA_MESSAGES:      # an unknown status is not "no root"
            _ic_raise(422, "investment_case_refused",
                      "the PPA price solve did not complete (see `code`)", code=_cut(code, 120),
                      contract_id=_cut(contract_id))
        _ic_raise(422, kind, _SOLVE_PPA_MESSAGES[kind], code=_cut(code, 120),
                  contract_id=_cut(contract_id))
    # The contract the engine priced and its price in the stored inputs (the
    # earliest template's, in its money year — the engine's reference).
    first = min(case.templates, key=lambda t: t.first_year)
    ref = next((ln for ln in first.lines if ln.price is not None and (
        ln.contract_id == contract_id if contract_id is not None
        else ln.stream == "ppa_settlement")), None)
    result = {
        "status": "ok",
        "solved_ppa_price_per_mwh": _ne(out.get("solved_ppa_price"), 4),
        "currency": case.inputs.currency,
        "money_year": out.get("solved_ppa_price_money_year"),
        "contract_id": _cut(ref.contract_id if ref is not None else contract_id),
        "price_in_stored_inputs_per_mwh": _ne(None if ref is None else ref.price, 4),
        "target_irr": target_irr, "target_year": target_year,
        "equity_post_tax_irr_at_target_year": _ne(out.get("solved_equity_irr_at_target_year"), 6),
        "owner": _cut(case.owner),
        "flags": [_cut(f, 160) for f in out.get("solve_ppa_flags") or []],
        "basis": ("post-tax equity IRR on the incremental cash against the counterfactual "
                  "supply cost, over operating years 1..target_year (SAM ppa_soln_mode=0)"),
        "stored_inputs_changed": False,
        "note": ("nothing was saved: the stored finance inputs and report are unchanged. To "
                 "use this price, change the contract price and re-run the solve and "
                 "run_investment_case"),
    }
    return result


# The engine's own sources in the report's cashflow lines (report.py
# `_cashflow_lines`); every other source is an operating line.
_IC_FINANCE_SOURCES = frozenset({"capex", "replacement_capex", "terminal_value", "itc", "ptc",
                                 "grant", "debt", "debt_draws", "dsra", "reserve_interest"})
_IC_AVOIDED = "avoided_supply_cost (counterfactual)"


def _ic_is_finance_source(src) -> bool:
    return src in _IC_FINANCE_SOURCES or str(src or "").startswith("tax:")


def _ic_is_counterfactual(ln: dict) -> bool:
    return str((ln.get("provenance") or {}).get("source") or "").startswith("counterfactual:")


def _ic_cf_lines_present(report: dict) -> bool:
    """The report's lines carry the counterfactual (negated) themselves
    (WP4.6b review B2) — then `counterfactual_net` must not be added again."""
    return any(_ic_is_counterfactual(ln) for ln in report.get("cashflow_lines") or [])


def _ic_label(ln: dict) -> str:
    if _ic_is_counterfactual(ln):
        return _IC_AVOIDED
    stream = str(ln.get("value_stream"))
    src = (ln.get("provenance") or {}).get("source")
    if not _ic_is_finance_source(src) or src in ("debt", stream):
        return _cut(stream, 80)
    src = str(src)
    return _cut(f"{stream}:{src[4:] if src.startswith('tax:') else src}", 80)


def _money(v):
    """Money rounded to the cent, -0.0 written as 0.0; None / NaN / inf → "not
    established" (WP4.6c review F4)."""
    f = _ne(v)
    return _NE if f == _NE else round(float(f), 2) + 0.0


def _ic_rate(report: dict, sim_state) -> tuple[float, str]:
    """The discount rate of the attribution: the cost of equity of the finance
    inputs the report was built from (their digest matches the report's
    provenance), else the equity post-tax IRR, else 0 (undiscounted)."""
    from services.finance.investment_case_runner import finance_digest

    p = _section(report, "project").get("payload") or {}
    coe = _ne(p.get("cost_of_equity"))              # recorded in the report itself
    if coe != _NE and coe > -1.0:
        return coe, "cost_of_equity"
    stored = ((p.get("provenance") or {}).get("inputs") or {}).get("finance")
    raw = getattr(sim_state.get("solver_config"), "finance", None)
    if stored and isinstance(raw, dict) and finance_digest(raw) == stored:
        coe = _ne(raw.get("cost_of_equity"))
        if coe != _NE and coe > -1.0:
            return coe, "cost_of_equity"
    irr = _ne(p.get("equity_post_tax_irr"))
    if irr != _NE and irr > -1.0:
        return irr, "equity_post_tax_irr (the report's finance inputs are no longer stored, " \
                    "so the cost of equity is not established; at the IRR the streams sum to 0)"
    return 0.0, "undiscounted (neither the cost of equity nor the IRR is established)"


def _ic_equity_attribution(report: dict, rate: float, top: int) -> dict:
    p = _section(report, "project").get("payload") or {}
    years = [int(y) for y in p.get("years") or []]
    eq = ((p.get("cash") or {}).get("equity_post_tax"))
    if not years or eq is None or any(_ne(v) == _NE for v in eq):
        note = _section(report, "project").get("note") or \
            "the post-tax equity cash is not established"
        return {"status": "not_established", "reason": str(note)[:300]}
    idx = {y: i for i, y in enumerate(years)}
    disc = [(1.0 + rate) ** -i for i in range(len(years))]
    by_label: dict[str, list[float]] = {}
    for ln in report.get("cashflow_lines") or []:
        i = idx.get(ln.get("year"))
        amt = ln.get("amount")
        if i is None or amt is None:
            continue
        if _ne(amt) == _NE:                       # NaN / inf in a stored report (review F4)
            return {"status": "not_established",
                    "reason": f"a cashflow line amount is not finite ({_cut(_ic_label(ln))})"}
        by_label.setdefault(_ic_label(ln), [0.0] * len(years))[i] += float(amt)
    cf = p.get("counterfactual_net")
    if p.get("has_counterfactual") and cf and all(v is not None for v in cf) and \
            not _ic_cf_lines_present(report):
        by_label[_IC_AVOIDED] = [-float(v) for v in cf]
    total = [sum(v[i] for v in by_label.values()) for i in range(len(years))]
    residual = max((abs(total[i] - float(eq[i])) for i in range(len(years))), default=0.0)
    pv = {k: sum(a * d for a, d in zip(v, disc)) for k, v in by_label.items()}
    streams = sorted(pv, key=lambda k: -abs(pv[k]))
    cells = sorted(((k, i, by_label[k][i] * disc[i]) for k in by_label
                    for i in range(len(years)) if by_label[k][i] != 0.0),
                   key=lambda c: -abs(c[2]))
    return {
        "status": "ok",
        "equity_post_tax_irr": _ne(p.get("equity_post_tax_irr"), 6),
        "equity_post_tax_npv_at_rate": _money(sum(float(c) * d for c, d in zip(eq, disc))),
        "sum_of_stream_pvs": _money(sum(pv.values())),
        "reconciles": residual <= 0.01 + 1e-9 * max(1.0, max(abs(float(v)) for v in eq)),
        "max_yearly_residual": _money(residual),
        "streams_total": len(streams),
        "by_stream": [{"stream": k, "pv": _money(pv[k]),
                       "undiscounted": _money(sum(by_label[k])),
                       "effect_on_irr": ("raises" if pv[k] > 0 else
                                         "lowers" if pv[k] < 0 else "none")}
                      for k in streams[:top]],
        "largest_cells": [{"stream": k, "year": years[i], "pv": _money(v),
                           "amount": _money(by_label[k][i])} for k, i, v in cells[:top]],
    }


def _ic_min_dscr_year(report: dict, top: int) -> dict:
    d = _section(report, "debt")
    dp = d.get("payload") or {}
    p = _section(report, "project").get("payload") or {}
    years = [int(y) for y in p.get("years") or []]
    dscr = dp.get("dscr") or []
    cands = [(float(v), i) for i, v in enumerate(dscr) if _ne(v) != _NE]   # NaN-safe
    if d.get("status") != "ok" or not cands or not years:
        return {"status": "not_established",
                "reason": str(d.get("note") or "no debt service, so no DSCR")[:300]}
    vmin = min(v for v, _ in cands)
    v, i = next((w, j) for w, j in cands                # a tie: the earliest year
                if abs(w - vmin) <= 1e-9 * max(1.0, abs(vmin)))
    year = years[i]
    tied = sum(1 for w, _ in cands if abs(w - v) <= 1e-9 * max(1.0, abs(v)))

    def at(key):
        s = dp.get(key) or []
        return _ne(s[i] if i < len(s) else None, 2)

    parts: dict[str, float] = {}
    for ln in report.get("cashflow_lines") or []:
        if ln.get("year") != year or ln.get("amount") is None:
            continue
        src = (ln.get("provenance") or {}).get("source")
        if _ic_is_finance_source(src) and src != "replacement_capex":
            continue
        label = _ic_label(ln)
        parts[label] = parts.get(label, 0.0) + float(ln["amount"])
    cf = p.get("counterfactual_net") or []
    if p.get("has_counterfactual") and i < len(cf) and cf[i] is not None and \
            not _ic_cf_lines_present(report):
        parts[_IC_AVOIDED] = -float(cf[i])
    cfads = at("cfads")
    comp = sorted(parts.items(), key=lambda kv: -abs(kv[1]))
    out = {"status": "ok", "year": year, "dscr": _ne(v, 4), "years_at_min": tied,
           "cfads": cfads,
           "cfads_parts_total": len(comp),
           "cfads_by_stream": [{"stream": k, "amount": _money(a)} for k, a in comp[:top]],
           "cfads_reconciles": (cfads != _NE and
                                abs(sum(parts.values()) - cfads) <= 0.01 + 1e-9 * abs(cfads)),
           "service": {"interest": at("interest"), "principal": at("principal"),
                       "total": at("service")},
           "by_tranche": []}
    for t in (dp.get("tranches") or [])[:4]:
        def ts(key, t=t):
            s = t.get(key) or []
            return _ne(s[i] if i < len(s) else None, 2)
        out["by_tranche"].append({"index": t.get("index"), "kind": _cut(t.get("kind"), 40),
                                  "interest": ts("interest"), "principal": ts("principal")})
    return out


_IC_EXPLAIN_METHOD = (
    "Equity IRR: each stream's post-tax equity cash lines (the report's cashflow lines, "
    "per year) discounted at `rate` to the financial close (end-of-year, year 0 "
    "undiscounted); the operating lines are the owner's TOTAL cash, so the avoided supply "
    "cost (the counterfactual, returns are on the incremental cash) is its own stream; the "
    "stream PVs sum to the post-tax equity NPV at that rate. A stream with a positive PV "
    "raises the IRR, a negative one lowers it. Min-DSCR year (the earliest on a tie; "
    "`years_at_min` counts the tied years): CFADS = incremental operating "
    "cash - replacement capex (terminal value excluded), split by stream; debt service = "
    "interest + principal, per tranche.")


def explain_cashflow(detail: str = "summary") -> dict:
    """Explain the stored investment-case report (IC P4): the largest
    contributions to the post-tax equity IRR by stream and by (stream, year),
    and the min-DSCR year's CFADS and debt-service composition. Not
    `explain_investment` (which explains one asset's LP sizing)."""
    from routers.simulation import _state as sim_state

    if detail not in ("summary", "full"):
        _ic_raise(422, "investment_case_request_invalid",
                  f"detail must be 'summary' or 'full', got {_cut(detail, 40)!r}")
    status, report = _ic_read()
    if report is None:
        return _no_data("investment_case", _IC_NO_DATA if status is None else
                        "no stored report yet: the run has not finished")
    top = 8 if detail == "summary" else 20
    rate, basis = _ic_rate(report, sim_state)
    stale = ((status or {}).get("report") or {}).get("stale")
    p = _section(report, "project").get("payload") or {}
    out: dict[str, Any] = {
        "status": "ok", "method": _IC_EXPLAIN_METHOD,
        "rate": {"value": round(rate, 6), "basis": basis},
        "currency": p.get("currency"), "stale": _NE if stale is None else stale,
        "equity_irr": _ic_equity_attribution(report, rate, top),
        "min_dscr_year": _ic_min_dscr_year(report, top),
    }
    # Fit the cap: fewer rows, then the method's long form.
    while _json_len(out) > _IC_SUMMARY_CHARS and top > 2:
        top = max(2, top // 2)
        out["equity_irr"] = _ic_equity_attribution(report, rate, top)
        out["min_dscr_year"] = _ic_min_dscr_year(report, top)
        out["rows_limited_to"] = top
    if _json_len(out) > _IC_SUMMARY_CHARS:
        out["method"] = ("stream PVs of the post-tax equity cash at `rate` (sum = equity NPV; "
                         "positive raises the IRR); min-DSCR year: CFADS by stream, service "
                         "by tranche")
    return out


# ── Validation (3) ──────────────────────────────────────────────────────────


def validate_network() -> list[dict]:
    from routers.simulation import preflight as _h
    return _h()


def check_solver_availability() -> dict:
    from routers.simulation import check_solvers as _h
    return _h()


def dispatch_status() -> dict:
    """
    B3: NO HTTP endpoint. Direct service call.

    Uses dispatch_status_detail so the result matches the schema's promised
    {state, mismatched_classes} shape (the plain dispatch_status returns a bare
    string).
    """
    from services.dispatch_status import dispatch_status_detail as _ds
    n = PyPSAService.get_network()
    return _ds(n)


# ── Simulation execution (4) ────────────────────────────────────────────────


def run_simulation() -> dict:
    # The route handler is `run`, NOT `run_simulation` — the latter is
    # `services.solver_service.run_simulation` re-exported via the router
    # module's `from services.solver_service import run_simulation` import.
    # Calling that low-level function bypasses the worker thread + lifecycle
    # state machine. Fixed Phase 4 walkthrough finding.
    # (No `force` param: the handler takes no args and there is no empty-network
    # gate to bypass — the old schema-declared `force` was a no-op, removed.)
    from routers.simulation import run as _h
    return _h()


def run_ac_pf_stage() -> dict:
    # Same shape as run_simulation — `run_ac_pf` is the route handler. The
    # router module re-exports `run_ac_pf_stage` from solver_service as the
    # service-level entry point; we want the HTTP-equivalent handler.
    from routers.simulation import run_ac_pf as _h
    return _h()


def abort_simulation() -> dict:
    from routers.simulation import abort as _h
    return _h()


def force_reset_simulation() -> dict:
    """v4-NIT-2: classified destructive (single tier — NOT execution_long_running)."""
    from routers.simulation import force_reset as _h
    return _h()


# ── Adequacy / solution-FMEA (10) ───────────────────────────────────────────
#
# The reliability surface (services/adequacy/*, routed under /api/results)
# was reachable only from the worksheet UI: none of its endpoints had a chat
# tool, so the agent could read a solved plan's COST in a dozen ways and its
# RELIABILITY in none. These tools close that gap — one read dispatcher
# over the twelve no-argument GETs, the two per-project sidecars, the five
# study starters and one abort.
#
# Two properties every caller here depends on:
#
#   * The GETs answer 204 when nothing has been computed (never run, or no
#     solve to judge), which `_adequacy_payload` maps to an explicit
#     `{"status": "no_data", …}` dict — see `_payload_or_no_data` for why a
#     bare `Response` must never reach the model. What is specific here is the
#     MESSAGE: each kind names its own missing precondition, because "no
#     frontier" and "no reserve margin" have different remedies.
#   * The study POSTs are ASYNCHRONOUS by construction — each publishes a
#     worker thread and returns `{"status": "running"}` immediately. The agent
#     must poll the matching GET kind to see rows/points/iterations land. They
#     are execution-tier for the same reason `run_simulation` is: minutes of
#     LP solves, and (for the sweep/frontier/loops/EH) a network the engine
#     mutates and restores.

# kind → handler symbol in routers.results. Every one takes NO arguments.
_ADEQUACY_HANDLER_NAMES: dict[str, str] = {
    "copt": "get_copt",
    "fmea_modes": "get_fmea_modes",
    "fmea_sweep": "get_fmea_sweep",
    "frontier": "get_frontier",
    "mc": "get_mc",
    "mc_elcc_candidates": "get_mc_elcc_candidates",
    "coupling_loop": "get_coupling_loop",
    "margin_loop": "get_margin_loop",
    "adequacy": "get_adequacy",
    "reserve_margin": "get_reserve_margin",
    "eh_study": "get_eh_study",
    "eh_reference_design": "get_eh_reference_design",
    # EH sibling tables (E2E review m5): the report summarises them; these
    # are the per-option / per-contingency rows behind each section.
    "eh_redundancy": "get_eh_redundancy",
    "eh_levers": "get_eh_levers",
    "eh_dtc": "get_eh_dtc",
    "eh_dtc_planning": "get_eh_dtc_planning",
}

# Why each kind can be empty. Surfaced verbatim on the no_data result so the
# agent tells the user WHICH precondition is missing instead of "no data".
_ADEQUACY_NO_DATA_HINTS: dict[str, str] = {
    "copt": (
        "the COPT engine found no dispatchable fleet to convolve — add "
        "conventional generators, or check that outage rates are set"
    ),
    "fmea_modes": (
        "no failure modes: the COPT ranking is empty and no contingency "
        "sweep has run in this session"
    ),
    "fmea_sweep": "no class-B/C contingency sweep has run in this session",
    "frontier": "no cost-vs-availability study has run in this session",
    "mc": "no sequential Monte-Carlo study has run in this session",
    "mc_elcc_candidates": "no assets are eligible for an ELCC study",
    "coupling_loop": "no coupling loop has run in this session",
    "margin_loop": "no margin loop has run in this session",
    "adequacy": (
        "nothing has been solved, or the last solve ran without a "
        "reliability target"
    ),
    "reserve_margin": (
        "nothing has been solved, the last solve set no reserve margin, or "
        "it produced no dispatch to judge one against"
    ),
    "eh_study": "no Energy Hub reference-design study has run in this session",
    "eh_reference_design": (
        "no Energy Hub ReferenceDesignReport has been stored — run "
        "run_eh_study first"
    ),
    "eh_redundancy": "no EH study with the redundancy stage has run",
    "eh_levers": "no EH study with the levers stage has run",
    "eh_dtc": "no EH study with the dtc_stress stage has run",
    "eh_dtc_planning": "no EH study with the dtc_planning stage has run",
}

# Path outlier, same shape as get_results' ac_pf_status (v4-MAJOR-4): eleven of
# the twelve kinds map 1:1 to /api/results/{kind}; mc_elcc_candidates is nested
# under /mc.
_ADEQUACY_PATH_OUTLIERS: dict[str, str] = {
    "mc_elcc_candidates": "/api/results/mc/elcc_candidates",
}

# study key → abort handler symbol in routers.results. `adequacy`,
# `reserve_margin`, `copt`, `fmea_modes`, `mc_elcc_candidates` and
# `eh_reference_design` are absent BY CONSTRUCTION: they are read-only
# surfaces computed on demand or stashed by a solve, with no worker thread
# to stop.
_ADEQUACY_ABORT_HANDLER_NAMES: dict[str, str] = {
    "fmea_sweep": "post_fmea_sweep_abort",
    "frontier": "post_frontier_abort",
    "mc": "post_mc_abort",
    "coupling_loop": "post_coupling_loop_abort",
    "margin_loop": "post_margin_loop_abort",
    "eh_study": "post_eh_study_abort",
}


def _resolve_adequacy_handler(kind: str, table: dict[str, str] | None = None):
    """Resolve an adequacy GET/abort handler by kind, or raise 400/500."""
    table = _ADEQUACY_HANDLER_NAMES if table is None else table
    if kind not in table:
        raise HTTPException(
            400,
            f"Unknown adequacy kind: {kind!r}. Known: "
            f"{', '.join(sorted(table))}",
        )
    from routers import results as results_router
    handler = getattr(results_router, table[kind], None)
    if handler is None:
        raise HTTPException(
            500, f"Handler {table[kind]!r} missing from routers.results")
    return handler


def _adequacy_payload(kind: str, result: Any) -> Any:
    """
    `_payload_or_no_data` with the per-kind precondition as the message.

    The reliability surface earns per-kind hints where /api/results makes do
    with one sentence: "no frontier" and "no reserve margin" have different
    remedies, and the agent is the one who has to name the missing one.
    """
    return _payload_or_no_data(
        kind, result,
        _ADEQUACY_NO_DATA_HINTS.get(
            kind, "nothing has been computed for this kind yet"),
    )


def get_adequacy_results(kind: str) -> Any:
    """
    Read one reliability surface. Mirrors `get_results`' dispatcher shape:
    a lookup dict, a 400 on an unknown kind, and one path outlier.
    """
    handler = _resolve_adequacy_handler(kind)
    return _adequacy_payload(kind, handler())


def adequacy_path_for(kind: str) -> str:
    """The route path a given adequacy kind reads (endpoint-map cross-check)."""
    return _ADEQUACY_PATH_OUTLIERS.get(kind, f"/api/results/{kind}")


def get_fmea_worksheet(name: str) -> dict:
    """
    The per-project FMEA sidecar: expert rows + per-mode overlays.

    Computed rows are NOT here — they come from `get_adequacy_results`
    ('fmea_modes') and the worksheet merges the two client-side.
    """
    from routers.adequacy_worksheet import get_worksheet as _h
    return _h(project=_authorized_project(name))


def get_stress_scenarios(name: str) -> dict:
    """The per-project class-C stress-scenario registry."""
    from routers.adequacy_worksheet import get_stress_scenarios as _h
    return _h(project=_authorized_project(name))


def put_stress_scenarios(name: str, scenarios: list) -> dict:
    """Replace the per-project class-C registry (whole list; 422 names the
    rule a scenario breaks). P22: lets the assistant apply a recommended
    scenario — read the registry first and send it back with the change."""
    from routers.adequacy_worksheet import (
        StressScenariosPut,
    )
    from routers.adequacy_worksheet import put_stress_scenarios as _h
    # Through `_route`: master's sidecar lock gate (68e5f62c3) declares `db`
    # and `user`; called bare they arrived as `Depends` sentinels and every
    # real (uuid-bearing) project crashed in server mode (merge review B1).
    return _route(_h, body=StressScenariosPut(scenarios=scenarios),
                  project=_authorized_project(name))


def get_eh_template(name: str) -> dict:
    """The Energy Hub template a project was created from (P19): recommended
    archetype, pack overrides, stages, DtC attribution and study notes."""
    from routers.adequacy_worksheet import get_eh_template as _h
    out = _h(project=_authorized_project(name))
    if not isinstance(out, dict):
        return {"status": "no_data",
                "message": f"project {name!r} was not created from an Energy "
                           "Hub template"}
    return out


def get_feature_guide(tour: str | None = None, field: str | None = None) -> dict:
    """The in-app guide (P21) the GUI's tours and hover tips show — the same
    wording, so explanations match the screen. ``tour`` returns one tour's
    steps; ``field`` one field's help; neither returns the index."""
    from services.guides import load_guide
    guide = load_guide("eh_fmea")
    if field is not None:
        text = guide["fields"].get(field)
        if text is None:
            raise HTTPException(
                404, f"no guide entry for field {field!r}; known: "
                f"{sorted(guide['fields'])}")
        return {"field": field, "help": text}
    if tour is not None:
        t = guide["tours"].get(tour)
        if t is None:
            raise HTTPException(
                404, f"no tour {tour!r}; known: {sorted(guide['tours'])}")
        return {"tour": tour, **t}
    return {"tours": {k: {"title": v["title"], "intro": v.get("intro"),
                          "steps": len(v["steps"])}
                      for k, v in guide["tours"].items()},
            "fields": sorted(guide["fields"])}


def review_eh_study() -> dict:
    """Analyse the latest Energy Hub study (P22): findings with evidence,
    recommendations and exact tool actions the user may choose to apply.
    One source with ``GET /api/results/eh_review`` (P24):
    ``eh_review.review_latest``."""
    from routers import results as R
    from services.adequacy.eh_review import review_latest

    record = R.get_eh_study()
    return review_latest(
        R._state, record if isinstance(record, dict) else None,
        no_data_message=_ADEQUACY_NO_DATA_HINTS["eh_reference_design"])


def suggest_eh_setup(archetype: str | None = None) -> dict:
    """Suggested Energy Hub tags for the live network (P25): the grid import
    Link, the point-of-connection bus, critical buses, and units without
    outage data — each with a reason and a ready update_component /
    bulk_update_components action. READ: nothing is applied; the write tools
    confirm whatever the user picks."""
    from services.adequacy.eh_setup import suggest_eh_setup as _suggest
    return _suggest(PyPSAService.get_network(), archetype=archetype)


def _campaign_gated(study: str, start, **estimate_kwargs):
    """
    Run a study under the active campaign's budget, if there is one.

    Check, start, THEN record — never charge-then-refund. A study that fails
    to start (409 while the mesh is busy, 422 for a missing VOLL) must not
    burn budget, and a refund path would be a second place for the total to go
    wrong. Nothing can slip between the check and the record: the study mesh
    allows at most one study in flight.

    With no campaign running both calls are no-ops, so a single study asked
    for directly behaves exactly as it did before this existed.
    """
    from services.adequacy import campaign

    solves = campaign.estimate_solves(PyPSAService.get_network(), study,
                                      **estimate_kwargs)
    campaign.check(study, solves)
    result = start()
    charged = campaign.record(study, solves)
    if charged is None:
        return result
    return {**result, "campaign": {
        "objective": charged["objective"],
        "solves_charged": solves,
        "spent_solves": charged["spent_solves"],
        "remaining_solves": charged["remaining_solves"],
    }}


def start_campaign(objective: str, budget_solves: int | None = None) -> dict:
    """Open a reliability campaign with one budget across every study."""
    from services.adequacy import campaign
    return campaign.start(objective, budget_solves)


def campaign_status() -> dict:
    """The running campaign's objective, budget, spend and study log."""
    from services.adequacy import campaign
    return campaign.status()


def end_campaign(note: str | None = None) -> dict:
    """Close the campaign and return its final record."""
    from services.adequacy import campaign
    return campaign.end(note)


def run_fmea_sweep(scenarios: list | None = None) -> dict:
    """
    Start the class-B (single link outage) contingency sweep, plus any
    class-C scenarios passed in.

    `scenarios` are re-validated by the route. They come from
    `get_stress_scenarios`, which is where authorization lives — this route
    operates on the FOREGROUND network and carries no project name.
    """
    from routers.results import FmeaSweepRequest, post_fmea_sweep as _h
    rows = list(scenarios or [])
    return _campaign_gated(
        "fmea_sweep", lambda: _h(FmeaSweepRequest(scenarios=rows)),
        scenarios=rows)


def run_frontier_study(targets_permyriad: list | None = None) -> dict:
    """
    Start the ε-constraint cost-vs-availability sweep: ONE full
    capacity-expansion solve per target, so the plan is re-optimised at every
    point. Omitting `targets_permyriad` uses the engine's default spread.
    """
    from routers.results import FrontierRequest, post_frontier as _h
    return _campaign_gated(
        "frontier",
        lambda: _h(FrontierRequest(targets_permyriad=targets_permyriad)),
        targets_permyriad=targets_permyriad)


def run_mc_study(
    draws: int | None = None,
    seed: int | None = None,
    cov_target: float | None = None,
    elcc_assets: list | None = None,
    elcc_portfolio: bool | None = None,
) -> dict:
    """
    Start the sequential Monte-Carlo adequacy study (LOLE / EUE, optionally
    with an ELCC table).

    Alone among the four studies this SOLVES NOTHING and never mutates the
    network — its metrics are hours and MWh, not euros, so it needs no VOLL.
    It is still mutually exclusive with the others: the snapshot it samples
    must not be a half-mutated network.
    """
    from routers.results import McRequest, post_mc as _h
    return _campaign_gated("mc", lambda: _h(McRequest(
        draws=draws,
        seed=seed,
        cov_target=cov_target,
        elcc_assets=elcc_assets,
        elcc_portfolio=elcc_portfolio,
    )))


def run_coupling_loop(
    target_lole_h: float,
    draws: int | None = None,
    seed: int | None = None,
    eps0: float | None = None,
    max_solves: int | None = None,
    restore: str | None = None,
) -> dict:
    """
    Drive the ENS CAP (ε) until the sampled plan meets `target_lole_h`:
    solve at ε, measure LOLE by Monte Carlo, adjust, repeat.

    `target_lole_h` is HORIZON-basis hours, not h/yr — convert before calling
    on a multi-year horizon, and say which basis you used when reporting.
    """
    from routers.results import CouplingLoopRequest, post_coupling_loop as _h
    return _campaign_gated(
        "coupling_loop",
        lambda: _h(CouplingLoopRequest(
            target_lole_h=target_lole_h,
            draws=draws,
            seed=seed,
            eps0=eps0,
            max_solves=max_solves,
            restore=restore,
        )),
        max_solves=max_solves)


def run_margin_loop(
    target_lole_h: float,
    draws: int | None = None,
    seed: int | None = None,
    max_solves: int | None = None,
    restore: str | None = None,
) -> dict:
    """
    Drive the PLANNING RESERVE MARGIN until the sampled plan meets
    `target_lole_h` — the firm-capacity lever, where run_coupling_loop turns
    the energy lever.

    There is deliberately no starting-margin parameter: the start is a
    MEASUREMENT taken by a probing solve, and a user-supplied one is the
    single number here that can silently make the study worthless.
    """
    from routers.results import MarginLoopRequest, post_margin_loop as _h
    return _campaign_gated(
        "margin_loop",
        lambda: _h(MarginLoopRequest(
            target_lole_h=target_lole_h,
            draws=draws,
            seed=seed,
            max_solves=max_solves,
            restore=restore,
        )),
        max_solves=max_solves)


def run_eh_study(
    archetype: str,
    stages: list | None = None,
    budget_solves: int | None = None,
    pack_overrides: dict | None = None,
    dtc_config: dict | None = None,
    dsr_buses: list | None = None,
    mc: dict | None = None,
    dtc_attribution: str | None = None,
) -> dict:
    """
    Start the Energy Hub reference-design study for one archetype pack.

    Applies the pack, runs the EH pipeline, and persists a
    ``ReferenceDesignReport`` for ``get_adequacy_results('eh_reference_design')``.
    Poll ``get_adequacy_results('eh_study')`` while it runs.
    """
    from routers.results import EhStudyRequest, post_eh_study as _h
    return _campaign_gated(
        "eh_study",
        lambda: _h(EhStudyRequest(
            archetype=archetype,
            stages=stages,
            budget_solves=budget_solves,
            pack_overrides=pack_overrides,
            dtc_config=dtc_config,
            dtc_attribution=dtc_attribution,
            dsr_buses=dsr_buses,
            mc=mc,
        )),
        budget_solves=budget_solves)


def abort_adequacy_study(study: str) -> dict:
    """
    Ask a running study to stop at its next boundary.

    IDEMPOTENT, and 200 even when the run has already finished. The closing
    base restore still runs, so an abort costs the work in flight plus that
    restore — it does NOT leave the network mid-contingency. 404 only when
    the named study has never run in this session.
    """
    handler = _resolve_adequacy_handler(study, _ADEQUACY_ABORT_HANDLER_NAMES)
    return handler()


# ── Solve queue (4) ─────────────────────────────────────────────────────────


def solve_queue_enqueue(project_id: str) -> dict:
    # Handler is enqueue_solve(req: EnqueueRequest) — wrap the id in the model.
    from routers.solve_queue import enqueue_solve as _h, EnqueueRequest
    return _route(_h, EnqueueRequest(project_id=project_id))


def solve_queue_list() -> dict:
    # P-1: `_route`, not a bare `_h()`. All four handlers take `db`/`user` now,
    # so a direct call would hand `user` the raw `Depends` sentinel — and before
    # they did, this tool read every org's queued project names.
    from routers.solve_queue import list_queue as _h
    return _route(_h)


def solve_queue_abort(job_id: str) -> dict:
    # Job ids are UUIDs (0005_solve_jobs). The old `int(job_id)` coercion
    # existed because the jobs dict was int-keyed and a string silently missed
    # every key — it now has to go, or every abort raises ValueError before it
    # reaches the handler.
    from routers.solve_queue import abort_job as _h
    return _route(_h, job_id)


def solve_queue_clear_finished() -> dict:
    """N1: read-tier — drops listing entries only, idempotent."""
    # P-1: super-admin only, so this 403s for an ordinary chat caller. That is
    # the intended outcome — the queue is process-global and the clear crosses
    # every org.
    from routers.solve_queue import clear_finished as _h
    return _route(_h)


# ── Project management (21) ─────────────────────────────────────────────────

# ── Acting identity for project-scoped tools (Step 0a) ──────────────────────
# Every `routers.projects` handler now takes `db` + `user` and authorizes the
# project against the caller's org. The chat tools call those handlers DIRECTLY
# (in-process, not over HTTP), so they have to supply the same identity — a
# direct call gets no dependency injection, and before this the unresolved
# `Depends` sentinel reached `user.id` and raised
# `AttributeError: 'Depends' object has no attribute 'id'`.
#
# The identity travels as a contextvar holding the USER ID, not a Session: the
# chat stream is an SSE generator that outlives its request, and the request's
# `Depends(get_db)` session is closed the moment the handler returns. Each tool
# therefore opens its own short-lived session.
#
# Tools run on `chat_service._TOOL_EXECUTOR`, and contextvars do NOT propagate
# into executor threads by themselves — the submit site copies the context.
# STEP 0b replaces this with the session-bound active project, at which point
# the identity comes from the session row rather than a contextvar.

_ACTING_USER_ID: ContextVar[str | None] = ContextVar("chat_acting_user_id", default=None)
# The acting SESSION travels as an ID for the same reason the user does, and one
# more: a `SessionRow` captured at request time belongs to the request's DB
# session, which is closed before the first tool runs, so touching it would
# raise DetachedInstanceError. `deps.current_session` re-resolves per request
# for exactly that reason; `_acting_session` re-fetches per tool call.
_ACTING_SESSION_ID: ContextVar[str | None] = ContextVar(
    "chat_acting_session_id", default=None
)


def set_acting_user(user_id: str | None) -> None:
    """Bind the user whose authority the tools in this context act with."""
    _ACTING_USER_ID.set(str(user_id) if user_id is not None else None)


def acting_user_id() -> str | None:
    return _ACTING_USER_ID.get()


def set_acting_session(session_id) -> None:
    """Bind the session whose active-project pointer this turn's tools may move."""
    _ACTING_SESSION_ID.set(str(session_id) if session_id is not None else None)


# C-3 — the LLMProfile this turn is running on, for tools that make their own
# model sub-call. `reconstruct_network_from_image` is the only one today, and
# it was entirely profile-blind: it built an Anthropic client and hardcoded
# DEFAULT_MODEL no matter which provider the user had selected.
#
# Carried as a contextvar for the same reason the acting user is: tools run on
# `chat_service._TOOL_EXECUTOR`, and the submit site already does
# `contextvars.copy_context()`. `None` is a legal answer and means "not inside
# a turn" (a direct call, or a test invoking the tool on its own), where there
# is no profile to honour and the pre-profile behaviour is correct.
_TURN_PROFILE: ContextVar[Any] = ContextVar("chat_turn_profile", default=None)


def set_turn_profile(profile: Any) -> None:
    """Bind the LLMProfile whose model a tool's own sub-call must use."""
    _TURN_PROFILE.set(profile)


def turn_profile() -> Any:
    """The bound `LLMProfile`, or None outside a turn."""
    return _TURN_PROFILE.get()
# ── gridspine: planning → dynamics studies (8) ─────────────────────────────
#
# These call services/gridspine_service.py DIRECTLY — the same functions the
# /api/gridspine router wraps — so the copilot and the UI share one
# implementation (spec, "Copilot parity"). `_service_call_` in TOOL_ROUTES.
# The project is resolved through `project_registry.resolve_project` under the
# acting identity, exactly as `require_project_access` does for the router:
# 404 for "no such project" and "not yours" alike.
#
# `gridspine_edit_template_param` hard-codes edited_by="chat": the ledger
# provenance the spec asks for, and the one thing the router's `user` default
# and this wrapper must never share.


def _gridspine_project(db, user, project_id: str):
    from services import project_registry
    return project_registry.resolve_project(db, user, project_id)


def gridspine_create_study(name: str, config: dict | None = None) -> dict:
    from services.gridspine_service import create_study as _h
    with _acting() as (db, user):
        return _h(db, user, name, config=config)


def gridspine_set_dispatch_source(
    project_id: str, from_dispatch: str | None = None, from_project: str | None = None,
) -> dict:
    from services.gridspine_service import set_dispatch_source as _h
    with _acting() as (db, user):
        if from_project is not None:
            source = {"from_project": from_project}
        elif from_dispatch is not None:
            source = {"from_dispatch": from_dispatch}
        else:
            source = "generate"
        return _h(db, _gridspine_project(db, user, project_id), source, user=user)


def gridspine_get_config(project_id: str) -> dict:
    from services.gridspine_service import get_config as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), db=db)


def gridspine_update_config(project_id: str, **patch) -> dict:
    from services.gridspine_service import update_config as _h
    with _acting() as (db, user):
        return _h(
            _gridspine_project(db, user, project_id),
            {k: v for k, v in patch.items() if v is not None},
            db=db, user=user,
        )


def gridspine_run_pipeline(project_id: str) -> dict:
    from services.gridspine_service import run_pipeline as _h
    with _acting() as (db, user):
        return _h(db, _gridspine_project(db, user, project_id), user=user)


def gridspine_get_stage_status(project_id: str) -> dict:
    from services.gridspine_service import get_stage_status as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def gridspine_list_ranked_snapshots(project_id: str) -> list:
    from services.gridspine_service import list_ranked_snapshots as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def gridspine_get_assumption_ledger(project_id: str) -> dict:
    from services.gridspine_service import get_assumption_ledger as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def gridspine_edit_template_param(project_id: str, unit_id: str, param: str,
                                  value: float, source: str) -> dict:
    from services.gridspine_service import edit_template_param as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), unit_id, param, value, source, "chat")


def gridspine_get_readback(project_id: str) -> dict:
    from services.gridspine_service import get_readback as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def gridspine_fetch_result_figure(project_id: str, hour: int, name: str) -> dict:
    from services.gridspine_service import fetch_result_figure as _h
    with _acting() as (db, user):
        # `hour` goes through UNCOALESCED: the service owns the 422 for an
        # unparseable one, and `int()` here would raise before it could answer.
        return _h(_gridspine_project(db, user, project_id), name, hour)


def gridspine_get_capacity(project_id: str, bus: str | None = None, kind: str | None = None,
                           hour: int | None = None) -> dict:
    from services.gridspine_service import get_capacity as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), bus=bus, kind=kind, hour=hour)


def gridspine_compute_capacity(project_id: str, bus: str, kind: str) -> dict:
    from services.gridspine_service import compute_capacity as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), bus, kind)


def gridspine_get_connection_assessments(project_id: str, assessment_id: str | None = None,
                                         hour: int | None = None) -> dict:
    from services.gridspine_service import get_connection as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), assessment_id=assessment_id, hour=hour)


def campus_get_study(project_id: str) -> dict:
    from services.campus_electrical_service import get_state as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_draft_campus(project_id: str, overwrite: bool = False) -> dict:
    from services.campus_electrical_service import draft as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), bool(overwrite))


def campus_run_study(project_id: str, k: int | None = None, pf: float | None = None,
                     profile: str | None = None, margin: float | None = None,
                     n_minus_1: bool | None = None, invest: bool | None = None,
                     pcc_switchgear_by_operator: bool | None = None) -> dict:
    from services.campus_electrical_service import run as _h
    settings = {key: v for key, v in (("k", k), ("profile", profile), ("margin", margin),
                                       ("n_minus_1", n_minus_1), ("invest", invest),
                                       ("pcc_switchgear_by_operator", pcc_switchgear_by_operator))
                if v is not None}
    settings["pf"] = pf
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), settings)


def campus_get_library(project_id: str) -> dict:
    from services.campus_electrical_service import get_library as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_set_library(project_id: str, yaml: str) -> dict:
    from services.campus_electrical_service import save_library as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), yaml)


def campus_get_investment(project_id: str) -> dict:
    from services.campus_electrical_service import get_investment as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_list_grid_codes(project_id: str) -> dict:
    from services.campus_grid_code_service import list_grid_codes as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id))


def campus_extract_grid_code(project_id: str, document_id: str) -> dict:
    # No publish or confirm tool exists, on purpose: the copilot drafts, a
    # person confirms each limit and publishes, in the panel (plan C10).
    from services.campus_grid_code_service import extract as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), document_id)


def gridspine_assess_connection(project_id: str, bus: str, load_mw: float, load_pf: float = 0.98,
                                onsite_mw: float = 0.0, onsite_converter: bool = True,
                                profile: str = "eu_rfg_dcc_ce") -> dict:
    from services.gridspine_service import assess_facility as _h
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), {
            "bus": bus, "load_mw": float(load_mw), "load_pf": float(load_pf),
            "onsite_mw": float(onsite_mw), "onsite_converter": bool(onsite_converter), "profile": profile,
        })


def gridspine_export_handoff_bundle(project_id: str, hour: int) -> dict:
    from services.gridspine_service import export_handoff_bundle as _h
    with _acting() as (db, user):
        # Pass-through, as above: the service answers 422 on a bad hour.
        project = _gridspine_project(db, user, project_id)
        path = _h(project, hour)
        # The DOWNLOAD ROUTE, not `str(path)`. The absolute path named the
        # server's storage root and the org and project UUIDs, and it was
        # useless to the model and the user alike — nothing can fetch a
        # server-side path. The route is what the study view links to.
        # `hour` as the service accepted it — never `int(hour)` here: the
        # tools are pass-throughs, and coercing in the wrapper turns the
        # service's 422 into a 500 (test_gridspine_service pins that).
        from urllib.parse import quote

        return {
            "download_url": (
                f"/api/gridspine/{quote(project.name, safe='')}"
                f"/bundles/{quote(str(hour), safe='')}"
            ),
            "filename": path.name,
            "bytes": path.stat().st_size,
        }


@contextlib.contextmanager
def _acting():
    """Yield ``(db, user)`` for one project-scoped call."""
    user_id = _ACTING_USER_ID.get()
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error_kind": "no_acting_user",
                "message": (
                    "This tool acts on projects and needs an authenticated "
                    "session. Reload the workbench and retry."
                ),
            },
        )
    from db.models import User
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        user = db.get(User, uuid.UUID(user_id))
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        # P-2 — re-check the account status, do not trust the bind.
        #
        # The HTTP path refuses a non-active user at `resolve_session`
        # (`services/auth_service.py:83`), so the request that opened this chat
        # turn could not have reached us with a disabled account. That check is
        # not enough on its own: the SSE generator OUTLIVES its request, and
        # every tool it dispatches re-enters `_acting()` afterwards. Looking the
        # user up by id and testing only `is None` meant an account disabled
        # mid-turn kept full tool authority — save, delete, solve — until the
        # stream ended. Deliberately the same predicate as `resolve_session`, so
        # the two gates cannot drift: any status but "active" is refused.
        if user.status != "active":
            raise HTTPException(
                status_code=401,
                detail={
                    "error_kind": "inactive_acting_user",
                    "message": (
                        "This account is no longer active, so it can no longer "
                        "act on projects. Sign in again, or ask an "
                        "administrator to re-enable it."
                    ),
                },
            )
        yield db, user
    finally:
        db.close()


def _acting_session(db):
    """
    Re-fetch the acting session row inside `db`, or None when none is bound.

    Deliberately re-read rather than carried as an ORM object: the row would
    otherwise belong to the request's DB session, which `chat_stream` closes
    long before the SSE generator dispatches a tool, and every attribute read
    would raise DetachedInstanceError. None is a legal answer — local mode
    issues no session cookie at all, and there the HTTP path passes None too.
    """
    session_id = _ACTING_SESSION_ID.get()
    if session_id is None:
        return None
    from db.models import Session as SessionRow

    return db.get(SessionRow, uuid.UUID(session_id))


def _route(handler, *args, **kwargs):
    """Call a project-router handler with the acting identity injected."""
    with _acting() as (db, user):
        params = inspect.signature(handler).parameters
        # `save_project` and `activate_project` declare a THIRD dependency,
        # `session: SessionRow | None = Depends(current_session)`, and use it to
        # move the session's active-project pointer. Unsupplied, they receive the
        # raw `Depends` and die on `.active_project_id`; supplied as a constant
        # None they stop crashing but a chat-driven Save-As silently leaves the
        # browser pointing at the old project. Every injection is keyed off the
        # signature because handlers reached here declare different subsets and
        # would raise TypeError on an unexpected keyword — `reset_network`
        # (`routers/network.py:1898`) declares `db` and `session` but no `user`.
        injected = {n: v for n, v in (("db", db), ("user", user),
                                      ("actor", user)) if n in params}
        if "session" in params and "session" not in kwargs:
            injected["session"] = _acting_session(db)
        # `_route`'s contract is "resolve whatever the target declares", and
        # nothing but this loop enforces it. A FOURTH dependency added to any
        # routed handler would otherwise arrive as a raw `Depends` sentinel and
        # die with an AttributeError deep inside the body — which is exactly how
        # F3 hid behind F1's 401 for a full cycle. A static scan is no
        # substitute: the DISPATCHERS-to-handler mapping is established by ~40
        # function-local imports, which is why earlier AST scans missed F3.
        consumed = set(list(params)[: len(args)]) | injected.keys() | kwargs.keys()
        for name, param in params.items():
            if name not in consumed and isinstance(param.default, fastapi_params.Depends):
                raise RuntimeError(
                    f"_route() cannot satisfy dependency {name!r} of "
                    f"{getattr(handler, '__module__', '?')}."
                    f"{getattr(handler, '__qualname__', handler)}: it supplies only "
                    f"db/user/actor/session. Resolve it at the call site or extend _route()."
                )
        return handler(*args, **{**injected, **kwargs})


def _authorized_project(name: str):
    """Resolve `name` to the `AuthorizedProject` the compare routes take."""
    from routers.deps import require_project_access

    with _acting() as (db, user):
        return require_project_access(name, db=db, user=user)




def list_projects() -> list[dict]:
    """
    v6-F2: each entry's `resident` flag is a snapshot at READ TIME. A
    concurrent eviction between this call and a follow-up activate_project
    can flip resident=true → false; activate_project takes the cold path
    automatically in that case (projects.py:1319-1326), both still succeed.
    """
    from routers.projects import list_projects as _h
    result = _route(_h)
    # Augment with resident: bool (snapshot-at-read-time per v6-F2).
    resident_set = set(PyPSAService._contexts.keys())
    if isinstance(result, list):
        return [
            (
                {**p, "resident": p.get("name") in resident_set}
                if isinstance(p, dict)
                else {**p.model_dump(), "resident": p.name in resident_set}
            )
            for p in result
        ]
    return result


def load_project(name: str) -> dict:
    """
    GET /api/projects/{name} — returns ImportSummary per schemas.py:455-464.
    v6-F3: outputs are {buses, generators, lines, links, storage_units,
    stores, loads, transformers, snapshots}.
    """
    from routers.projects import load_project as _h
    return _route(_h, name)


def activate_project(project_id: str) -> dict:
    """
    POST /api/projects/{project_id}/activate — instant resident-switch path.
    v6-F2: tolerates concurrent eviction (cold path at projects.py:1319-1326).
    """
    from routers.projects import activate_project as _h
    return _route(_h, project_id)


def save_project(name: str, force: bool = False, expect: str | None = None) -> dict:
    """
    DESTRUCTIVE — overwrites projects/<name>/. The v6 F1 backend guard inside
    `with ctx.mutation_lock:` (projects.py:976-992) catches cross-project
    name claims. This tool wrapper does NOT add a pre-check (the agent is
    asserting same-name autosave / first-save semantics).
    """
    from routers.projects import save_project as _h
    return _route(_h, name, force=force, expect=expect)


def save_project_as(name: str) -> dict:
    """
    POST /api/projects/{name}?rebind=true with M1 chat-side PRE-CHECK:
    if `name` already exists on disk AND the active ctx.loaded_project != name,
    refuse with HTTPException 409 BEFORE issuing the POST. This is a defence
    in depth — the backend F1 guard would catch the unintended overwrite,
    but the chat agent should not even attempt the POST.
    """
    # M1 pre-check
    projects = list_projects()  # returns list[dict] augmented w/ resident
    names = {p.get("name") if isinstance(p, dict) else p.name for p in projects}
    active_loaded = PyPSAService.get_loaded_project()
    if name in names and active_loaded != name:
        # Structured detail dict so the chat agent's _dispatch_real_tool_call
        # can extract error_kind='project_exists' and surface the
        # ChatPanel typed-confirmation flow (v4-MAJOR-1 / v6-F1).
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "project_exists",
                "message": (
                    f"Project '{name}' already exists on disk and active "
                    f"binding is '{active_loaded}'. Save-As to an existing "
                    f"name would overwrite its data; ask the user before "
                    f"retrying with force=true or pick a fresh name."
                ),
            },
        )
    from routers.projects import save_project as _h
    return _route(_h, name, rebind=True)


def save_project_a_copy(name: str) -> dict:
    """POST /api/projects/{name} with rebind=False — branches on disk."""
    from routers.projects import save_project as _h
    return _route(_h, name, rebind=False)


def rename_project(name: str, new_name: str) -> dict:
    from routers.projects import rename_project as _h
    from models.schemas import RenameProjectRequest
    body = RenameProjectRequest(new_name=new_name)
    return _route(_h, name, body)


def delete_project(name: str, cascade: bool = False) -> dict:
    """
    v4-MINOR-1: cascade param. On 409 with descendants the chat layer surfaces
    the descendant list in the Confirmation Card; the agent must NOT auto-cascade.
    """
    from routers.projects import delete_project as _h
    return _route(_h, name, cascade=cascade)


def create_scenario(base: str, new_name: str, description: str | None = None) -> dict:
    """
    POST /api/projects/{base}/scenarios. The route handler rejects when the
    active ctx is not `base` (projects.py:1529). Chat.jsonl is COPIED into
    the new scenario dir (F12 — Phase 4 polish).
    """
    from routers.projects import create_scenario as _h
    from models.schemas import CreateScenarioRequest
    body = CreateScenarioRequest(name=new_name, description=description or "")
    return _route(_h, base, body)


def list_scenarios(name: str) -> list[dict]:
    """
    B2: NO /scenarios endpoint exists. Derived tool — filter list_projects by
    parent_project == name.
    """
    all_projects = list_projects()
    result = []
    for p in all_projects:
        d = p if isinstance(p, dict) else p.model_dump()
        if d.get("parent_project") == name:
            result.append(d)
    return result


def get_project_results_bundle(name: str) -> dict:
    from routers.projects import get_results_bundle as _h
    return _route(_h, name)


def get_project_layout(name: str) -> dict:
    from routers.projects import get_layout as _h
    return _route(_h, name)


def update_project_layout(name: str, layout: dict) -> dict:
    from routers.projects import put_layout as _h
    return _route(_h, name, layout)


def download_project_bundle(name: str) -> dict:
    from routers.projects import _project_bundle_bytes
    proj = _authorized_project(name)
    return _save_agent_export(
        _project_bundle_bytes(proj.name, proj.directory),
        f"{proj.name}.pypsaproj.zip",
        "application/zip",
    )


def get_project_statistics(name: str) -> dict:
    from routers.projects import project_statistics as _h
    return _route(_h, name)


def get_project_network_meta(project_id: str) -> dict:
    """C11: non-active project meta peek without losing active."""
    # Handler get_project_meta(ctx: ProjectContext = ProjectDep) takes its ctx
    # via FastAPI dependency injection — a direct call gets no DI, so resolve the
    # ctx ourselves (validates id + 404s, resolve-or-load-resident, never touches
    # the active slot) and pass it explicitly.
    from routers.deps import resolve_project_context
    from routers.project_network import get_project_meta as _h
    with _acting() as (db, user):
        return _h(ctx=resolve_project_context(project_id, db=db, user=user))


def list_project_network_component(project_id: str, component_class: str) -> list[dict]:
    """C11: read one component table from a NON-ACTIVE project."""
    attr = "global_constraints" if component_class == "GlobalConstraint" \
        else _GENERIC_CRUD_ATTRS.get(component_class)
    if attr is None:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    # Handler get_project_component(component_class, ctx=ProjectDep) takes the
    # lowercase-plural attr segment + a DI-injected ctx. Resolve the ctx ourselves
    # and pass the attr. (The path-scoped handler serves the 8 asset components
    # only — GlobalConstraint/carriers will 404 there, by its allow-list.)
    from routers.deps import resolve_project_context
    from routers.project_network import get_project_component as _h
    with _acting() as (db, user):
        return _h(
            component_class=attr,
            ctx=resolve_project_context(project_id, db=db, user=user),
        )


def import_project_bundle(bundle_bytes_b64: str, filename: str = "bundle.zip") -> dict:
    """C9: import a project bundle export."""
    import base64
    import io
    from fastapi import UploadFile
    from routers.projects import import_bundle as _h
    data = base64.b64decode(bundle_bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    with _acting() as (db, user):
        # `import_bundle` now also declares `session: SessionRow | None =
        # Depends(current_session)` (it moves the session's active-project
        # pointer after a successful import). This call bypasses `_route`
        # because `import_bundle` is async and `_route` calls its handler
        # synchronously — so `session` must be injected by hand here the same
        # way `_route` does it, or it arrives as the raw `Depends` sentinel and
        # `set_active_project` blows up on it (see `_route`'s docstring on this
        # exact failure mode).
        return _sync(_h(upload, db=db, user=user, session=_acting_session(db)))


def create_project_from_template(template_id: str, new_name: str) -> dict:
    """C9: scaffold from a built-in template."""
    # Handler is create_from_template(template_id, name: str | None) — pass the
    # NAME STRING, not a dict (it does (name or "").strip() on the 2nd arg).
    from routers.projects import create_from_template as _h
    return _route(_h, template_id, new_name)


def get_project_compare_state(name: str) -> dict:
    from routers.compare import get_compare_state as _h
    return _h(project=_authorized_project(name))


def get_project_results_summary(name: str) -> dict:
    from routers.compare import get_results_summary as _h
    return _h(project=_authorized_project(name))


_COMPARE_FOCUS_TO_TAB = {
    "overview": "overview",
    "capacity": "capacity",
    "dispatch": "dispatch",
    "economics": "economics",
    "emissions": "emissions",
    "prices": "prices",
    "curtailment": "curtailment",
    "lost_load": "lost_load",
    "storage_cycling": "storage_cycling",
    "all": "overview",
}

_FOCUS_SUMMARY_KEYS = {
    "capacity": "capacity",
    "dispatch": "dispatch",
    "economics": "economics",
    "emissions": "emissions",
    "prices": "prices",
    "curtailment": "curtailment",
    "lost_load": "lost_load",
    "storage_cycling": "storage_cycling",
}


def _model_to_dict(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return value.model_dump()
        except Exception:  # noqa: BLE001
            return value
    return value


def _cpv_total(cpv: Any) -> float | None:
    """CarrierPeriodValue → float total (dict or model)."""
    if cpv is None:
        return None
    if isinstance(cpv, (int, float)):
        return float(cpv)
    if isinstance(cpv, dict):
        t = cpv.get("total")
        return float(t) if isinstance(t, (int, float)) else None
    t = getattr(cpv, "total", None)
    return float(t) if isinstance(t, (int, float)) else None


def _sum_cpv_map(mapping: Any) -> float:
    if not isinstance(mapping, dict):
        return 0.0
    total = 0.0
    for v in mapping.values():
        t = _cpv_total(v)
        if t is not None:
            total += t
    return total


def _sum_cpv_map_if_available(mapping: Any, has_solve: bool) -> float | None:
    """`_sum_cpv_map`, gated on the block having resolved.

    `capacity.available` and `dispatch.available` are both exactly
    `has_solve` (routers/compare.py's `_compute_capacity_summary` /
    `_compute_dispatch_summary` early-return their all-default block
    whenever `not has_solve` and set `available=True` on every success
    path) — so `has_solve` is the correcting signal for these by-carrier
    sums, matching `_cpv_total`'s existing None-on-unresolved behaviour
    instead of defaulting to a confident 0.0 (ADR-0001).
    """
    if not has_solve:
        return None
    return _sum_cpv_map(mapping)


def _scenario_headlines(summary: dict) -> dict:
    cap = summary.get("capacity") or {}
    disp = summary.get("dispatch") or {}
    if not isinstance(cap, dict):
        cap = _model_to_dict(cap) or {}
    if not isinstance(disp, dict):
        disp = _model_to_dict(disp) or {}
    has_solve = bool(summary.get("has_solve"))
    return {
        "has_solve": has_solve,
        "is_multi_period": bool(summary.get("is_multi_period")),
        "periods": list(summary.get("periods") or []),
        "capacity_mw_total": _sum_cpv_map_if_available(cap.get("capacity_mw_by_carrier"), has_solve),
        "capex_meur_total": _sum_cpv_map_if_available(cap.get("capex_meur_by_carrier"), has_solve),
        "new_capex_meur_total": _sum_cpv_map_if_available(cap.get("new_capex_meur_by_carrier"), has_solve),
        "dispatch_gwh_total": _sum_cpv_map_if_available(disp.get("dispatch_gwh_by_carrier"), has_solve),
        "opex_meur": _cpv_total(disp.get("opex_meur")),
        "total_load_gwh": _cpv_total(disp.get("total_load_gwh")),
    }


def _delta_numeric(a: Any, b: Any) -> float | None:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(b) - float(a)
    return None


def compare_scenarios(
    project_a: str,
    project_b: str,
    focus: str = "overview",
    open_compare_rail: bool = False,
) -> dict:
    """
    Side-by-side numeric comparison of two saved projects/scenarios.

    Pulls both results-summary payloads without activating either project.
    Optional ``open_compare_rail`` emits a navigate ui_event so the frontend
    opens Results + the A|B compare rail on the matching tab.
    """
    focus_key = (focus or "overview").strip().lower()
    if focus_key not in _COMPARE_FOCUS_TO_TAB:
        focus_key = "overview"

    raw_a = _model_to_dict(get_project_results_summary(project_a))
    raw_b = _model_to_dict(get_project_results_summary(project_b))
    if not isinstance(raw_a, dict):
        raw_a = {}
    if not isinstance(raw_b, dict):
        raw_b = {}

    head_a = _scenario_headlines(raw_a)
    head_b = _scenario_headlines(raw_b)
    deltas = {
        k: _delta_numeric(head_a.get(k), head_b.get(k))
        for k in (
            "capacity_mw_total", "capex_meur_total", "new_capex_meur_total",
            "dispatch_gwh_total", "opex_meur", "total_load_gwh",
        )
    }

    focus_payload: dict[str, Any] | None = None
    section_key = _FOCUS_SUMMARY_KEYS.get(focus_key)
    if section_key:
        focus_payload = {
            "a": _model_to_dict(raw_a.get(section_key)),
            "b": _model_to_dict(raw_b.get(section_key)),
        }
    elif focus_key == "all":
        focus_payload = {
            k: {
                "a": _model_to_dict(raw_a.get(k)),
                "b": _model_to_dict(raw_b.get(k)),
            }
            for k in _FOCUS_SUMMARY_KEYS.values()
        }

    out: dict[str, Any] = {
        "project_a": project_a,
        "project_b": project_b,
        "focus": focus_key,
        "a": head_a,
        "b": head_b,
        "delta_b_minus_a": deltas,
        "focus_section": focus_payload,
        "note": (
            "Figures are from each project's last saved results-summary "
            "(not unsaved in-memory edits). delta = B − A."
        ),
    }
    if open_compare_rail:
        out["_ui_event"] = True
        out["kind"] = "navigate"
        out["panel_id"] = "Results"
        out["compare_rail"] = True
        out["compare_a"] = project_a
        out["compare_b"] = project_b
        out["compare_tab"] = _COMPARE_FOCUS_TO_TAB[focus_key]
        out["results_tab"] = {
            "overview": "overview",
            "capacity": "capex",
            "dispatch": "dispatch",
            "economics": "economics",
            "emissions": "emissions",
            "prices": "prices",
            "curtailment": "curtailment",
            "lost_load": "lostload",
            "storage_cycling": "storage",
            "all": "overview",
        }.get(focus_key, "overview")
    return out


# ── Project snapshots (4) — checkpoint backups, NOT time snapshots ──────────


def create_project_snapshot(name: str, label: str, message: str | None = None) -> dict:
    # Handler is create_snapshot(req: CreateSnapshotRequest, project:
    # AuthorizedProject = ProjectAccessDep, db, user) and reads req.label /
    # req.message — pass the model, not a dict (a direct call doesn't get
    # FastAPI's body parsing, so a dict would AttributeError on req.label).
    #
    # `_route`, NOT a positional call. The handler grew `db`/`user` Depends
    # when `_enforce_project_lock` landed in its body; called positionally,
    # both arrived as raw `Depends` sentinels and `user.id` raised
    # AttributeError inside the lock check in auth mode — so the gate this
    # tool is supposed to pass through could never fire. `project` is still
    # resolved here (it is an `AuthorizedProject`, which `_route` cannot
    # supply) and handed over as a positional; `_route` injects the rest.
    # Same treatment `restore_project_snapshot` already had.
    from routers.snapshots import create_snapshot as _h, CreateSnapshotRequest
    return _route(
        _h,
        CreateSnapshotRequest(label=label, message=message or ""),
        _authorized_project(name),
    )


def list_project_snapshots(name: str) -> list[dict]:
    from routers.snapshots import list_snapshots as _h
    return _h(_authorized_project(name))


def restore_project_snapshot(name: str, snapshot_id: str) -> dict:
    # `restore_snapshot` now also declares `db`/`user`/`session` (it moves the
    # session's active-project pointer after a successful restore, same as
    # load_project). Unlike `import_bundle`, this handler is plain `def` — not
    # async — so `_route` (chat_tools.py:1494) can call it directly and inject
    # all three the way it already does for `activate_project`/`load_project`,
    # instead of hand-injecting them here. Calling it positionally with just
    # `snapshot_id` and `project` — as this used to — would otherwise hand
    # `db`, `user` and `session` their raw `Depends` sentinels and crash (see
    # `_route`'s docstring on this exact failure mode).
    from routers.snapshots import restore_snapshot as _h
    return _route(_h, snapshot_id, _authorized_project(name))


def delete_project_snapshot(name: str, snapshot_id: str) -> None:
    # `_route` for the same reason as `create_project_snapshot` above:
    # `delete_snapshot` declares `db`/`user` and calls `_enforce_project_lock`.
    from routers.snapshots import delete_snapshot as _h
    _route(_h, snapshot_id, _authorized_project(name))


# ── Import / Export (8) ─────────────────────────────────────────────────────


def _import_raw(handler, upload) -> dict:
    """Run one raw-import route (async) with the acting db + session supplied.

    The routes un-point the session after a successful import (see
    `routers.io._unbind_session`), so they now declare `db` and `session`. The
    handler is async, so `_route` cannot be used — its `with _acting()` would
    close `db` before the coroutine ran — and the dependencies are injected by
    hand inside the block instead, exactly as `import_project_bundle` does.

    With no acting identity (a direct in-process call) there is no session
    whose pointer could move, and these tools never required one before, so
    that case keeps working rather than turning into a 401.
    """
    if _ACTING_USER_ID.get() is None:
        return _sync(handler(upload, db=None, session=None))
    with _acting() as (db, _user):
        return _sync(handler(upload, db=db, session=_acting_session(db)))


def import_network_nc(bytes_b64: str, filename: str = "network.nc") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_netcdf as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _import_raw(_h, upload)


def import_csv_bundle(bytes_b64: str, filename: str = "csv.zip") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_csv as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _import_raw(_h, upload)


def import_excel(bytes_b64: str, filename: str = "network.xlsx") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_excel as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _import_raw(_h, upload)


def import_matpower(bytes_b64: str, filename: str = "case.m") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_matpower as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _import_raw(_h, upload)


def _save_agent_export(data: bytes, filename: str, mime: str) -> dict:
    """
    Persist agent-generated export bytes as an `agent_export` upload artifact and
    return lightweight metadata. The frontend's chat file strip renders
    agent_export uploads as downloadable chips.

    Why not return the bytes inline: a chat-tool result must be JSON-serializable
    AND fits in the model's context — base64 of a multi-MB netcdf/zip would flood
    the context window (and was the bug: the wrappers used to return a Starlette
    StreamingResponse, which json.dumps stringified to "<StreamingResponse ...>").
    Requires a loaded project (the artifact lives in that project's uploads dir);
    the HTTP export routes still stream to the browser without one.
    """
    from services import upload_service
    from services.pypsa_service import PyPSAService
    name = PyPSAService.get_loaded_project()
    if not name:
        raise HTTPException(
            400, "No project is loaded — save or load a project before exporting "
                 "(the export is attached to the project as a downloadable file)."
        )
    # Writing into the project's uploads directory is a write edge, and
    # `routers/uploads.py::post_upload` refuses it under a foreign lock for
    # that reason. This helper is the single chokepoint every `export_*` tool
    # reaches, so the check lives here rather than on ten wrappers — the
    # export tools are tiered `read` and `write` inconsistently, so the
    # derived gate cannot cover them as a family, and re-tiering them would
    # change their confirmation behaviour for unrelated reasons.
    _check_foreign_lock("export")
    meta = upload_service.add_upload(name, data, filename, mime, kind="agent_export")
    return {
        "file_id": meta.file_id,
        "filename": meta.filename,
        "mime": meta.mime,
        "size": meta.size,
        "kind": meta.kind,
        "message": (
            f"Exported '{meta.filename}' ({meta.size} bytes). It's available as a "
            "downloadable file in the chat panel's file strip."
        ),
    }


def export_network_nc() -> dict:
    from routers.io import _export_netcdf_bytes
    return _save_agent_export(_export_netcdf_bytes(), "network.nc", "application/x-netcdf")


def export_csv_bundle() -> dict:
    from routers.io import _export_csv_zip_bytes
    return _save_agent_export(_export_csv_zip_bytes(), "network_csv.zip", "application/zip")


def export_excel() -> dict:
    from routers.io import _export_excel_bytes
    return _save_agent_export(
        _export_excel_bytes(), "network.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def export_matpower() -> dict:
    from routers.io import _export_matpower_text
    return _save_agent_export(
        _export_matpower_text().encode("utf-8"), "network.m", "text/plain",
    )


# ── Audit / Undo (4) ────────────────────────────────────────────────────────


def audit_log(limit: int | None = None) -> list[dict]:
    # Both changelog handlers take `user`/`db` as unresolved `Depends` defaults
    # and org-scope the trail off `user`, so they must be routed, not called.
    from routers.changelog import get_changelog as _h
    entries = _route(_h)
    if limit is not None and isinstance(entries, list):
        return entries[-limit:]
    return entries


def clear_audit_log() -> None:
    from routers.changelog import clear_changelog as _h
    _route(_h)


def undo_last() -> dict:
    from routers.network import undo_last as _h
    return _h()


def undo_status() -> dict:
    from routers.network import undo_info as _h
    return _h()


# ── UI control (3) ──────────────────────────────────────────────────────────
# These return marker dicts the chat SSE generator picks up as ui_event
# frames the ChatPanel forwards to uiStore. NO backend mutation.


def ui_select_component(component_class: str, name: str) -> dict:
    return {"_ui_event": True, "kind": "select_component",
            "component_class": component_class, "name": name}


def ui_open_panel(
    panel_id: str,
    results_tab: str | None = None,
    bottom_tab: str | None = None,
    compare_rail: bool | None = None,
    compare_a: str | None = None,
    compare_b: str | None = None,
    compare_tab: str | None = None,
) -> dict:
    """
    Navigate the GUI. Emits a ``ui_event`` SSE frame (kind=navigate) that
    ChatPanel applies to uiStore — slide panels, Results sub-tabs, bottom
    asset tabs, and the A|B compare rail.
    """
    event: dict[str, Any] = {
        "_ui_event": True,
        "kind": "navigate",
        "panel_id": panel_id,
    }
    if results_tab:
        event["results_tab"] = results_tab
    if bottom_tab:
        event["bottom_tab"] = bottom_tab
    if compare_rail is not None:
        event["compare_rail"] = bool(compare_rail)
    if compare_a:
        event["compare_a"] = compare_a
    if compare_b:
        event["compare_b"] = compare_b
    if compare_tab:
        event["compare_tab"] = compare_tab
    return event


def ui_set_snapshot(snapshot_iso: str, period: int | None = None) -> dict:
    return {"_ui_event": True, "kind": "set_snapshot",
            "snapshot_iso": snapshot_iso, "period": period}


# ── Harness: ask the user (chat harness issue 04) ──────────────────────────

ASK_USER_MAX_OPTIONS = 8


def ask_user(
    title: str,
    question: str,
    options: list[dict],
    allow_free_text: bool = True,
) -> dict:
    """
    Present a structured question. Non-blocking by design (owner decision
    Q4): the loop turns this marker into a `choice_request` frame, the panel
    renders a Choice card, and the pick comes back as the next user message.
    The model gets `{status: "presented"}` and is told to end its turn.

    Validation is strict and typed (`invalid_tool_args`) because a half-built
    card — no options, two recommendations — is worse than none.
    """
    def bad(message: str) -> HTTPException:
        return HTTPException(status_code=422, detail={
            "error_kind": "invalid_tool_args", "message": f"ask_user: {message}",
        })

    if not isinstance(title, str) or not title.strip():
        raise bad("title must be a non-empty string")
    if not isinstance(question, str) or not question.strip():
        raise bad("question must be a non-empty string")
    if not isinstance(options, list) or not options:
        raise bad("options must be a non-empty list")
    if len(options) > ASK_USER_MAX_OPTIONS:
        raise bad(f"at most {ASK_USER_MAX_OPTIONS} options")
    clean: list[dict] = []
    seen: set[str] = set()
    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            raise bad(f"option {i} must be an object")
        label = str(opt.get("label") or "").strip()
        if not label:
            raise bad(f"option {i} needs a label")
        if label.lower() in seen:
            raise bad(f"option labels must be distinct ({label!r} repeats)")
        seen.add(label.lower())
        entry: dict[str, Any] = {"label": label[:120]}
        desc = opt.get("description")
        if isinstance(desc, str) and desc.strip():
            entry["description"] = " ".join(desc.split())[:400]
        if opt.get("recommended"):
            entry["recommended"] = True
        clean.append(entry)
    if sum(1 for o in clean if o.get("recommended")) > 1:
        raise bad("mark at most one option as recommended")
    return {
        "_ui_event": True,
        "kind": "choice",
        "title": " ".join(title.split())[:160],
        "question": " ".join(question.split())[:800],
        "options": clean,
        "allow_free_text": bool(allow_free_text),
    }


_CHAT_SESSION: ContextVar[Any] = ContextVar("chat_session", default=None)


def set_chat_session(session: Any) -> None:
    """Bind the ChatSession whose workflow state this turn's tools may move
    (chat harness issue 06). Set by run_turn beside set_turn_profile; the
    executor copies the context, so the tool thread sees it."""
    _CHAT_SESSION.set(session)


def chat_session() -> Any:
    return _CHAT_SESSION.get()


def use_skill(name: str) -> dict:
    """The body of a harness skill (issue 05). The catalogue (names and
    descriptions) is in the system prompt; the body only travels on
    request, so the prompt stays stable while procedures change."""
    from harness import skills
    key = str(name or "").strip().lower()
    try:
        skill = skills.get(key)
    except KeyError:
        raise HTTPException(status_code=404, detail={
            "error_kind": "unknown_skill",
            "message": f"no skill named {key!r}; the available skills are listed in your instructions",
        }) from None
    return {"name": skill.name, "description": skill.description, "instructions": skill.body}


def _workflow_session():
    sess = chat_session()
    if sess is None:
        raise HTTPException(status_code=500, detail={
            "error_kind": "internal_error",
            "message": "workflow tools need a chat session bound to the turn",
        })
    return sess


def _describe_step(wf, step) -> dict:
    ids = [s.id for s in wf.steps]
    return {
        "workflow": wf.id,
        "title": wf.title,
        "step": step.id,
        "step_title": step.title,
        "step_index": ids.index(step.id) + 1,
        "step_count": len(ids),
        "done_when": step.done_when,
        "instructions": step.body,
        "steps": [{"id": s.id, "title": s.title} for s in wf.steps],
        "note": ("These instructions are also attached to each of your turns "
                 "while this workflow is active; call advance_workflow when the "
                 "step is done, end_workflow to leave."),
    }


def start_workflow(workflow_id: str) -> dict:
    """Start a workflow on this session (issue 06): state is (id, step) on
    the ChatSession; the per-turn addendum carries the step from the next
    turn on, and this result carries it for the current one."""
    from harness import workflows
    key = str(workflow_id or "").strip().lower()
    wf = workflows.registry().get(key)
    if wf is None or wf.status != "active":
        raise HTTPException(status_code=404, detail={
            "error_kind": "unknown_workflow",
            "message": f"no active workflow {key!r}; the ids are "
                       + ", ".join(sorted(w.id for w in workflows.registry().values()
                                          if w.status == "active")),
        })
    sess = _workflow_session()
    step = wf.steps[0]
    sess.workflow = {"id": wf.id, "step": step.id}
    return _describe_step(wf, step)


def advance_workflow(step: str) -> dict:
    from harness import workflows
    sess = _workflow_session()
    state = getattr(sess, "workflow", None)
    if not state:
        raise HTTPException(status_code=409, detail={
            "error_kind": "no_active_workflow",
            "message": "no workflow is active on this session; call start_workflow first",
        })
    wf = workflows.get(state["id"])
    key = str(step or "").strip().lower()
    try:
        target = wf.step(key)
    except KeyError:
        raise HTTPException(status_code=404, detail={
            "error_kind": "unknown_workflow_step",
            "message": f"{wf.id!r} has no step {key!r}; its steps are "
                       + ", ".join(s.id for s in wf.steps),
        }) from None
    sess.workflow = {"id": wf.id, "step": target.id}
    return _describe_step(wf, target)


def end_workflow() -> dict:
    sess = _workflow_session()
    state = getattr(sess, "workflow", None)
    sess.workflow = None
    return {"ended": state["id"] if state else None}


# ── Conversation (2) ────────────────────────────────────────────────────────


def list_chat_history(limit: int | None = None) -> list[dict]:
    """
    Lock-free tail-read of ctx.chat_state.persist_path (chat.jsonl). Skips
    trailing partial lines on JSONDecodeError. Merges across rotated
    chat.jsonl.1 if it exists.
    """
    import json
    from services import chat_service
    ctx = PyPSAService.get_active_context()
    path = chat_service.get_persist_path(ctx)
    if path is None or not path.exists():
        return []
    # Also pick up the most recent rotated file so a freshly-rotated session
    # still surfaces the recent turns.
    rotated = path.with_suffix(path.suffix + ".1")
    sources = [rotated, path] if rotated.exists() else [path]
    turns: list[dict] = []
    for p in sources:
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        # trailing partial line — skip
                        continue
        except OSError:
            continue
    if limit is not None:
        return turns[-limit:]
    return turns


def clear_chat_history() -> dict:
    """Empties chat.jsonl under ctx.chat_state.lock (M9)."""
    from services import chat_service
    ctx = PyPSAService.get_active_context()
    path = chat_service.get_persist_path(ctx)
    if path is None:
        return {"cleared": False, "reason": "unbound_ctx"}
    with ctx.chat_state.lock:
        if path.exists():
            path.unlink()
        rotated = path.with_suffix(path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
    return {"cleared": True}


def clear_uploads() -> dict:
    """
    Delete every upload (and agent export) for the active project.

    Locked decision row 7 — uploads are INDEPENDENT of chat history. This
    tool is the explicit "purge all files" lever; ``clear_chat_history``
    does NOT touch uploads, and a project's ``clear_uploads`` call does
    NOT touch chat.jsonl.

    Returns ``{cleared: int, files: list[str]}`` so the chat panel can
    surface the count.
    """
    from services import upload_service
    name = _require_active_project()
    metas = upload_service.list_uploads(name)
    cleared: list[str] = []
    for m in metas:
        resp = upload_service.delete_upload(name, m.file_id)
        if resp.deleted:
            cleared.append(m.filename)
    return {"cleared": len(cleared), "files": cleared}


# ── Chatbot uploads — consume (5) ───────────────────────────────────────────
#
# These tools let the agent inspect + use files the user dragged into the
# chat panel (Phase A storage; Phase D upload UI). All run against the ACTIVE
# project — the project that owns the uploads dir is the same one the agent
# sees as "loaded". A non-loaded context returns the same `no_active_project`
# error the existing export tools raise.


def _require_active_project() -> str:
    """
    Return the active project name or raise an HTTPException(400).

    Reused by every Phase B upload tool — the upload dir is per-project and
    a tool call with no active project has no canonical destination.
    """
    name = PyPSAService.get_loaded_project()
    if not name:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "no_active_project",
                "message": (
                    "No project is loaded — load or save a project before "
                    "interacting with uploads (uploads live under the active "
                    "project's directory)."
                ),
            },
        )
    return name


def list_uploads() -> list[dict]:
    """List the active project's uploads (user uploads + agent exports)."""
    from services import upload_service
    name = _require_active_project()
    return [
        {
            "file_id": m.file_id,
            "filename": m.filename,
            "mime": m.mime,
            "kind": m.kind,
            "size_kb": round(m.size / 1024, 1),
            "uploaded_at": m.uploaded_at,
            "page_count": m.page_count,
        }
        for m in upload_service.list_uploads(name)
    ]


def read_upload_meta(file_id: str) -> dict:
    """Full meta.json for one upload."""
    from services import upload_service
    name = _require_active_project()
    return upload_service.get_upload_meta(name, file_id).model_dump()


def read_excel_sheet(
    file_id: str,
    sheet_name: str | None = None,
    max_rows: int = 200,
) -> dict:
    """
    Parse the Excel/CSV upload referenced by `file_id` and return a preview.

    Returns:
        {
            "columns": [str, ...],
            "rows": [[cell, cell, ...], ...],   # max_rows entries
            "total_rows": int,                  # actual size on disk
            "total_cols": int,
            "sheet_name": str,                  # ACTUAL sheet name pandas read (never a placeholder)
            "available_sheets": [str, ...],     # all sheets in the workbook
            "truncated": bool,                  # rows >= max_rows
        }

    Cap defaults to 200 rows so the LLM context isn't blown by a 50k-row
    workbook. For CSV: only one sheet — `sheet_name` is ignored.
    """
    from services import upload_service
    import pandas as pd

    name = _require_active_project()
    meta = upload_service.get_upload_meta(name, file_id)
    blob = upload_service.get_upload_path(name, file_id)
    is_csv = meta.mime in {"text/csv", "text/plain"} or meta.filename.lower().endswith(".csv")
    available_sheets: list[str] = []
    try:
        if is_csv:
            df = pd.read_csv(blob)
            actual_sheet = "(csv)"
            available_sheets = ["(csv)"]
        else:
            # Open the workbook via ExcelFile so we can (a) enumerate every
            # sheet name for the agent's next call, and (b) resolve "first
            # sheet" to its REAL name (e.g. "Sheet1", "Data", "load_H2") so a
            # follow-up `apply_demand_from_excel` doesn't have to guess.
            # Without this, the agent reuses the placeholder string `(first
            # sheet)` as the sheet_name kwarg, pandas raises ValueError, and
            # the user sees a confusing `excel_parse_failed` (incident
            # 2026-06-08).
            xl = pd.ExcelFile(blob)
            available_sheets = list(xl.sheet_names)
            if sheet_name:
                if sheet_name not in available_sheets:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error_kind": "excel_parse_failed",
                            "message": (
                                f"sheet {sheet_name!r} not in workbook; "
                                f"available: {available_sheets}"
                            ),
                        },
                    )
                actual_sheet = sheet_name
            else:
                # No sheet specified → first sheet by index 0, but report
                # its REAL name (not a placeholder string).
                actual_sheet = available_sheets[0] if available_sheets else "Sheet1"
            df = xl.parse(sheet_name=actual_sheet)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "excel_parse_failed",
                "message": f"failed to parse upload {file_id!r}: {exc}",
            },
        ) from exc
    total_rows = int(df.shape[0])
    truncated = total_rows > max_rows
    head = df.head(max_rows)
    # Coerce NaN/Inf → None so the JSON serialiser doesn't 500.
    rows = [
        [None if pd.isna(v) else v for v in row]
        for row in head.itertuples(index=False, name=None)
    ]
    return {
        "columns": [str(c) for c in df.columns],
        "rows": rows,
        "total_rows": total_rows,
        "total_cols": int(df.shape[1]),
        "sheet_name": actual_sheet,
        "available_sheets": available_sheets,
        "truncated": truncated,
    }


def apply_demand_from_excel(
    file_id: str,
    time_col: str,
    value_col: str,
    load_name: str,
    sheet_name: str | None = None,
) -> dict:
    """
    Parse an Excel/CSV upload's `time_col` + `value_col` into a per-snapshot
    Load demand profile, replacing any profile the Load already has. Two-pass:

      Pass 1 (NO mutation): parse the time + value columns, align to
        n.snapshots, return structured `error_kind` on mismatch.
      Pass 2 (LOCKED): write the aligned series into _user_ts so it's
        persisted alongside the project on next save.

    The load must already exist in the network (this tool doesn't create
    components — that's a separate `create_component` call).

    Errors (HTTPException 400 with structured detail):
      * `load_not_found`            — `load_name` missing from n.loads
      * `time_column_parse_error`   — time column can't be parsed as dt
      * `value_column_parse_error`  — value column has non-numeric data
      * `snapshot_count_mismatch`   — row count != len(n.snapshots)
      * `snapshot_range_mismatch`   — time range doesn't cover snapshots
    """
    import pandas as pd

    from services import upload_service
    name = _require_active_project()
    n = PyPSAService.get_network()

    if load_name not in n.loads.index:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "load_not_found",
                "message": (
                    f"load {load_name!r} not found in network; available: "
                    f"{list(n.loads.index)[:10]}"
                ),
            },
        )

    # Pass 1 — parse + align outside the lock. The upload blob is
    # content-addressed so mid-call mutation isn't a concern, but the
    # network mutation in Pass 2 IS guarded by the PyPSA lock.
    meta = upload_service.get_upload_meta(name, file_id)
    blob = upload_service.get_upload_path(name, file_id)
    is_csv = meta.mime in {"text/csv", "text/plain"} or meta.filename.lower().endswith(".csv")
    try:
        if is_csv:
            df = pd.read_csv(blob)
        else:
            # Reject the placeholder string `(first sheet)` the older
            # read_excel_sheet returned — the agent could echo it back as
            # sheet_name and pandas can't find such a sheet. Treat it as
            # "default sheet" same as None.
            if sheet_name in (None, "", "(first sheet)"):
                # Resolve to the real first-sheet name so a follow-up tool
                # call sees a usable identifier in any change_log entry.
                xl = pd.read_excel(blob, sheet_name=None)  # dict of {name: df}
                if not xl:
                    raise ValueError("workbook has no sheets")
                first_name = next(iter(xl.keys()))
                df = xl[first_name]
                sheet_name = first_name
            else:
                df = pd.read_excel(blob, sheet_name=sheet_name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={"error_kind": "excel_parse_failed", "message": str(exc)},
        ) from exc

    if time_col not in df.columns:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "time_column_parse_error",
                "message": f"column {time_col!r} not found in sheet; available: {list(df.columns)}",
            },
        )
    if value_col not in df.columns:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "value_column_parse_error",
                "message": f"column {value_col!r} not found in sheet; available: {list(df.columns)}",
            },
        )

    try:
        parsed_time = pd.to_datetime(df[time_col], errors="raise")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "time_column_parse_error",
                "message": f"column {time_col!r} contains non-datetime values: {exc}",
            },
        ) from exc

    try:
        parsed_values = pd.to_numeric(df[value_col], errors="raise")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "value_column_parse_error",
                "message": f"column {value_col!r} contains non-numeric values: {exc}",
            },
        ) from exc

    # Preserve the row order of the spreadsheet — DO NOT sort by time. For
    # multi-period networks the user's flat 26,280-row sheet maps positionally
    # to the MultiIndex snapshots (3 periods × 8,760 hours), and re-sorting
    # by the flat timestamps would interleave periods incorrectly.
    series = pd.Series(parsed_values.values, index=parsed_time.values)

    import numpy as _np
    import pandas as _pd
    snaps = n.snapshots
    is_multi_period = isinstance(snaps, _pd.MultiIndex)
    total_snaps = len(snaps)

    # Multi-period auto-tile: a 1-year operational profile is THE typical
    # multi-period demand input — the user expects the same hourly shape to
    # repeat each investment period. When the row count divides total_snaps
    # evenly into the period count we tile the values N× across periods.
    # Exact-count matches still flow through (no tiling needed). Anything
    # else is a mismatch.
    auto_tiled = False
    tile_factor = 1
    if is_multi_period:
        n_periods = len(snaps.get_level_values(0).unique())
        timesteps_per_period = total_snaps // n_periods
        if len(series) == total_snaps:
            values_array = series.values
        elif n_periods > 1 and len(series) == timesteps_per_period:
            values_array = _np.tile(series.values, n_periods)
            auto_tiled = True
            tile_factor = n_periods
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_kind": "snapshot_count_mismatch",
                    "message": (
                        f"Excel has {len(series)} rows. The network has "
                        f"{total_snaps} snapshots across {n_periods} investment "
                        f"periods ({timesteps_per_period} timesteps per period). "
                        f"Provide either {total_snaps} rows (full coverage) or "
                        f"{timesteps_per_period} rows (auto-tiled across periods)."
                    ),
                },
            )
    else:
        if len(series) != total_snaps:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_kind": "snapshot_count_mismatch",
                    "message": (
                        f"Excel has {len(series)} rows but network has "
                        f"{total_snaps} snapshots. Counts must match exactly."
                    ),
                },
            )
        values_array = series.values

    # Range check is only meaningful for FLAT networks where the
    # spreadsheet's time column corresponds 1:1 to the snapshot index. For
    # multi-period networks the operational year usually repeats across
    # periods, so the user's flat timestamps won't match the MultiIndex's
    # composite tuples — we trust positional alignment instead and skip
    # the strict range check.
    if not is_multi_period and list(series.index) != list(snaps):
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "snapshot_range_mismatch",
                "message": (
                    f"Excel time column starts at {series.index[0]} ends at "
                    f"{series.index[-1]}; network snapshots span "
                    f"{snaps[0]} → {snaps[-1]}. Ranges must match."
                ),
            },
        )

    # Pass 2 — write under the PyPSA lock so a concurrent solve can't see
    # half-applied state. We write the series directly into n.loads_t.p_set;
    # also call into `_user_ts` so save_project persists it. The values
    # array is reindexed positionally against n.snapshots — works for both
    # flat DatetimeIndex AND MultiIndex(period, timestep) targets.
    aligned = _pd.Series(values_array, index=snaps)
    # Phase 12f: this tool writes `loads_t.p_set` and `_user_ts` DIRECTLY,
    # bypassing every HTTP handler and therefore every guard on them. Its only
    # numeric gate is `pd.to_numeric(..., errors="raise")`, which passes NaN
    # straight through — an empty spreadsheet cell yields `[1.0, nan, 3.0]`.
    # A non-finite demand hour masks that snapshot's nodal balance, so refuse
    # here too, with the same message the upload routes give.
    try:
        from routers.network import _reject_nonfinite_timeseries
        _reject_nonfinite_timeseries(
            _pd.DataFrame({load_name: aligned}), "Load", "p_set")
    except ImportError:  # pragma: no cover — only if routers/network moves
        pass

    with PyPSAService.get_lock():
        try:
            from routers.network import _user_ts, _user_ts_lock
        except ImportError:  # pragma: no cover — only fails if routers/network refactor breaks paths
            _user_ts = None
            _user_ts_lock = None
        # Direct dataframe write so the LP sees the new demand on next solve.
        if value_col in n.loads_t.p_set.columns or load_name in n.loads_t.p_set.columns:
            n.loads_t.p_set[load_name] = aligned
        else:
            # Add a fresh column for the load.
            n.loads_t.p_set[load_name] = aligned
        # Persist to _user_ts so save_project writes user_ts.json.
        if _user_ts is not None and _user_ts_lock is not None:
            with _user_ts_lock:
                _user_ts[("loads", "p_set", load_name)] = aligned

    from services import change_log_service
    tile_note = (
        f" (auto-tiled {tile_factor}× across investment periods)"
        if auto_tiled else ""
    )
    change_log_service.log(
        "update", "Load", load_name,
        f"Applied demand profile from upload {file_id} "
        f"({len(aligned)} snapshots{tile_note})",
    )
    return {
        "applied": True,
        "load_name": load_name,
        "rows": len(aligned),
        "min": float(aligned.min()),
        "max": float(aligned.max()),
        "mean": float(aligned.mean()),
        # Surface the tile factor so the chat agent can mention it in the
        # confirmation message — "I tiled your 1-year profile across the
        # 3 investment periods" reads less surprising than silently
        # broadcasting the values.
        "auto_tiled": auto_tiled,
        "tile_factor": tile_factor,
    }


def delete_upload(file_id: str) -> dict:
    """
    Delete one upload from the active project. Idempotent: returns
    `{deleted: false, reason: "not_found"}` if the file_id is gone.
    """
    from services import upload_service
    name = _require_active_project()
    resp = upload_service.delete_upload(name, file_id)
    return resp.model_dump(exclude_none=True)


def reconstruct_network_from_image(
    file_id: str,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    client: Any | None = None,
) -> dict:
    """
    Read an image upload and ask Anthropic vision to identify the buses
    + lines in the diagram, then materialise them via the existing
    `create_component` helpers.

    Coordinate transform (image pixels → canvas grid, locked decision row 5):
        gx = (px - origin_x) * scale_x
        gy = (origin_y - py) * scale_y      # Y-flip (image origin is top-left)

    The default identity transform (1:1, no offset) is fine for a
    schematic diagram; the user / agent can override when they want to
    anchor to existing buses.

    Wrapped in a 30-second timeout. The original blob is preserved on
    disk; only the structured JSON the vision model returns drives the
    component creation. All components are created inside one undo
    snapshot so a misread can be reverted in one click.

    `client` is injected for tests; production callers omit it.
    """
    import asyncio
    import json as _json
    import re

    name = _require_active_project()

    # Resolve + cap-check the image up-front via the same path the run_turn
    # multimodal builder uses.
    from services import upload_service
    blocks = upload_service.build_multimodal_content_blocks(name, [file_id])
    if not blocks or blocks[0].get("type") != "image":
        raise HTTPException(
            status_code=415,
            detail={
                "error_kind": "mime_not_allowlisted_for_multimodal",
                "message": (
                    "reconstruct_network_from_image only accepts image "
                    "uploads (PNG/JPEG/WebP/GIF). PDFs go through the "
                    "document multimodal channel; use the chat-side "
                    "attachment_file_ids flow instead."
                ),
            },
        )

    # C-3 — honour the profile this turn is actually running on.
    #
    # This tool speaks the Anthropic SDK's `messages.stream` directly, so it
    # cannot run on the openai wire without being ported to the provider seam.
    # Until that port it REFUSES rather than silently substituting Anthropic:
    # a silent substitution ships the user's image to a provider they did not
    # choose and bills a model they did not select, while the deployment may
    # deliberately have no Anthropic key at all. Same `capability_unsupported`
    # shape `run_turn` uses for vision, and — same rule — the message names the
    # profile LABEL only, never an id or base_url, because redaction is
    # secrets-only and would scrub neither.
    #
    # `None` means "not inside a turn" (a direct call, or a test driving the
    # tool on its own): there is no profile to honour, so the pre-profile
    # behaviour stands unchanged.
    profile = turn_profile()
    if profile is not None:
        if profile.wire != "anthropic":
            raise HTTPException(
                status_code=409,
                detail={
                    "error_kind": "capability_unsupported",
                    "message": (
                        f"the {profile.label!r} profile cannot read a network "
                        "diagram — this tool needs an Anthropic-wire profile. "
                        "Switch to one, or add the components by hand."
                    ),
                },
            )
        if not profile.vision:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_kind": "capability_unsupported",
                    "message": (
                        f"the {profile.label!r} profile does not support image "
                        "input (vision is disabled for this profile), so it "
                        "cannot read a network diagram — switch to a "
                        "vision-capable profile."
                    ),
                },
            )

    if client is None:
        from services import chat_service
        client, err = chat_service._anthropic_client_for_profile(profile)
        if client is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_kind": err or "internal_error",
                    "message": (
                        "vision sub-call could not build a client for the "
                        "profile this chat is running on"
                    ),
                },
            )

    VISION_INSTRUCTION = (
        "You are looking at a hand-drawn or printed diagram of an electric "
        "power network. Identify every BUS (junction / node) and every LINE "
        "(branch / transmission segment) drawn in the image, with rough "
        "coordinates in image-pixel space (top-left origin). Return ONLY a "
        "single JSON object — no prose, no markdown fences — with this "
        "exact shape:\n"
        "{\n"
        '  "buses":  [{"name": "B1", "px": 120, "py": 50, "v_nom": 380}, ...]\n'
        '  "lines":  [{"name": "L1", "bus0": "B1", "bus1": "B2"}, ...]\n'
        "}\n"
        "Use simple short names (B1, B2, L1, L2, ...). `v_nom` is optional. "
        "Skip generators / loads — only buses + lines for this pass."
    )

    user_content = list(blocks)
    user_content.append({"type": "text", "text": VISION_INSTRUCTION})

    async def _ask_vision() -> dict:
        from services.chat_service import DEFAULT_MODEL  # noqa: PLC0415

        # C-3 — the turn profile's own model, so the sub-call bills what the
        # user selected. DEFAULT_MODEL only when there is no bound profile.
        vision_model = profile.model if profile is not None else DEFAULT_MODEL

        with client.messages.stream(
            model=vision_model,
            max_tokens=2048,
            system="You return ONLY raw JSON when asked.",
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            final = stream.get_final_message()
        # Concatenate text blocks from the response.
        text_out = ""
        for block in (final.content or []):
            if getattr(block, "type", None) == "text":
                text_out += getattr(block, "text", "")
        return {"raw": text_out}

    try:
        result = asyncio.run(asyncio.wait_for(_ask_vision(), timeout=30.0))
    except asyncio.TimeoutError as exc:  # noqa: PERF203
        raise HTTPException(
            status_code=504,
            detail={
                "error_kind": "image_analysis_timeout",
                "message": "vision sub-call did not return within 30 s",
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={
                "error_kind": "vision_call_failed",
                "message": _redact_secrets_in_str(
                    f"vision sub-call raised {type(exc).__name__}: {exc}"
                ),
            },
        ) from exc

    raw_text = (result.get("raw") or "").strip()
    # Strip markdown fences if the model returned them despite instructions.
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)
    try:
        parsed = _json.loads(raw_text)
    except _json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_kind": "vision_invalid_json",
                "message": f"vision returned non-JSON content: {exc}",
                "snippet": raw_text[:200],
            },
        ) from exc

    buses_in = parsed.get("buses") or []
    lines_in = parsed.get("lines") or []

    # Materialise components inside the PyPSA lock + audit log. Use
    # the existing create_component helper so the lineage / undo /
    # change_log machinery fires uniformly. Track created names so the
    # response can report them.
    created_buses: list[str] = []
    created_lines: list[str] = []
    n = PyPSAService.get_network()
    existing_bus_names = set(n.buses.index)
    existing_line_names = set(n.lines.index)
    with PyPSAService.get_lock():
        for b in buses_in:
            try:
                bname = str(b.get("name") or "").strip()
                if not bname or bname in existing_bus_names or bname.startswith("ic:"):
                    continue  # `ic:` is reserved (P2 WP2.2-0)
                px = float(b.get("px") or 0.0)
                py = float(b.get("py") or 0.0)
                gx = (px - origin_x) * scale_x
                gy = (origin_y - py) * scale_y
                v_nom = b.get("v_nom")
                kwargs: dict[str, Any] = {"x": gx, "y": gy}
                if v_nom is not None:
                    kwargs["v_nom"] = float(v_nom)
                n.add("Bus", bname, **kwargs)
                created_buses.append(bname)
                existing_bus_names.add(bname)
            except Exception as exc:  # noqa: BLE001
                logger.warning("vision: bus add failed for %r: %s", b, exc)
        for ln in lines_in:
            try:
                lname = str(ln.get("name") or "").strip()
                bus0 = str(ln.get("bus0") or "").strip()
                bus1 = str(ln.get("bus1") or "").strip()
                if not lname or lname in existing_line_names:
                    continue
                if bus0 not in n.buses.index or bus1 not in n.buses.index:
                    logger.warning(
                        "vision: line %r refs unknown bus(es) %s/%s",
                        lname, bus0, bus1,
                    )
                    continue
                n.add("Line", lname, bus0=bus0, bus1=bus1)
                created_lines.append(lname)
                existing_line_names.add(lname)
            except Exception as exc:  # noqa: BLE001
                logger.warning("vision: line add failed for %r: %s", ln, exc)

    from services import change_log_service
    change_log_service.log(
        "create", "Network", "reconstruct_from_image",
        f"Vision-reconstructed {len(created_buses)} buses, {len(created_lines)} "
        f"lines from upload {file_id!r}",
    )

    return {
        "ok": True,
        "buses_created": created_buses,
        "lines_created": created_lines,
        "buses_reported": len(buses_in),
        "lines_reported": len(lines_in),
        "buses_skipped": max(0, len(buses_in) - len(created_buses)),
        "lines_skipped": max(0, len(lines_in) - len(created_lines)),
    }


# ── Asset results (3) ───────────────────────────────────────────────────────
#
# Task 14: the chatbot surface for the per-asset Results tab (services/
# asset_results/{service,export}.py). get_asset_results defaults to
# STATISTICS, not raw arrays — an hourly year x ten metrics is ~87 000
# numbers, which would consume a large share of the context window on a
# single question. resolution="raw" returns real arrays, capped at
# max_rows, with truncated/n_total set and a note pointing at
# export_asset_results for the complete set. ui_open_asset_detail is a pure
# ui_event marker (no backend mutation), same pattern as ui_select_component
# / ui_open_panel above.


def _series_stats(index: list[str], values: list, *, points: int = 48) -> dict:
    """
    Compress a series to something worth putting in a context window.

    An hourly year is 8 760 numbers per metric; ten metrics is ~87 000. The
    agent can answer almost every real question — peak, mean, total, when it
    peaks, how often it sits at zero — from these ~12 fields plus a coarse
    sparkline, and it is told to reach for the export tool when it cannot.

    `zero_count` is an unweighted count of SNAPSHOTS, deliberately not named
    `zero_hours` — the response's own `scalars["zero_hours"]` (from the
    `zero_hours` metric in the registry) is the snapshot-WEIGHTED hour count.
    Two identically-named fields with different values in the same payload
    would be indistinguishable to the agent; different names make the
    difference legible instead of silent.
    """
    finite = [(i, float(v)) for i, v in enumerate(values)
              if v is not None and math.isfinite(float(v))]
    if not finite:
        return {"min": None, "max": None, "mean": None, "sum": None,
                "p50": None, "p95": None, "peak_at": None,
                "zero_count": 0, "sparkline": []}
    vals = [v for _, v in finite]
    ordered = sorted(vals)
    peak_i = max(finite, key=lambda t: t[1])[0]

    def pct(q: float) -> float:
        k = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[k]

    step = max(1, len(vals) // points)
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(vals) / len(vals),
        "sum": sum(vals),
        "p50": pct(0.5),
        "p95": pct(0.95),
        "peak_at": index[peak_i] if peak_i < len(index) else None,
        "zero_count": sum(1 for v in vals if abs(v) < 1e-9),
        "sparkline": [round(v, 4) for v in vals[::step]][:points],
    }


def get_asset_results(
    component_class: str,
    name: str,
    *,
    category: str = "summary",
    metrics: list | None = None,
    source: str = "lopf",
    from_iso: str | None = None,
    to_iso: str | None = None,
    period: str | None = None,
    resolution: str = "stats",
    max_rows: int = 2000,
) -> dict:
    """Per-asset results for the agent. Statistics by default; raw on request."""
    from services.asset_results import service as svc
    from services.asset_results.registry import metrics_for

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise ValueError(f"No {component_class} named '{name}'")

    # No explicit metric list means "everything in this category" — the agent
    # asks a question, it does not know the registry's metric ids up front.
    requested = [str(m) for m in (metrics or [])]
    if not requested:
        requested = [m.id for m in metrics_for(component_class, category)]

    resp = svc.build_response(
        n, component_class, name, category=category, metric_ids=requested,
        source=source, from_iso=from_iso, to_iso=to_iso, period=period,
        mode="chronological",
    )

    unavailable = [
        {"id": m["id"], "label": m["label"], "status": m["status"],
         "reason": m.get("reason", "")}
        for m in resp["metrics"] if m["status"] != "ok"
    ]
    out: dict = {
        "asset": resp["asset"],
        "category": category,
        "categories": [{"id": c["id"], "status": c["status"],
                        "reason": c.get("reason", "")} for c in resp["categories"]],
        "scalars": resp["scalars"],
        "unavailable": unavailable,
        "n_snapshots": len(resp["index"]),
    }
    # The default category is `summary`, whose own metrics are just identity
    # and parameters. The headline KPIs are what makes "summarise Gas 1"
    # answerable in one call instead of seven — carry them through, each
    # tagged with the tab it came from so the agent can cite a source.
    if resp.get("headline"):
        out["headline"] = [
            {"id": h["id"], "label": h["label"], "unit": h.get("unit", ""),
             "category": h["category"], "status": h["status"],
             **({"value": h["value"]} if "value" in h else {}),
             **({"reason": h["reason"]} if h.get("reason") else {})}
            for h in resp["headline"]
        ]
    if resolution == "raw":
        out["resolution"] = "raw"
        out["index"] = resp["index"][:max_rows]
        out["series"] = {k: v[:max_rows] for k, v in resp["series"].items()}
        out["truncated"] = len(resp["index"]) > max_rows
        out["n_total"] = len(resp["index"])
        out["note"] = (
            f"Truncated to the first {max_rows} rows. Call export_asset_results for the "
            "complete set as a workbook."
            if out["truncated"] else "Complete — no truncation."
        )
    else:
        out["resolution"] = "stats"
        out["series_stats"] = {
            k: _series_stats(resp["index"], v) for k, v in resp["series"].items()
        }
    return out


def ui_open_asset_detail(
    component_class: str,
    name: str,
    *,
    category: str | None = None,
    metrics: list | None = None,
    mode: str | None = None,
    chart: bool | None = None,
) -> dict:
    """Open the Asset Detail tab pre-configured. No backend mutation."""
    event: dict[str, Any] = {
        "_ui_event": True, "kind": "open_asset_detail",
        "component_class": component_class, "name": name,
    }
    if category:
        event["category"] = category
    if metrics:
        event["metrics"] = [str(m) for m in metrics]
    if mode:
        event["mode"] = mode
    if chart is not None:
        event["chart"] = bool(chart)
    return event


def export_asset_results(
    component_class: str,
    name: str,
    *,
    scope: str = "view",
    category: str = "summary",
    metrics: list | None = None,
    filename: str | None = None,
    source: str = "lopf",
    mode: str = "chronological",
) -> dict:
    """Write one asset's results to an xlsx workbook in the project's uploads/."""
    from services.asset_results import export as xls
    from services.asset_results import service as svc

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise ValueError(f"No {component_class} named '{name}'")

    blob = xls.build_workbook(
        n, component_class, name, scope=scope, category=category,
        metric_ids=[str(m) for m in (metrics or [])], source=source,
        from_iso=None, to_iso=None, period=None, mode=mode,
        project=PyPSAService.get_loaded_project(),
    )
    # Same 25 MB pre-check export_to_excel applies before handing bytes to
    # the shared writer — see the note on `_save_agent_export` below for why
    # this isn't inside that helper itself.
    if len(blob) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "error_kind": "file_too_large",
                "message": (
                    f"serialised workbook exceeds the 25 MB upload cap "
                    f"({len(blob) // (1024 * 1024)} MB)"
                ),
            },
        )
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    fname = filename or f"{safe_name}_{category}.xlsx"
    # No `_write_agent_export` helper exists anywhere in this codebase — only
    # a stale docstring reference in filename_service.py. The real shared
    # writer (already used by export_to_excel and four sibling export tools)
    # is `_save_agent_export(data, filename, mime) -> dict`, which returns
    # `size`, not `bytes`. Reuse it as-is — restructuring a helper five
    # working call sites depend on is out of scope here — and alias the
    # field this tool's own schema promises.
    meta = _save_agent_export(
        blob, fname,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    meta["bytes"] = meta["size"]
    return meta


# ── LLM provider switching (1) — Task 10 ────────────────────────────────────


def set_active_profile(profile_id: str) -> dict:
    """
    Switch the assistant to an ALREADY-CONFIGURED LLM profile.

    SCOPE BOUNDARY, deliberate and load-bearing. This tool only selects among
    profiles a super-admin has already created in Settings. It never creates
    a profile, never edits one, and never accepts an API key — so no key
    material ever transits the chat channel, where it would land in the
    model's context, in `session.messages`, and (via the assistant's own
    reply) potentially in `chat.jsonl`. Creation and key entry stay on the
    super-admin-gated HTTP surface.

    WHY THE CHANGE IS DEFERRED TO A NEW CHAT. A session is bound to the
    profile it resolved at creation (`ChatSession.profile_id` / `bound_wire`),
    because its message history is stored in one provider's block shapes;
    replaying thinking or image blocks to a different wire is a 400 at best
    and a silent capability loss at worst. So this writes the ACTIVE profile
    for the next session and says so, rather than mutating the running one.

    Returns ``{ok, active_profile_id, note}``. An unconfigured id raises a
    structured `HTTPException` (`error_kind='unknown_profile_id'`) which the
    harness surfaces as a `tool_error` frame — never an escaping exception.

    The message names LABELS only, never an identifier or a base_url:
    redaction is secrets-only by design and would not scrub either.
    """
    from services import llm_config

    # AUTHORIZATION — super-admin only, matching the HTTP surface.
    #
    # `POST /chat/settings/llm/active` is `_require_super_admin`-gated because
    # the active profile is INSTANCE-WIDE: it decides which provider every
    # organization's chat runs on, and whose API key pays for it. This tool
    # reaches the same store, so without this check an ordinary member could
    # flip it by asking the model and approving their own confirmation card.
    #
    # Confirmation-gating is NOT a substitute. It exists to stop the MODEL
    # taking a destructive action the user did not intend; it says nothing
    # about whether that user is entitled to the action, and the confirm
    # endpoint itself only validates a session-scoped token. Caught in review
    # after the first cut of this tool shipped with no role check at all.
    #
    # Local mode is unaffected: its single seeded identity is a super-admin.
    with _acting() as (_db, user):
        if not user.is_super_admin:
            raise HTTPException(
                status_code=403,
                detail={
                    "error_kind": "not_authorized",
                    "message": (
                        "Switching the model profile changes it for everyone "
                        "on this instance, so only a super-admin can do it. "
                        "Ask an administrator to change it in Settings."
                    ),
                },
            )

    profiles, _active = llm_config.load_profiles()
    known = {p.id: p for p in profiles}
    if profile_id not in known:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "unknown_profile_id",
                "message": (
                    f"no configured profile {profile_id!r}. Configured: "
                    + ", ".join(sorted(p.label for p in profiles))
                    + ". Add one in Settings first — this tool only switches "
                    "between profiles that already exist."
                ),
            },
        )
    llm_config.set_active(profile_id)
    return {
        "ok": True,
        "active_profile_id": profile_id,
        "note": (
            f"{known[profile_id].label} is now the active profile. This chat "
            "stays on the model it started with — start a new chat to use it."
        ),
    }


# ── Chatbot uploads — produce (4) ───────────────────────────────────────────
#
# Agent-driven file exports. Each writes the bytes into the active project's
# uploads/ dir with `kind="agent_export"` so the UI distinguishes them with
# a download button + accent colour. Filenames are sanitised
# (`safe_upload_filename`) and the 25 MB cap is enforced by the underlying
# `add_upload`.


# A spreadsheet treats a cell that STARTS with one of these as a formula, and
# the cells these two tools write come from the model — which copies component
# names, uploaded files and imported networks into them. So a bus named
# `=HYPERLINK("https://…","open")` became a live link in a file the user was
# invited to download and open. (Register: lower-confidence notes, 2026-09-28.)
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _inert_csv_cell(value):
    """A CSV cell a spreadsheet will show as text, never evaluate.

    OWASP's mitigation: prefix a leading apostrophe. Applied to strings only,
    and not to a string that IS a number (`"-5"`, `"+3.2"`): that is data a
    spreadsheet reads as the number it is, and an apostrophe would turn it
    into text.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGERS):
        try:
            float(value)
        except ValueError:
            return "'" + value
    return value


def export_to_excel(sheets: dict, filename: str) -> dict:
    """
    Materialise a multi-sheet xlsx workbook from `sheets`.

    Args:
        sheets: ``{sheet_name: [[row1col1, row1col2, ...], [row2col1, ...]]}``
                The first row of each sheet is treated as headers.
        filename: target filename (sanitised; falls back to a synthetic
                  name on traversal attempts).

    Returns the same chip-meta shape as `_save_agent_export`.
    """
    import io as _io
    from openpyxl import Workbook
    wb = Workbook()
    # Remove the default sheet so we don't ship an empty "Sheet" leaf.
    default = wb.active
    if default is not None:
        wb.remove(default)
    for sheet_name, rows in (sheets or {}).items():
        ws = wb.create_sheet(title=str(sheet_name)[:31] or "Sheet")
        for row in (rows or []):
            ws.append(list(row))
            # openpyxl stores ANY string starting with "=" as a formula
            # (`data_type == "f"`), which Excel evaluates on open. A string
            # cell is shown as written, so force the type rather than edit
            # the text: in xlsx the cell type decides, not the characters.
            for cell in ws[ws.max_row]:
                if cell.data_type == "f":
                    cell.data_type = "s"
    buf = _io.BytesIO()
    wb.save(buf)
    payload = buf.getvalue()
    if len(payload) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "error_kind": "file_too_large",
                "message": (
                    f"serialised workbook exceeds the 25 MB upload cap "
                    f"({len(payload) // (1024*1024)} MB)"
                ),
            },
        )
    return _save_agent_export(
        payload, filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def export_to_csv(rows: list, columns: list, filename: str) -> dict:
    """
    Write a rectangular CSV. `columns` is the header; `rows` is a list of
    lists where each inner list matches `columns` length. RFC 4180 CRLF
    line endings.
    """
    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    if columns:
        w.writerow([_inert_csv_cell(c) for c in columns])
    for row in (rows or []):
        w.writerow([_inert_csv_cell(c) for c in row])
    payload = buf.getvalue().encode("utf-8")
    if len(payload) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "error_kind": "file_too_large",
                "message": (
                    f"serialised csv exceeds the 25 MB upload cap "
                    f"({len(payload) // (1024*1024)} MB)"
                ),
            },
        )
    return _save_agent_export(payload, filename, "text/csv")


def export_preview_png(filename: str, png_bytes_b64: str) -> dict:
    """
    Write a PNG generated by the agent (e.g. a topology preview from
    `reconstruct_network_from_image`). The bytes arrive base64-encoded so
    the JSON tool-call payload stays text.

    Validates the first bytes match PNG magic (89 50 4e 47) so a bogus
    payload doesn't masquerade as an image.
    """
    import base64
    try:
        payload = base64.b64decode(png_bytes_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "invalid_base64",
                "message": f"png_bytes_b64 is not valid base64: {exc}",
            },
        ) from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "invalid_png",
                "message": "decoded bytes do not start with PNG magic",
            },
        )
    if len(payload) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "error_kind": "file_too_large",
                "message": (
                    f"png exceeds the 25 MB upload cap "
                    f"({len(payload) // (1024*1024)} MB)"
                ),
            },
        )
    return _save_agent_export(payload, filename, "image/png")


def export_chat_summary(
    format: str = "md",
    since_turn: int | None = None,
    filename: str | None = None,
) -> dict:
    """
    Render the active project's chat history as a downloadable summary.

    `format`:
      * `"md"`  — Markdown (one section per turn).
      * `"txt"` — plain text.

    `since_turn`: drop turns before that index. Default is the full
    history.

    `filename`: override the auto-generated `chat_summary_<ts>.<ext>` name.
    """
    import time as _time
    fmt = format.lower()
    if fmt not in {"md", "txt"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "format_not_supported",
                "message": (
                    f"format {format!r} not supported yet; use 'md' or 'txt'"
                ),
            },
        )
    # Reuse the existing list_chat_history reader so a single source of
    # truth handles rotated chat.jsonl.1 and trailing-partial-line skips.
    turns = list_chat_history(None) or []
    if since_turn is not None:
        turns = turns[since_turn:]
    lines: list[str] = []
    if fmt == "md":
        lines.append("# Chat conversation summary\n")
        for i, t in enumerate(turns):
            user = (t.get("user") or "").strip()
            lines.append(f"\n## Turn {i + 1}")
            if user:
                lines.append(f"\n**User**: {user}\n")
            assistant_text = ""
            for b in (t.get("assistant") or []):
                if isinstance(b, dict) and b.get("type") == "text":
                    assistant_text += str(b.get("text", "")) + "\n"
                elif isinstance(b, dict) and b.get("type") == "tool_use":
                    assistant_text += f"\n_[tool: {b.get('name','?')}]_\n"
            if assistant_text.strip():
                lines.append(f"\n**Assistant**: {assistant_text.rstrip()}\n")
    else:  # txt
        for i, t in enumerate(turns):
            user = (t.get("user") or "").strip()
            lines.append(f"--- Turn {i + 1} ---")
            if user:
                lines.append(f"User: {user}")
            assistant_text = ""
            for b in (t.get("assistant") or []):
                if isinstance(b, dict) and b.get("type") == "text":
                    assistant_text += str(b.get("text", "")) + "\n"
            if assistant_text.strip():
                lines.append(f"Assistant: {assistant_text.rstrip()}")
            lines.append("")
    payload = "\n".join(lines).encode("utf-8")
    target = filename or f"chat_summary_{int(_time.time())}.{fmt}"
    return _save_agent_export(
        payload, target, "text/markdown" if fmt == "md" else "text/plain",
    )


def export_eh_report_docx(filename: str | None = None) -> dict:
    """
    The stored Energy Hub ``ReferenceDesignReport`` as a Word document in
    the active project's uploads/ dir (an ``agent_export`` chip).

    WP0 of the report-generation plan: no language model is involved —
    every cell is the assembler's own number, formatted so that a missing
    figure reads "not established" and never 0, and every report section is
    present even when the study did not establish it.
    """
    import time as _time

    from routers import results as results_router
    from services.reports.docx_writer import (
        DOCX_MIME,
        render_reference_design_docx,
    )
    from services.reports.figures import fmea_pareto_png
    from services.reports.evidence import fmea_top_modes

    name = _require_active_project()
    body = results_router.get_eh_reference_design()
    if getattr(body, "status_code", None) == 204:
        raise HTTPException(
            status_code=404,
            detail={
                "error_kind": "eh_report_not_found",
                "message": _ADEQUACY_NO_DATA_HINTS["eh_reference_design"],
            },
        )
    # The analyst's class-D rows are part of the deliverable; a project that
    # has no worksheet yet is the common case, not an error.
    try:
        worksheet = get_fmea_worksheet(name)
    except HTTPException:
        worksheet = None
    figures: dict[str, bytes] = {}
    fmea_section = (body.get("sections") or {}).get("fmea_top") or {}
    png = fmea_pareto_png(fmea_top_modes(fmea_section.get("payload")))
    if png:
        figures["fmea_top"] = png
    data = render_reference_design_docx(
        body, fmea_worksheet=worksheet, figures=figures)
    target = filename or f"eh_reference_design_{int(_time.time())}.docx"
    if not target.lower().endswith(".docx"):
        target += ".docx"
    return _save_agent_export(data, target, DOCX_MIME)


# ── Reports (WP6) — the generated study report ──────────────────────────────
#
# Thin wrappers over WP1/WP3/WP5's routes (`routers/reports.py`,
# `routers/report_jobs.py`), called in-process for the ACTIVE project through
# `_route(...)` exactly as `run_eh_study` wraps `post_eh_study`. Every refusal
# the routes raise passes through unchanged (`detail["error_kind"]`), so the
# manifest's `report_*` kinds are the tools' too. Not campaign-gated: a report
# solves nothing — it narrates what the studies established.
#
# The one thing added here is SHAPE: `get_report` must fit the chat harness's
# 4000-char result cap (`chat_service._truncate_result`), which a document
# with its tables inline never would, so tables collapse to their id, columns
# and row count (`get_report_table` pages the rows), figures to their id and
# caption, and a document whose PROSE alone would not fit degrades to a
# per-section outline with a hint to read one section at a time.

# Under `_truncate_result`'s 4000, measured the same way it measures
# (`len(json.dumps(result, default=str))`) on the very dict it measures.
REPORT_RESULT_BUDGET = 3900
_REPORT_HEADER_KEYS = ("report_id", "version", "title", "language", "created_at",
                       "mode", "profile_id", "model", "evidence_hash")
_PROSE_SOURCES = ("llm", "user_edit")


def _report_project():
    """The active project as the report routes take it (authorized, with its directory)."""
    return _authorized_project(_require_active_project())


def _newest_report_id(project) -> str:
    """The newest report of the project, or the 404 that names the remedy."""
    from services.reports import store

    metas = store.list_reports(project.directory)
    if not metas:
        raise HTTPException(
            status_code=404,
            detail={
                "error_kind": "report_not_found",
                "message": (
                    "This project has no study report yet — generate_report "
                    "writes one from the session's results."
                ),
            },
        )
    return metas[0].report_id


def _load_report_document(project, report_id: str, version: int | None) -> dict:
    """One version of a report as the GET route serves it (a plain dict)."""
    from routers.reports import get_report as _h
    return _route(_h, report_id, version, project=project)


def _json_len(payload: Any) -> int:
    return len(json.dumps(payload, default=str))


def _compact_block(block: dict, tables: dict, figures: dict) -> dict:
    """A table reference becomes its shape, a figure its caption; prose stays."""
    kind = block.get("type")
    if kind == "table_ref":
        table = tables.get(block.get("table_id")) or {}
        out = {
            "type": "table",
            "table_id": block.get("table_id"),
            "columns": list(table.get("columns") or []),
            "n_rows": len(table.get("rows") or []),
        }
        caption = block.get("caption") or table.get("caption")
        if caption:
            out["caption"] = caption
        return out
    if kind == "figure_ref":
        figure = figures.get(block.get("figure_id")) or {}
        out = {"type": "figure", "figure_id": block.get("figure_id")}
        caption = block.get("caption") or figure.get("caption")
        if caption:
            out["caption"] = caption
        return out
    return dict(block)


def _compact_section(section: dict, tables: dict, figures: dict) -> dict:
    """The section with its prose intact; an empty audit is left out."""
    out = {
        "section_id": section.get("section_id"),
        "heading": section.get("heading"),
        "source": section.get("source"),
        "status": section.get("status"),
        "blocks": [_compact_block(b, tables, figures) for b in section.get("blocks") or []],
    }
    if section.get("note"):
        out["note"] = section["note"]
    audit = section.get("audit") or {}
    if audit.get("unverified") or audit.get("verified"):
        # `unverified` intact — those are the numbers to check. `verified`
        # as the texts alone: the evidence path each matched is the viewer's
        # citation, and nothing in chat can dereference a JSON pointer.
        out["audit"] = {
            "unverified": list(audit.get("unverified") or []),
            "verified": [v.get("text") if isinstance(v, dict) else v
                         for v in audit.get("verified") or []],
        }
    return out


def _outline_section(section: dict) -> dict:
    """
    One row per section: what is there, not what it says. Empty counts are
    left out, and so is the heading — the id names the section and a
    per-section read carries the heading — because a dozen such rows must
    leave room for the sections that carry prose.
    """
    blocks = section.get("blocks") or []
    audit = section.get("audit") or {}
    out = {
        "section_id": section.get("section_id"),
        "source": section.get("source"),
        "status": section.get("status"),
    }
    counts = {
        "paragraphs": sum(1 for b in blocks if b.get("type") == "paragraph"),
        "bullets": sum(len(b.get("items") or []) for b in blocks if b.get("type") == "bullets"),
        "callouts": sum(1 for b in blocks if b.get("type") == "callout"),
        "tables": [b.get("table_id") for b in blocks if b.get("type") == "table_ref"],
        "figures": [b.get("figure_id") for b in blocks if b.get("type") == "figure_ref"],
        "unverified": list(audit.get("unverified") or []),
    }
    out.update({k: v for k, v in counts.items() if v})
    if section.get("note"):
        out["note"] = section["note"]
    return out


def generate_report(
    title: str | None = None,
    language: str | None = None,
    sections: list | None = None,
    instruction: str | None = None,
    template_file_id: str | None = None,
) -> dict:
    """
    Start the report job for the active project on the active LLM profile.

    The route collects the evidence and renders the figures in this call;
    the prose is written on a daemon thread. An empty `sections` list means
    the default target set, not a 422 — a model that passes `[]` means "all".
    `language` left None is the template's detected language when
    `template_file_id` names one (WP11), else "en" — the route's own rule.
    """
    from routers.report_jobs import GenerateReportBody, generate_report as _h

    project = _report_project()
    body = GenerateReportBody(
        title=title, language=language or None,
        sections=[str(s) for s in sections] if sections else None,
        instruction=instruction, template_file_id=template_file_id or None,
    )
    result = _route(_h, body, project=project)
    return {
        **result,
        "message": (
            "Report generation started — poll get_report_status until it is "
            "done, aborted or failed, then read the report with "
            f"get_report('{result.get('report_id')}')."
        ),
    }


def get_report_status() -> Any:
    """The job record of this session; `no_data` when nothing has run yet."""
    from routers.report_jobs import get_generate_status as _h

    project = _report_project()
    return _payload_or_no_data(
        "report_job", _route(_h, project=project),
        "no report generation has been run in this session — generate_report "
        "starts one",
    )


def abort_report_generation() -> dict:
    """Ask the running job to stop after its current section. Idempotent."""
    from routers.report_jobs import abort_generate as _h

    project = _report_project()
    return _route(_h, project=project)


def list_reports() -> list[dict]:
    from routers.reports import list_reports as _h

    project = _report_project()
    return _route(_h, project=project)


def get_report(
    report_id: str | None = None,
    version: int | None = None,
    section_id: str | None = None,
) -> dict:
    """
    One report version for the model, under the result cap by construction.

    Prose blocks (paragraphs, bullets, callouts, fields) are intact; tables
    are their shape, figures their caption; `audit` is the section's own.
    When even that does not fit, the whole document comes back as an outline
    and `section_id` reads one section in full.
    """
    project = _report_project()
    rid = report_id if report_id is not None else _newest_report_id(project)
    raw = _load_report_document(project, rid, version)
    tables = raw.get("tables") or {}
    figures = raw.get("figures") or {}
    sections = list(raw.get("sections") or [])
    if section_id is not None:
        sections = [s for s in sections if s.get("section_id") == section_id]
        if not sections:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_kind": "report_section_not_found",
                    "message": (
                        f"Report {rid} has no section {section_id!r}; it has: "
                        + ", ".join(s.get("section_id") for s in raw.get("sections") or [])
                    ),
                },
            )
    head = {k: raw.get(k) for k in _REPORT_HEADER_KEYS}
    full = {
        **head,
        "sections": [_compact_section(s, tables, figures) for s in sections],
        "message": (
            "Tables are summarised as {table_id, columns, n_rows}: "
            "get_report_table(report_id, table_id) pages the rows."
        ),
    }
    if section_id is not None or _json_len(full) <= REPORT_RESULT_BUDGET:
        return full
    # Too big with everything in full: every section becomes a row, then the
    # sections that carry PROSE are put back in full, in document order, as
    # long as the budget allows — the most of the report one result can
    # carry, and always under the cap. A row has no `blocks`.
    rows = [_outline_section(s) for s in sections]
    out = {
        **head,
        "outline": True,
        "sections": rows,
        "message": (
            "Not everything fits one result: sections with `blocks` are in "
            "full, the rest are rows. get_report(report_id, section_id=...) "
            "reads any one in full; get_report_table pages a table."
        ),
    }
    used = _json_len(out)
    for i, section in enumerate(sections):
        if section.get("source") not in _PROSE_SOURCES:
            continue
        whole = _compact_section(section, tables, figures)
        delta = _json_len(whole) - _json_len(rows[i])
        if used + delta <= REPORT_RESULT_BUDGET:
            out["sections"][i] = whole
            used += delta
    return out


def get_report_table(
    report_id: str,
    table_id: str,
    version: int | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> dict:
    """The rows of one table of a report version, in the shared page envelope."""
    project = _report_project()
    raw = _load_report_document(project, report_id, version)
    tables = raw.get("tables") or {}
    table = tables.get(table_id)
    if table is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_kind": "report_table_not_found",
                "message": (
                    f"Report {report_id} (version {raw.get('version')}) has no "
                    f"table {table_id!r}; it has: "
                    + (", ".join(sorted(tables)) or "none")
                ),
            },
        )
    page = _paginate(list(table.get("rows") or []), offset, limit)
    return {
        "table_id": table_id,
        "columns": list(table.get("columns") or []),
        "caption": table.get("caption"),
        "source_path": table.get("source_path"),
        **page,
    }


def regenerate_report_section(
    report_id: str,
    section_id: str,
    instruction: str | None = None,
    language: str | None = None,
) -> dict:
    """One section again, from the latest version, saved as the next one."""
    from routers.report_jobs import RegenerateSectionBody, regenerate_section as _h

    project = _report_project()
    body = RegenerateSectionBody(instruction=instruction, language=language)
    result = _route(_h, report_id, section_id, body, project=project)
    return {
        **result,
        "message": (
            "Section rewrite started — poll get_report_status; when it is done, "
            f"get_report('{report_id}') returns the new version and the "
            "earlier versions stay readable."
        ),
    }


def export_report_docx(
    report_id: str | None = None,
    version: int | None = None,
    filename: str | None = None,
) -> dict:
    """
    One report version as a Word document in the active project's uploads/
    dir. The route renders and saves it as an `agent_export`; this returns
    the chip in the same shape `_save_agent_export` gives the other exports.
    """
    from routers.reports import ExportReportBody, export_report as _h

    project = _report_project()
    rid = report_id if report_id is not None else _newest_report_id(project)
    meta = _route(_h, rid, ExportReportBody(version=version, filename=filename),
                  project=project)
    return {
        "file_id": meta["file_id"],
        "filename": meta["filename"],
        "mime": meta["mime"],
        "size": meta["size"],
        "kind": meta["kind"],
        "report_id": rid,
        "message": (
            f"Exported '{meta['filename']}' ({meta['size']} bytes). It's "
            "available as a downloadable file in the chat panel's file strip."
        ),
    }


def delete_report(report_id: str) -> dict:
    """Remove one report and every version of it (edit-lock checked by the route)."""
    from routers.reports import delete_report as _h

    project = _report_project()
    return _route(_h, report_id, project=project)


# ── WP11: user templates ────────────────────────────────────────────────────
#
# Thin wrappers over the template routes (`routers/reports.py`,
# `routers/report_jobs.py`), called in-process for the active project. A
# template is DATA: the mapping job shows its outline to the model inside the
# untrusted-data fence, and nothing found in a template is ever followed.


def list_report_templates() -> list[dict]:
    """The active project's uploads of kind `report_template`, newest first."""
    from services import upload_service

    name = _require_active_project()
    return [
        {
            "file_id": m.file_id,
            "filename": m.filename,
            "mime": m.mime,
            "kind": m.kind,
            "size_kb": round(m.size / 1024, 1),
            "uploaded_at": m.uploaded_at,
        }
        for m in upload_service.list_uploads(name, kind="report_template")
    ]


def set_report_template(report_id: str, file_id: str | None = None) -> dict:
    """Bind an upload as the report's template (null unbinds); the route's outline back."""
    from routers.reports import BindTemplateBody, bind_template as _h

    project = _report_project()
    result = _route(_h, report_id, BindTemplateBody(file_id=file_id or None), project=project)
    if result.get("template_file_id") is None:
        message = (f"Report {report_id} now uses the default document layout; "
                   "export_report_docx renders it with the bundled writer.")
    elif result.get("mode") == "tagged":
        message = (f"Report {report_id} is bound to a TAGGED template: its {{ }} tags "
                   "are filled on export_report_docx; no mapping plan is needed.")
    else:
        message = (f"Report {report_id} is bound to an UNTAGGED template "
                   f"(language {result.get('language') or 'undetected'}): "
                   "propose_report_mapping proposes how its headings map onto the "
                   "report, set_report_mapping edits the plan, export_report_docx "
                   "rebuilds the body into it.")
    return {**result, "message": message}


def get_report_template(report_id: str | None = None) -> dict:
    """The bound template's outline and stored plan (the newest report when omitted)."""
    from routers.reports import get_template as _h

    project = _report_project()
    rid = report_id if report_id is not None else _newest_report_id(project)
    return _route(_h, rid, project=project)


def propose_report_mapping(report_id: str, language: str | None = None) -> dict:
    """Start the mapping job for the report's untagged template (same slot as generate)."""
    from routers.report_jobs import ProposeMappingBody, propose_template_plan as _h

    project = _report_project()
    result = _route(_h, report_id, ProposeMappingBody(language=language or None),
                    project=project)
    return {
        **result,
        "message": (
            "Mapping proposal started — poll get_report_status until it is done "
            f"(mode 'mapping'), then read the plan with get_report_template('{report_id}')."
        ),
    }


def set_report_mapping(report_id: str, plan: dict, strict: bool = False) -> dict:
    """Store a (user- or model-edited) mapping plan; the sanitised plan back."""
    from routers.reports import put_template_plan as _h

    project = _report_project()
    body = dict(plan) if isinstance(plan, dict) else plan
    if isinstance(body, dict) and strict:
        body["strict"] = True
    return _route(_h, report_id, body, project=project)


# ── WP13: the round trip ────────────────────────────────────────────────────
#
# Thin wrappers over the round-trip routes (`routers/reports.py`), called
# in-process for the active project. An edited copy is DATA: its text becomes
# the report's `user_edit` sections and its comments become pending
# instructions the user chooses to apply; nothing found in it is followed by
# the assistant.


def list_report_roundtrips() -> list[dict]:
    """The active project's uploads of kind `report_roundtrip`, newest first."""
    from services import upload_service

    name = _require_active_project()
    return [
        {
            "file_id": m.file_id,
            "filename": m.filename,
            "mime": m.mime,
            "kind": m.kind,
            "size_kb": round(m.size / 1024, 1),
            "uploaded_at": m.uploaded_at,
        }
        for m in upload_service.list_uploads(name, kind="report_roundtrip")
    ]


def import_edited_report(report_id: str, file_id: str, bind_as_template: bool = True) -> dict:
    """Merge an edited Word copy back as the report's next version; the route's answer plus a summary."""
    from routers.reports import RoundTripBody, roundtrip_report as _h

    project = _report_project()
    result = _route(_h, report_id, RoundTripBody(file_id=file_id, bind_as_template=bool(bind_as_template)),
                    project=project)
    rt = result.get("result") or {}
    sections = rt.get("sections") or []
    changed = [s.get("section_id") for s in sections if s.get("changed") and s.get("section_id")]
    commented = [s.get("section_id") for s in sections if s.get("comments") and s.get("section_id")]
    unmatched = rt.get("unmatched") or []
    parts = [f"Report {report_id} is now version {result.get('version')}: "
             f"{len(changed)} section(s) edited by the user ({', '.join(changed) or 'none'}), "
             f"{rt.get('accepted_tracked_changes', 0)} tracked change(s) accepted."]
    if commented:
        parts.append(f"Comments became pending instructions on: {', '.join(commented)} — "
                     "regenerate_report_section(report_id, section_id) with no instruction "
                     "applies each.")
    if unmatched:
        parts.append(f"{len(unmatched)} piece(s) of content could not be placed in any "
                     "section and were NOT merged; relay them to the user.")
    if result.get("template_file_id") == file_id:
        parts.append("The edited file is now the report's template, so its styling "
                     "survives the next export.")
    return {**result, "changed": changed, "commented": commented, "message": " ".join(parts)}


def diff_report_versions(report_id: str, a: int, b: int) -> dict:
    """Per-section change between two versions, with the changed/added/removed ids summarised."""
    from routers.reports import diff_report_versions as _h

    project = _report_project()
    out = _route(_h, report_id, int(a), int(b), project=project)
    rows = out.get("sections") or []
    return {
        **out,
        "changed": [r["section_id"] for r in rows if r.get("change") == "changed"],
        "added": [r["section_id"] for r in rows if r.get("change") == "added"],
        "removed": [r["section_id"] for r in rows if r.get("change") == "removed"],
    }


def export_report_pdf(
    report_id: str | None = None,
    version: int | None = None,
    filename: str | None = None,
) -> dict:
    """
    One report version as a PDF in the active project's uploads/ dir, when
    this host has LibreOffice (501 `pdf_not_available` otherwise; the .docx
    export always works).
    """
    from routers.reports import ExportReportBody, export_report as _h

    project = _report_project()
    rid = report_id if report_id is not None else _newest_report_id(project)
    meta = _route(_h, rid, ExportReportBody(version=version, filename=filename, format="pdf"),
                  project=project)
    return {
        "file_id": meta["file_id"],
        "filename": meta["filename"],
        "mime": meta["mime"],
        "size": meta["size"],
        "kind": meta["kind"],
        "report_id": rid,
        "message": (
            f"Exported '{meta['filename']}' ({meta['size']} bytes) as PDF. It's "
            "available as a downloadable file in the chat panel's file strip."
        ),
    }


def build_study_report(project: str | None = None) -> dict:
    """
    Assemble the client-facing reliability write-up from everything this
    session established — and everything it did not.

    `project` names the asset-health ledger to fold in. Omitted, or naming a
    project that is not in the foreground, means the provenance gap is simply
    not reported rather than reported against somebody else's network — the
    same guard `get_asset_health` applies, for the same reason.
    """
    from services.adequacy import campaign as _campaign
    from services.adequacy.asset_health import provenance_report
    from services.adequacy.study_report import build_study_report as _build

    n = PyPSAService.get_network()

    health = None
    if project and PyPSAService.get_loaded_project() == project:
        ledger = get_asset_health(project)
        health = ledger.get("provenance")
    elif project is None and PyPSAService.get_loaded_project():
        # No project named, but one IS bound: use it. A report that silently
        # skipped the provenance gap because the caller omitted an argument
        # would be missing the finding that undermines every number in it.
        active = PyPSAService.get_loaded_project()
        try:
            ledger = get_asset_health(active)
            health = provenance_report(n, ledger.get("entries", []))
        except Exception:  # noqa: BLE001 — no ledger is not a report failure
            health = None

    status = _campaign.status()
    return _build(n, get_adequacy_results,
                  campaign=status if status.get("active") else None,
                  health=health)


# ── Asset health / outage-rate provenance (2) ───────────────────────────────
#
# `resolve_outage_params` already resolves a rate as `asset`,
# `carrier_default` or `missing`. Two of those explain themselves — the
# carrier library ships its own citation, and `missing` is the absence of a
# claim. `asset` does not, and it is the one that matters: a condition-based
# rate IS the claim behind condition-based reliability, and the model records
# "a drone survey found conductor damage" and "someone typed it" identically.
#
# These two tools are the interface a perception feed lands through — an
# inspection programme, a DGA monitor, a vegetation model — BEFORE any such
# model exists. The ledger never sets a rate: values go on the components
# through the ordinary edit paths (bulk_update_components), and
# `provenance_report` reconciles the two. A ledger nothing can contradict
# would be decoration.

def get_asset_health(name: str) -> dict:
    """
    The provenance ledger for `name`, reconciled against the LIVE network.

    The reconciliation is the point — which asset-level rates have no source,
    which have drifted away from what was measured — but it is only meaningful
    when `name` is the project currently loaded in the foreground. When it is
    not, the ledger is still served and `provenance` is null with a note
    saying why: silently reporting drift computed against a DIFFERENT
    network's rates is worse than reporting none.
    """
    from routers.adequacy_worksheet import get_asset_health as _h
    from services.adequacy.asset_health import provenance_report

    ledger = _h(project=_authorized_project(name))
    active = PyPSAService.get_loaded_project()
    if active != name:
        return {
            **ledger,
            "provenance": None,
            "note": (
                f"'{name}' is not the project in the foreground "
                f"({active or 'none'}), so its ledger cannot be reconciled "
                f"against a network. Activate it first, or read the ledger "
                f"alone."
            ),
        }
    return {
        **ledger,
        "provenance": provenance_report(
            PyPSAService.get_network(), ledger["entries"]),
    }


def record_asset_health(name: str, entries: list) -> dict:
    """
    Replace the provenance ledger for `name`.

    Whole-ledger replacement, like the worksheet: the payload is small, and a
    merge would need a delete verb nobody asked for. Validation runs before
    the write, so a rejected batch leaves the previous ledger byte-identical.

    This records where numbers came from; it does NOT apply them. Setting the
    rates is `bulk_update_components` on `outage_rate_value` / `mttr_hours`,
    and the split is deliberate — the ledger's job is to be contradictable by
    the network, which it cannot be if it writes the network.
    """
    from routers.adequacy_worksheet import AssetHealthPut, put_asset_health as _h

    # Through `_route`, not bare. The handler now declares `db` / `user` for
    # its foreign-lock check, and `_route`'s contract is to "resolve whatever
    # the target declares" — called bare, those two arrive as raw `Depends`
    # sentinels and die inside the lock lookup. Routing it is also what makes
    # the new check real on THIS path: a non-holder editing the provenance
    # ledger through chat is refused exactly as they are over HTTP.
    return _route(_h, AssetHealthPut(entries=list(entries or [])),
                  project=_authorized_project(name))


# ── Explanation / synthesis (1) ─────────────────────────────────────────────
#
# The first real member of the composite family the DISPATCHERS block below
# documents as removed-because-never-implemented. It fuses results IN PROCESS
# rather than making the agent chain four reads and reconcile them from
# 4000-char truncations.
#
# What it adds over `get_asset_results`, which already returns this asset's
# cross-tab KPIs:
#
#   * THE BOUND. In a capacity-expansion LP, "why is it this big" is almost
#     always answered by WHICH CONSTRAINT BOUND IT, and no per-asset metric
#     carries p_nom_max / p_nom_extendable. An asset sitting on its ceiling
#     was sized by that ceiling, not by its economics, and an explanation
#     that talks about capture prices instead is confidently wrong.
#   * THE SYSTEM SIGNALS that made it attractive HERE: the CO2 shadow price
#     it is priced against, the marginal price at its bus, and whether the
#     lines out of that bus are congested.
#   * THE EQUILIBRIUM FRAMING. An extendable asset at an interior optimum
#     earns ≈ zero net profit BY CONSTRUCTION — the LP builds until the
#     marginal MW breaks even. Without that note the agent reads a near-zero
#     net_profit_eur as a defect and invents a cause.
#
# It returns EVIDENCE and one structural classification, never a narrative
# verdict: the model writes the prose, and can only write it from numbers
# that are in the payload.

# Which bus columns carry an asset's electrical location, per class.
_INVESTMENT_BUS_COLS: dict[str, tuple[str, ...]] = {
    "Generator": ("bus",),
    "StorageUnit": ("bus",),
    "Store": ("bus",),
    "Link": ("bus0", "bus1"),
    "Line": ("bus0", "bus1"),
    "Transformer": ("bus0", "bus1"),
}

# One sentence per structural outcome — the LP fact, not advice.
_BINDING_EXPLANATIONS: dict[str, str] = {
    "not_solved": (
        "the network has no fresh dispatch, so there is no sizing decision to "
        "explain — every capacity below is an input or a stale leftover"
    ),
    "not_extendable": (
        "the LP could not size this asset at all: its capacity is an INPUT, "
        "not a result. Set p_nom_extendable (or the class's equivalent) to "
        "let the optimisation choose it"
    ),
    "at_upper_bound": (
        "the LP took every MW the upper bound allowed. The BOUND set this "
        "size, not the economics — raise it to learn what the economics would "
        "build"
    ),
    "not_built": (
        "the LP chose to build none of it: at these costs it did not compete "
        "at the margin against everything else on the system. Nothing blocked "
        "it — it was simply not worth building"
    ),
    "at_lower_bound": (
        "the LP built the minimum it was FORCED to and no more. The asset was "
        "not competitive at the margin; a non-zero floor is holding it up, so "
        "this capacity is a constraint's doing, not the economics'"
    ),
    "interior": (
        "the LP stopped between the bounds, so this size IS the economic "
        "answer: the marginal MW broke even against everything else on the "
        "system"
    ),
}


def _finite(value: Any) -> float | None:
    """float(value) or None for anything non-finite, missing or unparseable."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _is_at(value: float | None, bound: float | None) -> bool:
    """
    Is an optimised capacity sitting ON a bound?

    Relative, because an LP lands on a bound within solver tolerance and an
    exact `==` reports "interior" for a plainly saturated asset — the single
    wrong answer this whole tool exists to avoid.
    """
    if value is None or bound is None:
        return False
    return abs(value - bound) <= max(abs(bound), abs(value), 1.0) * 1e-6


def _sizing(row: Any, nom_col: str, *, solved: bool) -> dict:
    """Classify the sizing decision from the asset's static row."""
    existing = _finite(row.get(nom_col))
    optimised = _finite(row.get(f"{nom_col}_opt")) if solved else None
    lower = _finite(row.get(f"{nom_col}_min"))
    upper = _finite(row.get(f"{nom_col}_max"))   # None == unbounded (inf)
    extendable = bool(row.get(f"{nom_col}_extendable", False))

    if not solved:
        binding = "not_solved"
    elif not extendable:
        binding = "not_extendable"
    elif _is_at(optimised, upper):
        binding = "at_upper_bound"
    elif _is_at(optimised, lower):
        # A floor of zero is not a floor. Reporting "the minimum it was forced
        # to" for an asset nobody forced anywhere reads as if a constraint
        # explained the zero, when the honest answer is that it lost on cost.
        binding = "at_lower_bound" if (lower or 0.0) > 0 else "not_built"
    else:
        binding = "interior"

    added = None if (optimised is None or existing is None) else optimised - existing
    headroom = None if (optimised is None or upper is None) else upper - optimised
    return {
        "capacity_column": nom_col,
        "extendable": extendable,
        "existing": existing,
        "optimised": optimised,
        "added": added,
        "lower_bound": lower,
        "upper_bound": upper,          # null = unbounded (p_nom_max = inf)
        "headroom": headroom,
        "binding_constraint": binding,
        "explanation": _BINDING_EXPLANATIONS[binding],
    }


def _bus_price_signals(n: Any, buses: list[str]) -> dict:
    """Mean / min / max / load-weighted marginal price at each of the asset's buses."""
    from services.asset_results import service as svc

    wanted = ["bus_price_mean", "bus_price_min", "bus_price_max",
              "bus_load_weighted_price"]
    out: dict[str, Any] = {}
    for bus in buses:
        if bus not in n.buses.index:
            continue
        try:
            resp = svc.build_response(
                n, "Bus", bus, category="prices", metric_ids=wanted,
                source="lopf", from_iso=None, to_iso=None, period=None,
                mode="chronological",
            )
        except Exception:  # noqa: BLE001 — a missing signal is not a failure
            continue
        scalars = {k: v for k, v in resp.get("scalars", {}).items() if k in wanted}
        if scalars:
            out[bus] = scalars
    return out


def _congestion_at(n: Any, buses: list[str]) -> dict:
    """
    The binding LINES touching the asset's buses, WITH why the list may be
    empty.

    An empty list has four very different causes — no lines there at all, no
    duals captured on this solve, lines that never bind, or an asset that
    connects through links and transformers, which `compute_line_duals` does
    not cover (it walks `n.lines`). Returning the bare list makes all four read
    as "uncongested", the one reading that can be flatly wrong, so the reason
    travels with the data instead of being inferred from its absence.
    """
    at_bus = {
        str(name) for name, line in n.lines.iterrows()
        if str(line.get("bus0")) in buses or str(line.get("bus1")) in buses
    }
    if not at_bus:
        # Checked FIRST: on a network with no lines, compute_line_duals says
        # "No LP duals captured — re-run the solve", which sends the agent
        # (and the user) after a solve that would change nothing.
        return {"lines": [], "note": (
            "no line connects to this asset's buses, so line congestion does "
            "not apply here — links and transformers are out of scope either "
            "way"
        )}

    payload = get_results("line_duals")
    if not isinstance(payload, dict):
        return {"lines": [], "note": "line duals unavailable"}
    if payload.get("status") == "no_data":
        return {"lines": [], "note": payload.get("message")}
    if payload.get("note"):
        # compute_line_duals' own sentence — "No LP duals captured…". Empty
        # here means UNKNOWN, not uncongested.
        return {"lines": [], "note": str(payload["note"])}

    lines = [
        {k: r.get(k) for k in ("name", "binding_hours",
                               "max_mu_eur_per_MWh", "congestion_rent_eur")}
        for r in payload.get("rows", [])
        if r.get("name") in at_bus and (r.get("binding_hours") or 0) > 0
    ]
    note = None if lines else (
        "no line at this asset's buses binds in any hour — but this covers "
        "n.lines only, so a link- or transformer-connected corridor is not "
        "evidence either way"
    )
    return {"lines": lines, "note": note}


def _co2_signals() -> list[dict]:
    """Active CO2 caps with their shadow prices — the system-wide clean premium."""
    payload = get_results("emissions")
    if not isinstance(payload, dict) or payload.get("status") == "no_data":
        return []
    return [
        {k: cap.get(k) for k in ("name", "scope", "investment_period",
                                 "binding", "shadow_price_eur_per_tCO2",
                                 "slack_tCO2")}
        for cap in payload.get("caps", []) if cap.get("active")
    ]


def _reading_notes(sizing: dict, co2: list[dict], buses: list[str]) -> list[str]:
    """The framing that keeps the narration honest. Order is deliberate."""
    notes = [
        "This payload is EVIDENCE, not a verdict. Narrate only numbers that "
        "appear in it, and name the field you used.",
    ]
    binding = sizing["binding_constraint"]
    if binding == "interior":
        notes.append(
            "Zero-profit equilibrium: an extendable asset at an interior "
            "optimum earns approximately zero net profit BY CONSTRUCTION — "
            "the LP builds until the marginal MW breaks even. A near-zero "
            "net_profit_eur here is the expected result, not a fault."
        )
    elif binding == "at_upper_bound":
        notes.append(
            "The size is a bound, not an optimum: do not narrate capture "
            "price or profitability as the reason it is this big."
        )
    elif binding == "not_built":
        notes.append(
            "Nothing was built, so revenue / capture-price KPIs below are "
            "zero or absent BY CONSTRUCTION. The question to answer is what "
            "it lost to: compare its capital_cost and marginal_cost against "
            "the bus price and against what the LP built instead."
        )
    elif binding == "not_extendable":
        notes.append(
            "Every capacity number below is an input the user typed. Nothing "
            "here explains a build decision, because none was made."
        )
    binding_caps = [c for c in co2 if c.get("binding")]
    if binding_caps:
        notes.append(
            "A CO2 cap binds. Its shadow price is part of this asset's "
            "competitiveness and vanishes if the cap is relaxed — say so "
            "rather than presenting the economics as cap-independent."
        )
    if len(buses) > 1:
        notes.append(
            "This asset spans more than one bus; the price signals are "
            "reported per bus and can disagree across a congested corridor."
        )
    notes.append(
        "Read system_signals.congestion.note before concluding anything from "
        "an empty `lines` list: it says whether nothing binds, the duals were "
        "never captured, or the corridor is simply out of scope."
    )
    return notes


def explain_investment(component_class: str, name: str) -> dict:
    """
    Assemble the evidence behind one sizing decision: what the LP built, WHICH
    CONSTRAINT stopped it there, what the asset earned, and the system signals
    it was priced against.
    """
    from services.asset_results.compute import attr_for, nom_col_for
    from services.dispatch_status import dispatch_status_detail

    nom_col = nom_col_for(component_class)
    if nom_col is None:
        raise HTTPException(
            400,
            f"{component_class!r} carries no capacity the optimiser sizes. "
            f"Sizeable classes: "
            f"{', '.join(sorted(_INVESTMENT_BUS_COLS))}",
        )

    n = PyPSAService.get_network()
    # `attr_for`, not `_GENERIC_CRUD_ATTRS`: the same class → DataFrame map
    # `get_asset_results` uses, so the row this reads and the KPIs it fuses
    # below can never come from two different tables.
    df = getattr(n, attr_for(component_class))
    if name not in df.index:
        raise HTTPException(404, f"No {component_class} named {name!r}")
    row = df.loc[name].to_dict()

    dispatch = dispatch_status_detail(n)
    solved = dispatch.get("state") == "fresh"

    buses = [str(row.get(col)) for col in _INVESTMENT_BUS_COLS[component_class]
             if row.get(col) is not None]
    sizing = _sizing(row, nom_col, solved=solved)
    co2 = _co2_signals() if solved else []

    # The per-asset KPIs, taken from the registry rather than recomputed, so
    # this can never disagree with the Asset Detail tab the user is looking at.
    kpis = get_asset_results(component_class, name, category="summary")

    return {
        "asset": {
            "component_class": component_class,
            "name": name,
            "carrier": row.get("carrier"),
            "buses": buses,
        },
        "dispatch_state": dispatch,
        "sizing": sizing,
        "asset_kpis": kpis.get("headline", []),
        "unavailable_kpis": kpis.get("unavailable", []),
        "system_signals": {
            "bus_prices": _bus_price_signals(n, buses) if solved else {},
            "co2_caps": co2,
            "congestion": _congestion_at(n, buses) if solved else {
                "lines": [], "note": "no fresh dispatch — nothing to assess"},
        },
        "reading_notes": _reading_notes(sizing, co2, buses),
    }

# ── Pre-dispatch validation (Improvement #19) ───────────────────────────────
#
# A validator answers one question about a destructive call BEFORE the user is
# asked to authorise it: can this possibly work? It returns an error message
# to refuse with, or None to proceed. `chat_service` consults this map right
# before `issue_confirmation`.
#
# The problem it solves is not a wasted round-trip. `cascade_delete_bus`
# carries a TYPED confirmation — the user retypes the bus name before Approve
# unlocks — so a call that was never going to succeed made someone type a
# name to authorise nothing. Do that a few times and confirming reads as
# harmless, which is the one habit a destructive prompt must not build.
#
# SCOPE, and why it stops where it does: every validator here checks the
# ACTIVE in-memory network, which the caller has already proved access to by
# having it open. Project- and snapshot-level tools (delete_project,
# restore_project_snapshot, …) are deliberately absent. Their existence check
# is inseparable from tenancy resolution, and CLAUDE.md's 403→404 rule exists
# because a check that runs before the caller has proved read access IS an
# existence oracle. A second, sloppier copy of that logic in a validator is
# precisely the wrong thing to add; those tools keep answering through the
# route handler that already gets it right.
#
# A validator must be cheap and side-effect-free — it runs on the SSE thread
# before any lock is taken.


# Mirrors `delete_component`'s own handler table, which is the authority on
# what that tool accepts.
_COMPONENT_CLASS_TO_ATTR: dict[str, str] = {
    "Bus": "buses",
    "Carrier": "carriers",
    "Line": "lines",
    "Link": "links",
    "Transformer": "transformers",
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Load": "loads",
    "ShuntImpedance": "shunt_impedances",
    "GlobalConstraint": "global_constraints",
}


def _validate_delete_component(args: dict[str, Any]) -> str | None:
    from services.pypsa_service import PyPSAService
    component_class = args.get("component_class")
    name = args.get("name")
    attr = _COMPONENT_CLASS_TO_ATTR.get(str(component_class))
    if attr is None:
        return (
            f"unknown component_class {component_class!r}; expected one of: "
            + ", ".join(sorted(_COMPONENT_CLASS_TO_ATTR))
        )
    df = getattr(PyPSAService.get_network(), attr, None)
    if df is None or name not in df.index:
        return (
            f"no {component_class} named {name!r} in the network — nothing to "
            f"delete. List the existing ones before retrying."
        )
    return None


def _validate_cascade_delete_bus(args: dict[str, Any]) -> str | None:
    from services.pypsa_service import PyPSAService
    name = args.get("name")
    if name not in PyPSAService.get_network().buses.index:
        return (
            f"no Bus named {name!r} in the network — nothing to delete. "
            f"List the buses before retrying."
        )
    return None


def _validate_batch_delete_components(args: dict[str, Any]) -> str | None:
    component_class = args.get("component_class")
    names = args.get("names")
    if not isinstance(names, list) or not names:
        return "names must be a non-empty list"
    attr = _COMPONENT_CLASS_TO_ATTR.get(str(component_class))
    if attr is None:
        return (
            f"unknown component_class {component_class!r}; expected one of: "
            + ", ".join(sorted(_COMPONENT_CLASS_TO_ATTR))
        )
    from services.pypsa_service import PyPSAService
    df = getattr(PyPSAService.get_network(), attr, None)
    index = set() if df is None else {str(x) for x in df.index}
    missing = [str(x) for x in names if str(x) not in index]
    if missing:
        sample = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        return (
            f"{len(missing)} of {len(names)} {component_class}(s) are not in "
            f"the network: {sample}. The whole batch would be refused — list "
            f"the existing ones and retry with names that exist."
        )
    return None


PRE_DISPATCH_VALIDATORS: dict[str, Any] = {
    "delete_component": _validate_delete_component,
    "cascade_delete_bus": _validate_cascade_delete_bus,
    # The widest blast radius in the set: do not make someone approve
    # deleting thirty components when one name is wrong and the call 404s
    # either way.
    "batch_delete_components": _validate_batch_delete_components,
}

# ── Registry entry-point ────────────────────────────────────────────────────

# Single source of truth for the (tool_name → callable) mapping. The Phase 2
# chat session loop iterates this dict to dispatch incoming tool_use blocks.
# Tools NOT in this dict are NOT exposed to the LLM.
DISPATCHERS: dict[str, Any] = {
    # read (22)
    "list_components": list_components,
    "diagnose_network": diagnose_network,
    "get_component": get_component,
    "get_meta": get_meta,
    "list_snapshots": list_snapshots,
    "list_carriers": list_carriers,
    "list_global_constraints": list_global_constraints,
    "list_timeseries_profiles": list_timeseries_profiles,
    "list_transformer_types": list_transformer_types,
    "download_timeseries_template": download_timeseries_template,
    "download_snapshot_weightings_csv": download_snapshot_weightings_csv,
    "list_investment_periods": list_investment_periods,
    "list_vintage_bounds": list_vintage_bounds,
    "get_vintage_results": get_vintage_results,
    "get_timeseries": get_timeseries,
    "list_all_timeseries": list_all_timeseries,
    "get_solver_config": get_solver_config,
    "get_solver_capabilities": get_solver_capabilities,
    "get_asset_costs": get_asset_costs,
    "get_simulation_status": get_simulation_status,
    "get_simulation_lock_status": get_simulation_lock_status,
    "get_simulation_log_history": get_simulation_log_history,
    "get_results": get_results,
    "get_aggregate_load": get_aggregate_load,
    # synthesis / analysis (read) — composite, in-process result fusion.
    #
    # NOT YET IMPLEMENTED. This block previously registered eight names —
    # diagnose_results, solve_overview, sanity_check_results,
    # compare_scenarios, generate_run_report, submit_plan, plan_what_if,
    # undo_my_last_chat_action — that were never defined anywhere in this
    # module. Importing chat_tools therefore raised
    # `NameError: name 'diagnose_results' is not defined` at module scope,
    # which took the whole chat tool surface down and blocked collection of
    # six test files, including test_tool_schema_signature_consistency.py —
    # the very test that asserts len(TOOLS) == len(DISPATCHERS). The defect
    # disabled its own detector.
    #
    # They are also absent from chat_tools_schema.TOOLS, so the LLM never
    # saw them: removing the registrations loses no working behaviour and
    # restores the documented invariant (112 schema == 112 dispatchers).
    #
    # To add one for real: implement the function here, add a matching entry
    # to chat_tools_schema.TOOLS *and* TOOL_ROUTES, and confirm the schema
    # `required` array matches the Python signature's defaults (see the
    # "Optional tool params" pitfall in CLAUDE.md).
    #
    # explain_investment is the first one added that way — a real fusion of
    # the sizing bound, the registry's per-asset KPIs and the system-wide
    # price/CO2/congestion signals.
    "explain_investment": explain_investment,
    # write_generic_crud (4)
    "create_component": create_component,
    "update_component": update_component,
    "delete_component": delete_component,
    "cascade_delete_bus": cascade_delete_bus,
    # write_bulk (1)
    "bulk_update_components": bulk_update_components,
    "batch_create_components": batch_create_components,
    "batch_delete_components": batch_delete_components,
    # write_carriers (1)
    "create_carrier": create_carrier,
    # write_meta (1)
    "update_meta": update_meta,
    # write_topology (2)
    "cluster_network": cluster_network,
    "recalculate_line_lengths": recalculate_line_lengths,
    # write_snapshots (4)
    "set_snapshots": set_snapshots,
    "set_snapshot_weightings": set_snapshot_weightings,
    "upload_snapshot_weightings_csv": upload_snapshot_weightings_csv,
    "sample_representative_weeks": sample_representative_weeks,
    # write_periods (3)
    "set_multi_period_snapshots": set_multi_period_snapshots,
    "set_investment_periods": set_investment_periods,
    "set_investment_period_weightings": set_investment_period_weightings,
    # write_vintage (3)
    "set_vintage_bounds": set_vintage_bounds,
    "delete_vintage_bounds": delete_vintage_bounds,
    "cleanup_orphan_vintages": cleanup_orphan_vintages,
    # write_timeseries (6)
    "upload_timeseries": upload_timeseries,
    "generate_exemplary_timeseries": generate_exemplary_timeseries,
    "delete_timeseries": delete_timeseries,
    "upload_load_profile": upload_load_profile,
    "upload_generator_profile": upload_generator_profile,
    "upload_link_profile": upload_link_profile,
    # write_solver (1)
    "update_solver_config": update_solver_config,
    # validation (3)
    "validate_network": validate_network,
    "check_solver_availability": check_solver_availability,
    "dispatch_status": dispatch_status,
    # execution_long_running (2)
    "run_simulation": run_simulation,
    "run_ac_pf_stage": run_ac_pf_stage,
    # execution (2)
    "abort_simulation": abort_simulation,
    "force_reset_simulation": force_reset_simulation,
    # adequacy_fmea (10) — the reliability surface: one read dispatcher over
    # the twelve no-argument GETs, the two per-project sidecars, the five
    # study starters, one abort.
    "get_adequacy_results": get_adequacy_results,
    "get_fmea_worksheet": get_fmea_worksheet,
    "get_asset_health": get_asset_health,
    "record_asset_health": record_asset_health,
    "get_stress_scenarios": get_stress_scenarios,
    "put_stress_scenarios": put_stress_scenarios,
    "get_eh_template": get_eh_template,
    "get_feature_guide": get_feature_guide,
    "review_eh_study": review_eh_study,
    "suggest_eh_setup": suggest_eh_setup,
    "run_fmea_sweep": run_fmea_sweep,
    "run_frontier_study": run_frontier_study,
    "run_mc_study": run_mc_study,
    "run_coupling_loop": run_coupling_loop,
    "run_margin_loop": run_margin_loop,
    "run_eh_study": run_eh_study,
    "abort_adequacy_study": abort_adequacy_study,
    # campaign (3) — one budget across a chain of studies
    "start_campaign": start_campaign,
    "campaign_status": campaign_status,
    "end_campaign": end_campaign,
    # study report (1) — the write-up, and what it does not establish
    "build_study_report": build_study_report,
    # solve_queue (4)
    "solve_queue_enqueue": solve_queue_enqueue,
    "solve_queue_list": solve_queue_list,
    "solve_queue_abort": solve_queue_abort,
    "solve_queue_clear_finished": solve_queue_clear_finished,
    # gridspine (16)
    "gridspine_create_study": gridspine_create_study,
    "gridspine_set_dispatch_source": gridspine_set_dispatch_source,
    "gridspine_get_config": gridspine_get_config,
    "gridspine_update_config": gridspine_update_config,
    "gridspine_run_pipeline": gridspine_run_pipeline,
    "gridspine_get_stage_status": gridspine_get_stage_status,
    "gridspine_list_ranked_snapshots": gridspine_list_ranked_snapshots,
    "gridspine_get_assumption_ledger": gridspine_get_assumption_ledger,
    "gridspine_edit_template_param": gridspine_edit_template_param,
    "gridspine_export_handoff_bundle": gridspine_export_handoff_bundle,
    "gridspine_get_readback": gridspine_get_readback,
    "gridspine_fetch_result_figure": gridspine_fetch_result_figure,
    "gridspine_get_capacity": gridspine_get_capacity,
    "gridspine_compute_capacity": gridspine_compute_capacity,
    "gridspine_get_connection_assessments": gridspine_get_connection_assessments,
    "gridspine_assess_connection": gridspine_assess_connection,
    # campus electrical (3)
    "campus_get_study": campus_get_study,
    "campus_draft_campus": campus_draft_campus,
    "campus_run_study": campus_run_study,
    # campus asset library and investment (3, plan C9)
    "campus_get_library": campus_get_library,
    "campus_set_library": campus_set_library,
    "campus_get_investment": campus_get_investment,
    # campus grid codes (2): no publish tool, by design (plan C10)
    "campus_list_grid_codes": campus_list_grid_codes,
    "campus_extract_grid_code": campus_extract_grid_code,
    # library (4)
    "list_library_items": list_library_items,
    "get_library_item": get_library_item,
    "import_urdb_tariff": import_urdb_tariff,
    "attach_tariff": attach_tariff,
    "set_site_connection": set_site_connection,
    "define_participants": define_participants,
    # investment case (4) — IC P4 WP4.6c
    "run_investment_case": run_investment_case,
    "get_investment_case": get_investment_case,
    "solve_ppa_price": solve_ppa_price,
    "explain_cashflow": explain_cashflow,
    # project_mgmt (21)
    "list_projects": list_projects,
    "load_project": load_project,
    "activate_project": activate_project,
    "save_project": save_project,
    "save_project_as": save_project_as,
    "save_project_a_copy": save_project_a_copy,
    "rename_project": rename_project,
    "delete_project": delete_project,
    "create_scenario": create_scenario,
    "list_scenarios": list_scenarios,
    "get_project_results_bundle": get_project_results_bundle,
    "get_project_layout": get_project_layout,
    "update_project_layout": update_project_layout,
    "download_project_bundle": download_project_bundle,
    "get_project_statistics": get_project_statistics,
    "get_project_network_meta": get_project_network_meta,
    "list_project_network_component": list_project_network_component,
    "import_project_bundle": import_project_bundle,
    "create_project_from_template": create_project_from_template,
    "get_project_compare_state": get_project_compare_state,
    "get_project_results_summary": get_project_results_summary,
    "compare_scenarios": compare_scenarios,
    # project_snapshots (4)
    "create_project_snapshot": create_project_snapshot,
    "list_project_snapshots": list_project_snapshots,
    "restore_project_snapshot": restore_project_snapshot,
    "delete_project_snapshot": delete_project_snapshot,
    # import_export (8)
    "import_network_nc": import_network_nc,
    "import_csv_bundle": import_csv_bundle,
    "import_excel": import_excel,
    "import_matpower": import_matpower,
    "export_network_nc": export_network_nc,
    "export_csv_bundle": export_csv_bundle,
    "export_excel": export_excel,
    "export_matpower": export_matpower,
    # audit_undo (4)
    "audit_log": audit_log,
    "clear_audit_log": clear_audit_log,
    "undo_last": undo_last,
    "undo_status": undo_status,
    # ui_control (3)
    "ui_select_component": ui_select_component,
    "ui_open_panel": ui_open_panel,
    "ui_set_snapshot": ui_set_snapshot,
    "ask_user": ask_user,
    "use_skill": use_skill,
    "start_workflow": start_workflow,
    "advance_workflow": advance_workflow,
    "end_workflow": end_workflow,
    # conversation (2)
    "list_chat_history": list_chat_history,
    "clear_chat_history": clear_chat_history,
    # uploads — consume (5)
    "list_uploads": list_uploads,
    "read_upload_meta": read_upload_meta,
    "read_excel_sheet": read_excel_sheet,
    "apply_demand_from_excel": apply_demand_from_excel,
    "delete_upload": delete_upload,
    # uploads — vision (Phase C stub)
    "reconstruct_network_from_image": reconstruct_network_from_image,
    # uploads — produce / agent exports (4)
    "export_to_excel": export_to_excel,
    "export_to_csv": export_to_csv,
    "export_preview_png": export_preview_png,
    "export_chat_summary": export_chat_summary,
    # reports (WP0 spike) — the EH ReferenceDesignReport as a .docx chip
    "export_eh_report_docx": export_eh_report_docx,
    # reports (WP6) — the generated study report over the WP1/WP3/WP5 routes
    "generate_report": generate_report,
    "get_report_status": get_report_status,
    "abort_report_generation": abort_report_generation,
    "list_reports": list_reports,
    "get_report": get_report,
    "get_report_table": get_report_table,
    "regenerate_report_section": regenerate_report_section,
    "export_report_docx": export_report_docx,
    "delete_report": delete_report,
    # reports (WP11) — user templates over the template routes
    "list_report_templates": list_report_templates,
    "set_report_template": set_report_template,
    "get_report_template": get_report_template,
    "propose_report_mapping": propose_report_mapping,
    "set_report_mapping": set_report_mapping,
    # reports (WP13) — the round trip over the round-trip routes
    "list_report_roundtrips": list_report_roundtrips,
    "import_edited_report": import_edited_report,
    "diff_report_versions": diff_report_versions,
    "export_report_pdf": export_report_pdf,
    # uploads — bulk delete (1, locked decision row 7: independent of chat history)
    "clear_uploads": clear_uploads,
    # asset_results (3) — Task 14: per-asset results chat surface
    "get_asset_results": get_asset_results,
    "ui_open_asset_detail": ui_open_asset_detail,
    "export_asset_results": export_asset_results,
    # llm provider switching (1) — Task 10
    "set_active_profile": set_active_profile,
}


# ── Foreign-lock gate at the dispatch seam (fix-wave F1) ────────────────────
#
# The write middleware in `main.py` refuses a non-holder's write to
# `/api/network/*`, `/api/io/*` and `/api/simulation/*` while another user
# holds the ACTIVE project's edit lock. Chat never goes through it: every tool
# above calls its route handler as a plain Python function, inside the SSE
# generator, long after any middleware ran. So the same component edit that a
# non-holder cannot make from the canvas was making it through the chat panel
# and landing in the holder's shared resident network, where the holder's next
# autosave persisted it.
#
# The gate is applied by WRAPPING the entries in `DISPATCHERS` rather than
# each tool body: `DISPATCHERS` is the single seam `chat_service` dispatches
# through, and a per-body check would have to be remembered ~40 times. The
# module-level functions stay unwrapped, so in-process callers that deliberately
# bypass the chat surface (tests, smoke harnesses) are unaffected.

# Kept in step with `main._FOREIGN_LOCK_GATE_PREFIXES` DELIBERATELY, not
# incidentally: this is the chat surface's copy of the same decision, and the two
# drifted the moment the HTTP gate gained "/api/results/" (2026-09-12) while this
# one did not. That drift was latent -- `TOOL_ROUTES` maps no tool to the five
# adequacy-study POSTs today -- and would have become a real hole the day an
# adequacy-study tool was added, which is exactly the "nothing to remember"
# guarantee `_lock_gated_tool_names` claims below. Caught by an independent QA
# review; `tests/test_chat_tools_lock_gate_parity.py` now fails if they diverge
# again, so this comment is not the only thing holding them together.
_LOCK_GATE_PREFIXES = (
    "/api/network/", "/api/io/", "/api/simulation/", "/api/results/",
)
# Explicit allowlist, not a prefix — mirrors `main.py`'s
# `_FOREIGN_LOCK_GATE_EXEMPT_EXACT` / `_FOREIGN_LOCK_GATE_EXEMPT_PATTERNS`.
# A queue route is exempt only when it acts on a JOB or names its project in
# the body; a hypothetical future sibling under `/api/simulation/queue/`
# that acts on the active project must stay gated by default, so this is
# spelled out per-route rather than `path.startswith("/api/simulation/queue")`.
#
#   * `/api/simulation/queue`                      (`solve_queue_enqueue`)
#     — names its project in the body, runs its own holder check.
#   * `/api/simulation/queue/clear_finished`       (`solve_queue_clear_finished`)
#     — cross-org by construction, super-admin-gated; never touches the
#       active project.
#   * `/api/simulation/queue/{job_id}/abort`       (`solve_queue_abort`)
#     — job-scoped; carries its own authorization keyed on the job. This is
#       `TOOL_ROUTES`'s literal template string (never a real job id at this
#       seam), so an exact match on the template is correct and does not need
#       the regex `main.py` uses against real request paths.
_LOCK_GATE_EXEMPT_PATHS = frozenset({
    "/api/simulation/queue",
    "/api/simulation/queue/clear_finished",
    "/api/simulation/queue/{job_id}/abort",
    # The study ABORTS, mirroring `main._FOREIGN_LOCK_GATE_EXEMPT_EXACT`. This
    # set had only the three queue paths, so `abort_adequacy_study` — which
    # routes to these — was refused under a foreign lock in chat while the
    # same POST succeeded over HTTP. main.py's own comment says why that is
    # the wrong way round: "Gating an abort would be actively harmful: a
    # foreign lock acquired while a study runs would trap it with no way to
    # stop it." The parity test now compares exemptions, not just prefixes.
    "/api/results/frontier/abort",
    "/api/results/mc/abort",
    "/api/results/fmea_sweep/abort",
    "/api/results/margin_loop/abort",
    "/api/results/coupling_loop/abort",
    "/api/results/eh_study/abort",
    # Latent today — `validate_network` is tiered `read`, so the tier filter
    # skips it before this set is consulted — but HTTP exempts preflight on
    # purpose, and the per-route parity test found the two sets disagreeing
    # here. Re-tiering that one tool would have made chat refuse a preflight
    # the middleware deliberately allows: the latent-drift case the parity
    # test's own docstring was written about.
    "/api/simulation/preflight",
})
_LOCK_GATE_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Tools with no HTTP route (`_service_call_` in TOOL_ROUTES) that nonetheless
# mutate the resident network. The route-derived rule below cannot see them,
# and they are exactly as capable of overwriting a lock holder's work as the
# routed ones — `batch_delete_components` more so than most. The undo capture
# (`_undo_captured_tool_names`) needs the same list for the same reason.
_NETWORK_SERVICE_CALL_MUTATORS = frozenset({
    "batch_create_components",
    "batch_delete_components",
    "generate_exemplary_timeseries",
    "apply_demand_from_excel",
    "reconstruct_network_from_image",
})

_LOCK_GATE_SERVICE_CALL_MUTATORS = _NETWORK_SERVICE_CALL_MUTATORS | frozenset({
    # Write edges into the PROJECT DIRECTORY that call their service layer
    # directly, so they never reach the REST handler that checks the lock.
    # `routers/uploads.py` decided this question the other way and said so:
    # the upload POST is "a write edge into `project.directory` same as
    # save/rename/delete", and the DELETE is "the sharp end of the gap this
    # router had: a non-holder deleting a file another session is actively
    # referencing (e.g. mid multimodal turn)". Both REST routes check the
    # lock; these tools did the same work without it.
    "delete_upload",
    "clear_uploads",
    # Unlinks the project's chat.jsonl and its rotation. No REST equivalent
    # exists, so there was no handler-level check to inherit either.
    "clear_chat_history",
    # Mutate the campaign record in the SHARED resident context's
    # solver_state, so a non-holder could set a solve budget on the holder's
    # context or close the campaign they are running.
    "start_campaign",
    "end_campaign",
})


def _lock_gated_tool_names() -> frozenset[str]:
    """
    The tools the seam gates: middleware parity, derived — not a hand list.

    A tool is gated when its safety tier is not "read" AND it either maps to a
    write route under a gated prefix (`chat_tools_schema.TOOL_ROUTES` is the
    tool→route map the endpoint-map test already keeps honest) or is one of the
    routeless network mutators above.

    Deriving it has a second payoff: a tool added later against a new
    `/api/network/*` route is gated the day it lands, with nothing to remember.

    Everything else is deliberately NOT gated:
      * `/api/projects/*` write tools (save / rename / delete / layout /
        snapshots) already call `_enforce_project_lock` in the handler body,
        and its 409 is the richer one — it names the TARGET project, which for
        a tool like `save_project('Other')` is not the active one this seam
        would have tested.
      * `solve_queue_enqueue` names its project in the body and runs its own
        check, exactly as `/api/simulation/queue` is exempt in the middleware.
      * `load_project` / `activate_project` are how a user gets AWAY from a
        locked project; gating them would trap them there. The middleware
        likewise gates neither (one is a GET, the other is an exempt suffix).
      * Export tools are tiered `read` or `write` inconsistently, so the
        derivation above cannot cover them as a family. They are gated at
        their single chokepoint instead — `_save_agent_export` calls
        `_check_foreign_lock` itself, which covers all ten regardless of tier
        and without re-tiering any of them (re-tiering would change their
        confirmation behaviour, which is a product decision and not this
        seam's to make).

        This bullet used to read "Upload / export / chat-history tools write
        artifacts, not network state, on surfaces the middleware does not gate
        either", and listed them as deliberately ungated. `routers/uploads.py`
        had already decided the same question the other way, in writing: the
        upload POST is "a write edge into `project.directory` same as
        save/rename/delete", and the DELETE is "the sharp end of the gap".
        Both REST routes check; the tools bypassed the handler and did the
        work anyway, so the two paths disagreed about the same bytes.
    """
    from services.chat_tools_schema import TOOL_ROUTES, safety_tier_for

    gated: set[str] = set()
    for name in DISPATCHERS:
        if safety_tier_for(name) == "read":
            continue
        if name in _LOCK_GATE_SERVICE_CALL_MUTATORS:
            gated.add(name)
            continue
        for route in TOOL_ROUTES.get(name, ()):
            if not isinstance(route, tuple):
                continue  # a `_service_call_` / `_ui_event_` sentinel
            method, path = route
            if (
                method.upper() in _LOCK_GATE_WRITE_METHODS
                and any(path.startswith(p) for p in _LOCK_GATE_PREFIXES)
                and path not in _LOCK_GATE_EXEMPT_PATHS
            ):
                gated.add(name)
                break
    return frozenset(gated)


def _check_foreign_lock(tool_name: str) -> None:
    """
    Raise 409 `project_locked` when another user holds the active project's
    edit lock. Same predicate as the middleware gate in `main.py`.

    CHECK ONLY — never an acquire. A free or expired lock, the acting user's
    own lock, local mode, an unbound scratch context and a tool call with no
    acting identity all pass through untouched.
    """
    import local_mode

    if local_mode.is_local_mode():
        return  # D7 — one identity, no lock semantics
    user_id = _ACTING_USER_ID.get()
    if user_id is None:
        return  # nothing to compare a holder against; `_acting()` owns the 401
    try:
        acting_uuid = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        return
    try:
        binding_uuid = PyPSAService.get_active_context().project_uuid
    except Exception:  # noqa: BLE001
        return  # unbound scratch context — nothing to guard
    if not binding_uuid:
        return
    try:
        lock_project_id = uuid.UUID(str(binding_uuid))
    except (TypeError, ValueError):
        return

    from db.session import SessionLocal
    from services import project_locks

    try:
        with SessionLocal() as gate_db:
            lock = project_locks.get_lock(gate_db, lock_project_id)
            if lock is None or lock.holder_user_id == acting_uuid:
                return
            detail = {
                "error_kind": "project_locked",
                "message": (
                    "This project is being edited by another user, so "
                    f"{tool_name!r} was not run. Their edit lock must expire "
                    "or be released first."
                ),
                "lock": project_locks.serialize_lock(
                    gate_db, lock_project_id, acting_uuid
                ),
            }
    except Exception:  # noqa: BLE001
        # FAIL CLOSED, matching the middleware gate (F4): `get_lock` prunes an
        # expired row (DELETE + commit), so a race can raise here, and a DB
        # error means the lock is UNKNOWN rather than absent. Dispatching
        # anyway would wave through precisely the write the gate exists to
        # stop, at the moment the check broke.
        logger.exception(
            "chat dispatch seam: lock check failed for %r; refusing the tool",
            tool_name,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error_kind": "project_lock_unavailable",
                "message": (
                    "Could not verify this project's edit lock, so "
                    f"{tool_name!r} was not run. Retry in a moment."
                ),
            },
        ) from None
    raise HTTPException(status_code=409, detail=detail)


def _lock_gated(tool_name: str, handler):
    """Wrap one dispatcher with the foreign-lock check."""
    import functools

    @functools.wraps(handler)
    def _wrapped(*args, **kwargs):
        _check_foreign_lock(tool_name)
        return handler(*args, **kwargs)

    return _wrapped


# Applied in place so anything already holding a reference to DISPATCHERS
# (chat_service imports the dict itself) sees the gated callables.
DISPATCHERS.update({
    name: _lock_gated(name, DISPATCHERS[name])
    for name in _lock_gated_tool_names()
})


# ── Live-network study gate at the same seam (P27a, gate B1) ────────────────
#
# `main.py` refuses every `/api/network/*` and `/api/io/*` write while a
# live-network study (sweep, frontier, coupling / margin loop) runs, because
# the study re-solves the user's own network between its iterates. The chat
# tools call their handlers in process and never meet that middleware, so the
# same refusal is applied here, to the same DERIVED set the foreign-lock gate
# uses, narrowed to those two prefixes (plus the routeless mutators). A tool
# added later against an `/api/network/*` route is gated the day it lands.
#
# Left out on purpose: the tools that REPLACE the whole network. The imports
# go through `reset_network` and the re-cluster through its own swap path,
# both of which refuse over every study with the swap sentence (Phase 11) —
# the stricter guard, since a swap detaches even an `mc` / `eh_study` run.
# Read tools are never gated (`_lock_gated_tool_names` skips them).
_STUDY_GATE_PREFIXES = ("/api/network/", "/api/io/")
_STUDY_GATE_SWAP_TOOLS = frozenset({
    "import_network_nc", "import_csv_bundle", "import_excel", "import_matpower",
    "cluster_network",
})


def _study_gated_tool_names() -> frozenset[str]:
    """The chat tools a running live-network study refuses (derived)."""
    from services.chat_tools_schema import TOOL_ROUTES

    gated: set[str] = set()
    for name in _lock_gated_tool_names():
        if name in _STUDY_GATE_SWAP_TOOLS:
            continue
        # The NETWORK mutators, not the whole lock-gate set: that set also
        # holds project-folder writes (uploads, chat history, campaigns) that
        # never touch the network a study re-solves.
        if name in _NETWORK_SERVICE_CALL_MUTATORS:
            gated.add(name)
            continue
        for route in TOOL_ROUTES.get(name, ()):
            if not isinstance(route, tuple):
                continue
            method, path = route
            if (method.upper() in _LOCK_GATE_WRITE_METHODS
                    and any(path.startswith(p) for p in _STUDY_GATE_PREFIXES)):
                gated.add(name)
                break
    return frozenset(gated)


def _study_gated(tool_name: str, handler):
    """Wrap one dispatcher with the live-network study refusal."""
    import functools

    from services.study_state import refuse_edit_during_live_study

    @functools.wraps(handler)
    def _wrapped(*args, **kwargs):
        refuse_edit_during_live_study()
        return handler(*args, **kwargs)

    return _wrapped


DISPATCHERS.update({
    name: _study_gated(name, DISPATCHERS[name])
    for name in _study_gated_tool_names()
})


# ── Undo capture (CH-3) ─────────────────────────────────────────────────────
#
# Undo snapshots are pushed by `main.undo_snapshot_middleware`, and a chat tool
# calls its handler in-process, so a chat edit used to push nothing: a later
# `undo_last` either refused or reverted an OLDER canvas edit while reporting
# `{"undone": true}`. The dispatcher now pushes the snapshot itself — see
# `chat_service._snapshot_for_turn_undo` — for exactly the tools below.
#
# Mirrors of main.py's two undo constants. Kept in step by
# `tests/test_chat_undo_snapshot.py`, which compares them against main's, so
# this comment is not the only thing holding them together.
_UNDO_PREFIXES = ("/api/network/", "/api/io/")
_UNDO_EXCLUDE = frozenset({"/api/network/undo", "/api/network/undo/info"})


def _undo_captured_tool_names() -> frozenset[str]:
    """
    The tools whose call is preceded by an undo snapshot: middleware parity,
    derived the way `_lock_gated_tool_names` is.

    A tool is captured when its tier is not "read" AND it either maps to a
    write route the middleware would snapshot (`_UNDO_PREFIXES`, minus
    `_UNDO_EXCLUDE`) or is one of the routeless network mutators. Project-
    folder writes (uploads, chat history, campaigns) are deliberately NOT in
    it: the undo stack holds the NETWORK, so a snapshot before them would be an
    undo step that changes nothing.
    """
    # From the catalogue's home, per harness/README.md's contract item 7 (new
    # code imports from `harness.*`); `services.chat_tools_schema` is an alias
    # of the same module object.
    from harness.catalogue import TOOL_ROUTES, safety_tier_for

    captured: set[str] = set()
    for name in DISPATCHERS:
        if safety_tier_for(name) == "read":
            continue
        if name in _NETWORK_SERVICE_CALL_MUTATORS:
            captured.add(name)
            continue
        for route in TOOL_ROUTES.get(name, ()):
            if not isinstance(route, tuple):
                continue
            method, path = route
            if (
                method.upper() in _LOCK_GATE_WRITE_METHODS
                and any(path.startswith(p) for p in _UNDO_PREFIXES)
                and path not in _UNDO_EXCLUDE
            ):
                captured.add(name)
                break
    return frozenset(captured)


UNDO_CAPTURED_TOOLS = _undo_captured_tool_names()
