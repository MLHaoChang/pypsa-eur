"""
Redundancy scenario enumeration (Phase 3a).

Discrete options at a fixed availability target — cost vs achieved ENS —
not FOR derating and not a joint MILP. Spec decision 5; plan Phase 3a.
"""
from __future__ import annotations

import copy
import logging
import queue
import threading
from typing import Any, Callable, Iterable

from models.energy_hub import AvailabilityTarget

logger = logging.getLogger("pypsa_gui.redundancy")

DEFAULT_SCENARIOS: tuple[str, ...] = (
    "base",
    "n1_generation",
    "n1_conversion",
    "parallel_storage",
)


class RedundancyScenarioError(ValueError):
    pass


def apply_redundancy_scenario(n, scenario_id: str) -> Callable[[], None]:
    """Mutate ``n`` for a scenario; return undo that restores component tables."""
    if scenario_id == "base":
        return lambda: None

    snap: dict[str, Any] = {
        "buses": n.buses.copy(deep=True) if n.buses is not None else None,
        "generators": n.generators.copy(deep=True) if n.generators is not None else None,
        "storage_units": (
            n.storage_units.copy(deep=True)
            if n.storage_units is not None else None),
        "links": n.links.copy(deep=True) if n.links is not None else None,
    }

    if scenario_id == "n1_generation":
        bus = _primary_load_bus(n)
        if "eh_spare_gen" not in n.generators.index:
            n.add(
                "Generator", "eh_spare_gen", bus=bus, carrier="gas",
                p_nom=0.0, p_nom_extendable=True, p_nom_max=50.0,
                capital_cost=60.0, marginal_cost=180.0,
            )
    elif scenario_id == "n1_conversion":
        # Distinct from n1_generation: Link topology only — never spare-gen alias.
        if n.links is not None and not n.links.empty:
            name = str(n.links.index[0])
            if "p_nom_max" in n.links.columns:
                cur = float(n.links.at[name, "p_nom_max"] or 0.0)
                if cur != cur:  # NaN
                    cur = float(n.links.at[name, "p_nom"] or 0.0)
                n.links.at[name, "p_nom_max"] = cur + 50.0
            if "p_nom_extendable" in n.links.columns:
                n.links.at[name, "p_nom_extendable"] = True
            if "eh_role" not in n.links.columns:
                n.links["eh_role"] = ""
            n.links.at[name, "eh_role"] = "eh_n1_conversion"
        else:
            bus = _primary_load_bus(n)
            if "eh_n1_conv_bus" not in n.buses.index:
                n.add("Bus", "eh_n1_conv_bus", carrier="AC")
            if "eh_n1_conv_feeder" not in n.generators.index:
                n.add(
                    "Generator", "eh_n1_conv_feeder", bus="eh_n1_conv_bus",
                    carrier="gas", p_nom=50.0, marginal_cost=180.0,
                )
            if n.links is None or "eh_spare_conversion" not in getattr(
                    n.links, "index", []):
                n.add(
                    "Link", "eh_spare_conversion",
                    bus0=bus, bus1="eh_n1_conv_bus",
                    p_nom=0.0, p_nom_extendable=True, p_nom_max=50.0,
                    capital_cost=40.0, efficiency=0.95,
                )
                if "eh_role" not in n.links.columns:
                    n.links["eh_role"] = ""
                n.links.at["eh_spare_conversion", "eh_role"] = "eh_n1_conversion"
    elif scenario_id == "parallel_storage":
        bus = _primary_load_bus(n)
        if "eh_spare_storage" not in n.storage_units.index:
            n.add(
                "StorageUnit", "eh_spare_storage", bus=bus, carrier="battery",
                p_nom=0.0, p_nom_extendable=True, p_nom_max=40.0,
                max_hours=4.0, capital_cost=80.0,
                efficiency_store=0.9, efficiency_dispatch=0.9,
            )
    else:
        raise RedundancyScenarioError(
            f"unknown redundancy scenario {scenario_id!r}")

    def undo() -> None:
        if snap["buses"] is not None:
            n.buses = snap["buses"]
        if snap["generators"] is not None:
            n.generators = snap["generators"]
        if snap["storage_units"] is not None:
            n.storage_units = snap["storage_units"]
        if snap["links"] is not None:
            n.links = snap["links"]
        elif scenario_id == "n1_conversion":
            if n.links is not None and not n.links.empty:
                n.links = n.links.iloc[0:0].copy()

    return undo


def _primary_load_bus(n) -> str:
    if n.loads is None or n.loads.empty:
        if n.buses is None or n.buses.empty:
            raise RedundancyScenarioError(
                "network has no buses for redundancy")
        return str(n.buses.index[0])
    return str(n.loads.iloc[0]["bus"])


def _detach_solver_model(network) -> None:
    """PyPSA refuses ``network.copy()`` while a solver model is attached."""
    model = getattr(network, "model", None)
    if model is not None and getattr(model, "solver_model", None) is not None:
        model.solver_model = None


def compare_redundancy_scenarios(
    network,
    cfg,
    *,
    lock,
    stop_event: threading.Event,
    log_queue: queue.Queue | None = None,
    scenarios: Iterable[str] | None = None,
    availability: AvailabilityTarget | None = None,
    state_update=None,
    store: dict | None = None,
) -> dict[str, Any]:
    """Solve each scenario at a fixed ENS target; return comparison table.

    Certify method for P3a is **ENS** (planning metric). MC LOLE comparison
    is out of scope here (P3b / coupling loop).
    """
    from services.solver_service import SolverConfig, run_simulation

    scenarios = tuple(scenarios) if scenarios is not None else DEFAULT_SCENARIOS
    if not scenarios:
        raise RedundancyScenarioError("need at least one redundancy scenario")
    log_queue = log_queue or queue.SimpleQueue()
    state_update = state_update or (lambda **kw: None)

    ens_cap = None
    if availability is not None:
        ens_cap = availability.ens_cap_permyriad
    if ens_cap is None:
        ens_cap = getattr(cfg, "ens_cap_permyriad", None)
    if ens_cap is None or float(ens_cap) <= 0:
        raise RedundancyScenarioError(
            "redundancy compare requires ens_cap_permyriad > 0")

    options: list[dict[str, Any]] = []
    for sid in scenarios:
        if stop_event.is_set():
            break
        _detach_solver_model(network)
        nn = network.copy()
        undo = apply_redundancy_scenario(nn, sid)
        sink: dict = {}
        try:
            cfg_i = copy.copy(cfg) if cfg is not None else SolverConfig()
            try:
                cfg_i.ens_cap_permyriad = float(ens_cap)
            except Exception:
                pass
            if float(getattr(cfg_i, "voll", 0.0) or 0.0) <= 0:
                cfg_i.voll = 150.0
            status, condition = run_simulation(
                cfg_i, nn, lock, stop_event, log_queue,
                state_update=lambda **kw: (sink.update(kw), state_update(**kw)),
            )
            rep = sink.get("adequacy_report") if isinstance(
                sink.get("adequacy_report"), dict) else {}
            tgt = (rep or {}).get("target") or {}
            system = tgt.get("system") or {}
            cost = (rep or {}).get("cost") or {}
            achieved = system.get("achieved_ens_mwh")
            cap = system.get("cap_mwh")
            meets = False
            if achieved is not None and cap is not None and float(cap) > 0:
                meets = float(achieved) <= float(cap) * (1.0 + 1e-4)
            elif tgt.get("binding") == "system_cap":
                meets = True
            options.append({
                "scenario_id": sid,
                "status": status,
                "condition": condition,
                "cost_at_target_eur": cost.get("total_system_cost_eur"),
                "achieved_ens_mwh": achieved,
                "cap_mwh": cap,
                "binding": tgt.get("binding"),
                "binding_metric": "ens",
                "meets_target": bool(meets) and status in ("ok", "optimal"),
                "excludes_shed_cost": True,
            })
        finally:
            try:
                undo()
            except Exception:
                logger.exception("redundancy scenario undo failed for %s", sid)
            _detach_solver_model(nn)

    out = {
        "certify_method": "ens",
        "ens_cap_permyriad": float(ens_cap),
        "options": options,
    }
    if store is not None:
        store["eh_redundancy_comparison"] = out
    return out
