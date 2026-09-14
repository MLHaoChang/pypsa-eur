"""
Assemble ``ReferenceDesignReport`` — the only report builder for EH studies.

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §4–§5
decision 16: EHStudyRunner emits reports only through this function.

Phase 5 adds sizing summary, TEA/LCOE wrap, stable export, and store/GET helpers.
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
    TeaBlock,
)

EH_REPORT_STORE_KEY = "eh_reference_design_report"

EXPORT_KEYS: tuple[str, ...] = (
    "archetype",
    "pack_hash",
    "assumptions_hash",
    "ens_cap_permyriad",
    "achieved_ens_permyriad",
    "achieved_shed_hours",
    "mc_lole_h",
    "cost_at_target_eur",
    "period_basis",
    "excludes_shed_cost",
    "completeness",
    "sections",
    "pipeline",
    "tea",
    "gates",
)


def sizing_summary_from_network(n) -> dict[str, Any]:
    """Installed p_nom by generator carrier (MVP-A sizing section)."""
    by_carrier: dict[str, float] = {}
    total = 0.0
    if n.generators is not None and not n.generators.empty:
        for name, row in n.generators.iterrows():
            # Skip VOLL / DSR slack generators if tagged.
            carrier = str(row.get("carrier", "") or "")
            if str(name).startswith("__voll") or carrier in {
                    "voll", "load_shedding", "dsr", "demand_response"}:
                continue
            try:
                p_nom = float(row.get("p_nom", 0.0) or 0.0)
            except (TypeError, ValueError):
                p_nom = 0.0
            # Prefer optimised capacity when present and finite.
            if "p_nom_opt" in n.generators.columns:
                try:
                    opt = float(row.get("p_nom_opt"))
                    if opt == opt and opt > 0:  # not NaN
                        p_nom = opt
                except (TypeError, ValueError):
                    pass
            key = carrier or "unknown"
            by_carrier[key] = by_carrier.get(key, 0.0) + p_nom
            total += p_nom
    return {
        "by_carrier": {k: float(v) for k, v in sorted(by_carrier.items())},
        "total_p_nom_mw": float(total),
    }


def served_energy_mwh_from_network(n, *, ens_mwh: float | None = None) -> float | None:
    """Weighted electrical demand less ENS (served energy for LCOE).

    Prefer ``loads_t.p_set`` (demand) over ``loads_t.p`` (which can include
    shed-as-dispatch artefacts). Subtract ``ens_mwh`` when provided so LCOE
    is cost / *served* energy, not cost / gross demand.
    """
    if n.loads is None or n.loads.empty:
        return None
    weights = n.snapshot_weightings.generators
    demand: float | None = None
    if hasattr(n.loads_t, "p_set") and n.loads_t.p_set is not None \
            and not n.loads_t.p_set.empty:
        demand = float((n.loads_t.p_set.mul(weights, axis=0)).sum().sum())
    elif "p_set" in n.loads.columns:
        hours = float(weights.sum()) if weights is not None else float(len(n.snapshots))
        demand = float(n.loads["p_set"].fillna(0).sum()) * hours
    elif hasattr(n.loads_t, "p") and n.loads_t.p is not None and not n.loads_t.p.empty:
        # Last resort — may not distinguish shed; caller should pass ens_mwh.
        demand = float((n.loads_t.p.clip(lower=0).mul(weights, axis=0)).sum().sum())
    if demand is None or demand <= 0:
        return None
    if ens_mwh is not None:
        return max(0.0, float(demand) - float(ens_mwh))
    return float(demand)


def compute_tea(*, cost_eur: float | None,
                served_energy_mwh: float | None) -> TeaBlock:
    """Post-process LCOE from existing cost + energy — no second cost engine."""
    if cost_eur is None or served_energy_mwh is None or served_energy_mwh <= 0:
        return TeaBlock(
            notes="LCOE not established: need cost_at_target_eur and served energy > 0")
    return TeaBlock(
        lcoe_eur_per_mwh=float(cost_eur) / float(served_energy_mwh),
        notes="LCOE = cost_at_target_eur / served_energy_mwh (ex-shed cost)",
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
    tea: TeaBlock | None = None,
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
        tea=tea,
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


def export_reference_design(report: ReferenceDesignReport) -> dict[str, Any]:
    """Stable-key export for golden fixtures / CSV/JSON download."""
    raw = report.model_dump(mode="json")
    return {k: raw.get(k) for k in EXPORT_KEYS}


def store_eh_report(store: dict, report: ReferenceDesignReport) -> None:
    store[EH_REPORT_STORE_KEY] = report.model_dump(mode="json")


def load_eh_report(store: dict) -> ReferenceDesignReport | None:
    raw = store.get(EH_REPORT_STORE_KEY)
    if not raw:
        return None
    if isinstance(raw, ReferenceDesignReport):
        return raw
    return ReferenceDesignReport.model_validate(raw)


def eh_reference_design_http_payload(store: dict) -> tuple[dict | None, int]:
    """Return (body, status) for GET /results/eh_reference_design."""
    report = load_eh_report(store)
    if report is None:
        return None, 204
    return export_reference_design(report), 200
