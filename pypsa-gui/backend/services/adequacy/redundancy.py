"""
Redundancy scenario enumeration (Phase 3a).

Discrete options at a fixed availability target — cost vs achieved ENS —
not FOR derating and not a joint MILP. Spec decision 5; plan Phase 3a.

Scenario economics use synthetic placeholder costs (``cost_basis``) until
pack/asset-derived train counts land. Sub-solves use a private sink and
never write the caller's solver state (cf. sweep.py § private sink).
"""
from __future__ import annotations

import copy
import logging
import math
import queue
import threading
from typing import Any, Callable, Iterable

from models.energy_hub import AvailabilityTarget, ImportOverlaySpec

logger = logging.getLogger("pypsa_gui.redundancy")

DEFAULT_SCENARIOS: tuple[str, ...] = (
    "base",
    "n1_generation",
    "n1_conversion",
    "parallel_storage",
)

_PLACEHOLDER_HEADROOM_MW = 50.0
_PLACEHOLDER_STORAGE_MW = 40.0

# Positive conversion identity only. Never treat bare AC/electricity as
# conversion — those match grid-import Links on weak_flexible packs.
_CONVERSION_ROLES = (
    "eh_conversion",
    "conversion",
    "electrolyser",
    "fuel_cell",
)
_CONVERSION_CARRIERS = ("H2", "heat", "methanol", "ammonia")
_IMPORT_ROLES = frozenset({"grid_import", "eh_import", "import"})


class RedundancyScenarioError(ValueError):
    pass


def _finite_base_mw(row, *, fallback: float = 0.0) -> float:
    """Finite MW base for headroom bumps — never ``inf``.

    Prefer a positive ``p_nom_opt`` (post-solve); otherwise positive ``p_nom``.
    Skip zeros so an unsolved ``p_nom_opt=0`` does not erase nameplate.
    """
    for col in ("p_nom_opt", "p_nom"):
        if col not in row.index:
            continue
        try:
            v = float(row[col])
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            return v
    return float(fallback)


def _import_link_ids(n) -> set[str]:
    """Links that ``select_import_links`` would claim (or role-tagged imports)."""
    out: set[str] = set()
    if n.links is None or n.links.empty:
        return out
    if "eh_role" in n.links.columns:
        for i in n.links.index:
            if str(n.links.at[i, "eh_role"]) in _IMPORT_ROLES:
                out.add(str(i))
    try:
        from services.adequacy.archetypes import select_import_links
        out.update(select_import_links(n, ImportOverlaySpec()))
    except Exception:
        logger.debug("select_import_links unavailable; role filter only", exc_info=True)
    # eh_poc endpoints
    if n.buses is not None and not n.buses.empty and "eh_poc" in n.buses.columns:
        poc = {
            str(b) for b in n.buses.index
            if n.buses.at[b, "eh_poc"] is True
            or str(n.buses.at[b, "eh_poc"]).lower() in ("true", "1", "yes")
        }
        if poc:
            for i in n.links.index:
                b0 = str(n.links.at[i, "bus0"]) if "bus0" in n.links.columns else ""
                b1 = str(n.links.at[i, "bus1"]) if "bus1" in n.links.columns else ""
                if b0 in poc or b1 in poc:
                    out.add(str(i))
    return out


def _select_conversion_link(n) -> str | None:
    """Pick a conversion Link by positive identity; never guess / never import.

    Explicit ``eh_role`` in ``_CONVERSION_ROLES`` always wins, even if the
    link's carrier would also match the import-carrier fallback. Import-role
    / PoC links are never selected. Bare AC/electricity without a conversion
    role is not conversion identity — return ``None`` and invent a spare path.
    """
    if n.links is None or n.links.empty:
        return None
    links = n.links
    banned = _import_link_ids(n)

    # 1) Positive role match (overrides import-carrier false positives).
    if "eh_role" in links.columns:
        for role in _CONVERSION_ROLES:
            for i in links.index:
                if str(links.at[i, "eh_role"]) == role:
                    # Still refuse if also tagged as an import role.
                    if str(links.at[i, "eh_role"]) in _IMPORT_ROLES:
                        continue
                    return str(i)

    # 2) Conversion carriers only, excluding anything import selection claims.
    if "carrier" in links.columns:
        carriers = {str(c) for c in _CONVERSION_CARRIERS}
        for i in links.index:
            if str(i) in banned:
                continue
            if "eh_role" in links.columns and str(links.at[i, "eh_role"]) in _IMPORT_ROLES:
                continue
            if str(links.at[i, "carrier"]) in carriers:
                return str(i)
    return None


def _na_mutation(scenario_id: str, reason: str) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "applied": [],
        "cost_basis": "synthetic_placeholder",
        "not_applicable": True,
        "not_applicable_reason": reason,
    }


def apply_redundancy_scenario(n, scenario_id: str) -> tuple[Callable[[], None], dict]:
    """Mutate ``n`` for a scenario; return ``(undo, mutation_record)``.

    ``mutation_record`` describes what changed so options are falsifiable.
    Economics are ``synthetic_placeholder`` unless ``cost_basis == "none"``.
    When a scenario cannot enlarge the feasible set, ``not_applicable`` is set
    and ``applied`` is empty (monotone headroom; assessor D2).
    """
    if scenario_id == "base":
        return lambda: None, {
            "scenario_id": "base",
            "applied": [],
            "cost_basis": "none",
            "not_applicable": False,
        }

    snap: dict[str, Any] = {
        "buses": n.buses.copy(deep=True) if n.buses is not None else None,
        "generators": n.generators.copy(deep=True) if n.generators is not None else None,
        "storage_units": (
            n.storage_units.copy(deep=True)
            if n.storage_units is not None else None),
        "links": n.links.copy(deep=True) if n.links is not None else None,
    }
    applied: list[dict[str, Any]] = []

    if scenario_id == "n1_generation":
        bus = _primary_load_bus(n)
        if "eh_spare_gen" not in n.generators.index:
            n.add(
                "Generator", "eh_spare_gen", bus=bus, carrier="gas",
                p_nom=0.0, p_nom_extendable=True,
                p_nom_max=_PLACEHOLDER_HEADROOM_MW,
                capital_cost=60.0, marginal_cost=180.0,
            )
            applied.append({
                "component": "Generator",
                "name": "eh_spare_gen",
                "action": "add",
                "bus": bus,
                "p_nom_max": _PLACEHOLDER_HEADROOM_MW,
                "capital_cost": 60.0,
                "marginal_cost": 180.0,
                "carrier": "gas",
            })
    elif scenario_id == "n1_conversion":
        link_name = _select_conversion_link(n)
        if link_name is not None:
            row = n.links.loc[link_name]
            base_mw = _finite_base_mw(row)
            target_max = base_mw + _PLACEHOLDER_HEADROOM_MW
            old_max_f: float | None
            try:
                old_max_f = float(row["p_nom_max"]) if "p_nom_max" in n.links.columns else None
            except (TypeError, ValueError):
                old_max_f = None
            if old_max_f is not None and math.isfinite(old_max_f):
                if old_max_f >= target_max:
                    # Would shrink or leave unchanged — not a redundancy enlarge.
                    def undo_na() -> None:
                        pass
                    return undo_na, _na_mutation(
                        scenario_id,
                        f"link {link_name!r} p_nom_max={old_max_f} already "
                        f">= target {target_max}; refuse to shrink",
                    )
                new_max = target_max  # strictly > old_max_f
            else:
                # Missing / inf: force finite enlargement from nameplate.
                new_max = target_max
            n.links.at[link_name, "p_nom_max"] = new_max
            if "p_nom_extendable" in n.links.columns:
                n.links.at[link_name, "p_nom_extendable"] = True
            if "eh_role" not in n.links.columns:
                n.links["eh_role"] = ""
            n.links.at[link_name, "eh_role"] = "eh_n1_conversion"
            applied.append({
                "component": "Link",
                "name": link_name,
                "action": "raise_p_nom_max",
                "base_mw": base_mw,
                "old_p_nom_max": old_max_f,
                "new_p_nom_max": new_max,
                "headroom_mw": _PLACEHOLDER_HEADROOM_MW,
            })
        else:
            # No identifiable conversion Link: invent a spare path rather than
            # mutating an import / random AC link (assessor D1).
            bus = _primary_load_bus(n)
            if "eh_n1_conv_bus" not in n.buses.index:
                n.add("Bus", "eh_n1_conv_bus", carrier="AC")
            if "eh_n1_conv_feeder" not in n.generators.index:
                n.add(
                    "Generator", "eh_n1_conv_feeder", bus="eh_n1_conv_bus",
                    carrier="gas", p_nom=_PLACEHOLDER_HEADROOM_MW,
                    marginal_cost=180.0,
                )
            if n.links is None or "eh_spare_conversion" not in getattr(
                    n.links, "index", []):
                n.add(
                    "Link", "eh_spare_conversion",
                    bus0=bus, bus1="eh_n1_conv_bus",
                    p_nom=0.0, p_nom_extendable=True,
                    p_nom_max=_PLACEHOLDER_HEADROOM_MW,
                    capital_cost=40.0, efficiency=0.95,
                    carrier="H2",
                )
                if "eh_role" not in n.links.columns:
                    n.links["eh_role"] = ""
                n.links.at["eh_spare_conversion", "eh_role"] = "eh_n1_conversion"
            applied.append({
                "component": "Link",
                "name": "eh_spare_conversion",
                "action": "add_conversion_path",
                "bus0": bus,
                "bus1": "eh_n1_conv_bus",
                "p_nom_max": _PLACEHOLDER_HEADROOM_MW,
                "capital_cost": 40.0,
            })
    elif scenario_id == "parallel_storage":
        bus = _primary_load_bus(n)
        if "eh_spare_storage" not in n.storage_units.index:
            n.add(
                "StorageUnit", "eh_spare_storage", bus=bus, carrier="battery",
                p_nom=0.0, p_nom_extendable=True,
                p_nom_max=_PLACEHOLDER_STORAGE_MW,
                max_hours=4.0, capital_cost=80.0,
                efficiency_store=0.9, efficiency_dispatch=0.9,
            )
            applied.append({
                "component": "StorageUnit",
                "name": "eh_spare_storage",
                "action": "add",
                "bus": bus,
                "p_nom_max": _PLACEHOLDER_STORAGE_MW,
                "capital_cost": 80.0,
                "max_hours": 4.0,
            })
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

    return undo, {
        "scenario_id": scenario_id,
        "applied": applied,
        "cost_basis": "synthetic_placeholder",
        "not_applicable": False,
    }


def _primary_load_bus(n) -> str:
    if n.loads is None or n.loads.empty:
        if n.buses is None or n.buses.empty:
            raise RedundancyScenarioError(
                "network has no buses for redundancy")
        return str(n.buses.index[0])
    return str(n.loads.iloc[0]["bus"])


def _detach_solver_model(network) -> None:
    """Clear attached solver model so ``network.copy()`` is allowed.

    Side effect: mutates ``network.model.solver_model`` on the *input*
    network (sets it to ``None``). Callers that still need the live model
    must re-attach it themselves.
    """
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
    store: dict | None = None,
    pack_hash: str | None = None,
    assumptions_hash: str | None = None,
) -> dict[str, Any]:
    """Solve each scenario at a fixed ENS target; return comparison table.

    Certify method for P3a is **ENS**. Sub-solves use a private sink (N2).
    Pass ``pack_hash`` / ``assumptions_hash`` for staleness detection (N4).
    """
    from services.solver_service import SolverConfig, run_simulation

    scenarios = tuple(scenarios) if scenarios is not None else DEFAULT_SCENARIOS
    if not scenarios:
        raise RedundancyScenarioError("need at least one redundancy scenario")
    log_queue = log_queue or queue.SimpleQueue()

    ens_cap = None
    if availability is not None:
        ens_cap = availability.ens_cap_permyriad
    if ens_cap is None:
        ens_cap = getattr(cfg, "ens_cap_permyriad", None)
    if ens_cap is None or float(ens_cap) <= 0:
        raise RedundancyScenarioError(
            "redundancy compare requires ens_cap_permyriad > 0")

    options: list[dict[str, Any]] = []
    solves_attempted = 0
    aborted = False
    for sid in scenarios:
        if stop_event.is_set():
            aborted = True
            break
        _detach_solver_model(network)
        nn = network.copy()
        undo, mutation = apply_redundancy_scenario(nn, sid)
        if mutation.get("not_applicable"):
            options.append({
                "scenario_id": sid,
                "status": "not_applicable",
                "condition": mutation.get("not_applicable_reason"),
                "cost_at_target_eur": None,
                "achieved_ens_mwh": None,
                "cap_mwh": None,
                "binding": None,
                "binding_metric": "ens",
                "meets_target": None,
                "excludes_shed_cost": True,
                "cost_basis": mutation["cost_basis"],
                "applied": mutation["applied"],
                "not_applicable": True,
            })
            try:
                undo()
            except Exception:
                logger.exception("redundancy N/A undo failed for %s", sid)
            continue

        sink: dict = {}
        try:
            cfg_i = copy.copy(cfg) if cfg is not None else SolverConfig()
            try:
                cfg_i.ens_cap_permyriad = float(ens_cap)
            except Exception:
                pass
            if float(getattr(cfg_i, "voll", 0.0) or 0.0) <= 0:
                cfg_i.voll = 150.0
            solves_attempted += 1
            status, condition = run_simulation(
                cfg_i, nn, lock, stop_event, log_queue,
                state_update=lambda **kw: sink.update(kw),
            )
            rep = sink.get("adequacy_report") if isinstance(
                sink.get("adequacy_report"), dict) else {}
            tgt = (rep or {}).get("target") or {}
            system = tgt.get("system") or {}
            cost = (rep or {}).get("cost") or {}
            achieved = system.get("achieved_ens_mwh")
            cap = system.get("cap_mwh")
            meets: bool | None
            if achieved is not None and cap is not None and float(cap) > 0:
                meets = float(achieved) <= float(cap) * (1.0 + 1e-4)
            else:
                meets = None
            options.append({
                "scenario_id": sid,
                "status": status,
                "condition": condition,
                "cost_at_target_eur": cost.get("total_system_cost_eur"),
                "achieved_ens_mwh": achieved,
                "cap_mwh": cap,
                "binding": tgt.get("binding"),
                "binding_metric": "ens",
                "meets_target": (
                    bool(meets) and status in ("ok", "optimal")
                    if meets is not None else None),
                "excludes_shed_cost": True,
                "cost_basis": mutation["cost_basis"],
                "applied": mutation["applied"],
                "not_applicable": False,
            })
        finally:
            try:
                undo()
            except Exception:
                logger.exception("redundancy scenario undo failed for %s", sid)
            _detach_solver_model(nn)

    solved = [
        o for o in options
        if o.get("status") in ("ok", "optimal") and not o.get("not_applicable")
    ]
    out = {
        "certify_method": "ens",
        "ens_cap_permyriad": float(ens_cap),
        "pack_hash": pack_hash,
        "assumptions_hash": assumptions_hash,
        "options": options,
        "solves_attempted": solves_attempted,
        "aborted": aborted,
        "comparable_solved": len(solved),
    }
    if store is not None:
        store["eh_redundancy_comparison"] = out
    return out


def redundancy_section_status(table: dict[str, Any]) -> tuple[str, str | None]:
    """Map a comparison table to (section_status, note) — assessor D3 honesty."""
    if table.get("aborted"):
        return "not_established", "redundancy compare aborted mid-loop"
    options = table.get("options") or []
    solved = [
        o for o in options
        if o.get("status") in ("ok", "optimal") and not o.get("not_applicable")
    ]
    if len(solved) < 1:
        return "not_established", "no redundancy option solved"
    if len(solved) < 2:
        return "not_established", "fewer than two comparable solved options"
    return "ok", None
