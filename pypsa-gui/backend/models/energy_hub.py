"""
Energy Hub reference-design contracts (Phase 0).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md
Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md (P0)

Skeleton shapes only — no solver, orchestrator, or UI behaviour. Later phases
FILL fields; they do not renegotiate this module's names or completeness enum.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

EnergyHubArchetype = Literal["strong_grid", "weak_flexible", "off_grid"]

SectionStatus = Literal["ok", "not_established", "skipped"]

# ── Energy Hub network tags (P14; plan B11 / R5) ─────────────────────────────
# ONE role vocabulary for Link `eh_role`, shared by archetypes (§6 selection),
# redundancy and levers. Kept as separate subsets: §6 selection rule 1 is
# `grid_import` ONLY — widening it to every import role would silently change
# which Links each pack applies to.
EH_IMPORT_ROLES: tuple[str, ...] = ("grid_import", "eh_import", "import")
EH_CONVERSION_ROLES: tuple[str, ...] = (
    "eh_conversion", "conversion", "electrolyser", "fuel_cell")
# Written by the redundancy study on its own private copies only.
EH_INTERNAL_ROLES: tuple[str, ...] = ("eh_n1_conversion",)
EH_LINK_ROLES: tuple[str, ...] = (
    ("",) + EH_IMPORT_ROLES + EH_CONVERSION_ROLES + EH_INTERNAL_ROLES)

# Custom (non-PyPSA) columns the GUI may write. Each maps to a kind:
# "bool" (flag, default False), "float_nonneg" (MVA, default NaN) or
# "role" (one of EH_LINK_ROLES, default "").
EH_CUSTOM_COLUMNS: dict[str, dict[str, str]] = {
    "Bus": {"eh_poc": "bool", "eh_critical": "bool",
            "eh_sk_mva": "float_nonneg", "eh_ibr_mva": "float_nonneg"},
    "Link": {"eh_role": "role"},
}

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

# mc_certify draw budget per pack. The engine's own product cap is
# ``services.adequacy.mc.MAX_DRAWS`` (2000); the literal here keeps this
# contract module free of service imports and is asserted equal in tests.
# 500 = the P11/P13 study default (``eh_study.DEFAULT_MC_DRAWS``); a request's
# ``mc.draws`` overrides the pack (merge 2026-09-28).
DEFAULT_EH_MC_DRAWS = 500
MAX_EH_MC_DRAWS = 2000

# Frontier stage defaults (plan eh-wire-skipped-stages WP2). Factors on the
# pack's ``ens_cap_permyriad``, loosest first; ×1 is the report's own point.
# The ceiling mirrors ``services.adequacy.frontier.MAX_FRONTIER_POINTS`` (12),
# asserted equal in tests so this contract module stays free of services.
DEFAULT_EH_FRONTIER_LADDER: tuple[float, ...] = (4.0, 2.0, 1.0, 0.5, 0.25)
MAX_EH_FRONTIER_POINTS = 12
# Ranked residual failure modes kept in ``fmea_top`` (spec §9 P12 amendment:
# top-5 Class-B Link modes; the class-A COPT screening block uses the same N).
DEFAULT_EH_FMEA_TOP_N = 5
MAX_EH_FMEA_TOP_N = 50

# Certification verdict (spec decision 2, §4 P11 amendment Q1): ``pass`` iff
# the LOLE 95% CI upper bound is within the target, ``fail`` iff its lower
# bound exceeds it, else ``inconclusive`` (also below the resolution floor).
# No target → no verdict (null); an MC that cannot run → the section is
# ``not_established`` with the reason. LOLE failure fails certification even
# when ENS is met.
CertificationVerdict = Literal["pass", "fail", "inconclusive"]

REPORT_SECTIONS: tuple[str, ...] = (
    "target",
    # MC LOLE certification of the fixed plan (spec decisions 1–2); filled by
    # the ``mc_certify`` stage, ``skipped`` when not requested, and
    # ``not_established`` with the reason when required but not run.
    "certification",
    "cost",
    "frontier",
    "sizing",
    "redundancy",
    "levers",
    "dtc",
    "fmea_top",
    "tea",
    "gates",
    "multi_energy",
)


class AvailabilityTarget(BaseModel):
    """
    Planning + certification targets (spec decisions 1–2).

    Planning always uses ENS when ``ens_cap_permyriad`` is set. MC LOLE is the
    acceptance metric when loops are run. If both are set, LOLE failure fails
    certification even when ENS is met.

    Units: ``ens_cap_permyriad`` is ‱ of demand; ``target_lole_h`` is hours
    per YEAR. Certification compares it with the MC's per-horizon LOLE as
    ``target_lole_h × horizon_years`` (spec §4 amendment, P11).
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
    # P17: compare energy import budgets (weak_flexible only).
    import_energy: bool = False


class DtcConfig(BaseModel):
    """DtC stress sidecar (spec decision 8; plan Phase 4a; §10 P16 amendment).

    ``bus_aggregate_not_per_load`` (default): a critical Load promotes its
    whole bus. ``per_load`` (opt-in): critical Loads are reported by Load,
    made non-degenerate by a critical VOLL premium scoped to the DtC stress
    re-dispatch; refused when the shed capture is not Load-keyed. No "auto".
    """

    critical_bus_ids: list[str] = Field(default_factory=list)
    critical_load_ids: list[str] = Field(default_factory=list)
    islanding_contingencies: list[str] = Field(default_factory=list)
    attribution: Literal["bus_aggregate_not_per_load", "per_load"] = (
        "bus_aggregate_not_per_load")

    @model_validator(mode="after")
    def _require_targets(self) -> "DtcConfig":
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
    # P12: the cost–availability frontier runs by default only where spec §3
    # makes it the deliverable (strong_grid); elsewhere only when requested.
    frontier_default: bool = False
    # DSR: weak_flexible may suggest opt-in; never silently global (decision 15).
    dsr_opt_in: bool = False
    # mc_certify draw budget (sequential MC solves nothing; this bounds the
    # arithmetic, not the LP budget). Seed + CoV target are the engine's
    # defaults so a pack certifies reproducibly.
    mc_draws: int = Field(default=DEFAULT_EH_MC_DRAWS, ge=1, le=MAX_EH_MC_DRAWS)
    mc_seed: int = 0
    mc_cov_target: float = Field(default=0.05, gt=0)
    # Frontier stage: factors on ens_cap_permyriad (each a full expansion
    # solve, trimmed to the remaining budget keeping the points nearest ×1).
    frontier_ladder: tuple[float, ...] = DEFAULT_EH_FRONTIER_LADDER
    # fmea_top: how many ranked residual failure modes the report keeps.
    fmea_top_n: int = Field(default=DEFAULT_EH_FMEA_TOP_N, ge=1,
                            le=MAX_EH_FMEA_TOP_N)

    @model_validator(mode="after")
    def _frontier_ladder_is_usable(self) -> ArchetypePack:
        ladder = self.frontier_ladder
        if not ladder:
            raise ValueError("frontier_ladder needs at least one factor")
        if len(ladder) > MAX_EH_FRONTIER_POINTS:
            raise ValueError(
                f"frontier_ladder has {len(ladder)} factors; the frontier "
                f"engine's budget is {MAX_EH_FRONTIER_POINTS} points")
        if len(set(ladder)) != len(ladder):
            raise ValueError("frontier_ladder factors must be distinct")
        for f in ladder:
            if not (math.isfinite(f) and f > 0):
                raise ValueError(
                    f"frontier_ladder factor {f!r} must be a positive finite "
                    "multiple of ens_cap_permyriad")
        return self


class PipelineStageRecord(BaseModel):
    stage: EHPipelineStage
    # ``aborted`` = the user's stop event; ``failed`` = the stage ran and did
    # not produce evidence (e.g. an infeasible ENS solve). Never both.
    status: Literal["run", "skipped", "aborted", "failed", "pending"] = "pending"
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
    # LCOH completeness (ADR-0001: an unresolvable number is null + a flag,
    # never 0). ``skipped`` = no electrolyser Links; ``not_established`` =
    # Links exist but produced no H₂ / the engine could not price them.
    lcoh_status: SectionStatus | None = None
    lcoh_note: str | None = None


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
    # MC LOLE in hours per YEAR (lole_hours / horizon_years), when certified.
    mc_lole_h: float | None = None
    # Decision 2: True only on a `pass` verdict; False on fail/inconclusive;
    # None when no LOLE target is set or certification is not established.
    certified: bool | None = None
    cost_at_target_eur: float | None = None
    period_basis: Literal["single_period", "multi_period"] | None = None
    excludes_shed_cost: Literal[True] = True
    sections: dict[str, SectionState] = Field(default_factory=dict)
    completeness: dict[str, SectionStatus] = Field(default_factory=dict)
    pipeline: EHStudyPipeline = Field(default_factory=EHStudyPipeline)
    tea: TeaBlock | None = None
    gates: GatesBlock | None = None
    # Study-level disclosures that belong to no single section (e.g. the
    # DSR opt-in preflight, decision 15).
    notes: list[str] = Field(default_factory=list)

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
        frontier_default=True,
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
