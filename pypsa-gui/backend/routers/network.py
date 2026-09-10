from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session as DBSession
from fastapi.responses import StreamingResponse
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
from services import active_project, change_log_service, vintage_service
from services.carrier_catalog import ensure_carrier
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
from services.upload_guard import read_capped
from services.http_filenames import content_disposition

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
# stores. The ~80 CRUD routes, their factory and `_xlsx_response` deliberately
# stay in this module.
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

router = APIRouter()

# ── Phase 5 façade: the time-axis routes ─────────────────────────────────────
# `services/chat_tools.py` imports fourteen of these handlers BY NAME and calls
# them in-process, so this module stays their import surface. At the TOP, with
# the other imports: a module body executes top to bottom, and a re-export at
# the bottom is not bound yet for anything above it that references it.
from routers.network_time_axis import (  # noqa: E402,F401
    _ATTR_TO_CLASS,
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
            if isinstance(v, str) and v.strip() in ("", "nan", "None"):
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


# Map tab/component-class names → PyPSA's network-attribute name. The frontend
# only ever knows the component class (e.g. "Generator"), so the bulk endpoint
# resolves the corresponding DataFrame here. Keeps the API contract narrow:
# the client doesn't need to know PyPSA's internal attribute conventions.
_COMPONENT_ATTRS: dict[str, str] = {
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


# ── Geometry helpers ─────────────────────────────────────────────────────────
# Used by line auto-length: on line create, and on any bus x/y change so the
# line lengths track the geometry. Manual edits via PUT /lines/{name} are
# respected — the user can still override the auto value.


def _xlsx_response(df: pd.DataFrame, fname: str) -> StreamingResponse:
    """
    Serialise `df` to an .xlsx StreamingResponse with a safely-encoded
    attachment filename. Shared tail of the load/generator/link profile-template
    download endpoints.

    The filename embeds a COMPONENT NAME, and component names are created
    through `POST /api/network/loads` (and friends) with no character
    validation at all — so a load called `ev"il` used to close the header's
    quoted-string early, and one containing a newline made uvicorn raise
    `RuntimeError: Invalid HTTP header value.` mid-send and the browser get an
    empty reply. `content_disposition` is byte-identical to the old f-string
    for every ordinary template name.
    """
    buf = io.BytesIO()
    df.to_excel(buf, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(fname)},
    )


# ── Buses ────────────────────────────────────────────────────────────────────

@router.get("/buses")
def get_buses():
    return _get_component("Bus", "buses")


@router.post("/buses", status_code=201)
def create_bus(bus: BusCreate):
    return _create_component("Bus", "buses", bus.name, bus.model_dump(exclude={"name"}))


@router.put("/buses/{name}")
def update_bus(name: str, bus: BusCreate):
    # Detect coordinate change BEFORE the in-place rebuild so we can decide
    # whether to recompute connected line lengths. Comparing post-update would
    # be a tautology (we'd just compare new vs new).
    #
    # Read x/y from the exclude-unset dump (NOT from `bus.x` / `bus.y`) so a
    # partial PUT that didn't touch coordinates doesn't trigger phantom
    # haversine recomputes. `BusCreate` declares `x: float = 0.0` /
    # `y: float = 0.0` as non-Optional defaults — without this guard, a body
    # like `{"control": "PV"}` arrives with `bus.x = 0.0` (Pydantic default)
    # and `coord_changed = (old_x != 0.0)` fires for every non-origin bus,
    # rewriting every connected line's length to the haversine distance to
    # (0, 0). Symptom: fleet of broken line lengths after editing a single
    # bus attribute.
    n = PyPSAService.get_network()
    submitted = bus.model_dump(exclude_unset=True)
    x_submitted = "x" in submitted
    y_submitted = "y" in submitted
    coord_changed = False
    if name in n.buses.index and (x_submitted or y_submitted):
        try:
            old_x = float(n.buses.at[name, "x"])
            old_y = float(n.buses.at[name, "y"])
            if x_submitted:
                new_x = float(submitted["x"]) if submitted["x"] is not None else float("nan")
                if old_x != new_x:
                    coord_changed = True
            if not coord_changed and y_submitted:
                new_y = float(submitted["y"]) if submitted["y"] is not None else float("nan")
                if old_y != new_y:
                    coord_changed = True
        except Exception:
            coord_changed = True
    result = _update_component("Bus", "buses", name, submitted)
    # Auto-rewrite line lengths for any line touching the moved bus. The user
    # can still override later via PUT /lines/{name}. Use the post-update name
    # (rename-aware) so we hit the renamed bus, not its ghost.
    rescale: list[dict] = []
    if coord_changed:
        new_name = result.get("name", name)
        with PyPSAService.get_lock():
            recompute = _recompute_lengths_for_bus(n, new_name)
        rescale = recompute.previews
        # Log the true rewrite count, not len(rescale): a zero-impedance line
        # still has its length rewritten but _impedance_preview omits it (no
        # rescale to offer), so len(rescale) alone would undercount whenever
        # such a line is among the ones touched.
        if recompute.updated:
            change_log_service.log(
                "update", "Lines", "(auto)",
                f"Auto-rewrote {recompute.updated} line length(s) after bus '{new_name}' moved",
            )
    if isinstance(result, dict):
        result = {**result, "rescale": rescale}
    return result


@router.delete("/buses/{name}", status_code=204)
def delete_bus(name: str):
    _delete_component("Bus", "buses", name)


@router.delete("/buses/{name}/cascade", status_code=204)
def delete_bus_cascade(name: str):
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.buses.index:
            raise HTTPException(404, f"Bus '{name}' not found")
        for cls, attr, cols in [
            ("Line", "lines", ["bus0", "bus1"]),
            ("Link", "links", ["bus0", "bus1"]),
            ("Transformer", "transformers", ["bus0", "bus1"]),
        ]:
            df = getattr(n, attr, None)
            if df is not None and not df.empty:
                mask = df[cols[0]].eq(name) | df[cols[1]].eq(name)
                for comp in df[mask].index.tolist():
                    n.remove(cls, comp)
        for cls, attr in [
            ("Generator", "generators"), ("Load", "loads"),
            ("StorageUnit", "storage_units"), ("Store", "stores"),
        ]:
            df = getattr(n, attr, None)
            if df is not None and not df.empty and "bus" in df.columns:
                for comp in df[df.bus.eq(name)].index.tolist():
                    n.remove(cls, comp)
        n.remove("Bus", name)
    change_log_service.log("delete", "Bus", name, f"Deleted bus '{name}' and all connected components")


@router.post("/buses/{name}/rename")
def rename_bus(name: str, body: dict):
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name cannot be empty")
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.buses.index:
            raise HTTPException(404, f"Bus '{name}' not found")
        if new_name != name and new_name in n.buses.index:
            raise HTTPException(409, f"Bus '{new_name}' already exists")
        if new_name == name:
            return {"old_name": name, "new_name": new_name}
        change_log_service.log("update", "Bus", new_name, f"Renamed bus '{name}' → '{new_name}'")
        # PyPSA 1.x's rename_component_names handles bus reference updates on
        # every dependent component (lines/links/transformers bus0/bus1,
        # generators/loads/storage_units/stores bus) AND invalidates the
        # cached `n.components` accessors + sub-network membership. The
        # previous manual `df.replace` path left those caches stale, so a
        # subsequent `n.statistics()` would silently return wrong numbers
        # for any aggregation that walked sub_networks (P0 data integrity).
        n.rename_component_names("Bus", **{name: new_name})
    return {"old_name": name, "new_name": new_name}


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
    from services import undo_service
    undo_service.clear()
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

# Phase 12f: the five LP bounds whose PyPSA class default is FINITE, so that
# clearing one has a real value to write. Mirrors
# `services.validation_service.FINITE_DEFAULT_BOUNDS`, which is the preflight
# that catches whatever gets past this route.
_FINITE_DEFAULT_BOUNDS = ("p_max_pu", "p_min_pu", "s_max_pu",
                          "e_max_pu", "e_min_pu")


def _finite_input_meta(component_class: str, col: str):
    """``(default, type)`` for ``col`` when it is a numeric INPUT attribute of
    ``component_class`` whose PyPSA class default is finite — the set Phase
    12g refuses NaN in — else ``None``. Read from PyPSA's own component
    metadata (`services.validation_service.finite_default_inputs`), so the
    two cannot drift; 12f's five bounds are the fallback so a PyPSA that
    reshapes `defaults` cannot turn a clear into a NaN write, which is the
    exact defect this exists to fix.
    """
    try:
        from services.pypsa_service import PyPSAService
        from services.validation_service import finite_default_inputs
        n = PyPSAService.get_network()
        meta = finite_default_inputs(n.components[component_class])
        if col in meta:
            dv, _varying, typ = meta[col]
            return float(dv), typ
        return None
    except Exception:                                         # noqa: BLE001
        if col not in _FINITE_DEFAULT_BOUNDS:
            return None
        if col == "p_min_pu":
            return (-1.0 if component_class == "StorageUnit" else 0.0), "float"
        return (0.0 if col == "e_min_pu" else 1.0), "float"


def _finite_bound_default(component_class: str, col: str) -> float:
    """12f's name, kept for its callers: the class default of one of the five
    bounds, through the metadata."""
    meta = _finite_input_meta(component_class, col)
    if meta is not None:
        return meta[0]
    if col == "p_min_pu":
        return -1.0 if component_class == "StorageUnit" else 0.0
    return 0.0 if col == "e_min_pu" else 1.0


def _bool_input_default(component_class: str, col: str) -> bool:
    """The class default of a BOOLEAN input column, read from PyPSA's own
    metadata — 12g's `finite_default_inputs` pattern for `type == "boolean"`.

    A hand-written "bools clear to False" list is wrong twice over: `active`
    defaults to True on EVERY class, and so does `Link.cyclic_delay`, which
    is bulk-editable. A custom GUI column (`p_max_pu_includes_outages`) is
    not in the table at all and falls back to False, its declared default.
    """
    try:
        from services.pypsa_service import PyPSAService
        comp = PyPSAService.get_network().components[component_class]
        d = getattr(comp, "defaults", None)
        if d is None:
            d = getattr(comp, "attrs", None)
        row = d.loc[col]
        if str(row.get("type", "")).strip() == "boolean" \
                and str(row.get("status", "")).strip().startswith("Input"):
            return bool(row.get("default"))
    except Exception:                                         # noqa: BLE001
        pass
    return False


@router.patch("/_bulk")
def bulk_update(body: dict) -> dict:
    component_class = body.get("component_class", "")
    names = body.get("names", [])
    updates = body.get("updates", {})

    if component_class not in _COMPONENT_ATTRS:
        raise HTTPException(400, f"Unknown component_class '{component_class}'. "
            f"Expected one of: {', '.join(sorted(_COMPONENT_ATTRS))}.")
    if not isinstance(names, list) or len(names) == 0:
        raise HTTPException(400, "names must be a non-empty list")
    if not isinstance(updates, dict) or len(updates) == 0:
        raise HTTPException(400, "updates must be a non-empty object")
    if "name" in updates:
        raise HTTPException(400, "Bulk rename not supported. Use PUT /<component>/{name}.")

    # Phase 12h: ONE lock hold spans the prologue, the unknown-column
    # check, the dtype dispatch and the write. The flag normaliser below
    # can CREATE a column, and both the check and the dispatch read the
    # frame's columns and dtypes — a solve adding and removing its slack
    # rows underneath would make the route write against a shape it never
    # inspected. `get_lock()` is an RLock, so a caller already holding it
    # is unaffected.
    with PyPSAService.get_lock():
        attr = _COMPONENT_ATTRS[component_class]
        n = PyPSAService.get_network()
        df = getattr(n, attr)

        # Phase 12h: `p_max_pu_includes_outages` is a custom BOOL column, and
        # this route is the one that has to set it on an import whose frame
        # never carried it — without the create-if-absent the unknown-column
        # check below refuses with `has no column(s)`. Normalising HERE, ahead
        # of that check AND of the dtype dispatch that reads `df[col].dtype`,
        # is also what makes a `_bulk` write land as a real `bool`: normalise
        # after the dispatch and the string 'True' is stored instead, which
        # `flag_is_set` reads as set and which exports fine — only the dtype
        # separates the two, and only until the next solve.
        if attr == "generators":
            try:
                from services.adequacy.occurrence import normalise_flag_column
                normalise_flag_column(n)
            except Exception:                                 # noqa: BLE001
                pass

        # Resolve names. Bulk semantics: refuse the whole batch if any target is
        # missing — partial application would be hard to undo predictably.
        name_strs = [str(x) for x in names]
        missing = [n_ for n_ in name_strs if n_ not in df.index]
        if missing:
            sample = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            raise HTTPException(404, f"{len(missing)} {component_class}(s) not found: {sample}")

        # Reject any target that's currently a solver-internal transient row
        # (vintage clone, VOLL slack). The /api/network/{component} filter
        # hides these from the UI, so a frontend can't normally surface their
        # names — but a stale localStorage payload, a replay attack, or a
        # power-user CLI hitting the bulk endpoint directly could. Mutating
        # LP scaffolding mid-solve corrupts the optimisation in subtle ways
        # (e.g. flipping a vintage's p_nom_extendable defeats the whole
        # per-period bound mechanism). Refuse with a clear 409.
        transient_targets = [n_ for n_ in name_strs
                             if n_ in PyPSAService.get_transient_rows(component_class)]
        if transient_targets:
            sample = ", ".join(transient_targets[:3]) + ("…" if len(transient_targets) > 3 else "")
            raise HTTPException(
                409,
                f"Cannot bulk-edit {len(transient_targets)} {component_class}(s) "
                f"({sample}) — these rows are LP scaffolding generated by the "
                f"current solve (vintage clones or VOLL slacks). Wait for the "
                f"solver to finish and try again on the parent row(s).",
            )

        # Validate every column exists. PyPSA defines its full schema lazily — the
        # column may exist on the DataFrame even if no row has set it explicitly,
        # so this catches typos like "p_min_pu " (trailing space).
        unknown_cols = [c for c in updates if c not in df.columns]
        if unknown_cols:
            raise HTTPException(400,
                f"{component_class} has no column(s): {', '.join(unknown_cols)}.")

        # Coerce each value to the column's existing dtype. Without this, writing
        # a string into a numeric column upcasts the whole column to `object`,
        # which then breaks `n.export_to_netcdf()` at save time with a cryptic
        # "object array contains mixed native types" ValueError. Reject up front
        # so the failure happens at edit-time with a clear message rather than at
        # save-time where the user has no idea which field is wrong.
        coerced: dict[str, Any] = {}
        for col, value in updates.items():
            col_dtype = df[col].dtype
            if pd.api.types.is_bool_dtype(col_dtype):
                if isinstance(value, str):
                    if value.strip().lower() in ("true", "1", "yes"):
                        value = True
                    elif value.strip().lower() in ("false", "0", "no"):
                        value = False
                if value is None:
                    # Phase 12h: the bulk editor sends `null` for a blank
                    # cell, and `df.loc[...] = None` upcasts the column to
                    # `object` — the one shape netCDF refuses — so the next
                    # project save is a 500. A null clears to the column's
                    # CLASS DEFAULT, read from PyPSA's metadata rather than
                    # assumed False.
                    #
                    # `active` is refused instead. Its default is True, so
                    # clearing it would ACTIVATE every selected asset,
                    # behind a confirm toast that reads "Set active =
                    # (unset) on 200 generator(s)?". 422 is the shape this
                    # route already uses for a value it could write but
                    # refuses on what the write would MEAN (12g's non-finite
                    # refusal); 400 is its wrong-type answer.
                    if col == "active":
                        raise HTTPException(
                            422,
                            "Column 'active' cannot be cleared — send true "
                            "or false. Its PyPSA default is true, so "
                            "clearing it would ACTIVATE every selected "
                            "asset rather than leave it as it is.")
                    coerced[col] = _bool_input_default(component_class, col)
                    continue
                coerced[col] = bool(value)
                continue
            if pd.api.types.is_numeric_dtype(col_dtype):
                if value is None or value == "":
                    # Blank-to-clear a bound should produce PyPSA's "no bound"
                    # sentinel (±inf), matching how the per-row PUT path clears the
                    # capacity/economic bounds via the schema aliases (_NoneToPosInf
                    # on *_max / lifetime, _NoneToNegInf on e_sum_min). The
                    # endswith("_max") predicate is intentionally a superset: it also
                    # covers PyPSA's inf-default voltage bounds (v_mag_pu_max,
                    # v_ang_max) — clearing those to inf is likewise their PyPSA
                    # default, so the resulting network is valid. Everything else
                    # keeps NaN ("missing"), as before.
                    # Phase 12g: the finite-default metadata decides FIRST. The
                    # suffix rules below target ±inf-default columns (`p_nom_max`,
                    # `lifetime`, `e_sum_min`) — but `Transformer.phase_shift_max`
                    # ends in `_max` and defaults to 0.0, and clearing it to `inf`
                    # made the next solve refuse the value `_bulk` itself wrote.
                    _meta = _finite_input_meta(component_class, col)
                    if _meta is not None:
                        coerced[col] = _meta[0]
                    elif col.endswith("_max") or col == "lifetime":
                        coerced[col] = float("inf")
                    elif col == "e_sum_min":
                        coerced[col] = float("-inf")
                    elif col in _FINITE_DEFAULT_BOUNDS:
                        # Phase 12f. NaN is not a valid "no bound" sentinel for
                        # these five: PyPSA does not fall back to a default, it
                        # MASKS the constraint row out of the LP, so clearing
                        # `p_max_pu` used to leave a 100 MW unit free to dispatch
                        # 500 MW. Their class default is finite, so "unset" has a
                        # real value — and it is exactly what `n.add(attr=None)`
                        # coerces to, verified for all five across Generator,
                        # Link, StorageUnit, Store, Line and Transformer. Keyed by
                        # (component, column) because `StorageUnit.p_min_pu` is
                        # −1.0 where a Generator's is 0.0.
                        #
                        # `ramp_limit_*` deliberately still lands in the NaN branch
                        # below: there the class default IS NaN and PyPSA masks the
                        # row on purpose, which is the documented way to say "this
                        # unit has no ramp limit".
                        coerced[col] = _finite_bound_default(component_class, col)
                    else:
                        coerced[col] = float("nan")  # pandas treats this as missing
                    continue
                try:
                    coerced[col] = float(value)
                except (TypeError, ValueError):
                    raise HTTPException(400,
                        f"Column '{col}' is numeric ({col_dtype}); got non-numeric "
                        f"value {value!r}.")
                # Phase 12f: `json.loads` accepts the bare `NaN` and `Infinity`
                # literals and `float()` accepts the strings "nan" and "inf", so
                # a non-finite value can reach one of the five bounds past the
                # `null` branch above. It masks the LP row exactly as a cleared
                # cell did, so it is refused here — the same answer the time-
                # series routes give — rather than accepted and refused at solve.
                # Whole-branch review S1: the outage rate is a probability-like
                # unavailability — finite and in [0, 1) — and the engines
                # convolve whatever number is here, so the bulk path refuses
                # exactly what the create/update schemas refuse.
                if col == "outage_rate_value" and not (
                        math.isfinite(coerced[col]) and 0.0 <= coerced[col] < 1.0):
                    raise HTTPException(
                        422,
                        f"Column 'outage_rate_value' must be a finite number in "
                        f"[0, 1); got {value!r}. It is a probability-like "
                        "unavailability, not a percentage or count. Send null "
                        "to unset it (the per-carrier default then applies).")
                if not math.isfinite(coerced[col]) and (
                        col in _FINITE_DEFAULT_BOUNDS
                        or _finite_input_meta(component_class, col) is not None):
                    # Phase 12g: every finite-default input, not only the five.
                    raise HTTPException(
                        422,
                        f"Column '{col}' must be a finite number; got {value!r}. "
                        "PyPSA does not default a non-finite value here, it drops "
                        "the term or the constraint that reads it. Send null to "
                        "restore the default.")
                continue
            # Strings / objects pass through. We still cast to str if the user
            # sent a number into a string column so dtype stays clean.
            if pd.api.types.is_string_dtype(col_dtype) or pd.api.types.is_object_dtype(col_dtype):
                coerced[col] = "" if value is None else str(value)
                continue
            coerced[col] = value

        # If the bulk update sets `carrier`, ensure the carrier row exists with
        # catalog metadata first — same auto-add behavior as PUT.
        if component_class != "Carrier" and "carrier" in coerced:
            new_carrier = coerced["carrier"]
            if isinstance(new_carrier, str):
                ensure_carrier(n, new_carrier)
        for col, value in coerced.items():
            # Phase 12g, measured and left alone: pandas 3.0.5 keeps an int64
            # column int64 when the written value is integral (`0`, `0.0`,
            # `2030.0` alike) and upcasts only on NaN — so `build_year`
            # cleared to its default 0 stays `int64` with no help. A dtype
            # restore written here on the plan review's contrary probe did
            # not bite and was removed.
            df.loc[name_strs, col] = value

    # One audit entry per bulk op (not per component). Pretty-print the values
    # so the History tab shows what changed at a glance.
    pretty_updates = ", ".join(f"{k}={v}" for k, v in updates.items())
    change_log_service.log(
        "update", component_class, f"({len(name_strs)} items)",
        f"Bulk: {pretty_updates}",
    )
    return {"updated": len(name_strs), "fields": list(updates.keys())}


# ── Undo stack ─────────────────────────────────────────────────────────────────

def _push_undo_snapshot() -> None:
    """
    Capture current network + user-ts state and push onto the undo stack.

    Called by the HTTP middleware in main.py before every mutating request so
    that the state *before* each change is always restorable. The middleware
    runs us in a worker thread to keep the event loop responsive, so we must
    hold the PyPSA lock for the duration of the export — otherwise a
    concurrent mutation could rewrite the network mid-snapshot.

    PyPSA ≥ 1.0's export_to_netcdf requires a real file path (its context
    manager's __exit__ calls Path(self.path) which rejects BytesIO with a
    TypeError). We write to a temp file and read the bytes back.
    """
    import logging as _logging
    import pathlib
    import tempfile

    from services import undo_service
    try:
        with PyPSAService.get_lock():
            n = PyPSAService.get_network()
            _backup_network_ts_to_user_ts(n)
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
                tmp = pathlib.Path(f.name)
            try:
                with PyPSAService.get_netcdf_io_lock():
                    PyPSAService.export_network_to_netcdf(n, tmp)
                netcdf_bytes = tmp.read_bytes()
            finally:
                tmp.unlink(missing_ok=True)
            user_ts_payload = _serialize_user_ts()
        undo_service.push(netcdf_bytes, user_ts_payload)
    except Exception as exc:
        # Log instead of swallowing silently so future regressions are visible.
        _logging.getLogger(__name__).warning("undo snapshot skipped: %s", exc)


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
    from services import undo_service
    return {
        "depth": undo_service.depth(),
        "memory_bytes": undo_service.memory_bytes(),
        "max_bytes": undo_service.MAX_BYTES,
        "max_steps": undo_service.MAX_STEPS,
    }


@router.post("/undo")
def undo_last():
    """Restore the network to the state before the most recent mutating operation."""
    import pathlib
    import tempfile

    from services import undo_service

    # ★ Precheck BEFORE the destructive pop (Phase 11 review, BLOCKER 1).
    # `undo_service.pop()` removes the entry from the stack and returns it; a
    # 409 raised after it discards that entry, so two refused Ctrl-Z presses
    # during a study emptied a two-deep undo stack while changing nothing.
    # /api/network/undo is in `_UNDO_EXCLUDE`, so the middleware's
    # push-then-rollback does not cover it either.
    PyPSAService.refuse_if_study_running("undo")
    result = undo_service.pop()
    if result is None:
        raise HTTPException(409, "Nothing to undo")

    netcdf_bytes, user_ts_data = result
    # PyPSA ≥ 1.0 import_from_netcdf also requires a path — round-trip via tempfile.
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
        tmp = pathlib.Path(f.name)
        f.write(netcdf_bytes)
    try:
        with PyPSAService.get_lock():
            # Undo is an in-place edit of the CURRENT project (not a project
            # switch), so identity must survive it. `reset_network()` clears
            # the binding to None; capture it first and restore it after the
            # re-import, all inside the lock, so a concurrent save never sees
            # the current project momentarily unbound (which would let its
            # `expect` guard fall through and its claim rebind wrongly).
            # Capture the WHOLE binding, not just the name: `reset_network()`
            # drops the tenant identity too, and restoring the name alone would
            # leave the ctx keyed by name in the resident registry — the
            # cross-org collision Step 0a removed.
            prev_binding = PyPSAService.get_binding()
            prev_loaded = prev_binding["name"]
            PyPSAService.reset_network()
            n = PyPSAService.get_network()
            with PyPSAService.get_netcdf_io_lock():
                PyPSAService.import_network_from_netcdf(n, tmp)
            PyPSAService.set_binding(prev_binding)
            if prev_loaded:
                try:
                    n.name = prev_loaded
                except Exception:
                    pass
    finally:
        tmp.unlink(missing_ok=True)

    _restore_user_ts(user_ts_data)
    n = PyPSAService.get_network()
    _reapply_user_ts_to_network(n)
    change_log_service.log("undo", "Network", "", "Undone last action")
    return {"undone": True, "remaining": undo_service.depth()}


# Guards every read/write of _user_ts. The PyPSA-network lock protects the
# PyPSA DataFrames; this lock protects this Python-side store independently
# so a concurrent upload + autosave can't trip
# `RuntimeError: dictionary changed size during iteration` inside
# _serialize_user_ts / _restore_user_ts.


# ── Time Series ───────────────────────────────────────────────────────────────









# ── Load profile helpers ───────────────────────────────────────────────────────


@router.get("/loads/profiles")
def get_load_profiles():
    """
    Return profile metadata for every load — whether a p_set time series exists.

    Checks user-uploaded _user_ts first, then falls back to n.loads_t.p_set so
    that time series loaded from a .nc file are also reported correctly.
    Each entry includes a `section` field ('electricity'|'hydrogen'|'heat'|'other')
    derived from the load's bus carrier.
    """
    n = PyPSAService.get_network()
    net_p_set = getattr(n.loads_t, "p_set", None)
    result: dict[str, dict] = {}
    # Skip transient rows (none today on Load, but registry is class-agnostic).
    keep_names = _filter_transient_names("Load", list(n.loads.index))
    for load_name in keep_names:
        section = _load_section(n, load_name)
        bus = str(n.loads.at[load_name, "bus"]) if "bus" in n.loads.columns else ""
        s = _user_ts.get(('loads', 'p_set', load_name))
        if s is None and net_p_set is not None and not net_p_set.empty and load_name in net_p_set.columns:
            s = net_p_set[load_name]
        if s is not None:
            col = s.dropna()
            # Multi-period (period, timestep) MultiIndex: use the timestep
            # level for the ISO timestamps; raw tuples don't have .isoformat.
            if len(col) and isinstance(col.index, pd.MultiIndex):
                _ts_lvl = col.index.get_level_values(-1)
                _start, _end = _ts_lvl[0], _ts_lvl[-1]
            elif len(col):
                _start, _end = col.index[0], col.index[-1]
            else:
                _start = _end = None
            result[load_name] = {
                "has_profile": True,
                "rows": int(len(col)),
                "start": (_start.isoformat() if hasattr(_start, "isoformat") else None),
                "end": (_end.isoformat() if hasattr(_end, "isoformat") else None),
                "mean": float(col.mean()) if len(col) else 0.0,
                "peak": float(col.max()) if len(col) else 0.0,
                # Σ of the profile values. For an hourly p_set profile this is
                # the delivered energy in MWh; the GUI labels it accordingly.
                "sum": float(col.sum()) if len(col) else 0.0,
                "section": section,
                "bus": bus,
            }
        else:
            result[load_name] = {"has_profile": False, "section": section, "bus": bus}
    return result


_LOAD_SHAPES = {
    "electricity": None,  # use _double_peak_profile (defined below)
    "hydrogen": _h2_load_profile,
    "heat": _heat_load_profile,
    "other": None,
}


@router.get("/loads/template")
def download_load_profile_template(
    section: str | None = None,
    load_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
):
    """
    Download a 1-week (168 h) hourly template Excel for load p_set profiles.

    Query params:
      - ``section``: ``electricity`` | ``hydrogen`` | ``heat`` | ``other``.
        Filters which loads appear in the template AND picks the daily shape
        (electricity → double peak, hydrogen → flat industrial, heat →
        morning/evening peaks). Defaults to all loads using their per-load
        section shape.
      - ``load_name``: if set, generate a single-column template for that load
        only (useful for per-load uploads).
    """
    n = PyPSAService.get_network()
    if n.loads.empty:
        raise HTTPException(400, "No loads in network")

    # Pick the load set
    all_loads = list(n.loads.index)
    if load_name is not None:
        if load_name not in n.loads.index:
            raise HTTPException(404, f"Load '{load_name}' not found")
        target_loads = [load_name]
    elif section:
        sec = section.lower().strip()
        target_loads = [name for name in all_loads if _load_section(n, name) == sec]
        if not target_loads:
            raise HTTPException(400, f"No loads belong to section '{sec}'")
    else:
        target_loads = all_loads

    snapshots, _src = _template_snapshots(n, start, end, freq, use_snapshots)

    # If section is given, every column uses that section's shape. Otherwise
    # each load uses its own carrier-derived shape — produces a mixed sheet.
    forced_shape = _shape_for_section(section) if section else None

    data: dict[str, np.ndarray] = {}
    for idx, name in enumerate(target_loads):
        p_max = float(n.loads.at[name, "p_set"]) if "p_set" in n.loads.columns else 100.0
        if not math.isfinite(p_max) or p_max <= 0:
            p_max = 100.0
        shape_fn = forced_shape or _shape_for_section(_load_section(n, name))
        data[name] = shape_fn(snapshots, p_max, noise_seed=42 + idx)

    df = pd.DataFrame(data, index=snapshots)
    df.index.name = "timestamp"

    if load_name:
        fname = f"load_{load_name.replace(' ', '_')}_template.xlsx"
    elif section:
        fname = f"load_{section}_template.xlsx"
    else:
        fname = "load_profiles_template.xlsx"
    return _xlsx_response(df, fname)


@router.get("/loads/aggregate")
def aggregate_load_profile(
    section: str | None = None,
    names: str | None = None,
):
    """
    Return the time-aligned sum of p_set across the requested loads.

    Query params:
      - ``names``: comma-separated explicit load names (takes precedence).
      - ``section``: aggregate every load in this section.
    Either may be supplied; if both, ``names`` wins. The response shape mirrors
    /timeseries: ``{index, values, total_loads, peak, mean}``.
    """
    n = PyPSAService.get_network()
    targets: list[str]
    if names:
        targets = [c.strip() for c in names.split(",") if c.strip()]
        targets = [c for c in targets if c in n.loads.index]
    elif section:
        sec = section.lower().strip()
        targets = [name for name in n.loads.index if _load_section(n, name) == sec]
    else:
        targets = list(n.loads.index)

    if not targets:
        return {"index": [], "values": [], "total_loads": 0, "peak": 0.0, "mean": 0.0}

    # Pull each series from _user_ts first, fall back to n.loads_t.p_set.
    net_p_set = getattr(n.loads_t, "p_set", None)
    series_list: list[pd.Series] = []
    for name in targets:
        s = _user_ts.get(("loads", "p_set", name))
        if s is None and net_p_set is not None and not net_p_set.empty and name in net_p_set.columns:
            s = net_p_set[name]
        if s is not None:
            series_list.append(s.rename(name))

    if not series_list:
        return {"index": [], "values": [], "total_loads": len(targets), "peak": 0.0, "mean": 0.0}

    df = pd.concat(series_list, axis=1).fillna(0.0)
    total = df.sum(axis=1)
    idx = [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in total.index]
    values = [None if isinstance(v, float) and not math.isfinite(v) else float(v) for v in total.tolist()]
    finite_vals = [v for v in values if v is not None]
    return {
        "index": idx,
        "values": values,
        "total_loads": len(targets),
        "loads_with_profile": len(series_list),
        "peak": float(max(finite_vals)) if finite_vals else 0.0,
        "mean": float(sum(finite_vals) / len(finite_vals)) if finite_vals else 0.0,
    }


def _apply_profile_upload(n, comp_attr: str, attribute: str, display_class: str, df) -> dict:
    """
    Shared body of the load/generator/link profile-upload endpoints.

    Matches uploaded `df` columns to the component's names, stores each matched
    column in `_user_ts[(comp_attr, attribute, col)]` (under `_user_ts_lock`,
    since autosave's `_serialize_user_ts` iterates concurrently), then reapplies
    to the network under the PyPSA lock — `_ensure_snapshots_cover_user_ts`
    auto-expands `n.snapshots` so a longer-than-current upload isn't truncated,
    and `_reapply_user_ts_to_network` aligns everything to the (possibly grown)
    index. Returns `{matched, unmatched, rows, snapshot_count}` where
    snapshot_count reflects the post-expansion `n.snapshots` so the frontend can
    refresh its counter.
    """
    valid = set(getattr(n, comp_attr).index)
    matched   = [c for c in df.columns if c in valid]
    unmatched = [c for c in df.columns if c not in valid]
    if not matched:
        raise HTTPException(
            400,
            f"No column names matched any {display_class.lower()}. "
            f"{display_class}s in network: {list(valid)[:10]}",
        )
    # Phase 12f: refuse before `_user_ts` is written — an entry that lands here
    # survives project reload and is re-injected on every solve, so a rejected
    # upload must leave nothing behind. Only the matched columns are checked:
    # an unmatched one is discarded anyway and its blanks are not the user's
    # problem.
    _reject_nonfinite_timeseries(df[matched], display_class, attribute)
    with _user_ts_lock:
        for col in matched:
            _user_ts[(comp_attr, attribute, col)] = df[col].astype(float)
    with PyPSAService.get_lock():
        _ensure_snapshots_cover_user_ts(n)
        _reapply_user_ts_to_network(n)
    names_preview = ", ".join(matched[:3]) + ("…" if len(matched) > 3 else "")
    cls_lower = display_class.lower()
    change_log_service.log(
        "timeseries", display_class, names_preview,
        f"Uploaded {cls_lower} {attribute} profiles: {len(matched)} {cls_lower}(s), {len(df)} rows",
    )
    return {
        "matched": matched, "unmatched": unmatched, "rows": len(df),
        "snapshot_count": len(n.snapshots),
    }


@router.post("/loads/upload_profile")
async def upload_load_profile(file: UploadFile = File(...)):
    """
    Upload an Excel or CSV file whose columns are load names and index is
    timestamps.  Matched columns are written into the user profile store and
    also into n.loads_t.p_set for simulation.
    """
    content = await read_capped(file)
    df = _parse_upload(content, file.filename or "")
    return _apply_profile_upload(PyPSAService.get_network(), "loads", "p_set", "Load", df)


# ── Generator profile helpers ──────────────────────────────────────────────────


@router.get("/generators/profiles")
def get_generator_profiles():
    """
    Return p_max_pu / p_min_pu / marginal_cost profile metadata for every
    generator.

    Top-level has_profile/rows/mean/peak fields describe p_max_pu (back-compat
    with the renewable/DR/links tabs). Conventional-tab sub-attributes are
    exposed under nested keys:
      • p_min_pu       — minimum dispatch floor (must-run)
      • marginal_cost  — time-varying €/MWh dispatch cost (e.g. fuel-price
        traces, market scenarios). Frontend renders the Conventional tab's
        third sub-toggle from this.
    """
    n = PyPSAService.get_network()
    net_p_max_pu = getattr(n.generators_t, "p_max_pu", None)
    net_p_min_pu = getattr(n.generators_t, "p_min_pu", None)
    net_mc       = getattr(n.generators_t, "marginal_cost", None)
    result: dict[str, dict] = {}
    # Skip transient generator rows (VOLL slacks, vintage clones) so the
    # frontend's profile-tab list mirrors what /api/network/generators
    # returns. Iterating filtered names is cheap; iterating the raw
    # index and conditional-skipping per-row would be equivalent.
    keep_names = _filter_transient_names("Generator", list(n.generators.index))
    for name in keep_names:
        carrier = str(n.generators.at[name, 'carrier']) if 'carrier' in n.generators.columns else ''
        category = _gen_category(carrier)
        max_meta = _profile_meta_for(
            name, _user_ts.get(('generators', 'p_max_pu', name)), net_p_max_pu,
        )
        min_meta = _profile_meta_for(
            name, _user_ts.get(('generators', 'p_min_pu', name)), net_p_min_pu,
        )
        # marginal_cost: user_only=True so solver-written CO2 surcharge
        # columns (from co2_price_per_period mode) don't show up as
        # "uploaded profiles" in the Time Series Manager. See solver_service
        # ~line 3736 — the per-period CO2 path writes to generators_t.marginal_cost
        # before solve; a project saved mid-solve carries those columns even
        # though the user never uploaded a marginal_cost profile.
        mc_meta = _profile_meta_for(
            name, _user_ts.get(('generators', 'marginal_cost', name)), net_mc,
            user_only=True,
        )
        result[name] = {
            **max_meta,
            'carrier': carrier,
            'category': category,
            'p_min_pu': min_meta,
            'marginal_cost': mc_meta,
        }
    return result


@router.get("/generators/template")
def download_generator_profile_template(
    category: str = "renewable",
    attribute: str = "p_max_pu",
    name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
):
    """
    Download a template Excel for generator capacity factors. Default
    horizon is the simulation snapshots; pass `use_snapshots=false` plus
    `start`/`end`/`freq` to override.

    Pass ``name`` to get a single-column file for one generator (used by the
    per-row download button on the Time Series page). When ``name`` is set,
    ``category`` is ignored — the shape is derived from the generator's own
    carrier so a "wind" pick gets the wind profile regardless of which tab
    triggered the download.
    """
    n = PyPSAService.get_network()
    if n.generators.empty:
        raise HTTPException(400, "No generators in network")

    snapshots, _src = _template_snapshots(n, start, end, freq, use_snapshots)

    # Must-run templates start from a flat baseline floor (the user can edit it
    # freely afterwards). Per-carrier defaults reflect typical minimum stable
    # loads: nuclear is the highest, lignite/coal next, others lower.
    def _must_run_floor(carrier_lower: str) -> float:
        if 'nuclear' in carrier_lower:                 return 0.5
        if 'lignite' in carrier_lower or 'coal' in carrier_lower: return 0.4
        return 0.3

    if name is not None:
        if name not in n.generators.index:
            raise HTTPException(404, f"Generator '{name}' not found")
        target_gens = [(0, name, n.generators.loc[name])]
    else:
        target_gens = [
            (idx, gname, row)
            for idx, (gname, row) in enumerate(n.generators.iterrows())
            if _gen_category(str(row.get('carrier', '') or '')) == category
        ]

    # Per-carrier baseline marginal_cost for the template (€/MWh). PyPSA-Eur
    # ballpark values — the user is expected to edit them. Falls back to the
    # generator's own scalar `marginal_cost` when no carrier-default exists,
    # then to 50 €/MWh as a generic baseline.
    def _mc_baseline(carrier_lower: str, fallback: float) -> float:
        if 'nuclear' in carrier_lower:                                 return 10.0
        if 'lignite' in carrier_lower:                                 return 35.0
        if 'coal' in carrier_lower:                                    return 45.0
        if 'ccgt' in carrier_lower:                                    return 60.0
        if 'ocgt' in carrier_lower or 'gas' in carrier_lower:          return 90.0
        if 'oil' in carrier_lower:                                     return 120.0
        if 'biomass' in carrier_lower or 'biogas' in carrier_lower:    return 70.0
        if fallback and 0 < fallback < 1000:                           return float(fallback)
        return 50.0

    data: dict[str, np.ndarray] = {}
    for idx, gname, row in target_gens:
        carrier = str(row.get('carrier', '') or '')
        c_lower = carrier.lower()
        if attribute == 'p_min_pu':
            data[gname] = np.full(len(snapshots), _must_run_floor(c_lower))
        elif attribute == 'marginal_cost':
            # Flat per-carrier baseline; user typically overlays a fuel-price
            # trace by editing the file. We keep the baseline FLAT (not noisy)
            # so it's obvious the values are seed defaults rather than a real
            # forecast — and so that an unedited upload doesn't quietly
            # perturb the LP with random per-hour cost noise.
            baseline = _mc_baseline(c_lower, float(row.get('marginal_cost', 0) or 0))
            data[gname] = np.full(len(snapshots), baseline)
        elif 'solar' in c_lower or 'pv' in c_lower:
            data[gname] = _solar_cf_profile(snapshots, noise_seed=42 + idx)
        elif 'wind' in c_lower:
            data[gname] = _wind_cf_profile(snapshots, noise_seed=42 + idx)
        else:
            data[gname] = _flat_cf_profile(snapshots, noise_seed=42 + idx)

    if not data:
        raise HTTPException(400, f"No generators in category '{category}'")

    df = pd.DataFrame(data, index=snapshots)
    df.index.name = "timestamp"

    if name:
        safe = name.replace(' ', '_')
        fname = f"generator_{safe}_{attribute}_template.xlsx"
    else:
        fname = f"generator_{attribute}_{category}_template.xlsx"
    return _xlsx_response(df, fname)


@router.post("/generators/upload_profile")
async def upload_generator_profile(
    attribute: str = "p_max_pu",
    file: UploadFile = File(...),
):
    """Upload an Excel or CSV file whose columns are generator names."""
    content = await read_capped(file)
    df = _parse_upload(content, file.filename or "")
    return _apply_profile_upload(PyPSAService.get_network(), "generators", attribute, "Generator", df)


# ── Link profile helpers ───────────────────────────────────────────────────────


@router.get("/links/profiles")
def get_link_profiles():
    """
    Return p_max_pu / p_min_pu / marginal_cost profile metadata for every
    link, including category.

    Top-level has_profile/rows/mean/peak describe p_max_pu (back-compat
    with the existing Links tab). p_min_pu and marginal_cost are exposed
    as nested keys so the Links tab's sub-toggle can render correct
    has-profile indicators per attribute.
    """
    n = PyPSAService.get_network()
    net_p_max_pu = getattr(n.links_t, "p_max_pu", None)
    net_p_min_pu = getattr(n.links_t, "p_min_pu", None)
    net_mc       = getattr(n.links_t, "marginal_cost", None)
    result: dict[str, dict] = {}
    # Skip transient link rows (vintage clones `parent@<year>`).
    keep_names = _filter_transient_names("Link", list(n.links.index))
    for name in keep_names:
        category = _link_category(n, name)
        max_meta = _profile_meta_for(
            name, _user_ts.get(('links', 'p_max_pu', name)), net_p_max_pu,
        )
        min_meta = _profile_meta_for(
            name, _user_ts.get(('links', 'p_min_pu', name)), net_p_min_pu,
        )
        # Same user_only treatment as generators — see get_generator_profiles.
        # Links don't currently get solver-written marginal_cost (only
        # generators do, via co2_price_per_period), but applying the flag
        # symmetrically keeps the policy consistent if a future solver
        # transform writes to links_t.marginal_cost.
        mc_meta = _profile_meta_for(
            name, _user_ts.get(('links', 'marginal_cost', name)), net_mc,
            user_only=True,
        )
        result[name] = {
            **max_meta,
            'category': category,
            'p_min_pu': min_meta,
            'marginal_cost': mc_meta,
        }
    return result


@router.get("/links/template")
def download_link_profile_template(
    attribute: str = "p_max_pu",
    name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
):
    """
    Download a flat-1.0 template for link availability profiles. Default
    horizon is the simulation snapshots; pass `use_snapshots=false` plus
    `start`/`end`/`freq` to override. Pass ``name`` for a single-column
    template (per-row download button on the Time Series page).
    """
    n = PyPSAService.get_network()
    if n.links.empty:
        raise HTTPException(400, "No links in network")

    snapshots, _src = _template_snapshots(n, start, end, freq, use_snapshots)

    if name is not None:
        if name not in n.links.index:
            raise HTTPException(404, f"Link '{name}' not found")
        target_links = [name]
    else:
        target_links = list(n.links.index)

    data = {lname: np.ones(len(snapshots)) for lname in target_links}
    df = pd.DataFrame(data, index=snapshots)

    if name:
        safe = name.replace(' ', '_')
        fname = f"link_{safe}_{attribute}_template.xlsx"
    else:
        fname = f"links_{attribute}_template.xlsx"
    return _xlsx_response(df, fname)


@router.post("/links/upload_profile")
async def upload_link_profile(
    attribute: str = "p_max_pu",
    file: UploadFile = File(...),
):
    """Upload an Excel or CSV file whose columns are link names."""
    content = await read_capped(file)
    df = _parse_upload(content, file.filename or "")
    return _apply_profile_upload(PyPSAService.get_network(), "links", attribute, "Link", df)


