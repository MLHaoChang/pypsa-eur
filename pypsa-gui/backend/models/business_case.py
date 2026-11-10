"""
Data-centre business case: the inputs (DC business case slice 02).

Spec: .scratch/dc-business-case/spec.md §4, §5, §9 (owner decision DCBC-SCOPE-1);
GK B conditions BC7 and BC9 (.scratch/dc-business-case/issues/01-design-plan.md).

`BusinessCaseInputs` sits beside `FinanceInputs`: the finance inputs say how the project
is financed and taxed, these say what the data centre sells and what it costs beyond
the energy the network prices. No arithmetic on cash here; the engine is slice 04.

Units are real and named in each field: money is in the finance case's currency and
base-year money (`FinanceInputs.currency`); a GPU-hour price is per GPU per hour, a
colocation price per kW of contracted IT per **month**, capex per MW of IT or per GPU,
shares are fractions in [0, 1], lives are years.

Rules (ADR-0001, DCBC-SCOPE-1 Q4):
- a revenue price has no default: `None` is not established, never 0;
- a cost line with no stated value is `None`; `with_cost_defaults` fills it from the
  defaults pack's `dc-business-case.*` rows (assumed, with source and year) and records
  which fields it filled, so a default is never mistaken for a user's figure;
- `require_runnable` refuses an incomplete case with a typed `BusinessCaseRefused`
  that names the field (BC7): a missing price, GPU-hour revenue without IT hardware
  capex, a revenue model without facility capex. Never a 0 or a guessed value.

Nothing here imports ``services``: the pack is passed in.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

RevenueModel = Literal["gpu_hour", "colocation"]
"""R1 GPU-hour rental (the project owns the IT) and R2 colocation / wholesale lease per
kW-month (the tenant owns the IT). R4 services and SLA credits are later (DCBC-SCOPE-1 Q2).
One model per case: both at once would sell the same IT capacity twice."""

ItCapexBasis = Literal["per_gpu", "per_mw_it"]

HOURS_PER_YEAR = 8760.0
MONTHS_PER_YEAR = 12
KW_PER_MW = 1000.0

_Share = Field(default=None, ge=0.0, le=1.0)
# A typo is refused, never dropped (as FinanceInputs); inf and NaN are refused in every float
# field, so an unbounded field (a price, a capex, a margin) can never carry a non-finite value.
_FORBID = ConfigDict(extra="forbid", allow_inf_nan=False)


class GpuHourRevenue(BaseModel):
    """R1: billable GPU-hours = served IT MWh × 1000 ÷ `kw_per_gpu` × `sold_share`
    (spec §4). u(t) is already in the IT MWh, so `sold_share` is the sold share of the
    utilised capacity, never a second utilisation. A `reserved_share` of the sold hours is
    billed at the contract price, the rest at the on-demand price."""

    model_config = _FORBID

    # Case currency per GPU-hour. No default (DCBC-SCOPE-1 Q4).
    price_per_gpu_hour: float | None = Field(default=None, ge=0.0)
    reserved_share: float = Field(default=0.0, ge=0.0, le=1.0)
    reserved_price_per_gpu_hour: float | None = Field(default=None, ge=0.0)
    # All-in server kW per GPU (racks, CPUs and networking inside); no default: it
    # depends on the system (research §5 gives 1.28 kW for an H100 DGX, 1.79 kW for B200).
    kw_per_gpu: float | None = Field(default=None, gt=0.0)
    sold_share: float = Field(default=1.0, ge=0.0, le=1.0)
    # Real annual indexation of the prices (contract class); negative = price decay.
    indexation: float = Field(default=0.0, gt=-1.0, lt=1.0)

    def gpu_count(self, it_mw: float) -> float:
        """GPUs installed in `it_mw` of IT. Refuses without `kw_per_gpu`."""
        if self.kw_per_gpu is None:
            raise PriceNotEstablished("gpu_hour.kw_per_gpu")
        return it_mw * KW_PER_MW / self.kw_per_gpu

    def gpu_hours(self, served_it_mwh: float) -> float:
        """Billable GPU-hours from served IT energy (MWh): MWh ÷ 1 h = MW, so GPUs × hours."""
        return self.gpu_count(served_it_mwh) * self.sold_share


class ColocationRevenue(BaseModel):
    """R2: contracted IT kW per phase × lease-up × price per kW-month × 12; take-or-pay,
    independent of u(t) (spec §4)."""

    model_config = _FORBID

    # Case currency per kW of contracted IT per month. No default (DCBC-SCOPE-1 Q4).
    price_per_kw_month: float | None = Field(default=None, ge=0.0)
    indexation: float = Field(default=0.0, gt=-1.0, lt=1.0)
    # Per COD phase (the load spec's phase order): the share of contracted kW billed in
    # operating years 1..k, the last entry holding. An absent phase bills 100 % from COD.
    lease_up_by_phase: list[list[float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _lease_up_shares(self) -> ColocationRevenue:
        for i, ramp in enumerate(self.lease_up_by_phase):
            if not ramp:
                raise ValueError(
                    f"lease_up_by_phase[{i}] is empty; omit the phase for 100 % from COD"
                )
            bad = [s for s in ramp if not 0.0 <= s <= 1.0]
            if bad:
                raise ValueError(
                    f"lease_up_by_phase[{i}] shares must be in [0, 1], got {bad}"
                )
        return self

    def rent_per_kw_year(self) -> float:
        """The price per kW-year (12 × the kW-month price). Refuses without a price."""
        if self.price_per_kw_month is None:
            raise PriceNotEstablished("colocation.price_per_kw_month")
        return self.price_per_kw_month * MONTHS_PER_YEAR


class PowerPassThrough(BaseModel):
    """R3: the tenant pays `share` of the energy bill × (1 + `margin`), presented gross
    (GK B plan answer 2): the bill stays a cost, the recovery is revenue."""

    model_config = _FORBID

    enabled: bool = False
    share: float | None = _Share
    margin: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _share_when_enabled(self) -> PowerPassThrough:
        if self.enabled and self.share is None:
            raise ValueError("share is required when the power pass-through is enabled")
        return self


class ItHardware(BaseModel):
    """IT hardware (R1 only): a lump per GPU or per MW of IT (DCBC-SCOPE-1 Q1), straight-line
    over `life_years` (pack default 5, sensitivity 4–6), re-bought every `refresh_years`
    (None: at the end of its life)."""

    model_config = _FORBID

    capex_basis: ItCapexBasis = "per_mw_it"
    # Case currency per GPU or per MW of IT; no default (no primary price list, research §2b).
    capex: float | None = Field(default=None, ge=0.0)
    life_years: float | None = Field(default=None, ge=1.0)
    refresh_years: int | None = Field(default=None, ge=1)

    def capex_total(self, it_mw: float, gpu: GpuHourRevenue | None = None) -> float:
        """The capex for `it_mw` of IT. Per GPU needs `kw_per_gpu` to count the GPUs."""
        if self.capex is None:
            raise ItCapexMissing("it_hardware.capex")
        if self.capex_basis == "per_mw_it":
            return self.capex * it_mw
        if gpu is None:
            raise PriceNotEstablished("gpu_hour.kw_per_gpu")
        return self.capex * gpu.gpu_count(it_mw)


class Facility(BaseModel):
    """Building, shell and M&E fit-out per MW of IT (spec §5), straight-line."""

    model_config = _FORBID

    capex_per_mw_it: float | None = Field(default=None, ge=0.0)
    life_years: float | None = Field(default=None, ge=1.0)


class NonEnergyOpex(BaseModel):
    """The operating costs the energy model does not see (spec §5). Money per MW of IT per
    year; shares as fractions of their base."""

    model_config = _FORBID

    staff_per_mw_year: float | None = Field(default=None, ge=0.0)
    maintenance_share: float | None = _Share  # of facility capex, per year
    insurance_share: float | None = _Share  # of capex, per year
    property_tax_share: float | None = _Share  # of asset value, per year
    network_per_mw_year: float | None = Field(default=None, ge=0.0)
    sga_share: float | None = _Share  # of revenue


class CostDefault(BaseModel):
    """Where a defaulted cost field came from: the pack row, its source and year. Always
    `assumed` (GK B BC9)."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    key: str
    value: float
    unit: str
    source: str = Field(min_length=1)
    source_year: int
    currency: str | None = None
    money_year: int | None = None
    pack: str = Field(min_length=1)  # the pack's stamp: id, version and hash
    status: Literal["assumed"] = "assumed"


class BusinessCaseInputs(BaseModel):
    """The business case's inputs (spec §4–§5). Saved partial: a missing price or cost is
    `None` (not established) until stated; `require_runnable` says what is missing."""

    model_config = _FORBID

    revenue_model: RevenueModel | None = None
    gpu_hour: GpuHourRevenue = Field(default_factory=GpuHourRevenue)
    colocation: ColocationRevenue = Field(default_factory=ColocationRevenue)
    pass_through: PowerPassThrough = Field(default_factory=PowerPassThrough)
    facility: Facility = Field(default_factory=Facility)
    it_hardware: ItHardware = Field(default_factory=ItHardware)
    opex: NonEnergyOpex = Field(default_factory=NonEnergyOpex)
    # Days of revenue outstanding (cash bridge only, never EBITDA: GK B BC3). None = not stated.
    receivable_days: float | None = Field(default=None, ge=0.0, le=365.0)
    # Field path ("facility.capex_per_mw_it") -> the pack default it holds. A field listed
    # here is assumed, not the user's figure; a field whose value differs from its entry is
    # refused, so an edit must drop the entry (the user's figure is then no longer assumed).
    assumed: dict[str, CostDefault] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _assumed_names_cost_fields(self) -> BusinessCaseInputs:
        unknown = sorted(set(self.assumed) - set(COST_DEFAULT_KEYS))
        if unknown:
            raise ValueError(
                f"assumed names {unknown}, which are not defaultable cost fields "
                f"{sorted(COST_DEFAULT_KEYS)}"
            )
        for path, d in self.assumed.items():
            if _get(self, path) != d.value:
                raise ValueError(
                    f"assumed[{path}] holds {d.value} but the field is {_get(self, path)}"
                )
        return self

    def require_runnable(self) -> None:
        """Refuse a case the engine cannot run, naming the field (BC7). Never fills a gap."""
        if self.revenue_model is None:
            raise RevenueModelMissing("revenue_model")
        if self.revenue_model == "gpu_hour":
            # R3 is a colocation recovery; a GPU-hour price is all-in (GK B ruling on #1320).
            if self.pass_through.enabled:
                raise PassThroughNotColocation("pass_through.enabled")
            if self.gpu_hour.price_per_gpu_hour is None:
                raise PriceNotEstablished("gpu_hour.price_per_gpu_hour")
            if (
                self.gpu_hour.reserved_share > 0
                and self.gpu_hour.reserved_price_per_gpu_hour is None
            ):
                raise PriceNotEstablished("gpu_hour.reserved_price_per_gpu_hour")
            if self.gpu_hour.kw_per_gpu is None:
                raise PriceNotEstablished("gpu_hour.kw_per_gpu")
            if self.it_hardware.capex is None:
                raise ItCapexMissing("it_hardware.capex")
            if self.it_hardware.life_years is None:
                raise LifeMissing("it_hardware.life_years")
        else:
            if self.colocation.price_per_kw_month is None:
                raise PriceNotEstablished("colocation.price_per_kw_month")
            # The tenant owns the IT under colocation: owned IT capex would be priced with no
            # revenue that needs it (B5: capex priced exactly once).
            if self.it_hardware.capex is not None:
                raise ItCapexNotOwned("it_hardware.capex")
        if self.facility.capex_per_mw_it is None:
            raise FacilityCapexMissing("facility.capex_per_mw_it")
        if self.facility.life_years is None:
            raise LifeMissing("facility.life_years")

    def with_cost_defaults(self, pack: _Pack, *, currency: str) -> BusinessCaseInputs:
        """A copy with each unset cost field filled from `pack`'s `dc-business-case.*` row and
        listed in `assumed`. A stated field is never overwritten; a revenue price is never
        filled. A money row in another currency than the case's is skipped, never converted."""
        out = self.model_copy(deep=True)
        assumed = dict(out.assumed)
        for path, key in COST_DEFAULT_KEYS.items():
            if _get(out, path) is not None:
                continue
            row = _pack_row(pack, key)
            if row is None or row.value is None:
                continue
            if row.currency is not None and row.currency != currency:
                continue
            _set(out, path, row.value)
            assumed[path] = CostDefault(
                key=key,
                value=row.value,
                unit=row.unit,
                source=row.source,
                source_year=row.source_year,
                currency=row.currency,
                money_year=row.money_year,
                pack=pack.stamp,
            )
        # Re-validate: a pack value outside a field's range is refused, never stored.
        return type(self).model_validate({**out.model_dump(), "assumed": assumed})


#: Defaultable cost field -> its defaults-pack key (pack 2026-11-10 on). Staff, network
#: and IT hardware capex have no row: no primary source (research §2b, §2c).
COST_DEFAULT_KEYS: dict[str, str] = {
    "facility.capex_per_mw_it": "dc-business-case.facility.overnight",
    "facility.life_years": "dc-business-case.facility.lifetime",
    "it_hardware.life_years": "dc-business-case.it_hardware.lifetime",
    "opex.maintenance_share": "dc-business-case.facility.maintenance_share",
    "opex.insurance_share": "dc-business-case.insurance_share",
    "opex.property_tax_share": "dc-business-case.property_tax_share",
    "opex.sga_share": "dc-business-case.sga_share",
}


# ------------------------------------------------------------------ refusals


class BusinessCaseRefused(ValueError):
    """The business case cannot run; `field` names the input (dotted path) and `kind` the
    reason, as `business_case_refused:<kind>`."""

    kind = "refused"

    def __init__(self, field: str):
        self.field = field
        super().__init__(f"business_case_refused:{self.kind}: {field}")

    @property
    def code(self) -> str:
        return f"business_case_refused:{self.kind}"


class RevenueModelMissing(BusinessCaseRefused):
    kind = "revenue_model_missing"


class PriceNotEstablished(BusinessCaseRefused):
    kind = "price_not_established"


class ItCapexMissing(BusinessCaseRefused):
    kind = "it_capex_missing"


class FacilityCapexMissing(BusinessCaseRefused):
    kind = "facility_capex_missing"


class LifeMissing(BusinessCaseRefused):
    kind = "life_missing"


class ItCapexNotOwned(BusinessCaseRefused):
    kind = "it_capex_not_owned"


class PassThroughNotColocation(BusinessCaseRefused):
    kind = "pass_through_not_colocation"


# ------------------------------------------------------------------ helpers


class _PackRow(Protocol):
    value: float | None
    unit: str
    source: str
    source_year: int
    currency: str | None
    money_year: int | None


class _Pack(Protocol):
    @property
    def stamp(self) -> str: ...

    def value(self, key: str) -> Any: ...


def _pack_row(pack: _Pack, key: str) -> _PackRow | None:
    """The pack's row `key`, or None in a pack version before 2026-11-10."""
    try:
        return pack.value(key)
    except LookupError:  # UnknownPackValue; a regional pack without the row
        return None


def _get(model: BaseModel, path: str) -> Any:
    group, name = path.split(".")
    return getattr(getattr(model, group), name)


def _set(model: BaseModel, path: str, value: float) -> None:
    group, name = path.split(".")
    setattr(getattr(model, group), name, value)
