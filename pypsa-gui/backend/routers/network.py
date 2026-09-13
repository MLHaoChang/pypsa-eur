from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession
from models.schemas import (
    BusCreate,
    CarrierCreate,
    GeneratorCreate,
    ImpedanceRescaleRequest,
    LineCreate,
    LinkCreate,
    LoadCreate,
    NetworkMeta,
    ShuntImpedanceCreate,
    StorageUnitCreate,
    StoreCreate,
    TransformerCreate,
)
from db.models import Session as SessionRow
from db.session import get_db
from deps import current_session
from services import active_project, attribute_catalog
from services.transient_rows import filter_transient_names
# Phase 12f's write-path guards live in `services/user_timeseries.py` beside
# the `_user_ts` machinery they protect, so `routers/network_time_axis.py`
# can call them without importing back from this module (the decomposition's
# one-way rule). Re-exported here because the chat tools and the
# nonfinite-bounds tests reach them through `routers.network`.
from services.user_timeseries import (  # noqa: F401
    _attribute_default_is_finite,
    _reject_nonfinite_timeseries,
)
from services.pypsa_service import PyPSAService

# ── Re-export façade: the extracted helper services ──────────────────────────
# These names are DEFINED under `services/` now (see the decomposition spec,
# Phase 4 addendum). They are imported back because `routers.network` is the
# import surface fifty-plus call sites already use — `services/chat_tools.py`,
# `routers/projects.py`, `routers/snapshots.py`, `routers/io.py`,
# `routers/project_network.py`, `main.py`, `services/solver_service.py` and the
# tests — and every one of them still works unchanged.
#
# Every cluster was a PURE move, so these are the identical objects, not
# wrappers. That matters most for `_user_ts` / `_user_ts_lock`: they are shared
# mutable state that importers take by value, so two objects would mean two
# stores. The ~80 thin CRUD routes stay in this module; the factory lives in services.network_crud (re-exported below).
# `bulk_update` stays as a thin FastAPI handler; its body is
# `services.network_bulk.apply_bulk_update` (helpers re-exported below).
# The generic CRUD factory is re-exported from services.network_crud.
# Profile routes (+ `_xlsx_response` / `_apply_profile_upload`) live in
# `routers.network_profiles` and are re-exported below.
from services.network_geometry import (  # noqa: F401
    _EARTH_KM,
    _IMPEDANCE_FIELDS,
    _RecomputeResult,
    _bus_coord,
    _haversine_km,
    _impedance_preview,
    _line_haversine_km,
    _recompute_lengths_for_bus,
)
from services.transformer_rules import (  # noqa: F401
    _VNOM_TOL_KV,
    _enrich_transformer_voltage,
    _sanitise_transformer_type,
    _validate_transformer_voltage,
)
from services.profile_shapes import (  # noqa: F401
    _CONVENTIONAL_KW,
    _DR_KW,
    _ELEC_CARRIERS,
    _H2_CARRIERS,
    _H2_CARRIERS_LOAD,
    _HEAT_CARRIERS,
    _RENEWABLE_KW,
    _double_peak_profile,
    _flat_cf_profile,
    _gen_category,
    _h2_load_profile,
    _heat_load_profile,
    _link_category,
    _load_section,
    _profile_meta_for,
    _shape_for_section,
    _solar_cf_profile,
    _template_snapshots,
    _wind_cf_profile,
)
from services.snapshot_index import (  # noqa: F401
    _build_period_multiindex,
    _infer_snapshot_freq,
)
from services.user_timeseries import (  # noqa: F401
    _TS_COMPONENTS,
    _annual_hourly_reference,
    _backup_network_ts_to_user_ts,
    _capture_snapshot_weights_per_timestep,
    _ensure_snapshots_cover_user_ts,
    _flatten_snapshot_state,
    _parse_upload,
    _reapply_snapshot_weights,
    _reapply_user_ts_to_network,
    _rebase_flat_user_ts,
    _restore_user_ts,
    _serialize_user_ts,
    _user_ts,
    _user_ts_delete_asset,
    _user_ts_extent,
    _user_ts_lock,
    _user_ts_rename_asset,
)
from services.network_bulk import (  # noqa: F401
    _COMPONENT_ATTRS,
    _FINITE_DEFAULT_BOUNDS,
    _bool_input_default,
    _coerce_bulk_value,
    _finite_bound_default,
    _finite_input_meta,
    apply_bulk_update,
)
from services.network_buses import (  # noqa: F401
    apply_delete_bus_cascade,
    apply_rename_bus,
    apply_update_bus,
)
from services.network_lines import (  # noqa: F401
    apply_recalculate_line_lengths,
    apply_rescale_impedances,
)
from services.network_global_constraints import (  # noqa: F401
    apply_create_global_constraint,
    apply_delete_global_constraint,
    apply_update_global_constraint,
)

router = APIRouter()

# ── Phase 5 façade: the time-axis routes ─────────────────────────────────────
# `services/chat_tools.py` imports fourteen of these handlers BY NAME and calls
# them in-process, so this module stays their import surface. At the TOP, with
# the other imports: a module body executes top to bottom, and a re-export at
# the bottom is not bound yet for anything above it that references it.
from routers.network_time_axis import (  # noqa: E402,F401
    _ATTR_TO_CLASS,
    # Re-exported for the same reason as every name in this block: the
    # decomposition's contract is that the ORIGINAL module stays the import
    # surface. `tests/test_model_horizon_endpoints.py` imports it from here.
    _infer_snapshot_freq,
    delete_timeseries,
    download_snapshot_weightings_csv,
    get_investment_periods,
    get_snapshots,
    get_timeseries,
    list_timeseries,
    sample_representative_weeks,
    set_investment_periods,
    set_multi_period_snapshots,
    set_snapshots,
    set_timeseries,
    update_investment_period_weightings,
    update_snapshot_weightings,
    upload_snapshot_weightings_csv,
    upload_timeseries,
)
from routers.network_time_axis import router as _time_axis_router

router.include_router(_time_axis_router)

# ── Profiles façade ──────────────────────────────────────────────────────────
# Same contract as the time-axis block: chat_tools imports these BY NAME.
from routers.network_profiles import (  # noqa: E402,F401
    _LOAD_SHAPES,
    _apply_profile_upload,
    _xlsx_response,
    aggregate_load_profile,
    download_generator_profile_template,
    download_link_profile_template,
    download_load_profile_template,
    get_generator_profiles,
    get_link_profiles,
    get_load_profiles,
    upload_generator_profile,
    upload_link_profile,
    upload_load_profile,
)
from routers.network_profiles import router as _profiles_router

router.include_router(_profiles_router)


# `df_to_json` (static-DataFrame → NaN-safe row dicts) now lives in
# `services/serialization.py` — the single JSON-boundary scrub home. Imported
# above; still called ~7× in this module. The separate *vectorised* time-series
# serialiser for the perf-critical `/timeseries` path stays inline below.

# ── Generic CRUD factory ────────────────────────────────────────────────────
# Bodies live in services.network_crud. Identity re-exports keep the fifty-plus
# call sites (chat_tools, project_network, tests) importing from routers.network.
from services.network_crud import (  # noqa: F401
    _serialize_component,
    _get_component,
    _meta_payload,
    _drop_unknown_extras,
    _normalise_flag_column,
    _create_component,
    _merge_partial_update,
    _detach_component_series,
    _reattach_component_series,
    _rename_component_safely,
    _update_component,
    _delete_component,
)





# Moved to `services/transient_rows.py` in Phase 5: `routers/network_time_axis.py`
# needs it too, and a router it was split out of is not something it may import
# back from. The alias keeps the private name working for this module's own
# call sites.
_filter_transient_names = filter_transient_names




# ── Geometry helpers ─────────────────────────────────────────────────────────
# Used by line auto-length: on line create, and on any bus x/y change so the
# line lengths track the geometry. Manual edits via PUT /lines/{name} are
# respected — the user can still override the auto value.


# ── Buses ────────────────────────────────────────────────────────────────────
# create/delete stay as CRUD one-liners; update/cascade/rename bodies live in
# services.network_buses (coord recompute, dependency wipe, rename_component_names).
# Spec: docs/superpowers/specs/2026-09-13-network-bus-specials-lift-design.md

@router.get("/buses")
def get_buses():
    return _get_component("Bus", "buses")


@router.post("/buses", status_code=201)
def create_bus(bus: BusCreate):
    return _create_component("Bus", "buses", bus.name, bus.model_dump(exclude={"name"}))


@router.put("/buses/{name}")
def update_bus(name: str, bus: BusCreate):
    return apply_update_bus(name, bus, update_component=_update_component)


@router.delete("/buses/{name}", status_code=204)
def delete_bus(name: str):
    _delete_component("Bus", "buses", name)


@router.delete("/buses/{name}/cascade", status_code=204)
def delete_bus_cascade(name: str):
    apply_delete_bus_cascade(name)


@router.post("/buses/{name}/rename")
def rename_bus(name: str, body: dict):
    return apply_rename_bus(name, body)


# ── Carriers ─────────────────────────────────────────────────────────────────

@router.get("/carriers")
def get_carriers():
    return _get_component("Carrier", "carriers")


@router.post("/carriers", status_code=201)
def create_carrier(carrier: CarrierCreate):
    # `name` is Optional on the schema (so PUT bodies can omit it) — but a
    # POST without a name has nowhere to put the row. Reject up front.
    if not carrier.name:
        raise HTTPException(400, "Carrier name is required on POST.")
    return _create_component("Carrier", "carriers", carrier.name, carrier.model_dump(exclude={"name"}))


@router.put("/carriers/{name}")
def update_carrier(name: str, carrier: CarrierCreate):
    return _update_component("Carrier", "carriers", name, carrier.model_dump(exclude_unset=True))


@router.delete("/carriers/{name}", status_code=204)
def delete_carrier(name: str):
    _delete_component("Carrier", "carriers", name)


# ── Lines ────────────────────────────────────────────────────────────────────

@router.get("/lines")
def get_lines():
    return _get_component("Line", "lines")


@router.post("/lines", status_code=201)
def create_line(line: LineCreate):
    # Auto-fill length from haversine when the user didn't supply one.
    # PyPSA's default for `length` is 0.0, so we treat anything ≤ 0 as
    # "not set" and replace with the great-circle distance between the two
    # buses. A user-supplied positive value is left untouched — that's the
    # manual-override path.
    kwargs = line.model_dump(exclude={"name"})
    user_length = kwargs.get("length")
    needs_auto = user_length is None or (isinstance(user_length, (int, float)) and user_length <= 0)
    if needs_auto:
        n = PyPSAService.get_network()
        d = _line_haversine_km(n, str(kwargs.get("bus0", "")), str(kwargs.get("bus1", "")))
        if d is not None:
            kwargs["length"] = float(d)
    return _create_component("Line", "lines", line.name, kwargs)


@router.put("/lines/{name}")
def update_line(name: str, line: LineCreate):
    return _update_component("Line", "lines", name, line.model_dump(exclude_unset=True))


@router.delete("/lines/{name}", status_code=204)
def delete_line(name: str):
    _delete_component("Line", "lines", name)


@router.post("/lines/recalculate_lengths")
def recalculate_line_lengths():
    """Rewrite line lengths from bus coordinates; return rescale previews."""
    return apply_recalculate_line_lengths()


@router.post("/lines/rescale_impedances")
def rescale_impedances(req: ImpedanceRescaleRequest):
    """Write consented impedance values after a length rewrite."""
    return apply_rescale_impedances(req)


# ── Links ────────────────────────────────────────────────────────────────────

@router.get("/links")
def get_links():
    return _get_component("Link", "links")


@router.post("/links", status_code=201)
def create_link(link: LinkCreate):
    return _create_component("Link", "links", link.name, link.model_dump(exclude={"name"}))


@router.put("/links/{name}")
def update_link(name: str, link: LinkCreate):
    return _update_component("Link", "links", name, link.model_dump(exclude_unset=True))


@router.delete("/links/{name}", status_code=204)
def delete_link(name: str):
    _delete_component("Link", "links", name)


# ── Generators ───────────────────────────────────────────────────────────────

@router.get("/generators")
def get_generators():
    return _get_component("Generator", "generators")


@router.post("/generators", status_code=201)
def create_generator(gen: GeneratorCreate):
    return _create_component("Generator", "generators", gen.name, gen.model_dump(exclude={"name"}))


@router.put("/generators/{name}")
def update_generator(name: str, gen: GeneratorCreate):
    return _update_component("Generator", "generators", name, gen.model_dump(exclude_unset=True))


@router.delete("/generators/{name}", status_code=204)
def delete_generator(name: str):
    _delete_component("Generator", "generators", name)


# ── Storage Units ─────────────────────────────────────────────────────────────

@router.get("/storage_units")
def get_storage_units():
    return _get_component("StorageUnit", "storage_units")


@router.post("/storage_units", status_code=201)
def create_storage_unit(su: StorageUnitCreate):
    return _create_component("StorageUnit", "storage_units", su.name, su.model_dump(exclude={"name"}))


@router.put("/storage_units/{name}")
def update_storage_unit(name: str, su: StorageUnitCreate):
    return _update_component("StorageUnit", "storage_units", name, su.model_dump(exclude_unset=True))


@router.delete("/storage_units/{name}", status_code=204)
def delete_storage_unit(name: str):
    _delete_component("StorageUnit", "storage_units", name)


# ── Stores ────────────────────────────────────────────────────────────────────

@router.get("/stores")
def get_stores():
    return _get_component("Store", "stores")


@router.post("/stores", status_code=201)
def create_store(store: StoreCreate):
    return _create_component("Store", "stores", store.name, store.model_dump(exclude={"name"}))


@router.put("/stores/{name}")
def update_store(name: str, store: StoreCreate):
    return _update_component("Store", "stores", name, store.model_dump(exclude_unset=True))


@router.delete("/stores/{name}", status_code=204)
def delete_store(name: str):
    _delete_component("Store", "stores", name)


# ── Loads ─────────────────────────────────────────────────────────────────────

@router.get("/loads")
def get_loads():
    return _get_component("Load", "loads")


@router.post("/loads", status_code=201)
def create_load(load: LoadCreate):
    return _create_component("Load", "loads", load.name, load.model_dump(exclude={"name"}))


@router.put("/loads/{name}")
def update_load(name: str, load: LoadCreate):
    return _update_component("Load", "loads", name, load.model_dump(exclude_unset=True))


@router.delete("/loads/{name}", status_code=204)
def delete_load(name: str):
    _delete_component("Load", "loads", name)


# ── Transformers ──────────────────────────────────────────────────────────────

# ── Transformer presets ────────────────────────────────────────────────────────
# Common voltage steps used across European/North-American transmission. Any
# entry can be selected from the GUI dropdown to pre-fill v_nom_0/v_nom_1 (the
# expected bus voltages) plus a typical s_nom and per-unit reactance. The
# GUI's "Custom" option bypasses this list entirely.
_TRANSFORMER_PRESETS = [
    {"label": "380/220 kV",  "v_nom_0": 380.0, "v_nom_1": 220.0,  "s_nom": 600.0, "x": 0.08},
    {"label": "380/110 kV",  "v_nom_0": 380.0, "v_nom_1": 110.0,  "s_nom": 600.0, "x": 0.10},
    {"label": "380/132 kV",  "v_nom_0": 380.0, "v_nom_1": 132.0,  "s_nom": 600.0, "x": 0.10},
    {"label": "220/110 kV",  "v_nom_0": 220.0, "v_nom_1": 110.0,  "s_nom": 300.0, "x": 0.10},
    {"label": "220/132 kV",  "v_nom_0": 220.0, "v_nom_1": 132.0,  "s_nom": 300.0, "x": 0.10},
    {"label": "132/33 kV",   "v_nom_0": 132.0, "v_nom_1": 33.0,   "s_nom": 100.0, "x": 0.12},
    {"label": "132/20 kV",   "v_nom_0": 132.0, "v_nom_1": 20.0,   "s_nom": 60.0,  "x": 0.12},
    {"label": "110/33 kV",   "v_nom_0": 110.0, "v_nom_1": 33.0,   "s_nom": 80.0,  "x": 0.12},
    {"label": "110/20 kV",   "v_nom_0": 110.0, "v_nom_1": 20.0,   "s_nom": 60.0,  "x": 0.12},
    {"label": "33/11 kV",    "v_nom_0": 33.0,  "v_nom_1": 11.0,   "s_nom": 25.0,  "x": 0.10},
    {"label": "20/0.4 kV",   "v_nom_0": 20.0,  "v_nom_1": 0.4,    "s_nom": 1.0,   "x": 0.06},
]


@router.get("/transformers/types")
def list_transformer_types():
    """Return the catalogue of common voltage-step presets for the GUI."""
    return _TRANSFORMER_PRESETS


@router.get("/transformers")
def get_transformers():
    rows = _get_component("Transformer", "transformers")
    return _enrich_transformer_voltage(rows, PyPSAService.get_network())


@router.post("/transformers", status_code=201)
def create_transformer(tr: TransformerCreate):
    n = PyPSAService.get_network()
    _validate_transformer_voltage(n, tr.bus0, tr.bus1, tr.v_nom_0, tr.v_nom_1)
    # v_nom_0/v_nom_1 are validation hints — strip before handing to PyPSA
    # (Transformer doesn't have those attributes; voltages live on the buses).
    payload = tr.model_dump(exclude={"name", "v_nom_0", "v_nom_1"})
    payload = _sanitise_transformer_type(n, payload)
    return _create_component("Transformer", "transformers", tr.name, payload)


@router.put("/transformers/{name}")
def update_transformer(name: str, tr: TransformerCreate):
    n = PyPSAService.get_network()
    _validate_transformer_voltage(n, tr.bus0, tr.bus1, tr.v_nom_0, tr.v_nom_1)
    payload = tr.model_dump(exclude={"v_nom_0", "v_nom_1"}, exclude_unset=True)
    payload = _sanitise_transformer_type(n, payload)
    return _update_component("Transformer", "transformers", name, payload)


@router.delete("/transformers/{name}", status_code=204)
def delete_transformer(name: str):
    _delete_component("Transformer", "transformers", name)


# ── Shunt Impedances ──────────────────────────────────────────────────────────

@router.get("/shunt_impedances")
def get_shunt_impedances():
    return _get_component("ShuntImpedance", "shunt_impedances")


@router.post("/shunt_impedances", status_code=201)
def create_shunt(shunt: ShuntImpedanceCreate):
    return _create_component("ShuntImpedance", "shunt_impedances", shunt.name, shunt.model_dump(exclude={"name"}))


@router.put("/shunt_impedances/{name}")
def update_shunt(name: str, shunt: ShuntImpedanceCreate):
    return _update_component("ShuntImpedance", "shunt_impedances", name, shunt.model_dump(exclude_unset=True))


@router.delete("/shunt_impedances/{name}", status_code=204)
def delete_shunt(name: str):
    _delete_component("ShuntImpedance", "shunt_impedances", name)


# ── Snapshots ─────────────────────────────────────────────────────────────────















# ── Investment Periods ────────────────────────────────────────────────────────







# ── Global constraints ───────────────────────────────────────────────────────
# Network-wide policy constraints stored in `n.global_constraints`. The five
# canonical PyPSA types are:
#   • primary_energy                  — CO2 cap, fuel-use cap, etc.
#   • transmission_volume_expansion_limit
#   • transmission_expansion_cost_limit
#   • tech_capacity_expansion_limit
#   • operational_limit
#
# We expose CRUD via `n.add("GlobalConstraint", ...)` / `n.remove(...)` so the
# constraints survive netcdf round-trip with the rest of the network.

from models.schemas import GlobalConstraintCreate

@router.get("/global_constraints")
def get_global_constraints():
    # Route through the generic helper so the transient-row filter runs
    # too. Today no solver-internal mutation registers GlobalConstraints
    # as transient, so the filter is a no-op — but if a future
    # _apply_modelling_assumptions step starts adding scaffolding
    # constraints (e.g. SCLOPF transient cuts), this endpoint will
    # already hide them without needing another edit.
    return _get_component("GlobalConstraint", "global_constraints")


@router.post("/global_constraints", status_code=201)
def create_global_constraint(body: GlobalConstraintCreate):
    return apply_create_global_constraint(body)


@router.put("/global_constraints/{name}")
def update_global_constraint(name: str, body: GlobalConstraintCreate):
    return apply_update_global_constraint(
        name, body, merge_partial_update=_merge_partial_update,
    )


@router.delete("/global_constraints/{name}", status_code=204)
def delete_global_constraint(name: str):
    apply_delete_global_constraint(name)


# ── Network Meta ──────────────────────────────────────────────────────────────

@router.get("/meta")
def get_meta():
    n = PyPSAService.get_network()
    return _meta_payload(n, PyPSAService.get_loaded_project())


@router.put("/meta")
def update_meta(meta: NetworkMeta):
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        n.name = meta.name
    return {"name": n.name}




@router.post("/reset")
def reset_network(
    db: DBSession = Depends(get_db),
    session: SessionRow | None = Depends(current_session),
):
    with PyPSAService.get_lock():
        PyPSAService.reset_network()
    # Atomic clear under the lock — concurrent serialise/iterate paths are safe.
    with _user_ts_lock:
        _user_ts.clear()
    from services import dirty_state, undo_service
    undo_service.clear()
    dirty_state.clear()  # memory and disk now agree
    # Step 0b: "New Project" un-binds the SESSION, not the process. Leaving the
    # pointer set would make the very next request re-resolve the old project
    # and hydrate it back on top of the network the user just cleared — the
    # reset would appear to silently undo itself.
    if session is not None:
        active_project.set_active_project(db, session, None)
    return {"status": "reset"}


# ── Bulk update ─────────────────────────────────────────────────────────────
# One round-trip mutation that sets the same field(s) on N components. Used by
# the bottom-panel multi-select edit bar — without this, setting p_min_pu on
# 500 generators would be 500 sequential PUTs, 500 audit entries, and 500
# query invalidations. Here it's one lock acquisition, one audit entry, one
# undo snapshot.
#
# Mutation shape: direct DataFrame write `df.loc[names, col] = value`. This
# bypasses PyPSA's add() type coercion path, which is the right call for
# numeric value updates (the common case) — but means renames and structural
# changes (e.g. flipping `committable`) aren't supported here. For those, the
# client should fall through to per-row PUT.



# Bulk edit body lives in services.network_bulk (apply_bulk_update + coerce /
# finite-default helpers). This module keeps the FastAPI route and re-exports
# the helpers so chat_tools / tests keep importing from routers.network.
# Spec: docs/superpowers/specs/2026-09-13-network-bulk-router-lift-design.md


@router.patch("/_bulk")
def bulk_update(body: dict) -> dict:
    """Bulk edit. Two request shapes, one implementation.

    `names` + `updates` applies ONE set of values to many rows; `rows` (spec
    D9) applies a DIFFERENT set per row. They are mutually exclusive, and
    everything after validation treats them as a list of batches so the
    coercion below — the only place that knows a column's dtype rules — has
    exactly one implementation rather than one per shape.
    """
    return apply_bulk_update(body)


# ── Undo stack ─────────────────────────────────────────────────────────────────
# Bodies live in services.network_undo. main.py middleware imports
# `_push_undo_snapshot` by name; chat_tools imports `undo_last` / `undo_info`.
# Spec: docs/superpowers/specs/2026-09-13-network-undo-router-lift-design.md
from services.network_undo import (  # noqa: E402,F401
    apply_undo,
    get_undo_info,
    push_undo_snapshot as _push_undo_snapshot,
)


@router.get("/undo/info")
def undo_info():
    """
    Return undo-stack telemetry: depth + memory usage.

    `memory_bytes` / `max_bytes` let the frontend surface a "Undo memory:
    X / Y MB" hint when the stack is approaching the byte budget — useful
    on multi-period sector-coupled networks where each snapshot is large
    enough that the byte-eviction path can trim deep undo history
    invisibly otherwise.
    """
    return get_undo_info()


@router.post("/undo")
def undo_last():
    """Restore the network to the state before the most recent mutating operation."""
    return apply_undo()




# ── Attribute catalog ─────────────────────────────────────────────────────────

@router.get("/catalog/{component}")
def get_attribute_catalog(component: str) -> dict:
    """
    PyPSA's own attribute metadata for one component class (spec D3, D24).

    Class-level and immutable at runtime, which is why the client caches it
    under the unscoped key ['catalog', component] with staleTime: Infinity.
    All catalog logic lives in services/attribute_catalog.py; this stays thin
    per .cursor/rules/pypsa-gui-backend.mdc:10-12.
    """
    n = PyPSAService.get_network()
    try:
        attributes = attribute_catalog.catalog_for(n, component)
    except KeyError:
        raise HTTPException(
            400,
            f"Unknown component '{component}'. Expected one of: "
            f"{', '.join(attribute_catalog.known_components())}.",
        )
    return {"component": component, "attributes": attributes}


# ── Time Series ───────────────────────────────────────────────────────────────















