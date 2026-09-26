"""
Edge Investment Case — finance contracts and report skeleton (Phase 0, WP0.1).

Design: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §4.2–4.3
Plan:   docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.1

Skeleton shapes only — no cashflow arithmetic. Every headline figure defaults
to ``None`` (ADR-0001: unresolvable ships as null + flag, never 0). Nothing
here imports ``services``.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from models.commercial import Participant, ValueStreamKind

IcSectionStatus = Literal["ok", "not_established", "skipped"]

IC_PIPELINE_STAGES: tuple[str, ...] = (
    "design_solve",
    "valuation_pf",
    "valuation_realistic",
    "billing",
    "participants",
    "finance",
    "uncertainty",
    "assemble",
)

IcPipelineStage = Literal[
    "design_solve", "valuation_pf", "valuation_realistic", "billing",
    "participants", "finance", "uncertainty", "assemble",
]

IC_REPORT_SECTIONS: tuple[str, ...] = (
    "design", "commercial", "dispatch_modes", "participants", "project",
    "debt", "tax", "tax_equity", "uncertainty", "gates",
)

DEFAULT_IC_BUDGET_SOLVES = 30
MAX_IC_BUDGET_SOLVES = 120

IC_EXPORT_KEYS: tuple[str, ...] = (
    "case_id", "reference_design_id", "assumptions_hash", "cost_at_target_eur",
    "excludes_shed_cost", "haircut_pct", "project_irr_pre_tax",
    "project_irr_post_tax", "npv_at_wacc", "lcoe_finance_consistent_eur_per_mwh",
    "ppa_price_for_target_irr_eur_per_mwh", "min_dscr", "avg_dscr", "llcr",
    "plcr", "flip_year", "wacc_vs_discount_rate_consistent", "conservation_ok",
    "completeness",
)

DispatchMode = Literal["pf", "realistic"]


# ------------------------------------------------------------------ finance inputs


class DebtTranche(BaseModel):
    kind: Literal["term_loan", "mini_perm", "construction", "mezzanine"]
    amount: float | None = Field(default=None, ge=0)
    gearing: float | None = Field(default=None, ge=0, le=1)
    rate: float = Field(ge=0)
    tenor_years: int = Field(ge=1)
    sculpting: Literal["annuity", "dscr_target", "level"] = "annuity"
    dscr_target: float | None = Field(default=None, gt=1)
    dsra_months: int = Field(default=0, ge=0)
    upfront_fee: float = Field(default=0.0, ge=0)
    commitment_fee: float = Field(default=0.0, ge=0)
    grace_years: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _sizing(self) -> "DebtTranche":
        if self.amount is None and self.gearing is None:
            raise ValueError("DebtTranche needs amount or gearing")
        if self.amount is not None and self.gearing is not None:
            raise ValueError("DebtTranche takes amount OR gearing, not both")
        if self.sculpting == "dscr_target" and self.dscr_target is None:
            raise ValueError("dscr_target sculpting needs dscr_target")
        return self


class DepreciationSchedule(BaseModel):
    method: Literal["straight_line", "declining_balance", "macrs", "bonus"]
    years: int | None = Field(default=None, ge=1)
    rate: float | None = Field(default=None, gt=0, le=1)
    macrs_class: int | None = None
    bonus_share: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _method_params(self) -> "DepreciationSchedule":
        if self.method == "straight_line" and self.years is None:
            raise ValueError("straight_line needs years")
        if self.method == "declining_balance" and self.rate is None:
            raise ValueError("declining_balance needs rate")
        if self.method == "macrs" and self.macrs_class not in (3, 5, 7, 10, 15, 20):
            raise ValueError("macrs needs a class in {3,5,7,10,15,20}")
        if self.method == "bonus" and self.bonus_share is None:
            raise ValueError("bonus needs bonus_share")
        return self


class TaxPack(BaseModel):
    jurisdiction: str = Field(min_length=2)
    corporate_rate: float | None = Field(default=None, ge=0, le=1)
    depreciation: dict[str, DepreciationSchedule] = Field(default_factory=dict)
    loss_carryforward_years: int | None = Field(default=None, ge=0)
    interest_deductibility_cap: float | None = Field(default=None, ge=0, le=1)
    pack_hash: str | None = None
    valid_from: date | None = None
    source: str | None = None


class EligibilityRule(BaseModel):
    begin_construction_by: date | None = None
    placed_in_service_by: date | None = None
    asset_classes: list[str] = Field(default_factory=list)


class Incentive(BaseModel):
    kind: Literal["itc", "ptc", "grant", "accelerated_depreciation", "cfd", "capacity_payment"]
    rate: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)
    eligibility: EligibilityRule = Field(default_factory=EligibilityRule)
    phase_out: list[tuple[date, float]] = Field(default_factory=list)
    feoc_flag: bool | None = None


class TaxEquityStructure(BaseModel):
    """Spec §6.7 (fields beyond shares added after review F13)."""

    kind: Literal["partnership_flip", "sale_leaseback", "inverted_lease"]
    te_share_pre_flip: float = Field(ge=0, le=1)
    te_share_post_flip: float = Field(ge=0, le=1)
    target_flip_irr: float = Field(gt=0)
    flip_year_cap: int = Field(ge=1)
    cash_share_pre_flip: float = Field(ge=0, le=1)
    cash_share_post_flip: float = Field(ge=0, le=1)
    dro_cap: float = Field(ge=0)
    itc_share_te: float | None = Field(default=None, ge=0, le=1)
    developer_fee: float = Field(default=0.0, ge=0)
    target_irr_basis: Literal["after_tax_cash_plus_tax_benefits"] = "after_tax_cash_plus_tax_benefits"
    itc_recapture_years: int = Field(default=5, ge=0)
    debt_in_structure: bool = False


class TerminalValueRule(BaseModel):
    method: Literal["none", "book_value", "multiple_of_ebitda", "fixed"] = "none"
    value: float | None = None


class FinanceInputs(BaseModel):
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    financial_close: date
    cod_by_asset: dict[str, date] = Field(default_factory=dict)
    construction_months_by_asset: dict[str, int] = Field(default_factory=dict)
    capex_phasing: list[float] = Field(default_factory=lambda: [1.0])
    # None = not supplied (ADR-0001): the engine reports the figure as
    # not_established rather than fabricating a 0.
    contingency_share: float | None = Field(default=None, ge=0)
    escalation: dict[str, float] = Field(default_factory=dict)
    degradation_by_asset: dict[str, float] = Field(default_factory=dict)
    replacement_capex: list[tuple[int, str, float]] = Field(default_factory=list)
    terminal_value: TerminalValueRule = Field(default_factory=TerminalValueRule)
    wacc_nominal: float | None = Field(default=None, ge=0)
    cost_of_equity: float | None = Field(default=None, ge=0)
    # None = not supplied; the WACC gate (spec §6.6) then reports
    # `not_established` instead of comparing against a fabricated 0.
    inflation: float | None = None
    debt: list[DebtTranche] = Field(default_factory=list)
    tax_pack_id: str | None = None
    incentives: list[Incentive] = Field(default_factory=list)
    tax_equity: TaxEquityStructure | None = None
    participants: list[Participant] = Field(default_factory=list)

    @model_validator(mode="after")
    def _phasing_sums_to_one(self) -> "FinanceInputs":
        if self.capex_phasing and abs(sum(self.capex_phasing) - 1.0) > 1e-9:
            raise ValueError("capex_phasing must sum to 1")
        return self


# ------------------------------------------------------------------ cashflow lines


class Provenance(BaseModel):
    source: str
    mode: DispatchMode
    pack_hash: str | None = None
    seed: int | None = None


class CashflowLine(BaseModel):
    year: int
    participant: str
    counterparty: str
    value_stream: ValueStreamKind
    tariff_item: str | None = None
    asset: str | None = None
    amount: float
    provenance: Provenance


# ------------------------------------------------------------------ report


class IcSectionState(BaseModel):
    status: IcSectionStatus
    payload: dict[str, Any] | None = None
    note: str | None = None


class IcPipelineStageRecord(BaseModel):
    stage: IcPipelineStage
    status: Literal["run", "skipped", "aborted", "pending"] = "pending"
    solves_charged: int = 0
    note: str | None = None


class IcStudyPipeline(BaseModel):
    stages: list[IcPipelineStageRecord] = Field(
        default_factory=lambda: [IcPipelineStageRecord(stage=s) for s in IC_PIPELINE_STAGES])
    budget_solves: int = Field(default=DEFAULT_IC_BUDGET_SOLVES, ge=1, le=MAX_IC_BUDGET_SOLVES)
    solves_consumed: int = 0
    aborted: bool = False


class GatesBlock(BaseModel):
    wacc_vs_discount_rate_consistent: bool | None = None
    billing_vs_lp_gap_pct: dict[str, float] | None = None
    conservation_ok: bool | None = None


class InvestmentCaseReport(BaseModel):
    """The one investment-case artifact (spec §4.3). Assembled only by
    ``services/finance/report.py::assemble_investment_case_report``."""

    case_id: str = Field(min_length=1)
    reference_design_id: str | None = None
    assumptions_hash: str = Field(min_length=8)
    packs: dict[str, str] = Field(default_factory=dict)
    # Headline figures — None until a stage establishes them (never 0).
    cost_at_target_eur: float | None = None
    excludes_shed_cost: Literal[True] = True
    haircut_pct: float | None = None
    project_irr_pre_tax: float | None = None
    project_irr_post_tax: float | None = None
    npv_at_wacc: float | None = None
    lcoe_finance_consistent_eur_per_mwh: float | None = None
    ppa_price_for_target_irr_eur_per_mwh: float | None = None
    min_dscr: float | None = None
    avg_dscr: float | None = None
    llcr: float | None = None
    plcr: float | None = None
    flip_year: int | None = None
    gates: GatesBlock = Field(default_factory=GatesBlock)
    # House shape (energy_hub.ReferenceDesignReport): `sections` carry the
    # payloads, `completeness` is the flat status map the UI chips read.
    sections: dict[str, IcSectionState] = Field(default_factory=dict)
    completeness: dict[str, IcSectionStatus]
    pipeline: IcStudyPipeline = Field(default_factory=IcStudyPipeline)
    cashflow_lines: list[CashflowLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def _completeness_covers_sections_exactly(self) -> "InvestmentCaseReport":
        if set(self.completeness) != set(IC_REPORT_SECTIONS):
            missing = set(IC_REPORT_SECTIONS) - set(self.completeness)
            extra = set(self.completeness) - set(IC_REPORT_SECTIONS)
            raise ValueError(
                f"completeness must cover exactly IC_REPORT_SECTIONS "
                f"(missing={sorted(missing)}, extra={sorted(extra)})")
        if set(self.sections) - set(IC_REPORT_SECTIONS):
            raise ValueError("sections may only name IC_REPORT_SECTIONS")
        for name, st in self.sections.items():
            if self.completeness[name] != st.status:
                raise ValueError(
                    f"completeness[{name!r}]={self.completeness[name]!r} disagrees "
                    f"with sections[{name!r}].status={st.status!r}")
        return self


def empty_ic_completeness(
        *, default: IcSectionStatus = "not_established") -> dict[str, IcSectionStatus]:
    return {name: default for name in IC_REPORT_SECTIONS}


def empty_ic_section_map(
        *, default: IcSectionStatus = "not_established") -> dict[str, IcSectionState]:
    """Section states for `InvestmentCaseReport.sections` (mirror of
    `energy_hub.empty_section_map`)."""
    return {name: IcSectionState(status=default) for name in IC_REPORT_SECTIONS}


def export_investment_case(report: InvestmentCaseReport) -> dict[str, Any]:
    """Stable export view: exactly ``IC_EXPORT_KEYS``; completeness as statuses."""
    raw = report.model_dump(mode="json")
    out: dict[str, Any] = {}
    for k in IC_EXPORT_KEYS:
        if k == "completeness":
            out[k] = dict(raw["completeness"])
        elif k in ("wacc_vs_discount_rate_consistent", "conservation_ok"):
            out[k] = raw["gates"].get(k)
        else:
            out[k] = raw.get(k)
    return out
