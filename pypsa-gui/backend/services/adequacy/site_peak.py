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
* firm ride-through batteries (DC 03) are the sidecar's committed
  ``ride_through_storage`` at what they hold through its ``sustained_h`` D:
  min(Σ p_nom, Σ efficiency_dispatch × p_nom × max_hours / D). A UPS bridges
  seconds and is not firm.

``firm`` is a list of contributions, one entry per source of firm MW.
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


def firm_battery_mw(n, island: dict | None) -> tuple[float, str]:
    """The committed ride-through batteries' firm MW and the rule that gave
    it: the MW they hold through the requirements' ``sustained_h``. The
    requirements' ``ride_through_storage`` names them (every bess when it is
    absent, as the schema fills it); an extendable unit counts 0, as an
    extendable genset does."""
    if not island:
        return 0.0, "no island sidecar: no battery is counted as firm"
    req = island.get("requirements") or {}
    units = island.get("units") or {}
    names = req.get("ride_through_storage")
    if names is None:
        names = [u for u, d in units.items() if d.get("kind") == "bess"]
    su = n.storage_units
    names = [u for u in names if u in su.index and not bool(su.at[u, "p_nom_extendable"])]
    p = sum(float(su.at[u, "p_nom"]) for u in names)
    if p <= 0:
        return 0.0, "no committed ride-through battery"
    d = _value(req.get("sustained_h"))
    if not d or float(d) <= 0:
        return 0.0, "no sustained ride-through time in the island requirements: no battery is counted as firm"
    d = float(d)
    e = sum(float(su.at[u, "efficiency_dispatch"]) * float(su.at[u, "p_nom"]) * float(su.at[u, "max_hours"])
            for u in names)
    return min(p, e / d), f"{p:g} MW / {e:.4g} MWh deliverable, held for the {d:g} h ride-through"


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
    cap(t) = Σ_l E_l(t) × efficiency_l + firm, headroom = min_t(cap(t) − load(t)),
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
    bess_mw, bess_rule = firm_battery_mw(n, island)
    firm = [{"source": "gensets", "mw": firm_mw, "rule": rule},
            {"source": "ride-through batteries", "mw": bess_mw, "rule": bess_rule}]
    firm_total = sum(f["mw"] for f in firm)
    scheduled = sorted(set(links) & set(energized_links(n)))
    eff = {l: float(n.links.at[l, "efficiency"]) for l in links}
    energized = {l: _energized(n, l) for l in links}
    total_e = sum(energized.values())
    delivered = sum(energized[l] * eff[l] for l in links)
    cap = delivered + firm_total
    snaps = list(n.snapshots)
    out = []
    for label, mask in _periods(n):
        idx = np.flatnonzero(mask)
        if not idx.size:
            continue
        t = idx[int(np.argmin(cap[idx] - load[idx]))]
        e_t = float(total_e[t])
        out.append({"period": label, "binding_snapshot": str(snaps[t]), "load_mw": float(load[t]),
                    "energized_mw": e_t,
                    "efficiency": float(delivered[t]) / e_t if e_t > 0 else min(eff.values()),
                    "delivered_mw": float(delivered[t]), "cap_mw": float(cap[t]),
                    "headroom_mw": float(cap[t] - load[t]), "peak_mw": float(load[idx].max()),
                    "energized_max_mw": float(total_e[idx].max()), "firm": firm,
                    "firm_mw": firm_total, "import_links": list(links),
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
