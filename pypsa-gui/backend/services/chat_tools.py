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

import logging
import math
from typing import Any

from fastapi import HTTPException

from services.pypsa_service import PyPSAService

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


def list_components(component_class: str) -> list[dict]:
    """List all components of one class (transient-filtered)."""
    from routers.network import _get_component
    if component_class == "GlobalConstraint":
        attr = "global_constraints"
    elif component_class not in _GENERIC_CRUD_ATTRS:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    else:
        attr = _GENERIC_CRUD_ATTRS[component_class]
    return _get_component(component_class, attr)


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


def list_all_timeseries() -> list[dict]:
    # NOTE: the route handler is `list_timeseries` (GET /api/network/timeseries),
    # not `list_all_timeseries`. It walks every `<component>_t` accessor and
    # reports non-empty frames + columns directly off the network, so time series
    # baked into an imported .nc are surfaced (not just user uploads).
    from routers.network import list_timeseries as _h
    return _h()


def get_aggregate_load(section: str | None = None, names: str | None = None) -> dict:
    """Time-aligned sum of load p_set, by explicit names (CSV) or by section.

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
    # Mirror the route handlers' exclude_unset behaviour by building a
    # Pydantic instance, then dumping exclude_unset=True.
    schema_name = _COMPONENT_CREATE_SCHEMAS[component_class]
    Schema = _get_schema(schema_name)
    instance = Schema(name=name, **attrs)
    payload = instance.model_dump(exclude_unset=True)
    return _update_component(
        component_class, _GENERIC_CRUD_ATTRS[component_class], name, payload,
    )


def delete_component(component_class: str, name: str) -> None:
    """Generic delete via the dedicated route handler (so the same lock + audit run)."""
    from routers import network as net
    handlers = {
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
    h = handlers.get(component_class)
    if h is None:
        raise HTTPException(400, f"Unknown component_class: {component_class!r}")
    h(name)


def cascade_delete_bus(name: str) -> None:
    """Bus + all attached lines/links/transformers/generators/loads/storage/stores."""
    from routers.network import delete_bus_cascade
    delete_bus_cascade(name)


# ── Bulk (1) ────────────────────────────────────────────────────────────────


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
    """
    import io
    from fastapi import UploadFile
    from routers.network import upload_timeseries as _h
    upload = UploadFile(filename=f"{name}.csv", file=io.BytesIO(csv_content.encode("utf-8")))
    return _sync(_h(component=component, attribute=attribute, file=upload))


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
    return _h(EnqueueRequest(project_id=project_id))


def solve_queue_list() -> dict:
    from routers.solve_queue import list_queue as _h
    return _h()


def solve_queue_abort(job_id: str) -> dict:
    # The queue's jobs dict is int-keyed (services/solve_queue.py); a string
    # job_id silently misses every key and the handler 404s while the model
    # thinks the abort worked. Coerce to int. A non-numeric id raises a clear
    # ValueError that the dispatcher surfaces as a tool_error.
    from routers.solve_queue import abort_job as _h
    return _h(int(job_id))


def solve_queue_clear_finished() -> dict:
    """N1: read-tier — drops listing entries only, idempotent."""
    from routers.solve_queue import clear_finished as _h
    return _h()


# ── Project management (21) ─────────────────────────────────────────────────


def list_projects() -> list[dict]:
    """
    v6-F2: each entry's `resident` flag is a snapshot at READ TIME. A
    concurrent eviction between this call and a follow-up activate_project
    can flip resident=true → false; activate_project takes the cold path
    automatically in that case (projects.py:1319-1326), both still succeed.
    """
    from routers.projects import list_projects as _h
    result = _h()
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
    return _h(name)


def activate_project(project_id: str) -> dict:
    """
    POST /api/projects/{project_id}/activate — instant resident-switch path.
    v6-F2: tolerates concurrent eviction (cold path at projects.py:1319-1326).
    """
    from routers.projects import activate_project as _h
    return _h(project_id)


def save_project(name: str, force: bool = False, expect: str | None = None) -> dict:
    """
    DESTRUCTIVE — overwrites projects/<name>/. The v6 F1 backend guard inside
    `with ctx.mutation_lock:` (projects.py:976-992) catches cross-project
    name claims. This tool wrapper does NOT add a pre-check (the agent is
    asserting same-name autosave / first-save semantics).
    """
    from routers.projects import save_project as _h
    return _h(name, force=force, expect=expect)


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
    return _h(name, rebind=True)


def save_project_a_copy(name: str) -> dict:
    """POST /api/projects/{name} with rebind=False — branches on disk."""
    from routers.projects import save_project as _h
    return _h(name, rebind=False)


def rename_project(name: str, new_name: str) -> dict:
    from routers.projects import rename_project as _h
    from models.schemas import RenameProjectRequest
    body = RenameProjectRequest(new_name=new_name)
    return _h(name, body)


def delete_project(name: str, cascade: bool = False) -> dict:
    """
    v4-MINOR-1: cascade param. On 409 with descendants the chat layer surfaces
    the descendant list in the Confirmation Card; the agent must NOT auto-cascade.
    """
    from routers.projects import delete_project as _h
    return _h(name, cascade=cascade)


def create_scenario(base: str, new_name: str, description: str | None = None) -> dict:
    """
    POST /api/projects/{base}/scenarios. The route handler rejects when the
    active ctx is not `base` (projects.py:1529). Chat.jsonl is COPIED into
    the new scenario dir (F12 — Phase 4 polish).
    """
    from routers.projects import create_scenario as _h
    from models.schemas import CreateScenarioRequest
    body = CreateScenarioRequest(name=new_name, description=description or "")
    return _h(base, body)


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
    return _h(name)


def get_project_layout(name: str) -> dict:
    from routers.projects import get_layout as _h
    return _h(name)


def update_project_layout(name: str, layout: dict) -> dict:
    from routers.projects import put_layout as _h
    return _h(name, layout)


def download_project_bundle(name: str) -> dict:
    from routers.projects import _project_bundle_bytes
    return _save_agent_export(
        _project_bundle_bytes(name), f"{name}.pypsaproj.zip", "application/zip",
    )


def get_project_statistics(name: str) -> dict:
    from routers.projects import project_statistics as _h
    return _h(name)


def get_project_network_meta(project_id: str) -> dict:
    """C11: non-active project meta peek without losing active."""
    # Handler get_project_meta(ctx: ProjectContext = ProjectDep) takes its ctx
    # via FastAPI dependency injection — a direct call gets no DI, so resolve the
    # ctx ourselves (validates id + 404s, resolve-or-load-resident, never touches
    # the active slot) and pass it explicitly.
    from routers.deps import resolve_project_context
    from routers.project_network import get_project_meta as _h
    return _h(ctx=resolve_project_context(project_id))


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
    return _h(component_class=attr, ctx=resolve_project_context(project_id))


def import_project_bundle(bundle_bytes_b64: str, filename: str = "bundle.zip") -> dict:
    """C9: import a project bundle export."""
    import base64
    import io
    from fastapi import UploadFile
    from routers.projects import import_bundle as _h
    data = base64.b64decode(bundle_bytes_b64)
    upload = UploadFile(filename=filename, file=io.BytesIO(data))
    return _sync(_h(upload))


def create_project_from_template(template_id: str, new_name: str) -> dict:
    """C9: scaffold from a built-in template."""
    # Handler is create_from_template(template_id, name: str | None) — pass the
    # NAME STRING, not a dict (it does (name or "").strip() on the 2nd arg).
    from routers.projects import create_from_template as _h
    return _h(template_id, new_name)


def get_project_compare_state(name: str) -> dict:
    from routers.compare import get_compare_state as _h
    return _h(name)


def get_project_results_summary(name: str) -> dict:
    from routers.compare import get_results_summary as _h
    return _h(name)


# ── Project snapshots (4) — checkpoint backups, NOT time snapshots ──────────


def create_project_snapshot(name: str, label: str, message: str | None = None) -> dict:
    # Handler is create_snapshot(name, req: CreateSnapshotRequest) and reads
    # req.label / req.message — pass the model, not a dict (a direct call doesn't
    # get FastAPI's body parsing, so a dict would AttributeError on req.label).
    from routers.snapshots import create_snapshot, CreateSnapshotRequest
    return create_snapshot(name, CreateSnapshotRequest(label=label, message=message or ""))


def list_project_snapshots(name: str) -> list[dict]:
    from routers.snapshots import list_snapshots as _h
    return _h(name)


def restore_project_snapshot(name: str, snapshot_id: str) -> dict:
    from routers.snapshots import restore_snapshot as _h
    return _h(name, snapshot_id)


def delete_project_snapshot(name: str, snapshot_id: str) -> None:
    from routers.snapshots import delete_snapshot as _h
    _h(name, snapshot_id)


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
    from routers.changelog import get_changelog as _h
    entries = _h()
    if limit is not None and isinstance(entries, list):
        return entries[-limit:]
    return entries


def clear_audit_log() -> None:
    from routers.changelog import clear_changelog as _h
    _h()


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


def ui_open_panel(panel_id: str) -> dict:
    return {"_ui_event": True, "kind": "open_panel", "panel_id": panel_id}


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
    """Return the active project name or raise an HTTPException(400).

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
        with client.messages.stream(
            model="claude-sonnet-4-6",
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
                "message": f"vision sub-call raised {type(exc).__name__}: {exc}",
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


# ── Registry entry-point ────────────────────────────────────────────────────

# Single source of truth for the (tool_name → callable) mapping. The Phase 2
# chat session loop iterates this dict to dispatch incoming tool_use blocks.
# Tools NOT in this dict are NOT exposed to the LLM.
DISPATCHERS: dict[str, Any] = {
    # read (22)
    "list_components": list_components,
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
    # synthesis / analysis (read) — composite, in-process result fusion
    "diagnose_results": diagnose_results,
    "solve_overview": solve_overview,
    "sanity_check_results": sanity_check_results,
    "compare_scenarios": compare_scenarios,
    "generate_run_report": generate_run_report,
    "submit_plan": submit_plan,
    "plan_what_if": plan_what_if,
    "undo_my_last_chat_action": undo_my_last_chat_action,
    # write_generic_crud (4)
    "create_component": create_component,
    "update_component": update_component,
    "delete_component": delete_component,
    "cascade_delete_bus": cascade_delete_bus,
    # write_bulk (1)
    "bulk_update_components": bulk_update_components,
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
    # write_timeseries (5)
    "upload_timeseries": upload_timeseries,
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
}
