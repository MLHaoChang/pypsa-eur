"""
Edge Investment Case — commercial contracts (Phase 0, WP0.1).

Design: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §4.1
Plan:   docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.1

Skeleton shapes only — no billing, LP-binding or library behaviour. Later
phases FILL fields; they do not renegotiate names or enums pinned here.
Nothing here imports ``services``.
"""
from __future__ import annotations

import math

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import AfterValidator, BaseModel, Field, model_validator

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
TariffUnit = Literal["per_kwh", "per_kw_month", "per_kw_year", "per_month", "per_kva_year",
                     "per_day"]
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


# ------------------------------------------------------------------ value flows (P3 WP3.0)

# The default external counterparties of a value-flow ledger (plan P3 WP3.0).
# Tariff items default to `retailer` (energy, fixed, certificate), `dso`
# (demand, capacity) and `tax_authority` (tax_levy); asset costs go to the
# supplier externals; the export price and grid-side commodity to `market`.
DEFAULT_EXTERNALS = ("retailer", "dso", "tso", "market", "tax_authority",
                     "capex_supplier", "om_contractor")


class TariffPayeeRule(BaseModel):
    """Who is paid a tariff item: by item id (wins) or by item kind."""

    kind: TariffItemKind | None = None
    item_id: str | None = None
    payee: str = Field(min_length=1)

    @model_validator(mode="after")
    def _names_a_target(self) -> "TariffPayeeRule":
        if self.kind is None and self.item_id is None:
            raise ValueError("a tariff payee rule names a kind or an item_id")
        return self


class AssetOwnership(BaseModel):
    asset_id: str = Field(min_length=1)
    component: Literal["Generator", "StorageUnit", "Store", "Link", "Line", "Transformer"]
    owner: str = Field(min_length=1)


class HubMember(BaseModel):
    link: str = Field(min_length=1)
    participant: str = Field(min_length=1)
    contracted_mw: float | None = Field(default=None, gt=0)


class ValueFlowConfig(BaseModel):
    """Participants and the assignment of every money flow (spec §7; P3 WP3.0).

    Structural checks only: parties are checked against the contracts and the
    network at the value-flows route (`services.commercial.participants`), so a
    stale party is a ledger flag, never an invalid commercial config. Stored RAW
    on `CommercialConfig.value_flows` and validated lazily (plan C8)."""

    template: Literal["single_owner", "btm_ppa", "landlord_tenant", "dso_developer",
                      "energy_hub", "custom"] = "custom"
    template_version: str | None = None
    built_digest: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    externals: list[str] = Field(default_factory=lambda: list(DEFAULT_EXTERNALS))
    tariff_payees: list[TariffPayeeRule] = Field(default_factory=list)
    asset_owners: list[AssetOwnership] = Field(default_factory=list)
    hub_members: list[HubMember] = Field(default_factory=list)
    allocation: AllocationKey | None = None
    export_revenue_to: Literal["site_party", "asset_owner"] = "site_party"


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


LibraryItemKind = Literal["tariff", "contract", "connection_agreement"]


class LibraryItemRef(BaseModel):
    """A pinned Library item version (P2 WP2.4a): `hash` is the sha256 of the
    item's canonical JSON — `hashing.library_item_digest(model)` for a tariff
    or a connection agreement; for a contract, the canonical JSON with its
    `type` added (`services/library/items._canonical_bytes`)."""

    kind: LibraryItemKind
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    hash: str = Field(min_length=64, max_length=64)


class TariffPeriod(BaseModel):
    """Season × weekday × time-window applicability with one rate.

    Empty ``months``/``weekdays`` mean "all". ``start_hour``/``end_hour`` are
    [start, end) on the local clock; both None means the whole day. Periods
    are evaluated in list order; the first match wins, so a catch-all goes last.
    """

    name: str = Field(min_length=1)
    rate: float = Field(allow_inf_nan=False)  # a NaN rate would price nothing silently
    months: list[int] = Field(default_factory=list)
    weekdays: list[int] = Field(default_factory=list)  # 0 = Monday
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=1, le=24)
    # Tier rates of THIS period (URDB `energyratestructure[period][tier]`), aligned
    # with the item's `tiers` thresholds (IC P2 WP2.1a-ii). Set on every period of a
    # windowed tiered item, whose `Tier.rate` are then 0; absent otherwise.
    tier_rates: list[float] | None = None

    @model_validator(mode="after")
    def _window(self) -> "TariffPeriod":
        if self.tier_rates is not None and not all(math.isfinite(r) for r in self.tier_rates):
            raise ValueError("tier_rates must be finite")
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

    threshold: float = Field(ge=0, allow_inf_nan=False)
    rate: float = Field(allow_inf_nan=False)


class Ratchet(BaseModel):
    """A demand ratchet: billed demand ≥ `share` × the maximum ACTUAL peak of the
    lookback months (IC P2 WP2.1a-iii adds two URDB modes):

    * range mode (`lookback_months = N`, URDB `lookbackRange`): the N months
      before; with `cyclic_year` the lookback wraps within the rate year
      (January reads December of the SAME year — REopt's steady-state year);
    * months mode (`months = [...]`, URDB `lookbackMonths`): year-wide — every
      month of the rate year is billed at least `share` × the maximum actual
      peak over the designated months of that year.

    Exactly one of `lookback_months` / `months` is set.
    """

    lookback_months: int | None = Field(default=None, ge=1, le=36)
    share: float = Field(gt=0, le=1)
    months: list[int] | None = None
    cyclic_year: bool = False

    @model_validator(mode="after")
    def _one_mode(self) -> "Ratchet":
        if (self.lookback_months is None) == (self.months is None):
            raise ValueError("a ratchet sets exactly one of lookback_months (range mode) and "
                             "months (designated months)")
        if self.months is not None:
            if not self.months or any(m < 1 or m > 12 for m in self.months) \
                    or len(set(self.months)) != len(self.months):
                raise ValueError("ratchet months are unique values in 1..12")
            if self.cyclic_year:
                raise ValueError("cyclic_year applies to range mode only (months mode is "
                                 "year-wide by definition)")
        if self.cyclic_year and self.lookback_months is not None and self.lookback_months > 11:
            raise ValueError("cyclic_year wraps within one rate year: lookback_months ≤ 11")
        return self


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

    @model_validator(mode="before")
    @classmethod
    def _migrate_p1_windowed_tiers(cls, data):
        """The P1 shape — tiers with their rates on `Tier.rate` and periods that
        are not one catch-all — means "the same tier rates in every window".
        Migrate it losslessly to per-period `tier_rates` (and `Tier.rate` 0), so
        persisted P1 configs keep validating and a single windowed period bills
        only inside its window (IC P2 WP2.1a-ii review #1/#2)."""
        if not isinstance(data, dict):
            return data
        tiers, periods = data.get("tiers"), data.get("periods")
        if not isinstance(tiers, list) or not isinstance(periods, list) or not tiers or not periods:
            return data                     # malformed or absent: field validation reports it

        def get(o, k):
            return o.get(k) if isinstance(o, dict) else getattr(o, k, None)

        if any(get(per, "tier_rates") is not None for per in periods):
            return data
        catch_all = (len(periods) == 1 and not get(periods[0], "months")
                     and not get(periods[0], "weekdays") and get(periods[0], "start_hour") is None)
        if catch_all:
            return data                     # P1 single catch-all tiers: unchanged semantics
        raw = [get(t, "rate") for t in tiers]
        if not all(isinstance(r, (int, float)) and not isinstance(r, bool) for r in raw):
            return data                     # a bad rate: field validation reports it
        rates = [float(r) for r in raw]

        def with_rates(per):
            if isinstance(per, dict):
                return {**per, "tier_rates": list(rates)}
            return per.model_copy(update={"tier_rates": list(rates)})

        def zeroed(t):
            if isinstance(t, dict):
                return {**t, "rate": 0.0}
            return t.model_copy(update={"rate": 0.0})

        return {**data, "periods": [with_rates(per) for per in periods],
                "tiers": [zeroed(t) for t in tiers]}

    @model_validator(mode="after")
    def _tiers_monotone(self) -> "TariffItem":
        if self.tiers:
            th = [t.threshold for t in self.tiers]
            if any(b <= a for a, b in zip(th, th[1:])):
                raise ValueError("tier thresholds must be strictly increasing")
        if self.ratchet is not None and self.kind != "demand":
            raise ValueError("ratchet only applies to demand items")
        with_rates = [per for per in self.periods if per.tier_rates is not None]
        if with_rates and not self.tiers:
            raise ValueError(f"item {self.id!r}: tier_rates need the item's tiers (thresholds)")
        p0 = self.periods[0]
        windowed = len(self.periods) > 1 or bool(p0.months or p0.weekdays
                                                 or p0.start_hour is not None)
        if self.tiers and (with_rates or windowed):
            # A windowed tiered item (IC P2 WP2.1a-ii): one threshold list, each
            # period's own rates; `Tier.rate` must be 0 so rates have one source.
            for per in self.periods:
                if per.tier_rates is None or len(per.tier_rates) != len(self.tiers):
                    raise ValueError(
                        f"item {self.id!r}: every period of a windowed tiered item needs "
                        f"tier_rates with {len(self.tiers)} rates (period {per.name!r})")
            if any(t.rate != 0 for t in self.tiers):
                raise ValueError(f"item {self.id!r}: a windowed tiered item takes its rates from "
                                 "the periods' tier_rates; every Tier.rate must be 0")
        if self.kind == "demand" or self.measured_on == "peak_import":
            # Billed as demand (the engine's and LP's `_is_demand`). A demand
            # WINDOW is the set of periods sharing a name (a URDB period is
            # several [start, end) fragments; IC P2 WP2.1a-0), and it has ONE
            # rate in any month. Checked on the EFFECTIVE first match, exactly:
            # matching depends only on month, weekday and whole hour, so the
            # 12 × 7 × 24 grid covers every case — a shadowed same-name default
            # stays valid, weekday/weekend variants at two rates do not.
            if "|" in self.id or any("|" in per.name for per in self.periods):
                raise ValueError(f"demand item {self.id!r}: '|' is reserved (it joins the LP "
                                 "peak keys) and may not appear in the item id or period names")
            seen: dict[tuple[int, str], float] = {}
            for month in range(1, 13):
                for weekday in range(7):
                    for hour in range(24):
                        for per in self.periods:
                            if per.months and month not in per.months:
                                continue
                            if per.weekdays and weekday not in per.weekdays:
                                continue
                            if per.start_hour is not None and not (
                                    per.start_hour <= hour < per.end_hour):
                                continue
                            val = (per.rate, tuple(per.tier_rates or ()))
                            prev = seen.setdefault((month, per.name), val)
                            if prev != val:
                                raise ValueError(
                                    f"demand item {self.id!r}: the window {per.name!r} has two "
                                    f"rates ({prev} and {val}) in month {month}; one demand "
                                    "window has one rate per month")
                            break
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
    # URDB fields a partial import could not map (P2 WP2.4b-i): the engine
    # bills the rest and flags `tariff_incomplete` with `total = None`.
    unsupported_fields: list[str] = Field(default_factory=list)

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
    # The Library template this agreement was copied from (P2 WP2.4a):
    # provenance only — nothing resolves it at solve time.
    library_ref: LibraryItemRef | None = None

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


def _unique_ids(v: list[str]) -> list[str]:
    """A contract names each asset or load once: a repeated id would settle
    its output twice (WP2.2d review #2)."""
    dup = sorted({x for x in v if v.count(x) > 1})
    if dup:
        raise ValueError(f"ids listed more than once: {dup}")
    return v


UniqueIds = Annotated[list[str], AfterValidator(_unique_ids)]


class PpaContract(BaseModel):
    # The discriminator of `CommercialConfig.contracts` (P2 WP2.2-0), with a
    # per-class default so `PpaContract(...)` without it still constructs.
    type: Literal["ppa"] = "ppa"
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
    asset_ids: UniqueIds = Field(min_length=1)
    reference_price: TimeSeriesRef | None = None
    changes_dispatch: bool = False
    # P2 WP2.2a (every field optional or defaulted: P0 JSON stays valid).
    base_year: int | None = None
    library_ref: LibraryItemRef | None = None
    pricing: Literal["fixed", "market_plus_premium"] = "fixed"
    premium_eur_per_mwh: float | None = None          # any sign; indexed like `price`
    baseload_mw: float | None = Field(default=None, ge=0)
    sleeving_fee_eur_per_mwh: float | None = Field(default=None, ge=0)
    sleeving_party: str | None = None

    @model_validator(mode="after")
    def _floor_below_cap(self) -> "PpaContract":
        if self.floor is not None and self.cap is not None and self.floor > self.cap:
            raise ValueError("PPA floor must not exceed cap")
        if self.pricing == "market_plus_premium":
            # Allowed with pay_as_produced / as_consumed_btm / sleeved; a
            # baseload PPA settles `price` against the reference (financial).
            if self.kind == "baseload":
                raise ValueError("a baseload PPA settles price against the reference; "
                                 "market_plus_premium is refused")
            if self.premium_eur_per_mwh is None:
                raise ValueError("market_plus_premium needs premium_eur_per_mwh")
            if self.reference_price is None:
                raise ValueError("market_plus_premium needs a reference_price")
        return self


class CfdContract(BaseModel):
    # The discriminator of `CommercialConfig.contracts` (P2 WP2.2-0), with a
    # per-class default so `CfdContract(...)` without it still constructs.
    type: Literal["cfd"] = "cfd"
    id: str = Field(min_length=1)
    strike: float = Field(ge=0)
    reference_price: TimeSeriesRef | None = None
    tenor_years: int = Field(ge=1)
    asset_ids: UniqueIds = Field(min_length=1)
    # P2 WP2.2a (optional or defaulted).
    base_year: int | None = None
    library_ref: LibraryItemRef | None = None
    generator_owner: str | None = None
    counterparty: str | None = None
    indexation_pct_per_year: float = 0.0
    reference: Literal["interval", "monthly_capture"] = "interval"
    suspend_on_negative_price: bool = False


class DrContract(BaseModel):
    # The discriminator of `CommercialConfig.contracts` (P2 WP2.2-0), with a
    # per-class default so `DrContract(...)` without it still constructs.
    type: Literal["dr"] = "dr"
    id: str = Field(min_length=1)
    availability_eur_per_mw_year: float = Field(ge=0)
    activation_eur_per_mwh: float = Field(ge=0)
    max_events: int | None = Field(default=None, ge=0)
    max_duration_h: float | None = Field(default=None, gt=0)
    notice_h: float | None = Field(default=None, ge=0)
    asset_ids: UniqueIds = Field(default_factory=list)
    load_ids: UniqueIds = Field(default_factory=list)
    # P2 WP2.2b (optional): the payer of availability and activation, and the
    # contracted MW availability is paid on (absent ⇒ not established).
    counterparty: str | None = None
    contracted_mw: float | None = Field(default=None, ge=0)
    base_year: int | None = None
    library_ref: LibraryItemRef | None = None

    @model_validator(mode="after")
    def _some_target(self) -> "DrContract":
        if not self.asset_ids and not self.load_ids:
            raise ValueError("DR contract needs asset_ids or load_ids")
        return self


class LeaseContract(BaseModel):
    # The discriminator of `CommercialConfig.contracts` (P2 WP2.2-0), with a
    # per-class default so `LeaseContract(...)` without it still constructs.
    type: Literal["lease"] = "lease"
    id: str = Field(min_length=1)
    lessor: str
    lessee: str
    annual_payment: float = Field(ge=0)
    tenor_years: int = Field(ge=1)
    asset_ids: UniqueIds = Field(min_length=1)
    base_year: int | None = None                  # P2: all contracts (P4 escalates)
    library_ref: LibraryItemRef | None = None


class EaasContract(BaseModel):
    # The discriminator of `CommercialConfig.contracts` (P2 WP2.2-0), with a
    # per-class default so `EaasContract(...)` without it still constructs.
    type: Literal["eaas"] = "eaas"
    id: str = Field(min_length=1)
    provider: str
    customer: str
    fee_eur_per_mwh: float | None = Field(default=None, ge=0)
    fee_eur_per_year: float | None = Field(default=None, ge=0)
    tenor_years: int = Field(ge=1)
    asset_ids: UniqueIds = Field(min_length=1)
    base_year: int | None = None
    library_ref: LibraryItemRef | None = None

    @model_validator(mode="after")
    def _some_fee(self) -> "EaasContract":
        if self.fee_eur_per_mwh is None and self.fee_eur_per_year is None:
            # A fee-less EaaS would settle a confident 0 (review 2.2b #3).
            raise ValueError("an EaaS contract needs fee_eur_per_mwh or fee_eur_per_year")
        return self


class RetailContract(BaseModel):
    # The discriminator of `CommercialConfig.contracts` (P2 WP2.2-0), with a
    # per-class default so `RetailContract(...)` without it still constructs.
    type: Literal["retail"] = "retail"
    id: str = Field(min_length=1)
    retailer: str
    customer: str
    tariff_id: str
    tenor_years: int = Field(ge=1)
    base_year: int | None = None
    library_ref: LibraryItemRef | None = None


Contract = Annotated[Union[PpaContract, CfdContract, DrContract, LeaseContract, EaasContract,
                           RetailContract], Field(discriminator="type")]


def _contract_type_from_shape(c: dict) -> str:
    """The `type` of an untagged P0-era contract payload, from its fields."""
    if "strike" in c:
        return "cfd"
    if "availability_eur_per_mw_year" in c:
        return "dr"
    if "annual_payment" in c:
        return "lease"
    if "fee_eur_per_mwh" in c or "fee_eur_per_year" in c:
        return "eaas"
    if "tariff_id" in c:
        return "retail"
    return "ppa"


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
    # A Library tariff (P2 WP2.4a). `PUT /solver_config` resolves it into the
    # inline `import_tariff`, which the solve uses; the ref is provenance and
    # drift (preflight checks the inline copy hashes to it).
    import_tariff_ref: LibraryItemRef | None = None
    export_price_ref: TimeSeriesRef | None = None
    # The `poc→grid` Link that carries export (spec §5: export price = −price
    # on a second PoC Link). Required by an export price or an export item.
    export_link: str | None = None
    # The site's IANA zone. Set → naive snapshots are UTC and tariff windows
    # are read on this clock; None → snapshots already are the site clock.
    timezone: str | None = None
    connection: ConnectionAgreement | None = None
    # Contracts settled on the solved dispatch (P2 WP2.2), discriminated by
    # `type`; untagged P0-era payloads are tagged from their shape.
    contracts: list[Contract] = Field(default_factory=list)
    # The grid's carbon-free share per snapshot, a Library series (P2 WP2.2-0,
    # for WP2.5's CFE score).
    grid_cfe_share_ref: TimeSeriesRef | None = None
    # Who the site is in contract parties (P2 WP2.2c): a PPA the site SELLS
    # while its output also earns the export price is a double count.
    site_party: str = Field(default="site", min_length=1)
    group_contract: str | None = None
    # Energy-hub group contract (WP1.6): the member PoC Links whose combined
    # import is capped at `group_cap_mw` in every snapshot.
    group_members: list[str] = Field(default_factory=list)
    group_cap_mw: float | None = Field(default=None, ge=0)
    # Reserved: a selection is refused at binding in P1 (every demand item is charged).
    demand_items: list[str] = Field(default_factory=list)
    # Metered monthly import peaks before the horizon, {"YYYY-MM": kW} on the
    # site clock: the seed a ratchet's lookback needs (WP1.5b).
    meter_history_peaks_kw: dict[str, float] = Field(default_factory=dict)
    # Metered import ENERGY per month, {"YYYY-MM": kWh} (P2 WP2.1c-ii): a
    # non-convex tier is priced in the LP at the tier the same month a year
    # earlier landed in (spec §5.3); absent ⇒ its first tier.
    meter_history_energy_kwh: dict[str, float] = Field(default_factory=dict)
    # P6 hook (windowed dispatch): a floor on a month's modelled peak, {"YYYY-MM": MW}.
    initial_peak_lower_bound: dict[str, float] = Field(default_factory=dict)
    # The site's power factor for tariff items billed per kVA (P2 WP2.1a-i);
    # absent ⇒ a `per_kva_year` item is not established (ADR-0001).
    power_factor: float | None = Field(default=None, gt=0, le=1)
    # Participants and value-flow assignment (P3 WP3.0), stored RAW: validated
    # into `ValueFlowConfig` lazily by `services.commercial.participants`, so a
    # stored value that later fails a rule never fails a solve or invalidates
    # the commercial rows. Written only through its own route; never read by
    # the LP or any committed hash (decision 9).
    value_flows: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _tag_untagged_contracts(cls, data):
        # A discriminated union refuses a dict without its tag even when the
        # field has a default (`union_tag_not_found`): tag P0-era payloads
        # from their shape before the union validates (WP2.2a).
        if isinstance(data, dict) and isinstance(data.get("contracts"), list):
            data = {**data, "contracts": [
                {**c, "type": _contract_type_from_shape(c)}
                if isinstance(c, dict) and "type" not in c else c
                for c in data["contracts"]]}
        return data

    @model_validator(mode="after")
    def _unique_contract_ids(self) -> "CommercialConfig":
        ids = [c.id for c in self.contracts]
        if len(ids) != len(set(ids)):
            # One `ic:contract:<id>` column per contract (WP2.2-0 review 0b #3).
            raise ValueError("contract ids must be unique")
        return self

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

        for name in ("meter_history_peaks_kw", "initial_peak_lower_bound",
                     "meter_history_energy_kwh"):
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
