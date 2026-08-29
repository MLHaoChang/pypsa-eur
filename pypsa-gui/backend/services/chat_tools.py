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
audit log, undo registration, _user_ts cleanup, and vintage-bounds cleanup
because they go through the same _create/_update/_delete_component generic
helpers (and dedicated wrappers for Bus rename / Transformer / GlobalConstraint).

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
from typing import Any

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
    # Prefill first; agent attrs win on conflict.
    instance = Schema(name=name, **{**prefill, **attrs})
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
    """
    n = PyPSAService.get_network()
    buses = list(n.buses.index)
    if not buses:
        return {
            "bus_count": 0, "island_count": 0, "islands": [],
            "isolated_buses": [], "islands_without_generation": [],
            "islands_truncated": False, "verdict": "empty",
        }

    # Union-find over the bus graph. Every branch class joins, including a
    # multi-port Link's bus2/bus3/… — those extra ports are exactly how
    # sector coupling reaches heat and hydrogen buses, so walking only
    # bus0/bus1 would report a coupled network as a pile of fragments.
    parent = {b: b for b in buses}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    for attr in ("lines", "links", "transformers"):
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ports = [c for c in df.columns if c.startswith("bus")]
        for row in df[ports].itertuples(index=False):
            attached = [str(v) for v in row if isinstance(v, str) and v]
            for other in attached[1:]:
                union(attached[0], other)

    groups: dict[str, list[str]] = {}
    for b in buses:
        groups.setdefault(find(b), []).append(b)

    # Which buses can serve load, and how much load sits where.
    supply: set[str] = set()
    for attr in ("generators", "storage_units", "stores"):
        df = getattr(n, attr, None)
        if df is not None and not df.empty and "bus" in df.columns:
            supply.update(str(b) for b in df["bus"])

    load_by_bus: dict[str, float] = {}
    loads = getattr(n, "loads", None)
    if loads is not None and not loads.empty and "bus" in loads.columns:
        p_set_t = getattr(n.loads_t, "p_set", None)
        for name, bus in loads["bus"].items():
            peak = 0.0
            if p_set_t is not None and name in getattr(p_set_t, "columns", []):
                series = p_set_t[name]
                peak = float(series.max()) if len(series) else 0.0
            else:
                peak = float(loads.at[name, "p_set"]) if "p_set" in loads.columns else 0.0
            load_by_bus[str(bus)] = load_by_bus.get(str(bus), 0.0) + peak

    islands = []
    for members in groups.values():
        members = sorted(members)
        peak = sum(load_by_bus.get(b, 0.0) for b in members)
        islands.append({
            "size": len(members),
            "buses": members[:_MAX_BUSES_PER_ISLAND],
            "has_generation": any(b in supply for b in members),
            "has_load": peak > 0,
            "peak_load_mw": round(peak, 6),
        })
    # Biggest first: on a fragmented network the large islands are the ones
    # the user recognises, and the truncation below keeps the head.
    islands.sort(key=lambda i: (-i["size"], i["buses"][0] if i["buses"] else ""))

    # A generation-only island is odd but solvable. Only a marooned LOAD is
    # a defect — flagging the rest would train the agent to ignore the field.
    stranded = [i for i in islands if i["has_load"] and not i["has_generation"]]

    branch_free = {
        b for b in buses
        if len(groups[find(b)]) == 1
    }
    isolated = sorted(branch_free)

    if stranded:
        verdict = "infeasible_topology"
    elif len(groups) > 1:
        verdict = "fragmented"
    else:
        verdict = "connected"

    return {
        "bus_count": len(buses),
        "island_count": len(groups),
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
    "asset_economics",
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


def get_results(result_kind: str, source: str = "lopf") -> Any:
    """
    v4-MAJOR-4 dispatcher: every results enum routes through the named
    handler, with ac_pf_status mapped to a distinct path via the lookup dict.
    `source` ('lopf' | 'ac_pf') is forwarded where the handler supports it.
    """
    handler = _resolve_results_handler(result_kind)
    # Some handlers take `source` as a query param; pass via kwargs if the
    # function accepts it, else call bare. Inspect via __code__.co_varnames.
    if "source" in handler.__code__.co_varnames:
        return handler(source=source)
    return handler()


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

      Bus + new_name → rename_bus  (n.rename_component_names preserves dependent
                                    bus0/bus1 refs on lines/links/transformers)
      Bus, no new_name → update_bus  (coord-change line-length recompute)
      Transformer → update_transformer  (voltage validation + type sanitise)
      GlobalConstraint → update_global_constraint  (partial-PUT mitigation;
                                                   NOT in _COMPONENT_ATTRS)
      Other 7 classes (Carrier/Line/Link/Generator/StorageUnit/Store/Load/
                       ShuntImpedance) → _update_component direct
    """
    attrs = dict(attrs or {})

    # F1: Bus rename has its own endpoint
    if component_class == "Bus" and new_name:
        from routers.network import rename_bus
        return rename_bus(name, {"new_name": new_name})

    # Bus non-rename: dedicated handler preserves coord-change recompute
    if component_class == "Bus":
        from routers.network import update_bus
        bus = _get_schema("BusCreate")(name=name, **attrs)
        return update_bus(name, bus)

    # F2: Transformer needs voltage validation
    if component_class == "Transformer":
        from routers.network import update_transformer
        tr = _get_schema("TransformerCreate")(name=name, **attrs)
        return update_transformer(name, tr)

    # F3: GlobalConstraint dedicated CRUD
    if component_class == "GlobalConstraint":
        from routers.network import update_global_constraint
        gc = _get_schema("GlobalConstraintCreate")(name=name, **attrs)
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
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # Validation passed and this still failed, so the batch IS
            # partial. Say exactly what landed — claiming atomicity we did
            # not deliver would send the agent looking for the wrong bug.
            raise HTTPException(
                500,
                f"batch partially applied: created {created} before "
                f"{name!r} failed: {exc}",
            ) from exc
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
        return _sync(_h(upload, attribute=attribute))
    return _sync(_h(upload))


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
        return _sync(_h(upload, attribute=attribute))
    return _sync(_h(upload))


# ── Solver config (1) ───────────────────────────────────────────────────────


def update_solver_config(partial: dict) -> dict:
    from routers.simulation import update_solver_config as _h
    from models.schemas import SolverConfigSchema
    body = SolverConfigSchema(**partial)
    return _h(body)


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
        injected = {n: v for n, v in (("db", db), ("user", user)) if n in params}
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
                    f"db/user/session. Resolve it at the call site or extend _route()."
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
    # AuthorizedProject = ProjectAccessDep) and reads req.label / req.message —
    # pass the model, not a dict (a direct call doesn't get FastAPI's body
    # parsing, so a dict would AttributeError on req.label). The `project`
    # default is an unresolved `Depends`, so it has to be supplied too; these
    # four take a resolved AuthorizedProject rather than db=/user=, so
    # `_authorized_project` is the right helper and `_route` is NOT.
    from routers.snapshots import create_snapshot, CreateSnapshotRequest
    return create_snapshot(
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
    from routers.snapshots import delete_snapshot as _h
    _h(snapshot_id, _authorized_project(name))


# ── Import / Export (8) ─────────────────────────────────────────────────────


def import_network_nc(bytes_b64: str, filename: str = "network.nc") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_netcdf as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(_h(upload))


def import_csv_bundle(bytes_b64: str, filename: str = "csv.zip") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_csv as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(_h(upload))


def import_excel(bytes_b64: str, filename: str = "network.xlsx") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_excel as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(_h(upload))


def import_matpower(bytes_b64: str, filename: str = "case.m") -> dict:
    import base64
    import io
    from fastapi import UploadFile
    from routers.io import import_matpower as _h
    data = base64.b64decode(bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(_h(upload))


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
    replace: bool = False,
) -> dict:
    """
    Parse an Excel/CSV upload's `time_col` + `value_col` into a per-snapshot
    Load demand profile. Two-pass:

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

    if client is None:
        from services import chat_service
        client, err = chat_service._build_anthropic_client()
        if client is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_kind": err or "internal_error",
                    "message": "vision sub-call requires ANTHROPIC_API_KEY",
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

        with client.messages.stream(
            model=DEFAULT_MODEL,
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
                if not bname or bname in existing_bus_names:
                    continue
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
        w.writerow(list(columns))
    for row in (rows or []):
        w.writerow(list(row))
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
    # solve_queue (4)
    "solve_queue_enqueue": solve_queue_enqueue,
    "solve_queue_list": solve_queue_list,
    "solve_queue_abort": solve_queue_abort,
    "solve_queue_clear_finished": solve_queue_clear_finished,
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
    # uploads — bulk delete (1, locked decision row 7: independent of chat history)
    "clear_uploads": clear_uploads,
    # asset_results (3) — Task 14: per-asset results chat surface
    "get_asset_results": get_asset_results,
    "ui_open_asset_detail": ui_open_asset_detail,
    "export_asset_results": export_asset_results,
    # llm provider switching (1) — Task 10
    "set_active_profile": set_active_profile,
}
