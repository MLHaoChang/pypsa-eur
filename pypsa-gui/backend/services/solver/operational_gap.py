"""The plan-vs-operation gap (DC 07, slice S3).

The operational run (``services/solver/operational.py``) dispatches the plan's
frozen sizes window by window, with no look-ahead past each window. The
comparison is a **full-horizon dispatch on the same sizes**: one solve over
every snapshot, with perfect foresight over the horizon. ``run_windows`` runs
it inside ``non_cyclic``, before window 1. That gives the same sizes, the same
configured initial state, the same cyclic flags (off), the same optimize kwargs
and the same ``extra_functionality`` (C12). Its outputs are then overwritten
by the windows. The ``n.meta`` it leaves is restored (C22), and it never
seeds a window: window i > 0 starts from the state solved at the snapshot
before it, which window i − 1 has already rewritten (C23).

:func:`metrics` is the one measure on both sides. It is called right after
each side's own solve, while the transient rows still exist (C24). The record
is ``n.meta[GAP_KEY]``. It is written for a solved run and for a run with a
failed window (windows counted, C14), and cleared at every solve start (C25).
:func:`read` returns the gap with each metric's definition, or None with a
flag when the record is missing, stale or not established (C12, C26).
"""

from __future__ import annotations

import copy
import hashlib

import pandas as pd

#: ``n.meta`` key of the last operational run's gap record.
GAP_KEY = "operational_gap"
VERSION = 1
LABEL = "full-horizon dispatch on the same sizes"

FRESH = "fresh"
MISSING = "missing"
STALE = "stale"
WINDOW_FAILED = "operational_window_failed"
REFERENCE_NOT_SOLVED = "reference_not_solved"

OPEX_BASIS = (
    "PyPSA statistics.opex (marginal + storage costs, curtailment and VOLL slack "
    "included as marginal cost) x objective snapshot weightings over the full "
    "horizon, on each side's own dispatch; LP-only objective terms excluded; "
    "n.objective is never read (after an operational run it is the last "
    "window's objective)"
)
#: The objective terms the LP carries that are not PyPSA opex (C24).
EXCLUDED_TERMS = (
    "capital costs (the sizes are fixed and equal on both sides)",
    "the objective constant",
    "LP-only penalty and cost rows added by extra_functionality "
    "(commercial bindings, connection fees, reserve and sizing-cut terms)",
)
CLAIM_LIMIT = (
    "The gap is what the lack of look-ahead past each window costs on these sizes, "
    "against a solve that sees the whole horizon at once. It is not a "
    "forecast-error measure: both sides see the same prices and loads."
)

DEFINITIONS = {
    "opex_eur": (
        "EUR. Sum over components of PyPSA statistics.opex (marginal and storage "
        "costs) x objective snapshot weightings, over the full horizon."
    ),
    "unserved_energy_mwh": (
        "MWh. Sum over snapshots of the dispatch of the involuntary slack "
        "generators (unserved energy) x generators snapshot weighting. Curtailment "
        "is not in it."
    ),
    "curtailed_energy_mwh": (
        "MWh. Sum over snapshots of the dispatch of the data-centre curtailment "
        "generators (carrier dc_curtailment) x generators snapshot weighting. It "
        "is reported apart from unserved energy and never summed with it."
    ),
    "storage_discharge_mwh": (
        "MWh. Sum over snapshots of StorageUnit p > 0 x stores snapshot weighting, "
        "plus Store p > 0 (a Store's p is its withdrawal) x stores snapshot weighting."
    ),
    "storage_end_mwh": (
        "MWh. The StorageUnit state of charge plus the Store energy at the last "
        "snapshot. Both sides are non-cyclic, so this shows how far each side "
        "drains its storage by the end of the horizon."
    ),
}
GAP_DEFINITION = (
    "gap = operational - reference; gap_pct = 100 x gap / |reference|. Both are None "
    "when either side is None, and gap_pct is None when the reference is 0."
)
WINDOWS_DEFINITION = (
    "total: the windows the horizon is cut into; solved: the windows that solved "
    "ok; failed: the windows that did not (the run stops at the first one); "
    "failed_index: the 0-based index of the failed window, or None."
)


def _weighted(frame: pd.DataFrame | None, columns, weights: pd.Series) -> float | None:
    """Sum of ``frame[columns]`` x ``weights``. None when no column exists, so
    a missing column never reads as 0 (C26)."""
    if frame is None or not len(columns):
        return None
    cols = [c for c in columns if c in frame.columns]
    if not cols:
        return None
    return float(frame[cols].mul(weights, axis=0).sum().sum())


def _value(value: float | None, reason: str) -> dict:
    return {"value": value} if value is not None else {"value": None, "reason": reason}


def metrics(n) -> dict[str, dict]:
    """Each metric of ``n``'s current dispatch: ``{"value": float}``, or
    ``{"value": None, "reason": ...}`` when the network has no such column."""
    from services.adequacy.slack import DC_CURTAILMENT_CARRIER, involuntary_slack_mask
    from services.solver.operational import stitched_opex

    gw = n.snapshot_weightings.generators
    sw = n.snapshot_weightings.stores
    gens = n.generators
    gp = n.generators_t.p
    ens = gens.index[involuntary_slack_mask(gens)] if len(gens) else []
    curt = (
        gens.index[gens["carrier"].astype(str) == DC_CURTAILMENT_CARRIER]
        if len(gens) and "carrier" in gens.columns
        else []
    )

    su, st = n.storage_units, n.stores
    su_p = n.storage_units_t.p
    st_p = n.stores_t.p
    discharge = [
        _weighted(su_p.clip(lower=0) if len(su) else None, su.index, sw),
        _weighted(st_p.clip(lower=0) if len(st) else None, st.index, sw),
    ]
    soc = n.storage_units_t.state_of_charge
    e = n.stores_t.e
    end = [
        float(soc[[c for c in su.index if c in soc.columns]].iloc[-1].sum())
        if len(su) and len(soc) and any(c in soc.columns for c in su.index)
        else None,
        float(e[[c for c in st.index if c in e.columns]].iloc[-1].sum())
        if len(st) and len(e) and any(c in e.columns for c in st.index)
        else None,
    ]

    def _total(parts):
        known = [p for p in parts if p is not None]
        return sum(known) if known else None

    return {
        "opex_eur": _value(stitched_opex(n), "no dispatch"),
        "unserved_energy_mwh": _value(
            _weighted(gp, ens, gw), "no involuntary slack generator in the network"
        ),
        "curtailed_energy_mwh": _value(
            _weighted(gp, curt, gw), "curtailment is off (no dc_curtailment generator)"
        ),
        "storage_discharge_mwh": _value(
            _total(discharge), "no StorageUnit or Store in the network"
        ),
        "storage_end_mwh": _value(
            _total(end), "no StorageUnit or Store in the network"
        ),
    }


def reference(n, **optimize_kwargs) -> dict:
    """The full-horizon dispatch on the same sizes: one ``n.optimize`` over
    every snapshot, its status and, when it solved, its :func:`metrics`. The
    ``n.meta`` it leaves is restored (C22). The outputs stay until the windows
    overwrite them."""
    saved = copy.deepcopy(dict(n.meta)) if isinstance(n.meta, dict) else None
    try:
        status, condition = n.optimize(n.snapshots, **optimize_kwargs)
        out = {"status": str(status), "condition": str(condition)}
        out["metrics"] = metrics(n) if status == "ok" else None
    finally:
        if saved is not None:
            n.meta.clear()
            n.meta.update(saved)
    return out


def weightings_digest(n) -> str:
    return hashlib.sha256(n.snapshot_weightings.to_json().encode()).hexdigest()


def horizon_of(cfg) -> tuple[int, int]:
    """``(H, O)`` as ``solver_service`` derives them from the config."""
    from services.solver.operational import _get

    h = max(1, int(_get(cfg, "rolling_horizon", 168)))
    o = max(0, int(_get(cfg, "rolling_overlap", 24)))
    return h, o


def record(
    n,
    *,
    horizon: int,
    overlap: int,
    total: int,
    windows: list[dict],
    ref: dict | None,
    failed: dict | None,
) -> dict:
    """The ``n.meta[GAP_KEY]`` record of an operational run: its key, the
    windows counted, the reference's status and metrics, and the operational
    metrics (None after a failed window)."""
    return {
        "version": VERSION,
        "key": {
            "horizon": horizon,
            "overlap": overlap,
            "weightings": weightings_digest(n),
            "reference_status": None if ref is None else ref["status"],
        },
        "windows": {
            "total": total,
            "solved": sum(1 for w in windows if w.get("status") == "ok"),
            "failed": 0 if failed is None else 1,
            "failed_index": None if failed is None else failed["index"],
        },
        "reference": None
        if ref is None
        else {"status": ref["status"], "condition": ref["condition"]},
        "reference_metrics": None if ref is None else ref.get("metrics"),
        "operational_metrics": None if failed is not None else metrics(n),
    }


def clear(n) -> None:
    """Drop the gap record at a solve start, whatever the strategy (C25)."""
    if isinstance(getattr(n, "meta", None), dict):
        n.meta.pop(GAP_KEY, None)


def _gap_rows(op: dict | None, ref: dict | None) -> dict:
    rows = {}
    for name, definition in DEFINITIONS.items():
        o = (op or {}).get(name) or {"value": None, "reason": "not established"}
        r = (ref or {}).get(name) or {"value": None, "reason": "not established"}
        ov, rv = o.get("value"), r.get("value")
        gap = None if ov is None or rv is None else ov - rv
        pct = None if gap is None or rv == 0 else 100.0 * gap / abs(rv)
        rows[name] = {
            "definition": definition,
            "operational": ov,
            "reference": rv,
            "gap": gap,
            "gap_pct": pct,
            **(
                {"operational_reason": o["reason"]}
                if ov is None and "reason" in o
                else {}
            ),
            **(
                {"reference_reason": r["reason"]}
                if rv is None and "reason" in r
                else {}
            ),
        }
    return rows


def _flag(n, cfg, rec: dict | None, live_fingerprint: str | None) -> str:
    from services.solver.operational import is_operational

    if rec is None or not is_operational(cfg):
        return MISSING
    h, o = horizon_of(cfg)
    key = rec.get("key") or {}
    solved = (
        n.meta.get("assistant_solve_input_fingerprint")
        if isinstance(n.meta, dict)
        else None
    )
    if (
        not solved
        or not live_fingerprint
        or solved != live_fingerprint
        or key.get("horizon") != h
        or key.get("overlap") != o
        or key.get("weightings") != weightings_digest(n)
    ):
        return STALE
    if (rec.get("windows") or {}).get("failed"):
        return WINDOW_FAILED
    if key.get("reference_status") != "ok" or rec.get("reference_metrics") is None:
        return REFERENCE_NOT_SOLVED
    return FRESH


def read(n, cfg, *, live_fingerprint: str | None) -> dict:
    """The gap of the last operational run. ``metrics`` is the table, or None
    with ``flag`` naming why: missing, stale, operational_window_failed or
    reference_not_solved. Each metric carries its definition (C14)."""
    rec = n.meta.get(GAP_KEY) if isinstance(getattr(n, "meta", None), dict) else None
    flag = _flag(n, cfg, rec, live_fingerprint)
    out = {
        "label": LABEL,
        "flag": flag,
        "opex_basis": OPEX_BASIS,
        "excluded_terms": list(EXCLUDED_TERMS),
        "claim_limit": CLAIM_LIMIT,
        "gap_definition": GAP_DEFINITION,
        "windows_definition": WINDOWS_DEFINITION,
        "definitions": dict(DEFINITIONS),
        "metrics": None,
        "windows": None,
        "reference": None,
    }
    if flag in (MISSING, STALE):
        return out
    out["windows"] = dict(rec["windows"])
    out["reference"] = rec.get("reference")
    out["horizon"] = rec["key"]["horizon"]
    out["overlap"] = rec["key"]["overlap"]
    if flag == FRESH:
        out["metrics"] = _gap_rows(
            rec.get("operational_metrics"), rec.get("reference_metrics")
        )
    return out
