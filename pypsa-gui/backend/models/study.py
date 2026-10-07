"""
Decision-study contracts (guided investment study, MVP-1 phase S1).

Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4
Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S1)

Shapes only. Later phases FILL these (S2 the ledger and maturity, S3 the
tariff, S4 the options, S5 the case, S6 the findings, S7 the report); they do
only add fields, never rename or retype the ones here; a field a later phase
needs and cannot find is added by that phase, with its test. Field names follow the spec's proposals; where the
spec names a field `class`, the Python attribute is `class_` and the wire
name stays `class` (serialise with ``by_alias=True``).

Two rules every payload here obeys:

* **ADR-0001** (`pypsa-gui/docs/adr/0001-unresolvable-figures-ship-as-null.md`):
  a figure the engine could not resolve is ``None`` AND carries a flag. Blocks
  with nullable figures derive from :class:`_FigureBlock`, which refuses a
  ``None`` without an ``unavailable[field]`` reason and a reason beside a
  value. A bare ``0.0`` is a real zero.
* Sections carry ``status: ok | not_established | skipped`` using the
  Energy Hub ``SectionStatus`` / ``SectionState`` (imported, not redefined).
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.energy_hub import SectionState, SectionStatus

__all__ = [
    "AssumptionsLedger", "Basis", "BatteryAttribution", "DecisionQuestion", "DecisionReport",
    "LedgerDomain",
    "DecisionStudy", "DemandCharge", "Fidelity", "Figure", "FinancialBasis",
    "Findings", "InvestmentCase", "LedgerRow", "OptionResult", "OptionSpec",
    "Bill", "BillComponents", "CaseKpis", "CaseProvenance", "CashFlowYear", "MarketRevenueAtDuals",
    "UpfrontGap", "ValueStream",
    "Perspective", "ReportDisclosure", "ReportSection", "SectionState", "SectionStatus",
    "StudyMaturity", "Tariff",
    "VerdictClass",
]

STUDY_SCHEMA_VERSION = 1
STUDY_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


# ── enums ─────────────────────────────────────────────────────────────────

class Perspective(StrEnum):
    """
    Whose cash flows the case models (spec decision 8). MVP-1 implements
    ``site_owner`` only; the others are contract names, not features.
    """

    site_owner = "site_owner"
    developer = "developer"
    investor = "investor"
    multi_party = "multi_party"


class Basis(StrEnum):
    """Real or nominal money terms (spec decision 7). MVP-1: ``real``."""

    real = "real"
    nominal = "nominal"


class VerdictClass(StrEnum):
    """The three computed verdict classes (spec decision 6)."""

    recommended = "recommended"
    marginal = "marginal"
    not_recommended = "not_recommended"


class Fidelity(StrEnum):
    """
    Which run produced a figure (spec decision 14, amended: both are 8760 h
    of one representative year in MVP-1).
    """

    quick_screen = "quick_screen"
    full_study = "full_study"


# The engine that produced a figure (spec §4.5, §5). Every figure names one.
Engine = Literal[
    "lp", "lp_duals", "bill_calculator", "contract", "mc_resilience",
    "cash_flow_expander", "ledger",
    # S7 (gate S7 [N6]): a constant of the method itself (the verdict's
    # break-even tolerance), neither a ledger row nor a run's output.
    "method_constant",
    # U2 WP7: the Investment Case engine's bill and finance engines (the
    # engine-built case and its value streams; §5.4's rename of the old two
    # names, with read-compat, is WP8's).
    "tariff_engine", "finance_engine",
]
SolveStatus = Literal[
    "not_run", "queued", "running", "ok", "infeasible", "failed", "aborted",
]
Provenance = Literal["library", "user", "imported", "measured"]
LedgerStatus = Literal["default", "customised", "needs_attention"]
MaturityClass = Literal["screening", "feasibility", "design"]
SalvageBasis = Literal["annuity_pv", "straight_line"]


# ── shared building blocks ────────────────────────────────────────────────

class _Model(BaseModel):
    """
    Refuse unknown keys: a typo in a stored study must fail loudly, not
    vanish on the next save.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _FigureBlock(_Model):
    """
    A block with nullable figures (ADR-0001).

    ``_figure_fields`` names the nullable numeric fields; ``unavailable`` maps
    each one that is ``None`` to a short reason code (``not_run``,
    ``no_sign_change``, ``not_applicable``, …). The two must agree exactly.
    """

    _figure_fields: ClassVar[tuple[str, ...]] = ()
    unavailable: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _null_carries_a_flag(self):
        cls = type(self).__name__
        stray = set(self.unavailable) - set(self._figure_fields)
        if stray:
            raise ValueError(
                f"ADR-0001: {cls}.unavailable names non-figure field(s) "
                f"{sorted(stray)}")
        for f in self._figure_fields:
            value = getattr(self, f)
            if value is None and f not in self.unavailable:
                raise ValueError(
                    f"ADR-0001: {cls}.{f} is null without an unavailable flag")
            if value is not None and f in self.unavailable:
                raise ValueError(
                    f"ADR-0001: {cls}.{f} has a value and an unavailable flag")
        return self


class FinancialBasis(_Model):
    """
    The stated basis of a money figure (spec decision 7). The default is the
    one labelled convention (IRENA): real, pre-tax, without subsidy.
    """

    terms: Basis = Basis.real
    tax: Literal["pre", "post"] = "pre"
    subsidy: Literal["excl", "incl"] = "excl"


MVP1_BASIS = FinancialBasis()
MVP1_PERSPECTIVES: frozenset[Perspective] = frozenset({Perspective.site_owner})


class Figure(_Model):
    """
    One reportable figure with its provenance: the unit of a headline KPI and
    of a report fact (spec §4.6 ``headline_kpis``, §4.7 ``facts``).
    ``unavailable`` is the ADR-0001 flag for a ``None`` value.
    """

    key: str
    label: str
    value: float | None
    unit: str
    basis: FinancialBasis | None = None
    currency_year: int | None = None
    engine: Engine
    fidelity: Fidelity | None = None
    unavailable: str | None = None

    @model_validator(mode="after")
    def _null_carries_a_flag(self):
        if self.value is None and not self.unavailable:
            raise ValueError(
                f"ADR-0001: Figure {self.key!r} is null without an "
                f"unavailable reason")
        if self.value is not None and self.unavailable:
            raise ValueError(
                f"ADR-0001: Figure {self.key!r} has a value and an "
                f"unavailable reason")
        return self

    @model_validator(mode="after")
    def _provenance_is_stated(self):
        """
        Gate S1 [S] (enforced from S6): a money figure states its basis and
        currency year, and a figure a run produced states its fidelity. A
        null money figure is exempt: its flag says why there is no number
        (e.g. ``currency_year_unknown``, gate S6 BC-S6-2).
        """
        if self.value is not None and self.unit.split("/")[0].strip().upper() in _CURRENCIES and (
                self.basis is None or self.currency_year is None):
            raise ValueError(
                f"Figure {self.key!r} is money ({self.unit}) without its basis "
                "and currency year")
        if self.engine in _RUN_ENGINES and self.fidelity is None:
            raise ValueError(
                f"Figure {self.key!r} comes from a run ({self.engine}) without "
                "its fidelity")
        return self


_CURRENCIES = frozenset({"EUR", "USD", "GBP", "CHF"})
_RUN_ENGINES = frozenset({"lp", "lp_duals", "bill_calculator", "cash_flow_expander",
                          "tariff_engine", "finance_engine"})


def _completeness_agrees(sections: dict[str, SectionStatus],
                         completeness: dict[str, SectionStatus],
                         owner: str) -> None:
    for name, status in completeness.items():
        if name in sections and sections[name] != status:
            raise ValueError(
                f"{owner}: completeness[{name!r}]={status!r} disagrees with "
                f"the section's status {sections[name]!r}")


# ── 4.3 AssumptionsLedger ─────────────────────────────────────────────────

class LedgerRange(_Model):
    low: float
    high: float
    # S2 marks a ±30 % band it had to assume, as distinct from a sourced one.
    source: Literal["source", "assumed"] | None = None

    @model_validator(mode="after")
    def _ordered(self):
        if self.low > self.high:
            raise ValueError(f"range low {self.low} > high {self.high}")
        return self


_DOMAIN_RE = re.compile(
    r"^\s*([\[(])\s*(-inf|[-+0-9.eE]+)\s*,\s*(inf|[-+0-9.eE]+)\s*([\])])\s*$")


def _fmt_bound(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


class LedgerDomain(_Model):
    """
    The physical domain of a ledger value, as an interval (gate S2 BC-S2-3):
    an efficiency is in ``(0, 1]``, a cost in ``[0, inf)``, a lifetime in
    ``[1, inf)``. ``None`` is an unbounded end. Written and parsed in
    interval notation (``str(domain)``, :meth:`parse`), so the library CSV
    carries the rule as data.
    """

    low: float | None = None
    high: float | None = None
    low_open: bool = False
    high_open: bool = False

    @model_validator(mode="after")
    def _unbounded_ends_are_open(self):
        # One spelling per interval, so a parsed domain equals the built one.
        if self.low is None:
            self.low_open = True
        if self.high is None:
            self.high_open = True
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"domain low {self.low} > high {self.high}")
        return self

    def contains(self, value: float) -> bool:
        if self.low is not None and (value <= self.low if self.low_open else value < self.low):
            return False
        if self.high is not None and (value >= self.high if self.high_open else value > self.high):
            return False
        return True

    def __str__(self) -> str:
        lo = "(-inf" if self.low is None else (
            ("(" if self.low_open else "[") + _fmt_bound(self.low))
        hi = "inf)" if self.high is None else (
            _fmt_bound(self.high) + (")" if self.high_open else "]"))
        return f"{lo}, {hi}"

    @classmethod
    def parse(cls, text: str) -> LedgerDomain:
        m = _DOMAIN_RE.match(text or "")
        if not m:
            raise ValueError(f"{text!r} is not an interval such as '(0, 1]'")
        lb, lo, hi, hb = m.groups()
        return cls(low=None if lo == "-inf" else float(lo),
                   high=None if hi == "inf" else float(hi),
                   low_open=lb == "(", high_open=hb == ")")


class LedgerRow(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = ("value",)

    key: str
    label: str
    technical_name: str | None = None
    # S4: one plain sentence on what the value means and what it moves (the
    # guided flow shows it beside the input; e.g. `energy_price_level`).
    help: str | None = None
    value: float | None
    unit: str
    basis: Basis = Basis.real
    # The year the money value is expressed in (review v1 N10). S2 also sets
    # it on rows that scale or discount money (a price multiplier, the real
    # discount rate) and keeps the source's year on lifetimes and
    # efficiencies; a money row is recognised by its unit, not by this field.
    currency_year: int | None = None
    source: str
    source_year: int | None = None
    source_url: str | None = None
    range: LedgerRange | None = None
    provenance: Provenance = "library"
    status: LedgerStatus = "default"
    sensitivity_flag: bool = False
    changed_by: str | None = None
    changed_at: datetime | None = None
    # S2 (gate BC-S2-3): the value's physical domain; a value outside it is
    # refused here and, with the row named, by `ledger.apply_user_row`.
    domain: LedgerDomain | None = None
    # U2 (plan §1, §6.2): the engine field the row compiles to (`compile.py`
    # is its only reader; None for a row that only shapes the network or is
    # not compiled), and the defaults pack's `illustrative` flag (None on a
    # ledger seeded from the legacy library). Neither is hashed.
    engine_path: str | None = None
    illustrative: bool | None = None

    @model_validator(mode="after")
    def _value_in_domain(self):
        if (self.domain is not None and self.value is not None
                and not self.domain.contains(self.value)):
            raise ValueError(
                f"{self.key}: {self.value} is outside its domain {self.domain}")
        return self


class AssumptionsLedger(_Model):
    ledger_version: str
    rows: list[LedgerRow] = Field(default_factory=list)
    honesty_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_keys(self):
        seen: set[str] = set()
        for row in self.rows:
            if row.key in seen:
                raise ValueError(f"duplicate ledger key {row.key!r}")
            seen.add(row.key)
        return self


# ── 4.4 Tariff ────────────────────────────────────────────────────────────

class TimeRule(_Model):
    """When a band applies. An empty list means "every": ``{}`` is always."""

    months: list[int] = Field(default_factory=list)    # 1..12
    weekdays: list[int] = Field(default_factory=list)  # 0 = Monday .. 6
    hours: list[int] = Field(default_factory=list)     # 0..23, hour starting

    @field_validator("months")
    @classmethod
    def _months(cls, v: list[int]) -> list[int]:
        if any(not 1 <= m <= 12 for m in v):
            raise ValueError("months must be 1..12")
        return v

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if any(not 0 <= d <= 6 for d in v):
            raise ValueError("weekdays must be 0..6")
        return v

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: list[int]) -> list[int]:
        if any(not 0 <= h <= 23 for h in v):
            raise ValueError("hours must be 0..23")
        return v


class EnergyBand(_Model):
    label: str
    price_per_mwh: float
    applies: TimeRule = Field(default_factory=TimeRule)


class Ratchet(_Model):
    months: int = Field(ge=1, le=36)
    share: float = Field(gt=0.0, le=1.0)


class DemandCharge(_Model):
    """
    A charge on peak import (spec decision 9): an LP constraint in S3, not
    a post-process.
    """

    price_per_mw_per_period: float = Field(ge=0.0)
    basis: Literal["billing_period_peak", "annual_peak", "ratchet"] = (
        "billing_period_peak")
    ratchet: Ratchet | None = None

    @model_validator(mode="after")
    def _ratchet_terms(self):
        if self.basis == "ratchet" and self.ratchet is None:
            raise ValueError("basis 'ratchet' needs ratchet {months, share}")
        if self.basis != "ratchet" and self.ratchet is not None:
            raise ValueError("ratchet terms given for a non-ratchet basis")
        return self


class CapacityCharge(_Model):
    price_per_mw_per_year: float = Field(ge=0.0)
    basis: Literal["contracted", "measured"] = "contracted"


class NetworkCharge(_Model):
    label: str
    price: float
    basis: str


class ExportCompensation(_Model):
    price_per_mwh: float | None = None
    series_ref: str | None = None
    cap_mw: float | None = None


class Tariff(_Model):
    tariff_id: str
    name: str
    source: str
    source_year: int | None = None
    currency: str = "EUR"
    currency_year: int | None = None
    billing_period: Literal["month", "year"] = "month"
    energy_bands: list[EnergyBand] = Field(default_factory=list)
    demand_charge: DemandCharge | None = None
    capacity_charge: CapacityCharge | None = None
    fixed_charge_per_period: float = 0.0
    network_charges: list[NetworkCharge] = Field(default_factory=list)
    export: ExportCompensation = Field(default_factory=ExportCompensation)
    connection_limit_mw: float | None = None
    # Snake_case codes (S5, gate S5 BC-S5-3); a supplied tariff may still
    # carry prose, which the case flags as `tariff_has_uncoded_notes`.
    honesty_notes: list[str] = Field(default_factory=list)
    # The sentence behind each code, for the guided flow and the report.
    honesty_help: dict[str, str] = Field(default_factory=dict)


# ── the bill (S3; moved here from `services/study/tariff.py` at S5) ─────
#
# `services/study/tariff.py` re-exports both names, so `tariff.Bill` is this
# class. The calculator that fills them stays there.

class BillComponents(_FigureBlock):
    """
    Signed contributions to the bill over the modelled horizon, in the
    tariff's currency: charges are positive, `export_credit` is negative (a
    credit), and the bill is their sum. `capacity` is the tariff's
    `CapacityCharge`, pro-rated by horizon hours / 8760.

    U2 (owner decision 7): a seventh component, `taxes_levies` ("Taxes &
    levies"), the engine's `tax_levy` and `certificate` items. The guided
    form compiles none, so it is a real 0.0 unless an Expert adds one; a bill
    stored before U2 reads 0.0.
    """

    _figure_fields: ClassVar[tuple[str, ...]] = (
        "energy", "demand", "capacity", "fixed", "network", "export_credit",
        "taxes_levies")

    energy: float | None
    demand: float | None
    capacity: float | None
    fixed: float | None
    network: float | None
    export_credit: float | None
    taxes_levies: float | None = 0.0


class Bill(_FigureBlock):
    """
    The site's grid bill (spec §4.4; ADR-0001). `total` covers the modelled
    snapshots; `annual_bill` is the same figure when they are one year and
    null (`horizon_not_one_year`) otherwise, never extrapolated.

    `honesty_notes` are stable snake_case codes, never prose (S7's prose
    validator rejects digits): `partial_billing_period_charged_in_full`
    (a period in `partial_billing_periods` carries a per-period charge in
    full) and `capacity_charge_prorated_by_hours` (horizon hours / a year).
    """

    _figure_fields: ClassVar[tuple[str, ...]] = ("total", "annual_bill")

    total: float | None
    annual_bill: float | None
    by_component: BillComponents
    peak_mw_by_billing_period: dict[str, float] = Field(default_factory=dict)
    billing_periods: list[str] = Field(default_factory=list)
    horizon_hours: float
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    currency: str = "EUR"
    currency_year: int | None = None
    # U2 WP5: `tariff_engine` is the IC engine's bill (`engine_adapter`); the
    # vocabulary rename of the GS literal with read-compat is WP8 (§5.4).
    engine: Literal["bill_calculator", "tariff_engine"] = "bill_calculator"
    # Gate S3 N3 (carried to S5): which run the dispatch came from; the
    # calculator itself does not know, the runner stamps it.
    fidelity: Fidelity | None = None
    honesty_notes: tuple[str, ...] = ()
    # Billing periods the snapshots cover only in part (labels, not prose).
    partial_billing_periods: list[str] = Field(default_factory=list)
    # U2 gate (visible now): the components the tariff carries an item for
    # (None on a bill stored before the list existed). A zero value stream
    # none of whose components has an item is hidden from the report and UI.
    itemised_components: list[str] | None = None


# ── 4.2 DecisionQuestion ──────────────────────────────────────────────────

class DiscreteChoice(_Model):
    key: str
    values: list[float | int | str]


class OptionSpec(_Model):
    """One option of a question template (spec §4.2 ``options[]``)."""

    option_id: str
    label: str
    one_line: str = ""
    free_assets: list[str] = Field(default_factory=list)
    fixed_assets: list[str] = Field(default_factory=list)
    # Review v2 BC-1: the "without" option OMITS its assets rather than fixing
    # them at 0 (a size fixed at 0 fails preflight). S4 fills it.
    omitted_assets: list[str] = Field(default_factory=list)
    discrete_choices: list[DiscreteChoice] = Field(default_factory=list)


class BaselineDefinition(_Model):
    text: str
    fixed_assets: list[str] = Field(default_factory=list)


class DecisionQuestion(_Model):
    question_id: str
    title: str
    one_line: str = ""
    archetype: str | None = None
    mandatory_inputs: list[str] = Field(default_factory=list)
    defaults: list[LedgerRow] = Field(default_factory=list)
    network_pack: str | None = None
    baseline_definition: BaselineDefinition
    options: list[OptionSpec] = Field(default_factory=list)
    headline_metrics: list[str] = Field(default_factory=list)
    value_streams: list[str] = Field(default_factory=list)
    key_drivers: list[str] = Field(default_factory=list)
    report_template_id: str | None = None


# ── maturity (spec decision 12, pulled into MVP-1 by §13) ─────────────────

class AccuracyBand(_Model):
    """
    Indicative accuracy in the spirit of AACE International RP 18R-97, whose
    classes give each end as a range (Class 5: low -20 to -50 %, high +30 to
    +100 %). ``low_pct``/``high_pct`` are the wide ends; the ``_narrow``
    fields (S2) are the narrow ends, and ``reference`` names the analogy.
    """

    low_pct: float
    high_pct: float
    low_pct_narrow: float | None = None
    high_pct_narrow: float | None = None
    reference: str | None = None


class StudyMaturity(_FigureBlock):
    """
    ``screening | feasibility | design`` plus an indicative accuracy band.
    Not established until S2 derives it from ledger provenance.
    """

    _figure_fields: ClassVar[tuple[str, ...]] = ("accuracy_band",)

    status: SectionStatus = "not_established"
    class_: MaturityClass | None = Field(default=None, alias="class")
    accuracy_band: AccuracyBand | None = None
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_flag(cls, data: Any) -> Any:
        # The default maturity has no band yet; say so rather than make every
        # caller spell the flag. An explicit ``unavailable`` is left alone.
        if (isinstance(data, dict) and data.get("accuracy_band") is None
                and "unavailable" not in data):
            data = {**data, "unavailable": {"accuracy_band": "not_computed"}}
        return data

    @model_validator(mode="after")
    def _ok_has_a_class(self):
        if self.status == "ok" and self.class_ is None:
            raise ValueError("maturity status 'ok' needs a class")
        return self


# ── 4.1 Study ─────────────────────────────────────────────────────────────

class StudyBudget(_Model):
    """
    The solve budget of ONE run. S4: a question-pack study derives
    ``solves_max`` from ``campaign.estimate_solves("decision_study", ...)``
    (one solve per option) at creation; the 12 below is only the record
    default of an attached record, which is never run. ``solves_used`` is
    what the last run charged.
    """

    solves_max: int = Field(default=12, ge=0)
    solves_used: int = Field(default=0, ge=0)


class DecisionStudy(_Model):
    """
    The study record: sidecar ``studies/<study_id>.json`` inside its base
    project's storage directory (``services/study/store.py``).

    ``intake`` holds the guided flow's answers keyed by step, applied one step
    at a time (spec decision 18: per-step apply, no transaction).
    ``base_project`` is the uuid of the project whose directory holds the
    record; the routes report the containing project, so a copy carried by a
    bundle or a Save-As never answers with a stale pointer.
    """

    schema_version: int = STUDY_SCHEMA_VERSION
    study_id: str
    name: str = Field(min_length=1, max_length=120)
    question_id: str
    base_project: str
    option_projects: list[str] = Field(default_factory=list)
    perspective: Perspective = Perspective.site_owner
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    currency_year: int | None = None
    intake: dict[str, Any] = Field(default_factory=dict)
    ledger_version: str | None = None
    # S2: the stored ledger; None until the first PUT (a GET seeds one in
    # memory from the library and the intake).
    ledger: AssumptionsLedger | None = None
    fidelity_last_run: Fidelity | None = None
    budget: StudyBudget = Field(default_factory=StudyBudget)
    maturity: StudyMaturity = Field(default_factory=StudyMaturity)
    findings_ref: str | None = None
    report_ref: str | None = None
    # S4 (M0): the uuid of the base project the question pack CREATED for this
    # study. A run is allowed only when it equals the containing project's
    # uuid, so a record attached to an existing user project (None), or one
    # copied into another project by Save-As, a scenario, a snapshot or a
    # bundle (the origin's uuid), is never run with a pack.
    pack_project: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    honesty_notes: tuple[str, ...] = ()

    @field_validator("study_id")
    @classmethod
    def _uuid4_hex(cls, v: str) -> str:
        if not STUDY_ID_RE.fullmatch(v):
            raise ValueError("study_id must be a lowercase uuid4 hex")
        return v

    @field_validator("question_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG_RE.fullmatch(v):
            raise ValueError("question_id must be a lowercase slug")
        return v


# ── 4.6 OptionSet ─────────────────────────────────────────────────────────

class AssetSize(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = ("p_nom_opt", "e_nom_opt")

    asset: str
    p_nom_opt: float | None
    e_nom_opt: float | None = None


class DeltaVsBaseline(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = ("npv", "payback", "capex", "co2")

    npv: float | None
    payback: float | None
    capex: float | None
    co2: float | None


class OptionResult(_FigureBlock):
    """One solved (or not) option; ``engine`` is the LP that sized it."""

    _figure_fields: ClassVar[tuple[str, ...]] = ("system_cost", "bill")

    option_id: str
    label: str
    project_ref: str | None = None
    solve_status: SolveStatus = "not_run"
    sizes: list[AssetSize] = Field(default_factory=list)
    system_cost: float | None
    bill: float | None
    case_ref: str | None = None
    delta_vs_baseline: DeltaVsBaseline
    engine: Engine = "lp"
    fidelity: Fidelity | None = None
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    currency_year: int | None = None


class BaselineResult(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = ("bill",)

    project_ref: str | None = None
    solve_status: SolveStatus = "not_run"
    bill: float | None
    case_ref: str | None = None


# ── 4.5 InvestmentCase ────────────────────────────────────────────────────

class CashFlowYear(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = (
        "bill_baseline", "bill_option", "resilience_value", "tax",
        "depreciation", "debt_service", "salvage",
    )

    year: int
    capex: float
    replacements: float
    opex_fixed: float
    opex_variable: float
    fuel: float
    co2_cost: float
    bill_baseline: float | None
    bill_option: float | None
    savings: float
    contract_revenue: float
    market_revenue_at_duals: float
    resilience_value: float | None
    tax: float | None
    depreciation: float | None
    debt_service: float | None
    # Residual value at horizon end, non-zero only in the last year (S5).
    # S5 (gate S1 re-gate): null with an `unavailable["salvage"]` flag when it
    # could not be computed, so a forgotten salvage never reads as a real 0.0;
    # a non-zero value needs the case's `salvage_basis` (InvestmentCase).
    salvage: float | None = 0.0
    net_cash_flow: float
    discounted_cash_flow: float
    cumulative_discounted: float


class CaseKpis(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = (
        "irr", "payback_simple", "payback_discounted", "lcoe", "lcos", "lcoh",
        "dscr_min", "salvage_eur",
    )

    npv: float
    irr: float | None
    payback_simple: float | None
    payback_discounted: float | None
    lcoe: float | None
    lcos: float | None
    lcoh: float | None
    dscr_min: float | None
    capex_total: float
    # S5: null plus `unavailable["salvage_eur"]` when not computed (ADR-0001).
    salvage_eur: float | None = 0.0


class ValueStream(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = ("annual_value", "share")

    # S5 (gate S1 carry): a stable key; for the pro forma, the bill component
    # the stream is the saving on (`energy`, `demand`, `fixed`, `network`,
    # `capacity`, `export_credit`).
    key: str | None = None
    label: str
    annual_value: float | None
    share: float | None
    engine: Engine
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    # U2 gate (visible now): whether the tariff has an item for one of the
    # stream's components (None: the bills did not say). A stream at 0.0 with
    # `itemised` False is hidden from the report and the UI.
    itemised: bool | None = None


class MarketRevenueAtDuals(_FigureBlock):
    """
    The option's assets' revenue at the LP's bus prices (S5, BC-7): the
    battery's ``discharge_revenue_eur - charge_cost_eur``, PV's
    ``revenue_eur``. Reported beside the case and EXCLUDED from
    ``net_cash_flow``: the bill already prices the same energy, and the duals
    include the demand-charge shadow price.
    """

    _figure_fields: ClassVar[tuple[str, ...]] = ("annual_value",)

    annual_value: float | None
    by_asset: dict[str, float] = Field(default_factory=dict)
    engine: Literal["lp_duals"] = "lp_duals"
    excluded_from_net_cash_flow: Literal[True] = True


class UpfrontGap(_Model):
    """
    Gate S4: where an existing surface (Capacity Expansion's lifetime CAPEX,
    Asset Detail) shows an asset's upfront cost as ``upfront_cost_series``
    (a single-lifetime back-calculation from ``capital_cost``), the case
    names the figure it books instead and the gap between the two.
    """

    asset: str
    ledger_upfront_eur: float
    back_calculated_upfront_eur: float | None
    gap_eur: float | None
    basis: Literal["upfront_back_calculated_single_lifetime"] = (
        "upfront_back_calculated_single_lifetime")


class CaseProvenance(_Model):
    """What the case was computed from (S5; the XLSX Provenance sheet)."""

    ledger_hash: str | None = None
    library_version: str | None = None
    tariff_id: str | None = None
    project_ref: str | None = None
    model_hash: str | None = None
    engines: list[str] = Field(default_factory=list)


class CaseSources(_Model):
    cost_breakdown_ref: str | None = None
    asset_economics_ref: str | None = None
    bill_refs: list[str] = Field(default_factory=list)
    mc_ref: str | None = None


class InvestmentCase(_Model):
    """
    The pro forma (spec §4.5). ``kpis`` is ``None`` exactly when the case
    is not ``ok``; the section status is then the flag.
    """

    case_id: str
    study_id: str
    option_id: str
    status: SectionStatus = "not_established"
    perspective: Perspective = Perspective.site_owner
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    currency_year: int | None = None
    fidelity: Fidelity | None = None
    engine: Engine = "cash_flow_expander"
    horizon_years: int = Field(ge=0)
    discount_rate: float
    wacc: float | None = None
    inflation: float | None = None
    # How residual value was valued (spec §5, BC-7): the present value of the
    # remaining annuities keeps one basis with the LP; straight line does not.
    salvage_basis: SalvageBasis | None = None
    years: list[CashFlowYear] = Field(default_factory=list)
    kpis: CaseKpis | None = None
    value_streams: list[ValueStream] = Field(default_factory=list)
    sources: CaseSources = Field(default_factory=CaseSources)
    completeness: dict[str, SectionStatus] = Field(default_factory=dict)
    honesty_notes: tuple[str, ...] = ()
    # S5 additions (all optional; S1 payloads validate unchanged).
    market_revenue_at_duals: MarketRevenueAtDuals | None = None
    upfront_gaps: list[UpfrontGap] = Field(default_factory=list)
    provenance: CaseProvenance | None = None

    @model_validator(mode="after")
    def _salvage_has_a_basis(self):
        """
        Gate S1 re-gate: a non-zero salvage names how it was valued; a
        salvage that was not computed is null with a flag (see the fields)
        and then has no basis; the last year and the KPI agree.
        """
        in_years = [y.salvage for y in self.years]
        kpi = self.kpis.salvage_eur if self.kpis is not None else 0.0
        nonzero = any(v not in (None, 0.0) for v in in_years) or kpi not in (None, 0.0)
        if nonzero and self.salvage_basis is None:
            raise ValueError("a non-zero salvage needs its salvage_basis")
        missing = kpi is None or any(v is None for v in in_years)
        if missing and self.salvage_basis is not None:
            raise ValueError(
                "a salvage that was not computed has no salvage_basis")
        if self.kpis is not None and self.years:
            total = None if any(v is None for v in in_years) else sum(in_years)
            if (total is None) != (kpi is None) or (
                    total is not None and abs(total - kpi) > 1e-6 * max(1.0, abs(kpi))):
                raise ValueError(
                    f"kpis.salvage_eur {kpi!r} disagrees with the years' "
                    f"salvage {total!r}")
        return self

    @model_validator(mode="after")
    def _kpis_iff_ok(self):
        if self.status == "ok" and self.kpis is None:
            raise ValueError("ADR-0001: an 'ok' case needs its kpis")
        if self.status != "ok" and self.kpis is not None:
            raise ValueError(
                f"a {self.status!r} case must not carry kpis")
        return self


# ── 4.6 Findings ──────────────────────────────────────────────────────────

class Verdict(_Model):
    status: SectionStatus = "not_established"
    class_: VerdictClass | None = Field(default=None, alias="class")
    # S6: the sentence is a fixed template whose numbers are `{{fact_id}}`
    # references into `facts` (no free prose, no bare digits);
    # `sentence_template` names the template.
    sentence: str | None = None
    sentence_template: str | None = None
    facts: dict[str, Figure] = Field(default_factory=dict)
    headline_kpis: list[Figure] = Field(default_factory=list, max_length=3)
    drivers: list[str] = Field(default_factory=list, max_length=3)
    main_caveat: str | None = None
    # S6: the option the verdict names (None when it names none).
    option_id: str | None = None
    # S6 (gate S5 carry): codes shown beside the centre figures — the NPV,
    # IRR >= rate and discounted payback <= horizon are bounded at the LP
    # optimum by construction.
    disclosures: tuple[str, ...] = ()
    # S6: why the verdict is not established, or what it could not judge
    # (codes, no digits).
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ok_has_a_class(self):
        if self.status == "ok" and self.class_ is None:
            raise ValueError("verdict status 'ok' needs a class")
        return self


class TornadoRow(_FigureBlock):
    """
    One key driver at its two bounds (S6). ``npv_low`` / ``npv_high`` /
    ``swing`` are the BATTERY NPV (``BatteryAttribution.battery_npv``) of the
    option the tornado runs on, at the driver's low and high value, sizes
    held fixed; ``swing = |npv_high - npv_low|``.
    """

    _figure_fields: ClassVar[tuple[str, ...]] = ("npv_low", "npv_high", "swing")

    key: str
    label: str
    # Gate S1 nit: the bounds carry their unit (the energy-price row is a
    # multiplier, not a price).
    unit: str | None = None
    centre_value: float | None = None
    low_value: float
    high_value: float
    npv_low: float | None
    npv_high: float | None
    swing: float | None
    # How the bounds were evaluated: `redispatch` (a fixed-size re-dispatch
    # at the perturbed tariff), `capex_only` or `rate_only` (no solve).
    evaluation: Literal["redispatch", "capex_only", "rate_only"] | None = None
    # Codes, e.g. `range_recentred_on_user_value`.
    notes: tuple[str, ...] = ()


class Breakeven(_FigureBlock):
    _figure_fields: ClassVar[tuple[str, ...]] = ("threshold",)

    key: str
    label: str
    threshold: float | None
    direction: Literal["below", "above"]
    text: str = ""


class OptionMap(_Model):
    x_key: str
    y_key: str
    grid: list[list[float | None]] = Field(default_factory=list)
    winner: list[list[str | None]] = Field(default_factory=list)


class Dotplot(_Model):
    n_futures: int
    n_positive: int
    values: list[float] = Field(default_factory=list)


class Robustness(_Model):
    status: SectionStatus = "not_established"
    # Spec §13: the tornado holds the recommended sizes fixed and re-dispatches.
    method: Literal["redispatch_fixed_sizes"] = "redispatch_fixed_sizes"
    tornado: list[TornadoRow] = Field(default_factory=list)
    breakevens: list[Breakeven] = Field(default_factory=list)
    option_map: OptionMap | None = None
    dotplot: Dotplot | None = None
    # When `status` is not `ok`: the ledger keys the tornado never reached
    # (an abort, a budget) — named, so "not established" says what is missing.
    pending: list[str] = Field(default_factory=list)
    note: str | None = None
    # S6: the option the tornado ran on, its centre battery NPV, the rows it
    # skipped (key -> code, e.g. a price level with no effect on one flat
    # band) and the solves it charged.
    option_id: str | None = None
    npv_centre: float | None = None
    skipped: dict[str, str] = Field(default_factory=dict)
    solves_charged: int = 0


class FindingsHashes(_Model):
    """
    What the findings were computed from, recorded at findings time so the
    report can say `stale` when any of it changes (S7). Option keys are the
    fork uuids.
    """

    ledger_hash: str | None = None
    base_network_hash: str | None = None
    option_network_hashes: dict[str, str] = Field(default_factory=dict)
    # Gate S7 [S4]: the intake the forks were built from
    # (`services/study/run_hashes.py::intake_hash`); an edit after the run
    # makes every reader refuse or mark stale.
    intake_hash: str | None = None


class BatteryAttribution(_FigureBlock):
    """
    The battery's share of one option's value (S6): "Do I need a BESS?".

    * ``battery_only``: ``battery_npv`` is the option's NPV.
    * ``battery_removed_same_pv`` (a ``bess_pv`` option): the option less a
      PV-only reference — the same fork with the StorageUnit OMITTED (never
      fixed at zero, BC-1) and PV fixed at the option's size, re-dispatched.
      The PV rows of the two cases cancel exactly.

    ``status`` is ``not_established`` when the reference was never computed
    (the verdict then cannot name the option), ``skipped`` for a battery
    sized to zero (``size_zero_no_investment``: judged by its size, its NPV
    is not read for a sign).
    """

    _figure_fields: ClassVar[tuple[str, ...]] = (
        "battery_npv", "option_npv", "reference_npv", "battery_p_nom_mw",
        "battery_payback_simple")

    option_id: str
    status: SectionStatus = "not_established"
    method: Literal["battery_only", "battery_removed_same_pv"]
    battery_p_nom_mw: float | None
    battery_npv: float | None
    option_npv: float | None
    reference_npv: float | None
    battery_payback_simple: float | None = None
    currency_year: int | None = None
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    fidelity: Fidelity | None = None
    engine: Engine = "cash_flow_expander"
    notes: tuple[str, ...] = ()


class Findings(_Model):
    options: list[OptionResult] = Field(default_factory=list)
    # `not_established` when a run stopped before every option solved;
    # `pending_options` then names the option ids never reached (S4).
    options_status: SectionStatus = "not_established"
    pending_options: list[str] = Field(default_factory=list)
    hashes: FindingsHashes = Field(default_factory=FindingsHashes)
    baseline: BaselineResult
    verdict: Verdict = Field(default_factory=Verdict)
    robustness: Robustness = Field(default_factory=Robustness)
    explain: list[dict[str, Any]] = Field(default_factory=list)
    completeness: dict[str, SectionStatus] = Field(default_factory=dict)
    honesty_notes: tuple[str, ...] = ()
    # S6: the battery's value per option, and the value streams of the option
    # the verdict names (the bill's six components in four streams).
    battery_attribution: list[BatteryAttribution] = Field(default_factory=list)
    value_streams: list[ValueStream] = Field(default_factory=list)
    value_streams_option: str | None = None
    value_streams_status: SectionStatus = "not_established"
    # S8 (gate S6 carry): what the streams are measured against — the
    # grid-only `baseline` (the option's full saving) or, for a `bess_pv`
    # option, its `pv_only_reference` (the battery's increment). None when
    # the streams are not established. The waterfall is labelled by it.
    value_streams_basis: Literal["baseline", "pv_only_reference"] | None = None

    @model_validator(mode="after")
    def _completeness_matches_sections(self):
        sections = {"options": self.options_status,
                    "verdict": self.verdict.status,
                    "robustness": self.robustness.status}
        if not self.completeness:
            self.completeness = dict(sections)
        _completeness_agrees(sections, self.completeness, "Findings")
        return self


# ── 4.7 DecisionReport ────────────────────────────────────────────────────

class ProseParagraph(_Model):
    text: str
    ai: bool = False
    reviewed: bool = False


class ReportSection(SectionState):
    """
    A report section: the Energy Hub ``SectionState`` (status, payload,
    note) plus the facts it may cite, its chart refs and its prose.
    """

    model_config = ConfigDict(extra="forbid")

    facts: dict[str, Figure] = Field(default_factory=dict)
    figures: list[str] = Field(default_factory=list)
    prose: list[ProseParagraph] = Field(default_factory=list)


class ReportDisclosure(_Model):
    """
    One honesty code and the human sentence behind it (S7): ``source`` says
    whose sentence it is — the study's own code table
    (``services/study/report.py::HELP``), the tariff's ``honesty_help``, or
    the fallback for a code nothing explains (named, never dropped).
    Disclosures are DATA, rendered before the first number and outside
    ``validate_prose``'s scope (a supplied tariff's sentence is its author's
    text, like a ledger source).
    """

    code: str
    text: str
    source: Literal["study", "tariff", "fallback"] = "study"


class DecisionReport(_Model):
    study_id: str
    question_id: str
    generated_at: datetime
    # S7 additions (all optional; an S1 payload validates unchanged).
    study_name: str | None = None
    question_title: str | None = None
    # Every fact any section's prose may cite: `{{fact_id}}` -> its Figure
    # (value, unit, basis, currency year, engine, fidelity; null + flag).
    facts: dict[str, Figure] = Field(default_factory=dict)
    # Spec §4.7: AI paragraphs carry `ai: true, reviewed: bool`. MVP-1 writes
    # none; the field is present and empty (plan S7).
    ai_paragraphs: list[ProseParagraph] = Field(default_factory=list, max_length=0)
    # Rendered BEFORE the first number (spec §7 rules).
    required_disclosures: list[ReportDisclosure] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    not_established: list[str] = Field(default_factory=list)
    # Code -> sentence for every code the report shows (honesty notes,
    # reasons, skip codes, stale reasons).
    honesty_help: dict[str, str] = Field(default_factory=dict)
    basis: FinancialBasis = Field(default_factory=FinancialBasis)
    currency_year: int | None = None
    fidelity: Fidelity | None = None
    maturity: StudyMaturity = Field(default_factory=StudyMaturity)
    sections: dict[str, ReportSection] = Field(default_factory=dict)
    completeness: dict[str, SectionStatus] = Field(default_factory=dict)
    # The hashes the findings were computed from; `stale` when the ledger or
    # an option fork's network no longer matches them (S7).
    hashes_at_findings: FindingsHashes | None = None
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    honesty_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _completeness_matches_sections(self):
        statuses = {k: v.status for k, v in self.sections.items()}
        if not self.completeness:
            self.completeness = dict(statuses)
        _completeness_agrees(statuses, self.completeness, "DecisionReport")
        return self
