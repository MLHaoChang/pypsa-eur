"""
Generic network CRUD factory.

Lifted out of ``routers/network.py``. The ~80 thin FastAPI CRUD routes stay on
the router and call these helpers. ``routers.network`` re-exports the public
names so ``chat_tools``, ``project_network``, and tests keep importing from the
router. Never imports ``routers.*``.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
from fastapi import HTTPException

from services import attribute_catalog, change_log_service, vintage_service
from services.adequacy.occurrence import BLANK_SPELLINGS as _BLANK_SPELLINGS
from services.carrier_catalog import ensure_carrier
from services.pypsa_service import PyPSAService
from services.serialization import df_to_json
from services.user_timeseries import (
    _user_ts_delete_asset,
    _user_ts_rename_asset,
)

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
    from services.adequacy.eh_columns import coerce_eh_value, eh_columns_for

    n = PyPSAService.get_network()
    # P14: whitelisted Energy Hub tags pass even before their column exists,
    # coerced to their typed value (a bad value is a 422 naming the rule).
    eh = eh_columns_for(component_class)
    out_eh = {}
    for k in [k for k in kwargs if k in eh]:
        try:
            out_eh[k] = coerce_eh_value(component_class, k, kwargs.pop(k))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    allowed = attribute_catalog.input_attributes(n, component_class)
    if not allowed:
        return {**kwargs, **out_eh}
    columns = set(getattr(n, attr).columns)
    kept = {k: v for k, v in kwargs.items() if k in allowed or k in columns}
    return {**kept, **out_eh}


def _normalise_flag_column(n, attr: str) -> None:
    """Phase 12h: keep `p_max_pu_includes_outages` a real `bool` column.

    A first `n.add` on a frame that lacks the column creates it as `object`,
    and an `object` column of PURE bools is the one shape netCDF refuses
    (`unsupported dtype for netCDF4 variable: bool`) — so the next project
    save is a 500 and the undo snapshot fails silently. Called at every
    boundary that can add a row or replace a frame; a no-op on anything but
    generators, and 0.17 ms on a 300-row frame.
    """
    if attr in ("buses", "links"):
        # P14: a first write of an eh_* tag creates the column with NaN for
        # every other row — make it typed (False / "") before anything saves.
        try:
            from services.adequacy.eh_columns import normalise_eh_columns
            normalise_eh_columns(n)
        except Exception:                                     # noqa: BLE001
            pass
        return
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
