"""
Edge Investment Case — commercial contracts (Phase 0, WP0.1).

Design: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §4.1
Plan:   docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.1

Skeleton shapes only — no billing, LP-binding or library behaviour. Later
phases FILL fields; they do not renegotiate names or enums pinned here.
Nothing here imports ``services``.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ------------------------------------------------------------------ enums

ParticipantRole = Literal[
    "site_owner", "developer", "investor", "lender", "tax_equity", "dso",
    "tso", "retailer", "tenant", "landlord", "hub_member", "offtaker", "other",
]

ValueStreamKind = Literal[
    "energy_import", "energy_export", "network_capacity", "network_energy",
    "demand_charge", "retail_fixed", "ancillary", "dr_availability",
    "dr_activation", "ppa_settlement", "cfd_settlement", "certificates",
    "lease", "eaas_fee", "fuel", "fom", "vom", "capex", "incentive", "tax",
    "debt_service", "other",
]

TariffItemKind = Literal["energy", "demand", "capacity", "fixed", "certificate", "tax_levy"]
TariffUnit = Literal["per_kwh", "per_kw_month", "per_kw_year", "per_month", "per_kva_year"]
Settlement = Literal["15min", "30min", "h"]
MeasuredOn = Literal["import", "export", "net", "peak_import"]
Direction = Literal["cost", "revenue"]
ConnectionKind = Literal["firm", "non_firm_static", "non_firm_dynamic", "fca"]
PpaKind = Literal["pay_as_produced", "baseload", "as_consumed_btm", "sleeved"]
MarketRegion = Literal["DE", "NL", "GB", "US_ERCOT", "US_PJM", "US_CAISO", "CA_ON", "AU_NEM"]


# ------------------------------------------------------------------ participants


class Participant(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: ParticipantRole
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class ValueStream(BaseModel):
    id: str = Field(min_length=1)
    kind: ValueStreamKind
    label: str | None = None


class AllocationKey(BaseModel):
    """How a shared stream is split across participants (energy-hub templates)."""

    basis: Literal["contracted_capacity", "peak_contribution", "energy", "fixed_shares"]
    shares: dict[str, float] | None = None  # participant id -> share, for fixed_shares

    @model_validator(mode="after")
    def _fixed_shares_sum_to_one(self) -> "AllocationKey":
        if self.basis == "fixed_shares":
            if not self.shares:
                raise ValueError("fixed_shares needs a non-empty shares map")
            total = sum(self.shares.values())
            if abs(total - 1.0) > 1e-9:
                raise ValueError(f"fixed shares must sum to 1, got {total}")
        return self


# ------------------------------------------------------------------ series refs


class PriceSeriesRef(BaseModel):
    """Reference into the Library series store (spec decision 17)."""

    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    hash: str = Field(min_length=8)
    source: str
    vintage_year: int | None = None
    provider: str | None = None


TimeSeriesRef = PriceSeriesRef  # one ref type; the kind lives in the Library item


# ------------------------------------------------------------------ tariffs


class TariffPeriod(BaseModel):
    """Season × weekday × time-window applicability with one rate.

    Empty ``months``/``weekdays`` mean "all". ``start_hour``/``end_hour`` are
    [start, end) on the local clock; both None means the whole day. Periods
    are evaluated in list order; the first match wins, so a catch-all goes last.
    """

    name: str = Field(min_length=1)
    rate: float
    months: list[int] = Field(default_factory=list)
    weekdays: list[int] = Field(default_factory=list)  # 0 = Monday
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=1, le=24)

    @model_validator(mode="after")
    def _window(self) -> "TariffPeriod":
        if (self.start_hour is None) != (self.end_hour is None):
            raise ValueError("start_hour and end_hour must be set together")
        if self.start_hour is not None and self.end_hour <= self.start_hour:
            raise ValueError("end_hour must be after start_hour")
        if any(m < 1 or m > 12 for m in self.months):
            raise ValueError("months are 1..12")
        if any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("weekdays are 0..6")
        return self


class Tier(BaseModel):
    """Cumulative-volume tier: applies to volume above ``threshold`` up to the next one."""

    threshold: float = Field(ge=0)
    rate: float


class Ratchet(BaseModel):
    lookback_months: int = Field(ge=1, le=36)
    share: float = Field(gt=0, le=1)


class TariffItem(BaseModel):
    id: str = Field(min_length=1)
    kind: TariffItemKind
    unit: TariffUnit
    periods: list[TariffPeriod] = Field(min_length=1)
    tiers: list[Tier] | None = None
    ratchet: Ratchet | None = None
    settlement: Settlement = "15min"
    measured_on: MeasuredOn = "import"
    direction: Direction = "cost"

    @model_validator(mode="after")
    def _tiers_monotone(self) -> "TariffItem":
        if self.tiers:
            th = [t.threshold for t in self.tiers]
            if any(b <= a for a, b in zip(th, th[1:])):
                raise ValueError("tier thresholds must be strictly increasing")
        if self.ratchet is not None and self.kind != "demand":
            raise ValueError("ratchet only applies to demand items")
        return self


class Tariff(BaseModel):
    id: str = Field(min_length=1)
    name: str
    jurisdiction: str = Field(min_length=2)
    dso_or_retailer: str | None = None
    valid_from: date
    valid_to: date | None = None
    items: list[TariffItem] = Field(min_length=1)
    pack_hash: str | None = None

    @model_validator(mode="after")
    def _unique_item_ids(self) -> "Tariff":
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("tariff item ids must be unique")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        return self


# ------------------------------------------------------------------ connection


class ConnectionAgreement(BaseModel):
    kind: ConnectionKind
    import_cap_mw: float = Field(ge=0)
    export_cap_mw: float | None = Field(default=None, ge=0)
    envelope: TimeSeriesRef | None = None
    curtailment_hours_per_year: float | None = Field(default=None, ge=0)
    curtailment_compensation_eur_per_mwh: float | None = Field(default=None, ge=0)
    capacity_fee: TariffItem | None = None
    available_from: date
    group: str | None = None

    @model_validator(mode="after")
    def _kind_requirements(self) -> "ConnectionAgreement":
        if self.kind == "non_firm_dynamic" and self.envelope is None:
            raise ValueError("non_firm_dynamic needs an envelope series")
        if self.kind == "fca" and self.envelope is None \
                and self.curtailment_hours_per_year is None:
            raise ValueError("fca needs an envelope or curtailment_hours_per_year")
        if self.capacity_fee is not None and self.capacity_fee.kind != "capacity":
            raise ValueError("capacity_fee must be a capacity tariff item")
        return self


# ------------------------------------------------------------------ contracts


class PpaContract(BaseModel):
    id: str = Field(min_length=1)
    kind: PpaKind
    price: float = Field(ge=0)
    indexation_pct_per_year: float = 0.0
    volume_cap_mwh_per_year: float | None = Field(default=None, ge=0)
    floor: float | None = None
    cap: float | None = None
    tenor_years: int = Field(ge=1)
    seller: str
    buyer: str
    asset_ids: list[str] = Field(min_length=1)
    reference_price: TimeSeriesRef | None = None
    changes_dispatch: bool = False

    @model_validator(mode="after")
    def _floor_below_cap(self) -> "PpaContract":
        if self.floor is not None and self.cap is not None and self.floor > self.cap:
            raise ValueError("PPA floor must not exceed cap")
        return self


class CfdContract(BaseModel):
    id: str = Field(min_length=1)
    strike: float = Field(ge=0)
    reference_price: TimeSeriesRef | None = None
    tenor_years: int = Field(ge=1)
    asset_ids: list[str] = Field(min_length=1)


class DrContract(BaseModel):
    id: str = Field(min_length=1)
    availability_eur_per_mw_year: float = Field(ge=0)
    activation_eur_per_mwh: float = Field(ge=0)
    max_events: int | None = Field(default=None, ge=0)
    max_duration_h: float | None = Field(default=None, gt=0)
    notice_h: float | None = Field(default=None, ge=0)
    asset_ids: list[str] = Field(default_factory=list)
    load_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _some_target(self) -> "DrContract":
        if not self.asset_ids and not self.load_ids:
            raise ValueError("DR contract needs asset_ids or load_ids")
        return self


class LeaseContract(BaseModel):
    id: str = Field(min_length=1)
    lessor: str
    lessee: str
    annual_payment: float = Field(ge=0)
    tenor_years: int = Field(ge=1)
    asset_ids: list[str] = Field(min_length=1)


class EaasContract(BaseModel):
    id: str = Field(min_length=1)
    provider: str
    customer: str
    fee_eur_per_mwh: float | None = Field(default=None, ge=0)
    fee_eur_per_year: float | None = Field(default=None, ge=0)
    tenor_years: int = Field(ge=1)
    asset_ids: list[str] = Field(min_length=1)


class RetailContract(BaseModel):
    id: str = Field(min_length=1)
    retailer: str
    customer: str
    tariff_id: str
    tenor_years: int = Field(ge=1)


class AncillaryProduct(BaseModel):
    name: str
    capacity_price: TimeSeriesRef | None = None
    activation_price: TimeSeriesRef | None = None
    min_bid_mw: float | None = Field(default=None, ge=0)


class MarketPack(BaseModel):
    id: str = Field(min_length=1)
    region: MarketRegion
    price_series: dict[str, TimeSeriesRef] = Field(default_factory=dict)
    products: list[AncillaryProduct] = Field(default_factory=list)
    pack_hash: str = Field(min_length=8)
    valid_year: int


# ------------------------------------------------------------------ solver-config boundary


class CommercialConfig(BaseModel):
    """Typed boundary carried on ``SolverConfigSchema.commercial`` (plan WP1.3).

    Dumped to a plain dict on the ``SolverConfig`` dataclass so ``asdict``
    persistence and ``assumptions_hash`` keep working.
    """

    poc_link: str = Field(min_length=1)
    import_tariff_id: str | None = None
    # P1 carries the tariff inline: Library CRUD for tariffs is P2 WP2.4, from
    # which point `import_tariff_id` names a Library tariff (plan WP1.3 note).
    import_tariff: Tariff | None = None
    export_price_ref: TimeSeriesRef | None = None
    # The `poc→grid` Link that carries export (spec §5: export price = −price
    # on a second PoC Link). Required by an export price or an export item.
    export_link: str | None = None
    # The site's IANA zone. Set → naive snapshots are UTC and tariff windows
    # are read on this clock; None → snapshots already are the site clock.
    timezone: str | None = None
    connection: ConnectionAgreement | None = None
    group_contract: str | None = None
    # Energy-hub group contract (WP1.6): the member PoC Links whose combined
    # import is capped at `group_cap_mw` in every snapshot.
    group_members: list[str] = Field(default_factory=list)
    group_cap_mw: float | None = Field(default=None, ge=0)
    demand_items: list[str] = Field(default_factory=list)
    # Metered monthly import peaks before the horizon, {"YYYY-MM": kW} on the
    # site clock: the seed a ratchet's lookback needs (WP1.5b).
    meter_history_peaks_kw: dict[str, float] = Field(default_factory=dict)
    # P6 hook (windowed dispatch): a floor on a month's modelled peak, {"YYYY-MM": MW}.
    initial_peak_lower_bound: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _tariff_and_links(self) -> "CommercialConfig":
        import re as _re

        if bool(self.group_members) != (self.group_cap_mw is not None):
            raise ValueError("a group contract needs both group_members and group_cap_mw")
        if bool(self.group_members) != (self.group_contract is not None):
            # A named group that binds nothing, or members with no contract
            # name, would say nothing about what the solve did (ADR-0001).
            raise ValueError("group_contract and group_members are set together")
        if len(set(self.group_members)) != len(self.group_members):
            raise ValueError("group_members must be unique")

        for name in ("meter_history_peaks_kw", "initial_peak_lower_bound"):
            for k, v in getattr(self, name).items():
                if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", k):
                    raise ValueError(f"{name}: month keys are YYYY-MM, got {k!r}")
                if not (v >= 0):
                    raise ValueError(f"{name}: {k} must be a non-negative number")
        # An id with no inline tariff is valid data (a Library tariff, P2
        # WP2.4); the P1 binding refuses it (`lp_bindings.validate_for_network`).
        if (self.import_tariff_id is not None and self.import_tariff is not None
                and self.import_tariff_id != self.import_tariff.id):
            raise ValueError("import_tariff_id must equal import_tariff.id")
        if self.export_price_ref is not None and self.export_link is None:
            raise ValueError("export_price_ref needs export_link")
        if self.export_link is not None and self.export_link == self.poc_link:
            raise ValueError("export_link must be a different Link from poc_link")
        if self.timezone is not None:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        return self
