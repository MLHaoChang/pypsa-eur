"""
Assemble ``ReferenceDesignReport`` — the only report builder for EH studies.

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §4–§5
decision 16: EHStudyRunner emits reports only through this function.

Phase 5 adds sizing summary, TEA/LCOE wrap, stable export, and store/GET helpers.
"""
from __future__ import annotations

import math
from typing import Any

from models.energy_hub import (
    REPORT_SECTIONS,
    EHStudyPipeline,
    GatesBlock,
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
    "certified",
    "cost_at_target_eur",
    "period_basis",
    "excludes_shed_cost",
    "completeness",
    "sections",
    "pipeline",
    "tea",
    "gates",
    "notes",
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


# The electrolyser-Link carrier tokens ``services.results.lcoh.compute_lcoh``
# filters on (and the frontend's ``isElectrolyzerCarrier``). Read from the
# engine's own frame walk below rather than duplicated as a rule: this module
# only asks "are there any such Links" before spending the engine's time.
def _live_result_df(n, accessor_name: str, attr: str, source: str = "lopf"):
    """``result_df`` for a network with no router state: the live frames.

    ``compute_lcoh`` reads bus duals through ``corrected_marginal_prices``,
    which takes the router's ``_result_df`` (LP-stage snapshot first, live
    network as fallback). Inside the EH study the network IS the just-solved
    plan, so the live frame is the right answer and there is no router state
    to prefer.
    """
    frame = getattr(getattr(n, accessor_name, None), attr, None)
    if frame is None:
        raise KeyError(f"{accessor_name}.{attr} not available")
    return frame


def has_electrolyser_links(n) -> bool:
    """Any Link whose carrier the LCOH engine would price as an electrolyser."""
    links = getattr(n, "links", None)
    if links is None or links.empty or "carrier" not in links.columns:
        return False
    tokens = ("electrol", "p2g", "p2h2", "power-to-h2", "power-to-gas",
              "power2gas", "hydrogen", "h2")
    for c in links["carrier"].astype(str).str.strip().str.lower():
        if any(t in c for t in tokens):
            return True
    return False


def compute_tea(*, cost_eur: float | None,
                served_energy_mwh: float | None,
                network=None, cfg=None) -> TeaBlock:
    """Post-process LCOE (+ optional LCOH) from existing cost + energy — no
    second cost engine (spec decision 9).

    LCOE = ``cost_at_target_eur / served_energy_mwh`` (ex-shed cost). LCOH is
    the fleet ``lcoh_eur_per_kg_h2`` of ``services.results.lcoh.compute_lcoh``
    over the solved network's electrolyser Links, when ``network`` is given
    and has any. ADR-0001: an LCOH that cannot be established is ``None`` +
    ``lcoh_status``/``lcoh_note`` — ``skipped`` when there is no electrolyser
    Link, ``not_established`` when there is one but it produced no H₂ (or the
    engine could not price it). Never 0.
    """
    if cost_eur is None or served_energy_mwh is None or served_energy_mwh <= 0:
        block = TeaBlock(
            notes="LCOE not established: need cost_at_target_eur and served energy > 0")
    else:
        block = TeaBlock(
            lcoe_eur_per_mwh=float(cost_eur) / float(served_energy_mwh),
            notes="LCOE = cost_at_target_eur / served_energy_mwh (ex-shed cost)",
        )
    if network is None:
        return block
    if not has_electrolyser_links(network):
        block.lcoh_status = "skipped"
        block.lcoh_note = "LCOH skipped: the network has no electrolyser Links"
        return block
    try:
        from services.results.lcoh import compute_lcoh
        from services.solver_service import SolverConfig

        payload = compute_lcoh(network, cfg or SolverConfig(),
                               result_df=_live_result_df)
    except Exception as exc:  # noqa: BLE001 — flagged, never zeroed
        block.lcoh_status = "not_established"
        block.lcoh_note = f"LCOH not established: LCOH engine failed ({exc})"
        return block
    total = (payload or {}).get("total") or {}
    value = total.get("lcoh_eur_per_kg_h2")
    if value is None or not math.isfinite(float(value)):
        n_rows = len((payload or {}).get("rows") or [])
        block.lcoh_status = "not_established"
        block.lcoh_note = (
            f"LCOH not established: {n_rows} electrolyser Link(s) but no H₂ "
            "produced in the solved dispatch (Links never consumed)")
        return block
    block.lcoh_eur_per_kg = float(value)
    block.lcoh_status = "ok"
    block.lcoh_note = (
        f"LCOH = fleet (CAPEX + VOM + electricity) / H₂ produced over "
        f"{len(payload.get('rows') or [])} electrolyser Link(s), LHV basis")
    block.notes = f"{block.notes}; {block.lcoh_note}"
    return block


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
    certified: bool | None = None,
    cost_at_target_eur: float | None = None,
    period_basis: str | None = None,
    tea: TeaBlock | None = None,
    gates: GatesBlock | None = None,
    notes: list[str] | None = None,
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
        certified=certified,
        cost_at_target_eur=cost_at_target_eur,
        period_basis=period_basis,  # type: ignore[arg-type]
        sections=sections,
        completeness=completeness,
        pipeline=pipeline,
        tea=tea,
        gates=gates,
        notes=list(notes or []),
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
