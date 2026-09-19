"""
DtC stress mode — fixed-plan islanding re-dispatch (Phase 4a).

Spec decision 8; plan Phase 4a. Critical unmet is reported at **bus**
aggregate (one VOLL slack per bus). Per-load shed attribution is refused.
"""
from __future__ import annotations

import copy
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from models.energy_hub import DtcConfig

logger = logging.getLogger("pypsa_gui.dtc")

HONESTY_NOTES: tuple[str, ...] = (
    "no_per_load_attribution",
    "stress_on_fixed_plan",
    "islanding_is_planning_contingency",
)

PLANNING_HONESTY_NOTES: tuple[str, ...] = (
    "no_per_load_attribution",
    "retained_critical_demand",
    "islanding_is_planning_contingency",
    "system_ens_not_per_load",
)

class DtcPlanningError(ValueError):
    pass


class DtcConfigError(ValueError):
    pass


class DtcStressError(ValueError):
    pass


def load_dtc_config(path: str | Path) -> DtcConfig:
    raw = json.loads(Path(path).read_text())
    try:
        return DtcConfig.model_validate(raw)
    except Exception as exc:
        raise DtcConfigError(str(exc)) from exc


def apply_islanding_contingency(
    n, link_id: str,
) -> tuple[Callable[[], None], dict[str, Any]]:
    """Zero import Link availability (Class-B p_*_pu); return undo + mutation.

    Undo restores both static and time-series ``p_max_pu`` / ``p_min_pu``
    (assessor P4a-B1). Prefer calling on a copy when the caller will discard
    the network anyway.
    """
    if n.links is None or link_id not in n.links.index:
        raise DtcStressError(f"islanding contingency Link {link_id!r} not found")
    snap_static = n.links.copy(deep=True)
    snap_t: dict[str, Any] = {}
    if "p_max_pu" not in n.links.columns:
        n.links["p_max_pu"] = 1.0
    if "p_min_pu" not in n.links.columns:
        n.links["p_min_pu"] = 0.0
    n.links.at[link_id, "p_max_pu"] = 0.0
    n.links.at[link_id, "p_min_pu"] = 0.0
    for attr in ("p_max_pu", "p_min_pu"):
        ts = getattr(getattr(n, "links_t", None), attr, None)
        if ts is not None and link_id in getattr(ts, "columns", []):
            snap_t[attr] = ts[link_id].copy()
            ts[link_id] = 0.0

    def undo() -> None:
        n.links = snap_static
        for attr, series in snap_t.items():
            ts = getattr(getattr(n, "links_t", None), attr, None)
            if ts is not None and link_id in getattr(ts, "columns", []):
                ts[link_id] = series

    return undo, {
        "action": "island_import",
        "link": str(link_id),
        "method": "p_max_pu_p_min_pu_zero",
        "restored_time_series": sorted(snap_t.keys()),
    }


def _critical_buses(n, dtc: DtcConfig) -> set[str]:
    buses = {str(b) for b in dtc.critical_bus_ids}
    if dtc.critical_load_ids and n.loads is not None and not n.loads.empty:
        for lid in dtc.critical_load_ids:
            if lid in n.loads.index and "bus" in n.loads.columns:
                buses.add(str(n.loads.at[lid, "bus"]))
    if n.buses is not None and not n.buses.empty and "eh_critical" in n.buses.columns:
        for b in n.buses.index:
            val = n.buses.at[b, "eh_critical"]
            if val is True or str(val).lower() in ("true", "1", "yes"):
                buses.add(str(b))
    return buses


def _bus_unserved_mwh(
    n, bus_ids: set[str], *, sink: dict | None = None,
) -> float:
    """Sum unserved energy on buses (MWh).

    Prefer ``last_lost_load`` from the solver sink (VOLL gens are cleaned up
    after optimize). Fall back to residual ``__voll_*`` dispatch if present.
    """
    if not bus_ids:
        return 0.0
    if sink:
        lost = sink.get("last_lost_load")
        if isinstance(lost, dict):
            by_bus = lost.get("lost_load_bus_period_mwh")
            if by_bus is not None:
                try:
                    # DataFrame columns = buses; rows = periods — sum matching buses.
                    cols = [c for c in by_bus.columns if str(c) in bus_ids]
                    if cols:
                        return float(by_bus[cols].to_numpy().sum())
                except Exception:
                    pass
            # Time series fallback: columns = buses.
            lost_t = lost.get("lost_load_t")
            if lost_t is not None:
                try:
                    weights = (
                        n.snapshot_weightings["objective"]
                        if "objective" in n.snapshot_weightings.columns
                        else n.snapshot_weightings.iloc[:, 0]
                    )
                    cols = [c for c in lost_t.columns if str(c) in bus_ids]
                    if cols:
                        return float((lost_t[cols].mul(weights, axis=0)).to_numpy().sum())
                except Exception:
                    pass
    if n.generators is None or n.generators.empty:
        return 0.0
    if getattr(n, "generators_t", None) is None or n.generators_t.p is None:
        return 0.0
    weights = (
        n.snapshot_weightings["objective"]
        if "objective" in n.snapshot_weightings.columns
        else n.snapshot_weightings.iloc[:, 0]
    )
    total = 0.0
    for bus in bus_ids:
        cand = [f"__voll_{bus}"]
        cand = [c for c in cand if c in n.generators_t.p.columns]
        if not cand:
            cand = [
                str(g) for g in n.generators.index
                if str(n.generators.at[g, "bus"]) == bus
                and (
                    str(g).startswith("__voll")
                    or str(n.generators.at[g, "carrier"]) in (
                        "voll", "load_shedding", "load_shedding_voll")
                )
            ]
        for g in cand:
            if g not in n.generators_t.p.columns:
                continue
            series = n.generators_t.p[g].fillna(0.0)
            total += float((series * weights).sum())
    return total


def _all_load_buses(n) -> set[str]:
    if n.loads is None or n.loads.empty or "bus" not in n.loads.columns:
        return set()
    return {str(b) for b in n.loads["bus"].tolist()}


def _detach_solver_model(network) -> None:
    model = getattr(network, "model", None)
    if model is not None and getattr(model, "solver_model", None) is not None:
        model.solver_model = None


def run_dtc_stress(
    network,
    cfg,
    *,
    lock,
    stop_event: threading.Event,
    log_queue: queue.Queue | None = None,
    dtc: DtcConfig,
    store: dict | None = None,
    pack_hash: str | None = None,
    assumptions_hash: str | None = None,
) -> dict[str, Any]:
    """Island each contingency and re-dispatch; report critical vs other unserved."""
    from services.solver_service import SolverConfig, run_simulation

    if not isinstance(dtc, DtcConfig):
        dtc = DtcConfig.model_validate(dtc)
    log_queue = log_queue or queue.SimpleQueue()
    critical = _critical_buses(network, dtc)
    if not critical:
        raise DtcStressError("no critical buses resolved from DtC config")
    noncritical = _all_load_buses(network) - critical

    contingencies: list[dict[str, Any]] = []
    solves = 0
    aborted = False
    for link_id in dtc.islanding_contingencies:
        if stop_event.is_set():
            aborted = True
            break
        _detach_solver_model(network)
        nn = network.copy()
        undo, mutation = apply_islanding_contingency(nn, str(link_id))
        sink: dict = {}
        try:
            cfg_i = copy.copy(cfg) if cfg is not None else SolverConfig()
            if float(getattr(cfg_i, "voll", 0.0) or 0.0) <= 0:
                cfg_i.voll = 500.0
            try:
                cfg_i.ens_cap_permyriad = None
            except Exception:
                pass
            solves += 1
            status, condition = run_simulation(
                cfg_i, nn, lock, stop_event, log_queue,
                state_update=lambda **kw: sink.update(kw),
            )
            contingencies.append({
                "contingency": str(link_id),
                "status": status,
                "condition": condition,
                "critical_unserved_mwh": _bus_unserved_mwh(
                    nn, critical, sink=sink),
                "noncritical_unserved_mwh": _bus_unserved_mwh(
                    nn, noncritical, sink=sink),
                "critical_buses": sorted(critical),
                "noncritical_buses": sorted(noncritical),
                "applied": mutation,
                "effective_voll": float(cfg_i.voll),
            })
        finally:
            try:
                undo()
            except Exception:
                logger.exception("DtC islanding undo failed for %s", link_id)
            _detach_solver_model(nn)

    solved = [c for c in contingencies if c.get("status") in ("ok", "optimal")]
    out = {
        "mode": "stress_fixed_plan",
        "attribution": "bus_aggregate_not_per_load",
        "honesty_notes": list(HONESTY_NOTES),
        "pack_hash": pack_hash,
        "assumptions_hash": assumptions_hash,
        "contingencies": contingencies,
        "solves_attempted": solves,
        "aborted": aborted,
        "comparable_solved": len(solved),
        "critical_buses": sorted(critical),
    }
    if store is not None:
        store["eh_dtc_stress"] = out
    return out




def apply_retained_critical_demand(
    n, dtc: DtcConfig,
) -> tuple[Callable[[], None], dict[str, Any]]:
    """Zero non-critical-bus load ``p_set``; keep critical buses intact.

    Spec §10: retained critical demand for P4b planning. Critical and
    non-critical loads must sit on different buses (P4a honesty boundary).
    """
    if n.loads is None or n.loads.empty:
        raise DtcPlanningError("no loads to apply retained-critical overlay")
    crit = _critical_buses(n, dtc)
    if not crit:
        raise DtcPlanningError("no critical buses resolved for retained demand")
    snap = n.loads.copy(deep=True)
    zeroed: list[str] = []
    for lid in list(n.loads.index):
        bus = str(n.loads.at[lid, "bus"]) if "bus" in n.loads.columns else ""
        if bus and bus not in crit:
            n.loads.at[lid, "p_set"] = 0.0
            zeroed.append(str(lid))
    # Also clear dynamic p_set for zeroed loads when present.
    ts_snap = None
    if getattr(n, "loads_t", None) is not None and getattr(n.loads_t, "p_set", None) is not None:
        ts = n.loads_t.p_set
        cols = [c for c in zeroed if c in ts.columns]
        if cols:
            ts_snap = ts[cols].copy()
            ts[cols] = 0.0

    def undo() -> None:
        n.loads = snap
        if ts_snap is not None:
            for c in ts_snap.columns:
                n.loads_t.p_set[c] = ts_snap[c]

    return undo, {
        "action": "retain_critical_demand",
        "critical_buses": sorted(crit),
        "zeroed_load_ids": zeroed,
    }


def run_dtc_planning(
    network,
    cfg,
    *,
    lock,
    stop_event: threading.Event,
    log_queue: queue.Queue | None = None,
    dtc: DtcConfig,
    store: dict | None = None,
    pack_hash: str | None = None,
    assumptions_hash: str | None = None,
) -> dict[str, Any]:
    """ENS-capped expansion under islanded + retained-critical overlay (P4b)."""
    from services.solver_service import SolverConfig, run_simulation

    if dtc.attribution != "bus_aggregate_not_per_load":
        raise DtcPlanningError("planning refuses per-load attribution")
    ens_cap = getattr(cfg, "ens_cap_permyriad", None) if cfg is not None else None
    try:
        ens_cap_f = float(ens_cap) if ens_cap is not None else None
    except (TypeError, ValueError):
        ens_cap_f = None
    if ens_cap_f is None or ens_cap_f <= 0:
        raise DtcPlanningError(
            "dtc planning requires ens_cap_permyriad > 0 "
            "(ENS-capped expansion; refuse uncapped VoLL-only sizing)"
        )
    log_queue = log_queue or queue.SimpleQueue()
    contingencies: list[dict[str, Any]] = []
    solves_attempted = 0
    aborted = False
    crit = sorted(_critical_buses(network, dtc))

    for link_id in dtc.islanding_contingencies:
        if stop_event.is_set():
            aborted = True
            break
        _detach_solver_model(network)
        nn = network.copy()
        _detach_solver_model(nn)
        try:
            undo_island, island_mut = apply_islanding_contingency(nn, str(link_id))
            undo_ret, ret_mut = apply_retained_critical_demand(nn, dtc)
        except Exception as exc:
            contingencies.append({
                "contingency": str(link_id),
                "status": "error",
                "condition": str(exc),
                "cost_at_target_eur": None,
                "built_p_nom_mw": None,
            })
            continue
        sink: dict = {}
        try:
            cfg_i = copy.copy(cfg) if cfg is not None else cfg
            status, condition = run_simulation(
                cfg_i, nn, lock, stop_event, log_queue,
                state_update=lambda **kw: sink.update(kw),
            )
            solves_attempted += 1
            cost = None
            built = None
            ens_mwh = None
            ar = sink.get("adequacy_report")
            if isinstance(ar, dict):
                cost = (ar.get("cost") or {}).get("total_system_cost_eur")
                system = (ar.get("target") or {}).get("system") or {}
                ens_mwh = system.get("achieved_ens_mwh")
            # Built capacity: extendable gens that gained p_nom_opt > p_nom.
            try:
                if nn.generators is not None and not nn.generators.empty:
                    built = 0.0
                    for g in nn.generators.index:
                        row = nn.generators.loc[g]
                        if "p_nom_extendable" in nn.generators.columns and not bool(row.get("p_nom_extendable", False)):
                            continue
                        p0 = float(row["p_nom"]) if "p_nom" in nn.generators.columns else 0.0
                        p1 = float(row["p_nom_opt"]) if "p_nom_opt" in nn.generators.columns else p0
                        if p1 > p0 + 1e-6:
                            built += p1 - p0
            except Exception:
                built = None
            contingencies.append({
                "contingency": str(link_id),
                "status": status,
                "condition": condition,
                "cost_at_target_eur": cost,
                "achieved_ens_mwh": ens_mwh,
                "built_p_nom_mw": built,
                "applied_island": island_mut,
                "retained_critical": ret_mut,
                "retained_critical_buses": crit,
            })
        finally:
            try:
                undo_ret()
            except Exception:
                logger.exception("retained-critical undo failed")
            try:
                undo_island()
            except Exception:
                logger.exception("island undo failed")
            _detach_solver_model(nn)

    out = {
        "mode": "planning",
        "attribution": "bus_aggregate_not_per_load",
        "honesty_notes": list(PLANNING_HONESTY_NOTES),
        "pack_hash": pack_hash,
        "assumptions_hash": assumptions_hash,
        "contingencies": contingencies,
        "solves_attempted": solves_attempted,
        "aborted": aborted,
    }
    if store is not None:
        store["eh_dtc_planning"] = out
    return out


def dtc_planning_section_status(table: dict[str, Any]) -> tuple[str, str | None]:
    if table.get("aborted"):
        return "not_established", "DtC planning aborted mid-loop"
    solved = [
        c for c in (table.get("contingencies") or [])
        if c.get("status") in ("ok", "optimal")
    ]
    if len(solved) < 1:
        return "not_established", "no DtC planning contingency solved"
    return "ok", None


def dtc_section_status(table: dict[str, Any]) -> tuple[str, str | None]:
    if table.get("aborted"):
        return "not_established", "DtC stress aborted mid-loop"
    solved = [
        c for c in (table.get("contingencies") or [])
        if c.get("status") in ("ok", "optimal")
    ]
    if len(solved) < 1:
        return "not_established", "no DtC contingency solved"
    return "ok", None
