"""Bounded grid import and export for checked answers (checked-results S7b).

The Load shape tab's own definitions (`services/results/load_shape.py`), rebuilt
from the solve: grid import is what the "grid"-carrier generators supply; export
is what branches deliver into a grid bus (a bus that only supplies) plus what a
generator off a grid bus absorbs. Energy uses generator weights. The hours at
the import limit compare import with the eh_role grid_import Links'
p_nom_opt x p_max_pu, snapshot by snapshot. The heatmap, week and monthly views
are the tab's own and are not checked.
"""

import math

import numpy as np

from harness import profile_common as common

PROFILE = "site-import/1"
AT_LIMIT = 0.999  # the tab's share of the connection that counts as "at the limit"
REFUSALS = frozenset({"network_scope_required", "load_shape_not_established", "unsupported_snapshot_scope"})
METRICS = {
    "grid_import_energy": {
        "formula_id": "site-grid-import-energy/1",
        "unit": "MWh",
        "label": "Grid import",
    },
    "peak_grid_import": {
        "formula_id": "site-peak-grid-import/1",
        "unit": "MW",
        "label": "Peak grid import",
    },
    "export_energy": {
        "formula_id": "site-export-energy/1",
        "unit": "MWh",
        "label": "Export to the grid",
    },
    "hours_at_import_limit": {
        "formula_id": "site-hours-at-import-limit/1",
        "unit": "h",
        "label": "Weighted hours at the import limit",
    },
}
DISCLOSURE = (
    "Grid import and export as the Load shape tab measures them, rebuilt from the solve with generator "
    "snapshot weights. At the import limit means import at or above 99.9% of the grid_import "
    "connection's p_nom_opt x p_max_pu in that snapshot. The tab's heatmap, week and monthly views are "
    "not checked."
)


def _column(table, name, index):
    if table is None or name not in table.columns or not table.index.equals(index):
        return None
    values = table[name].to_numpy(dtype=float)
    return values.tolist() if np.isfinite(values).all() else None


def source(ctx, entity):
    """Called under the network lock: raw arrays only; arithmetic in calculate."""
    from harness.calculations import _number, _series
    from services.results.energy_flows import period_mask, read_dispatch

    n = ctx.network
    index = common.single_period_index(n)
    if entity != "network":
        raise ValueError("network_scope_required")
    # The tab refuses what the energy flows refuse; so does the profile.
    dispatch, _why = read_dispatch(n, period_mask(n, None), None,
                                   ctx.solver_state.get("last_lost_load"))
    if dispatch is None:
        raise ValueError("load_shape_not_established")
    gens = n.generators
    carrier = gens["carrier"] if "carrier" in gens.columns else None
    generators = [
        {"bus": str(gens.at[g, "bus"]),
         "grid": carrier is not None and str(carrier.get(g, "") or "").strip().lower() == "grid",
         "p": _column(n.generators_t.p, g, index)}
        for g in gens.index
    ]
    # A grid generator's bus is the external grid only when nothing there is the site's own.
    sited = {str(b) for b in n.loads["bus"]} | {str(b) for b in n.storage_units["bus"]} | {
        str(b) for b in n.stores["bus"]}
    external = sorted({g["bus"] for g in generators if g["grid"]} - sited)
    ports = []
    for name in n.links.index:
        for column in n.links.columns:
            k = column[3:]
            if column.startswith("bus") and k.isdigit() and str(n.links.at[name, column]).strip():
                ports.append((str(n.links.at[name, column]), _column(n.links_t.get(f"p{k}"), name, index)))
    for component, accessor in (("lines", "lines_t"), ("transformers", "transformers_t")):
        df = getattr(n, component)
        for name in df.index:
            for k in ("0", "1"):
                ports.append((str(df.at[name, f"bus{k}"]),
                              _column(getattr(getattr(n, accessor), f"p{k}", None), name, index)))
    links = n.links
    connection = []
    if "eh_role" in links.columns:
        component = n.components["Link"]
        for name in links.index[links["eh_role"].astype(str) == "grid_import"]:
            connection.append({"p_nom_opt": _number(links.at[name, "p_nom_opt"]),
                               "p_max_pu": _series(component, "p_max_pu", name, index)})
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "w_gen": [_number(v) for v in n.snapshot_weightings["generators"]],
        "generators": generators,
        "external": external,
        "ports": ports,
        "connection": connection,
        "valid_solve": common.lp_outputs_live(ctx),
    }


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("site_import_source_identity")
    a, b = common.window(raw["index"], scope)
    base = "unverified_solve" if not raw["valid_solve"] else "invalid_or_missing_dispatch"
    reasons = {m: base for m in METRICS}
    available = {}
    gens, ports = raw["generators"], raw["ports"]
    readable = (all(g["p"] is not None for g in gens) and all(p is not None for _bus, p in ports)
                and all(v is not None and v >= 0 for v in raw["w_gen"]))
    w = np.array([v if v is not None else math.nan for v in raw["w_gen"]], dtype=float)
    if raw["valid_solve"] and readable:
        rows = slice(a, b + 1)
        T = len(w)
        external = set(raw["external"])
        grid = [np.asarray(g["p"]) for g in gens if g["grid"]]
        import_mw = np.maximum(np.array(grid), 0.0).sum(axis=0) if grid else np.zeros(T)
        export_mw = np.zeros(T)
        for g in gens:
            if g["bus"] not in external:
                export_mw += np.maximum(-np.asarray(g["p"]), 0.0)
        for bus, p in ports:
            if bus in external:
                export_mw += np.maximum(-np.asarray(p), 0.0)
        if grid:
            available["grid_import_energy"] = math.fsum((import_mw * w)[rows])
            available["peak_grid_import"] = float(import_mw[rows].max())
        else:
            reasons.update(grid_import_energy="no_grid_connection", peak_grid_import="no_grid_connection")
        if grid or external or any(min(g["p"]) < 0 for g in gens):
            available["export_energy"] = math.fsum((export_mw * w)[rows])
        else:
            reasons["export_energy"] = "no_grid_connection"
        limit = _limit(raw["connection"], T)
        if not grid:
            reasons["hours_at_import_limit"] = "no_grid_connection"
        elif limit is None:
            reasons["hours_at_import_limit"] = "no_import_connection"
        else:
            at = (limit > 0) & (import_mw >= AT_LIMIT * limit)
            available["hours_at_import_limit"] = math.fsum((w * at)[rows])
    return common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def _limit(connection, T):
    """MW per snapshot of the grid_import Links, or None without one, unreadable, or
    never above zero (the tab's `_import_capacity` shows no connection then)."""
    if not connection:
        return None
    total = np.zeros(T)
    for link in connection:
        cap, pu = link["p_nom_opt"], np.asarray(link["p_max_pu"], dtype=float)
        if cap is None or cap < 0 or len(pu) != T or not np.isfinite(pu).all():
            return None
        total += cap * pu
    return total if (total > 0).any() else None


def notes(raw):
    lines = ["Load shape heatmaps, the week view and the monthly table are the tab's own and are not checked."]
    if raw["connection"]:
        lines.append("The import limit is p_nom_opt x p_max_pu per snapshot; the tab's week view flags "
                     "against p_nom_opt alone.")
    return lines
