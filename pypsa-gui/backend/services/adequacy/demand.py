"""
One demand basis for every adequacy engine (Phase 12c-0; the fifteenth
finding).

``load_scalers`` / ``load_scalers_by_carrier`` are applied to
``loads_t.p_set`` transiently inside ``_apply_modelling_assumptions`` — before
the LP, reverted after — so the LP, the ENS cap and the reserve-margin
CONSTRAINT see scaled demand while every engine that reads the restored
network (``snapshot_inputs``, ``fleet_and_residual``, the route-side
``reserve_margin_facts``, both certifying loops) saw the raw series. On a
project with a 1.25 growth factor the coupling loop certified a plan built for
125 % of demand against 100 % of it.

This module owns the ONE factor resolution. ``_apply_modelling_assumptions``
applies it in place for the LP; ``lp_demand_frame`` applies it to a copy for
everything else; ``lp_scaled_load_frame`` (the Results/Compare tabs) takes
its fallback from here too. Three consumers, one rule — the rule is the LP's,
verbatim (plan 12c v3.1 A1):

* gated on ``cfg.multi_investment_periods``, a MultiIndex snapshot axis and a
  non-empty ``loads_t.p_set`` — exactly the LP's gate;
* ``loads_t.p_set`` COLUMNS only. A static ``loads.p_set`` is never scaled by
  the LP and is never scaled here; scaling it would manufacture the very
  mismatch this module removes (v3 review, finding 1);
* per-carrier first, legacy global second, identity third; non-finite or
  unparseable factors are identity; the carrier fallback is ``"unspecified"``.
"""
from __future__ import annotations

import math

import pandas as pd


def load_scale_factors(n, cfg) -> list[tuple]:
    """``[(period, column, carrier_key, factor), ...]`` for every
    (period, load column) whose resolved factor is not 1.0 — the LP's
    resolution, nothing else. Empty when the LP would scale nothing."""
    if cfg is None:
        return []
    by_carrier_cfg = getattr(cfg, "load_scalers_by_carrier", {}) or {}
    load_scalers = getattr(cfg, "load_scalers", {}) or {}
    loads_t = getattr(n, "loads_t", None)
    p_set = getattr(loads_t, "p_set", None) if loads_t is not None else None
    if not (
        getattr(cfg, "multi_investment_periods", False)
        and (load_scalers or by_carrier_cfg)
        and isinstance(getattr(n, "snapshots", None), pd.MultiIndex)
        and p_set is not None and not p_set.empty
    ):
        return []
    from services.solver_service import _canonical_load_carrier_key

    loads = getattr(n, "loads", None)
    carrier_by_col: dict[str, str] = {}
    if loads is not None and "carrier" in loads.columns:
        for col in p_set.columns:
            if col in loads.index:
                carrier_by_col[col] = _canonical_load_carrier_key(loads.at[col, "carrier"])
            else:
                carrier_by_col[col] = "unspecified"
    else:
        for col in p_set.columns:
            carrier_by_col[col] = "unspecified"

    def _finite(raw):
        try:
            f = float(raw)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    out: list[tuple] = []
    period_level = p_set.index.get_level_values(0)
    for period in sorted(set(period_level)):
        for col in p_set.columns:
            carrier_key = carrier_by_col.get(col, "unspecified")
            factor = None
            car_block = by_carrier_cfg.get(carrier_key)
            if isinstance(car_block, dict):
                raw = car_block.get(str(period))
                if raw is not None:
                    factor = _finite(raw)
            if factor is None and load_scalers:
                raw = load_scalers.get(str(period))
                if raw is not None:
                    factor = _finite(raw)
            if factor is None or factor == 1.0:
                continue
            out.append((period, col, carrier_key, factor))
    return out


def lp_demand_frame(n, cfg):
    """``loads_t.p_set`` as the LP sees it. Returns the frame ITSELF (no
    copy) when nothing is scaled, so the no-scaler path is bit-identical to
    reading the frame directly; a scaled copy otherwise. ``None`` when the
    network has no ``loads_t``.

    The returned frame MAY BE THE LIVE MODEL INPUT — read it, never mutate
    it (12c-0 shipped-code review, finding 2: every consumer today is
    read-only; a future in-place op would corrupt ``loads_t.p_set``)."""
    loads_t = getattr(n, "loads_t", None)
    p_set = getattr(loads_t, "p_set", None) if loads_t is not None else None
    if p_set is None:
        return None
    factors = load_scale_factors(n, cfg)
    if not factors:
        return p_set
    df = p_set.copy(deep=True)
    period_level = df.index.get_level_values(0)
    masks = {period: period_level == period for period, *_ in factors}
    for period, col, _carrier, factor in factors:
        df.loc[masks[period], col] = df.loc[masks[period], col] * factor
    return df


def demand_frame_for(n, cfg, *, demand_scaled_in_place: bool):
    """The frame an engine reads: the raw ``loads_t.p_set`` when the caller
    says the LP's transforms are already applied in place (the solve
    wrapper), else the LP basis via ``lp_demand_frame``. The switch is
    explicit because ``_apply_modelling_assumptions`` scales the frame the
    engines read, and applying the factors twice is not idempotent
    (measured: ×1.25² — v3 review, finding 6)."""
    if demand_scaled_in_place:
        loads_t = getattr(n, "loads_t", None)
        return getattr(loads_t, "p_set", None) if loads_t is not None else None
    return lp_demand_frame(n, cfg)
