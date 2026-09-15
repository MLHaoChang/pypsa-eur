"""
Import-cap + storage-duration scenario levers (Phase 3c).

Discrete planning options at a fixed ENS target — especially for off_grid /
weak_flexible packs. Spec decisions 6–7; plan Phase 3c.

Honesty pins:
* import = planning limit only (not certified interconnector adequacy)
* annual ENS/LOLE ≠ multi-day autonomy; autonomy scenarios are reported
  explicitly via storage_duration hours
"""
from __future__ import annotations

import copy
import logging
import math
import queue
import threading
from typing import Any, Callable, Iterable

from models.energy_hub import AvailabilityTarget, ImportOverlaySpec

logger = logging.getLogger("pypsa_gui.levers")

DEFAULT_LEVER_KINDS: tuple[str, ...] = ("import_cap", "storage_duration")
DEFAULT_IMPORT_CAPS_MW: tuple[float, ...] = (0.0, 25.0, 50.0)
DEFAULT_STORAGE_HOURS: tuple[float, ...] = (4.0, 24.0, 72.0)

HONESTY_NOTES: tuple[str, ...] = (
    "import_is_planning_limit_only",
    "annual_ens_is_not_multi_day_autonomy",
)


class LeverScenarioError(ValueError):
    pass


def _import_link_ids(n) -> list[str]:
    if n.links is None or n.links.empty:
        return []
    try:
        from services.adequacy.archetypes import select_import_links
        hits = select_import_links(n, ImportOverlaySpec())
        if hits:
            return list(hits)
    except Exception:
        logger.debug("select_import_links failed; falling back to role", exc_info=True)
    if "eh_role" in n.links.columns:
        return [
            str(i) for i in n.links.index
            if str(n.links.at[i, "eh_role"]) in ("grid_import", "eh_import", "import")
        ]
    return [str(n.links.index[0])] if len(n.links.index) else []


def apply_lever_scenario(
    n, kind: str, *, value: float,
) -> tuple[Callable[[], None], dict[str, Any]]:
    """Mutate ``n`` for one lever setting; return ``(undo, mutation)``."""
    if kind == "import_cap":
        links = _import_link_ids(n)
        if not links:
            raise LeverScenarioError("no import Links to apply import_cap")
        snap = n.links.copy(deep=True)
        cap = float(value)
        for name in links:
            if "p_nom_max" in n.links.columns:
                n.links.at[name, "p_nom_max"] = cap
            if "p_nom" in n.links.columns:
                n.links.at[name, "p_nom"] = cap
            if "p_nom_extendable" in n.links.columns:
                n.links.at[name, "p_nom_extendable"] = False
        def undo() -> None:
            n.links = snap
        return undo, {
            "kind": kind,
            "value": cap,
            "unit": "MW",
            "applied_links": links,
            "firmness": "planning_limit_only",
            "autonomy_note": None,
        }

    if kind == "storage_duration":
        if n.storage_units is None or n.storage_units.empty:
            raise LeverScenarioError("no StorageUnits to apply storage_duration")
        snap = n.storage_units.copy(deep=True)
        hours = float(value)
        if hours <= 0:
            raise LeverScenarioError("storage_duration must be > 0")
        for name in n.storage_units.index:
            n.storage_units.at[name, "max_hours"] = hours
        def undo() -> None:
            n.storage_units = snap
        return undo, {
            "kind": kind,
            "value": hours,
            "unit": "h",
            "applied_storage": [str(i) for i in n.storage_units.index],
            "firmness": "planning_limit_only",
            "autonomy_note": (
                f"{hours:g} h nameplate autonomy (not a multi-day ENS claim)"
            ),
        }

    raise LeverScenarioError(f"unknown lever kind {kind!r}")


def _detach_solver_model(network) -> None:
    model = getattr(network, "model", None)
    if model is not None and getattr(model, "solver_model", None) is not None:
        model.solver_model = None


def compare_lever_scenarios(
    network,
    cfg,
    *,
    lock,
    stop_event: threading.Event,
    log_queue: queue.Queue | None = None,
    kind: str = "storage_duration",
    values: Iterable[float] | None = None,
    availability: AvailabilityTarget | None = None,
    store: dict | None = None,
    pack_hash: str | None = None,
    assumptions_hash: str | None = None,
) -> dict[str, Any]:
    """Solve each lever value at a fixed ENS target; return comparison table."""
    from services.solver_service import SolverConfig, run_simulation

    if kind not in DEFAULT_LEVER_KINDS:
        raise LeverScenarioError(f"unknown lever kind {kind!r}")
    if values is None:
        values = (
            DEFAULT_STORAGE_HOURS if kind == "storage_duration"
            else DEFAULT_IMPORT_CAPS_MW
        )
    values = tuple(float(v) for v in values)
    if len(values) < 1:
        raise LeverScenarioError("need at least one lever value")
    log_queue = log_queue or queue.SimpleQueue()

    ens_cap = None
    if availability is not None:
        ens_cap = availability.ens_cap_permyriad
    if ens_cap is None:
        ens_cap = getattr(cfg, "ens_cap_permyriad", None)
    if ens_cap is None or float(ens_cap) <= 0:
        raise LeverScenarioError(
            "lever compare requires ens_cap_permyriad > 0")

    options: list[dict[str, Any]] = []
    solves_attempted = 0
    aborted = False
    for val in values:
        if stop_event.is_set():
            aborted = True
            break
        _detach_solver_model(network)
        nn = network.copy()
        undo, mutation = apply_lever_scenario(nn, kind, value=val)
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
                "kind": kind,
                "value": val,
                "unit": mutation["unit"],
                "status": status,
                "condition": condition,
                "cost_at_target_eur": cost.get("total_system_cost_eur"),
                "achieved_ens_mwh": achieved,
                "cap_mwh": cap,
                "binding_metric": "ens",
                "meets_target": (
                    bool(meets) and status in ("ok", "optimal")
                    if meets is not None else None),
                "firmness": mutation["firmness"],
                "autonomy_note": mutation.get("autonomy_note"),
                "applied": mutation,
            })
        finally:
            try:
                undo()
            except Exception:
                logger.exception("lever scenario undo failed for %s=%s", kind, val)
            _detach_solver_model(nn)

    solved = [o for o in options if o.get("status") in ("ok", "optimal")]
    out = {
        "kind": kind,
        "certify_method": "ens",
        "ens_cap_permyriad": float(ens_cap),
        "import_firmness": "planning_limit_only",
        "honesty_notes": list(HONESTY_NOTES),
        "pack_hash": pack_hash,
        "assumptions_hash": assumptions_hash,
        "options": options,
        "solves_attempted": solves_attempted,
        "aborted": aborted,
        "comparable_solved": len(solved),
    }
    if store is not None:
        store["eh_lever_comparison"] = out
    return out


def levers_section_status(table: dict[str, Any]) -> tuple[str, str | None]:
    if table.get("aborted"):
        return "not_established", "lever compare aborted mid-loop"
    solved = [
        o for o in (table.get("options") or [])
        if o.get("status") in ("ok", "optimal")
    ]
    if len(solved) < 1:
        return "not_established", "no lever option solved"
    if len(solved) < 2:
        return "not_established", "fewer than two comparable solved lever options"
    return "ok", None
