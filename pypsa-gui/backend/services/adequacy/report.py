"""
Assemble the minimal AdequacyReport after a target-constrained solve.

Design: spec §§5.5, 7; plan Phase 1 Task 3. Everything here is arithmetic
over SOLVE-TIME truth handed in by the solver:

* ``targets`` — what `_wrap_with_ens_cap` actually enforced, stashed on the
  network at optimize time. Restore reverts the load-scaling transforms, so
  recomputing the demand denominator post-solve would drift from what the LP
  saw — the same two-readers-drift bug class the lost-load capture had.
* ``captured`` — the lost-load capture, whose weighted per-bus-per-period
  energies and electrical shed-hours are computed at capture time, before
  the slack generators are stripped.

Binding classification (spec §5.5): whichever standard actually shaped the
plan — a zone at its ceiling wins over the system cap, which wins over
"voll" (the cap was set but VoLL economics shed less than it allowed). The
user must be able to see which; two users with the same stated target
otherwise get different plans for unobservable reasons.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass

import pandas as pd

from models.adequacy import (
    AdequacyReport,
    CostBlock,
    EnergyBlock,
    InputsBlock,
    MetricsBlock,
    SystemTarget,
    TargetBlock,
    VollBlock,
    ZoneTarget,
)
from services.adequacy.metrics import electrical_columns

# A cap counts as binding when achieved shed reaches this share of it —
# LP tolerances keep the achieved value epsilon under the cap.
BINDING_TOLERANCE = 1e-3


def _config_hash(cfg) -> str:
    try:
        payload = asdict(cfg) if is_dataclass(cfg) else dict(vars(cfg))
        blob = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        blob = repr(cfg)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _outage_basis_counts(n) -> dict[str, int]:
    """How many assets entered occurrence data on each basis (asset-level
    rows only — carrier defaults are library values, not user inputs)."""
    from services.adequacy.occurrence import resolve_outage_params

    counts: dict[str, int] = {}
    for component in ("generators", "storage_units", "stores", "links", "lines"):
        try:
            params = resolve_outage_params(n, component)
        except Exception:
            continue
        asset_rows = params[params["source"] == "asset"]
        for basis in asset_rows["basis"]:
            b = str(basis)
            counts[b] = counts.get(b, 0) + 1
    return counts


def build_adequacy_report(n, cfg, targets: dict, captured: dict) -> dict:
    """
    Returns the report as a plain dict (``model_dump``) — the solver state is
    pickled, and a dict keeps the pickle free of model-class coupling.

    ``targets``: ``{"permyriad", "zone_multiple", "zone_of_bus": {bus: zone},
    "periods": {P: {"cap_mwh", "demand_mwh", "zones": {z: cap_mwh}}}}``.
    ``captured`` may be empty (target on, nothing shed).
    """
    periods: dict = targets.get("periods", {})
    zone_of_bus: dict = targets.get("zone_of_bus", {}) or {}

    # Achieved, from the capture's solve-time weighted energies.
    bp = captured.get("lost_load_bus_period_mwh")
    if bp is None or getattr(bp, "empty", True):
        bp = pd.DataFrame()
    elec_cols = electrical_columns(n, list(bp.columns)) if not bp.empty else []

    def _achieved(period, cols) -> float:
        if bp.empty or period not in bp.index:
            return 0.0
        cols = [c for c in cols if c in bp.columns]
        return float(bp.loc[period, cols].sum()) if cols else 0.0

    sys_cap_total = 0.0
    sys_achieved_total = 0.0
    binding_system = False
    zone_rows: dict[str, dict] = {}
    for P, tp in periods.items():
        cap_p = float(tp.get("cap_mwh", 0.0))
        ach_p = _achieved(P, elec_cols)
        sys_cap_total += cap_p
        sys_achieved_total += ach_p
        if cap_p > 0 and ach_p >= cap_p * (1.0 - BINDING_TOLERANCE):
            binding_system = True
        for z, z_cap in (tp.get("zones") or {}).items():
            z_cols = [c for c in elec_cols if zone_of_bus.get(c, "") == z]
            z_ach = _achieved(P, z_cols)
            row = zone_rows.setdefault(z, {"cap": 0.0, "ach": 0.0, "binding": False})
            row["cap"] += float(z_cap)
            row["ach"] += z_ach
            if z_cap > 0 and z_ach >= float(z_cap) * (1.0 - BINDING_TOLERANCE):
                row["binding"] = True

    binding = (
        "zone_cap" if any(r["binding"] for r in zone_rows.values())
        else "system_cap" if binding_system
        else "voll"
    )

    sh = captured.get("shed_hours_electrical") or {"total": 0.0}
    involuntary_mwh = float(captured.get("lost_load_total_mwh", 0.0) or 0.0)
    shed_cost = float(captured.get("lost_load_cost_eur", 0.0) or 0.0)

    try:
        objective = float(n.objective)
    except (TypeError, ValueError, AttributeError):
        objective = float("nan")
    total_system_cost = objective - shed_cost if math.isfinite(objective) else 0.0

    # Zone field population — computed from the network, not from targets:
    # it must be reportable even when no zone ceiling was requested.
    zone_field_populated = False
    buses = getattr(n, "buses", None)
    loads = getattr(n, "loads", None)
    if buses is not None and "country" in getattr(buses, "columns", []) and loads is not None:
        load_buses = set(map(str, loads["bus"])) if "bus" in loads.columns else set()
        for b in buses.index:
            if str(b) in load_buses and str(buses.at[b, "country"] or "").strip():
                zone_field_populated = True
                break

    multi = bool(getattr(cfg, "multi_investment_periods", False))
    report = AdequacyReport(
        engine="lp_proxy",
        fidelity="deterministic_scenario",
        target=TargetBlock(
            basis="energy",
            system=SystemTarget(
                cap_mwh=sys_cap_total,
                achieved_ens_mwh=sys_achieved_total,
                achieved_shed_hours=float(sh.get("total", 0.0)),
            ),
            zones=[
                ZoneTarget(zone=z, cap_mwh=r["cap"], achieved_ens_mwh=r["ach"],
                           binding=r["binding"])
                for z, r in sorted(zone_rows.items())
            ],
            binding=binding,
            zone_field_populated=zone_field_populated,
        ),
        metrics=MetricsBlock(
            ens_mwh=sys_achieved_total,
            shed_hours=float(sh.get("total", 0.0)),
            time_basis="hours_per_year",
        ),
        cost=CostBlock(
            total_system_cost_eur=total_system_cost,
            period_basis="npv_multi_period" if multi else "single_period",
        ),
        inputs=InputsBlock(
            weather_years=["modelled"],
            voll=VollBlock(default_eur_per_mwh=float(getattr(cfg, "voll", 0.0) or 0.0)),
            assumptions_hash=_config_hash(cfg),
            outage_rate_bases=_outage_basis_counts(n),
        ),
        energy=EnergyBlock(
            involuntary_mwh=involuntary_mwh,
            demand_response_mwh=float(captured.get("dsr_total_mwh", 0.0) or 0.0),
        ),
    )
    return report.model_dump()
