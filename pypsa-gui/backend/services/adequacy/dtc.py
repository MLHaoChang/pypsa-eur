"""
DtC stress mode — fixed-plan islanding re-dispatch (Phase 4a).

Spec decision 8; plan Phase 4a. By default critical unmet is reported at
**bus** aggregate (a critical Load promotes its whole bus). P16 (spec §10
amendment, decision Q5): ``attribution="per_load"`` reports it by **Load**,
made non-degenerate by a critical VOLL premium (``CRITICAL_VOLL_PREMIUM_EPS``)
scoped to the stress re-dispatch alone.
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

# P16 — opt-in per-Load attribution. Critical Loads' VOLL slacks bid
# VOLL × (1 + ε) in the stress re-dispatch, so the LP sheds non-critical
# Loads first instead of splitting a shared bus arbitrarily.
CRITICAL_VOLL_PREMIUM_EPS = 0.05

PER_LOAD_HONESTY_NOTES: tuple[str, ...] = (
    "per_load_by_voll_priority",
    "stress_on_fixed_plan",
    "islanding_is_planning_contingency",
)

PER_LOAD_PLANNING_HONESTY_NOTES: tuple[str, ...] = (
    "retained_critical_by_load",
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


def _critical_loads(n, dtc: DtcConfig) -> set[str]:
    """``per_load`` critical set: named Loads plus every Load on a critical
    bus (``critical_bus_ids`` / ``eh_critical``). A named Load does NOT
    promote its bus here — that is the whole point of per-Load attribution."""
    if n.loads is None or n.loads.empty:
        return set()
    loads = {str(i) for i in n.loads.index}
    out = {str(lid) for lid in dtc.critical_load_ids if str(lid) in loads}
    buses = {str(b) for b in dtc.critical_bus_ids}
    if n.buses is not None and "eh_critical" in n.buses.columns:
        for b in n.buses.index:
            val = n.buses.at[b, "eh_critical"]
            if val is True or str(val).lower() in ("true", "1", "yes"):
                buses.add(str(b))
    if "bus" in n.loads.columns:
        out |= {str(i) for i, b in n.loads["bus"].items() if str(b) in buses}
    return out


def _load_unserved_by_load(n, load_ids: set[str], *,
                           sink: dict | None) -> dict[str, float]:
    """Unserved MWh per Load from the Load-keyed P6(b) capture.

    Refuses (``DtcStressError``) when the capture carries no Load-keyed data:
    a bus roll-up cannot be split into Loads, and ``per_load`` never guesses.
    """
    lost = (sink or {}).get("last_lost_load")
    lost = lost if isinstance(lost, dict) else {}
    known = ({str(i) for i in n.loads.index}
             if n.loads is not None and not n.loads.empty else set())
    by_load = lost.get("lost_load_load_period_mwh")
    if by_load is not None and any(str(c) in known for c in by_load.columns):
        return {lid: (float(by_load[lid].to_numpy().sum())
                      if lid in by_load.columns else 0.0)
                for lid in sorted(load_ids)}
    lost_t = lost.get("lost_load_t")
    if lost_t is not None and any(str(c) in known for c in lost_t.columns):
        weights = _energy_weights(n).reindex(lost_t.index).fillna(0.0)
        return {lid: (float(lost_t[lid].clip(lower=0).mul(weights).sum())
                      if lid in lost_t.columns else 0.0)
                for lid in sorted(load_ids)}
    raise DtcStressError(
        "per_load attribution needs Load-keyed shed data "
        "(lost_load_load_period_mwh / Load-keyed lost_load_t); the capture "
        "has none — use attribution='bus_aggregate_not_per_load'")


def _load_unserved_mwh(n, load_ids: set[str], *, sink: dict | None) -> float:
    return float(sum(_load_unserved_by_load(n, load_ids, sink=sink).values()))


def _priority_caveats(n, cfg, *, exclude: set[str],
                      eps: float = CRITICAL_VOLL_PREMIUM_EPS,
                      ) -> tuple[bool, list[str], bool]:
    """(exact, lossy_links, line_losses) for the per-Load VOLL priority.

    Serving a critical Load through a path of efficiency η costs V/η of
    non-critical shed against (1+ε)·V of critical shed, so the priority is
    exact only where every electrical path is at least 1/(1+ε) efficient.
    Conservative: every electrical↔electrical Link below that threshold is
    named (the islanded contingencies excepted), and LP line losses make
    the claim inexact outright.
    """
    from services.adequacy.metrics import electrical_columns

    threshold = 1.0 / (1.0 + eps) - 1e-9
    lossy: list[str] = []
    links = getattr(n, "links", None)
    if links is not None and not links.empty:
        eff_t = getattr(getattr(n, "links_t", None), "efficiency", None)
        for lid in links.index:
            if str(lid) in exclude:
                continue
            ends = [str(links.at[lid, "bus0"]), str(links.at[lid, "bus1"])]
            if len(electrical_columns(n, ends)) < 2:
                continue
            eff = float(links.at[lid, "efficiency"]) \
                if "efficiency" in links.columns else 1.0
            if eff_t is not None and lid in getattr(eff_t, "columns", []):
                eff = min(eff, float(eff_t[lid].min()))
            if 0.0 < eff < threshold:
                lossy.append(str(lid))
    line_losses = bool(getattr(cfg, "transmission_losses", False))
    return (not lossy and not line_losses), sorted(lossy), line_losses


def _load_to_bus(n) -> dict[str, str]:
    if n.loads is None or n.loads.empty or "bus" not in n.loads.columns:
        return {}
    return {str(i): str(b) for i, b in n.loads["bus"].items()}


def _energy_weights(n):
    """Snapshot weights the P6(b) capture uses for MWh (``generators``)."""
    sw = n.snapshot_weightings
    for col in ("generators", "objective"):
        if col in sw.columns:
            return sw[col]
    return sw.iloc[:, 0]


def _bus_unserved_mwh(
    n, bus_ids: set[str], *, sink: dict | None = None,
) -> float:
    """Sum unserved energy on buses (MWh).

    Sources, most authoritative first (P6(b) keys the capture by **Load**):

    1. ``lost_load_bus_period_mwh`` — the capture's own bus roll-up. When it
       is present it is final, including 0 for a bus that shed nothing.
    2. ``lost_load_load_period_mwh`` rolled up by ``loads.bus``.
    3. ``lost_load_t`` (MW, Load-keyed; legacy bus-keyed columns accepted)
       weighted and rolled up the same way.
    4. Residual involuntary slack dispatch on the network, if any survived.
    """
    if not bus_ids:
        return 0.0
    load_bus = _load_to_bus(n)

    def _bus_of(col) -> str:
        return load_bus.get(str(col), str(col))

    if sink:
        lost = sink.get("last_lost_load")
        if isinstance(lost, dict):
            by_bus = lost.get("lost_load_bus_period_mwh")
            if by_bus is not None:
                try:
                    cols = [c for c in by_bus.columns if str(c) in bus_ids]
                    return float(by_bus[cols].to_numpy().sum()) if cols else 0.0
                except Exception:
                    logger.exception("DtC: unreadable lost_load_bus_period_mwh")
            by_load = lost.get("lost_load_load_period_mwh")
            if by_load is not None:
                try:
                    cols = [c for c in by_load.columns if _bus_of(c) in bus_ids]
                    return float(by_load[cols].to_numpy().sum()) if cols else 0.0
                except Exception:
                    logger.exception("DtC: unreadable lost_load_load_period_mwh")
            lost_t = lost.get("lost_load_t")
            if lost_t is not None:
                try:
                    weights = _energy_weights(n).reindex(lost_t.index).fillna(0.0)
                    cols = [c for c in lost_t.columns if _bus_of(c) in bus_ids]
                    if not cols:
                        return 0.0
                    return float(
                        lost_t[cols].clip(lower=0).mul(weights, axis=0)
                        .to_numpy().sum())
                except Exception:
                    logger.exception("DtC: unreadable lost_load_t")
    if n.generators is None or n.generators.empty:
        return 0.0
    if getattr(n, "generators_t", None) is None or n.generators_t.p is None:
        return 0.0
    weights = _energy_weights(n)
    from services.adequacy.slack import involuntary_slack_mask

    invol = involuntary_slack_mask(n.generators)
    total = 0.0
    for g in n.generators.index:
        if not bool(invol.at[g]) or str(n.generators.at[g, "bus"]) not in bus_ids:
            continue
        if g not in n.generators_t.p.columns:
            continue
        series = n.generators_t.p[g].fillna(0.0)
        total += float((series * weights).sum())
    return total


def _plan_is_nameplate(n) -> None:
    """On an unsolved (private) copy, make ``*_nom_opt`` the nameplate."""
    from services.adequacy.sweep import _CAPACITY_ATTRS

    for attr, nom in _CAPACITY_ATTRS:
        df = getattr(n, attr, None)
        opt = f"{nom}_opt"
        if df is None or df.empty or nom not in df.columns:
            continue
        df[opt] = df[nom].astype(float)


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
    max_solves: int | None = None,
) -> dict[str, Any]:
    """Island each contingency and re-dispatch; report critical vs other unserved.

    **Fixed plan** (spec decision 8): extendable capacity is frozen at its
    solved size (``*_nom_opt``) with the same ``sweep.freeze_capacities`` the
    Class-B sweep uses. On a network that has never been solved PyPSA holds
    ``*_nom_opt = 0``, which would stress a brownfield extendable as if it
    did not exist — there the plan IS the nameplate, so ``*_nom_opt`` is set
    to ``*_nom`` on the private copy before freezing. Islanding must not
    buy new capacity, or the stress reports the shortfall a re-plan would
    fix instead of the one the plan has. The ENS cap, zone multiple and
    reserve margin are stripped for the frozen re-dispatch, as in the sweep:
    a surviving margin on pinned capacity is infeasible, and a binding ENS
    cap would read as the target rather than as the islanding damage.

    ``max_solves`` caps the LPs attempted (EH study budget).
    """
    import dataclasses

    from services.adequacy.sweep import freeze_capacities
    from services.solver_service import SolverConfig, run_simulation

    from services.solver.assumptions import voll_load_premium

    if not isinstance(dtc, DtcConfig):
        dtc = DtcConfig.model_validate(dtc)
    log_queue = log_queue or queue.SimpleQueue()
    per_load = dtc.attribution == "per_load"
    if per_load:
        crit_loads = _critical_loads(network, dtc)
        if not crit_loads:
            raise DtcStressError("no critical Loads resolved from DtC config")
        noncrit_loads = {str(i) for i in network.loads.index} - crit_loads
        load_bus = _load_to_bus(network)
        critical = {load_bus.get(lid, "") for lid in crit_loads} - {""}
        noncritical = {load_bus.get(lid, "") for lid in noncrit_loads} - {""}
        premium = {lid: 1.0 + CRITICAL_VOLL_PREMIUM_EPS for lid in crit_loads}
    else:
        critical = _critical_buses(network, dtc)
        if not critical:
            raise DtcStressError("no critical buses resolved from DtC config")
        noncritical = _all_load_buses(network) - critical
        premium = {}

    contingencies: list[dict[str, Any]] = []
    solves = 0
    aborted = False
    budget_exhausted = False
    refused: str | None = None
    for link_id in dtc.islanding_contingencies:
        if stop_event.is_set():
            aborted = True
            break
        if max_solves is not None and solves >= max_solves:
            budget_exhausted = True
            break
        _detach_solver_model(network)
        nn = network.copy()
        undo, mutation = apply_islanding_contingency(nn, str(link_id))
        if not network.is_solved:
            _plan_is_nameplate(nn)
        unfreeze = freeze_capacities(nn)
        sink: dict = {}
        try:
            cfg_i = dataclasses.replace(
                cfg if cfg is not None else SolverConfig(),
                ens_cap_permyriad=None, ens_zone_cap_multiple=None,
                reserve_margin=None)
            if float(getattr(cfg_i, "voll", 0.0) or 0.0) <= 0:
                cfg_i.voll = 500.0
            solves += 1
            # The premium lives only for THIS re-dispatch (R5).
            with voll_load_premium(premium):
                status, condition = run_simulation(
                    cfg_i, nn, lock, stop_event, log_queue,
                    state_update=lambda **kw: sink.update(kw),
                )
            row: dict[str, Any] = {
                "contingency": str(link_id),
                "status": status,
                "condition": condition,
                "critical_buses": sorted(critical),
                "noncritical_buses": sorted(noncritical),
                "applied": mutation,
                "effective_voll": float(cfg_i.voll),
            }
            if per_load:
                solved_ok = status in ("ok", "optimal")
                try:
                    by_load = (_load_unserved_by_load(nn, crit_loads, sink=sink)
                               if solved_ok else None)
                except DtcStressError as exc:
                    # Keep the solve already spent and stop: the capture's
                    # shape does not change between contingencies.
                    refused = str(exc)
                    row.update({"status": "refused", "condition": refused,
                                "critical_unserved_mwh": None,
                                "noncritical_unserved_mwh": None})
                    contingencies.append(row)
                    break
                row.update({
                    "critical_unserved_mwh": (float(sum(by_load.values()))
                                              if by_load is not None else None),
                    "noncritical_unserved_mwh": (
                        _load_unserved_mwh(nn, noncrit_loads, sink=sink)
                        if solved_ok else None),
                    "critical_unserved_by_load": by_load,
                    "critical_loads": sorted(crit_loads),
                    "noncritical_loads": sorted(noncrit_loads),
                })
            else:
                row.update({
                    "critical_unserved_mwh": _bus_unserved_mwh(
                        nn, critical, sink=sink),
                    "noncritical_unserved_mwh": _bus_unserved_mwh(
                        nn, noncritical, sink=sink),
                })
            contingencies.append(row)
        finally:
            try:
                unfreeze()
                undo()
            except Exception:
                logger.exception("DtC islanding undo failed for %s", link_id)
            _detach_solver_model(nn)

    solved = [c for c in contingencies if c.get("status") in ("ok", "optimal")]
    out = {
        "mode": "stress_fixed_plan",
        "attribution": dtc.attribution,
        "honesty_notes": list(PER_LOAD_HONESTY_NOTES if per_load
                              else HONESTY_NOTES),
        "voll_premium_eps": CRITICAL_VOLL_PREMIUM_EPS if per_load else None,
        "refused": refused,
        "pack_hash": pack_hash,
        "assumptions_hash": assumptions_hash,
        "contingencies": contingencies,
        "solves_attempted": solves,
        "aborted": aborted,
        "budget_exhausted": budget_exhausted,
        "comparable_solved": len(solved),
        "critical_buses": sorted(critical),
    }
    if per_load:
        exact, lossy, line_losses = _priority_caveats(
            network, cfg, exclude={str(x) for x in dtc.islanding_contingencies})
        out["priority_exact"] = exact
        out["priority_caveat_links"] = lossy
        out["priority_caveat_line_losses"] = line_losses
        if not exact:
            out["honesty_notes"].append("priority_may_invert_on_lossy_paths")
    if store is not None:
        store["eh_dtc_stress"] = out
    return out




def apply_retained_critical_demand(
    n, dtc: DtcConfig,
) -> tuple[Callable[[], None], dict[str, Any]]:
    """Zero non-critical demand for the planning solve; undo restores.

    Spec §10: retained critical demand for P4b planning. Bus aggregate
    (default): every Load on a non-critical bus is zeroed. ``per_load``
    (P16): every non-critical LOAD is zeroed, including one that shares a
    bus with a critical Load.
    """
    if n.loads is None or n.loads.empty:
        raise DtcPlanningError("no loads to apply retained-critical overlay")
    per_load = dtc.attribution == "per_load"
    if per_load:
        keep = _critical_loads(n, dtc)
        if not keep:
            raise DtcPlanningError("no critical Loads resolved for retained demand")
        load_bus = _load_to_bus(n)
        crit = {load_bus.get(lid, "") for lid in keep} - {""}
    else:
        crit = _critical_buses(n, dtc)
        if not crit:
            raise DtcPlanningError("no critical buses resolved for retained demand")
        keep = {str(lid) for lid in n.loads.index
                if "bus" in n.loads.columns and str(n.loads.at[lid, "bus"]) in crit}
    snap = n.loads.copy(deep=True)
    zeroed: list[str] = []
    for lid in list(n.loads.index):
        if str(lid) not in keep:
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
        "attribution": dtc.attribution,
        "critical_buses": sorted(crit),
        "critical_loads": sorted(keep),
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
    max_solves: int | None = None,
) -> dict[str, Any]:
    """ENS-capped expansion under islanded + retained-critical overlay (P4b).

    ``max_solves`` caps the LPs attempted (EH study budget).
    """
    from services.solver_service import SolverConfig, run_simulation

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
    budget_exhausted = False

    for link_id in dtc.islanding_contingencies:
        if stop_event.is_set():
            aborted = True
            break
        if max_solves is not None and solves_attempted >= max_solves:
            budget_exhausted = True
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
                "retained_critical_buses": ret_mut["critical_buses"],
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
        "attribution": dtc.attribution,
        # No premium in planning: only critical demand remains in the solve.
        "honesty_notes": list(PER_LOAD_PLANNING_HONESTY_NOTES
                              if dtc.attribution == "per_load"
                              else PLANNING_HONESTY_NOTES),
        "pack_hash": pack_hash,
        "assumptions_hash": assumptions_hash,
        "contingencies": contingencies,
        "solves_attempted": solves_attempted,
        "aborted": aborted,
        "budget_exhausted": budget_exhausted,
    }
    if store is not None:
        store["eh_dtc_planning"] = out
    return out


def dtc_planning_section_status(table: dict[str, Any]) -> tuple[str, str | None]:
    if table.get("aborted"):
        return "not_established", "DtC planning aborted mid-loop"
    if table.get("budget_exhausted"):
        return ("not_established",
                "budget_solves exhausted before every contingency was solved")
    solved = [
        c for c in (table.get("contingencies") or [])
        if c.get("status") in ("ok", "optimal")
    ]
    if len(solved) < 1:
        return "not_established", "no DtC planning contingency solved"
    return "ok", None


def dtc_section_status(table: dict[str, Any]) -> tuple[str, str | None]:
    if table.get("refused"):
        return "not_established", str(table["refused"])
    if table.get("aborted"):
        return "not_established", "DtC stress aborted mid-loop"
    if table.get("budget_exhausted"):
        return ("not_established",
                "budget_solves exhausted before every contingency was solved")
    solved = [
        c for c in (table.get("contingencies") or [])
        if c.get("status") in ("ok", "optimal")
    ]
    if len(solved) < 1:
        return "not_established", "no DtC contingency solved"
    return "ok", None
