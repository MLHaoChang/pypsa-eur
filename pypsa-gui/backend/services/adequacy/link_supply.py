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
  electrical bus1 that is not a split storage's power Link, with a positive
  ``efficiency`` in some hour. It is credited only when EVERY bus it draws
  from (bus0, and each busN with a negative ``efficiencyN``) has an unlimited
  fuel: a non-slack Generator with no ``e_sum_max``, and no Store,
  StorageUnit, Load or other Link on that bus. A store-fed fuel cell is NOT
  credited (0 MW, with a note): full output in every hour overstates
  adequacy whenever the store is empty, and a storage model would
  double-count a store that also feeds an offtake. The fuel side must also be
  free of limits the unit's two states cannot carry, so each of these
  withholds the credit, with a note: a GlobalConstraint that caps the fuel
  Generator's carrier (``operational_limit`` on the carrier, or
  ``primary_energy`` on an attribute the carrier has), and outage data set
  on the fuel Generator itself (only the Link's outage is sampled; a carrier
  default is a plant-class figure and says nothing about a fuel supply, so
  it is not read). An input port on an electrical bus withholds it too.
  The credit is the delivered MW, never below zero.

Occurrence data comes from ``resolve_outage_params(n, "links")`` only; a rate
outside ``[0, 1)`` raises ``OutageRateError``. Units are ordered generators,
then import Links, then firm Links, each by sorted name (AL-4), so every
generator keeps its positional CRN substream. A network with no trusted
import Link and no firm-backup candidate gets ``snapshot_inputs``' result
unchanged (AL-5).

``assemble_fleet`` is the one builder of units, residual and notes;
``sampled_fleet`` (the loops) and ``eh_stages.freeze_fixed_plan`` both call
it, each with its own ``hub_fleet_scope``.

``nothing_to_sample`` is the refusal every loop site shares (AL-7).
"""

from __future__ import annotations

import dataclasses
import math
import re

import numpy as np

from services.adequacy.coupling import NOTHING_TO_SAMPLE

__all__ = [
    "NOTHING_TO_SAMPLE",
    "assemble_fleet",
    "firm_backup_units",
    "nothing_to_sample",
    "sampled_fleet",
]


def _never_failing(model) -> str:
    """AL-2's disclosure for an import Link taken as a firm block."""
    if model.source == "missing":
        why = "no outage data"
    elif "sampling disabled" in str(getattr(model, "reason", "") or ""):
        why = "Link sampling disabled (firm-block comparison)"
    else:
        why = "no finite MTTR in its outage data"
    return f"grid import taken as never failing: {why} for {model.name}"


def sampled_fleet(
    n,
    *,
    keep_zero_capacity: bool = False,
    cfg=None,
    demand_scaled_in_place: bool = False,
):
    """``MCInputs`` for the loops: ``snapshot_inputs`` plus the supplying
    Links (module docstring). Call under the PyPSAService lock."""
    from models.energy_hub import ImportOverlaySpec
    from services.adequacy.eh_stages import hub_fleet_scope

    # No carrier fallback: only a Link the network itself names is a boundary.
    scope = hub_fleet_scope(
        n,
        ImportOverlaySpec(import_carriers=[]),
        sample_links=True,
        keep_zero_capacity=keep_zero_capacity,
    )
    return assemble_fleet(
        n,
        scope,
        keep_zero_capacity=keep_zero_capacity,
        cfg=cfg,
        demand_scaled_in_place=demand_scaled_in_place,
    )


def assemble_fleet(
    n,
    scope,
    *,
    keep_zero_capacity: bool = False,
    cfg=None,
    demand_scaled_in_place: bool = False,
):
    """``MCInputs`` for ``scope`` (a ``hub_fleet_scope`` result): the units in
    AL-4 order, the residual net of the firm-block imports, and the notes.
    The one assembly behind the loops and ``freeze_fixed_plan``, so the same
    network and scope give both the same fleet and the same CRN substreams."""
    from services.adequacy.eh_stages import _hub_side_copy
    from services.adequacy.mc import snapshot_inputs

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
                notes.append(
                    f"import Link {m.name}: no capacity in any hour; not counted"
                )
            elif m.model == "excluded":
                notes.append(f"import Link {m.name}: not counted — {m.reason}")
    else:
        side = n
        if scope.import_links:
            # AL-6: a trusted Link that does not separate the sides is a
            # branch inside the copper plate, not supply.
            notes.append(scope.note)
    inputs = snapshot_inputs(
        side,
        keep_zero_capacity=keep_zero_capacity,
        cfg=cfg,
        demand_scaled_in_place=demand_scaled_in_place,
    )
    firm_units, firm_notes, firm_cands = firm_backup_units(
        side, keep_zero_capacity=keep_zero_capacity
    )
    if firm is None and not candidates and not firm_cands and not notes:
        return inputs  # AL-5: byte-equal
    residual = inputs.residual
    if firm is not None:
        residual = np.ascontiguousarray(residual - firm)
    return dataclasses.replace(
        inputs,
        units=tuple(inputs.units) + tuple(import_units) + tuple(firm_units),
        residual=residual,
        fleet_notes=tuple(notes + firm_notes),
        supply_links=tuple(candidates + firm_cands),
    )


def _gen_series(n, name: str, attr: str, default: float) -> np.ndarray:
    """A Generator's per-snapshot ``attr`` (its series when it has one)."""
    frame = getattr(getattr(n, "generators_t", None), attr, None)
    if frame is not None and name in getattr(frame, "columns", []):
        return frame[name].reindex(n.snapshots).fillna(default).to_numpy(float)
    try:
        v = float(n.generators.at[name, attr])
    except (KeyError, TypeError, ValueError):
        v = default
    return np.full(len(n.snapshots), v if math.isfinite(v) else default, dtype=float)


def _energy_cap_on(n, carrier: str) -> list[str]:
    """The GlobalConstraints that cap the energy of ``carrier``: an
    ``operational_limit`` on the carrier itself, or a ``primary_energy`` on a
    carrier attribute this carrier has. A ``>=`` constraint is a floor."""
    gcs = getattr(n, "global_constraints", None)
    if gcs is None or gcs.empty or not carrier:
        return []
    carriers = getattr(n, "carriers", None)
    out: list[str] = []
    for gc in gcs.index:
        if str(gcs.at[gc, "sense"]).strip() == ">=":
            continue
        kind = str(gcs.at[gc, "type"])
        attr = str(gcs.at[gc, "carrier_attribute"])
        if kind == "operational_limit":
            hit = attr == carrier
        elif kind == "primary_energy":
            try:
                hit = float(carriers.at[carrier, attr]) > 0.0
            except (AttributeError, KeyError, TypeError, ValueError):
                hit = False
        else:
            hit = False
        if hit:
            out.append(str(gc))
    return sorted(out)


def _input_ports(n, lk: str) -> list[tuple[str, str, np.ndarray]]:
    """``(port, bus, draw)`` for every bus the Link draws from: bus0 at 1 per
    MW sent, and each busN (N >= 2) whose ``efficiencyN`` is negative in some
    hour, at ``-efficiencyN`` per MW sent."""
    from services.adequacy.eh_stages import _series

    ldf = n.links
    ports = [("bus0", str(ldf.at[lk, "bus0"]), np.ones(len(n.snapshots)))]
    for col in sorted(c for c in ldf.columns if re.fullmatch(r"bus([2-9]|[1-9]\d+)", c)):
        bus = ldf.at[lk, col]
        if not isinstance(bus, str) or not bus:
            continue
        draw = np.clip(-_series(n, lk, "efficiency" + col[3:], 1.0), 0.0, None)
        if draw.max(initial=0.0) > 0.0:
            ports.append((col, bus, draw))
    return ports


def _fuel_exclusions(
    n, lk: str, bus: str, port: str = "bus0", fuel_outage=None
) -> tuple[list[str], list[str]]:
    """``(why_not, fuel_generators)`` for one input ``port`` of a firm Link.
    ``why_not`` lists EVERY failed condition of the unlimited-fuel rule (each
    test reads its own words, #1257 F1a); empty means the port is fine."""
    from services.adequacy.slack import slack_generator_mask

    why: list[str] = []
    for attr, label in (
        ("stores", "a Store (energy-limited)"),
        ("storage_units", "a StorageUnit (energy-limited)"),
        ("loads", "a Load"),
    ):
        df = getattr(n, attr, None)
        if df is not None and not df.empty and "bus" in df.columns:
            on = sorted(str(i) for i in df.index if str(df.at[i, "bus"]) == bus)
            if on:
                why.append(f"{port} {bus} carries {label}: {', '.join(on)}")
    ldf = n.links
    feeders = sorted(
        str(i) for i in ldf.index if str(i) != lk and str(ldf.at[i, "bus1"]) == bus
    )
    if feeders:
        why.append(f"{port} {bus} is fed by another Link: {', '.join(feeders)}")
    # The fuel is credited whole to one Link, so a second Link drawing on the
    # same bus (any port but bus1, named above) would count it twice.
    ports = [c for c in ldf.columns if c.startswith("bus") and c != "bus1"]
    sharers = sorted(
        str(i)
        for i in ldf.index
        if str(i) != lk and any(str(ldf.at[i, c]) == bus for c in ports)
    )
    if sharers:
        why.append(f"{port} {bus} is shared with another Link: {', '.join(sharers)}")
    gens = getattr(n, "generators", None)
    fuel: list[str] = []
    if gens is not None and not gens.empty:
        slack = slack_generator_mask(gens)
        for g in gens.index:
            if str(gens.at[g, "bus"]) != bus or bool(slack.get(g, False)):
                continue
            try:
                cap = (
                    float(gens.at[g, "e_sum_max"])
                    if "e_sum_max" in gens.columns
                    else math.inf
                )
            except (TypeError, ValueError):
                cap = math.inf
            if math.isnan(cap) or cap == math.inf:
                fuel.append(str(g))
    if not fuel:
        why.append(
            f"{port} {bus} has no fuel Generator without an energy cap (e_sum_max)"
        )
    for g in fuel:
        carrier = str(gens.at[g, "carrier"]) if "carrier" in gens.columns else ""
        caps = _energy_cap_on(n, carrier)
        if caps:
            why.append(
                f"{port} {bus} fuel Generator {g} (carrier {carrier}) is under "
                f"an energy cap (GlobalConstraint {', '.join(caps)})"
            )
        if fuel_outage is not None and str(fuel_outage.at[g, "source"]) == "asset":
            why.append(
                f"{port} {bus} fuel Generator {g} has its own outage data, "
                "which the Link's outage does not carry"
            )
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
        if str(ldf.at[lk, "bus1"]) not in elec or str(ldf.at[lk, "bus0"]) in elec:
            continue
        # A Link that never delivers (efficiency <= 0 in every hour) is a
        # consumer on bus1, not supply.
        if np.any(_series(n, lk, "efficiency", 1.0) > 0.0):
            cands.append(str(lk))
    if not cands:
        return [], [], []
    cands.sort()
    params = resolve_outage_params(n, "links")
    fuel_outage = resolve_outage_params(n, "generators")
    blocks = period_blocks(n.snapshots)
    lctx = ActivityContext(n, "links", blocks)
    gctx = ActivityContext(n, "generators", blocks)
    H = len(n.snapshots)
    units: list = []
    notes: list[str] = []
    bad: list[str] = []
    for lk in cands:
        ports = _input_ports(n, lk)
        why: list[str] = []
        fuels: list[tuple[list[str], np.ndarray]] = []
        for port, bus, draw in ports:
            if bus in elec:
                why.append(f"{port} {bus} is an electrical bus the Link draws from")
                continue
            port_why, fuel = _fuel_exclusions(n, lk, bus, port, fuel_outage)
            why += port_why
            fuels.append((fuel, draw))
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
            notes.append(
                f"firm Link {lk}: not credited (0 MW) — no finite MTTR in its outage data"
            )
            continue
        cap_max, series = lctx.capacity_series(lk, ldf.loc[lk])
        cap = cap_max if series is None else series
        eff = _series(n, lk, "efficiency", 1.0)
        sending = capped_mw(
            n, lk, cap * np.clip(_series(n, lk, "p_max_pu", 1.0), 0.0, None)
        )
        for fuel, draw in fuels:
            fuel_mw = np.zeros(H, dtype=float)
            for g in fuel:
                g_max, g_series = gctx.capacity_series(g, n.generators.loc[g])
                g_cap = g_max if g_series is None else g_series
                fuel_mw += g_cap * np.clip(
                    _gen_series(n, g, "p_max_pu", 1.0), 0.0, None
                )
            # The port takes `draw` per MW sent, so its fuel bounds the MW sent.
            sending = np.minimum(
                sending,
                np.divide(fuel_mw, draw, out=np.full(H, np.inf), where=draw > 0.0),
            )
        # Never below zero: a negative efficiency in some hour is no delivery
        # then, not a load the unit adds while it is up.
        delivered = np.clip(sending * eff, 0.0, None)
        c_max = float(delivered.max()) if H else 0.0
        if c_max <= 0.0 and not keep_zero_capacity:
            continue
        constant = c_max <= 0.0 or bool(np.all(delivered == delivered[0]))
        units.append(
            CoptUnit(
                name=f"link:{lk}",
                capacity_mw=max(c_max, 0.0),
                q=float(occ["rate"]),
                basis=str(occ["basis"] or "FOR"),
                mttr_hours=mttr,
                source=src,
                capacity_series=None if constant else delivered.copy(),
            )
        )
    if bad:
        raise OutageRateError(
            f"outage rate outside [0, 1) on firm Link(s): {', '.join(bad)}. "
            "An outage rate is a probability-like unavailability; fix the "
            "value (or clear it) before running an adequacy engine on this "
            "network."
        )
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
        about = [
            s
            for s in (getattr(inputs, "fleet_notes", ()) or ())
            if any(re.search(rf"(?<![\w-]){re.escape(lk)}(?![\w-])", s) for lk in links)
        ]
        clause = (
            " — and no Link supplying an electrical bus is sampled ("
            + ("; ".join(about) if about else ", ".join(links))
            + ")"
        )
    return (
        f"{NOTHING_TO_SAMPLE} (unavailability + MTTR), so the sampled "
        f"fleet is empty{clause}. An empty fleet would report the entire "
        "horizon as loss of load, which is a statement about missing "
        "input data, not about the system"
    )
