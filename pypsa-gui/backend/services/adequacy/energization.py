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


# ── One period's MW (template-e2e 02b) ─────────────────────────────────────
#
# Guided section 3 edits the schedule as one MW value per investment period.
# A period is written flat across its snapshots, and only a period that is
# already flat: a shaped one (an uploaded ramp, say) is refused with its range
# named, never scaled. The frame is edited in place: no column is created, no
# row is added, and its index and dtype stay as they are.

#: The key of the one period of a single-period axis (`energized_mw_by_period`).
ALL = "all"
#: The refusals of an MW value (the UX review's plain sentences, label "Connection limit (MW)").
NOT_A_NUMBER = "Connection limit: enter a number."
NOT_ABOVE_ZERO = "Connection limit: must be above 0 MW."


class EnergizationEditError(ValueError):
    """A per-period edit refused before anything was written: ``error_kind``
    is the typed kind, the message says why in plain words."""

    def __init__(self, error_kind: str, message: str):
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message


def _period_masks(index) -> list[tuple[str, np.ndarray]]:
    """``[(period key, row mask)]`` over the frame's own index: each level-0
    period of a MultiIndex, or the one ``"all"`` of a single axis."""
    if isinstance(index, pd.MultiIndex):
        level = index.get_level_values(0)
        return [(str(p), np.asarray(level == p)) for p in pd.unique(level)]
    return [(ALL, np.ones(len(index), dtype=bool))]


def by_period(n, link: str) -> dict | None:
    """The schedule per period of the frame's own index, as stored:
    ``{"by_period": {period: MW | {"min", "max"} | None}}``. A flat period
    reads its one value; one that varies inside it (or has hours without a
    value) reads its ``{"min", "max"}`` (the frontend's read-only "varies"
    row); a period with no value at all reads None. None when the link has no
    schedule. The one reading the flow row, the run's freeze and its
    admission take."""
    store = energization_frame(n)
    if store is None or link not in store.columns:
        return None
    values = pd.to_numeric(store[link], errors="coerce").to_numpy(dtype=float)
    out: dict[str, float | dict | None] = {}
    for key, mask in _period_masks(store.index):
        v = values[mask]
        finite = v[np.isfinite(v)]
        if not finite.size:
            out[key] = None
        elif finite.size == v.size and finite.min() == finite.max():
            out[key] = float(finite[0])
        else:
            out[key] = {"min": float(finite.min()), "max": float(finite.max())}
    return {"by_period": out}


def schedule_digest(n) -> str | None:
    """A digest of the whole schedule frame (its links, index and every MW
    value), or None when no link carries a value: the investment case's key
    part (template-e2e 02b W2), so a case is never keyed without it."""
    import hashlib

    store = energization_frame(n)
    if store is None or not store.notna().any().any():
        return None
    h = hashlib.sha256(repr([str(c) for c in store.columns]).encode())
    h.update(pd.util.hash_pandas_object(store, index=True).to_numpy().tobytes())
    return h.hexdigest()[:32]


def all_by_period(n) -> dict[str, dict]:
    """``{link: by_period}`` for every scheduled Link ({} without one)."""
    return {link: by_period(n, link) for link in energized_links(n)}


def _period_mask(store: pd.DataFrame, period) -> tuple[np.ndarray, str]:
    """The rows of ``period`` in the frame's own index, and its key. A
    multi-period axis takes a level-0 value (an int, or its digits); a single
    axis takes only ``"all"`` or None."""
    index = store.index
    if isinstance(index, pd.MultiIndex):
        masks = dict(_period_masks(index))
        if isinstance(period, bool) or not isinstance(period, (int, str)) or str(period) not in masks:
            raise EnergizationEditError(
                "energization_unknown_period",
                f"{period!r} is not one of the schedule's periods ({', '.join(masks)}).")
        return masks[str(period)], str(period)
    if period is not None and period != ALL:
        raise EnergizationEditError(
            "energization_unknown_period",
            f"This network has one period, so the schedule is set for all of it "
            f"(period \"all\"), not for {period!r}.")
    return np.ones(len(index), dtype=bool), ALL


def plan_period_mw(n, link: str, period, mw) -> dict:
    """Check one period's edit without writing: EnergizationEditError, or what
    `set_period_mw` writes (``mask``, ``period``, ``mw``, ``previous_mw``,
    ``p_nom_mw``, ``note``)."""
    # bool first: True is an int, and would read as 1 MW.
    if isinstance(mw, bool) or not isinstance(mw, (int, float, np.integer, np.floating)):
        raise EnergizationEditError("energization_invalid_mw", NOT_A_NUMBER)
    value = float(mw)
    if not np.isfinite(value):
        raise EnergizationEditError("energization_invalid_mw", NOT_A_NUMBER)
    if value <= 0:
        raise EnergizationEditError("energization_invalid_mw", NOT_ABOVE_ZERO)
    store = energization_frame(n)
    if store is None or link not in store.columns or link not in n.links.index:
        # Never created here: a schedule is the template's, or an upload's.
        raise EnergizationEditError(
            "energization_no_schedule", f"The link {link!r} has no energization schedule to edit.")
    if store[link].dtype.kind != "f":
        raise EnergizationEditError(
            "energization_no_schedule",
            f"The energization schedule of {link!r} is not stored as MW numbers.")
    mask, key = _period_mask(store, period)
    current = store[link].to_numpy(dtype=float)[mask]
    finite = current[np.isfinite(current)]
    if finite.size and (finite.size != current.size or finite.min() != finite.max()):
        # Never scaled: the range is named and the network is where it is set.
        raise EnergizationEditError(
            "energization_period_not_flat",
            f"{'The period' if key == ALL else key} varies {finite.min():g}–{finite.max():g} MW: "
            "set it on the network.")
    p_nom = float(n.links.at[link, "p_nom"])
    note = None
    if value > p_nom:
        # The per-period value is the energized (study) limit; p_nom is the link's rating.
        note = f"This is above the link's rating ({p_nom:g} MW): it doesn't raise the link's capacity."
    return {"mask": mask, "period": key, "mw": value,
            "previous_mw": float(finite[0]) if finite.size else None,
            "p_nom_mw": p_nom, "note": note}


def set_period_mw(n, link: str, period, mw) -> dict:
    """Set ``link``'s schedule to ``mw`` MW in one period, flat across that
    period's snapshots (``period`` a level-0 value of a multi-period axis;
    ``"all"`` or None on a single one). Refused with EnergizationEditError
    and the frame untouched. The caller holds the network lock. Returns
    ``{link, period, mw, previous_mw, p_nom_mw, note, series}``; ``note``
    says when ``mw`` is above the link's ``p_nom``, which the schedule does
    not raise; ``series`` is the period's rows as written."""
    plan = plan_period_mw(n, link, period, mw)
    store = energization_frame(n)
    store.loc[plan["mask"], link] = plan["mw"]
    return {"link": link, **{k: plan[k] for k in ("period", "mw", "previous_mw", "p_nom_mw", "note")},
            "series": store.loc[plan["mask"], link].copy()}
