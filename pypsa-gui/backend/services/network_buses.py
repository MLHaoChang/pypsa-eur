"""
Bus specials: coord-aware update, cascade delete, rename.

Lifted out of ``routers/network.py``. The plain create/delete one-liners stay
on the router (CRUD factory). ``apply_update_bus`` takes ``update_component=``
so this module never imports ``routers.*`` — the thin handler injects the
factory. Geometry stays in ``services.network_geometry``.
"""
from __future__ import annotations

from fastapi import HTTPException

from services import change_log_service
from services.network_crud import purge_component_side_data
from services.network_geometry import _recompute_lengths_for_bus
from services.pypsa_service import PyPSAService
from services.study_state import refuse_edit_during_live_study


def apply_update_bus(name: str, bus, *, update_component):
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
    result = update_component("Bus", "buses", name, submitted)
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


def apply_delete_bus_cascade(name: str) -> None:
    refuse_edit_during_live_study()  # P27a A1: not routed via _delete_component
    # Every removal here goes through `purge_component_side_data`, the same
    # cleanup `_delete_component` runs. Without it the cascade — the path a
    # user reaches by deleting a node on the map, so the common one — left an
    # orphaned `_user_ts` entry and a stale vintage bound behind for each of
    # the up-to-seven classes it removes. The orphan is not inert: it is
    # re-injected on every solve and inherited by any later component that
    # reuses the name.
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
                    purge_component_side_data(n, cls, attr, comp)
        for cls, attr in [
            ("Generator", "generators"), ("Load", "loads"),
            ("StorageUnit", "storage_units"), ("Store", "stores"),
        ]:
            df = getattr(n, attr, None)
            if df is not None and not df.empty and "bus" in df.columns:
                for comp in df[df.bus.eq(name)].index.tolist():
                    n.remove(cls, comp)
                    purge_component_side_data(n, cls, attr, comp)
        n.remove("Bus", name)
        purge_component_side_data(n, "Bus", "buses", name)
    change_log_service.log("delete", "Bus", name, f"Deleted bus '{name}' and all connected components")


def apply_rename_bus(name: str, body: dict):
    refuse_edit_during_live_study()  # P27a A1: not routed via _update_component
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name cannot be empty")
    from services.commercial.settlement_inputs import reserved_bus_name

    if reserved_bus_name(new_name):
        # `ic:` names the commercial reference frames' columns (P2 WP2.2-0).
        raise HTTPException(422, f"bus names starting 'ic:' are reserved (got {new_name!r})")
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
    # A bus is placeable too (the site view draws a switchyard per bus): keep
    # its placement across the rename. Same best-effort seam as the CRUD path.
    from services.network_crud import _rename_site_placement

    _rename_site_placement("Bus", name, new_name)
    return {"old_name": name, "new_name": new_name}

