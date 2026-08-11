from __future__ import annotations

import io
import math
from typing import Any, NamedTuple

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

_EARTH_KM = 6371.0


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def _bus_coord(n, bus_name: str) -> tuple[float, float] | None:
    if bus_name not in n.buses.index:
        return None
    try:
        x = float(n.buses.at[bus_name, "x"])
        y = float(n.buses.at[bus_name, "y"])
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    # PyPSA's Bus.x / Bus.y default to 0.0, so the exact pair means "never
    # set", not "the Gulf of Guinea". Without this, every line touching an
    # unplaced bus is rewritten to the great-circle distance to Null Island
    # and stored as fact — see tests/test_line_lengths.py and
    # docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
    #
    # BOTH exactly zero: a bus at (0, 51.478) is Greenwich and stays valid.
    if x == 0.0 and y == 0.0:
        return None
    # Mirrors the range check in frontend/src/utils/geo.ts's busLatLng. The
    # frontend hides a bus outside these bounds and counts it as "unplaced"
    # in UnplacedBusesPanel, but until this check _bus_coord had no range
    # check at all — a bus at y == 91 was hidden by the map and reported as
    # unplaced while recalculate_lengths still measured a haversine distance
    # to it and wrote that into n.lines.length. Reachable in practice:
    # PropertiesPanel's Longitude/Latitude fields are unbounded NumInputs and
    # BusCreate.x / BusCreate.y (models/schemas.py) are plain unbounded
    # floats. Do not remove the frontend's check when reading this — both
    # layers must reject out-of-range coordinates.
    if not (-90.0 <= y <= 90.0 and -180.0 <= x <= 180.0):
        return None
    return x, y


def _line_haversine_km(n, bus0: str, bus1: str) -> float | None:
    c0 = _bus_coord(n, bus0)
    c1 = _bus_coord(n, bus1)
    if c0 is None or c1 is None:
        return None
    return _haversine_km(c0[0], c0[1], c1[0], c1[1])


_IMPEDANCE_FIELDS = ("r", "x", "b")


def _impedance_preview(
    line_name: str, old_length: float, new_length: float, old: dict[str, float]
) -> dict | None:
    """
    What a per-km-preserving rescale WOULD do. Never mutates.

    Returns None when there is no choice to offer — an all-zero impedance
    scales to zero whatever the length does.

    The relative change is identical for r, x and b (each is multiplied by the
    same length ratio), so one number describes all three.

    `rel_change` is a MAGNITUDE (`abs(ratio - 1.0)`), not a signed delta — a
    shrinking line reports the same positive number as a growing one at the
    same ratio. This is deliberate, not an oversight: downstream, previews
    get partitioned by `rel_change <= <threshold>` to decide what to apply
    WITHOUT asking the user. If this were signed, a line whose length HALVED
    (ratio 0.5, signed change -0.5) would read as -0.5, which is <= any
    positive threshold, and its impedance would be silently halved with no
    prompt — the exact silent rewrite this feature exists to prevent. Keep
    the `abs()`; a shrink must clear the same bar a growth does.
    """
    if all(float(old.get(k, 0.0) or 0.0) == 0.0 for k in _IMPEDANCE_FIELDS):
        return None

    reason: str | None = None
    if not (old_length > 0):
        reason = "old_length<=0"      # per-km undefined — nothing to preserve
    elif not (new_length > 0):
        reason = "new_length<=0"      # would zero the impedance

    if reason is not None:
        new = dict(old)
        rel = 0.0
    else:
        ratio = new_length / old_length
        new = {k: float(old.get(k, 0.0) or 0.0) * ratio for k in _IMPEDANCE_FIELDS}
        # Magnitude, on purpose — see the docstring above. Do NOT drop the
        # abs(): a shrinking line (ratio < 1) must report the same positive
        # rel_change a growing line at the same ratio would, or a threshold
        # comparison downstream lets shrinks slip through unprompted.
        rel = abs(ratio - 1.0)

    return {
        "name": line_name,
        "old_length": float(old_length),
        "new_length": float(new_length),
        "old": {k: float(old.get(k, 0.0) or 0.0) for k in _IMPEDANCE_FIELDS},
        "new": new,
        "rel_change": rel,
        "skipped_reason": reason,
    }


class _RecomputeResult(NamedTuple):
    """
    `_recompute_lengths_for_bus` counts two different things and they are NOT
    interchangeable: `updated` is how many lines actually had `length`
    rewritten (every line that resolved a haversine distance); `previews` is
    the (possibly shorter) list of impedance-rescale offers, which
    `_impedance_preview` omits for an all-zero-impedance line even though its
    length WAS rewritten. A changelog that reports `len(previews)` undercounts
    whenever a zero-impedance line is among the ones touched.
    """
    updated: int
    previews: list[dict]


def _recompute_lengths_for_bus(n, bus_name: str) -> _RecomputeResult:
    """
    Rewrite line.length for every line touching `bus_name`, and return both
    the rewrite count and one preview per line whose impedance a
    per-km-preserving rescale would change.

    Length is rewritten here because it follows from geometry. Impedance is a
    modelling choice and is only PREVIEWED — see _impedance_preview and
    POST /lines/rescale_impedances. The caller must hold PyPSAService.get_lock().
    """
    if n.lines.empty:
        return _RecomputeResult(0, [])
    mask = (n.lines["bus0"] == bus_name) | (n.lines["bus1"] == bus_name)
    updated = 0
    previews: list[dict] = []
    for line_name in n.lines.index[mask]:
        b0 = str(n.lines.at[line_name, "bus0"])
        b1 = str(n.lines.at[line_name, "bus1"])
        d = _line_haversine_km(n, b0, b1)
        if d is None:
            continue
        old_length = float(n.lines.at[line_name, "length"])
        old = {k: float(n.lines.at[line_name, k]) for k in _IMPEDANCE_FIELDS}
        n.lines.at[line_name, "length"] = float(d)
        updated += 1
        p = _impedance_preview(str(line_name), old_length, float(d), old)
        if p is not None:
            previews.append(p)
    return _RecomputeResult(updated, previews)


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


def _build_period_multiindex(periods, blocks) -> pd.MultiIndex:
    """
    Build the `(period, timestep)` snapshot MultiIndex from parallel `periods`
    (year keys) + per-period `blocks` (each a DatetimeIndex of that period's
    operational timesteps). To replicate ONE operational range under every
    period, pass `[idx] * len(periods)`.

    ALWAYS sets `mi.name = "snapshot"`. PyPSA's `set_snapshots(MultiIndex)` only
    inherits the name on flat→multi transitions; on multi→multi REBUILDS the new
    MultiIndex's name=None wins and propagates to every `_t` table — xarray then
    emits dim `dim_0` and the next LP fails on `.sel(snapshot=sns)`. Centralising
    the builder makes that footgun impossible to forget.
    """
    period_level = np.concatenate([np.full(len(blk), p) for p, blk in zip(periods, blocks)])
    timestep_level = pd.DatetimeIndex(np.concatenate([blk.values for blk in blocks]))
    mi = pd.MultiIndex.from_arrays(
        [period_level, timestep_level], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    return mi


def _infer_snapshot_freq(n) -> str | None:
    """
    The snapshot index's resolution, as a pandas offset alias ("h", "3h", "D").

    The Model Horizon page used to render its own form state here, which was
    seeded to "h" at mount and never read back from the network — so a
    3-hourly MultiIndex and a Daily flat index both reported "Hourly (h)".

    MultiIndex networks are measured over the FIRST period's slice only: the
    flattened timestep level contains a discontinuity at each period seam
    (period P's last hour → period P+1's first), which would read as irregular.

    `pd.infer_freq` is tried first because it names calendar frequencies ("D",
    "MS", "W") that a raw timedelta cannot. It returns None for a
    representative-week index — contiguous 168-hour blocks separated by gaps —
    whose resolution is nevertheless hourly, so fall back to the modal
    successive delta. Returns None when neither resolves; the UI renders that
    as "irregular" rather than guessing.
    """
    sns = n.snapshots
    try:
        if isinstance(sns, pd.MultiIndex):
            level0 = sns.get_level_values(0)
            if len(level0) == 0:
                return None
            first = level0[0]
            idx = pd.DatetimeIndex(sns[level0 == first].get_level_values(1))
        else:
            idx = pd.DatetimeIndex(sns)
    except (ValueError, TypeError):
        # `pd.DatetimeIndex(...)` raises (e.g. `DateParseError`, a `ValueError`
        # subclass) on a non-parseable object index. No current GUI path
        # produces one, but this helper runs unconditionally at the top of
        # `get_snapshots` — degrade to "irregular" rather than 500ing the
        # page's primary endpoint.
        return None
    if len(idx) < 2:
        return None
    try:
        inferred = pd.infer_freq(idx)
    except (ValueError, TypeError):
        inferred = None
    if inferred:
        return inferred
    deltas = idx.to_series().diff().dropna()
    if deltas.empty:
        return None
    modal = deltas.mode()
    if modal.empty:
        return None
    hours = modal.iloc[0].total_seconds() / 3600.0
    if hours <= 0:
        return None
    if hours == 1.0:
        return "h"
    if float(hours).is_integer():
        return f"{int(hours)}h"
    return None


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
# Tolerance (kV) when matching declared v_nom_X against actual bus voltages.
# Loose enough to accept 132 vs 132.0001 floats but tight enough to reject any
# real mismatch (380 vs 220 differs by 160 kV — far past 0.5).
_VNOM_TOL_KV = 0.5


@router.get("/transformers/types")
def list_transformer_types():
    """Return the catalogue of common voltage-step presets for the GUI."""
    return _TRANSFORMER_PRESETS


def _validate_transformer_voltage(n, bus0: str, bus1: str,
                                  v_nom_0: float | None, v_nom_1: float | None) -> None:
    """
    Reject creation/edit when the declared step doesn't match the buses.

    `v_nom_0 is None` OR `v_nom_1 is None` ⇒ user opted out of the check
    (omitted the field from the payload, or picked Custom and left it
    blank). Allows either orientation — bus0 may be the high or low side.
    Legacy `<= 0` sentinel still honoured for old project files / external
    callers that send 0.0.
    """
    if v_nom_0 is None or v_nom_1 is None or v_nom_0 <= 0.0 or v_nom_1 <= 0.0:
        return
    if bus0 not in n.buses.index:
        raise HTTPException(404, f"Bus '{bus0}' not found")
    if bus1 not in n.buses.index:
        raise HTTPException(404, f"Bus '{bus1}' not found")
    actual_0 = float(n.buses.at[bus0, "v_nom"]) if "v_nom" in n.buses.columns else 0.0
    actual_1 = float(n.buses.at[bus1, "v_nom"]) if "v_nom" in n.buses.columns else 0.0
    same_orientation = (
        abs(actual_0 - v_nom_0) <= _VNOM_TOL_KV
        and abs(actual_1 - v_nom_1) <= _VNOM_TOL_KV
    )
    swapped_orientation = (
        abs(actual_0 - v_nom_1) <= _VNOM_TOL_KV
        and abs(actual_1 - v_nom_0) <= _VNOM_TOL_KV
    )
    if not (same_orientation or swapped_orientation):
        raise HTTPException(
            400,
            f"Voltage mismatch: transformer expects {v_nom_0:g}/{v_nom_1:g} kV "
            f"but bus '{bus0}' is {actual_0:g} kV and bus '{bus1}' is {actual_1:g} kV. "
            "Adjust the bus v_nom values or pick a transformer type that matches.",
        )


def _enrich_transformer_voltage(rows: list[dict], n) -> list[dict]:
    """
    Inject derived v_nom_0/v_nom_1 from connected buses into each row.

    PyPSA stores voltages on the buses, not on the Transformer. The GUI
    surfaces these as columns in the bottom-panel table and the right-panel
    properties view, so we attach them here on the read path.
    """
    if "v_nom" not in n.buses.columns:
        return rows
    for r in rows:
        bus0 = r.get("bus0")
        bus1 = r.get("bus1")
        r["v_nom_0"] = float(n.buses.at[bus0, "v_nom"]) if bus0 in n.buses.index else None
        r["v_nom_1"] = float(n.buses.at[bus1, "v_nom"]) if bus1 in n.buses.index else None
    return rows


@router.get("/transformers")
def get_transformers():
    rows = _get_component("Transformer", "transformers")
    return _enrich_transformer_voltage(rows, PyPSAService.get_network())


def _sanitise_transformer_type(n, payload: dict) -> dict:
    """
    The GUI's transformer presets ("380/220 kV" etc.) are stored on
    `transformer.type` for display purposes, but PyPSA treats `type` as a
    foreign key into `n.transformer_types` and crashes at solve time with
    "type does not exist in n.transformer_types" when it isn't registered.

    Our presets ARE NOT PyPSA transformer types — they're UI helpers that
    already filled in the explicit r/x/s_nom values. So if the user-supplied
    `type` isn't in n.transformer_types, drop it (PyPSA will then use the
    explicit parameters) but keep its label out of harm's way.
    """
    raw_type = payload.get("type", "")
    if not raw_type:
        return payload
    try:
        known = set(n.transformer_types.index)
    except Exception:
        known = set()
    if raw_type in known:
        return payload  # legitimate PyPSA type — pass through unchanged
    # Strip the type so n.add() falls back to the explicit s_nom / x we sent.
    cleaned = dict(payload)
    cleaned["type"] = ""
    return cleaned


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
    freq = _infer_snapshot_freq(n)
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
            "freq": freq,
        }
    snaps = [s.isoformat() if hasattr(s, "isoformat") else str(s) for s in sns]
    weightings = df_to_json(n.snapshot_weightings) if not n.snapshot_weightings.empty else []
    return {
        "count": len(sns), "snapshots": snaps, "weightings": weightings,
        "ts_start": ts_start, "ts_end": ts_end,
        "can_sample_weeks": can_sample_weeks,
        "freq": freq,
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
    # A bare ISO `snapshot` key is ambiguous on a multi-period network — see
    # the tolerant `iso_to_idx[iso] = idx` registration above, which is
    # last-write-wins across periods and so always resolves to the LAST
    # period. Same hazard as `update_snapshot_weightings`'s PATCH path;
    # tracked here too so a CSV that lost its `period|` prefix (Excel, a
    # hand-edited file) doesn't silently write every row into the wrong
    # period with no trace in the response or audit log.
    ambiguous_bare_keys = 0
    for row in reader:
        key = (row.get("snapshot") or "").strip()
        if not key or key not in iso_to_idx:
            skipped += 1
            continue
        if is_multi and "|" not in key:
            ambiguous_bare_keys += 1
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
            f"{skipped} row(s) skipped (no match)."
            + (f" — WARNING: {ambiguous_bare_keys} bare-ISO key(s) on a multi-period "
               "network each resolved to the LAST period; send `period|iso` to target "
               "a specific period." if ambiguous_bare_keys else ""),
        )
    return {
        "applied": applied,
        "skipped": skipped,
        "columns": cols,
        "ambiguous_bare_keys": ambiguous_bare_keys,
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
        # A bare ISO key on a MultiIndex network is ambiguous — it is
        # registered once per period and last-write-wins, so it silently
        # resolves to the LAST period. The GUI now sends `period|iso`; anything
        # still sending bare keys (older clients, chat tools) gets recorded in
        # the audit log rather than writing to a surprising row in silence.
        ambiguous_bare_keys = 0
        for key, vals in updates.items():
            if not isinstance(vals, dict):
                continue
            if key in iso_to_idx:
                idx = iso_to_idx[key]
                if is_multi and "|" not in str(key):
                    ambiguous_bare_keys += 1
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
            f"Updated snapshot weightings: all={all_val}, per-row updates={applied}"
            + (f" — WARNING: {ambiguous_bare_keys} bare-ISO key(s) on a multi-period "
               "network each resolved to the LAST period; send `period|iso` to target "
               "a specific period." if ambiguous_bare_keys else ""),
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
        # True when the network carried non-default snapshot_weightings before
        # sampling. Rep-week sampling necessarily replaces them (the prior
        # weights have no mapping onto the new sparse index), and until now that
        # was recorded only in the audit log — the user was never told.
        "had_custom_weights": _had_custom_weights,
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
      2) MultiIndex → different MultiIndex (period added / removed): each
         period that survives keeps ITS OWN operational range (needed for
         "Different year per period" multi-year weather data — 2030→2019,
         2040→2020, …); only a genuinely new period inherits the first
         existing period's range as a template. User uploads survive via
         _user_ts.
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

        # Determine each period's operational block. A period that exists both
        # before and after keeps ITS OWN timesteps — the previous code rebuilt
        # every period from the FIRST period's range, which silently destroyed
        # the "Different year per period" setup (2030→2019 weather, 2040→2020,
        # …) the moment the user added or removed a year.
        if is_multi:
            level0 = n.snapshots.get_level_values(0)
            existing_periods = sorted(level0.unique().tolist())
            existing_blocks = {
                int(p): pd.DatetimeIndex(
                    n.snapshots[level0 == p].get_level_values(1),
                )
                for p in existing_periods
            }
            base_idx = existing_blocks[int(existing_periods[0])]
        else:
            existing_periods = []
            existing_blocks = {}
            base_idx = pd.DatetimeIndex(n.snapshots)

        # Only rebuild snapshots if the period set actually changed.
        if existing_periods != new_periods:
            _backup_network_ts_to_user_ts(n)
            captured_weights = _capture_snapshot_weights_per_timestep(n)
            # Surviving periods keep their own block; a genuinely new period
            # inherits the first existing period's range as a template (on a
            # flat→multi promotion there is only one range, so every period
            # legitimately gets it).
            blocks = [existing_blocks.get(int(p), base_idx) for p in new_periods]
            mi = _build_period_multiindex(new_periods, blocks)
            n.set_snapshots(mi)
            _reapply_snapshot_weights(n, captured_weights)
            _reapply_user_ts_to_network(n)

        # Set / re-set the periods list. PyPSA validates it matches level-0.
        n.investment_periods = new_periods

        if body.objective_weightings:
            n.investment_period_weightings["objective"] = body.objective_weightings
        if body.years_weightings:
            n.investment_period_weightings["years"] = body.years_weightings

        # Read inside the lock — the rest of this handler holds it, and a
        # concurrent request between lock-release and this read could change
        # the snapshot count out from under the log line.
        snapshot_count = len(n.snapshots)
        # "existing periods kept their own operational range" is only true on
        # the actual multi→multi rebuild branch above. It was previously
        # logged unconditionally, which was wrong in two other reachable
        # cases: a flat→multi promotion (nothing was "kept" — there were no
        # existing periods, every period got the same templated range) and
        # the no-rebuild path (the period set didn't change, so the rebuild
        # was skipped entirely; nothing was rebuilt, let alone "kept").
        promoted = not is_multi
        rebuilt = existing_periods != new_periods
        if promoted:
            range_note = "promoted from flat snapshots; every period shares the previous operational range"
        elif rebuilt:
            range_note = "existing periods kept their own operational range; new periods inherited the first period's range as a template"
        else:
            range_note = "period set unchanged; snapshot rebuild skipped"

    change_log_service.log(
        "update", "Network", "investment_periods",
        f"Set investment periods: {new_periods} "
        f"({snapshot_count} snapshots total; {range_note})",
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


# ── User-uploaded time series store ───────────────────────────────────────────
# Each column is stored as an independent pd.Series keyed by
# (component, attribute, column_name).  Using a per-column key avoids every
# pandas index-alignment pitfall: uploading a column with 2024 timestamps will
# never corrupt another column that was uploaded with 2026 timestamps, and a
# re-upload of any column simply overwrites its own entry.
_user_ts: dict[tuple[str, str, str], pd.Series] = {}

# Guards every read/write of _user_ts. The PyPSA-network lock protects the
# PyPSA DataFrames; this lock protects this Python-side store independently
# so a concurrent upload + autosave can't trip
# `RuntimeError: dictionary changed size during iteration` inside
# _serialize_user_ts / _restore_user_ts.
import threading as _ts_threading

_user_ts_lock = _ts_threading.RLock()


def _user_ts_rename_asset(component_attr: str, old_name: str, new_name: str) -> int:
    """
    Re-key `_user_ts` entries when a component is renamed via PUT.

    Without this, a `PUT /generators/Solar` with `{"name": "Solar_new"}`
    renames in PyPSA + drops the vintage_bounds entry but leaves
    `_user_ts[("generators", "p_max_pu", "Solar")]` orphaned. Next save
    persists it; next load, `_reapply_user_ts_to_network` skips it
    (because `col not in n.generators.index`) and the profile is silently
    lost. Move every matching key to the new name so the profile follows
    the rename.

    Returns the number of entries re-keyed. No-op when no entries match.
    """
    if old_name == new_name:
        return 0
    with _user_ts_lock:
        keys_to_move = [
            (comp, attr, col) for (comp, attr, col) in _user_ts
            if comp == component_attr and col == old_name
        ]
        for key in keys_to_move:
            comp, attr, _ = key
            _user_ts[(comp, attr, new_name)] = _user_ts.pop(key)
    return len(keys_to_move)


def _user_ts_delete_asset(component_attr: str, name: str) -> int:
    """
    Drop `_user_ts` entries for a deleted component so they don't
    accumulate forever in saved projects (each save would serialise the
    orphan; each load would silently drop it during reapply because the
    component is gone). Also prevents a future component that happens to
    reuse the same name from inheriting the deleted asset's profile.

    Returns the number of entries dropped.
    """
    with _user_ts_lock:
        keys_to_drop = [
            (comp, attr, col) for (comp, attr, col) in _user_ts
            if comp == component_attr and col == name
        ]
        for key in keys_to_drop:
            del _user_ts[key]
    return len(keys_to_drop)


def _user_ts_extent() -> tuple[str | None, str | None]:
    """
    Start / end datetime of the *longest* flat (DatetimeIndex) series in
    _user_ts — the main uploaded profile. Returns (None, None) when nothing
    flat has been uploaded.

    Uses the longest series rather than the union min/max across all series
    on purpose: _user_ts also holds short series backed up from the network's
    own _t tables (see _backup_network_ts_to_user_ts), and a stale backed-up
    range (e.g. a template's 2024 default) would otherwise drag the reported
    start backwards even though the user's actual upload starts elsewhere.
    The longest series is exactly the reference _ensure_snapshots_cover_user_ts
    keys on to realign n.snapshots, so this keeps the Model Horizon default
    consistent with the snapshot index the upload actually produced.

    Per-period (MultiIndex) series are skipped — their range is period-scoped
    and doesn't describe a single operational window.
    """
    with _user_ts_lock:
        flat = [
            s for s in _user_ts.values()
            if not isinstance(s.index, pd.MultiIndex) and len(s.index) > 0
        ]
    if not flat:
        return None, None
    ref = max(flat, key=lambda s: len(s.index))
    try:
        lo, hi = ref.index.min(), ref.index.max()
    except Exception:  # noqa: BLE001 — defensive over arbitrary uploads
        return None, None
    return (
        lo.isoformat() if hasattr(lo, "isoformat") else str(lo),
        hi.isoformat() if hasattr(hi, "isoformat") else str(hi),
    )


def _annual_hourly_reference():
    """
    Validate that an uploaded profile is sample-able into representative
    weeks. Returns ``(idx, None)`` when the longest flat _user_ts series is a
    deduped DatetimeIndex spanning all 12 calendar months of ONE year at
    hourly resolution; otherwise ``(None, reason)`` where ``reason`` explains
    the failed precondition.

    Drives both the ``can_sample_weeks`` flag on GET /network/snapshots and
    the precondition check inside POST /network/snapshots/sample_weeks.
    """
    with _user_ts_lock:
        flat = [
            s for s in _user_ts.values()
            if not isinstance(s.index, pd.MultiIndex) and len(s.index) > 1
        ]
    if not flat:
        return None, (
            "No uploaded time series found. Upload a full-year hourly profile "
            "(generation or load) first."
        )
    ref = max(flat, key=lambda s: len(s.index))
    idx = pd.DatetimeIndex(sorted(set(ref.index)))
    years = sorted(idx.year.unique().tolist())
    months = sorted(int(m) for m in idx.month.unique())
    if len(years) != 1 or months != list(range(1, 13)):
        return None, (
            "Representative-week sampling needs a profile spanning all 12 "
            f"months of a single calendar year — uploaded data covers "
            f"year(s) {years}, month(s) {months}."
        )
    try:
        med = idx.to_series().diff().median()
    except Exception:  # noqa: BLE001 — defensive over arbitrary uploads
        med = None
    if med != pd.Timedelta(hours=1):
        return None, (
            f"Representative-week sampling needs an hourly profile "
            f"({len(idx)} timesteps found; expected ~8760)."
        )
    return idx, None


def _serialize_user_ts() -> dict:
    """
    Return _user_ts as a JSON-serialisable nested dict.

    Format: ``{component: {attribute: {column: {index: [...], values: [...]}}}}``
    The index entries are:
      • DatetimeIndex series → list of ISO strings: ``["2025-01-01T00:00:00", …]``
      • MultiIndex(period, timestep) series → list of [int, ISO] pairs:
        ``[[2025, "2025-01-01T00:00:00"], …]``
    Restore auto-detects the shape from the first entry.

    Nested top-level structure avoids separator-collision bugs with component
    names that contain "|" or "/".
    """
    result: dict = {}
    # Hold _user_ts_lock for the iteration so concurrent uploads can't trip
    # `dictionary changed size during iteration`. Snapshot keys first so a
    # writer waiting on the lock isn't blocked for the JSON-serialisation
    # cost (just the dict-snapshot cost).
    with _user_ts_lock:
        items = list(_user_ts.items())
    for (comp, attr, col), series in items:
        if isinstance(series.index, pd.MultiIndex):
            idx = [
                [int(period), ts.isoformat() if hasattr(ts, "isoformat") else str(ts)]
                for period, ts in series.index
            ]
        else:
            idx = [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in series.index]
        vals = [
            None if isinstance(v, float) and not math.isfinite(v) else v
            for v in series.tolist()
        ]
        result.setdefault(comp, {}).setdefault(attr, {})[col] = {"index": idx, "values": vals}
    return result


def _restore_user_ts(data: dict) -> None:
    """
    Restore _user_ts from the format produced by _serialize_user_ts.
    Supports both the current nested format and the legacy pipe-separated format
    for backwards compatibility with old user_ts.json files.
    All-NaN series are silently skipped — they represent corrupt/empty data.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    new_store: dict[tuple[str, str, str], pd.Series] = {}

    # Detect format: any top-level key containing "|" → legacy pipe format.
    is_legacy = any("|" in k for k in data.keys())

    def _build_series(raw_idx, values, name: str) -> pd.Series:
        """Reconstruct a Series, detecting MultiIndex vs flat by index shape."""
        if raw_idx and isinstance(raw_idx[0], (list, tuple)) and len(raw_idx[0]) == 2:
            periods = [int(p) for p, _ in raw_idx]
            timesteps = pd.to_datetime([ts for _, ts in raw_idx])
            mi = pd.MultiIndex.from_arrays([periods, timesteps], names=["period", "timestep"])
            return pd.Series(values, index=mi, dtype=float, name=name)
        idx = pd.to_datetime(raw_idx)
        return pd.Series(values, index=idx, dtype=float, name=name)

    if is_legacy:
        for key, payload in data.items():
            parts = key.split("|", 2)
            if len(parts) != 3:
                continue
            comp, attr, col = parts
            try:
                series = _build_series(payload["index"], payload["values"], col)
                if series.isna().all():
                    continue  # skip corrupt all-NaN series
                new_store[(comp, attr, col)] = series
            except Exception as exc:
                _log.warning("_restore_user_ts (legacy): skipping '%s': %s", key, exc)
    else:
        for comp, attrs in data.items():
            if not isinstance(attrs, dict):
                continue
            for attr, cols in attrs.items():
                if not isinstance(cols, dict):
                    continue
                for col, payload in cols.items():
                    if not isinstance(payload, dict):
                        continue
                    try:
                        series = _build_series(payload["index"], payload["values"], col)
                        if series.isna().all():
                            continue  # skip corrupt all-NaN series
                        new_store[(comp, attr, col)] = series
                    except Exception as exc:
                        _log.warning(
                            "_restore_user_ts: skipping %s/%s/%s: %s", comp, attr, col, exc
                        )

    # Replace the store atomically under the lock — readers in
    # _serialize_user_ts / _user_ts.items() never see a half-populated state.
    with _user_ts_lock:
        _user_ts.clear()
        _user_ts.update(new_store)


# Components whose `_t` tables we walk for time-series backup. Every non-empty
# attribute on every component is captured — not just the historical 3 slots —
# so that q_set, p_set, p_min_pu, state_of_charge, marginal_cost, etc. all
# round-trip through save/load.
# Components whose _t tables can hold USER-uploaded profiles (loads, capacity
# factors, etc.). Used by _backup_network_ts_to_user_ts to decide what to
# preserve across a set_snapshots(). Deliberately omits Bus and GlobalConstraint:
# their _t frames (buses_t.marginal_price, global_constraints_t.mu, …) are
# solver OUTPUTS, not user inputs, so they must not be ingested into _user_ts.
# NOTE: _flatten_snapshot_state must NOT use this list — it has to walk EVERY
# component (n.all_components) since set_snapshots reindexes all of them.
_TS_COMPONENTS: list[str] = [
    "generators", "loads", "storage_units", "stores",
    "lines", "links", "transformers", "shunt_impedances",
]


def _backup_network_ts_to_user_ts(n=None) -> None:
    """
    Copy time series from the network's _t tables into _user_ts.

    Walks every non-empty (component, attribute) pair. Only copies a column when:
      - it is not already in _user_ts, OR
      - the existing _user_ts entry is all-NaN (corrupt from a previous bad save).
    All-NaN columns from the network are never ingested — they represent
    reindex artifacts, not real user data.
    Call this BEFORE n.set_snapshots() or n.export_to_netcdf() so that profiles
    from imported networks are captured without overwriting good user uploads.

    INPUT-attribute filter: PyPSA's component defaults flag each attribute as
    Input (user-supplied profile) or Output (solver result). We only ingest
    Inputs — capturing Outputs like ``generators_t.p`` / ``lines_t.p0`` /
    ``storage_units_t.state_of_charge`` causes the next call to
    ``_reapply_user_ts_to_network`` (triggered by every autosave) to write
    captured dispatch back to the live network, overwriting fresh solve
    results with stale snapshots and producing orphan columns after a
    topology mutation. Falls back to capturing every attribute if defaults
    aren't readable, preserving legacy behaviour for exotic component classes.
    """
    if n is None:
        n = PyPSAService.get_network()
    for comp in _TS_COMPONENTS:
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        input_attrs: set[str] | None = None
        try:
            comp_defaults = getattr(n.components, comp).defaults
            mask = comp_defaults["status"].astype(str).str.startswith("Input", na=False)
            input_attrs = set(comp_defaults.index[mask])
        except Exception:
            input_attrs = None
        try:
            attrs = list(ts_store.keys()) if hasattr(ts_store, "keys") else []
        except Exception:
            attrs = []
        for attr in attrs:
            if input_attrs is not None and attr not in input_attrs:
                continue
            # Skip marginal_cost: the LP-build path (solver_service ~3736)
            # writes per-snapshot CO2 surcharges into n.<component>_t.marginal_cost
            # when co2_price_per_period is configured. Without this skip, the
            # next autosave ingests those solver-written columns into _user_ts,
            # they appear as "uploaded profiles" in the Time Series Manager,
            # and DELETE /api/network/timeseries is futile — the next solve
            # rewrites the columns and the autosave-after re-ingests them.
            # Legitimate user uploads bypass this path (the upload endpoint
            # writes _user_ts directly) and persist via user_ts.json on save.
            if attr == "marginal_cost":
                continue
            df = ts_store.get(attr) if hasattr(ts_store, "get") else getattr(ts_store, attr, None)
            if df is None or not hasattr(df, "empty") or df.empty:
                continue
            for col in df.columns:
                series = df[col]
                if series.isna().all():
                    continue
                key = (comp, attr, col)
                # Atomic read-and-write under _user_ts_lock — this function
                # is reachable from save_project's autosave path (projects.py
                # ~line 677) BEFORE that path takes the PyPSA lock, so a
                # foreground upload route in another thread can race the
                # iteration here without the lock. Holding _user_ts_lock
                # for the per-key get-or-insert keeps `_serialize_user_ts`'s
                # snapshot-then-iterate path safe.
                with _user_ts_lock:
                    existing = _user_ts.get(key)
                    if existing is None or existing.isna().all():
                        _user_ts[key] = series.copy()


def _rebase_flat_user_ts(new_idx: pd.DatetimeIndex) -> int:
    """
    Positionally re-base every flat _user_ts series of the SAME LENGTH as
    `new_idx` onto `new_idx`. Returns the count re-based.

    Prevents silent data loss when _user_ts holds profiles from different
    calendar years. Scenario: a project is loaded with 2024 profiles (so
    _user_ts carries a 2024 series for every profiled column), then the user
    uploads a 2026 profile for ONE column. `_ensure_snapshots_cover_user_ts`
    then realigns `n.snapshots` to 2026 — and without this, every still-2024
    column reindexes to all-NaN inside `_reapply_user_ts_to_network` and is
    silently skipped, leaving those loads at 0 demand and those renewables at
    a flat `p_max_pu = 1.0`. The result is a badly corrupted solve (massive
    curtailment + lost load) with no error surfaced.

    Re-basing by POSITION keeps every same-resolution profile on one common
    operational range — a series' value at hour i maps to hour i of the new
    index. Series whose length differs from `new_idx` (a genuine 24 h
    representative day, leap- vs non-leap-year data) are left untouched: a
    positional re-base there would shift the calendar and corrupt the data.
    """
    n_target = len(new_idx)
    rebased = 0
    with _user_ts_lock:
        for key, series in list(_user_ts.items()):
            if isinstance(series.index, pd.MultiIndex):
                continue
            if len(series.index) != n_target or series.index.equals(new_idx):
                continue
            _user_ts[key] = pd.Series(
                series.values, index=new_idx, name=series.name,
            )
            rebased += 1
    return rebased


def _ensure_snapshots_cover_user_ts(n=None) -> bool:
    """
    Align n.snapshots with the _user_ts profiles so an upload actually
    reaches the optimiser. Returns True when snapshots were updated.

    Two triggers — the longest stored series is adopted as the operational
    range when EITHER:

      • it is **longer** than the network currently models (growth — the
        original behaviour), OR
      • it has **zero date overlap** with the current snapshots — i.e. the
        upload is for a completely different time window (e.g. a May profile
        on a January network). This is the important case:
        ``_reapply_user_ts_to_network`` skips every column whose aligned
        series would be all-NaN, so without realigning here the upload is
        stored in ``_user_ts`` (and shown in the GUI) but NEVER reaches
        ``n.loads_t.p_set`` etc. — the model then optimises with no profile.

    Two paths depending on the current snapshot shape:

      • Flat ``DatetimeIndex`` → ``set_snapshots`` swaps to the uploaded range.
      • ``MultiIndex(period, timestep)`` → rebuild the MultiIndex with the new
        per-period range replicated under every existing period (canonical
        "Same year per period"). Always MultiIndex → MultiIndex here, never
        to flat, so the pandas ``cannot include dtype 'M' in a buffer``
        reindex bug doesn't fire.

      • Per-period uploads (series whose index is itself MultiIndex) are
        filtered out before scanning — ``sorted(set(MultiIndex))`` yields
        tuples and would explode ``pd.DatetimeIndex(...)``. They're already
        period-scoped and don't drive the range decision.

    Used after bundle load / per-component profile upload. Caller is
    responsible for calling _reapply_user_ts_to_network afterwards to
    populate the freshly sized _t tables.
    """
    if n is None:
        n = PyPSAService.get_network()
    if not _user_ts:
        return False
    flat_series = [s for s in _user_ts.values() if not isinstance(s.index, pd.MultiIndex)]
    if not flat_series:
        return False
    longest = max(flat_series, key=lambda s: len(s.index))
    # Dedup before comparing/applying so a series with duplicate timestamps
    # can't accidentally shrink n.snapshots below its current length.
    new_idx = pd.DatetimeIndex(sorted(set(longest.index)))

    if isinstance(n.snapshots, pd.MultiIndex):
        periods = sorted(n.snapshots.get_level_values(0).unique().tolist())
        if not periods:
            return False
        per_period_now = len(n.snapshots) // len(periods)
        # Per-period timestep range (deduped across periods) — used for the
        # zero-overlap check below.
        per_period_idx = pd.DatetimeIndex(
            sorted(set(n.snapshots.get_level_values(1)))
        )
        no_overlap = len(per_period_idx.intersection(new_idx)) == 0
        if len(new_idx) <= per_period_now and not no_overlap:
            return False
        # Grow / realign the per-period operational range to the uploaded
        # series' extent, keeping the same periods. _t tables are MultiIndex →
        # MultiIndex reindex, which PyPSA handles cleanly.
        _backup_network_ts_to_user_ts(n)
        # Re-base same-length _user_ts series onto the new timestep range so a
        # mixed-year _user_ts (old project profiles + a fresh upload) doesn't
        # leave the non-uploaded columns stranded in the old year (→ all-NaN
        # → silently dropped by _reapply). See _rebase_flat_user_ts.
        _rebase_flat_user_ts(new_idx)
        mi = _build_period_multiindex(periods, [new_idx] * len(periods))
        n.set_snapshots(mi)
        return True

    # Flat → flat.
    cur = n.snapshots
    grew = len(new_idx) > len(cur)
    # Zero overlap → the upload is for a different operational window than the
    # network's current snapshots; adopt the uploaded range so the data the
    # user gave actually drives the optimisation (see docstring).
    realign = len(cur.intersection(new_idx)) == 0
    if not grew and not realign:
        return False
    # Preserve network-side _t tables (e.g. from a freshly imported netcdf)
    # into _user_ts before set_snapshots reindexes them — otherwise rows that
    # fall outside the new index would be silently dropped. Backup is a no-op
    # for columns already present in _user_ts.
    _backup_network_ts_to_user_ts(n)
    # Re-base same-length _user_ts series onto new_idx so a mixed-year _user_ts
    # (old project profiles + a fresh upload in a different year) doesn't leave
    # the non-uploaded columns stranded in the old year — which would reindex
    # to all-NaN and be silently dropped by _reapply. See _rebase_flat_user_ts.
    _rebase_flat_user_ts(new_idx)
    n.set_snapshots(new_idx)
    return True


def _reapply_user_ts_to_network(n=None) -> None:
    """
    Re-apply _user_ts profiles to the network's _t tables, aligned to the
    current snapshot index.  Call this after n.set_snapshots() or after a
    project load so that the network uses the correct time series for simulation.

    If a stored series has zero overlap with the current snapshots (e.g. the
    user uploaded 2024 hourly data but the network still uses 2013 daily
    snapshots), the column is skipped rather than overwriting the network table
    with all-NaN.  Update the network's snapshots first, then call this again.

    Implementation note: aligned series are grouped by (component, attribute)
    and written back in a single concat per group. The previous per-column
    `existing.copy(); merged[col] = aligned; ts_store[attr] = merged` loop was
    O(R·N²) per group (each iteration re-copied the full DataFrame), which
    pushed bundle imports past axios' 30 s timeout for year-of-hourly-data
    projects. The grouped path is O(R·N).
    """
    import logging as _logging
    from collections import defaultdict

    import numpy as _np
    _log = _logging.getLogger(__name__)
    if n is None:
        n = PyPSAService.get_network()

    # Three cases for aligning a stored _user_ts series with n.snapshots:
    #   1) series.index is MultiIndex (per-period upload) AND n.snapshots is
    #      MultiIndex → direct reindex (PyPSA matches tuples).
    #   2) series.index is MultiIndex AND n.snapshots is flat → skip; the
    #      stored data is scoped to specific periods that no longer exist.
    #   3) series.index is DatetimeIndex AND n.snapshots is MultiIndex →
    #      broadcast via level-1 lookup (canonical "same operational year").
    #      reindex() rejects duplicate-target labels (level-1 has duplicates
    #      across periods), so we lookup positionally via get_indexer.
    #   4) series.index is DatetimeIndex AND n.snapshots is flat → plain
    #      reindex (the original code path).
    is_multi = isinstance(n.snapshots, pd.MultiIndex)
    target_lookup = n.snapshots.get_level_values(1) if is_multi else None

    # Defensive pre-pass: align any stale `_t` DataFrame whose index doesn't
    # match the current `n.snapshots`. Without this, a frame written under
    # a previous snapshot regime (e.g. flat DatetimeIndex from a single-period
    # solve) silently leaks into the next solve via PyPSA's `_t` access —
    # the LP either reindexes to all-NaN (broken) or the table grows in
    # subsequent concats (rows from BOTH old and new indexes coexist,
    # 35040-row mixed-index frames observed in live state).
    #
    # Strategy mirrors the per-column reapply logic below:
    #   • flat → multi: broadcast each column by level-1 timestep match
    #   • multi → flat: drop entirely (period scope no longer exists; if
    #     the data is preserved in `_user_ts`, the main loop re-injects)
    #   • multi → multi or flat → flat with different bounds: reindex
    #     (fills missing snapshots with NaN; PyPSA's default-fallback
    #     handles those at solve time)
    for comp in ("generators", "loads", "storage_units", "stores", "links",
                 "lines", "transformers"):
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        for attr in list(ts_store.keys()):
            ts_df = ts_store[attr]
            if ts_df is None or ts_df.empty:
                continue
            if ts_df.index.equals(n.snapshots):
                continue  # already aligned, no-op
            df_is_multi = isinstance(ts_df.index, pd.MultiIndex)
            if df_is_multi and is_multi:
                ts_store[attr] = ts_df.reindex(n.snapshots)
            elif df_is_multi and not is_multi:
                ts_store[attr] = pd.DataFrame(index=n.snapshots)
            elif is_multi:
                # Flat existing → broadcast to MultiIndex by level-1 lookup.
                positions = ts_df.index.get_indexer(target_lookup)
                rebroadcast = pd.DataFrame(index=n.snapshots)
                mask = positions >= 0
                for c in ts_df.columns:
                    out = _np.full(len(target_lookup), _np.nan, dtype=float)
                    out[mask] = ts_df[c].values[positions[mask]]
                    rebroadcast[c] = out
                ts_store[attr] = rebroadcast
            else:
                ts_store[attr] = ts_df.reindex(n.snapshots)

    # Input-attribute filter, mirroring the gate in
    # _backup_network_ts_to_user_ts. Older user_ts.json files (saved before
    # the backup-side filter landed) may carry OUTPUT keys like
    # ("generators", "p", "Solar2"); writing those back to the network would
    # clobber freshly-solved dispatch with the captured snapshot. Cache the
    # per-component Input set so the .defaults lookup is paid once per call.
    input_attrs_by_comp: dict[str, set[str] | None] = {}

    def _is_input_attr(comp_name: str, attr_name: str) -> bool:
        if comp_name not in input_attrs_by_comp:
            try:
                comp_defaults = getattr(n.components, comp_name).defaults
                mask = comp_defaults["status"].astype(str).str.startswith("Input", na=False)
                input_attrs_by_comp[comp_name] = set(comp_defaults.index[mask])
            except Exception:
                input_attrs_by_comp[comp_name] = None
        cached = input_attrs_by_comp[comp_name]
        return cached is None or attr_name in cached

    grouped: dict[tuple[str, str], dict[str, pd.Series]] = defaultdict(dict)
    for (comp, attr, col), series in _user_ts.items():
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        if not _is_input_attr(comp, attr):
            _log.debug("_reapply: skipping %s/%s/%s — output attribute", comp, attr, col)
            continue
        component_df = getattr(n, comp, None)
        if component_df is None or col not in component_df.index:
            _log.debug("_reapply: skipping %s/%s/%s — component not in network", comp, attr, col)
            continue
        series_is_multi = isinstance(series.index, pd.MultiIndex)
        if series_is_multi and is_multi:
            # Case 1 — both MultiIndex: direct tuple reindex.
            aligned = series.reindex(n.snapshots)
        elif series_is_multi and not is_multi:
            # Case 2 — stored per-period data on flat snapshots: skip rather
            # than guess which period to project. User must rebuild snapshots
            # to MultiIndex (or re-upload as DatetimeIndex) to use this data.
            _log.debug(
                "_reapply: skipping per-period %s/%s/%s — network is flat",
                comp, attr, col,
            )
            continue
        elif is_multi:
            # Case 3 — DatetimeIndex series + MultiIndex snapshots: broadcast.
            positions = series.index.get_indexer(target_lookup)
            out = _np.full(len(target_lookup), _np.nan, dtype=float)
            mask = positions >= 0
            out[mask] = series.values[positions[mask]]
            aligned = pd.Series(out, index=n.snapshots)
        else:
            # Case 4 — plain reindex.
            aligned = series.reindex(n.snapshots)
        if aligned.isna().all() and not series.isna().all():
            _log.warning(
                "_reapply: %s/%s/%s has no overlap with current snapshots "
                "(%s … %s) — skipping to avoid writing all-NaN. "
                "Set network snapshots to match the uploaded profile first.",
                comp, attr, col,
                n.snapshots[0] if len(n.snapshots) else "?",
                n.snapshots[-1] if len(n.snapshots) else "?",
            )
            continue
        grouped[(comp, attr)][col] = aligned

    for (comp, attr), col_dict in grouped.items():
        ts_store = getattr(n, f"{comp}_t", None)
        if ts_store is None:
            continue
        new_block = pd.DataFrame(col_dict, index=n.snapshots)
        existing = getattr(ts_store, attr, None)
        if existing is None or (hasattr(existing, "empty") and existing.empty):
            ts_store[attr] = new_block
        else:
            # Drop overlapping columns from `existing` first, then concat once.
            # Single O(R·(N+M)) pass instead of N copies of an N-column frame.
            overlap = [c for c in col_dict.keys() if c in existing.columns]
            base = existing.drop(columns=overlap) if overlap else existing
            # Re-align `base` to `n.snapshots` BEFORE the concat. Without this
            # guard, a flat-DatetimeIndex `base` from a previous single-period
            # solve unions with the MultiIndex `new_block` — producing a
            # mixed-index DataFrame whose flat rows don't match
            # `n.snapshots`, so PyPSA's LP silently falls back to the scalar
            # default for every column the base owns (e.g. `Solar2` p_max_pu).
            # Symptom: renewable dispatch flat at p_nom while vintages still
            # honour the profile, because vintage columns were written with
            # the correct MultiIndex.
            #
            # Case-by-case re-alignment mirrors the `_user_ts` path above:
            #   • flat base + multi snapshots → broadcast by level-1 timestep
            #   • multi base + flat snapshots → drop (period scope no longer
            #     exists)
            #   • same-shape index → no-op (reindex is identity)
            if not base.empty and not base.index.equals(n.snapshots):
                base_is_multi = isinstance(base.index, pd.MultiIndex)
                if base_is_multi and is_multi:
                    base = base.reindex(n.snapshots)
                elif base_is_multi and not is_multi:
                    # Multi-period base, flat target — period scope is gone,
                    # drop the entire base; user-uploaded data lives in
                    # `_user_ts` and will be re-broadcast by the loop above.
                    base = pd.DataFrame(index=n.snapshots)
                elif is_multi:
                    # Flat base, multi target — broadcast by level-1 timestep
                    # so each period gets the same operational profile.
                    base_target_lookup = n.snapshots.get_level_values(1)
                    positions = base.index.get_indexer(base_target_lookup)
                    rebroadcast = pd.DataFrame(index=n.snapshots)
                    for c in base.columns:
                        out = _np.full(len(base_target_lookup), _np.nan, dtype=float)
                        mask = positions >= 0
                        out[mask] = base[c].values[positions[mask]]
                        rebroadcast[c] = out
                    base = rebroadcast
                else:
                    base = base.reindex(n.snapshots)
            ts_store[attr] = pd.concat([base, new_block], axis=1)


def _capture_snapshot_weights_per_timestep(n) -> pd.DataFrame | None:
    """
    Snapshot ``n.snapshot_weightings`` keyed by timestep so it survives a
    subsequent ``n.set_snapshots(mi)`` reset.

    PyPSA's ``set_snapshots`` calls ``_snapshots_data.reindex(new_idx,
    fill_value=default_snapshot_weightings)`` — for a MultiIndex transition,
    the old DatetimeIndex tuples have no match in the new MultiIndex, so every
    cell falls back to 1.0. Any custom weights the user set on the flat
    snapshots (representative-week scaling factor 52.14, half-hour resolution
    0.5, etc.) are silently lost, and the LP's ``n.nyears`` collapses to
    ``n_timesteps / 8760`` — heavily undervaluing CAPEX and producing
    renewable over-build.

    Returned frame is indexed by *timestep only* (the timezone-naive datetime),
    not by snapshot — the reapply helper aligns it against the NEW
    ``n.snapshots``. Returns ``None`` when weights are at PyPSA defaults
    (all 1.0) since there's nothing meaningful to preserve.
    """
    sw = n.snapshot_weightings.copy()
    if sw.empty:
        return None
    if isinstance(sw.index, pd.MultiIndex):
        first_p = sw.index.get_level_values(0)[0]
        sw_per_ts = sw.loc[first_p].copy()
        # `.loc[first_p]` collapses the level — sw_per_ts has a flat
        # DatetimeIndex named "timestep". Normalise to "snapshot" so the
        # reapply path's `.reindex(timestep_idx)` works regardless of origin.
        sw_per_ts.index.name = "snapshot"
    else:
        sw_per_ts = sw.copy()
    # No point preserving the all-1.0 default — saves a needless write.
    if (sw_per_ts == 1.0).all().all():
        return None
    return sw_per_ts


def _reapply_snapshot_weights(n, captured) -> None:
    """
    Write the captured per-timestep weights back onto ``n.snapshot_weightings``
    after ``set_snapshots`` has rebuilt the index. Aligns by timestep value:

      • Flat target  → reindex by ``n.snapshots`` directly.
      • Multi target → for each period, reindex the timestep slice; replicate
        the same per-timestep weights under every period (the canonical
        multi-period workflow uses the same operational year per period, so
        broadcasting weights matches user intent).

    Missing timesteps (e.g. user changed operational range) fall back to 1.0.
    Must run AFTER ``n.set_snapshots(...)`` so ``n.snapshots`` reflects the
    new index. Holds no lock — caller responsibility.
    """
    if captured is None:
        return
    idx = n.snapshots
    if isinstance(idx, pd.MultiIndex):
        # Build the new frame period-by-period.
        chunks = []
        for p in idx.get_level_values(0).unique():
            mask = idx.get_level_values(0) == p
            ts_slice = idx[mask].get_level_values(1)
            aligned = captured.reindex(ts_slice).fillna(1.0)
            aligned.index = idx[mask]
            chunks.append(aligned)
        new_sw = pd.concat(chunks)
    else:
        new_sw = captured.reindex(idx).fillna(1.0)
        new_sw.index = idx
    # Setter validates df.index.equals(n.snapshots); we built new_sw against
    # n.snapshots so it should pass. If the columns mismatch (PyPSA added new
    # weight columns in a future version), assign per-column to be safe.
    for col in new_sw.columns:
        if col in n.snapshot_weightings.columns:
            try:
                n.snapshot_weightings[col] = new_sw[col].values
            except Exception:
                # Defensive — column-level assignment failure shouldn't break
                # the whole solve. Default 1.0 is acceptable as fallback.
                pass


def _flatten_snapshot_state(n=None) -> None:
    """
    Collapse any MultiIndex(period, timestep) snapshot structure on the
    network down to a flat DatetimeIndex, in place — so a subsequent
    ``n.set_snapshots(flat)`` is a safe flat→flat operation.

    A direct ``set_snapshots(flat)`` on a MultiIndexed network trips pandas'
    "cannot include dtype 'M' in a buffer" bug (``MultiIndex._wrap_reindex_result``
    → ``tuples_to_object_array``) for every non-empty MultiIndexed frame PyPSA
    reindexes, and a length mismatch for the empty ones. To pre-empt both we
    walk EXACTLY the frames ``set_snapshots`` touches — every component's
    ``dynamic`` dict plus ``_snapshots_data`` — and demote each MultiIndexed one
    to its first period's timesteps. (The earlier version walked a
    hand-maintained component list that omitted Bus / GlobalConstraint, so their
    solved ``_t`` frames survived as MultiIndex and still tripped the bug.)

    No-op when everything is already flat. Rows from non-first periods are
    discarded — the caller should ``_backup_network_ts_to_user_ts(n)`` before
    and ``_reapply_user_ts_to_network(n)`` after so uploaded profiles survive.
    Must be called under ``PyPSAService.get_lock()``.
    """
    if n is None:
        n = PyPSAService.get_network()
    snaps_multi = isinstance(n.snapshots, pd.MultiIndex)

    if snaps_multi:
        first_p = n.snapshots.get_level_values(0).unique()[0]
        base_idx = pd.DatetimeIndex(
            n.snapshots[n.snapshots.get_level_values(0) == first_p].get_level_values(1)
        )
    else:
        base_idx = pd.DatetimeIndex(n.snapshots)
    base_idx.name = "snapshot"

    # Collapse one MultiIndexed frame to flat by keeping its FIRST period's rows
    # and relabelling the index with that period's own timestep values. Safe for
    # empty frames too — a boolean-mask slice works on a 0-column frame — and the
    # result is always length-consistent, so set_snapshots() is then flat→flat.
    def _demote(df):
        lvl0 = df.index.get_level_values(0)
        mask = lvl0 == lvl0.unique()[0]
        out = df[mask].copy()
        out.index = pd.DatetimeIndex(df.index[mask].get_level_values(1))
        out.index.name = "snapshot"
        return out

    # Walk every component's dynamic dict — the exact set ``set_snapshots``
    # iterates (``n.c[component].dynamic`` for ``component in n.all_components``)
    # — so nothing PyPSA will later reindex is left MultiIndexed.
    for component in n.all_components:
        try:
            dynamic = n.c[component].dynamic
        except (KeyError, AttributeError, TypeError):
            continue
        for k in list(dynamic.keys()):
            df = dynamic[k]
            if df is None or not isinstance(df.index, pd.MultiIndex):
                continue
            dynamic[k] = _demote(df)

    if isinstance(n.snapshot_weightings.index, pd.MultiIndex):
        # Direct write to the private backing — PyPSA's public setter validates
        # df.index == self.snapshots, which is still MultiIndex at this point.
        n._snapshots_data = _demote(n.snapshot_weightings)

    if snaps_multi:
        n.set_snapshots(base_idx)


def _parse_upload(content: bytes, filename: str) -> pd.DataFrame:
    """
    Parse an uploaded Excel or CSV file, returning a DataFrame with a
    timezone-naive DatetimeIndex.  Raises HTTPException on failure.
    """
    fname = (filename or "").lower()
    try:
        if fname.endswith(".xlsx") or fname.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content), index_col=0)
        else:
            df = pd.read_csv(io.BytesIO(content), index_col=0, parse_dates=False)
    except Exception as exc:
        raise HTTPException(400, f"Could not parse file: {exc}") from exc

    # Normalise index to timezone-naive datetime using a consistent parser.
    # Using format="mixed" (pandas ≥ 2) gracefully handles both
    # "2024-01-01 00:00" and "2024-01-01T00:00:00" strings.
    try:
        idx = pd.to_datetime(df.index, utc=False, format="mixed", dayfirst=False)
    except TypeError:
        # pandas < 2 doesn't have format="mixed"; fall back to inference
        idx = pd.to_datetime(df.index, utc=False, infer_datetime_format=True)
    df.index = idx.tz_localize(None) if idx.tz is not None else idx
    df = df[df.index.notna()]          # drop rows that didn't parse
    df.index.name = "timestamp"
    return df

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

# Carrier-keyword sets used to classify loads into energy-vector sections.
_ELEC_CARRIERS = {
    "ac", "dc", "electricity", "elec", "low voltage", "medium voltage",
    "high voltage", "lv", "mv", "hv", "ehv",
}
_H2_CARRIERS_LOAD = {"h2", "hydrogen", "h2 pipeline"}
_HEAT_CARRIERS = {
    "heat", "low temperature heat", "urban heat", "rural heat",
    "space heat", "water tank", "central heat", "district heat",
    "low-t heat", "low t heat",
}


def _load_section(n, load_name: str) -> str:
    """
    Classify a load as electricity / hydrogen / heat / other.

    Looks up the load's bus, then the bus's carrier. Empty/AC default carriers
    fall through to 'electricity' (PyPSA's default bus carrier is 'AC').
    """
    try:
        bus = str(n.loads.at[load_name, "bus"]) if "bus" in n.loads.columns else ""
        carrier = str(n.buses.at[bus, "carrier"]).lower().strip() if bus in n.buses.index else ""
    except Exception:
        carrier = ""
    if not carrier:
        return "electricity"
    if carrier in _H2_CARRIERS_LOAD or "hydrogen" in carrier:
        return "hydrogen"
    if carrier in _HEAT_CARRIERS or "heat" in carrier or "water tank" in carrier:
        return "heat"
    if carrier in _ELEC_CARRIERS:
        return "electricity"
    return "other"


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


def _h2_load_profile(
    snapshots: pd.DatetimeIndex,
    p_max: float,
    noise_seed: int,
    weekend_factor: float = 0.60,
    noise_pct: float = 0.02,
) -> np.ndarray:
    """
    Industrial-style hydrogen demand: roughly flat during weekdays
    (production runs), reduced on weekends. Normalised so a typical weekday
    hour equals p_max.
    """
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    dow = snapshots.dayofweek.values
    # Slight mid-shift dip in the early morning to avoid a perfectly flat line.
    base = 0.95 + 0.05 * np.cos((hours - 14.0) * np.pi / 12.0) * 0.3
    wf = np.where(dow >= 5, weekend_factor, 1.0)
    noise = rng.uniform(-noise_pct, noise_pct, size=len(snapshots))
    values = p_max * base * wf * (1.0 + noise)
    return np.round(np.maximum(values, 0.0), 3)


def _heat_load_profile(
    snapshots: pd.DatetimeIndex,
    p_max: float,
    noise_seed: int,
    weekend_factor: float = 0.95,
    noise_pct: float = 0.04,
) -> np.ndarray:
    """
    Heat demand: pronounced morning + evening peaks, low overnight, weekends
    only marginally lower. Normalised so the evening peak equals p_max.
    Seasonal scaling (winter > summer) is left to the caller — for a 1-week
    template the daily shape is what matters.
    """
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    dow = snapshots.dayofweek.values

    morning = 0.85 * np.exp(-0.5 * ((hours - 7.0) / 1.8) ** 2)
    evening = 1.00 * np.exp(-0.5 * ((hours - 20.0) / 2.0) ** 2)
    daytime = 0.35 * np.exp(-0.5 * ((hours - 13.0) / 4.0) ** 2)
    raw = morning + evening + daytime + 0.10  # baseline overnight floor

    norm = (1.00 * np.exp(-0.5 * ((20.0 - 20.0) / 2.0) ** 2)
            + 0.85 * np.exp(-0.5 * ((20.0 - 7.0) / 1.8) ** 2)
            + 0.35 * np.exp(-0.5 * ((20.0 - 13.0) / 4.0) ** 2)
            + 0.10)
    shape = raw / norm

    wf = np.where(dow >= 5, weekend_factor, 1.0)
    noise = rng.uniform(-noise_pct, noise_pct, size=len(snapshots))
    values = p_max * shape * wf * (1.0 + noise)
    return np.round(np.maximum(values, 0.0), 3)


_LOAD_SHAPES = {
    "electricity": None,  # use _double_peak_profile (defined below)
    "hydrogen": _h2_load_profile,
    "heat": _heat_load_profile,
    "other": None,
}


def _double_peak_profile(
    snapshots: pd.DatetimeIndex,
    p_max: float,
    noise_seed: int,
    weekend_factor: float = 0.80,
    noise_pct: float = 0.03,
) -> np.ndarray:
    """
    Realistic hourly load profile for one week.

    Shape: two Gaussian peaks (morning ~09:00, evening ~19:00) with a soft
    overnight trough, normalised so the evening peak equals p_max.
    Weekend days (Sat/Sun) are scaled by weekend_factor.
    Independent ±noise_pct random variation per load (seeded for reproducibility).
    """
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    dow   = snapshots.dayofweek.values          # 0=Mon … 6=Sun

    morning = 0.75 * np.exp(-0.5 * ((hours - 9.0)  / 2.5) ** 2)
    evening = 1.00 * np.exp(-0.5 * ((hours - 19.0) / 2.5) ** 2)
    trough  = 0.20 * np.exp(-0.5 * ((hours - 3.0)  / 2.0) ** 2)
    raw     = morning + evening + trough

    # Normalise so the theoretical evening peak = 1
    norm = (1.00 * np.exp(-0.5 * ((19.0 - 19.0) / 2.5) ** 2)
          + 0.75 * np.exp(-0.5 * ((19.0 -  9.0) / 2.5) ** 2)
          + 0.20 * np.exp(-0.5 * ((19.0 -  3.0) / 2.0) ** 2))
    shape = raw / norm

    wf    = np.where(dow >= 5, weekend_factor, 1.0)
    noise = rng.uniform(-noise_pct, noise_pct, size=len(snapshots))
    values = p_max * shape * wf * (1.0 + noise)
    return np.round(np.maximum(values, 0.0), 3)


def _shape_for_section(section: str):
    """
    Return the daily-shape function for a given section (defaulting to the
    electrical double-peak shape when no carrier-specific shape is registered).
    """
    if section == "hydrogen":
        return _h2_load_profile
    if section == "heat":
        return _heat_load_profile
    return _double_peak_profile


# ── Template horizon helper ──────────────────────────────────────────────────
# All three template endpoints (loads / generators / links) used to hard-code a
# 168-hour Monday-this-week sample. The user usually wants the template aligned
# with the simulation horizon they're already configured with — otherwise upload
# either truncates or has to be re-shaped.
#
# Resolution rules (first match wins):
#   1. start + end provided   → custom pd.date_range(start, end, freq=freq)
#   2. use_snapshots == True  → n.snapshots (the simulation horizon)
#   3. fallback                → 168 h starting Monday-this-week, hourly
#
# Returns a (snapshots, source_label) tuple. The label is used to make the
# error messages and filenames descriptive ("simulation" / "custom" / "sample").
def _template_snapshots(
    n,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
) -> tuple[pd.DatetimeIndex, str]:
    if start and end:
        try:
            sns = pd.date_range(start, end, freq=freq)
        except Exception as exc:
            raise HTTPException(400, f"Invalid template range: {exc}")
        if len(sns) == 0:
            raise HTTPException(400, "Template range produced 0 timestamps; check start/end/freq.")
        sns.name = "timestamp"
        return sns, "custom"
    if use_snapshots and len(n.snapshots) > 0:
        # MultiIndex (period, timestep) — `pd.DatetimeIndex(multi)` raises
        # `Cannot create a DatetimeArray from a MultiIndex`. The template
        # represents one operational year that gets replicated per period, so
        # extract period-0's timestep range and use that as the template
        # horizon.
        if isinstance(n.snapshots, pd.MultiIndex):
            first_p = n.snapshots.get_level_values(0).unique()[0]
            mask = n.snapshots.get_level_values(0) == first_p
            sns = pd.DatetimeIndex(n.snapshots[mask].get_level_values(1))
        else:
            sns = pd.DatetimeIndex(n.snapshots)
        sns.name = "timestamp"
        return sns, "simulation"
    try:
        import datetime as _dt
        today = _dt.date.today()
        week_start = today - _dt.timedelta(days=today.weekday())
        sns = pd.date_range(str(week_start), periods=168, freq="h")
    except Exception:
        sns = pd.date_range("2024-01-01", periods=168, freq="h")
    sns.name = "timestamp"
    return sns, "sample"


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

_RENEWABLE_KW    = {'wind', 'solar', 'ror', 'hydro', 'geothermal', 'wave', 'tidal', 'pv', 'biomass', 'biogas', 'run-of-river'}
# Dispatchable conventional units PLUS sinks / dumps / spills that behave
# like dispatchable units from a UX standpoint: the user wants to tune
# marginal cost / availability / must-run from the Conventional tab.
# 'dump', 'spill', 'sink', 'slack' cover heat-dump generators (carrier
# 'heat-dump' uses 'dump'), wind-spill model patterns, and VOLL slacks.
_CONVENTIONAL_KW = {'coal', 'lignite', 'gas', 'nuclear', 'oil', 'ccgt', 'ocgt', 'chp', 'thermal', 'diesel', 'peat', 'steam',
                    'dump', 'spill', 'sink', 'slack'}
_DR_KW           = {'dr', 'dsm', 'flex', 'dsr', 'interruptible', 'demand_response', 'curtail'}


def _gen_category(carrier: str) -> str:
    """Return 'renewable' | 'conventional' | 'dr' | 'other' based on carrier keyword match."""
    c = (carrier or '').lower()
    if any(k in c for k in _DR_KW):           return 'dr'
    if any(k in c for k in _RENEWABLE_KW):    return 'renewable'
    if any(k in c for k in _CONVENTIONAL_KW): return 'conventional'
    return 'other'


def _profile_meta_for(
    name: str,
    user_series: pd.Series | None,
    network_df: pd.DataFrame | None,
    user_only: bool = False,
) -> dict:
    """
    Build a {has_profile, rows, mean, peak, sum, start, end} block for one generator.

    Prefers the user-uploaded series; falls back to a column on a network _t
    DataFrame if present, UNLESS ``user_only=True``. Returns
    ``{has_profile: False}`` when neither has data (or only the network
    has data and ``user_only`` is set).

    The ``user_only`` flag is for attributes the user expects to control
    exclusively from the Time Series Manager — `marginal_cost` is the
    canonical case: the solver writes per-snapshot CO2 surcharges into
    ``generators_t.marginal_cost`` when ``co2_price_per_period`` is set,
    and a project saved mid-solve can persist those columns into netcdf.
    Surfacing them as "uploaded profiles" misleads the user, so this flag
    restricts has_profile detection to the explicit user upload store.

    `sum` is Σ of the profile values — for a p_max_pu profile that's the
    full-load-equivalent hours; for a load p_set profile it's energy in MWh.
    The GUI labels it per attribute.

    Handles multi-period networks: when `col.index` is a `(period, timestep)`
    MultiIndex, the first/last positions are tuples — calling `.isoformat()`
    on those throws `AttributeError: 'tuple' object has no attribute
    'isoformat'`. Use the timestep level (level 1) for the date stamps so
    the frontend gets a usable ISO string in both shapes.
    """
    s = user_series
    if (
        s is None
        and not user_only
        and network_df is not None
        and not network_df.empty
        and name in network_df.columns
    ):
        s = network_df[name]
    if s is None:
        return {'has_profile': False}
    col = s.dropna()
    if not len(col):
        return {'has_profile': False}
    if isinstance(col.index, pd.MultiIndex):
        # Multi-period: pull the timestep-level (level 1) values.
        ts_level = col.index.get_level_values(-1)
        start_ts = ts_level[0]
        end_ts = ts_level[-1]
    else:
        start_ts = col.index[0]
        end_ts = col.index[-1]
    def _iso(t) -> str:
        if hasattr(t, "isoformat"):
            return t.isoformat()
        return str(t)
    return {
        'has_profile': True,
        'rows': int(len(col)),
        'start': _iso(start_ts),
        'end':   _iso(end_ts),
        'mean':  float(col.mean()),
        'peak':  float(col.max()),
        'sum':   float(col.sum()),
    }


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


def _solar_cf_profile(snapshots: pd.DatetimeIndex, noise_seed: int) -> np.ndarray:
    """Solar capacity factor (0–1): bell curve peaking at 13:00, zero at night."""
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    raw = np.exp(-0.5 * ((hours - 13.0) / 3.5) ** 2)
    raw = np.where((hours < 5) | (hours > 21), 0.0, raw)
    noise = rng.uniform(-0.04, 0.04, size=len(snapshots))
    return np.round(np.clip(raw * (1.0 + noise), 0.0, 1.0), 3)


def _wind_cf_profile(snapshots: pd.DatetimeIndex, noise_seed: int) -> np.ndarray:
    """Wind capacity factor (0–1): smooth variation around 0.35 mean."""
    rng = np.random.default_rng(noise_seed)
    n = len(snapshots)
    t = np.arange(n)
    base = 0.35 + 0.12 * np.sin(t * 2 * np.pi / 24) + 0.08 * np.sin(t * 2 * np.pi / 72)
    noise = rng.normal(0, 0.06, size=n)
    smooth = np.convolve(noise, np.ones(6) / 6, mode='same')
    return np.round(np.clip(base + smooth, 0.0, 1.0), 3)


def _flat_cf_profile(snapshots: pd.DatetimeIndex, noise_seed: int, base: float = 0.85) -> np.ndarray:
    """Flat capacity factor with minor noise (conventional / DR generators)."""
    rng = np.random.default_rng(noise_seed)
    noise = rng.uniform(-0.03, 0.03, size=len(snapshots))
    return np.round(np.clip(base * (1.0 + noise), 0.0, 1.0), 3)


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

_H2_CARRIERS = {'h2', 'hydrogen', 'h2 pipeline', 'h2pipeline', 'h2_pipeline'}


def _link_category(n, link_name: str) -> str:
    """Return 'electrolyzer' | 'fuel_cell' | 'other' based on bus carriers."""
    try:
        bus0 = str(n.links.at[link_name, 'bus0']) if 'bus0' in n.links.columns else ''
        bus1 = str(n.links.at[link_name, 'bus1']) if 'bus1' in n.links.columns else ''
        c0 = str(n.buses.at[bus0, 'carrier']).lower() if bus0 in n.buses.index else ''
        c1 = str(n.buses.at[bus1, 'carrier']).lower() if bus1 in n.buses.index else ''
        if c1 in _H2_CARRIERS:   return 'electrolyzer'
        if c0 in _H2_CARRIERS:   return 'fuel_cell'
    except Exception:
        pass
    return 'other'


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
