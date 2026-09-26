"""
Energy Hub readiness preflight (P14) — what a study WOULD do, without solving.

Read-only: works on a pack-applied private copy and reuses the driver's own
selectors, gating order and cost helpers (``eh_study``), so the preview and
the run cannot disagree about which Links are imports, what the hub is, or
which stages fit the budget. Estimates are exact where the driver's cost is
fixed in advance (ENS, frontier, fmea_top) and upper bounds where a loop can
stop early (redundancy, levers, DtC). A budget skip that follows an upper
bound is reported as ``may_skip_budget``: an early stop can leave room.

Predictions: ``run``, ``not_requested``, ``not_established`` (the driver
records a skip with a reason), ``skipped_budget`` / ``may_skip_budget``,
``fails`` (``apply_pack`` refuses the pack — the study raises) and
``not_reached`` (every stage after a failing ``apply_pack``).
"""
from __future__ import annotations

from typing import Any

from models.energy_hub import EH_PIPELINE_STAGES, ArchetypePack
from services.adequacy import archetypes as arch
from services.adequacy import eh_study as S


def _stage_estimate(stage: str, net, pack: ArchetypePack, ctx: dict) -> tuple[
        int, str, str | None]:
    """(solves, basis, not_established_reason) for one requested stage."""
    from services.adequacy import levers as lev
    from services.adequacy import redundancy as red

    if stage == "ens_solve":
        return 1, "exact", None
    if stage == "mc_certify":
        if not ctx["mc_boundary"]["ok"]:
            return 0, "exact", ctx["mc_boundary"]["error"]
        return 0, "exact", None
    if stage in ("apply_pack", "assemble"):
        return 0, "exact", None
    if stage in ("frontier", "fmea_top") and not ctx["voll_ok"]:
        return 0, "exact", (f"{stage} needs VOLL > 0 (the session VOLL is "
                            f"{ctx['voll']:g})")
    if stage == "frontier":
        if pack.availability.ens_cap_permyriad is None:
            return 0, "exact", "frontier needs the pack's ENS target"
        n = S.frontier_point_count(ctx["remaining"], ctx["budget"])
        if n < 2:
            return 0, "exact", "budget leaves room for fewer than two points"
        return (len(S.frontier_targets(pack.availability.ens_cap_permyriad, n)),
                "exact", None)
    if stage == "fmea_top":
        k = ctx["class_b"]["k"]
        if ctx["class_b"].get("error"):
            return 0, "exact", ctx["class_b"]["error"]
        if not k:
            return 0, "exact", "no Class-B-eligible Links (no outage data)"
        return S.fmea_solve_cost(k), "exact", None
    if stage == "redundancy":
        return (len(red.expand_redundancy_domain(red.DEFAULT_SCENARIOS)),
                "upper_bound", None)
    if stage == "levers":
        total = 0
        if pack.levers.storage_duration and ctx["storage_units"]:
            total += len(lev.DEFAULT_STORAGE_HOURS)
        if pack.levers.import_cap and ctx["lever_import_links"]:
            total += len(lev.DEFAULT_IMPORT_CAPS_MW)
        if not pack.levers.storage_duration and not pack.levers.import_cap \
                and ctx["storage_units"]:
            total += len(lev.DEFAULT_STORAGE_HOURS)
        if total == 0:
            return 0, "exact", "no applicable lever (no StorageUnits / import Links)"
        return total, "upper_bound", None
    if stage in ("dtc_stress", "dtc_planning"):
        if not ctx["dtc"]["derivable"]:
            return 0, "exact", ctx["dtc"]["reason"]
        return len(ctx["dtc"]["islanding_contingencies"]), "upper_bound", None
    return 0, "exact", None


def eh_readiness(network, pack: ArchetypePack, *, budget_solves: int,
                 stages=None, voll: float | None = None) -> dict[str, Any]:
    """Readiness of ``network`` for an EH study of ``pack`` (see module doc)."""
    from services.adequacy import levers as lev
    from services.adequacy import scr_gate as scr_mod
    from services.adequacy import sweep as sw

    requested = (S.validate_stages(stages) if stages is not None
                 else S.default_stages_for(pack))
    warnings: list[str] = []

    net = S._private_copy(network)
    links, rule = arch.select_import_links_with_rule(net, pack.import_overlay)
    applied = None
    try:
        applied = arch.apply_archetype_pack_detailed(net, pack)
    except arch.ArchetypePackError as exc:
        warnings.append(f"pack cannot be applied: {exc}")

    crit = S.critical_buses(net)
    dtc = S.derive_dtc_config(net, pack)
    dtc_block = ({"derivable": True,
                  "critical_bus_ids": dtc.critical_bus_ids,
                  "islanding_contingencies": dtc.islanding_contingencies,
                  "reason": None} if dtc is not None else
                 {"derivable": False, "critical_bus_ids": crit,
                  "islanding_contingencies": list(links),
                  "reason": ("tag at least one bus eh_critical"
                             if links else "no import Links identified")
                             + " — or pass dtc_config"})

    if pack.archetype == "weak_flexible":
        _block, status, payload, note = scr_mod.evaluate_network_scr_gate(net)
        scr = {"status": status, "note": note,
               "min_scr": (payload or {}).get("min_scr")}
    else:
        scr = {"status": "not_required", "note": None, "min_scr": None}

    closed = S._closed_import_links(net, pack)
    fcopy = S._private_copy(net)
    if closed:
        fcopy.remove("Link", closed)
    class_b: dict[str, Any] = {"k": 0, "closed_import_links": closed,
                               "error": None}
    try:
        class_b["k"] = len(sw.class_b_contingencies(fcopy))
    except sw.SweepBudgetError as exc:
        class_b["error"] = str(exc)

    try:
        _mc, info = arch.hub_boundary_copy(net, pack)
        mc_boundary = {"ok": True, "error": None, **info}
    except arch.HubBoundaryError as exc:
        mc_boundary = {"ok": False, "error": str(exc), "rule": rule}

    # The driver's cfg VOLL (a session config without one reads as 0 there).
    voll_v = float(voll or 0.0)
    if voll_v <= 0:
        warnings.append("VOLL is 0 — frontier and fmea_top need VOLL > 0")

    ctx = {"remaining": budget_solves, "budget": budget_solves,
           "class_b": class_b, "dtc": dtc_block, "mc_boundary": mc_boundary,
           "voll": voll_v, "voll_ok": voll_v > 0,
           "storage_units": int(len(net.storage_units)),
           # The levers stage selects with its OWN rule, not the pack's §6.
           "lever_import_links": lev._import_link_ids(net)}
    rows = []
    total = 0
    after_upper = False
    for stage in EH_PIPELINE_STAGES:
        if stage not in requested:
            rows.append({"stage": stage, "prediction": "not_requested",
                         "solves": 0, "basis": "exact", "reason": None})
            continue
        if applied is None:
            pred, reason = (("fails", warnings[0]) if stage == "apply_pack"
                            else ("not_reached", "apply_pack fails"))
            rows.append({"stage": stage, "prediction": pred, "solves": 0,
                         "basis": "exact", "reason": reason})
            continue
        solves, basis, reason = _stage_estimate(stage, net, pack, ctx)
        skip_budget = "may_skip_budget" if after_upper else "skipped_budget"
        if (stage not in S.ZERO_SOLVE_STAGES and stage not in S.REQUIRED_STAGES
                and ctx["remaining"] <= 0):
            pred, reason, solves = skip_budget, (
                f"budget_solves exhausted before this stage"), 0
        elif reason is not None:
            pred = skip_budget if "budget" in reason else "not_established"
            solves = 0
        elif stage == "fmea_top" and solves > ctx["remaining"]:
            pred, reason = skip_budget, (
                f"needs {solves} solves, {ctx['remaining']} left")
            solves = 0
        else:
            pred = "run"
            solves = min(solves, ctx["remaining"]) if basis == "upper_bound" \
                else solves
            after_upper = after_upper or (basis == "upper_bound" and solves > 0)
        total += solves
        ctx["remaining"] = max(0, ctx["remaining"] - solves)
        rows.append({"stage": stage, "prediction": pred, "solves": solves,
                     "basis": basis, "reason": reason})

    return {
        "archetype": pack.archetype,
        "pack_hash": arch.pack_hash(pack),
        "import": {"rule": rule, "links": list(links),
                   "applied": applied is not None},
        "critical_buses": crit,
        "dtc": dtc_block,
        "scr": scr,
        "storage_units": ctx["storage_units"],
        "class_b": class_b,
        "mc_boundary": mc_boundary,
        "budget_solves": budget_solves,
        "estimated_solves": total,
        "stages": rows,
        "warnings": warnings,
    }
