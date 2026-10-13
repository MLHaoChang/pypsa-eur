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
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


NonNegRate = Annotated[float, Field(ge=0)]


class DebtTranche(BaseModel):
    """A debt tranche (spec §4.2; IC P4 plan C8). The fees, DSRA months and
    grace years have NO default (P0 gate finding 5, plan C12): `None` = not
    stated, refused at run time — 0 must be typed. `rate` is one rate or one
    per operating year of the tenor."""
    model_config = ConfigDict(extra="forbid")   # a typo is refused, not dropped (review B3)

    kind: Literal["term_loan", "mini_perm", "construction", "mezzanine"]
    amount: float | None = Field(default=None, ge=0)
    gearing: float | None = Field(default=None, ge=0, le=1)
    # What `gearing` is a share of: installed capex incl. contingency (SAM's
    # `debt_percent` base) or total uses (capex + IDC + fees + DSRA — the
    # fixed point, plan C8).
    gearing_base: Literal["capex", "total_uses"] = "capex"
    rate: NonNegRate | list[NonNegRate]
    tenor_years: int = Field(ge=1)
    sculpting: Literal["annuity", "dscr_target", "level"] = "annuity"
    dscr_target: float | None = Field(default=None, gt=1)
    # A cap on a sculpted tranche (SAM `dscr_maximum_debt_fraction`).
    max_gearing: float | None = Field(default=None, gt=0, le=1)
    dsra_months: int | None = Field(default=None, ge=0)
    upfront_fee: float | None = Field(default=None, ge=0, le=1)       # a share (IC P4 WP4.2 review B6)
    commitment_fee: float | None = Field(default=None, ge=0, le=1)
    grace_years: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _sizing(self) -> "DebtTranche":
        # A sculpted tranche is sized by its DSCR (plan C8) — `max_gearing` is
        # its cap; `amount` / `gearing` size the others (IC P4 WP4.2).
        if self.sculpting == "dscr_target":
            if self.dscr_target is None:
                raise ValueError("dscr_target sculpting needs dscr_target")
            if self.amount is not None or self.gearing is not None:
                raise ValueError("a sculpted tranche is sized by dscr_target; cap it with "
                                 "max_gearing, not amount / gearing")
        else:
            if self.amount is None and self.gearing is None:
                raise ValueError("DebtTranche needs amount or gearing")
            if self.amount is not None and self.gearing is not None:
                raise ValueError("DebtTranche takes amount OR gearing, not both")
            if self.max_gearing is not None:
                raise ValueError("max_gearing caps a sculpted (dscr_target) tranche only")
        if isinstance(self.rate, list) and not self.rate:
            raise ValueError("a per-year rate list needs at least one rate")
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
    """An incentive (spec §6.5; IC P4 plan WP4.4). `rate` is a SHARE of the
    eligible basis for `itc` / `grant`, and a price in currency/MWh (COD-year
    money) for a stated `ptc`; `amount` is the ITC cap or the grant's sum.
    `feoc_flag`: material assistance from a prohibited foreign entity (applies
    where the pack's rule does). A grant states its tax treatment
    (`grant_tax_treatment`, no default): it reduces the depreciable basis (and
    the ITC base of its assets), or it is taxable income when received."""
    model_config = ConfigDict(extra="forbid")   # a typo is refused, not dropped (review B3)

    kind: Literal["itc", "ptc", "grant", "accelerated_depreciation", "cfd", "capacity_payment"]
    rate: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)
    eligibility: EligibilityRule = Field(default_factory=EligibilityRule)
    phase_out: list[tuple[date, float]] = Field(default_factory=list)
    feoc_flag: bool | None = None
    grant_tax_treatment: Literal["reduces_basis", "taxable"] | None = None

    @model_validator(mode="after")
    def _share_at_most_one(self) -> "Incentive":
        # An ITC / grant rate is a share of the eligible basis (WP4.4 round 4,
        # deferred to the P4 gate): above 1 it is refused here, not later as
        # `tax_basis_invalid`.
        if self.kind in ("itc", "grant") and self.rate is not None and self.rate > 1.0:
            raise ValueError(f"incentive_rate_above_one: a {self.kind} rate is a share of the "
                             f"eligible basis (≤ 1), got {self.rate}")
        return self


class TaxEquityStructure(BaseModel):
    """Spec §6.7 (fields beyond shares added after review F13)."""
    model_config = ConfigDict(extra="forbid")   # a typo is refused, not dropped (review B3)

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
    # P0 gate finding 5: no default — P7 states it (sourced) or leaves None.
    itc_recapture_years: int | None = Field(default=None, ge=0)
    debt_in_structure: bool = False


class SolvePpa(BaseModel):
    """Solve a contract's price for a target after-tax equity IRR in a target
    year (spec §6.6; SAM `ppa_soln_mode=0`; IC P4 plan C9). `contract_id`
    None = the case's single owner-sold PPA."""
    model_config = ConfigDict(extra="forbid")   # a typo is refused, not dropped (review B3)

    contract_id: str | None = None
    target_irr: float = Field(gt=-1, lt=10)
    target_year: int = Field(ge=1)


class TerminalValueRule(BaseModel):
    """The terminal value at the last operating year (inside EBITDA, taxed):
    `fixed` an amount (SAM's salvage), `multiple_of_ebitda` a multiple,
    `book_value` the remaining tax basis, or `remaining_life_annuity` (IC S0b
    plan S6, decision D10) — each part's purchase still alive at the horizon
    valued on the annuity the LP charged for it (GS's salvage); `value` is then
    unused and must be None."""
    model_config = ConfigDict(extra="forbid")   # a typo is refused, not dropped (review B3)

    method: Literal["none", "book_value", "multiple_of_ebitda", "fixed",
                    "remaining_life_annuity"] = "none"
    value: float | None = None

    @model_validator(mode="after")
    def _value_unused(self) -> "TerminalValueRule":
        if self.method == "remaining_life_annuity" and self.value is not None:
            raise ValueError("remaining_life_annuity values the parts' remaining life from the "
                             "case; value must be None")
        return self


ESCALATION_CLASSES: tuple[str, ...] = ("opex", "fuel", "tariff", "ppa", "export", "capex")


class FinanceInputs(BaseModel):
    """The finance case's inputs (spec §4.2, §4.2a; IC P4 plan C2–C14).

    `escalation` holds nominal rates per year for the six classes of
    `ESCALATION_CLASSES` (plan C4); a class with cashflows and no rate is
    not established, never 0. `participants` is derived from the value-flow
    config (P3) — a stored list that differs is refused at run time."""
    model_config = ConfigDict(extra="forbid")   # a typo is refused, not dropped (review B3)

    currency: str = Field(default="EUR", min_length=3, max_length=3)
    # The money year of the typed costs and rates (GS Q6; U1 follow-up d):
    # None = not stated, never a guessed year. `price_basis` says whether the
    # cash is nominal (escalated, the default) or real (constant money of
    # `currency_year`: escalation and inflation then 0 — a non-zero one is
    # flagged `real_basis_with_escalation:<class>`, IC plan C4).
    currency_year: int | None = Field(default=None, ge=1900, le=2200)
    price_basis: Literal["nominal", "real"] = "nominal"
    financial_close: date
    cod_by_asset: dict[str, date] = Field(default_factory=dict)
    construction_months_by_asset: dict[str, int] = Field(default_factory=dict)
    capex_phasing: list[float] = Field(default_factory=lambda: [1.0])
    # None = not supplied (ADR-0001): the engine reports the figure as
    # not_established rather than fabricating a 0.
    contingency_share: float | None = Field(default=None, ge=0)
    escalation: dict[str, float] = Field(default_factory=dict)
    # A constant annual rate d (factor (1 − d)^(k − 1), SAM's), or a list of annual
    # STEPS: entry j = the loss from operating year j+1 to j+2, compounded, the last
    # entry repeating (NOT SAM's cumulative-vs-nameplate schedule) — plan C5.
    degradation_by_asset: dict[str, float | list[float]] = Field(default_factory=dict)
    # The case's dates and length (plan C2): the axis, eligibility and the
    # pack's dated rules. `analysis_years` None → the run is refused.
    analysis_years: int | None = Field(default=None, ge=1, le=60)
    acquisition_date: date | None = None
    construction_start: date | None = None
    # Scale a non-annual operating template to a year (plan C3; flagged).
    annualise: bool = False
    # Tax treatment choices with no default (plan C6, C7, C12).
    tax_losses: Literal["offset_other_income", "carryforward"] | None = None
    financing_fee_tax: Literal["amortised", "not_deducted"] | None = None
    # Jurisdiction inputs the packs need (plan WP4.3a/4.4): None = not stated,
    # distinct from a typed 0.
    hebesatz_pct: float | None = Field(default=None, ge=0)
    state_rate: float | None = Field(default=None, ge=0, le=1)
    pwa_met: bool | None = None
    small_business_163j: bool | None = None
    reserves_rate: float | None = Field(default=None, ge=0)
    solve_ppa: SolvePpa | None = None
    # A depreciation class per owner asset where the pack assigns none (plan
    # WP4.3a, C11): "macrs_<n>", "sl_<n>" (US half-year), "afa_<n>" (DE
    # straight-line pro rata), "db_<rate>_<n>" (declining balance, rate as a
    # fraction, switching to straight-line). Stated by the user (source: user).
    depreciation_class_by_asset: dict[str, str] = Field(default_factory=dict)
    replacement_capex: list[tuple[int, str, float]] = Field(default_factory=list)
    # How replacements are scheduled (IC S0b plan S5, decision D9): `fixed` =
    # the `replacement_capex` entries only; `part_lifetimes` = each investment
    # part re-bought at the end of its lifetime at its current upfront cost (a
    # `replacement_capex` entry for an asset it replaces is then refused).
    replacement_rule: Literal["fixed", "part_lifetimes"] = "fixed"
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
        unknown = sorted(set(self.escalation) - set(ESCALATION_CLASSES))
        if unknown:
            raise ValueError(f"escalation classes {unknown} are not in {list(ESCALATION_CLASSES)}")
        for asset, d in self.degradation_by_asset.items():
            rates = d if isinstance(d, list) else [d]
            if not rates or any(not (0.0 <= r < 1.0) for r in rates):
                raise ValueError(f"degradation for {asset!r} must be rates in [0, 1)")
        return self


# ------------------------------------------------------------------ cashflow lines


class Provenance(BaseModel):
    source: str
    mode: DispatchMode
    pack_hash: str | None = None
    seed: int | None = None
    # The P3 ledger's drill-down (plan WP4.0; the WP3.1 mapping pin).
    source_id: str | None = None
    contract_id: str | None = None
    period: str | None = None


# The finance-side streams (IC P4 plan WP4.0, review round 2 R4): the ledger's
# `ValueStreamKind` plus kinds only finance has. Corporate tax never collides
# with the P3 levy stream `tax`; incentives use the existing `incentive`.
FinanceOnlyStream = Literal["corporate_tax", "terminal_value", "financing_fee", "reserve",
                            "interest", "principal"]
CashflowStream = ValueStreamKind | FinanceOnlyStream


class CashflowLine(BaseModel):
    year: int
    participant: str
    counterparty: str
    value_stream: CashflowStream
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
