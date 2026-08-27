"""
Adequacy metrics over the lost-load capture.

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md
§§5.1, 6.3. The capture's per-snapshot frame (``lost_load_t``) is unweighted
MW — a power series. Everything TOTALLED from it must go through the
snapshot weights (``period_utils.snapshot_weights``: "generators" column for
energy quantities, "objective" for cost quantities), or representative-
snapshot runs (tsam) under-report by the weight factor — the "~5× too small"
bug class period_utils exists to prevent.
"""
from __future__ import annotations

import pandas as pd


def lost_load_totals(
    ll: pd.DataFrame,
    *,
    energy_weights: pd.Series,
    cost_weights: pd.Series,
    voll: float,
) -> dict[str, float]:
    """
    Weighted totals over an unweighted per-snapshot shed frame
    (snapshot × bus, MW).

    * ``total_mwh`` — Σ over buses of Σ_t shed[t] × energy_weights[t]
    * ``cost_eur``  — the same integral on ``cost_weights`` × ``voll``,
      so it matches what the LP objective actually charged.

    Negative values are LP numerical dust and are clipped to zero, matching
    the capture's historical behaviour. Missing weights reindex to 0 — a
    snapshot outside the weight index contributes nothing rather than
    poisoning the sum with NaN.
    """
    if ll is None or ll.empty:
        return {"total_mwh": 0.0, "cost_eur": 0.0}
    shed = ll.clip(lower=0.0)
    ew = energy_weights.reindex(ll.index).fillna(0.0)
    cw = cost_weights.reindex(ll.index).fillna(0.0)
    total_mwh = float(shed.mul(ew, axis=0).to_numpy().sum())
    cost_eur = float(shed.mul(cw, axis=0).to_numpy().sum()) * float(voll)
    return {"total_mwh": total_mwh, "cost_eur": cost_eur}


# The canonical shed-hours definition (spec §5.1). Deliberately an argument,
# not a buried constant: LP solutions carry ~1e-9 numerical dust that must
# not count as a loss-of-load hour, but callers with a stricter or looser
# notion of "shedding" (e.g. a regulator's de-minimis) get to say so.
DEFAULT_SHED_THRESHOLD_MW = 1e-3


def shed_hours(
    ll: pd.DataFrame,
    *,
    weights: pd.Series,
    threshold_mw: float = DEFAULT_SHED_THRESHOLD_MW,
) -> dict:
    """
    Weighted count of loss-of-load hours over an unweighted per-snapshot shed
    frame (snapshot × bus, MW): the sum of snapshot weights over snapshots
    where TOTAL shed power (summed across the frame's columns, negatives
    clipped as LP dust) exceeds ``threshold_mw``.

    This is the reported reliability number that stays informative under a
    binding energy cap (spec §5.1): achieved ENS ≈ the cap by construction,
    but the same MWh can land as one long event or many short ones —
    shed-hours tells them apart. Column scope is the CALLER's decision
    (``electrical_columns`` for the electricity-only target scope).

    Returns ``{"total": float, "by_period": {period: float}}`` — the period
    split mirrors the other capture consumers: MultiIndex level 0, or the
    single bucket ``"ALL"`` for a flat index.
    """
    if ll is None or ll.empty:
        return {"total": 0.0, "by_period": {}}
    total_shed = ll.clip(lower=0.0).sum(axis=1)
    w = weights.reindex(ll.index).fillna(0.0)
    counted = w.where(total_shed > threshold_mw, 0.0)
    if isinstance(ll.index, pd.MultiIndex):
        # Keep every period, zeros included — a period with no shed hours is
        # information, not absence.
        by_period = {
            int(p): float(v)
            for p, v in counted.groupby(ll.index.get_level_values(0)).sum().items()
        }
    else:
        by_period = {"ALL": float(counted.sum())}
    return {"total": float(counted.sum()), "by_period": by_period}


def electrical_columns(n, columns) -> list[str]:
    """
    The subset of ``columns`` (bus names) whose bus carrier classifies as
    electrical — the electricity-only target scope of spec §4.3. Uses the
    same canonical classifier as the load-scaling machinery
    (``_canonical_load_carrier_key``: AC aliases and blank → "electrical"),
    so the target and the load views can never disagree on what counts.

    A column with NO matching bus is KEPT: the metric must not silently drop
    shed energy it cannot classify (a renamed bus would otherwise vanish
    from the reliability number).
    """
    # Lazy import — solver_service is a 5,800-line module and pulls the whole
    # solver stack; metrics must stay importable in isolation.
    from services.solver_service import _canonical_load_carrier_key

    buses = getattr(n, "buses", None)
    if buses is None or "carrier" not in getattr(buses, "columns", []):
        return list(columns)
    out = []
    for col in columns:
        if col not in buses.index:
            out.append(col)
            continue
        if _canonical_load_carrier_key(buses.at[col, "carrier"]) == "electrical":
            out.append(col)
    return out
