"""
The site's peak load against what its connection and firm gensets supply
(DC grid readiness 01 and 02, gatekeeper review of 02).

One check, two callers: a data-center project created with a ``dc_load`` body
is refused (422) when a period's peak does not fit (``eh_templates._check_dc_peak``),
and the grid-readiness scorecard reports the same rows (``grid_readiness``).

Hour by hour: load(t) ≤ Σ energized import(t) × efficiency + firm gensets, and
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
  (``genset_redundancy_n``); without a sidecar no genset counts as firm.

``firm`` is a list of contributions so a later source of firm MW (battery
ride-through, DC 03) is one more entry rather than a second check.

A period whose load or energization is a placeholder (an example built for
entered planning years it has no figure for, E1: ``unset_periods``) is
unknown: its ``headroom_mw`` is None and ``unset`` names the inputs, so
neither the 422 nor the scorecard reads the placeholder as a number.
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


#: The growth inputs the check rests on (``eh_template.json`` ``growth_inputs`` kinds).
PEAK_INPUTS = ("it_mw", "energized_mw")


def unset_periods(meta: dict | None) -> dict[str, list[str]]:
    """``{period: [kind, ...]}`` of the growth inputs the template metadata
    marks not set (``growth_inputs`` rows with ``set`` false); {} when it
    has none (every value is the example's own)."""
    out: dict[str, list[str]] = {}
    for g in (meta or {}).get("growth_inputs") or []:
        if isinstance(g, dict) and g.get("set") is False and g.get("kind") in PEAK_INPUTS:
            out.setdefault(str(g.get("year")), []).append(str(g["kind"]))
    return out


def peak_check(n, island: dict | None, *, unset: dict[str, list[str]] | None = None) -> list[dict]:
    """One row per period, evaluated hour by hour (gatekeeper review of #435):
    cap(t) = Σ_l E_l(t) × efficiency_l + firm, headroom = min_t(cap(t) − load(t)),
    reported at the binding snapshot (the hour of least headroom):
    ``{period, binding_snapshot, load_mw, energized_mw, efficiency, delivered_mw,
    cap_mw, headroom_mw, peak_mw, energized_max_mw, firm: [{source, mw, rule}],
    firm_mw, import_links, scheduled, unset}``. A period in ``unset``
    (``unset_periods``) has ``headroom_mw`` None: unknown. ``load_mw``/``cap_mw``/``energized_mw``
    are at the binding hour; ``peak_mw`` is the period's highest load and
    ``energized_max_mw`` its highest energized import. An empty list when the
    network has no import Link (an island-only site has no connection to check)."""
    from services.adequacy.energization import energized_links

    links = import_links(n)
    if not links:
        return []
    load = _fixed_load(n).to_numpy(dtype=float)
    firm_mw, rule = firm_genset_mw(n, island)
    firm = [{"source": "gensets", "mw": firm_mw, "rule": rule}]
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
        missing = list((unset or {}).get(label, []))
        out.append({"period": label, "binding_snapshot": str(snaps[t]), "load_mw": float(load[t]),
                    "energized_mw": e_t,
                    "efficiency": float(delivered[t]) / e_t if e_t > 0 else min(eff.values()),
                    "delivered_mw": float(delivered[t]), "cap_mw": float(cap[t]),
                    "headroom_mw": None if missing else float(cap[t] - load[t]),
                    "peak_mw": float(load[idx].max()),
                    "energized_max_mw": float(total_e[idx].max()), "firm": firm,
                    "firm_mw": firm_total, "import_links": list(links),
                    "scheduled": bool(scheduled), "unset": missing})
    return out


def describe(row: dict) -> str:
    """The cap in words, as the 422 and the scorecard both say it."""
    eff = row["efficiency"]
    shown = f" (× {eff:.4g} efficiency)" if abs(eff - 1.0) > 1e-12 else ""
    firm = " plus ".join(f"{f['mw']:g} MW of firm {f['source']} ({f['rule']})" for f in row["firm"])
    return (f"the {row['energized_mw']:g} MW energized import{shown} plus {firm} "
            f"= {row['cap_mw']:.4g} MW")
