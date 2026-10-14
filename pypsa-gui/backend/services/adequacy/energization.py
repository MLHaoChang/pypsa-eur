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
p_max_pu(t), E(t) / p_nom). A rewritten `p_nom` then composes as
min(E, cap). A firm-capacity reading (the MC certification's import credit)
takes the horizon minimum, `firm_energized_mw`, and says so.
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
    """E(t) in MW on the current snapshots, or None (no schedule, or a gap)."""
    store = energization_frame(n)
    if store is None or link not in store.columns:
        return None
    col = store[link].reindex(n.snapshots)
    if col.isna().any():
        return None
    return col.to_numpy(dtype=float)


def firm_energized_mw(n, link: str) -> float | None:
    """The horizon minimum of the schedule (the MW firm in every period)."""
    e = energization_mw(n, link)
    return None if e is None or not len(e) else float(np.min(e))


def _base_pu(n, link: str) -> np.ndarray:
    ts = n.links_t.p_max_pu
    if link in ts.columns:
        return ts[link].reindex(n.snapshots).to_numpy(dtype=float)
    return np.full(len(n.snapshots), float(n.links.at[link, "p_max_pu"]))


def apply_energization(n):
    """Write p_max_pu = min(own p_max_pu, E/p_nom) on every scheduled Link,
    for one solve. Returns the undo (idempotent). A Link whose p_nom is not
    positive, or whose schedule has a gap, is left alone."""
    saved: dict[str, pd.Series | None] = {}
    for link in energized_links(n):
        e = energization_mw(n, link)
        p_nom = float(n.links.at[link, "p_nom"])
        if e is None or not p_nom > 0:
            continue
        ts = n.links_t.p_max_pu
        saved[link] = ts[link].copy() if link in ts.columns else None
        limit = np.minimum(_base_pu(n, link), np.clip(e, 0.0, None) / p_nom)
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
