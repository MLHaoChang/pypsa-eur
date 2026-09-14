"""
Energy Hub study orchestrator (Phase 1.5).

Runs archetype pack → ENS solve → assemble. Optional stages may be skipped;
required-but-missing stages surface as ``not_established`` / pipeline notes.

Report emission: ``assemble_reference_design_report`` only (spec decision 16).
"""
from __future__ import annotations

import hashlib
import json
import logging
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

# Re-export for tests / callers.
__all__ = [
    "DEFAULT_STAGES",
    "DEFAULT_EH_BUDGET_SOLVES",
    "MAX_EH_BUDGET_SOLVES",
    "run_eh_study",
]


def _assumptions_hash(cfg) -> str:
    raw = {
        "voll": getattr(cfg, "voll", None),
        "ens_cap_permyriad": getattr(cfg, "ens_cap_permyriad", None),
        "dsr_buses": list(getattr(cfg, "dsr_buses", None) or []),
    }
    blob = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
) -> ReferenceDesignReport:
    """Synchronous EH study driver (HTTP worker wraps this later)."""
    from services.solver_service import run_simulation

    requested = tuple(stages) if stages is not None else DEFAULT_STAGES
    budget_solves = max(1, min(int(budget_solves), MAX_EH_BUDGET_SOLVES))
    log_queue = log_queue or queue.SimpleQueue()
    state_update = state_update or (lambda **kw: None)

    # Stages this driver can actually execute today (MVP-A sync slice).
    IMPLEMENTED = frozenset({"apply_pack", "ens_solve", "assemble"})

    records: list[PipelineStageRecord] = []
    for name in DEFAULT_STAGES:
        if name not in requested:
            note = None
            if name == "mc_certify" and pack.mc_certify_required:
                note = "required by pack but not requested — not_established"
            records.append(PipelineStageRecord(
                stage=name, status="skipped", note=note))  # type: ignore[arg-type]
        elif name not in IMPLEMENTED:
            note = f"{name} not implemented in P1.5 sync driver"
            if name == "mc_certify" and pack.mc_certify_required:
                note = "required by pack but not implemented — not_established"
            records.append(PipelineStageRecord(
                stage=name, status="skipped", note=note))  # type: ignore[arg-type]
        else:
            records.append(PipelineStageRecord(stage=name, status="pending"))  # type: ignore[arg-type]

    section_payloads: dict[str, tuple] = {}
    solves = 0
    aborted = False
    undo = lambda: None  # noqa: E731
    pack_h = arch.pack_hash(pack)
    ens_cap = pack.availability.ens_cap_permyriad
    achieved_ens_permyriad = None
    achieved_shed_hours = None
    cost_at_target = None
    period_basis = None
    adequacy_report: dict[str, Any] | None = None

    def _mark(stage: str, status: str, *, note: str | None = None,
              solves_charged: int = 0) -> None:
        for rec in records:
            if rec.stage == stage:
                rec.status = status  # type: ignore[assignment]
                rec.note = note
                rec.solves_charged = solves_charged
                break

    try:
        if "apply_pack" in requested:
            if stop_event.is_set():
                aborted = True
                _mark("apply_pack", "aborted")
            else:
                result = arch.apply_archetype_pack_detailed(network, pack)
                undo = result.undo
                pack_h = result.pack_hash
                _mark("apply_pack", "run",
                      note="; ".join(result.warnings) or None)

        if not aborted and "ens_solve" in requested:
            if stop_event.is_set():
                aborted = True
                _mark("ens_solve", "aborted")
            else:
                # Merge pack solver patch onto cfg fields we care about.
                patch = arch.solver_config_patch(pack)
                for k, v in patch.items():
                    if getattr(cfg, k, None) is None:
                        try:
                            setattr(cfg, k, v)
                        except Exception:
                            pass
                sink: dict = {}
                status, condition = run_simulation(
                    cfg, network, lock, stop_event, log_queue,
                    state_update=lambda **kw: (sink.update(kw),
                                               state_update(**kw)),
                )
                solves += 1
                if status not in ("ok", "optimal"):
                    _mark("ens_solve", "aborted",
                          note=f"{status}:{condition}", solves_charged=1)
                    aborted = True
                else:
                    _mark("ens_solve", "run", solves_charged=1)
                    adequacy_report = sink.get("adequacy_report")
                    if isinstance(adequacy_report, dict):
                        tgt = adequacy_report.get("target") or {}
                        system = tgt.get("system") or {}
                        metrics = adequacy_report.get("metrics") or {}
                        cost = adequacy_report.get("cost") or {}
                        achieved_shed_hours = system.get("achieved_shed_hours")
                        ens_mwh = system.get("achieved_ens_mwh")
                        # Invert ‱ if demand known from cap_mwh.
                        cap_mwh = system.get("cap_mwh")
                        if (ens_cap is not None and cap_mwh
                                and float(cap_mwh) > 0 and ens_mwh is not None):
                            # achieved ‱ ≈ ens_mwh / demand * 1e4;
                            # demand = cap_mwh / (ens_cap/1e4)
                            demand = float(cap_mwh) / (float(ens_cap) / 1e4)
                            achieved_ens_permyriad = (
                                float(ens_mwh) / demand * 1e4 if demand else None)
                        section_payloads["target"] = (
                            "ok",
                            {"binding": tgt.get("binding"),
                             "system": system, "metrics": metrics},
                            None,
                        )
                        cost_at_target = cost.get("total_system_cost_eur")
                        period_basis = cost.get("period_basis")
                        section_payloads["cost"] = (
                            "ok",
                            {"total_system_cost_eur": cost_at_target,
                             "period_basis": period_basis,
                             "excludes_shed_cost": True},
                            None,
                        )
                        # Sizing best-effort placeholder until P5 deepens it.
                        section_payloads["sizing"] = (
                            "not_established", None,
                            "sizing summary deferred to P5 enrichment")
        elif "ens_solve" not in requested:
            section_payloads.setdefault(
                "target", ("not_established", None, "ens_solve not run"))
            section_payloads.setdefault(
                "cost", ("not_established", None, "ens_solve not run"))

        # Optional / not-yet-implemented stages → section skipped (never pending).
        for optional, section in (
            ("frontier", "frontier"),
            ("redundancy", "redundancy"),
            ("levers", "levers"),
            ("dtc_stress", "dtc"),
            ("fmea_top", "fmea_top"),
        ):
            if optional not in IMPLEMENTED:
                reason = (
                    f"{optional} not implemented in P1.5 sync driver"
                    if optional in requested
                    else f"{optional} not requested"
                )
                section_payloads.setdefault(
                    section, ("skipped", None, reason))

        if pack.mc_certify_required and "mc_certify" not in IMPLEMENTED:
            section_payloads["gates"] = (
                "not_established", None,
                "mc_certify required by pack but not implemented in P1.5",
            )
        elif pack.mc_certify_required and "mc_certify" not in requested:
            section_payloads["gates"] = (
                "not_established", None,
                "mc_certify required by pack but not requested",
            )
        else:
            section_payloads.setdefault(
                "gates", ("skipped", None, "gates/SCR not in P1.5 MVP-A"))

        section_payloads.setdefault(
            "tea", ("skipped", None, "TEA wrap is P5"))
        section_payloads.setdefault(
            "fmea_top", section_payloads.get(
                "fmea_top", ("skipped", None, "fmea_top not requested")))

        if "assemble" in requested and not (aborted and adequacy_report is None):
            _mark("assemble", "run")
        elif "assemble" in requested:
            _mark("assemble", "aborted", note="no fragments to assemble")

    finally:
        try:
            undo()
        except Exception:
            logger.exception("EH pack undo failed")

    pipeline = report_mod.pipeline_from_records(
        records, budget_solves=budget_solves, solves_consumed=solves,
        aborted=aborted)

    return report_mod.assemble_reference_design_report(
        archetype=pack.archetype,
        pack_hash=pack_h,
        assumptions_hash=_assumptions_hash(cfg),
        section_payloads=section_payloads,
        pipeline=pipeline,
        ens_cap_permyriad=ens_cap,
        achieved_ens_permyriad=achieved_ens_permyriad,
        achieved_shed_hours=achieved_shed_hours,
        cost_at_target_eur=cost_at_target,
        period_basis=period_basis,
    )
