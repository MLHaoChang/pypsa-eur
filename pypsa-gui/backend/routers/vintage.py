"""
Per-period capacity bounds endpoints.

Read-friendly REST: the bounds live in `n.meta["vintage_bounds"]` and are
applied transiently inside `_apply_modelling_assumptions` at solve time, so
these endpoints just mutate the saved dict. The PyPSA service lock is held
for writes; reads are lock-free.

All mutating endpoints sit under /api/network so the main.py middleware
captures an undo snapshot and clears stale solver dispatch automatically.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services import change_log_service
from services.pypsa_service import PyPSAService
from services.vintage_service import (
    SUPPORTED_COMPONENTS,
    delete_bounds_for_asset,
    get_all_bounds,
    set_bounds_for_asset,
)

router = APIRouter()


class PeriodBound(BaseModel):
    """
    One row in the per-period bounds editor. Either field may be omitted
    (meaning "use the asset's scalar value"); supplying neither clears the
    row and reverts to the scalar for that period.
    """

    p_nom_min: float | None = Field(default=None, ge=0)
    p_nom_max: float | None = Field(default=None, ge=0)


class VintageBoundsUpdate(BaseModel):
    """
    Full overwrite of one asset's per-period bounds. Keys are period years
    as strings (so they survive JSON round-trip identical to how they're
    stored).
    """

    bounds: dict[str, PeriodBound] = Field(default_factory=dict)


@router.get("/vintage_bounds")
def list_vintage_bounds():
    """
    Return every saved bound. Shape:
    `{component_class: {asset_name: {period_str: {p_nom_min?, p_nom_max?}}}}`.

    Always returns an object (empty when nothing is saved) so the frontend
    can simplify its handling.
    """
    n = PyPSAService.get_network()
    return {
        "bounds": get_all_bounds(n),
        "supported_components": list(SUPPORTED_COMPONENTS.keys()),
    }


@router.get("/vintage_results")
def list_vintage_results():
    """
    Per-vintage solve results captured at the end of the last LP run.

    Shape: `{component_class: {parent_name: {capacity_field, initial_capacity,
    periods: [{build_year, p_nom_opt, p_nom_min, p_nom_max}, ...]}}}`.
    Used by the Capacity Expansion view to emit one row per (asset, period)
    build year instead of collapsing every vintage onto the parent's row
    (which only has a single, pre-expansion build_year).
    """
    n = PyPSAService.get_network()
    meta = getattr(n, "meta", None) or {}
    results = meta.get("vintage_results")
    if not isinstance(results, dict):
        results = {}
    return {"results": results}


@router.put("/vintage_bounds/{component_class}/{name}")
def update_vintage_bounds(component_class: str, name: str, payload: VintageBoundsUpdate):
    """
    Overwrite the per-period bounds for one asset. The body's `bounds`
    dict is the new state — pass `{}` (or omit the field) to clear all
    per-period entries for the asset.

    Returns the cleaned dict that was actually saved (rows with neither min
    nor max are dropped server-side).
    """
    if component_class not in SUPPORTED_COMPONENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported component class: {component_class}. "
                   f"Supported: {sorted(SUPPORTED_COMPONENTS.keys())}",
        )
    n = PyPSAService.get_network()
    spec = SUPPORTED_COMPONENTS[component_class]
    df = getattr(n, spec["attr"], None)
    if df is None or name not in df.index:
        raise HTTPException(
            status_code=404,
            detail=f"{component_class} '{name}' not found",
        )
    raw = {k: v.model_dump(exclude_none=True) for k, v in (payload.bounds or {}).items()}
    # Reject bounds keyed by periods that aren't part of the network's
    # investment-period axis. The solver's vintage-expand step silently
    # skips entries whose period isn't in the loop's `periods`, so a
    # mis-typed `{"2099": {...}}` would be persisted, surface in the UI
    # as "saved", but never actually constrain a solve. Surfacing 400 at
    # write-time gives the user clear feedback they need to set the
    # network's investment periods first (or fix the typo). Also blocks
    # the no-multi-period case where the bounds are inherently dead.
    if raw:
        try:
            valid_periods = {int(p) for p in n.investment_periods}
        except Exception:
            valid_periods = set()
        if not valid_periods:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot set per-period bounds on a flat (single-period) "
                    "network. Promote snapshots to multi-period first via "
                    "POST /api/network/snapshots/multi_period, then retry."
                ),
            )
        bad_keys: list[str] = []
        for key in raw:
            try:
                if int(key) not in valid_periods:
                    bad_keys.append(str(key))
            except (TypeError, ValueError):
                bad_keys.append(str(key))
        if bad_keys:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Period key(s) {bad_keys!r} not in n.investment_periods "
                    f"({sorted(valid_periods)}). Vintage bounds keyed on "
                    "non-existent periods would be silently skipped by the "
                    "solver. Fix the keys (or add the missing periods to "
                    "the network) and retry."
                ),
            )
    with PyPSAService.get_lock():
        saved = set_bounds_for_asset(n, component_class, name, raw)
        change_log_service.log(
            "update", component_class, name,
            f"per-period bounds: {len(saved)} period(s)",
        )
    return {"name": name, "component_class": component_class, "bounds": saved}


@router.delete("/vintage_bounds/{component_class}/{name}")
def remove_vintage_bounds(component_class: str, name: str):
    """
    Clear every per-period bound for one asset. Idempotent (200 even if
    nothing was saved).
    """
    if component_class not in SUPPORTED_COMPONENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported component class: {component_class}",
        )
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        removed = delete_bounds_for_asset(n, component_class, name)
        if removed:
            change_log_service.log(
                "delete", component_class, name,
                "per-period bounds cleared",
            )
    return {"name": name, "component_class": component_class, "removed": removed}


@router.post("/vintage_bounds/_cleanup_orphans")
def cleanup_orphan_vintages():
    """
    Find and remove TWO kinds of orphans:

    1. Vintage rows in `n.<comp>` matching the `{parent}@{4-digit-year}`
       naming convention that survived a previous solve's restore (typical
       cause: crash mid-flow or project saved with vintages mid-LP).
    2. Per-period bounds entries in `n.meta["vintage_bounds"]` keyed by
       asset names that no longer exist in the network (typical cause:
       project save+reload where the bounds were authored against an
       asset that was later removed via a path that didn't cascade —
       e.g. importing a netcdf that lacks the asset, scenario fork).
       At solve time these entries are silently skipped (apply_vintage_bounds
       checks `asset_name not in df.index`), so they don't break the LP,
       but they clutter the bounds editor and confuse users into thinking
       a phantom asset is still being sized.

    Returns the per-class counts that were removed. Idempotent — running on
    a clean network is a no-op that returns zeros across the board.
    """
    import re
    n = PyPSAService.get_network()
    # Tightened pattern — original `^.+@\d{4}$` matched legitimate user-named
    # assets like "Solar Farm @2030" (a human label with deliberate spacing).
    # The vintage naming convention `vintage_service._vintage_name` emits
    # `{parent}@{period}` with no embedded `@` in the parent — restrict the
    # parent group to `[^@]+` so a real "@" in the user's asset name
    # excludes it from cleanup.
    #
    # Defense-in-depth for legitimately-named assets that slip past the
    # regex: delete only when EITHER the name is in the transient registry
    # OR the year suffix matches one of `n.investment_periods`. The
    # registry covers in-session orphans (e.g. solver crash mid-restore).
    # The period-match covers the PRIMARY use case — orphans persisting
    # across a project save+reload, where `PyPSAService.reset_network()`
    # / project-load wipes the transient registry. Pre-I4 the endpoint
    # relied on regex alone (vulnerable to user-asset false positives);
    # I4 added the AND-with-transient check which BROKE the post-reload
    # path. Re-relaxed here to OR: legitimate vintages always reference
    # real investment periods, so the period-match is a safe substitute
    # signal when the registry has been cleared.
    pattern = re.compile(r"^[^@]+@(\d{4})$")
    try:
        investment_periods = {int(p) for p in n.investment_periods}
    except Exception:
        investment_periods = set()
    removed_by_class: dict[str, list[str]] = {}
    removed_bounds_by_class: dict[str, list[str]] = {}
    with PyPSAService.get_lock():
        for cls, spec in SUPPORTED_COMPONENTS.items():
            df = getattr(n, spec["attr"], None)
            if df is None or df.empty:
                continue
            transient_set = PyPSAService.get_transient_rows(cls)
            victims: list[str] = []
            for name in df.index:
                name_str = str(name)
                m = pattern.match(name_str)
                if not m:
                    continue
                in_registry = name_str in transient_set
                try:
                    suffix_year = int(m.group(1))
                except (TypeError, ValueError):
                    suffix_year = None
                period_match = (
                    suffix_year is not None
                    and suffix_year in investment_periods
                )
                if in_registry or period_match:
                    victims.append(name_str)
            if not victims:
                continue
            for name in victims:
                try:
                    n.remove(cls, name)
                    PyPSAService.unmark_transient(cls, name)
                except Exception:
                    # n.remove can fail on edge cases (e.g. the row already
                    # gone from a parallel cleanup). Skip and continue.
                    continue
            removed_by_class[cls] = victims
            change_log_service.log(
                "delete", cls, "",
                f"cleanup_orphan_vintages: removed {len(victims)} orphan vintage row(s)",
            )

        # Second pass: prune n.meta["vintage_bounds"] entries whose asset
        # name no longer exists in the network. Walks the saved bounds
        # bucket directly (not via SUPPORTED_COMPONENTS which is keyed by
        # the same class names) and drops the orphans via the canonical
        # delete_bounds_for_asset helper so the meta and any side stores
        # (vintage_results) stay consistent.
        from services.vintage_service import (
            _ensure_meta_bucket,
            delete_bounds_for_asset,
        )
        bounds_bucket = _ensure_meta_bucket(n)
        # Snapshot keys before iterating — delete_bounds_for_asset mutates
        # the dict, so we can't iterate it directly without RuntimeError.
        for cls in list(bounds_bucket.keys()):
            spec = SUPPORTED_COMPONENTS.get(cls)
            if spec is None:
                # Bounds saved against an unknown component class — drop
                # the whole class bucket, it can't be applied at solve time
                # anyway.
                orphans = list(bounds_bucket[cls].keys())
                bounds_bucket.pop(cls, None)
                if orphans:
                    removed_bounds_by_class[cls] = orphans
                continue
            df = getattr(n, spec["attr"], None)
            asset_index = set(df.index) if df is not None else set()
            orphans = [
                name for name in list(bounds_bucket[cls].keys())
                if name not in asset_index
            ]
            for name in orphans:
                delete_bounds_for_asset(n, cls, name)
            if orphans:
                removed_bounds_by_class[cls] = orphans
                change_log_service.log(
                    "delete", cls, "",
                    f"cleanup_orphan_vintages: removed {len(orphans)} orphan "
                    f"vintage_bounds entry(ies)",
                )
    total = sum(len(v) for v in removed_by_class.values())
    total_bounds = sum(len(v) for v in removed_bounds_by_class.values())
    return {
        "total_removed": total,
        "by_class": removed_by_class,
        "total_removed_bounds": total_bounds,
        "by_class_bounds": removed_bounds_by_class,
    }
