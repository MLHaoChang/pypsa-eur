"""
Assemble ``ReferenceDesignReport`` — the only report builder for EH studies.

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §4–§5
decision 16: EHStudyRunner emits reports only through this function.
"""
from __future__ import annotations

from typing import Any

from models.energy_hub import (
    REPORT_SECTIONS,
    EHStudyPipeline,
    PipelineStageRecord,
    ReferenceDesignReport,
    SectionState,
    SectionStatus,
)


def assemble_reference_design_report(
    *,
    archetype: str,
    pack_hash: str,
    assumptions_hash: str,
    section_payloads: dict[str, tuple[SectionStatus, dict[str, Any] | None, str | None]],
    pipeline: EHStudyPipeline,
    ens_cap_permyriad: float | None = None,
    achieved_ens_permyriad: float | None = None,
    achieved_shed_hours: float | None = None,
    mc_lole_h: float | None = None,
    cost_at_target_eur: float | None = None,
    period_basis: str | None = None,
) -> ReferenceDesignReport:
    """Build the one product artifact from stage fragments + completeness."""
    sections: dict[str, SectionState] = {}
    completeness: dict[str, SectionStatus] = {}
    for name in REPORT_SECTIONS:
        if name in section_payloads:
            status, payload, note = section_payloads[name]
        else:
            status, payload, note = "not_established", None, None
        sections[name] = SectionState(status=status, payload=payload, note=note)
        completeness[name] = status

    return ReferenceDesignReport(
        archetype=archetype,  # type: ignore[arg-type]
        pack_hash=pack_hash,
        assumptions_hash=assumptions_hash,
        ens_cap_permyriad=ens_cap_permyriad,
        achieved_ens_permyriad=achieved_ens_permyriad,
        achieved_shed_hours=achieved_shed_hours,
        mc_lole_h=mc_lole_h,
        cost_at_target_eur=cost_at_target_eur,
        period_basis=period_basis,  # type: ignore[arg-type]
        sections=sections,
        completeness=completeness,
        pipeline=pipeline,
    )


def pipeline_from_records(
        records: list[PipelineStageRecord], *,
        budget_solves: int,
        solves_consumed: int,
        aborted: bool = False) -> EHStudyPipeline:
    return EHStudyPipeline(
        stages=records,
        budget_solves=budget_solves,
        solves_consumed=solves_consumed,
        aborted=aborted,
    )
