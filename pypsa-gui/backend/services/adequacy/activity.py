"""
Activity and vintages — an asset's capacity as a function of the period
(Phase 12d; plan 2026-09-03-fmea-phase12d-engine-activity-v1.md §1).

Two recorded defects, one mechanism. The engines admitted every asset that
cleared the scope tests to EVERY hour of the horizon, never reading
``build_year`` / ``lifetime`` / ``active`` — while the LP and the reserve
margin mask by ``get_active_assets(P)``; and ``solved_capacity`` read a
vintage-expanded parent's ``p_nom_opt`` — the SUM over vintages the restore
writes — in every period, so a vintage the LP built for 2040 was scored in
2030. Both are the same statement: capacity is per period.

**The rule.** For a component row ``i`` and a period block ``P`` of the
snapshot axis (``period_blocks``: the investment periods in axis order, or
the single block ``"ALL"`` on a flat axis)::

    c_{i,P} = cap_i · [i active in P]                             (plain row)
    c_{i,P} = initial_i · [parent active in P]
            + Σ_v opt_v · [by_v ≤ P < by_v + lt_v] · [parent.active]   (vintage-expanded parent)

``cap_i = solved_capacity(row)``; ``[i active in P]`` is PyPSA's own mask,
``get_active_assets(P)`` for an integer label and ``get_active_assets()``
(the static ``active`` column only) for ``"ALL"`` — the SAME call the reserve
margin makes (its ``_active`` delegates here) and the one PyPSA's ``optimize``
uses to decide which variables exist. The vintage line reads the breakdown the
restore persists in ``n.meta["vintage_results"]`` (``initial_capacity``, and
per vintage ``build_year``, ``p_nom_opt``, ``lifetime``) and replicates
PyPSA's activity rule for rows that no longer exist; ``test_adequacy_activity``
E1 pins it against PyPSA on rows that do.

**The breakdown is used only when it is CONSISTENT with the row**:
``initial + Σ_v opt_v == p_nom_opt(parent)`` (rel 1e-9 / abs 1e-6). The restore
writes exactly that identity; a breakdown that no longer describes the row
falls back to the plain rule — silently, because the engines have no channel
for warnings, and the portfolio block's fingerprint catches the user-facing
case. The consistency test is the BACKSTOP: the first line is that
``apply_vintage_bounds`` clears its own entries at every solve start (v1
review, finding 6). A myopic strategy's ``source == "myopic_freeze"`` entry
(one period, ``p_nom_opt = delta``) IS a breakdown under this rule: the delta
exists from its period onward for the parent's lifetime and did not exist
before it, which is what the myopic LP saw (finding 3). ``lifetime`` absent
from an entry (freeze entries; breakdowns written before this phase) falls
back to the parent's finite positive lifetime, else ``inf`` — the two branches
of the rule that created the row (``vintage_service.apply_vintage_bounds``).

**What the engines carry.** ``capacity_series_h = c_{i,P(h)}`` in MW — an
``(H,)`` array, ``None`` when it is constant at ``capacity_mw = max_P c_{i,P}``
(the nameplate a firm block brackets, the capacity the loops hash). ``None``
is the scalar path, byte-for-byte: every single-period network without
``active=False``, every multi-period network whose assets are all active
everywhere, is untouched by construction. The series is in MW, not a
fraction, so the residual netting, the preserved must-take profiles and the
per-block COPT read ``c_P`` directly (finding 11); only the sampler's float32
column multiplies it by the availability profile.

Membership is NOT per period: a row is in the fleet when ``cap_max > 0`` (or
under ``keep_zero_capacity``), so the name set — the CRN contract's positional
substreams — survives a solve that changes what is active.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

_CLASS_OF_COMP = {"generators": "Generator", "storage_units": "StorageUnit"}
_REL = 1e-9
_ABS = 1e-6
#: a series is "constant" when every block is within this of the maximum
_CONST_TOL = 1e-12


def period_blocks(snapshots) -> tuple:
    """Contiguous blocks of the snapshot axis: ``((label, start, end), ...)``.

    On a MultiIndex horizon these are the investment periods in axis order —
    the boundaries at which chronology RESTARTS (MC spec §2.4 step 1).
    Contiguity is asserted, not assumed: a re-ordered axis would make "hour N
    of period P is followed by hour 0 of P+1" true again in the arrays while
    being false in the model, which is exactly the error the re-initialisation
    exists to prevent. (Moved here from ``mc._period_blocks`` in Phase 12d;
    ``mc`` re-exports it under that name.)
    """
    n_h = len(snapshots)
    if not isinstance(snapshots, pd.MultiIndex):
        return (("ALL", 0, n_h),)
    level = list(snapshots.get_level_values(0))
    blocks: list[tuple] = []
    start = 0
    for i in range(1, n_h + 1):
        if i == n_h or level[i] != level[start]:
            label = level[start]
            try:
                label = int(label)
            except (TypeError, ValueError):
                label = str(label)
            blocks.append((label, start, i))
            start = i
    labels = [b[0] for b in blocks]
    assert len(set(labels)) == len(labels), (
        f"snapshot periods are not contiguous: {labels}")
    return tuple(blocks)


def active_mask(n, comp: str, period) -> pd.Series:
    """PyPSA's activity mask for one component frame in one period: the
    static ``active`` column for a string label (``"ALL"``), and
    ``build_year ≤ P < build_year + lifetime`` AND ``active`` for an integer
    one. No exception is swallowed here: on a well-formed network the only
    failures are a label PyPSA does not know or a frame without an
    ``active`` column, and both are caller bugs (v1 review, finding 9). The
    reserve margin keeps its own all-True guard around this call."""
    c = getattr(n.components, comp)
    if isinstance(period, str):
        return c.get_active_assets()
    return c.get_active_assets(int(period))


def _finite(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _row_value(row, col) -> float | None:
    try:
        return _finite(row[col])
    except (KeyError, IndexError, TypeError):
        return None


def vintage_breakdown(n, comp: str, name: str, row) -> dict | None:
    """The CONSISTENT persisted breakdown for ``(comp, name)`` as
    ``{"initial": float, "vintages": [(build_year, p_nom_opt, lifetime), …]}``
    or None: none persisted, malformed, or ``initial + Σ opt`` does not equal
    the row's ``p_nom_opt``. ``lifetime`` falls back to the parent's finite
    positive lifetime, else ``inf`` (module docstring)."""
    meta = getattr(n, "meta", None)
    root = meta.get("vintage_results") if isinstance(meta, dict) else None
    if not isinstance(root, dict):
        return None
    by_asset = root.get(_CLASS_OF_COMP.get(comp, ""))
    entry = by_asset.get(str(name)) if isinstance(by_asset, dict) else None
    if not isinstance(entry, dict):
        return None
    initial = _finite(entry.get("initial_capacity"))
    if initial is None:
        return None
    parent_lt = _row_value(row, "lifetime")
    fallback_lt = parent_lt if (parent_lt is not None and parent_lt > 0) else math.inf
    vintages: list[tuple] = []
    for per in entry.get("periods") or []:
        if not isinstance(per, dict):
            return None
        try:
            by = int(per.get("build_year"))
        except (TypeError, ValueError):
            return None
        opt = _finite(per.get("p_nom_opt"))
        if opt is None:
            return None
        lt = _finite(per.get("lifetime"))
        if lt is None or lt <= 0:
            lt = fallback_lt
        vintages.append((by, max(opt, 0.0), lt))
    total = initial + sum(v[1] for v in vintages)
    p_nom_opt = _row_value(row, "p_nom_opt")
    if p_nom_opt is None or not math.isclose(total, p_nom_opt, rel_tol=_REL, abs_tol=_ABS):
        return None
    return {"initial": max(initial, 0.0), "vintages": vintages}


class ActivityContext:
    """The masks of one component frame over the period blocks, computed
    ONCE per network (one ``get_active_assets`` per period, not per row),
    and the per-row capacity rule applied against them."""

    def __init__(self, n, comp: str, blocks) -> None:
        self.n = n
        self.comp = comp
        self.blocks = tuple(blocks)
        self.labels = [b[0] for b in self.blocks]
        self.H = int(self.blocks[-1][2]) if self.blocks else 0
        self.masks = {P: active_mask(n, comp, P) for P in self.labels}
        df = getattr(n, comp, None)
        self._static = (df["active"] if df is not None and "active" in df.columns
                        else None)

    def is_active(self, name, P) -> bool:
        return bool(self.masks[P].get(name, False))

    def static_active(self, name) -> bool:
        if self._static is None:
            return True
        return bool(self._static.get(name, True))

    def capacity_by_period(self, name, row) -> list[float]:
        """§1's rule, one value per block label."""
        from services.adequacy.copt import solved_capacity

        base = float(solved_capacity(row))
        bd = vintage_breakdown(self.n, self.comp, name, row)
        out: list[float] = []
        for P in self.labels:
            act = self.is_active(name, P)
            if bd is None or not isinstance(P, int):
                out.append(base if act else 0.0)
                continue
            c = bd["initial"] if act else 0.0
            if self.static_active(name):
                for by, opt, lt in bd["vintages"]:
                    if by <= P < by + lt:
                        c += opt
            out.append(c)
        return out

    def capacity_series(self, name, row) -> tuple[float, np.ndarray | None]:
        """``(capacity_mw, capacity_series)``: the maximum over the blocks and
        the ``(H,)`` MW series, or ``None`` when the series is constant at
        the maximum (or the maximum is 0 — an unbuilt row)."""
        caps = self.capacity_by_period(name, row)
        cap_max = max(caps) if caps else 0.0
        if cap_max <= 0.0:
            return 0.0, None
        if all(abs(c - cap_max) <= _CONST_TOL * cap_max for c in caps):
            return cap_max, None
        series = np.empty(self.H, dtype=np.float64)
        for (_label, start, end), c in zip(self.blocks, caps):
            series[start:end] = c
        return cap_max, series


def block_capacity(cap_max: float, series, start: int, end: int) -> float:
    """The one capacity a unit or store has over the block ``[start, end)``
    — asserted constant, which the construction above guarantees and a
    hand-built series may violate."""
    if series is None:
        return float(cap_max)
    seg = np.asarray(series, dtype=np.float64)[start:end]
    if seg.size == 0:
        return 0.0
    lo, hi = float(seg.min()), float(seg.max())
    assert hi - lo <= _CONST_TOL * max(hi, 1.0), (
        f"capacity series is not constant over the block [{start}, {end}): "
        f"{lo} … {hi}")
    return float(seg[0])


def activity_summary(units, storage, blocks) -> dict:
    """The payload disclosure: per block label the names whose capacity in
    the block is 0 (``inactive``) or strictly between 0 and their nameplate
    (``partial`` — a later vintage not yet built), and one sentence, or
    ``None`` for the note when nothing is masked anywhere."""
    by_period: dict = {}
    parts: list[str] = []
    for label, start, end in blocks:
        inactive: list[str] = []
        partial: list[str] = []
        for kind, rows in (("unit", units), ("store", storage)):
            for r in rows:
                cs = getattr(r, "capacity_series", None)
                if cs is None:
                    continue
                cap_max = float(getattr(r, "capacity_mw", None)
                                if kind == "unit" else getattr(r, "p_nom_mw", 0.0))
                c = block_capacity(cap_max, cs, start, end)
                if c <= 0.0:
                    inactive.append(str(r.name))
                elif c < cap_max * (1.0 - _CONST_TOL):
                    partial.append(str(r.name))
        by_period[str(label)] = {"inactive": inactive, "partial": partial}
        if inactive or partial:
            bits = []
            if inactive:
                bits.append(f"{len(inactive)} inactive ({', '.join(inactive[:8])}"
                            f"{' …' if len(inactive) > 8 else ''})")
            if partial:
                bits.append(f"{len(partial)} below nameplate, a later vintage "
                            f"not yet built ({', '.join(partial[:8])}"
                            f"{' …' if len(partial) > 8 else ''})")
            parts.append(f"{label}: " + "; ".join(bits))
    note = None
    if parts:
        note = ("The engines mask assets by build year and lifetime, as the LP "
                "and the reserve margin do — " + ". ".join(parts) + ".")
    return {"by_period": by_period, "note": note}
