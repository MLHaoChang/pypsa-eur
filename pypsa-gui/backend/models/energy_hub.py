"""
Energy Hub reference-design contracts (Phase 0).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md
Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md (P0)

Skeleton shapes only — no solver, orchestrator, or UI behaviour. Later phases
FILL fields; they do not renegotiate this module's names or completeness enum.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

EnergyHubArchetype = Literal["strong_grid", "weak_flexible", "off_grid"]

SectionStatus = Literal["ok", "not_established", "skipped"]

# Spec decision 18 — ordered default pipeline.
EH_PIPELINE_STAGES: tuple[str, ...] = (
    "apply_pack",
    "ens_solve",
    "frontier",
    "mc_certify",
    "fmea_top",
    "redundancy",
    "levers",
    "dtc_stress",
    "dtc_planning",
    "assemble",
)

EHPipelineStage = Literal[
    "apply_pack",
    "ens_solve",
    "frontier",
    "mc_certify",
    "fmea_top",
    "redundancy",
    "levers",
    "dtc_stress",
    "dtc_planning",
    "assemble",
]

# Spec decisions 17 — same ceiling philosophy as campaign.DEFAULT_BUDGET_SOLVES.
DEFAULT_EH_BUDGET_SOLVES = 30
MAX_EH_BUDGET_SOLVES = 120

REPORT_SECTIONS: tuple[str, ...] = (
    "target",
    "cost",
    "frontier",
    "sizing",
    "redundancy",
    "levers",
    "dtc",
    "fmea_top",
    "tea",
    "gates",
)


class AvailabilityTarget(BaseModel):
    """Planning + certification targets (spec decisions 1–2).

    Planning always uses ENS when ``ens_cap_permyriad`` is set. MC LOLE is the
    acceptance metric when loops are run. If both are set, LOLE failure fails
    certification even when ENS is met.
    """

    ens_cap_permyriad: float | None = Field(default=None, gt=0)
    target_lole_h: float | None = Field(default=None, ge=0)
    # Explicit so consumers never invent precedence.
    planning_metric: Literal["ens"] = "ens"
    certification_metric: Literal["mc_lole", "none"] = "none"

    @model_validator(mode="after")
    def _require_at_least_one_or_cert_none(self) -> AvailabilityTarget:
        if self.ens_cap_permyriad is None and self.target_lole_h is None:
            raise ValueError(
                "AvailabilityTarget needs ens_cap_permyriad and/or target_lole_h")
        if self.target_lole_h is not None and self.certification_metric == "none":
            # Stating a LOLE without intending to certify is allowed for
            # documentation, but MVP packs that require certify set mc_lole.
            pass
        return self


class OptimizationLevers(BaseModel):
    """Which levers a study may exercise (spec decisions 5–7)."""

    sizing: bool = True
    redundancy: bool = False
    import_cap: bool = False
    storage_duration: bool = False


class DtcConfig(BaseModel):
    """DtC stress sidecar (spec decision 8; plan Phase 4a).

    Attribution is bus-aggregate only — today's one-VOLL-per-bus model cannot
    honestly claim per-load shed. Reject any other attribution mode.
    """

    critical_bus_ids: list[str] = Field(default_factory=list)
    critical_load_ids: list[str] = Field(default_factory=list)
    islanding_contingencies: list[str] = Field(default_factory=list)
    attribution: Literal["bus_aggregate_not_per_load"] = "bus_aggregate_not_per_load"

    @model_validator(mode="after")
    def _refuse_per_load_attribution(self) -> "DtcConfig":
        if self.attribution != "bus_aggregate_not_per_load":
            raise ValueError(
                "DtC attribution must be bus_aggregate_not_per_load "
                "(per_load shed claims are not supported on one-slack-per-bus)"
            )
        if not self.critical_bus_ids and not self.critical_load_ids:
            raise ValueError("DtC config needs critical_bus_ids and/or critical_load_ids")
        if not self.islanding_contingencies:
            raise ValueError("DtC config needs at least one islanding contingency")
        return self


class ImportOverlaySpec(BaseModel):
    """Normative import overlay knobs (spec §6) — values only; apply in P1."""

    import_carriers: list[str] = Field(
        default_factory=lambda: ["AC", "DC", "electricity"])
    # weak_flexible: total or per-study power cap (MW). Equal-split across
    # selected Links when applying. None = power overlay not used.
    import_p_nom_mw: float | None = Field(default=None, ge=0)
    import_energy_mwh_per_year: float | None = Field(default=None, ge=0)
    import_firmness: Literal["planning_limit_only"] = "planning_limit_only"


class ArchetypePack(BaseModel):
    """Parameter + overlay intent for one archetype (apply/undo lands in P1)."""

    archetype: EnergyHubArchetype
    availability: AvailabilityTarget
    levers: OptimizationLevers = Field(default_factory=OptimizationLevers)
    import_overlay: ImportOverlaySpec = Field(default_factory=ImportOverlaySpec)
    # Pack policy: whether mc_certify is required for a complete MVP-B report.
    mc_certify_required: bool = False
    dtc_stress_default: bool = False
    # Opt-in P4b planning overlay (islanded + retained critical demand).
    dtc_planning_default: bool = False
    # DSR: weak_flexible may suggest opt-in; never silently global (decision 15).
    dsr_opt_in: bool = False


class PipelineStageRecord(BaseModel):
    stage: EHPipelineStage
    status: Literal["run", "skipped", "aborted", "pending"] = "pending"
    solves_charged: int = 0
    note: str | None = None


class EHStudyPipeline(BaseModel):
    """Default stage list + budget policy (spec decisions 17–18)."""

    stages: list[PipelineStageRecord] = Field(
        default_factory=lambda: [
            PipelineStageRecord(stage=s) for s in EH_PIPELINE_STAGES
        ])
    budget_solves: int = Field(
        default=DEFAULT_EH_BUDGET_SOLVES, ge=1, le=MAX_EH_BUDGET_SOLVES)
    solves_consumed: int = 0
    aborted: bool = False


class SectionState(BaseModel):
    status: SectionStatus
    payload: dict[str, Any] | None = None
    note: str | None = None


class TeaBlock(BaseModel):
    lcoe_eur_per_mwh: float | None = None
    lcoh_eur_per_kg: float | None = None
    notes: str | None = None


class GatesBlock(BaseModel):
    scr: Literal["pass", "warn", "fail"] | None = None
    emt_recommended: bool | None = None


class ReferenceDesignReport(BaseModel):
    """The one EH product artifact (spec §4). Assembled only by P5 helper."""

    archetype: EnergyHubArchetype
    pack_hash: str
    assumptions_hash: str
    # Headline target/achieved — may be empty when section status is not ok.
    ens_cap_permyriad: float | None = None
    achieved_ens_permyriad: float | None = None
    achieved_shed_hours: float | None = None
    mc_lole_h: float | None = None
    cost_at_target_eur: float | None = None
    period_basis: Literal["single_period", "multi_period"] | None = None
    excludes_shed_cost: Literal[True] = True
    sections: dict[str, SectionState] = Field(default_factory=dict)
    completeness: dict[str, SectionStatus] = Field(default_factory=dict)
    pipeline: EHStudyPipeline = Field(default_factory=EHStudyPipeline)
    tea: TeaBlock | None = None
    gates: GatesBlock | None = None

    @model_validator(mode="after")
    def _completeness_matches_sections(self) -> ReferenceDesignReport:
        if not self.completeness and self.sections:
            self.completeness = {
                name: st.status for name, st in self.sections.items()
            }
        for name, status in self.completeness.items():
            if name in self.sections and self.sections[name].status != status:
                raise ValueError(
                    f"completeness[{name!r}]={status!r} disagrees with "
                    f"sections[{name!r}].status="
                    f"{self.sections[name].status!r}")
        return self


def empty_section_map(
        *, default: SectionStatus = "not_established") -> dict[str, SectionState]:
    """MVP-A starting point: every section flagged until stages fill them."""
    return {name: SectionState(status=default) for name in REPORT_SECTIONS}


def default_strong_grid_pack() -> ArchetypePack:
    return ArchetypePack(
        archetype="strong_grid",
        availability=AvailabilityTarget(
            ens_cap_permyriad=10.0,
            certification_metric="none",
        ),
        levers=OptimizationLevers(sizing=True),
        mc_certify_required=False,
        dtc_stress_default=False,
        dtc_planning_default=False,
        dsr_opt_in=False,
    )


def default_weak_flexible_pack() -> ArchetypePack:
    return ArchetypePack(
        archetype="weak_flexible",
        availability=AvailabilityTarget(
            ens_cap_permyriad=10.0,
            target_lole_h=3.0,
            certification_metric="mc_lole",
        ),
        levers=OptimizationLevers(
            sizing=True, import_cap=True, storage_duration=True),
        import_overlay=ImportOverlaySpec(import_p_nom_mw=50.0),
        mc_certify_required=True,
        dtc_stress_default=True,
        dtc_planning_default=False,
        dsr_opt_in=True,
    )


def default_off_grid_pack() -> ArchetypePack:
    return ArchetypePack(
        archetype="off_grid",
        availability=AvailabilityTarget(
            ens_cap_permyriad=5.0,
            target_lole_h=3.0,
            certification_metric="mc_lole",
        ),
        # import_cap stays False: Class-B islanding zeros p_*_pu, so a
        # planning-limit MW lever is a no-op after apply_pack (P3c-B1).
        levers=OptimizationLevers(
            sizing=True, import_cap=False, storage_duration=True),
        # import_p_nom_mw unused for off_grid: islanding is p_*_pu→0
        # (Class-B discipline), not p_nom→0. Leave None so fixtures cannot
        # be misread as a zero-nominal mutation.
        import_overlay=ImportOverlaySpec(import_p_nom_mw=None),
        mc_certify_required=True,
        dtc_stress_default=False,
        dtc_planning_default=False,
        dsr_opt_in=False,
    )
