"""
The site's peak load against what its connection, firm gensets and firm
ride-through batteries supply
(DC grid readiness 01 and 02, gatekeeper review of 02).

One check, two callers: a data-center project created with a ``dc_load`` body
is refused (422) when a period's peak does not fit (``eh_templates._check_dc_peak``),
and the grid-readiness scorecard reports the same rows (``grid_readiness``).

Hour by hour: load(t) ≤ Σ energized import(t) × efficiency + firm gensets and batteries, and
each period reports its binding hour (least headroom), where

* the load is the hourly-mean sum of the fixed loads' ``p_set`` (a flexible
  conversion unit such as an electrolyser is a decision and is not counted;
  the in-hour peak of an AI-training load is a few per cent higher, which the
  scorecard reports separately);
* the energized import is min(p_nom × the Link's p_max_pu(t), the
  energization schedule's E(t)) per import Link and hour
  (``services.adequacy.energization.capped_mw``); a normally open tie supplies 0;
* the efficiency is each import Link's ``efficiency`` (what reaches the site
  side of the PoC Link; losses further in are not counted);
* firm gensets are the island sidecar's committed genset fleet less its k
  largest units when the requirements ask for genset N+k
  (``genset_redundancy_n``); without a sidecar no genset counts as firm;
* ride-through batteries (DC 03, gatekeeper review) add only their free
  energy, hour by hour. The island study holds an outage reserve in them at
  every hour (``services.solver.island`` row (a)): R = max over the period of
  (critical load − firm gensets)⁺ × ``sustained_h``, plus the seamless bridge
  B (the delivered import × the slowest genset's start and sync) when a
  grid-forming one is among them. Energy above R shaves the hours the import
  and gensets fall short: a state-of-charge walk over the period, starting
  full (the optimistic dispatch), discharging min(shortfall, Σ p_nom,
  energy above R) and recharging from the headroom at ≤ Σ p_nom. Only power
  is free twice (on grid loss the shaving stops); energy is counted once.
  Committed ``ride_through_storage`` StorageUnits only; a UPS bridges
  seconds and is not firm.

``firm`` is a list of contributions, one entry per source of firm MW, at the
binding hour.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def import_links(n) -> list[str]:
    """The import Links, by the EH study's own selector (``eh_role`` →
    ``eh_poc`` bus → carrier)."""
    from models.energy_hub import ImportOverlaySpec
    from services.adequacy.archetypes import select_import_links

    return select_import_links(n, ImportOverlaySpec())


def _value(v):
    return v.get("value") if isinstance(v, dict) else v


def firm_genset_mw(n, island: dict | None) -> tuple[float, str]:
    """The committed genset fleet's firm MW and the rule that gave it, from a
    raw island sidecar (``{"units", "requirements"}``, values ``{value, source}``)."""
    if not island:
        return 0.0, "no island sidecar: no genset is counted as firm"
    names = [u for u, d in (island.get("units") or {}).items()
             if d.get("kind") == "genset" and u in n.generators.index
             and not bool(n.generators.at[u, "p_nom_extendable"])]
    sizes = sorted((float(n.generators.at[u, "p_nom"]) for u in names), reverse=True)
    k = int(_value((island.get("requirements") or {}).get("genset_redundancy_n")) or 0)
    if k:
        return sum(sizes[k:]), f"genset N+{k}: the fleet less its {k} largest unit(s)"
    return sum(sizes), "the whole genset fleet (no genset redundancy required)"


def ride_through_batteries(n, island: dict | None) -> dict:
    """The committed ride-through batteries the peak check may shave with:
    ``{names, p_mw, e_mwh, eta_store, eta_dispatch, reason}``. ``e_mwh`` is
    deliverable (after ``efficiency_dispatch``); the efficiencies are
    energy-weighted. The requirements' ``ride_through_storage`` names them
    (every bess when it is absent, as the schema fills it); an extendable unit
    counts 0, as an extendable genset does. ``reason`` says why there are
    none."""
    none = {"names": [], "p_mw": 0.0, "e_mwh": 0.0, "eta_store": 1.0, "eta_dispatch": 1.0}
    if not island:
        return {**none, "reason": "no island sidecar: no battery is counted as firm"}
    req = island.get("requirements") or {}
    names = req.get("ride_through_storage")
    if names is None:
        names = [u for u, d in (island.get("units") or {}).items() if d.get("kind") == "bess"]
    su = n.storage_units
    names = [u for u in names if u in su.index and not bool(su.at[u, "p_nom_extendable"])
             and float(su.at[u, "p_nom"]) > 0]
    if not names:
        return {**none, "reason": "no committed ride-through battery"}
    d = _value(req.get("sustained_h"))
    if not d or float(d) <= 0:
        return {**none, "reason": "no sustained ride-through time in the island requirements: "
                                  "no battery is counted as firm"}
    stored = {u: float(su.at[u, "p_nom"]) * float(su.at[u, "max_hours"]) for u in names}
    total = sum(stored.values()) or 1.0
    eta_d = sum(float(su.at[u, "efficiency_dispatch"]) * stored[u] for u in names) / total
    eta_s = sum(float(su.at[u, "efficiency_store"]) * stored[u] for u in names) / total
    return {"names": names, "p_mw": sum(float(su.at[u, "p_nom"]) for u in names),
            "e_mwh": eta_d * sum(stored.values()), "eta_store": eta_s, "eta_dispatch": eta_d,
            "sustained_h": float(d), "reason": None}


def _named_load(n, names) -> np.ndarray:
    """The named loads' total p_set per snapshot (0 where none is in the network)."""
    names = [x for x in names if x in n.loads.index]
    if not names:
        return np.zeros(len(n.snapshots))
    ts = n.loads_t.p_set.reindex(columns=names)
    static = n.loads.loc[names, "p_set"] if "p_set" in n.loads.columns else 0.0
    return ts.fillna(static).reindex(n.snapshots).fillna(0.0).sum(axis=1).to_numpy(dtype=float)


def outage_reserve_mwh(n, island: dict | None, mask=None, delivered=None) -> tuple[float, str]:
    """The energy the island study keeps in the ride-through batteries at
    every hour (row (a)): max over the hours in ``mask`` of (critical load −
    firm gensets)⁺ × ``sustained_h``, plus the seamless bridge B when a
    grid-forming ride-through battery is in the set: max ``delivered`` import
    × the slowest genset's (start_s + sync_s). Deliverable MWh, and the words."""
    rt = ride_through_batteries(n, island)
    if not rt["names"]:
        return 0.0, rt["reason"]
    units = island.get("units") or {}
    mask = np.ones(len(n.snapshots), dtype=bool) if mask is None else mask
    critical = [u for u, d in units.items() if d.get("kind") == "load" and d.get("critical")]
    g, _ = firm_genset_mw(n, island)
    short = np.clip(_named_load(n, critical)[mask] - g, 0.0, None)
    d = rt["sustained_h"]
    r = float(short.max()) * d if short.size else 0.0
    words = f"{r:.4g} MWh outage reserve (critical load less firm gensets for {d:g} h)"
    scen = (island.get("requirements") or {}).get("scenarios") or []
    gfm = [u for u in rt["names"] if str(_value((units.get(u) or {}).get("control"))) in ("gfm_droop", "gfm_vsm")]
    if "seamless" in scen and gfm and delivered is not None:
        joins = max((float(_value(v.get("start_s")) or 0.0) + float(_value(v.get("sync_s")) or 0.0)
                     for v in units.values() if v.get("kind") == "genset"), default=0.0)
        b = float(np.max(delivered[mask], initial=0.0)) * joins / 3600.0
        if b > 0:
            r += b
            words += f" plus {b:.3g} MWh seamless bridge ({joins:g} s until the gensets join)"
    return r, words


def _shave(short: np.ndarray, spare: np.ndarray, rt: dict, reserve: float, hours: float) -> np.ndarray:
    """The batteries' discharge MW per hour of one period: a state-of-charge
    walk that starts full, discharges min(shortfall, Σ p_nom, energy above
    the reserve) and recharges from the spare headroom at ≤ Σ p_nom."""
    out = np.zeros(short.size)
    soc, cap, p = rt["e_mwh"], rt["e_mwh"], rt["p_mw"]
    gain = rt["eta_store"] * rt["eta_dispatch"]
    for i in range(short.size):
        if short[i] > 0:
            out[i] = max(0.0, min(short[i], p, (soc - reserve) / hours))
            soc -= out[i] * hours
        elif spare[i] > 0:
            soc = min(cap, soc + min(spare[i], p) * hours * gain)
    return out


def _fixed_load(n) -> pd.Series:
    """The fixed loads' total p_set per snapshot (static p_set where a load
    has no series)."""
    ts = n.loads_t.p_set.reindex(columns=n.loads.index)
    static = n.loads["p_set"] if "p_set" in n.loads.columns else pd.Series(0.0, index=n.loads.index)
    return ts.fillna(static).reindex(n.snapshots).fillna(0.0).sum(axis=1)


def _periods(n) -> list[tuple[str, np.ndarray]]:
    """(label, snapshot mask) per period: each investment period on a
    multi-period axis, else one period named for the first snapshot's year."""
    if isinstance(n.snapshots, pd.MultiIndex):
        level = n.snapshots.get_level_values(0)
        return [(str(p), np.asarray(level == p)) for p in pd.unique(level)]
    first = n.snapshots[0] if len(n.snapshots) else None
    label = str(first.year) if hasattr(first, "year") else "all"
    return [(label, np.ones(len(n.snapshots), dtype=bool))]


def _snapshot_hours(n) -> float:
    """Δ, the hours per snapshot, from the snapshot index (never from the
    weightings), as the island rows read it; 1 when it cannot tell."""
    idx = n.snapshots.get_level_values(-1) if isinstance(n.snapshots, pd.MultiIndex) else n.snapshots
    if len(idx) < 2 or not isinstance(idx, pd.DatetimeIndex):
        return 1.0
    step = pd.Series(idx).diff().dropna().dt.total_seconds()
    step = step[step > 0]
    return float(step.min()) / 3600.0 if len(step) else 1.0


def _energized(n, link: str) -> np.ndarray:
    """E_l(t) in MW per snapshot: min(p_nom × the Link's own p_max_pu(t), the
    energization schedule's E(t)). The Link's p_max_pu bounds it too: a
    normally open tie (p_max_pu 0) supplies nothing, whatever its rating; an
    hour without a schedule value is not capped by it (``capped_mw``)."""
    from services.adequacy.energization import capped_mw

    ts = n.links_t.p_max_pu
    pu = (ts[link].reindex(n.snapshots).to_numpy(dtype=float) if link in ts.columns
          else np.full(len(n.snapshots), float(n.links.at[link, "p_max_pu"])))
    own = float(n.links.at[link, "p_nom"]) * np.clip(np.nan_to_num(pu, nan=1.0), 0.0, None)
    return capped_mw(n, link, own)


def peak_check(n, island: dict | None) -> list[dict]:
    """One row per period, evaluated hour by hour (gatekeeper review of #435):
    cap(t) = Σ_l E_l(t) × efficiency_l + firm gensets + the batteries' discharge(t),
    headroom = min_t(cap(t) − load(t)),
    reported at the binding snapshot (the hour of least headroom):
    ``{period, binding_snapshot, load_mw, energized_mw, efficiency, delivered_mw,
    cap_mw, headroom_mw, peak_mw, energized_max_mw, firm: [{source, mw, rule}],
    firm_mw, import_links, scheduled}``. ``load_mw``/``cap_mw``/``energized_mw``
    are at the binding hour; ``peak_mw`` is the period's highest load and
    ``energized_max_mw`` its highest energized import. An empty list when the
    network has no import Link (an island-only site has no connection to check)."""
    from services.adequacy.energization import energized_links

    links = import_links(n)
    if not links:
        return []
    load = _fixed_load(n).to_numpy(dtype=float)
    firm_mw, rule = firm_genset_mw(n, island)
    scheduled = sorted(set(links) & set(energized_links(n)))
    eff = {l: float(n.links.at[l, "efficiency"]) for l in links}
    energized = {l: _energized(n, l) for l in links}
    total_e = sum(energized.values())
    delivered = sum(energized[l] * eff[l] for l in links)
    cap0 = delivered + firm_mw
    rt = ride_through_batteries(n, island)
    hours = _snapshot_hours(n)
    snaps = list(n.snapshots)
    out = []
    for label, mask in _periods(n):
        idx = np.flatnonzero(mask)
        if not idx.size:
            continue
        shave = np.zeros(idx.size)
        if rt["names"]:
            reserve, words = outage_reserve_mwh(n, island, mask, delivered)
            free = max(0.0, rt["e_mwh"] - reserve)
            shave = _shave(load[idx] - cap0[idx], cap0[idx] - load[idx], rt, reserve, hours)
            bess_rule = (f"{rt['p_mw']:g} MW / {rt['e_mwh']:.4g} MWh, {free:.4g} MWh above the "
                         f"{words}; shaved hour by hour from full")
        else:
            bess_rule = rt["reason"]
        cap = cap0[idx] + shave
        j = int(np.argmin(cap - load[idx]))
        t = idx[j]
        e_t = float(total_e[t])
        firm = [{"source": "gensets", "mw": firm_mw, "rule": rule},
                {"source": "ride-through batteries", "mw": float(shave[j]), "rule": bess_rule}]
        out.append({"period": label, "binding_snapshot": str(snaps[t]), "load_mw": float(load[t]),
                    "energized_mw": e_t,
                    "efficiency": float(delivered[t]) / e_t if e_t > 0 else min(eff.values()),
                    "delivered_mw": float(delivered[t]), "cap_mw": float(cap[j]),
                    "headroom_mw": float(cap[j] - load[t]), "peak_mw": float(load[idx].max()),
                    "energized_max_mw": float(total_e[idx].max()), "firm": firm,
                    "firm_mw": firm_mw + float(shave[j]), "import_links": list(links),
                    "scheduled": bool(scheduled)})
    return out


def describe(row: dict) -> str:
    """The cap in words, as the 422 and the scorecard both say it."""
    eff = row["efficiency"]
    shown = f" (× {eff:.4g} efficiency)" if abs(eff - 1.0) > 1e-12 else ""
    # A source that adds nothing beyond the first is left out of the words.
    sources = [f for i, f in enumerate(row["firm"]) if i == 0 or f["mw"] > 0]
    firm = " plus ".join(f"{f['mw']:g} MW of firm {f['source']} ({f['rule']})" for f in sources)
    return (f"the {row['energized_mw']:g} MW energized import{shown} plus {firm} "
            f"= {row['cap_mw']:.4g} MW")
