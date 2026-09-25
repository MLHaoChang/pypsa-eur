"""
Energy Hub study orchestrator (Phase 1.5; stage table since P11).

Runs archetype pack → ENS solve → the requested stages in spec decision-18
order → assemble. Optional stages may be skipped; required-but-missing stages
surface as ``not_established`` / pipeline notes.

Report emission: ``assemble_reference_design_report`` only (spec decision 16).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import queue
import threading
from typing import Any, Iterable

from models.energy_hub import (
    DEFAULT_EH_BUDGET_SOLVES,
    EH_PIPELINE_STAGES,
    MAX_EH_BUDGET_SOLVES,
    ArchetypePack,
    PipelineStageRecord,
    ReferenceDesignReport,
)
from services.adequacy import archetypes as arch
from services.adequacy import eh_report as report_mod

logger = logging.getLogger("pypsa_gui.eh_study")

DEFAULT_STAGES = EH_PIPELINE_STAGES

# Spec decision 14 / P2: FMEA top-N from the EH study is Link-primary Class-B
# residual risk. AC Line/Transformer N-1 stays on SCLOPF and is omitted from
# the FMEA ranking unless a future product decision merges them.
FMEA_TOP_LINK_PRIMARY_NOTE = (
    "Link-primary residual risk (Class-B Link sweep); "
    "AC Line/Transformer N-1 remains on SCLOPF and is omitted from FMEA ranking"
)

# Re-export for tests / callers.
__all__ = [
    "DEFAULT_STAGES",
    "DEFAULT_EH_BUDGET_SOLVES",
    "MAX_EH_BUDGET_SOLVES",
    "FMEA_TOP_LINK_PRIMARY_NOTE",
    "REQUIRED_STAGES",
    "SIBLING_STORE_KEYS",
    "ZERO_SOLVE_STAGES",
    "certification_wanted",
    "default_stages_for",
    "run_eh_study",
    "validate_stages",
]

# Spec decision 18: stages *after* ens_solve may be skipped — these two may not.
# A report labelled with an archetype whose pack was never applied, or with no
# ENS solve behind it, is not that archetype's reference design.
REQUIRED_STAGES: tuple[str, ...] = ("apply_pack", "ens_solve")

# Per-stage sibling tables served by GET /results/eh_* next to the report.
# Cleared when a new study starts so a table from a previous archetype/run is
# never shown beside a newer report.
SIBLING_STORE_KEYS: tuple[str, ...] = (
    "eh_redundancy_comparison",
    "eh_lever_comparison",
    "eh_dtc_stress",
    "eh_dtc_planning",
)

# Pipeline stage → report section it fills (for unreached-stage notes).
_STAGE_SECTION = {
    "redundancy": "redundancy",
    "levers": "levers",
    "dtc_stress": "dtc",
    "dtc_planning": "dtc",
    "mc_certify": "certification",
}

# Stages that charge no LP solve. Never skipped for an exhausted budget (plan
# B5): a spent LP budget must not silently drop a required certification.
ZERO_SOLVE_STAGES: frozenset[str] = frozenset(
    {"apply_pack", "mc_certify", "assemble"})

# MC certification defaults (the /mc study's defaults; P13 exposes them).
DEFAULT_MC_DRAWS = 500
DEFAULT_MC_SEED = 0
DEFAULT_MC_COV_TARGET = 0.05


def validate_stages(stages: Iterable[str] | None) -> tuple[str, ...] | None:
    """Refuse unknown stage names and lists that drop a required stage.

    ``None`` means the pack's default pipeline. Raises ``ValueError`` with a
    message naming the offending stage(s) — HTTP maps it to 422.
    """
    if stages is None:
        return None
    requested = tuple(str(s) for s in stages)
    if not requested:
        raise ValueError("stages, when set, must be a non-empty list")
    unknown = [s for s in requested if s not in EH_PIPELINE_STAGES]
    if unknown:
        raise ValueError(
            f"unknown EH pipeline stage(s) {unknown}; expected a subset of "
            f"{list(EH_PIPELINE_STAGES)}")
    missing = [s for s in REQUIRED_STAGES if s not in requested]
    if missing:
        raise ValueError(
            f"stages must include {list(REQUIRED_STAGES)} (only stages after "
            f"ens_solve may be skipped); missing {missing}")
    return requested


def _assumptions_hash(cfg) -> str:
    raw = {
        "voll": getattr(cfg, "voll", None),
        "ens_cap_permyriad": getattr(cfg, "ens_cap_permyriad", None),
        "dsr_buses": list(getattr(cfg, "dsr_buses", None) or []),
        "dsr_price_eur_per_mwh": getattr(cfg, "dsr_price_eur_per_mwh", None),
        "dsr_share_of_load": getattr(cfg, "dsr_share_of_load", None),
    }
    blob = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def certification_wanted(pack: ArchetypePack) -> bool:
    """Whether the pack's default pipeline certifies on MC LOLE (plan B3)."""
    a = pack.availability
    return bool(pack.mc_certify_required
                or a.certification_metric == "mc_lole"
                or a.target_lole_h is not None)


def default_stages_for(pack: ArchetypePack) -> tuple[str, ...]:
    """The pack's default pipeline (``stages=None``)."""
    def _keep(s: str) -> bool:
        if s == "redundancy":
            return bool(pack.levers.redundancy)
        if s == "levers":
            return bool(pack.levers.import_cap or pack.levers.storage_duration)
        if s == "mc_certify":
            return certification_wanted(pack)
        if s == "dtc_stress":
            return bool(pack.dtc_stress_default)
        if s == "dtc_planning":
            return bool(getattr(pack, "dtc_planning_default", False))
        return True
    return tuple(s for s in DEFAULT_STAGES if _keep(s))


class _Study:
    """Mutable state of one run, shared by the stage handlers."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def mark(self, stage: str, status: str, *, note: str | None = None,
             solves_charged: int = 0) -> None:
        for rec in self.records:
            if rec.stage == stage:
                rec.status = status  # type: ignore[assignment]
                rec.note = note
                rec.solves_charged = solves_charged
                break

    def remaining(self) -> int:
        return max(0, self.budget_solves - self.solves)

    def blocked(self, stage: str) -> bool:
        """Skip a requested stage when ens_solve failed or (for stages that
        solve LPs) the budget is spent."""
        if self.failed_reason is not None:
            reason = f"not run: {self.failed_reason}"
        elif stage not in ZERO_SOLVE_STAGES and self.remaining() <= 0:
            reason = (f"not run: budget_solves exhausted "
                      f"({self.solves}/{self.budget_solves}) before this stage")
        else:
            return False
        self.mark(stage, "skipped", note=reason)
        section = _STAGE_SECTION.get(stage)
        if section is not None:
            self.sections.setdefault(section, ("not_established", None, reason))
        return True


# ── stage handlers (each runs only when requested, not blocked, not aborted) ─


def _stage_apply_pack(st: _Study) -> None:
    result = arch.apply_archetype_pack_detailed(st.network, st.pack)
    st.undo = result.undo
    st.pack_h = result.pack_hash
    st.mark("apply_pack", "run",
            note="; ".join(list(result.warnings) + st.pack_notes) or None)


def _stage_ens_solve(st: _Study) -> None:
    from services.solver_service import run_simulation

    sink: dict = {}
    status, condition = run_simulation(
        st.cfg, st.network, st.lock, st.stop_event, st.log_queue,
        state_update=lambda **kw: sink.update(kw),
    )
    st.solves += 1
    if status not in ("ok", "optimal") and st.stop_event.is_set():
        st.mark("ens_solve", "aborted",
                note=f"{status}:{condition}", solves_charged=1)
        st.aborted = True
        return
    if status not in ("ok", "optimal"):
        reason = f"ens_solve {status}:{condition}"
        if "infeasible" in str(condition):
            reason += (" — the pack's ENS target cannot be met by this "
                       "network under the pack overlay")
        st.failed_reason = reason
        st.mark("ens_solve", "failed", note=reason, solves_charged=1)
        for sec in ("target", "cost", "sizing", "tea", "multi_energy"):
            st.sections[sec] = ("not_established", None, reason)
        return

    st.mark("ens_solve", "run", solves_charged=1)
    adequacy_report = sink.get("adequacy_report")
    st.adequacy_report = adequacy_report
    if not isinstance(adequacy_report, dict):
        return
    network, cfg = st.network, st.cfg
    tgt = adequacy_report.get("target") or {}
    system = tgt.get("system") or {}
    metrics = adequacy_report.get("metrics") or {}
    cost = adequacy_report.get("cost") or {}
    st.achieved_shed_hours = system.get("achieved_shed_hours")
    ens_mwh = system.get("achieved_ens_mwh")
    # Invert ‱ if demand known from cap_mwh, at the cap actually solved at.
    cap_mwh = system.get("cap_mwh")
    st.ens_cap = getattr(cfg, "ens_cap_permyriad", st.ens_cap)
    ens_cap = st.ens_cap
    if (ens_cap is not None and cap_mwh
            and float(cap_mwh) > 0 and ens_mwh is not None):
        # achieved ‱ ≈ ens_mwh / demand * 1e4; demand = cap_mwh / (ens_cap/1e4)
        demand = float(cap_mwh) / (float(ens_cap) / 1e4)
        st.achieved_ens_permyriad = (
            float(ens_mwh) / demand * 1e4 if demand else None)
    st.sections["target"] = (
        "ok", {"binding": tgt.get("binding"), "system": system,
               "metrics": metrics}, None)
    st.cost_at_target = cost.get("total_system_cost_eur")
    st.period_basis = cost.get("period_basis")
    st.sections["cost"] = (
        "ok", {"total_system_cost_eur": st.cost_at_target,
               "period_basis": st.period_basis,
               "excludes_shed_cost": True}, None)
    # P5: sizing from the solved network (installed p_nom).
    st.sections["sizing"] = (
        "ok", report_mod.sizing_summary_from_network(network), None)
    # P5: TEA/LCOE from cost ÷ (demand − ENS).
    ens_for_tea = float(ens_mwh) if ens_mwh is not None else None
    served = report_mod.served_energy_mwh_from_network(
        network, ens_mwh=ens_for_tea)
    if served is None and isinstance(metrics, dict):
        dem = metrics.get("demand_mwh")
        ens = metrics.get("ens_mwh")
        if dem is not None and ens is not None:
            served = max(0.0, float(dem) - float(ens))
    if served is None and ens_cap is not None \
            and cap_mwh and float(cap_mwh) > 0 and ens_mwh is not None:
        demand = float(cap_mwh) / (float(ens_cap) / 1e4)
        served = max(0.0, demand - float(ens_mwh))
    tea_block = report_mod.compute_tea(
        cost_eur=st.cost_at_target, served_energy_mwh=served)
    if tea_block.lcoe_eur_per_mwh is not None:
        st.sections["tea"] = ("ok", tea_block.model_dump(mode="json"), None)
    else:
        st.sections["tea"] = ("not_established",
                              tea_block.model_dump(mode="json"),
                              tea_block.notes)
    # P6(a): dedicated-bus multi-energy ENS disclosure.
    from services.adequacy import multi_energy as ME
    capture = sink.get("last_lost_load")
    if not isinstance(capture, dict):
        capture = {}
    st.sections["multi_energy"] = ME.multi_energy_section_from_capture(
        network, capture)


def _stage_mc_certify(st: _Study) -> None:
    """MC LOLE certification of the ENS plan (spec decisions 1–2, §4 P11).

    Samples the SOLVED plan (extendables at ``p_nom_opt``) on a hub-boundary
    copy — the MC is copper-plate, so the far side of the import Links must
    not count as local capacity. Charges no LP solve. Verdict (Q1): pass iff
    CI upper ≤ target, fail iff CI lower > target, else inconclusive; target
    below the resolution floor → inconclusive. Target basis (Q2): h/yr ×
    horizon_years, refused when the horizon is shorter than the largest MTTR.
    """
    from services.adequacy import mc as mc_mod

    pack = st.pack
    target = pack.availability.target_lole_h

    def _not_established(reason: str, payload: dict | None = None) -> None:
        st.mark("mc_certify", "skipped", note=reason)
        st.sections["certification"] = ("not_established", payload, reason)

    try:
        mc_net, boundary = arch.hub_boundary_copy(st.network, pack)
    except arch.HubBoundaryError as exc:
        return _not_established(str(exc))
    try:
        with st.lock:
            inputs = mc_mod.snapshot_inputs(mc_net, cfg=st.cfg)
    except ValueError as exc:
        return _not_established(f"MC snapshot refused: {exc}",
                                {"fleet_boundary": boundary})
    if not inputs.units:
        return _not_established(
            "nothing to sample: no hub-side electrical generator carries "
            "resolvable occurrence data (unavailability + MTTR) — an empty "
            "fleet says nothing about the system", {"fleet_boundary": boundary})
    for u in inputs.units:
        try:
            mc_mod.transition_probs(u.q, u.mttr_hours, name=u.name)
        except ValueError as exc:
            return _not_established(str(exc), {"fleet_boundary": boundary})
    horizon_years = float(inputs.nyears)
    if not horizon_years > 0:
        return _not_established(
            "horizon_years ≤ 0 — the modelled horizon has no length, so no "
            "annual LOLE can be stated", {"fleet_boundary": boundary})
    modelled_h = horizon_years * 8760.0
    mttrs = [float(u.mttr_hours) for u in inputs.units
             if math.isfinite(float(u.mttr_hours))]
    max_mttr = max(mttrs) if mttrs else 0.0
    if modelled_h < max_mttr:
        return _not_established(
            f"modelled horizon {modelled_h:g} h is shorter than the largest "
            f"unit MTTR ({max_mttr:g} h): one repair outlasts the study, so "
            "its LOLE cannot stand for annual adequacy (decision Q2)",
            {"fleet_boundary": boundary, "horizon_years": horizon_years,
             "max_mttr_hours": max_mttr})

    res = mc_mod.mc_adequacy(
        inputs, draws=st.mc_draws, seed=st.mc_seed,
        cov_target=st.mc_cov_target, stop_event=st.stop_event)
    if st.stop_event.is_set():
        st.aborted = True
        st.mark("mc_certify", "aborted", note="MC stopped by abort — no verdict")
        st.sections["certification"] = (
            "not_established", None, "MC aborted — no verdict")
        return

    lole = float(res["lole_hours"])
    lo, hi = (float(x) for x in res["lole_ci"])
    floor = res.get("resolution_floor_h")
    verdict = met = confident = target_h = None
    note = None
    if target is not None:
        target_h = float(target) * horizon_years
        met = lole <= target_h
        confident = hi <= target_h
        if floor is not None and target_h < float(floor):
            verdict = "inconclusive"
            note = (f"target {target_h:g} h over the horizon is below the MC "
                    f"resolution floor {float(floor):g} h at "
                    f"{res['n_samples']} draws — more draws needed")
        elif hi <= target_h:
            verdict = "pass"
        elif lo > target_h:
            verdict = "fail"
        else:
            verdict = "inconclusive"
            note = "the LOLE 95% CI straddles the target"
    else:
        note = "no target_lole_h — LOLE reported, not certified"
    dsr_on = bool(getattr(st.cfg, "dsr_buses", None)) and float(
        getattr(st.cfg, "dsr_price_eur_per_mwh", 0.0) or 0.0) > 0
    payload = {
        "metric": "mc_lole",
        "target_lole_h": target,
        "target_basis": "h_per_year",
        "target_lole_h_per_horizon": target_h,
        "horizon_years": horizon_years,
        "lole_h_per_horizon": lole,
        "lole_h_per_year": lole / horizon_years,
        "lole_ci": [lo, hi],
        "eue_mwh": res.get("eue_mwh"),
        "eue_ci": list(res.get("eue_ci") or []),
        "by_period": res.get("by_period"),
        "n_samples": res.get("n_samples"),
        "converged": res.get("converged"),
        "draws": st.mc_draws,
        "seed": st.mc_seed,
        "cov_target": st.mc_cov_target,
        "resolution_floor_h": floor,
        "time_basis": res.get("time_basis"),
        "warning": res.get("warning"),
        "fleet_boundary": boundary,
        "verdict": verdict,
        "met_on_mean": met,
        "confident": confident,
        "dsr_note": (
            "the LP plan uses demand response but the MC does not model it — "
            "this LOLE is pessimistic relative to the plan" if dsr_on else None),
        "solves_charged": 0,
    }
    st.mc_lole_h = lole / horizon_years
    st.certified = (verdict == "pass") if verdict is not None else None
    st.sections["certification"] = ("ok", payload, note)
    st.mark("mc_certify", "run", solves_charged=0,
            note=f"verdict {verdict}" if verdict else note)


def _derive_dtc_config(st: _Study, error_cls, stage: str):
    """Minimal DtcConfig from import Links + ``eh_critical`` bus tags."""
    from models.energy_hub import DtcConfig

    if st.dtc_config is not None:
        return st.dtc_config
    network = st.network
    links = arch.select_import_links(network, st.pack.import_overlay)
    crit_buses: list[str] = []
    if network.buses is not None and "eh_critical" in getattr(
            network.buses, "columns", []):
        crit_buses = [
            str(b) for b in network.buses.index
            if network.buses.at[b, "eh_critical"] is True
            or str(network.buses.at[b, "eh_critical"]).lower()
            in ("true", "1", "yes")
        ]
    if not links or not crit_buses:
        raise error_cls(
            f"{stage} requested but no import Links / critical buses "
            "resolved; pass dtc_config")
    return DtcConfig(critical_bus_ids=crit_buses,
                     islanding_contingencies=list(links))


def _stage_redundancy(st: _Study) -> None:
    from services.adequacy import redundancy as red
    try:
        table = red.compare_redundancy_scenarios(
            st.network, st.cfg, lock=st.lock, stop_event=st.stop_event,
            log_queue=st.log_queue, scenarios=None,
            availability=st.pack.availability, store=st.store,
            pack_hash=st.pack_h, assumptions_hash=_assumptions_hash(st.cfg),
            max_solves=st.remaining(),
        )
        n_attempted = int(table.get("solves_attempted") or 0)
        sec_status, sec_note = red.redundancy_section_status(table)
        st.mark("redundancy", "run", solves_charged=n_attempted,
                note=sec_note or f"{n_attempted} solves")
        st.sections["redundancy"] = (sec_status, table, sec_note)
        st.solves += n_attempted
    except Exception as exc:
        logger.exception("redundancy compare failed")
        st.mark("redundancy", "aborted", note=str(exc))
        st.sections["redundancy"] = ("not_established", None, str(exc))


def _stage_levers(st: _Study) -> None:
    from services.adequacy import levers as lev
    pack = st.pack
    try:
        kinds = []
        if pack.levers.storage_duration:
            kinds.append("storage_duration")
        if pack.levers.import_cap:
            kinds.append("import_cap")
        if not kinds:
            kinds = ["storage_duration"]
        # Primary kind first; merge options if both enabled.
        merged = None
        attempted = 0
        skipped_kinds: list[str] = []
        applicable_kinds: list[str] = []
        budget_exhausted = False
        for kind in kinds:
            if st.budget_solves - st.solves - attempted <= 0:
                budget_exhausted = True
                break
            try:
                table = lev.compare_lever_scenarios(
                    st.network, st.cfg, lock=st.lock, stop_event=st.stop_event,
                    log_queue=st.log_queue, kind=kind, values=None,
                    availability=pack.availability, store=None,
                    pack_hash=st.pack_h,
                    assumptions_hash=_assumptions_hash(st.cfg),
                    max_solves=st.budget_solves - st.solves - attempted,
                )
            except lev.LeverScenarioError as exc:
                msg = str(exc)
                # Soft-skip only asset-absence — config/unknown-kind errors
                # must fail closed with the original message.
                if not ("no StorageUnits" in msg or "no import Links" in msg):
                    raise
                skipped_kinds.append(f"{kind}:{exc}")
                logger.info("lever kind %s not applicable: %s", kind, exc)
                continue
            applicable_kinds.append(kind)
            attempted += int(table.get("solves_attempted") or 0)
            budget_exhausted = (budget_exhausted
                                or bool(table.get("budget_exhausted")))
            if merged is None:
                merged = table
            else:
                merged = {
                    **table,
                    "kind": "+".join(applicable_kinds),
                    "options": list(merged.get("options") or [])
                    + list(table.get("options") or []),
                    "solves_attempted": attempted,
                    "comparable_solved": int(merged.get("comparable_solved") or 0)
                    + int(table.get("comparable_solved") or 0),
                }
        # Always surface soft-skips on the payload (even if store is None or
        # every kind was inapplicable).
        if merged is None:
            merged = {"kind": "+".join(kinds), "options": [],
                      "solves_attempted": 0, "comparable_solved": 0,
                      "aborted": False}
        merged = {**merged, "budget_exhausted": budget_exhausted}
        if skipped_kinds:
            merged = {**merged, "skipped_kinds": list(skipped_kinds)}
        if st.store is not None:
            st.store["eh_lever_comparison"] = merged
        sec_status, sec_note = lev.levers_section_status(merged)
        if skipped_kinds and not applicable_kinds:
            skip_note = "all lever kinds inapplicable: " + "; ".join(skipped_kinds)
            sec_status, sec_note = "not_established", skip_note
            st.mark("levers", "skipped", note=skip_note)
        else:
            note = sec_note or f"{attempted} solves"
            if skipped_kinds:
                note = f"{note}; soft-skipped " + ", ".join(skipped_kinds)
            st.mark("levers", "run", solves_charged=attempted, note=note)
        st.sections["levers"] = (sec_status, merged, sec_note)
        st.solves += attempted
    except Exception as exc:
        logger.exception("lever compare failed")
        st.mark("levers", "aborted", note=str(exc))
        st.sections["levers"] = ("not_established", None, str(exc))


def _stage_dtc_stress(st: _Study) -> None:
    from services.adequacy import dtc as dtc_mod
    try:
        cfg_dtc = _derive_dtc_config(st, dtc_mod.DtcStressError, "dtc_stress")
        table = dtc_mod.run_dtc_stress(
            st.network, st.cfg, lock=st.lock, stop_event=st.stop_event,
            log_queue=st.log_queue, dtc=cfg_dtc, store=st.store,
            pack_hash=st.pack_h, assumptions_hash=_assumptions_hash(st.cfg),
            max_solves=st.remaining(),
        )
        n_attempted = int(table.get("solves_attempted") or 0)
        sec_status, sec_note = dtc_mod.dtc_section_status(table)
        st.mark("dtc_stress", "run", solves_charged=n_attempted,
                note=sec_note or f"{n_attempted} solves")
        st.sections["dtc"] = (sec_status, table, sec_note)
        st.solves += n_attempted
    except Exception as exc:
        logger.exception("DtC stress failed")
        st.mark("dtc_stress", "aborted", note=str(exc))
        st.sections["dtc"] = ("not_established", None, str(exc))


def _stage_dtc_planning(st: _Study) -> None:
    from services.adequacy import dtc as dtc_mod
    try:
        cfg_dtc = _derive_dtc_config(st, dtc_mod.DtcPlanningError,
                                     "dtc_planning")
        table = dtc_mod.run_dtc_planning(
            st.network, st.cfg, lock=st.lock, stop_event=st.stop_event,
            log_queue=st.log_queue, dtc=cfg_dtc, store=st.store,
            pack_hash=st.pack_h, assumptions_hash=_assumptions_hash(st.cfg),
            max_solves=st.remaining(),
        )
        n_attempted = int(table.get("solves_attempted") or 0)
        sec_status, sec_note = dtc_mod.dtc_planning_section_status(table)
        st.mark("dtc_planning", "run", solves_charged=n_attempted,
                note=sec_note or f"{n_attempted} solves")
        # Merge with an existing dtc stress payload when present.
        prev = st.sections.get("dtc")
        if prev and isinstance(prev[1], dict) \
                and prev[1].get("mode") in ("stress", "stress_fixed_plan"):
            merged = {"mode": "stress+planning", "stress": prev[1],
                      "planning": table,
                      "attribution": "bus_aggregate_not_per_load"}
            st.sections["dtc"] = (sec_status, merged, sec_note)
        else:
            st.sections["dtc"] = (sec_status, table, sec_note)
        st.solves += n_attempted
    except Exception as exc:
        logger.exception("DtC planning failed")
        st.mark("dtc_planning", "aborted", note=str(exc))
        st.sections.setdefault("dtc", ("not_established", None, str(exc)))


# Executable stages, looked up at call time. `frontier` / `fmea_top` are not
# executed yet (P12) and are recorded as skipped with a reason.
_STAGE_HANDLERS = {
    "apply_pack": _stage_apply_pack,
    "ens_solve": _stage_ens_solve,
    "mc_certify": _stage_mc_certify,
    "redundancy": _stage_redundancy,
    "levers": _stage_levers,
    "dtc_stress": _stage_dtc_stress,
    "dtc_planning": _stage_dtc_planning,
}


def run_eh_study(
    network,
    pack: ArchetypePack,
    cfg,
    *,
    lock,
    stop_event: threading.Event,
    log_queue: queue.Queue | None = None,
    stages: Iterable[str] | None = None,
    budget_solves: int = DEFAULT_EH_BUDGET_SOLVES,
    state_update=None,
    store: dict | None = None,
    dtc_config=None,
    dsr_buses: list[str] | None = None,
    mc_draws: int = DEFAULT_MC_DRAWS,
    mc_seed: int = DEFAULT_MC_SEED,
    mc_cov_target: float = DEFAULT_MC_COV_TARGET,
) -> ReferenceDesignReport:
    """Synchronous EH study driver (HTTP worker: ``eh_study_runner``).

    Stages execute in spec decision-18 order (``EH_PIPELINE_STAGES``) from
    the stage table ``_STAGE_HANDLERS``.

    If ``store`` is provided, the finished report is persisted under
    ``eh_reference_design_report`` for ``GET /results/eh_reference_design``,
    and a redundancy stage also writes ``eh_redundancy_comparison`` for
    ``GET /results/eh_redundancy``. Sibling tables from a previous study are
    cleared at start.

    Isolation: the study runs on a private copy of ``network`` and of ``cfg``.
    The pack's ENS target is authoritative for every stage (the report header
    states it), the caller's ``cfg`` is never mutated, and solve side-results
    are never published through ``state_update`` — the foreground results keep
    describing the user's own solve (same rule as frontier ``_restore_base``).
    ``state_update`` is accepted for runner symmetry only.

    ``dsr_buses`` opts buses into the DSR tier for packs with
    ``dsr_opt_in`` (decision 15) through the double-count preflight; the
    preflight's warnings land on the ``apply_pack`` note and ``notes``.

    ``budget_solves`` is a ceiling on LP solves: multi-solve stages receive
    the remaining budget and a stage that would start with none left is
    skipped. Zero-solve stages (``mc_certify``) are never budget-skipped.

    ``mc_draws`` / ``mc_seed`` / ``mc_cov_target`` configure the
    ``mc_certify`` stage (the MC's own draw cap applies).

    Stage list vs ``pack.levers.redundancy`` (P3a binding condition):
    - Explicit ``stages=...`` wins: including ``\"redundancy\"`` runs the
      compare even when ``levers.redundancy`` is False.
    - Default pipeline (``stages is None``): ``redundancy`` is dropped unless
      ``pack.levers.redundancy`` is True. MVP-A packs ship False and therefore
      skip redundancy unless the caller opts in via levers or stages.
    """
    from services.adequacy.redundancy import _detach_solver_model
    from services.solver_service import SolverConfig

    requested_explicit = validate_stages(stages)
    requested = (requested_explicit if requested_explicit is not None
                 else default_stages_for(pack))
    budget_solves = max(1, min(int(budget_solves), MAX_EH_BUDGET_SOLVES))
    log_queue = log_queue or queue.SimpleQueue()

    if store is not None:
        for key in SIBLING_STORE_KEYS + (report_mod.EH_REPORT_STORE_KEY,):
            store.pop(key, None)

    # Private network: pack overlay + ENS solve never touch the shared one.
    # Copied under the network's lock so an edit in flight cannot tear it;
    # every later read (including the DSR preflight) is of the copy.
    with lock:
        _detach_solver_model(network)
        network = network.copy()
    # Private cfg: the pack target wins for every stage, the session config
    # is left exactly as the user set it.
    cfg = copy.copy(cfg) if cfg is not None else SolverConfig()
    patch, pack_notes = arch.solver_config_patch_with_preflight(
        pack, network=network, dsr_buses=dsr_buses)
    for k, v in patch.items():
        try:
            setattr(cfg, k, v)
        except Exception:
            pass

    records: list[PipelineStageRecord] = []
    for name in DEFAULT_STAGES:
        if name not in requested:
            note = None
            if name == "mc_certify" and certification_wanted(pack):
                note = "required by pack but not requested — not_established"
            records.append(PipelineStageRecord(
                stage=name, status="skipped", note=note))  # type: ignore[arg-type]
        elif name not in _STAGE_HANDLERS and name != "assemble":
            note = f"{name} not implemented in the sync driver yet"
            if name == "fmea_top":
                note = FMEA_TOP_LINK_PRIMARY_NOTE + f"; {note}"
            records.append(PipelineStageRecord(
                stage=name, status="skipped", note=note))  # type: ignore[arg-type]
        else:
            records.append(PipelineStageRecord(stage=name, status="pending"))  # type: ignore[arg-type]

    st = _Study(
        network=network, pack=pack, cfg=cfg, lock=lock, stop_event=stop_event,
        log_queue=log_queue, store=store, dtc_config=dtc_config,
        requested=requested, records=records, budget_solves=budget_solves,
        pack_notes=pack_notes, mc_draws=int(mc_draws), mc_seed=int(mc_seed),
        mc_cov_target=float(mc_cov_target),
        sections={}, solves=0, aborted=False, failed_reason=None,
        undo=lambda: None, pack_h=arch.pack_hash(pack),
        ens_cap=pack.availability.ens_cap_permyriad,
        achieved_ens_permyriad=None, achieved_shed_hours=None,
        cost_at_target=None, period_basis=None, adequacy_report=None,
        mc_lole_h=None, certified=None, executed=[],
    )
    sections = st.sections
    gates_obj = None

    try:
        for stage in EH_PIPELINE_STAGES:
            if stage == "assemble" or st.aborted:
                break
            if stage not in requested:
                continue
            handler = _STAGE_HANDLERS.get(stage)
            if handler is None:
                continue                      # recorded skipped above
            if stage not in REQUIRED_STAGES and st.blocked(stage):
                continue
            if stop_event.is_set():
                st.aborted = True
                st.mark(stage, "aborted")
                break
            st.executed.append(stage)
            handler(st)

        if "ens_solve" not in requested:  # unreachable via validate_stages
            for sec in ("target", "cost", "multi_energy"):
                sections.setdefault(sec, ("not_established", None,
                                          "ens_solve not run"))

        # Not-yet-executable stages → section skipped (never pending).
        for optional in ("frontier", "fmea_top"):
            if optional in _STAGE_HANDLERS:
                continue
            if optional == "fmea_top":
                reason = FMEA_TOP_LINK_PRIMARY_NOTE + (
                    "; stage not implemented in sync driver"
                    if optional in requested else "; stage not requested")
            else:
                reason = (f"{optional} not implemented in sync driver"
                          if optional in requested
                          else f"{optional} not requested")
            sections.setdefault(optional, ("skipped", None, reason))

        if "redundancy" not in requested:
            sections.setdefault(
                "redundancy", ("skipped", None, "redundancy not requested"))
        if "levers" not in requested:
            sections.setdefault("levers", ("skipped", None, "levers not requested"))
        if "dtc_stress" not in requested and "dtc_planning" not in requested:
            sections.setdefault(
                "dtc", ("skipped", None, "dtc_stress/dtc_planning not requested"))
        if "mc_certify" not in requested:
            if certification_wanted(pack):
                sections.setdefault("certification", (
                    "not_established", None,
                    "MC certification required by the pack but not requested"))
            else:
                sections.setdefault("certification", (
                    "skipped", None, "no LOLE target — certification not requested"))

        if pack.archetype == "weak_flexible":
            # P9 thin SCR warn-only gate (feasibility flag, not co-opt).
            # Orthogonal to mc_certify: certification has its own section.
            from services.adequacy import scr_gate as scr_gate_mod
            gate_block, gate_status, gate_payload, gate_note = (
                scr_gate_mod.evaluate_network_scr_gate(network))
            sections["gates"] = (gate_status, gate_payload, gate_note)
            if gate_block is not None:
                gates_obj = gate_block
        else:
            sections.setdefault("gates", (
                "skipped", None,
                "SCR gate not required for this archetype (P9 thin slice "
                "is weak_flexible only)"))

        sections.setdefault(
            "tea", ("skipped", None, "TEA not produced (ens_solve did not run)"))
        sections.setdefault("multi_energy", (
            "skipped", None, "multi_energy not produced (ens_solve did not run)"))

        for rec in records:
            if rec.status == "pending" and rec.stage != "assemble":
                reason = (
                    "not reached: study aborted" if st.aborted
                    else f"not run: {st.failed_reason}" if st.failed_reason
                    else "not reached")
                rec.status = "skipped"  # type: ignore[assignment]
                rec.note = reason
                section = _STAGE_SECTION.get(rec.stage)
                if section is not None:
                    sections.setdefault(section, ("not_established", None, reason))

        if "assemble" in requested and not (
                st.aborted and st.adequacy_report is None):
            st.mark("assemble", "run")
        elif "assemble" in requested:
            st.mark("assemble", "aborted", note="no fragments to assemble")

    finally:
        try:
            st.undo()
        except Exception:
            logger.exception("EH pack undo failed")

    tea_obj = None
    tea_sec = sections.get("tea")
    if tea_sec and tea_sec[0] == "ok" and isinstance(tea_sec[1], dict):
        from models.energy_hub import TeaBlock
        tea_obj = TeaBlock.model_validate(tea_sec[1])

    pipeline = report_mod.pipeline_from_records(
        records, budget_solves=budget_solves, solves_consumed=st.solves,
        aborted=st.aborted)

    report = report_mod.assemble_reference_design_report(
        archetype=pack.archetype,
        pack_hash=st.pack_h,
        assumptions_hash=_assumptions_hash(cfg),
        section_payloads=sections,
        pipeline=pipeline,
        ens_cap_permyriad=st.ens_cap,
        achieved_ens_permyriad=st.achieved_ens_permyriad,
        achieved_shed_hours=st.achieved_shed_hours,
        mc_lole_h=st.mc_lole_h,
        certified=st.certified,
        cost_at_target_eur=st.cost_at_target,
        period_basis=st.period_basis,
        tea=tea_obj,
        gates=gates_obj,
        notes=pack_notes,
    )
    if store is not None:
        report_mod.store_eh_report(store, report)
    return report
