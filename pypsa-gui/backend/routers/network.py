from __future__ import annotations

import math
from typing import Any

import pandas as pd
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
from services import active_project, attribute_catalog, change_log_service, vintage_service
from services.carrier_catalog import ensure_carrier
from services.adequacy.occurrence import BLANK_SPELLINGS as _BLANK_SPELLINGS
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
from services.serialization import df_to_json

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
# stores. The ~80 CRUD routes and their factory deliberately stay in this module.
# `bulk_update` stays as a thin FastAPI handler; its body is
# `services.network_bulk.apply_bulk_update` (helpers re-exported below).
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

def _serialize_component(
    n: Any, attr: str, transient: set[str]
) -> list[dict]:
    """
    Serialise a PyPSA component DataFrame for the frontend, hiding the
    solver-only transient rows named in `transient` (vintage clones, VOLL
    slacks) so the user never sees them in the asset tables.

    Network-agnostic core shared by the active-network shim (`_get_component`,
    which passes the active/solving ctx's transient set) and the B6
    path-scoped route (which passes the INJECTED ctx's set, so a resident
    project's solver internals are hidden per-project). The caller owns
    resolving `transient` from the right context — this fn only filters.
    """
    df = getattr(n, attr)
    if not df.empty and transient:
        # Drop rows whose index name is in the transient set. Use
        # difference() rather than `~isin(...)` for stability when
        # the DataFrame has a small number of transients and a
        # large number of real rows.
        keep_idx = df.index.difference(pd.Index(list(transient)))
        df = df.loc[keep_idx]
    rows = df_to_json(df)
    # An unset outage basis is `null` on the wire whichever way the frame
    # spells it — NaN before a save, "" after a netCDF round trip of a mixed
    # column (whole-branch review, M13) — so a row read here can be sent
    # back unchanged.
    if "outage_rate_basis" in df.columns:
        for row in rows:
            v = row.get("outage_rate_basis")
            if isinstance(v, str) and v.strip() in _BLANK_SPELLINGS:
                row["outage_rate_basis"] = None
    return rows


def _get_component(component_class: str, attr: str) -> list[dict]:
    """
    Serialise the ACTIVE network's component DataFrame for the frontend,
    filtering out solver-only transient rows (vintage clones, VOLL slacks)
    so the user never sees them in the asset tables.

    Reads never acquire the PyPSA lock (per the project's read-never-locks
    policy), so during a solve the worker thread has already populated
    `n.generators` with `__voll_<bus>` rows (convention:
    services/adequacy/slack.py) and `n.links` with
    `parent@<year>` vintages. Without this filter those leak into every
    /api/network/{component} response and confuse the user — they appear
    as "extra" assets that vanish once the LP completes.

    The transient registry on `PyPSAService` is the source of truth:
    apply_vintage_bounds + the VOLL slack code mark each added row, and
    the restore() callbacks unmark on removal. We short-circuit on the
    common case (empty registry) so the healthy path costs one dict
    lookup. The actual serialise + filter is delegated to
    `_serialize_component` so the B6 path-scoped route can reuse it against
    a non-active context's network + transient set.
    """
    n = PyPSAService.get_network()
    transient: set[str] = set()
    if PyPSAService.has_any_transient_rows():
        transient = PyPSAService.get_transient_rows(component_class)
    return _serialize_component(n, attr, transient)


def _meta_payload(n: Any, loaded_project: str | None) -> dict:
    """
    The /network/meta response shape for a given network + its on-disk
    binding. `name` is the (mutable) display title; `loaded_project` is the
    authoritative on-disk binding the save path enforces — None when the
    network is unbound (fresh / never loaded). Clients comparing identity
    should use `loaded_project`, not `name`.

    Shared by the active-network shim (`GET /api/network/meta`) and the B6
    path-scoped `GET /api/projects/{id}/network/meta`, so the two payloads
    can't drift.
    """
    return {
        "name": n.name,
        "loaded_project": loaded_project,
        "snapshot_count": len(n.snapshots),
        "bus_count": len(n.buses),
    }




# Moved to `services/transient_rows.py` in Phase 5: `routers/network_time_axis.py`
# needs it too, and a router it was split out of is not something it may import
# back from. The alias keeps the private name working for this module's own
# call sites.
_filter_transient_names = filter_transient_names


def _drop_unknown_extras(component_class: str, attr: str, kwargs: dict) -> dict:
    """
    Drop any key PyPSA does not recognise for this component class (spec D21).

    Pydantic's `extra='allow'` lets an undeclared key survive
    `model_dump(exclude_unset=True)` so a newly-exposed attribute can persist
    instead of being silently ignored. Without a whitelist that same setting
    would let an arbitrary key reach `n.add()`, so the two ship together.

    A key passes if the catalog reports it as an Input attribute, OR it is
    already a column on the frame. The second arm is what preserves today's
    behaviour for fields a Create model declares but PyPSA marks Output —
    narrowing to catalog-Input alone would be a silent behaviour change.
    """
    n = PyPSAService.get_network()
    allowed = attribute_catalog.input_attributes(n, component_class)
    if not allowed:
        return kwargs
    columns = set(getattr(n, attr).columns)
    return {k: v for k, v in kwargs.items() if k in allowed or k in columns}


def _normalise_flag_column(n, attr: str) -> None:
    """Phase 12h: keep `p_max_pu_includes_outages` a real `bool` column.

    A first `n.add` on a frame that lacks the column creates it as `object`,
    and an `object` column of PURE bools is the one shape netCDF refuses
    (`unsupported dtype for netCDF4 variable: bool`) — so the next project
    save is a 500 and the undo snapshot fails silently. Called at every
    boundary that can add a row or replace a frame; a no-op on anything but
    generators, and 0.17 ms on a 300-row frame.
    """
    if attr != "generators":
        return
    try:
        from services.adequacy.occurrence import normalise_flag_column
        normalise_flag_column(n)
    except Exception:                                         # noqa: BLE001
        pass


def _create_component(component_class: str, attr: str, name: str, kwargs: dict) -> dict:
    # Dispatch invalidation lives in the undo middleware (main.py) — it runs
    # after every successful /api/network/* mutation, so cascade-delete,
    # /bulk writes, rename, and global-constraint mutations all benefit
    # without each having to call an invalidation helper here.
    n = PyPSAService.get_network()
    kwargs = _drop_unknown_extras(component_class, attr, kwargs)
    with PyPSAService.get_lock():
        df = getattr(n, attr)
        if name in df.index:
            raise HTTPException(409, f"{component_class} '{name}' already exists")
        if component_class != "Carrier":
            ensure_carrier(n, kwargs.get("carrier", ""))
        n.add(component_class, name, **kwargs)
        _normalise_flag_column(n, attr)
    change_log_service.log("add", component_class, name, f"Added {component_class.lower()} '{name}'")
    return {"name": name}


def _merge_partial_update(n, attr: str, name: str, submitted: dict) -> dict:
    """
    Merge a partial PUT onto the existing row for a remove+add update.

    Reads the current INPUT-column values from `n.{attr}.loc[name]` (PyPSA
    distinguishes input vs output cols via `components.<attr>.defaults["status"]`;
    fall back to all columns), drops non-finite floats (n.add fills its own
    defaults; passing NaN upcasts to object dtype), and overlays `submitted`
    (the user's `model_dump(exclude_unset=True)`). Fields the user didn't send
    keep their current value instead of resetting to schema defaults — the
    partial-PUT footgun. Caller holds the PyPSA lock and has validated `name`.
    """
    df = getattr(n, attr)
    try:
        defaults = getattr(n.components, attr).defaults
        mask = defaults["status"].str.startswith("Input", na=False)
        input_cols = list(defaults.index[mask])
        # AND custom GUI-added columns (curtailment_cost, etc.) — any column on
        # the DataFrame that PyPSA's defaults don't know about. They are inputs
        # by construction (the GUI put them there), but they never appear in
        # `defaults`, so filtering on `defaults` alone drops them from `current`
        # and the remove+add cycle silently resets them on every partial PUT.
        # Mirrors the same widening in services/vintage_service.py.
        known_defaults = set(defaults.index)
        input_cols += [c for c in df.columns if c not in known_defaults]
    except Exception:
        input_cols = list(df.columns)
    current = {c: df.at[name, c] for c in input_cols if c in df.columns}
    current = {k: v for k, v in current.items()
               if not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))}
    return {**current, **submitted}


def _detach_component_series(n, attr: str, name: str) -> list[tuple[str, "pd.Series"]]:
    """Every time-varying INPUT column this component owns, copied out before
    a remove+add drops it (IEEE 39-bus review, F2).

    ``_update_component`` updates by ``n.remove`` + ``n.add``, and PyPSA drops
    the component's columns from every ``n.<attr>_t`` table when it is
    removed — so saving a generator's row from the Properties panel, with no
    field changed, silently deleted its availability profile. Measured on the
    IEEE 39-bus network: the 500 MW wind farm became a firm must-take at
    ``p_max_pu`` 1.0, the COPT LOLE fell 0.831 h -> 0.319 h, its ELCC
    candidate nameplate went 306 MW -> 500 MW, and the next solve was refused
    by the margin's own `reserve_margin_unpriceable_assets`. Nothing warned:
    the Time Series view is served from the saved project and still showed the
    profile.

    INPUT attributes only, the same filter ``_backup_network_ts_to_user_ts``
    documents: an edit invalidates the solve, so carrying a stale ``_t.p``
    across it would leave one component holding dispatch the rest of the
    network no longer has. If the component defaults cannot be read, every
    column is carried — preserving is strictly safer than dropping, which is
    the behaviour this exists to end.
    """
    ts_store = getattr(n, f"{attr}_t", None)
    if ts_store is None:
        return []
    input_attrs: set[str] | None = None
    try:
        comp_defaults = getattr(n.components, attr).defaults
        mask = comp_defaults["status"].astype(str).str.startswith("Input", na=False)
        input_attrs = set(comp_defaults.index[mask])
    except Exception:                                         # noqa: BLE001
        input_attrs = None
    saved: list[tuple[str, pd.Series]] = []
    try:
        ts_attrs = list(ts_store.keys()) if hasattr(ts_store, "keys") else []
    except Exception:                                         # noqa: BLE001
        return []
    for ts_attr in ts_attrs:
        if input_attrs is not None and ts_attr not in input_attrs:
            continue
        df = (ts_store.get(ts_attr) if hasattr(ts_store, "get")
              else getattr(ts_store, ts_attr, None))
        if df is None or not hasattr(df, "columns") or name not in df.columns:
            continue
        try:
            saved.append((ts_attr, df[name].copy()))
        except Exception:                                     # noqa: BLE001
            continue
    return saved


def _reattach_component_series(n, attr: str, name: str,
                               saved: list[tuple[str, "pd.Series"]]) -> None:
    """Put back what ``_detach_component_series`` took out, under the SAME
    name — a rename runs afterwards through ``rename_component_names``, which
    re-keys the ``_t`` columns with everything else that refers to the
    component (F2)."""
    if not saved:
        return
    ts_store = getattr(n, f"{attr}_t", None)
    if ts_store is None:
        return
    for ts_attr, series in saved:
        df = (ts_store.get(ts_attr) if hasattr(ts_store, "get")
              else getattr(ts_store, ts_attr, None))
        if df is None or not hasattr(df, "columns"):
            continue
        try:
            df[name] = series.reindex(df.index)
        except Exception:                                     # noqa: BLE001
            continue


def _rename_component_safely(n, component_class: str, old: str, new: str) -> None:
    """Rename a component, re-pointing whatever refers to it — without the
    ``KeyError`` PyPSA raises for every class but ``Bus``.

    ``rename_component_names`` renames the static index and the dynamic
    columns, then walks every component re-pointing cross references. That
    walk derives the column from the RENAMED class and asks each component
    for one per port:

        col = self.name.lower()                       # "generator"
        cols = [f"{col}{port}" for port in component.ports]
        component.static[cols] = component.static[cols].replace(kwargs)

    which holds only when the class's own name IS a port column. That is true
    of ``Bus`` (`bus`, `bus0`, `bus1`) and of nothing else: a Generator rename
    looks for a `generator` column, a Line rename for `line0`/`line1`, and a
    Carrier rename for `carrier0`/`carrier1` on Lines — each a KeyError, so
    renaming a generator from the Properties panel was a 500. Verified on
    PyPSA 1.1.2 (the pinned version) and 1.3.0; PyPSA's own source carries a
    "TODO: Generalize" on that line (IEEE 39-bus review, F9).

    The predicate is the walk's own precondition rather than a hardcoded
    "Bus": where every derived column exists, PyPSA's function runs and
    re-points dependents as before; where one does not, the walk would have
    nothing to re-point anyway — no component carries a `generator` column —
    so the rename is completed here exactly as PyPSA does it before that walk,
    the static index and every dynamic column. Our own references (the vintage
    bounds and the `_user_ts` keys) are re-keyed by the callers, as they were.
    """
    col = component_class.lower()
    for comp in n.components:
        ports = list(getattr(comp, "ports", None) or [])
        if not ports or comp.static.empty:
            continue
        if not all(f"{col}{port}" in comp.static.columns for port in ports):
            break
    else:
        n.rename_component_names(component_class, **{old: new})
        return

    comp = n.components[component_class]
    comp.static = comp.static.rename(index={old: new})
    for key in list(comp.dynamic.keys()):
        comp.dynamic[key] = comp.dynamic[key].rename(columns={old: new})


def _update_component(component_class: str, attr: str, name: str, kwargs: dict) -> dict:
    """
    Update by remove+add. `kwargs` should be the user's *partial* dict
    (produced via `model_dump(exclude_unset=True)`). Reads the current row from
    `n.{attr}.loc[name]` and merges the user's fields on top — fields the user
    didn't send keep their current values instead of resetting to Pydantic
    defaults. This avoids the partial-PUT footgun where a one-line `{"control":
    "PV"}` PUT would otherwise wipe `marginal_cost`, `p_nom`, etc. to schema
    defaults via the destructive remove+add cycle.
    """
    n = PyPSAService.get_network()
    kwargs = _drop_unknown_extras(component_class, attr, kwargs)
    with PyPSAService.get_lock():
        df = getattr(n, attr)
        if name not in df.index:
            raise HTTPException(404, f"{component_class} '{name}' not found")
        # Read current row + overlay the user's partial dict (shared helper).
        merged = _merge_partial_update(n, attr, name, kwargs)
        if component_class != "Carrier":
            ensure_carrier(n, merged.get("carrier", ""))
        new_name = merged.pop("name", name)
        # Refuse to rename onto an occupied name. Without this the remove+add
        # below silently destroyed the source component and (once the rename
        # goes through PyPSA) would drag its dependents onto the target — a
        # merge the user never asked for, reported as a 200.
        if new_name != name and new_name in df.index:
            raise HTTPException(409, f"{component_class} '{new_name}' already exists")
        # F2: PyPSA drops this component's columns from every `_t` table on
        # remove, so carry them across the remove+add. Taken BEFORE the
        # remove and put back straight after the add, under the old name, so
        # the rename below re-keys them with everything else.
        saved_series = _detach_component_series(n, attr, name)
        n.remove(component_class, name)
        # Re-add under the OLD name and rename separately. A rename by
        # remove+add does NOT re-point the components that REFER to this one:
        # `loads.bus`, `generators.bus`, `lines.bus0/bus1` (and `carrier` on
        # everything, for a Carrier rename) keep the old string, so renaming a
        # bus orphaned everything attached to it. The orphans are invisible
        # until the preflight reports `bus_ref_unknown`, and contribute nothing
        # to the solve in the meantime. PyPSA's `rename_component_names` is the
        # primitive that re-points dependents — and it also invalidates the
        # cached `n.components` accessors and sub-network membership that a
        # manual column rewrite would leave stale. `POST /buses/{name}/rename`
        # already used it; this path is the one the Properties panel's edit
        # cards take, and it did not.
        n.add(component_class, name, **merged)
        _reattach_component_series(n, attr, name, saved_series)
        # Re-key any saved per-period bounds so the modal data follows the
        # rename instead of stranding under the old key.
        if new_name != name:
            _rename_component_safely(n, component_class, name, new_name)
            vintage_service.rename_asset(n, component_class, name, new_name)
            # Same fix for the time-series store — _user_ts keys carry the
            # column name, and without this the profile would be silently
            # lost on the next save+reload (re-apply skips entries whose
            # column is no longer in the network DataFrame).
            _user_ts_rename_asset(attr, name, new_name)
        _normalise_flag_column(n, attr)
    desc = (f"Renamed {component_class.lower()} '{name}' → '{new_name}'"
            if new_name != name else f"Updated {component_class.lower()} '{name}'")
    change_log_service.log("update", component_class, new_name, desc)
    return {"name": new_name}


def _delete_component(component_class: str, attr: str, name: str) -> None:
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        df = getattr(n, attr)
        if name not in df.index:
            raise HTTPException(404, f"{component_class} '{name}' not found")
        n.remove(component_class, name)
        # Drop any saved per-period bounds for the now-gone asset so the
        # vintage_bounds dict doesn't keep stale entries that the solver would
        # try (and fail) to expand at next solve.
        vintage_service.delete_bounds_for_asset(n, component_class, name)
        # Drop _user_ts entries too — without this they accumulate forever
        # in project saves and a future component reusing the same name
        # inherits the deleted asset's profile.
        _user_ts_delete_asset(attr, name)
    change_log_service.log("delete", component_class, name, f"Deleted {component_class.lower()} '{name}'")


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
    """
    Rewrite n.lines.length (km) from haversine distance between bus0 / bus1
    coordinates. Buses without a usable (x, y) pair are skipped — their lines'
    length stays unchanged. Returns counts so the frontend can show a summary.

    Triggered by the "Recalculate from coordinates" button in the map toolbar.
    The user is expected to acknowledge that this overrides existing length
    values (which feed length-scaled capital_cost models downstream).
    """
    n = PyPSAService.get_network()
    if n.lines.empty:
        return {"updated": 0, "skipped": 0, "total": 0, "rescale": []}

    updated = 0
    skipped = 0
    previews: list[dict] = []
    with PyPSAService.get_lock():
        for line_name in n.lines.index:
            b0 = str(n.lines.at[line_name, "bus0"]) if "bus0" in n.lines.columns else ""
            b1 = str(n.lines.at[line_name, "bus1"]) if "bus1" in n.lines.columns else ""
            d_km = _line_haversine_km(n, b0, b1)
            if d_km is None:
                skipped += 1
                continue
            old_length = float(n.lines.at[line_name, "length"])
            old = {k: float(n.lines.at[line_name, k]) for k in _IMPEDANCE_FIELDS}
            n.lines.at[line_name, "length"] = float(d_km)
            updated += 1
            p = _impedance_preview(str(line_name), old_length, float(d_km), old)
            if p is not None:
                previews.append(p)

    change_log_service.log(
        "update", "Lines", "(haversine)",
        f"Recalculated line lengths from bus coordinates: {updated} updated, {skipped} skipped",
    )
    return {"updated": updated, "skipped": skipped, "total": int(len(n.lines)), "rescale": previews}


@router.post("/lines/rescale_impedances")
def rescale_impedances(req: ImpedanceRescaleRequest):
    """
    Write the previewed impedances for an explicit list of lines.

    Deliberately takes the VALUES rather than recomputing them: by the time the
    user consents, the length has already been rewritten, so the old per-km is
    no longer derivable from the network. Recomputing here would silently use
    the new length as the old one and scale by 1.
    """
    n = PyPSAService.get_network()
    updated = 0
    skipped: list[dict] = []
    with PyPSAService.get_lock():
        for entry in req.lines:
            if entry.name not in n.lines.index:
                skipped.append({"name": entry.name, "reason": "unknown-line"})
                continue
            n.lines.at[entry.name, "r"] = float(entry.r)
            n.lines.at[entry.name, "x"] = float(entry.x)
            n.lines.at[entry.name, "b"] = float(entry.b)
            updated += 1
    if updated:
        change_log_service.log(
            "update", "Lines", "(rescale)",
            f"Rescaled impedance on {updated} line(s) to preserve per-km values after a length change",
        )
    return {"updated": updated, "skipped": skipped}


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

_GC_OPTIONAL = ("carrier_attribute", "carrier", "investment_period")


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
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if body.name in n.global_constraints.index:
            raise HTTPException(409, f"GlobalConstraint '{body.name}' already exists")
        kwargs: dict[str, Any] = {
            "type": body.type,
            "sense": body.sense,
            "constant": float(body.constant),
        }
        for opt in _GC_OPTIONAL:
            v = getattr(body, opt, None)
            # Empty strings should not become column entries either — PyPSA
            # treats "" the same as None for these optional fields.
            if v is None or v == "":
                continue
            kwargs[opt] = v
        n.add("GlobalConstraint", body.name, **kwargs)
    change_log_service.log(
        "add", "GlobalConstraint", body.name,
        f"Added global constraint '{body.name}' ({body.type} {body.sense} {body.constant})",
    )
    return {"name": body.name}


@router.put("/global_constraints/{name}")
def update_global_constraint(name: str, body: GlobalConstraintCreate):
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.global_constraints.index:
            raise HTTPException(404, f"GlobalConstraint '{name}' not found")
        # Real partial-PUT: same pattern as _update_component for the regular
        # component CRUD. Reads the existing row, merges `exclude_unset=True`
        # on top, so a body of `{"constant": 100}` ONLY changes constant
        # instead of resetting `type`/`sense`/`carrier_attribute`/period to
        # the Pydantic schema defaults (which was the B2 footgun before).
        # NOTE: deliberately NOT routed through _update_component — that injects
        # ensure_carrier (wrong for a GlobalConstraint). Shared MERGE only.
        merged = _merge_partial_update(
            n, "global_constraints", name, body.model_dump(exclude_unset=True)
        )
        new_name = merged.pop("name", name)
        n.remove("GlobalConstraint", name)
        n.add("GlobalConstraint", new_name, **merged)
    change_log_service.log(
        "update", "GlobalConstraint", new_name,
        f"Updated global constraint '{name}' → '{new_name}'",
    )
    return {"name": new_name}


@router.delete("/global_constraints/{name}", status_code=204)
def delete_global_constraint(name: str):
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.global_constraints.index:
            raise HTTPException(404, f"GlobalConstraint '{name}' not found")
        n.remove("GlobalConstraint", name)
    change_log_service.log(
        "delete", "GlobalConstraint", name,
        f"Deleted global constraint '{name}'",
    )


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















