"""
The site's peak load against what its connection and firm gensets supply
(DC grid readiness 01 and 02, gatekeeper review of 02).

One check, two callers: a data-center project created with a ``dc_load`` body
is refused (422) when a period's peak does not fit (``eh_templates._check_dc_peak``),
and the grid-readiness scorecard reports the same rows (``grid_readiness``).

Per period: peak ≤ Σ energized import × loss factor + firm gensets, where

* the peak is the hourly-mean sum of the fixed loads' ``p_set`` (a flexible
  conversion unit such as an electrolyser is a decision and is not counted;
  the in-hour peak of an AI-training load is a few per cent higher, which the
  scorecard reports separately);
* the energized import is min(p_nom × the Link's highest p_max_pu in the
  period, the energization schedule's minimum in it) per import Link
  (``services.adequacy.energization``); a normally open tie supplies 0;
* the loss factor is each import Link's ``efficiency`` (what reaches the site
  side of the PoC Link; losses further in are not counted);
* firm gensets are the island sidecar's committed genset fleet less its k
  largest units when the requirements ask for genset N+k
  (``genset_redundancy_n``); without a sidecar no genset counts as firm.

``firm`` is a list of contributions so a later source of firm MW (battery
ride-through, DC 03) is one more entry rather than a second check.
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


def _energized(n, link: str, mask: np.ndarray) -> float:
    from services.adequacy.energization import energization_mw

    # The Link's own p_max_pu bounds it too: a normally open tie (p_max_pu 0)
    # supplies nothing, whatever its rating.
    ts = n.links_t.p_max_pu
    pu = (ts[link].reindex(n.snapshots).to_numpy(dtype=float)[mask].max()
          if link in ts.columns and mask.any() else float(n.links.at[link, "p_max_pu"]))
    p_nom = float(n.links.at[link, "p_nom"]) * max(0.0, float(pu))
    e = energization_mw(n, link)
    if e is None:
        return p_nom
    e = e[mask]
    e = e[np.isfinite(e)]
    return min(p_nom, float(e.min())) if e.size else p_nom


def peak_check(n, island: dict | None) -> list[dict]:
    """One row per period: ``{period, peak_mw, energized_mw, loss_factor,
    delivered_mw, firm: [{source, mw, rule}], firm_mw, cap_mw, headroom_mw,
    import_links, scheduled}``. An empty list when the network has no import
    Link (an island-only site has no connection to check)."""
    from services.adequacy.energization import energized_links

    links = import_links(n)
    if not links:
        return []
    load = _fixed_load(n).to_numpy(dtype=float)
    firm_mw, rule = firm_genset_mw(n, island)
    firm = [{"source": "gensets", "mw": firm_mw, "rule": rule}]
    scheduled = sorted(set(links) & set(energized_links(n)))
    out = []
    eff = {l: float(n.links.at[l, "efficiency"]) for l in links}
    for label, mask in _periods(n):
        energized = {l: _energized(n, l, mask) for l in links}
        delivered = sum(energized[l] * eff[l] for l in links)
        total_e = sum(energized.values())
        loss = delivered / total_e if total_e > 0 else min(eff.values())
        cap = delivered + sum(f["mw"] for f in firm)
        peak = float(load[mask].max()) if mask.any() else 0.0
        out.append({"period": label, "peak_mw": peak, "energized_mw": total_e,
                    "loss_factor": loss, "delivered_mw": delivered, "firm": firm,
                    "firm_mw": sum(f["mw"] for f in firm), "cap_mw": cap,
                    "headroom_mw": cap - peak, "import_links": list(links),
                    "scheduled": bool(scheduled)})
    return out


def describe(row: dict) -> str:
    """The cap in words, as the 422 and the scorecard both say it."""
    loss = row["loss_factor"]
    losses = f" (× {loss:.4g} losses)" if abs(loss - 1.0) > 1e-12 else ""
    firm = " plus ".join(f"{f['mw']:g} MW of firm {f['source']} ({f['rule']})" for f in row["firm"])
    return (f"the {row['energized_mw']:g} MW energized import{losses} plus {firm} "
            f"= {row['cap_mw']:.4g} MW")
