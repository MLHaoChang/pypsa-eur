"""
A connection energized in steps, in absolute MW (E2E demo plan 2026-10-14,
slice 8; gatekeeper review of #266).

A utility often energizes a connection in stages: 20 MW of a 40 MW import
until a substation is reinforced, say. As a per-unit `p_max_pu` on the Link
that schedule is a fraction of `p_nom`, and every consumer that rewrites
`p_nom` (the EH `import_cap` lever, the pack's `import_p_nom_mw` overlay, a
connection agreement's firm cap) silently rescales it: a 30 MW lever rung read
15 MW in the 20 MW year.

So the schedule is stored in MW, `links_t["eh_energization_mw"]`, and turned
into `p_max_pu` only for a solve (`apply_energization`, called by
`run_simulation` and undone after it): p_max_pu(t) = min(the Link's own
p_max_pu(t), E(t) / p_nom). A rewritten fixed `p_nom` then composes as
min(E, cap). The MC's sampled import unit composes hour by hour in MW
(`capped_mw`); the two-state boundary credit takes the horizon minimum and
discloses the minimum per period (`energized_mw_by_period`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ATTR = "eh_energization_mw"


def energization_frame(n) -> pd.DataFrame | None:
    """The MW schedule frame, or None when the network carries none."""
    links_t = getattr(n, "links_t", None)
    store = links_t.get(ATTR) if links_t is not None and hasattr(links_t, "get") else None
    if store is None or getattr(store, "empty", True):
        return None
    return store


def energized_links(n) -> list[str]:
    store = energization_frame(n)
    if store is None:
        return []
    return [str(c) for c in store.columns if c in n.links.index]


def energization_mw(n, link: str) -> np.ndarray | None:
    """E(t) in MW on the current snapshots, or None (no schedule). An hour
    with no value (NaN: a period added after the schedule was written) has
    no limit in that hour only; the other hours keep theirs."""
    store = energization_frame(n)
    if store is None or link not in store.columns:
        return None
    return store[link].reindex(n.snapshots).to_numpy(dtype=float)


def capped_mw(n, link: str, mw: np.ndarray) -> np.ndarray:
    """min(mw, E) hour by hour; an hour without a schedule value is not
    capped. The one composition every reader uses (the solve hook through
    ``apply_energization``, the MC's sampled import unit)."""
    e = energization_mw(n, link)
    mw = np.asarray(mw, dtype=float)
    if e is None:
        return mw
    return np.where(np.isfinite(e), np.minimum(mw, np.clip(e, 0.0, None)), mw)


def firm_energized_mw(n, link: str) -> float | None:
    """The horizon minimum of the schedule (the MW firm in every period)."""
    e = energization_mw(n, link)
    if e is None or not np.isfinite(e).any():
        return None
    return float(np.nanmin(e))


def energized_mw_by_period(n, link: str) -> dict[str, float] | None:
    """{period: the schedule's minimum in it} (one entry, "all", on a
    single-period axis), or None without a schedule."""
    e = energization_mw(n, link)
    if e is None or not np.isfinite(e).any():
        return None
    if isinstance(n.snapshots, pd.MultiIndex):
        level = n.snapshots.get_level_values(0)
        return {str(p): float(np.nanmin(e[level == p])) for p in pd.unique(level)
                if np.isfinite(e[level == p]).any()}
    return {"all": float(np.nanmin(e))}


def _base_pu(n, link: str) -> np.ndarray:
    ts = n.links_t.p_max_pu
    if link in ts.columns:
        return ts[link].reindex(n.snapshots).to_numpy(dtype=float)
    return np.full(len(n.snapshots), float(n.links.at[link, "p_max_pu"]))


def apply_energization(n):
    """Write p_max_pu = min(own p_max_pu, E/p_nom) on every scheduled Link,
    for one solve. Returns the undo (idempotent). A Link whose p_nom is not
    positive is left alone; an hour without a schedule value keeps its own
    p_max_pu.

    E/p_nom uses the STATIC p_nom: it composes with a rewritten fixed p_nom
    (the import_cap lever, the pack overlay, a firm agreement without a fee).
    An extendable import Link (a firm agreement with a connection fee makes
    the PoC extendable) is capped at E/p_nom of its static p_nom, not of its
    solved size: not yet supported, see slice P5c."""
    saved: dict[str, pd.Series | None] = {}
    for link in energized_links(n):
        e = energization_mw(n, link)
        p_nom = float(n.links.at[link, "p_nom"])
        if e is None or not p_nom > 0:
            continue
        ts = n.links_t.p_max_pu
        saved[link] = ts[link].copy() if link in ts.columns else None
        base = _base_pu(n, link)
        limit = capped_mw(n, link, base * p_nom) / p_nom
        n.links_t.p_max_pu[link] = limit

    done = {"v": False}

    def undo() -> None:
        if done["v"]:
            return
        for link, prev in saved.items():
            if prev is None:
                if link in n.links_t.p_max_pu.columns:
                    n.links_t.p_max_pu = n.links_t.p_max_pu.drop(columns=[link])
            else:
                n.links_t.p_max_pu[link] = prev
        done["v"] = True

    return undo
