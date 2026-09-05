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
    InvestmentPeriods,
    LineCreate,
    LinkCreate,
    LoadCreate,
    NetworkMeta,
    SampleWeeksConfig,
    ShuntImpedanceCreate,
    SnapshotConfig,
    StorageUnitCreate,
    StoreCreate,
    TransformerCreate,
)
from db.models import Session as SessionRow
from db.session import get_db
from deps import current_session
from services import active_project, change_log_service, vintage_service
from services.carrier_catalog import ensure_carrier
from services.pypsa_service import PyPSAService
from services.serialization import df_to_json
from services.upload_guard import read_capped

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
    return df_to_json(df)


def _get_component(component_class: str, attr: str) -> list[dict]:
    """
    Serialise the ACTIVE network's component DataFrame for the frontend,
    filtering out solver-only transient rows (vintage clones, VOLL slacks)
    so the user never sees them in the asset tables.

    Reads never acquire the PyPSA lock (per the project's read-never-locks
    policy), so during a solve the worker thread has already populated
    `n.generators` with `__voll_<bus>` rows and `n.links` with
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


# Map PyPSA's lowercase-plural DataFrame attribute names to the singular
# PascalCase class names used as keys in the transient-row registry.
# Kept as a module-level constant so endpoints that only know the attr
# form (e.g. /timeseries iterates "generators", "loads", …) can resolve
# to the registry's class key with a single lookup.
_ATTR_TO_CLASS: dict[str, str] = {
    "buses":         "Bus",
    "carriers":      "Carrier",
    "generators":    "Generator",
    "loads":         "Load",
    "lines":         "Line",
    "links":         "Link",
    "storage_units": "StorageUnit",
    "stores":        "Store",
    "transformers":  "Transformer",
}


def _filter_transient_names(component_class: str, names: list[str]) -> list[str]:
    """
    Drop solver-only transient names (vintage clones, VOLL slacks) from
    a plain name list. Mirror of the row filter in `_get_component`, but
    for endpoints that return column- or index-name LISTS directly (e.g.
    /timeseries, /generators/profiles) instead of full DataFrames.

    Short-circuits when the registry is empty so the healthy path costs
    one dict lookup. Preserves input order, no allocation if nothing
    needs filtering.
    """
    if not names or not PyPSAService.has_any_transient_rows():
        return names
    transient = PyPSAService.get_transient_rows(component_class)
    if not transient:
        return names
    return [n for n in names if n not in transient]


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
        # Re-key any saved per-period bounds so the modal data follows the
        # rename instead of stranding under the old key.
        if new_name != name:
            n.rename_component_names(component_class, **{name: new_name})
            vintage_service.rename_asset(n, component_class, name, new_name)
            # Same fix for the time-series store — _user_ts keys carry the
            # column name, and without this the profile would be silently
            # lost on the next save+reload (re-apply skips entries whose
            # column is no longer in the network DataFrame).
            _user_ts_rename_asset(attr, name, new_name)
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
    Serialise `df` to an .xlsx StreamingResponse with a quoted attachment
    filename. Shared tail of the load/generator/link profile-template download
    endpoints (filename is quoted per RFC 6266 — all template names are
    space-free so this is byte-equivalent for browsers).
    """
    buf = io.BytesIO()
    df.to_excel(buf, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
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

@router.get("/snapshots")
def get_snapshots():
    n = PyPSAService.get_network()
    sns = n.snapshots
    # Multi-period: emit ISO timestep strings + a parallel `periods` array,
    # same convention as /results/* TS payloads. Pre-fix the fallback
    # str(tuple) produced "(2026, Timestamp('...'))" which broke every
    # consumer doing indexOf / string-compare on the array.
    # Extent of uploaded time series — lets the Model Horizon page default its
    # snapshot range to the data the user actually uploaded. `can_sample_weeks`
    # gates the representative-week sampler (needs a full-year hourly profile).
    ts_start, ts_end = _user_ts_extent()
    can_sample_weeks = _annual_hourly_reference()[0] is not None
    if isinstance(sns, pd.MultiIndex):
        try:
            periods = [int(p) for p in sns.get_level_values(0)]
        except (TypeError, ValueError):
            periods = [str(p) for p in sns.get_level_values(0)]
        timesteps = sns.get_level_values(1)
        snaps = [
            ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            for ts in timesteps
        ]
        weightings = df_to_json(n.snapshot_weightings) if not n.snapshot_weightings.empty else []
        return {
            "count": len(sns),
            "snapshots": snaps,
            "periods": periods,
            "weightings": weightings,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "can_sample_weeks": can_sample_weeks,
        }
    snaps = [s.isoformat() if hasattr(s, "isoformat") else str(s) for s in sns]
    weightings = df_to_json(n.snapshot_weightings) if not n.snapshot_weightings.empty else []
    return {
        "count": len(sns), "snapshots": snaps, "weightings": weightings,
        "ts_start": ts_start, "ts_end": ts_end,
        "can_sample_weeks": can_sample_weeks,
    }


@router.get("/snapshots/weightings.csv")
def download_snapshot_weightings_csv():
    """
    Stream `n.snapshot_weightings` as a CSV file.

    Format — one row per snapshot, columns:
      • ``snapshot`` — ISO timestamp for flat networks; ``period|iso`` (e.g.
        ``2030|2024-01-01T00:00:00``) for MultiIndex networks. The pipe
        separator avoids datetime-parsing ambiguity inside Excel.
      • ``objective``, ``generators``, ``stores`` — float weights.

    The same shape is accepted by ``POST /snapshots/weightings.csv``.
    """
    import csv
    import io

    import pandas as pd
    from fastapi.responses import StreamingResponse

    n = PyPSAService.get_network()
    df = n.snapshot_weightings
    if df.empty:
        raise HTTPException(400, "Network has no snapshots yet.")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["snapshot", *df.columns])
    is_multi = isinstance(df.index, pd.MultiIndex)
    for idx, row in df.iterrows():
        if is_multi:
            period, ts = idx
            iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            key = f"{int(period)}|{iso}"
        else:
            key = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        w.writerow([key, *[float(row[c]) for c in df.columns]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="snapshot_weightings.csv"'},
    )


@router.post("/snapshots/weightings.csv")
async def upload_snapshot_weightings_csv(file: UploadFile = File(...)):
    """
    Replace `n.snapshot_weightings` from an uploaded CSV.

    CSV must have a ``snapshot`` column plus at least one of
    ``objective`` / ``generators`` / ``stores``. Other columns are ignored.
    Rows whose ``snapshot`` key doesn't match an existing snapshot are
    skipped (not an error — partial uploads are common while debugging).
    Returns the number of rows applied so the UI can show a count.
    """
    import csv
    import io

    import pandas as pd

    content = await read_capped(file)
    try:
        text = content.decode("utf-8-sig")  # handles Excel-saved UTF-8 BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "snapshot" not in (reader.fieldnames or []):
        raise HTTPException(
            400,
            "CSV must have a `snapshot` column (and one or more of "
            "`objective`, `generators`, `stores`).",
        )

    n = PyPSAService.get_network()
    df = n.snapshot_weightings
    if df.empty:
        raise HTTPException(
            400,
            "Network has no snapshots. Set the snapshot index first.",
        )
    is_multi = isinstance(df.index, pd.MultiIndex)

    # Build an index lookup so we can match either pipe-separated multi
    # keys ("2030|2024-01-01T00:00:00") or plain ISO timestamps.
    iso_to_idx: dict[str, object] = {}
    for idx in df.index:
        if is_multi:
            period, ts = idx
            iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            iso_to_idx[f"{int(period)}|{iso}"] = idx
            iso_to_idx[iso] = idx  # tolerant: accept ISO-only too
        else:
            iso = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
            iso_to_idx[iso] = idx

    cols = [c for c in ("objective", "generators", "stores") if c in (reader.fieldnames or [])]
    if not cols:
        raise HTTPException(
            400,
            "CSV has no weight columns. Include at least one of `objective`, "
            "`generators`, `stores`.",
        )

    # Two-pass validation + apply: parse + validate every cell BEFORE any
    # write, so a malformed row N doesn't leave rows 1..N-1 already
    # applied with no rollback. The previous single-pass loop raised
    # HTTPException mid-iteration after partially mutating
    # n.snapshot_weightings — symptom: a CSV with one bad cell would
    # leave the file partially applied and the user with no clean way
    # to retry without manually undoing the partial state.
    pending: list[tuple[object, str, float]] = []
    skipped = 0
    for row in reader:
        key = (row.get("snapshot") or "").strip()
        if not key or key not in iso_to_idx:
            skipped += 1
            continue
        idx = iso_to_idx[key]
        for c in cols:
            v = row.get(c, "").strip()
            if v == "":
                continue
            try:
                pending.append((idx, c, float(v)))
            except (TypeError, ValueError):
                raise HTTPException(
                    400,
                    f"Bad {c} value for {key}: {v!r} — no rows applied "
                    "(transaction rolled back). Fix the CSV and re-upload."
                )
    applied = 0
    with PyPSAService.get_lock():
        for idx, c, val in pending:
            df.at[idx, c] = val
            applied += 1
        change_log_service.log(
            "update", "Network", "snapshot_weightings",
            f"Uploaded snapshot_weightings.csv: {applied} cell(s) applied, "
            f"{skipped} row(s) skipped (no match).",
        )
    return {
        "applied": applied,
        "skipped": skipped,
        "columns": cols,
    }


@router.patch("/snapshots/weightings")
def update_snapshot_weightings(body: dict):
    """
    Update per-snapshot weights in `n.snapshot_weightings`.

    Body shape options (in priority order):

      • `{"all": <float>}` — set every snapshot weight (objective + generators +
        stores) to the same value. Canonical for the "representative day"
        workflow: 24 hourly snapshots representing 1 typical day in a 30-day
        month → set `{"all": 30}`.

      • `{"updates": {iso_or_idx: {objective?, generators?, stores?}, ...}}` —
        per-row override map. Keys may be ISO strings (e.g.
        `"2026-05-11T00:00:00"`) or integer indices into `n.snapshots`. Any
        column omitted is left unchanged.

    Returns the post-update weighting DataFrame so callers can verify.
    """
    import pandas as pd
    n = PyPSAService.get_network()
    if n.snapshot_weightings.empty:
        raise HTTPException(400, "Network has no snapshots. Set the snapshot index first via POST /snapshots.")
    with PyPSAService.get_lock():
        df = n.snapshot_weightings
        # Two-pass validate-then-apply: resolve + parse EVERYTHING first
        # (mutating nothing), raise on the first bad value, then write. The old
        # code wrote `df.at[idx,col]=float(raw)` mid-loop and raised 400 on a
        # bad cell at row N, leaving rows 0..N-1 already mutated with no
        # rollback — the user retries and the table is half-applied. Same
        # pattern as upload_snapshot_weightings_csv.
        all_val = body.get("all")
        all_float: float | None = None
        if all_val is not None:
            try:
                all_float = float(all_val)
            except (TypeError, ValueError):
                raise HTTPException(400, f"`all` must be a number, got {all_val!r}")
        updates = body.get("updates") or {}
        if not isinstance(updates, dict):
            raise HTTPException(400, "`updates` must be a dict keyed by snapshot.")
        # Build an iso → index lookup once so per-row updates are fast.
        # On multi-period networks, `df.index` is a MultiIndex of
        # `(period, ts)` tuples — neither has a top-level `.isoformat`
        # method, so the legacy `hasattr(s, 'isoformat')` branch fell
        # through to `str(tuple)` like "(2026, Timestamp('2026-05-11 …'))",
        # which a frontend ISO key never matches → every multi-period
        # weight PATCH 400'd with "Unknown snapshot key". Build two index
        # styles for multi-period: the bare-ts ISO and a `period|ts`
        # composite key. Flat networks keep the single-ISO behaviour.
        iso_to_idx: dict[str, object] = {}
        is_multi = isinstance(df.index, pd.MultiIndex)
        for s in df.index:
            if is_multi and isinstance(s, tuple) and len(s) == 2:
                period, ts = s
                ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                # Period-qualified key: frontend can disambiguate identical
                # operational hours across periods (`"2026|2026-05-11T00:00:00"`).
                iso_to_idx[f"{period}|{ts_iso}"] = s
                # Bare ISO key — kept for the flat→multi migration case
                # where the frontend hasn't been updated yet. Last write
                # wins (later periods overwrite earlier in the bare-ISO
                # map), so flat-style PATCHes target the latest period's
                # row. Documented quirk; multi-period clients should use
                # the period-qualified form.
                iso_to_idx[ts_iso] = s
            else:
                iso_to_idx[s.isoformat() if hasattr(s, "isoformat") else str(s)] = s
        # Pass 1 — resolve + parse every cell into `pending`, raising before
        # any write.
        pending: list[tuple[object, str, float]] = []
        for key, vals in updates.items():
            if not isinstance(vals, dict):
                continue
            if key in iso_to_idx:
                idx = iso_to_idx[key]
            else:
                try:
                    pos = int(key)
                    idx = df.index[pos]
                except (TypeError, ValueError, IndexError):
                    raise HTTPException(400, f"Unknown snapshot key {key!r}")
            for col, raw in vals.items():
                if col not in df.columns:
                    continue
                try:
                    pending.append((idx, col, float(raw)))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Bad weight value for {key}/{col}: {raw!r}")
        # Pass 2 — everything validated; apply atomically (the `all` broadcast
        # first, then per-row overrides on top).
        if all_float is not None:
            for col in df.columns:
                df[col] = all_float
        for idx, col, val in pending:
            df.at[idx, col] = val
        applied = len(pending)
        change_log_service.log(
            "update", "Network", "snapshot_weightings",
            f"Updated snapshot weightings: all={all_val}, per-row updates={applied}",
        )
    return {
        "count": len(n.snapshot_weightings),
        "weightings": df_to_json(n.snapshot_weightings),
    }


@router.post("/snapshots")
def set_snapshots(config: SnapshotConfig):
    import pandas as pd
    n = PyPSAService.get_network()
    # Preserve existing time series BEFORE PyPSA reindexes them to the new snapshots.
    _backup_network_ts_to_user_ts(n)
    with PyPSAService.get_lock():
        sns = pd.date_range(config.start, config.end, freq=config.freq)
        kw: dict = {}
        if config.weightings is not None:
            kw["default_snapshot_weightings"] = config.weightings
        # Demote any lingering MultiIndex (multi-period toggled off without
        # rebuilding n.snapshots, or a stale _t / weightings frame) to flat
        # FIRST. A direct set_snapshots(flat DatetimeIndex) on MultiIndex state
        # trips pandas' "cannot include dtype 'M' in a buffer" reindex bug.
        # No-op when the network is already flat.
        _flatten_snapshot_state(n)
        n.set_snapshots(sns, **kw)
        # Re-apply full profiles (from _user_ts) aligned to the new snapshot range.
        _reapply_user_ts_to_network(n)
    change_log_service.log(
        "update", "Network", "snapshots",
        f"Updated snapshots: {config.start} → {config.end} at {config.freq} ({len(n.snapshots)} steps)",
    )
    return {"count": len(n.snapshots)}


@router.post("/snapshots/multi_period")
def set_multi_period_snapshots(body: dict):
    """
    Build a 2-level MultiIndex (period, timestep) snapshot index for
    multi-investment-period planning.

    Body shapes:

      • Same operational year per period (canonical):
        `{"periods": [2025, 2036, 2046], "start": "2025-01-01T00:00",
          "end": "2025-12-31T23:00", "freq": "h"}`
        — same (start,end,freq) DatetimeIndex replicated under each period.

      • Different operational range per period:
        `{"periods": [2025, 2036, 2046],
          "per_period": [{"start":..., "end":..., "freq":...}, ...]}`
        — one (start,end,freq) per period. List length must equal periods.

    Side effects:
      • Sets `n.investment_periods = periods`.
      • Backs up _t tables to _user_ts BEFORE reindex; re-applies after.
      • PyPSA initialises `investment_period_weightings` rows (years=1.0,
        objective=1.0) — the user can then tune via /investment_period_weightings.
    """
    import pandas as pd
    n = PyPSAService.get_network()

    periods = body.get("periods")
    if not isinstance(periods, list) or not periods:
        raise HTTPException(400, "`periods` must be a non-empty list of years.")
    try:
        periods_int = [int(p) for p in periods]
    except (TypeError, ValueError):
        raise HTTPException(400, "`periods` entries must be integers.")
    if len(set(periods_int)) != len(periods_int):
        raise HTTPException(400, "`periods` must be unique.")
    periods_sorted = sorted(periods_int)

    per_period = body.get("per_period")
    if per_period is not None:
        if not isinstance(per_period, list) or len(per_period) != len(periods_sorted):
            raise HTTPException(
                400, "`per_period` length must equal `periods` length.",
            )
        timestep_blocks = []
        for i, spec in enumerate(per_period):
            if not isinstance(spec, dict):
                raise HTTPException(400, f"per_period[{i}] must be an object.")
            try:
                idx = pd.date_range(
                    spec.get("start"), spec.get("end"),
                    freq=spec.get("freq", "h"),
                )
            except Exception as exc:
                raise HTTPException(
                    400, f"per_period[{i}] bad date range: {exc}",
                ) from exc
            if len(idx) == 0:
                raise HTTPException(400, f"per_period[{i}] produced an empty index.")
            timestep_blocks.append(idx)
    else:
        start = body.get("start")
        end = body.get("end")
        freq = body.get("freq", "h")
        if not start or not end:
            raise HTTPException(400, "Provide `start`+`end` (or `per_period`).")
        try:
            base_idx = pd.date_range(start, end, freq=freq)
        except Exception as exc:
            raise HTTPException(400, f"Bad date range: {exc}") from exc
        if len(base_idx) == 0:
            raise HTTPException(400, "Date range produced an empty index.")
        timestep_blocks = [base_idx for _ in periods_sorted]

    mi = _build_period_multiindex(periods_sorted, timestep_blocks)

    # Preserve existing time series BEFORE reindex.
    _backup_network_ts_to_user_ts(n)
    # Capture snapshot_weightings BEFORE set_snapshots resets them to 1.0
    # (PyPSA fills with default_snapshot_weightings on reindex). Without
    # this, the LP's n.nyears collapses to n_timesteps/8760 — undervaluing
    # CAPEX 50× on representative-week setups and producing renewable
    # over-build.
    captured_weights = _capture_snapshot_weights_per_timestep(n)
    with PyPSAService.get_lock():
        # n.set_snapshots is order-sensitive vs n.investment_periods: PyPSA's
        # multi-period machinery expects investment_periods to mirror the
        # MultiIndex's level-0 values. Set snapshots first, then sync periods.
        n.set_snapshots(mi)
        n.investment_periods = periods_sorted
        # Re-broadcast the captured weights under each new period.
        _reapply_snapshot_weights(n, captured_weights)
        # Re-apply user time series (handles MultiIndex via the level-1 path
        # added in _reapply_user_ts_to_network).
        _reapply_user_ts_to_network(n)

    change_log_service.log(
        "update", "Network", "snapshots",
        f"Built MultiIndex snapshots: {len(periods_sorted)} periods × "
        f"{[len(blk) for blk in timestep_blocks]} steps = {len(mi)} total",
    )
    return {
        "count": len(n.snapshots),
        "periods": periods_sorted,
        "rows_per_period": [len(blk) for blk in timestep_blocks],
    }


@router.post("/snapshots/sample_weeks")
def sample_representative_weeks(config: SampleWeeksConfig):
    """
    Build a representative-week snapshot index from an uploaded annual
    hourly profile.

    For each calendar month, ``n_weeks`` random ISO calendar weeks (Mon–Sun)
    are sampled; their 168 hourly timesteps form the new snapshot index, and
    ``snapshot_weightings`` is set so each sampled hour represents
    ``days_in_month / (weeks_sampled_for_that_month × 7)`` hours — the
    weighted total reconstructs the full year (Σ ≈ 8760 h).

    Requires a flat hourly series in ``_user_ts`` spanning all 12 calendar
    months of one year (validated via ``_annual_hourly_reference``). Works for
    flat AND multi-period networks: for multi-period the sampled timestep
    index is replicated under every investment period (same representative
    weeks per period — the canonical multi-period workflow).
    """
    import calendar as _calendar
    import datetime as _datetime

    import numpy as _np

    if config.n_weeks < 1 or config.n_weeks > 5:
        raise HTTPException(400, "n_weeks must be between 1 and 5.")

    idx, reason = _annual_hourly_reference()
    if idx is None:
        raise HTTPException(400, reason)

    ref_year = int(idx.year[0])
    idx_set = set(idx)

    # ── Candidate ISO weeks per calendar month ────────────────────────────
    # An ISO week qualifies only if its full Mon 00:00 … Sun 23:00 (168 h)
    # span is present in the uploaded index — this naturally drops the partial
    # weeks at the Jan / Dec year edges. Each qualifying week is assigned to
    # the month of its Thursday (the ISO-standard rule for which month/year a
    # week belongs to).
    iso = idx.isocalendar()
    unique_weeks = sorted(set(zip(
        iso["year"].astype(int).tolist(),
        iso["week"].astype(int).tolist(),
    )))
    month_candidates: dict[int, list] = {m: [] for m in range(1, 13)}
    for iy, iw in unique_weeks:
        try:
            monday = pd.Timestamp(_datetime.date.fromisocalendar(iy, iw, 1))
        except ValueError:
            continue
        span = pd.date_range(monday, periods=168, freq="h")
        if not set(span).issubset(idx_set):
            continue
        owning_month = (monday + pd.Timedelta(days=3)).month  # Thursday's month
        month_candidates[int(owning_month)].append((iy, iw, monday))

    empty_months = [m for m, c in month_candidates.items() if not c]
    if empty_months:
        raise HTTPException(
            400,
            f"Month(s) {empty_months} have no fully-contained ISO week in the "
            "uploaded profile — cannot sample. The profile must cover complete "
            "Mon–Sun weeks in every month.",
        )

    # ── Sample n_weeks per month ──────────────────────────────────────────
    rng = _np.random.default_rng(config.seed)
    chosen: list = []                       # (month, iso_year, iso_week, monday)
    weeks_per_month: dict[int, int] = {}
    for month in range(1, 13):
        cands = month_candidates[month]
        take = min(config.n_weeks, len(cands))
        weeks_per_month[month] = take
        for pi in sorted(rng.choice(len(cands), size=take, replace=False)):
            iy, iw, monday = cands[int(pi)]
            chosen.append((month, iy, iw, monday))

    # ── Assemble the sampled timestep index + per-snapshot weights ────────
    chosen.sort(key=lambda t: t[3])         # chronological by Monday
    sampled_blocks: list = []
    weight_blocks: list = []
    week_meta: list = []
    for month, iy, iw, monday in chosen:
        span = pd.date_range(monday, periods=168, freq="h")
        days_in_month = _calendar.monthrange(ref_year, month)[1]
        w = days_in_month / (weeks_per_month[month] * 7.0)
        sampled_blocks.append(span)
        weight_blocks.append(_np.full(168, w))
        week_meta.append({
            "month": month,
            "iso_year": iy,
            "iso_week": iw,
            "start": span[0].isoformat(),
            "end": span[-1].isoformat(),
            "weight": round(w, 4),
        })
    sampled_idx = pd.DatetimeIndex(_np.concatenate([b.values for b in sampled_blocks]))
    weights = _np.concatenate(weight_blocks)

    # ── Apply to the network ──────────────────────────────────────────────
    n = PyPSAService.get_network()
    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    _backup_network_ts_to_user_ts(n)
    # Detect whether the user had non-default snapshot_weightings configured
    # BEFORE sampling. Representative-week sampling replaces the snapshot
    # index entirely — the prior weights have no meaningful mapping onto
    # the new sparse index, so they're necessarily overwritten with the
    # sampler-derived rep-week scaling. We can't preserve them safely, but
    # we CAN warn the user that their custom scaling is being discarded so
    # the silent-loss footgun documented for `set_snapshots(MultiIndex)` in
    # CLAUDE.md doesn't bite here. Compare every weight column to the PyPSA
    # default (1.0); anything else counts as "custom".
    _had_custom_weights = False
    try:
        sw_pre = n.snapshot_weightings
        if not sw_pre.empty:
            for _col in sw_pre.columns:
                if not (sw_pre[_col].astype(float) == 1.0).all():
                    _had_custom_weights = True
                    break
    except Exception:
        _had_custom_weights = False
    with PyPSAService.get_lock():
        if is_multi:
            periods = sorted(n.snapshots.get_level_values(0).unique().tolist())
            if not periods:
                raise HTTPException(
                    400, "Multi-period network has no investment periods.",
                )
            mi = _build_period_multiindex(periods, [sampled_idx] * len(periods))
            n.set_snapshots(mi)
            n.investment_periods = periods
            full_weights = _np.concatenate([weights for _ in periods])
        else:
            n.set_snapshots(sampled_idx)
            full_weights = weights
        # Re-apply uploaded profiles — sampled timesteps ⊂ uploaded index so
        # the reindex is exact (no all-NaN columns to skip).
        _reapply_user_ts_to_network(n)
        # Each sampled hour stands for days_in_month / (weeks × 7) hours. Set
        # all three weight columns so the LP objective, generator energy
        # balance and storage SoC equations scale consistently.
        for col in n.snapshot_weightings.columns:
            n.snapshot_weightings[col] = full_weights

    change_log_service.log(
        "update", "Network", "snapshots",
        f"Sampled {config.n_weeks} representative ISO week(s)/month → "
        f"{len(sampled_idx)} timestep(s)"
        + (f" × {len(periods)} period(s) = {len(n.snapshots)} snapshots"
           if is_multi else f" = {len(n.snapshots)} snapshots")
        + (" — NOTE: prior custom snapshot_weightings overwritten with "
           "rep-week scaling (each sampled hour now represents N hours)"
           if _had_custom_weights else ""),
    )
    return {
        "count": len(n.snapshots),
        "n_weeks": config.n_weeks,
        "seed": config.seed,
        "multi_period": is_multi,
        "timesteps_per_period": len(sampled_idx),
        "weeks": week_meta,
    }


# ── Investment Periods ────────────────────────────────────────────────────────

@router.get("/investment_periods")
def get_investment_periods():
    n = PyPSAService.get_network()
    if n.investment_periods.empty:
        return {"periods": [], "weightings": []}
    return {
        "periods": n.investment_periods.tolist(),
        "weightings": df_to_json(n.investment_period_weightings),
    }


@router.post("/investment_periods")
def set_investment_periods(body: InvestmentPeriods):
    """
    Set the list of investment periods, rebuilding ``n.snapshots`` to match.

    PyPSA's ``n.investment_periods = […]`` setter doesn't auto-extend the
    snapshot MultiIndex when periods are added — it raises if the new
    periods aren't already present as level-0 values. To make the GUI's
    "add year" interaction work without forcing the user to manually rebuild
    snapshots, this endpoint handles the three transitions:

      1) Flat snapshots → MultiIndex: promote by replicating the existing
         operational DatetimeIndex under each requested period.
      2) MultiIndex → different MultiIndex (period added / removed): rebuild
         by replicating the FIRST existing period's operational range under
         every new period. User uploads survive via _user_ts.
      3) Empty `periods` on a MultiIndex: demote back to flat using the first
         period's operational range.
    """
    import pandas as pd
    n = PyPSAService.get_network()

    new_periods = sorted({int(p) for p in body.periods})

    with PyPSAService.get_lock():
        is_multi = isinstance(n.snapshots, pd.MultiIndex)

        if not new_periods:
            # Demote to flat snapshots using period-0's timesteps.
            if is_multi:
                # Capture profiles, collapse the MultiIndex → flat (handles the
                # pandas "cannot include dtype 'M' in a buffer" reindex bug),
                # then re-apply profiles aligned to the flat index.
                _backup_network_ts_to_user_ts(n)
                _flatten_snapshot_state(n)
                _reapply_user_ts_to_network(n)
            else:
                # Already flat. PyPSA accepts an empty pd.Index for this.
                n.investment_periods = pd.Index([], dtype="int64")
            return {"count": 0}

        # Determine base operational DatetimeIndex.
        if is_multi:
            existing_periods = sorted(n.snapshots.get_level_values(0).unique().tolist())
            first_p = existing_periods[0]
            base_idx = pd.DatetimeIndex(
                n.snapshots[n.snapshots.get_level_values(0) == first_p]
                .get_level_values(1),
            )
        else:
            existing_periods = []
            base_idx = pd.DatetimeIndex(n.snapshots)

        # Only rebuild snapshots if the period set actually changed.
        if existing_periods != new_periods:
            _backup_network_ts_to_user_ts(n)
            captured_weights = _capture_snapshot_weights_per_timestep(n)
            mi = _build_period_multiindex(new_periods, [base_idx] * len(new_periods))
            n.set_snapshots(mi)
            _reapply_snapshot_weights(n, captured_weights)
            _reapply_user_ts_to_network(n)

        # Set / re-set the periods list. PyPSA validates it matches level-0.
        n.investment_periods = new_periods

        if body.objective_weightings:
            n.investment_period_weightings["objective"] = body.objective_weightings
        if body.years_weightings:
            n.investment_period_weightings["years"] = body.years_weightings

    change_log_service.log(
        "update", "Network", "investment_periods",
        f"Set investment periods: {new_periods} "
        f"(operational range × {len(base_idx)} steps)",
    )
    return {"count": len(new_periods)}


@router.patch("/investment_period_weightings")
def update_investment_period_weightings(body: dict):
    """
    Update per-period weights in `n.investment_period_weightings`.

    Body shape options (combinable in one call):

      • `{"all_years": <float>}` — set every period's `years` column.
      • `{"all_objective": <float>}` — set every period's `objective` column.
      • `{"updates": {<period>: {"years"?, "objective"?}, ...}}` — per-period
        overrides. Keys are integer years (matching `n.investment_periods`);
        string keys are coerced.

    `years` represents the number of calendar years a period stands in for
    (PyPSA's discounting uses this as the integration window). `objective`
    is the discount/weight applied to that period's operational + capital
    contribution in the LP objective. Defaults are both 1.0 — set them
    explicitly when running multi-period.
    """
    n = PyPSAService.get_network()
    if n.investment_periods.empty:
        raise HTTPException(
            400,
            "Network has no investment periods. Configure them first via "
            "POST /network/investment_periods.",
        )
    df = n.investment_period_weightings
    with PyPSAService.get_lock():
        all_years = body.get("all_years")
        all_obj = body.get("all_objective")
        if all_years is not None:
            try:
                df["years"] = float(all_years)
            except (TypeError, ValueError):
                raise HTTPException(400, f"`all_years` must be a number, got {all_years!r}")
        if all_obj is not None:
            try:
                df["objective"] = float(all_obj)
            except (TypeError, ValueError):
                raise HTTPException(400, f"`all_objective` must be a number, got {all_obj!r}")
        updates = body.get("updates") or {}
        if not isinstance(updates, dict):
            raise HTTPException(400, "`updates` must be a dict keyed by period (year).")
        applied = 0
        for key, vals in updates.items():
            if not isinstance(vals, dict):
                continue
            try:
                period = int(key)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Bad period key {key!r}")
            if period not in df.index:
                raise HTTPException(400, f"Unknown period {period}")
            for col in ("years", "objective"):
                if col in vals:
                    try:
                        df.at[period, col] = float(vals[col])
                        applied += 1
                    except (TypeError, ValueError):
                        raise HTTPException(
                            400, f"Bad {col} value for period {period}: {vals[col]!r}",
                        )
        change_log_service.log(
            "update", "Network", "investment_period_weightings",
            f"Updated period weightings: all_years={all_years}, "
            f"all_objective={all_obj}, per-row updates={applied}",
        )
    return {
        "periods": n.investment_periods.tolist(),
        "weightings": df_to_json(n.investment_period_weightings),
    }


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

    attr = _COMPONENT_ATTRS[component_class]
    n = PyPSAService.get_network()
    df = getattr(n, attr)

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
            coerced[col] = bool(value) if value is not None else value
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
                if col.endswith("_max") or col == "lifetime":
                    coerced[col] = float("inf")
                elif col == "e_sum_min":
                    coerced[col] = float("-inf")
                else:
                    coerced[col] = float("nan")  # pandas treats this as missing
                continue
            try:
                coerced[col] = float(value)
            except (TypeError, ValueError):
                raise HTTPException(400,
                    f"Column '{col}' is numeric ({col_dtype}); got non-numeric "
                    f"value {value!r}.")
            continue
        # Strings / objects pass through. We still cast to str if the user
        # sent a number into a string column so dtype stays clean.
        if pd.api.types.is_string_dtype(col_dtype) or pd.api.types.is_object_dtype(col_dtype):
            coerced[col] = "" if value is None else str(value)
            continue
        coerced[col] = value

    with PyPSAService.get_lock():
        # If the bulk update sets `carrier`, ensure the carrier row exists with
        # catalog metadata first — same auto-add behavior as PUT.
        if component_class != "Carrier" and "carrier" in coerced:
            new_carrier = coerced["carrier"]
            if isinstance(new_carrier, str):
                ensure_carrier(n, new_carrier)
        for col, value in coerced.items():
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
                    n.export_to_netcdf(str(tmp))
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
                n.import_from_netcdf(str(tmp))
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

@router.get("/timeseries")
def list_timeseries():
    n = PyPSAService.get_network()
    result = []
    for component in ["generators", "loads", "storage_units", "stores", "lines", "links"]:
        ts_store = getattr(n, f"{component}_t", None)
        if ts_store is None:
            continue
        comp_class = _ATTR_TO_CLASS.get(component, component)
        for attr in ts_store:
            df = ts_store[attr]
            if not df.empty:
                # Filter transient column names (vintage clones'
                # cloned-from-parent profiles, VOLL slack columns) so the
                # Time-Series tab list doesn't show LP scaffolding mid-solve.
                cols = _filter_transient_names(comp_class, df.columns.tolist())
                if not cols:
                    continue
                result.append({
                    "component": component,
                    "attribute": attr,
                    "column_count": len(cols),
                    "columns": cols,
                })
    return result


@router.get("/timeseries/{component}/{attribute}")
def get_timeseries(component: str, attribute: str, columns: str | None = None):
    """
    Return a time-series DataFrame as JSON.

    Optional ``columns`` query param: comma-separated list of column names to
    return (e.g. ``?columns=Wind+BE,Solar+BE``). Prefer user-uploaded data
    (from _user_ts) for any requested column that was uploaded by the user.
    """
    n = PyPSAService.get_network()
    ts_store = getattr(n, f"{component}_t", None)
    if ts_store is None:
        raise HTTPException(404, f"Component '{component}' not found")

    net_df = ts_store.get(attribute)
    wanted = [c.strip() for c in columns.split(",")] if columns else None

    if wanted:
        # For each requested column: prefer user-uploaded Series, fall back to network data
        series_list: list[pd.Series] = []
        for col in wanted:
            user_series = _user_ts.get((component, attribute, col))
            if user_series is not None:
                series_list.append(user_series.rename(col))
            elif net_df is not None and not net_df.empty and col in net_df.columns:
                series_list.append(net_df[col])
        df = pd.concat(series_list, axis=1) if series_list else pd.DataFrame()
    else:
        # No column filter — build from all user-uploaded columns for this
        # (component, attribute), then fall back to network data for the rest.
        prefix = (component, attribute)
        user_series_list = [
            s.rename(k[2]) for k, s in _user_ts.items() if k[:2] == prefix
        ]
        if user_series_list:
            df = pd.concat(user_series_list, axis=1)
        elif net_df is not None and not net_df.empty:
            df = net_df
        else:
            df = pd.DataFrame()

    if df is None or df.empty:
        return {"index": [], "columns": [], "data": []}

    # Drop transient columns (vintage clones' cloned profiles, VOLL slack
    # columns) so the Time-Series viewer doesn't render LP scaffolding
    # mid-solve. Apply AFTER user-supplied `wanted` resolution so an
    # explicit ?columns=foo@2026 request gets a clean empty payload
    # rather than a confusing partial frame.
    comp_class = _ATTR_TO_CLASS.get(component, component)
    keep_cols = _filter_transient_names(comp_class, list(df.columns))
    if len(keep_cols) != len(df.columns):
        df = df[keep_cols]
    if df.empty or len(df.columns) == 0:
        return {"index": [], "columns": [], "data": []}

    # Vectorised conversion. The previous nested Python loop with per-cell
    # isinstance/math.isfinite calls took 30+ s for tables in the 8760 × 100
    # range — long enough to block the event loop and time out the periodic
    # /network/meta poll. NumPy + DatetimeIndex.strftime do the same work in
    # well under a second.
    # MultiIndex on multi-period: emit ISO timesteps + parallel periods array,
    # same convention as `_ts_payload` and `/network/snapshots`. Without this,
    # `str(s)` on the tuple yields garbage like "(2026, Timestamp('...'))".
    periods: list | None = None
    if isinstance(df.index, pd.MultiIndex):
        try:
            periods = [int(p) for p in df.index.get_level_values(0)]
        except (TypeError, ValueError):
            periods = [str(p) for p in df.index.get_level_values(0)]
        timesteps = df.index.get_level_values(1)
        if isinstance(timesteps, pd.DatetimeIndex):
            idx = timesteps.strftime("%Y-%m-%dT%H:%M:%S").tolist()
        else:
            idx = [str(s) for s in timesteps]
    elif isinstance(df.index, pd.DatetimeIndex):
        idx = df.index.strftime("%Y-%m-%dT%H:%M:%S").tolist()
    else:
        idx = [str(s) for s in df.index]

    arr = df.to_numpy(dtype=float, copy=False)
    arr_obj = arr.astype(object)
    arr_obj[~np.isfinite(arr)] = None
    data = arr_obj.tolist()

    payload = {"index": idx, "columns": df.columns.tolist(), "data": data}
    if periods is not None:
        payload["periods"] = periods
    return payload


@router.put("/timeseries/{component}/{attribute}")
def set_timeseries(component: str, attribute: str, body: dict):
    import pandas as pd
    n = PyPSAService.get_network()
    ts_store = getattr(n, f"{component}_t", None)
    if ts_store is None:
        raise HTTPException(404)
    with PyPSAService.get_lock():
        idx = pd.DatetimeIndex(body.get("index", []))
        cols = body.get("columns", [])
        data = body.get("data", [])
        df = pd.DataFrame(data, index=idx, columns=cols)
        ts_store[attribute] = df
        # Persist edits in _user_ts so they survive project reload.
        # Hold _user_ts_lock for the mutation — autosave's
        # _serialize_user_ts iterates the dict concurrently and would
        # raise RuntimeError("dictionary changed size during iteration")
        # without serialization. Inner lock; outer is the PyPSA lock.
        with _user_ts_lock:
            for col in df.columns:
                _user_ts[(component, attribute, col)] = df[col].copy()
    cols_preview = ", ".join(list(df.columns)[:3]) + ("…" if len(df.columns) > 3 else "")
    change_log_service.log(
        "timeseries", component.capitalize(), cols_preview,
        f"Edited {component}/{attribute} time series: "
        f"{len(df.columns)} column(s), {len(df)} rows",
    )
    return {"rows": len(df), "columns": len(df.columns)}


@router.post("/timeseries/upload")
async def upload_timeseries(
    component: str,
    attribute: str,
    file: UploadFile = File(...),
    period: int | None = None,
):
    """
    Upload a CSV of time-series data for one (component, attribute) pair.

    Behaviour by network snapshot type:

    - Flat DatetimeIndex snapshots → period is ignored; CSV's DatetimeIndex is
      stored as-is (single-period workflow).
    - MultiIndex (period, timestep) snapshots, period=None → CSV stored with
      its DatetimeIndex. At apply time, the values are broadcast under every
      period via level-1 lookup (canonical "same operational year per period").
    - MultiIndex snapshots, period=<int> → CSV's DatetimeIndex is promoted to
      a MultiIndex by prepending `period`. Stored in _user_ts with that
      MultiIndex; subsequent uploads with a different `period` for the same
      column stitch (replace that period's rows, keep the others). Required
      for "different weather year per period" workflows.
    """
    import io

    import numpy as _np
    import pandas as pd
    content = await read_capped(file)
    df = pd.read_csv(io.BytesIO(content), index_col=0, parse_dates=True)
    n = PyPSAService.get_network()
    ts_store = getattr(n, f"{component}_t", None)
    if ts_store is None:
        raise HTTPException(404, f"Component '{component}' not found")

    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    if period is not None:
        if not is_multi:
            raise HTTPException(
                400, "?period=… requires MultiIndex snapshots. "
                "Build them first via /snapshots/multi_period.",
            )
        try:
            period_int = int(period)
        except (TypeError, ValueError):
            raise HTTPException(400, f"period must be an integer, got {period!r}")
        if period_int not in n.investment_periods:
            raise HTTPException(
                400, f"period={period_int} is not in n.investment_periods "
                f"({list(n.investment_periods)})",
            )
        # Promote the CSV's DatetimeIndex to MultiIndex(period, timestep).
        new_mi = pd.MultiIndex.from_arrays(
            [_np.full(len(df), period_int), df.index],
            names=["period", "timestep"],
        )
        df.index = new_mi

    with PyPSAService.get_lock():
        with _user_ts_lock:
            for col in df.columns:
                new_s = df[col]
                if period is not None:
                    existing = _user_ts.get((component, attribute, col))
                    if existing is not None and isinstance(existing.index, pd.MultiIndex):
                        # Drop existing rows for this period, then concat.
                        keep = existing[existing.index.get_level_values(0) != int(period)]
                        merged = pd.concat([keep, new_s]).sort_index()
                        _user_ts[(component, attribute, col)] = merged
                    else:
                        # First per-period upload for this column — replaces any
                        # earlier broadcast (DatetimeIndex) entry.
                        _user_ts[(component, attribute, col)] = new_s.copy()
                else:
                    _user_ts[(component, attribute, col)] = new_s.copy()
        # Grow n.snapshots if the upload covers more rows than the current
        # operational range. Matches the behaviour of the component-specific
        # upload routes (/generators/upload_profile, /loads/upload_profile)
        # so the user doesn't have to know which endpoint the GUI chose.
        # MultiIndex networks grow per-period; flat networks grow directly.
        # No-op (returns False) when the upload is per-period (`period=` set)
        # because that path stores a MultiIndex series, which the helper
        # already filters out.
        _ensure_snapshots_cover_user_ts(n)
        # Apply to the live network's _t table immediately (so the next GET
        # /timeseries reflects the upload without requiring a snapshot rebuild).
        # _reapply handles all three cases (DatetimeIndex+flat, DatetimeIndex+
        # MultiIndex broadcast, MultiIndex+MultiIndex direct).
        _reapply_user_ts_to_network(n)

    cols_preview = ", ".join(list(df.columns)[:3]) + ("…" if len(df.columns) > 3 else "")
    change_log_service.log(
        "timeseries", component.capitalize(), file.filename or cols_preview,
        f"Uploaded {component}/{attribute} time series '{file.filename}': "
        f"{len(df.columns)} column(s), {len(df)} rows"
        + (f", period={period}" if period is not None else ""),
    )
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "period": period,
        "mode": "per_period" if period is not None else ("broadcast" if is_multi else "flat"),
    }


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


# ── User-uploaded time-series delete ─────────────────────────────────────────
# Single endpoint that drops `(component, attribute, name)` entries from
# `_user_ts`. `component` is the PyPSA `_t` attr name ('loads' / 'generators'
# / 'links' / 'stores' / 'storage_units'). When `name` is omitted, EVERY
# uploaded profile under `(component, attribute, *)` is dropped — useful for
# a future "Clear all profiles on this tab" UX. The matching `_t` slot is
# reset to PyPSA's default (column dropped from `_t.<attribute>`) via the
# explicit column-drop below, so the LP falls back to whatever the static
# attribute holds.
@router.delete("/timeseries")
def delete_timeseries(
    component: str,
    attribute: str,
    name: str | None = None,
):
    """
    Delete one (or all matching) user-uploaded time-series entries.

    Query params:
      - component: 'loads' | 'generators' | 'links' | 'stores' | 'storage_units'
      - attribute: 'p_set' / 'p_max_pu' / 'p_min_pu' / 'marginal_cost' / ...
      - name: optional component name. When omitted, drops every profile
        matching (component, attribute, *).
    """
    if component not in {"loads", "generators", "links", "stores", "storage_units"}:
        raise HTTPException(400, f"Unsupported component '{component}'")
    n = PyPSAService.get_network()

    with _user_ts_lock:
        keys_to_drop = [
            (c, a, col) for (c, a, col) in _user_ts
            if c == component and a == attribute and (name is None or col == name)
        ]
        for key in keys_to_drop:
            del _user_ts[key]
    dropped_names = [col for (_, _, col) in keys_to_drop]
    if not dropped_names:
        # 404 over silent-success — the frontend uses this to surface
        # "Profile not found" vs a successful drop. Same `_user_ts` key
        # may have already been cleared by a concurrent autosave / reload,
        # so this is informational, not destructive.
        raise HTTPException(404, f"No uploaded profile found for {component}/{attribute}/{name or '*'}")

    # Drop the matching columns from n.<component>_t.<attribute>. Without
    # this the `_t` table keeps the stale column until next reset — the LP
    # would still see the old profile and the next save would re-serialise
    # it. `_reapply_user_ts_to_network` only writes; it doesn't drop.
    with PyPSAService.get_lock():
        t_obj = getattr(n, f"{component}_t", None)
        if t_obj is not None:
            attr_df = getattr(t_obj, attribute, None)
            if attr_df is not None and hasattr(attr_df, "columns"):
                cols_to_drop = [c for c in dropped_names if c in attr_df.columns]
                if cols_to_drop:
                    attr_df.drop(columns=cols_to_drop, inplace=True)
        _reapply_user_ts_to_network(n)

    names_preview = ", ".join(dropped_names[:3]) + ("…" if len(dropped_names) > 3 else "")
    # Component → display class: 'loads' → 'Load', 'generators' → 'Generator',
    # 'links' → 'Link'. Plural-strip the trailing 's' for the changelog.
    display_class = component[:-1].capitalize() if component.endswith("s") else component.capitalize()
    change_log_service.log(
        "timeseries", display_class, names_preview,
        f"Deleted {component} {attribute} profile(s): {len(dropped_names)} entry(ies)",
    )
    return {
        "deleted": dropped_names,
        "component": component,
        "attribute": attribute,
        "snapshot_count": len(n.snapshots),
    }
