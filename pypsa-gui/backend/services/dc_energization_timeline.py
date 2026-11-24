# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Demand against energization over time of a data-centre project (DC template
review 06; owner decision DC-REVIEW-PICK-1).

Per model period: the peak facility demand (the largest hourly sum of the
network's Loads in it), the energized grid import (the schedule's minimum in
the period, the MW firm all through it), the committed on-site firm capacity
(fixed-size gensets that burn fuel, fixed-size storages) and the shortfall,
the demand that grid plus on-site cannot carry. Read from the open network,
so a saved edit shows on the next read with no solve. After a run the
dispatch overlay adds the energy served, shed and curtailed per period.

Time to power is `services.dc_time_to_power`'s fact, grid-delivered power only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from services import dc_time_to_power as T

#: Carriers that are not firm: weather-driven or the grid itself.
_NOT_FIRM = {"solar", "wind", "onwind", "offwind", "ror", "grid", "AC"}

BASIS_INPUTS = (
    "the largest hourly facility load in each period, the energization "
    "schedule and the committed on-site units."
)
BASIS_RUN = (
    "the same inputs, with the last run's energy served, shed and curtailed "
    "in each period."
)


def _period_masks(n) -> list[tuple[str, np.ndarray]]:
    snaps = n.snapshots
    if isinstance(snaps, pd.MultiIndex):
        level = snaps.get_level_values(0)
        return [(str(p), np.asarray(level == p)) for p in pd.unique(level)]
    return [("all", np.ones(len(snaps), dtype=bool))]


def _load_series(n) -> np.ndarray:
    if n.loads.empty:
        return np.zeros(len(n.snapshots))
    pset = n.get_switchable_as_dense("Load", "p_set")
    return pset.sum(axis=1).to_numpy(dtype=float)


def _year(period: str, n) -> int | None:
    return int(period) if period.isdigit() else None


def onsite_firm(n, year: int | None) -> list[dict]:
    """The committed on-site firm capacity in service in ``year``: ``[{kind,
    mw, units}]`` for gensets (fixed-size, fuel-burning Generators) and
    batteries (fixed-size storages, AC-side MW)."""
    from services.adequacy.site_peak import committed_storage, in_service
    from services.adequacy.slack import is_slack_carrier, is_slack_name
    from services.solver.storage_inventory import list_storages

    gens = []
    g = n.generators
    for name in g.index:
        carrier = str(g.at[name, "carrier"]) if "carrier" in g.columns else ""
        if is_slack_name(name) or is_slack_carrier(carrier) or carrier in _NOT_FIRM:
            continue
        if bool(g.at[name, "p_nom_extendable"]) or not in_service(g, name, year):
            continue
        if "active" in g.columns and not bool(g.at[name, "active"]):
            continue
        mw = float(g.at[name, "p_nom"])
        if mw > 0:
            gens.append((str(name), mw))
    committed, _unsized = committed_storage(n, [e.name for e in list_storages(n)], year)
    entries = {e.name: e for e in list_storages(n)}
    bess = [(u, float(entries[u].p_nom_mw)) for u in committed]
    out = []
    for kind, units in (("genset", gens), ("battery", bess)):
        if units:
            out.append(
                {
                    "kind": kind,
                    "mw": sum(m for _, m in units),
                    "units": [u for u, _ in units],
                }
            )
    return out


def _overlay(n, masks) -> list[dict] | None:
    """Energy served, shed and curtailed (MWh) per period of the last run, or
    None when the network carries no dispatch."""
    from services.adequacy.slack import is_slack_carrier, is_slack_name
    from services.dispatch_status import network_has_dispatch

    if not network_has_dispatch(n) or n.generators_t.p.empty:
        return None
    w = (
        n.snapshot_weightings["generators"].to_numpy(dtype=float)
        if "generators" in n.snapshot_weightings
        else np.ones(len(n.snapshots))
    )
    p = n.generators_t.p.reindex(columns=n.generators.index, fill_value=0.0).fillna(0.0)
    slack = [
        g
        for g in n.generators.index
        if is_slack_name(g) or is_slack_carrier(n.generators.at[g, "carrier"])
    ]
    shed = p[slack].sum(axis=1).to_numpy(dtype=float) if slack else np.zeros(len(w))
    pmax = n.get_switchable_as_dense("Generator", "p_max_pu")
    avail = np.nan_to_num(
        (pmax * n.generators.p_nom_opt.reindex(pmax.columns)).to_numpy(float)
    )
    curt = np.clip(avail - p.reindex(columns=pmax.columns).to_numpy(float), 0.0, None)
    var = [
        i
        for i, g in enumerate(pmax.columns)
        if str(n.generators.at[g, "carrier"]) in _NOT_FIRM - {"grid", "AC"}
    ]
    curtailed = curt[:, var].sum(axis=1) if var else np.zeros(len(w))
    load = _load_series(n)
    out = []
    for key, m in masks:
        shed_e = float((shed[m] * w[m]).sum())
        out.append(
            {
                "period": key,
                "served_mwh": float((load[m] * w[m]).sum()) - shed_e,
                "shed_mwh": shed_e,
                "curtailed_mwh": float((curtailed[m] * w[m]).sum()),
            }
        )
    return out


def timeline(n, meta: dict | None) -> dict:
    """The timeline of the open network ``n`` and its template ``meta``."""
    from harness.time_to_power_calculations import _spec

    spec = _spec(meta or {})
    stages, reason = [], None
    try:
        stages = T.import_stages(n, meta)
    except T.StagesUnavailable as exc:
        reason = {"reason": exc.reason, "detail": exc.detail}
    by_stage = {s["period"]: s for s in stages}
    ttp = T.time_to_power(spec, stages, reason)
    load = _load_series(n)
    masks = _period_masks(n)
    periods = []
    for key, m in masks:
        demand = float(load[m].max()) if m.any() else 0.0
        stage = by_stage.get(key)
        grid = float(stage["energized_mw"]) if stage else 0.0
        site = onsite_firm(n, _year(key, n))
        site_mw = sum(k["mw"] for k in site)
        periods.append(
            {
                "period": key,
                "start": stage["start"] if stage else None,
                "end": stage["end"] if stage else None,
                "demand_mw": demand,
                "grid_mw": grid,
                "grid_set": bool(stage["set"]) if stage else False,
                "onsite": site,
                "onsite_mw": site_mw,
                "shortfall_mw": max(demand - grid - site_mw, 0.0),
            }
        )
    run = _overlay(n, masks)
    if run is not None:
        by_run = {r["period"]: r for r in run}
        for p in periods:
            p["run"] = by_run.get(p["period"])
    run_state = None
    if run is not None:
        from services.dispatch_status import dispatch_status

        run_state = "out_of_date" if dispatch_status(n) == "stale" else "fresh"
    return {
        "unit": "MW",
        "basis": "run" if run is not None else "inputs",
        "run_state": run_state,
        "basis_note": BASIS_RUN if run is not None else BASIS_INPUTS,
        "periods": periods,
        "time_to_power": ttp,
        "stage_reason": reason,
    }
