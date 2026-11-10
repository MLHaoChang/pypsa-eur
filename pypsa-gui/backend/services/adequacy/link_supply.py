"""
The adequacy loops' ONE sampled-fleet rule (adequacy loops 01, GK A (20)).

``snapshot_inputs`` builds the fleet from electrical Generators and storage.
A network whose supply reaches the electrical side through Links (a grid
connection, a fuel cell) had nothing to sample there. ``sampled_fleet`` adds
the Links that SUPPLY an electrical bus, in two roles:

* **Grid import** (AL-2, AL-9). The Links ``eh_stages.hub_fleet_scope``
  trusts as the boundary (``eh_role == grid_import`` or an ``eh_poc``
  endpoint; never the carrier fallback), and only when they separate the hub
  from the grid. The fleet and the demand are the HUB side; the grid side
  drops out, so a grid Generator is no longer netted as must-take on a copper
  plate (a double count). Each Link is a two-state unit at its hourly
  delivered cap, the ``mc_certify`` "sampled_unit" model. A Link without a
  chainable outage record is a firm block netted from the residual, and the
  inputs say so ("grid import taken as never failing: …").
* **Firm backup** (AL-1). A non-slack Link from a non-electrical bus0 into an
  electrical bus1 that is not a split storage's power Link. It is credited
  only when bus0 has an unlimited fuel: a non-slack Generator with no
  ``e_sum_max``, and no Store, StorageUnit, Load or other delivering Link on
  bus0. A store-fed fuel cell is NOT credited (0 MW, with a note): full
  output in every hour overstates adequacy whenever the store is empty, and a
  storage model would double-count a store that also feeds an offtake.

Occurrence data comes from ``resolve_outage_params(n, "links")`` only; a rate
outside ``[0, 1)`` raises ``OutageRateError``. Units are ordered generators,
then import Links, then firm Links, each by sorted name (AL-4), so every
generator keeps its positional CRN substream. A network with no trusted
import Link and no firm-backup candidate gets ``snapshot_inputs``' result
unchanged (AL-5).

``nothing_to_sample`` is the refusal every loop site shares (AL-7).
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from services.adequacy.coupling import NOTHING_TO_SAMPLE

__all__ = ["NOTHING_TO_SAMPLE", "firm_backup_units", "nothing_to_sample",
           "sampled_fleet"]


def _never_failing(model) -> str:
    """AL-2's disclosure for an import Link taken as a firm block."""
    why = ("no outage data" if model.source == "missing"
           else "no finite MTTR in its outage data")
    return f"grid import taken as never failing: {why} for {model.name}"


def sampled_fleet(n, *, keep_zero_capacity: bool = False, cfg=None,
                  demand_scaled_in_place: bool = False):
    """``MCInputs`` for the loops: ``snapshot_inputs`` plus the supplying
    Links (module docstring). Call under the PyPSAService lock."""
    from models.energy_hub import ImportOverlaySpec
    from services.adequacy.eh_stages import _hub_side_copy, hub_fleet_scope
    from services.adequacy.mc import snapshot_inputs

    # No carrier fallback: only a Link the network itself names is a boundary.
    scope = hub_fleet_scope(n, ImportOverlaySpec(import_carriers=[]),
                            sample_links=True,
                            keep_zero_capacity=keep_zero_capacity)
    notes: list[str] = []
    candidates: list[str] = []
    import_units: list = []
    firm = None
    if scope.mode == "hub_side":
        side = _hub_side_copy(n, scope.excluded_buses)
        candidates += sorted(str(x) for x in scope.import_links)
        import_units = sorted(scope.import_units, key=lambda u: u.name)
        firm = np.asarray(scope.import_firm_mw, dtype=np.float64)
        for m in scope.link_models:
            if m.model == "firm_block":
                notes.append(_never_failing(m))
            elif m.model == "islanded":
                notes.append(f"import Link {m.name}: no capacity in any hour; not counted")
            elif m.model == "excluded":
                notes.append(f"import Link {m.name}: not counted — {m.reason}")
    else:
        side = n
        if scope.import_links:
            # AL-6: a trusted Link that does not separate the sides is a
            # branch inside the copper plate, not supply.
            notes.append(scope.note)
    inputs = snapshot_inputs(side, keep_zero_capacity=keep_zero_capacity,
                             cfg=cfg,
                             demand_scaled_in_place=demand_scaled_in_place)
    firm_units, firm_notes, firm_cands = firm_backup_units(
        side, keep_zero_capacity=keep_zero_capacity)
    if not candidates and not firm_cands and not notes:
        return inputs  # AL-5: byte-equal
    residual = inputs.residual
    if firm is not None:
        residual = np.ascontiguousarray(residual - firm)
    return dataclasses.replace(
        inputs,
        units=tuple(inputs.units) + tuple(import_units) + tuple(firm_units),
        residual=residual,
        fleet_notes=tuple(notes + firm_notes),
        supply_links=tuple(candidates + firm_cands))


def _fuel_exclusions(n, lk: str, bus: str) -> tuple[list[str], list[str]]:
    """``(why_not, fuel_generators)`` for a firm Link's bus0. ``why_not``
    lists EVERY failed condition of the unlimited-fuel rule (each test reads
    its own words, #1257 F1a); empty means the Link is credited."""
    from services.adequacy.slack import slack_generator_mask

    why: list[str] = []
    for attr, label in (("stores", "a Store (energy-limited)"),
                        ("storage_units", "a StorageUnit (energy-limited)"),
                        ("loads", "a Load")):
        df = getattr(n, attr, None)
        if df is not None and not df.empty and "bus" in df.columns:
            on = sorted(str(i) for i in df.index if str(df.at[i, "bus"]) == bus)
            if on:
                why.append(f"bus0 {bus} carries {label}: {', '.join(on)}")
    ldf = n.links
    feeders = sorted(str(i) for i in ldf.index
                     if str(i) != lk and str(ldf.at[i, "bus1"]) == bus)
    if feeders:
        why.append(f"bus0 {bus} is fed by another Link: {', '.join(feeders)}")
    gens = getattr(n, "generators", None)
    fuel: list[str] = []
    if gens is not None and not gens.empty:
        slack = slack_generator_mask(gens)
        for g in gens.index:
            if str(gens.at[g, "bus"]) != bus or bool(slack.get(g, False)):
                continue
            try:
                cap = float(gens.at[g, "e_sum_max"]) if "e_sum_max" in gens.columns else math.inf
            except (TypeError, ValueError):
                cap = math.inf
            if math.isnan(cap) or cap == math.inf:
                fuel.append(str(g))
    if not fuel:
        why.append(f"bus0 {bus} has no fuel Generator without an energy cap (e_sum_max)")
    return why, fuel


def firm_backup_units(n, *, keep_zero_capacity: bool = False):
    """``(units, notes, candidates)`` for the firm-backup role (AL-1, AL-3)."""
    from services.adequacy.activity import ActivityContext, period_blocks
    from services.adequacy.copt import CoptUnit
    from services.adequacy.eh_stages import _series
    from services.adequacy.energization import capped_mw
    from services.adequacy.metrics import electrical_columns
    from services.adequacy.occurrence import (
        OutageRateError,
        rate_is_usable,
        resolve_outage_params,
    )
    from services.adequacy.slack import is_slack_carrier, is_slack_name
    from services.solver.storage_inventory import list_storages

    ldf = getattr(n, "links", None)
    if ldf is None or ldf.empty:
        return [], [], []
    elec = set(electrical_columns(n, list(n.buses.index)))
    split = {x for e in list_storages(n) for x in (e.charger, e.discharger) if x}
    cands: list[str] = []
    for lk in ldf.index:
        carrier = ldf.at[lk, "carrier"] if "carrier" in ldf.columns else ""
        role = str(ldf.at[lk, "eh_role"]) if "eh_role" in ldf.columns else ""
        if is_slack_name(lk) or is_slack_carrier(carrier) or role == "grid_import":
            continue
        if str(lk) in split:
            continue
        if str(ldf.at[lk, "bus1"]) in elec and str(ldf.at[lk, "bus0"]) not in elec:
            cands.append(str(lk))
    if not cands:
        return [], [], []
    cands.sort()
    params = resolve_outage_params(n, "links")
    blocks = period_blocks(n.snapshots)
    lctx = ActivityContext(n, "links", blocks)
    gctx = ActivityContext(n, "generators", blocks)
    H = len(n.snapshots)
    units: list = []
    notes: list[str] = []
    bad: list[str] = []
    for lk in cands:
        bus0 = str(ldf.at[lk, "bus0"])
        why, fuel = _fuel_exclusions(n, lk, bus0)
        if why:
            notes.append(f"firm Link {lk}: not credited (0 MW) — {'; '.join(why)}")
            continue
        occ = params.loc[lk]
        src = str(occ["source"])
        if src == "missing":
            notes.append(f"firm Link {lk}: not credited (0 MW) — no outage data")
            continue
        if not rate_is_usable(occ["rate"]):
            bad.append(f"{lk} (rate {float(occ['rate']):g})")
            continue
        mttr = float(occ["mttr_hours"])
        if not (math.isfinite(mttr) and mttr > 0):
            notes.append(f"firm Link {lk}: not credited (0 MW) — no finite MTTR in its outage data")
            continue
        cap_max, series = lctx.capacity_series(lk, ldf.loc[lk])
        cap = cap_max if series is None else series
        eff = _series(n, lk, "efficiency", 1.0)
        sending = capped_mw(n, lk, cap * np.clip(_series(n, lk, "p_max_pu", 1.0), 0.0, None))
        fuel_mw = np.zeros(H, dtype=float)
        for g in fuel:
            g_max, g_series = gctx.capacity_series(g, n.generators.loc[g])
            fuel_mw += g_max if g_series is None else g_series
        delivered = np.minimum(sending, fuel_mw) * eff
        c_max = float(delivered.max()) if H else 0.0
        if c_max <= 0.0 and not keep_zero_capacity:
            continue
        constant = c_max <= 0.0 or bool(np.all(delivered == delivered[0]))
        units.append(CoptUnit(
            name=f"link:{lk}", capacity_mw=max(c_max, 0.0), q=float(occ["rate"]),
            basis=str(occ["basis"] or "FOR"), mttr_hours=mttr, source=src,
            capacity_series=None if constant else delivered.copy()))
    if bad:
        raise OutageRateError(
            f"outage rate outside [0, 1) on firm Link(s): {', '.join(bad)}. "
            "An outage rate is a probability-like unavailability; fix the "
            "value (or clear it) before running an adequacy engine on this "
            "network.")
    return units, notes, cands


def nothing_to_sample(inputs) -> str | None:
    """The shared refusal (AL-7): ``None`` when the fleet samples something,
    else the message, decided on the fleet the caller samples (GK A (20)).
    It names only what that fleet's walk established (#1318 F1): each
    supplying Link's own reason when the Link walk built it, nothing about
    Links otherwise."""
    if inputs.units:
        return None
    links = tuple(getattr(inputs, "supply_links", ()) or ())
    clause = ""
    if links:
        # Only a fleet the Link walk built names Links; a generator-only
        # fleet (mc_loop's `snapshot_inputs`) claims nothing about them.
        about = [s for s in (getattr(inputs, "fleet_notes", ()) or ())
                 if any(lk in s for lk in links)]
        clause = (" — and no Link supplying an electrical bus is sampled ("
                  + ("; ".join(about) if about else ", ".join(links)) + ")")
    return (f"{NOTHING_TO_SAMPLE} (unavailability + MTTR), so the sampled "
            f"fleet is empty{clause}. An empty fleet would report the entire "
            "horizon as loss of load, which is a statement about missing "
            "input data, not about the system")
