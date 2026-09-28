"""
Settlement inputs committed with a solve (Edge Investment Case P2 WP2.2-0).

Part a — the DSR dispatch record. The per-bus DSR slack generators are
transient (`services/solver/assumptions.py` adds them and captures their
dispatch in the undo, which also runs on failed and sweep solves). The
captured frame (columns = bus names) is committed here as
`buses_t["ic_dsr_p"]` with `meta["ic_dsr"] = {axis_hash, total_mwh}` ONLY
after a successful, non-operational solve (the P1 `_ic_operational` rule: an
adequacy sweep solve is not the design), and cleared after a successful solve
without DSR. The custom `buses_t` frame rides network.nc. A DR contract's
activation reads it through `dsr_activation`; a missing record or one made on
another snapshot axis is `None` + `dr_activation_not_established` (ADR-0001).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib

import pandas as pd

DSR_ATTR = "ic_dsr_p"
META_DSR = "ic_dsr"
OPERATIONAL_ATTR = "_ic_operational"  # = connection.OPERATIONAL_ATTR (not imported: no cycle)


def axis_hash(snapshots: pd.Index) -> str:
    raw = "|".join(map(str, snapshots.tolist())).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def commit_dsr(n, dsr_t: pd.DataFrame | None, total_mwh: float | None = None) -> None:
    """Record (or clear) the DSR dispatch after a SUCCESSFUL solve. The caller
    runs it only on success; an operational solve leaves the record alone."""
    if getattr(n, OPERATIONAL_ATTR, False):
        return
    if dsr_t is None or dsr_t.empty:
        if DSR_ATTR in n.buses_t:
            n.buses_t[DSR_ATTR] = pd.DataFrame(index=n.snapshots)
        n.meta.pop(META_DSR, None)
        return
    frame = dsr_t.reindex(n.snapshots).astype(float)
    frame.columns = [str(c) for c in frame.columns]
    n.buses_t[DSR_ATTR] = frame
    n.meta[META_DSR] = {"axis_hash": axis_hash(n.snapshots),
                        "total_mwh": None if total_mwh is None else float(total_mwh),
                        "buses": list(frame.columns)}


def dsr_activation(n) -> tuple[pd.DataFrame | None, list[str]]:
    """(the committed DSR dispatch, MW per snapshot per bus; flags)."""
    rec = (n.meta or {}).get(META_DSR) if hasattr(n, "meta") else None
    frame = n.buses_t.get(DSR_ATTR) if hasattr(n.buses_t, "get") else None
    if not rec or frame is None or frame.empty or rec.get("axis_hash") != axis_hash(n.snapshots):
        return None, ["dr_activation_not_established"]
    cols = [c for c in (rec.get("buses") or frame.columns) if c in frame.columns]
    out = frame[cols].reindex(n.snapshots)
    if out.isna().any().any():
        return None, ["dr_activation_not_established"]
    return out, []
