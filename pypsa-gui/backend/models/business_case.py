"""
Business case on every template: the line-item model and its validation (BC-ALL slice 02).

Spec: .scratch/business-case/spec.md §3 (G1, G5–G9, G-C1, G-C2) and §4 (owner decisions
BC-ALL-1, BCA-SCOPE-1); GK B plan GO for slice 02 (P1–P5, 2026-11-10).

A case is a table of `LineItem`s (revenue, opex, capex) beside `FinanceInputs`. No
arithmetic on cash here: the engine is slice 05, linked outputs and unit conversions are
slice 04, presets as data are slice 07.

Units (G5): `unit` is the quantity's unit; `price` is currency per `unit`, in the line's
`money_year` money (None = the case's base year). No conversion happens here.

Rules:
- Parse time (pydantic): types, closed enums, `Finite` on every float, `extra=forbid`, and
  the shape faults (a column the line's kind, source or escalation does not take).
- Run time: `refusals()` returns every typed refusal (G9), `BusinessCaseRefused` with
  `.field = "lines[<i>].<column>"`, in line order and, within a line, a fixed column order
  with "not established" last. `require_runnable()` raises the first. A case with a
  refusal still saves; the route slice owns the save policy (GK B Q1).
- A revenue price has no default: `None` is `price_not_established`, never 0. A cost
  price still `None` after `with_cost_defaults` is `cost_not_established` (GK B P1).

Nothing here imports ``services``: the pack is passed in.
"""

from __future__ import annotations

from typing import Any, Literal, NamedTuple, Protocol, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from models.schemas import Finite

# ------------------------------------------------------------------ closed lists

Kind = Literal["revenue", "opex", "capex"]
RevenueCategory = Literal[
    "product_sale",
    "by_product",
    "service_fee",
    "lease",
    "capacity_payment",
    "tariff",
    "cost_recovery",
    "incentive",
    "other",
]
OpexCategory = Literal[
    "staff",
    "maintenance",
    "insurance",
    "property_tax",
    "feedstock",
    "consumables",
    "network",
    "land",
    "sga",
    "other",
]
# No `land`: it does not depreciate (spec §11).
CapexCategory = Literal["facility", "equipment", "it_hardware", "other"]
Category = Literal[RevenueCategory, OpexCategory, CapexCategory]

CATEGORIES: dict[str, tuple[str, ...]] = {
    "revenue": get_args(RevenueCategory),
    "opex": get_args(OpexCategory),
    "capex": get_args(CapexCategory),
}

Unit = Literal[
    "MWh",
    "MWh_H2",
    "kg",
    "t",
    "GPU_h",
    "kW_month",
    "kW_year",
    "MW_year",
    "unit",
    "unit_month",
    "lump",
    "share",
]

#: `models.finance.ESCALATION_CLASSES` plus `contract` (a test pins the two equal).
Escalation = Literal["opex", "fuel", "tariff", "ppa", "export", "capex", "contract"]
Depreciation = Literal["straight_line", "declining_balance"]
#: The columns a pack default may fill (`default_keys`, `assumed`).
DefaultColumn = Literal["price", "quantity", "life_years", "rate"]

#: (kind, category) -> (ledger stream, stream class), spec §3 (G-C1, GK B (a)). A capex
#: line is an asset, not a stream. Slice 05 adds the `STREAM_CLASS` rows.
CATEGORY_STREAM: dict[tuple[str, str], tuple[str | None, str]] = {
    ("revenue", "product_sale"): ("product_revenue", "contract"),
    ("revenue", "by_product"): ("byproduct_revenue", "contract"),
    ("revenue", "service_fee"): ("service_revenue", "contract"),
    ("revenue", "lease"): ("lease_revenue", "contract"),
    ("revenue", "capacity_payment"): ("capacity_revenue", "contract"),
    ("revenue", "tariff"): ("tariff_revenue", "contract"),
    ("revenue", "cost_recovery"): ("power_passthrough", "contract"),
    ("revenue", "incentive"): ("premium_revenue", "contract"),
    ("revenue", "other"): ("other_revenue", "contract"),
    **{("opex", c): (f"bc_{c}", "opex") for c in get_args(OpexCategory)},
    **{("capex", c): (None, "capex") for c in get_args(CapexCategory)},
}

_ID = r"[A-Za-z0-9_.\-]+"
#: `typed`, `linked:<output id>@<catalogue version>` (syntax only; 04 resolves it), or
#: `share_of:<kind>[/<category>]`.
QUANTITY_SOURCE_PATTERN = (
    rf"^(typed|linked:{_ID}@{_ID}|share_of:(revenue|opex|capex)(/[a-z_]+)?)$"
)
PRESET_PATTERN = rf"^{_ID}@{_ID}$"
MONEY_YEAR_MIN = 1990
MONEY_YEAR_AFTER_BASE = 5

# A typo is refused, never dropped; inf and NaN are refused in every float (#1320 F1).
_FORBID = ConfigDict(extra="forbid", allow_inf_nan=False)

# ------------------------------------------------------------------ models


class CostDefault(BaseModel):
    """Where a defaulted column came from: the pack row, its source and year. Always
    `assumed` (GK B BC9)."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    key: str
    value: Finite
    unit: str
    source: str = Field(min_length=1)
    source_year: int
    currency: str | None = None
    money_year: int | None = None
    pack: str = Field(min_length=1)  # the pack's stamp: id, version and hash
    status: Literal["assumed"] = "assumed"


class PresetRule(BaseModel):
    """What a preset's revenue row owns (G7, G-C3, G-C4). Written by the preset, read-only."""

    model_config = _FORBID

    requires: list[CapexCategory] = Field(default_factory=list)
    excludes: list[CapexCategory] = Field(default_factory=list)
    price_includes_energy: bool = False

    @model_validator(mode="after")
    def _requires_and_excludes_disjoint(self) -> PresetRule:
        both = sorted(set(self.requires) & set(self.excludes))
        if both:
            raise ValueError(f"{both} both required and excluded")
        return self


class Cell(NamedTuple):
    """One year's value of a line's column: the value (None = not established), the money
    year it is in (None = the case's base year), and whether the row's escalation applies."""

    value: float | None
    money_year: int | None
    escalates: bool


class LineItem(BaseModel):
    """One row of the business-case table (spec §4). No tax column (G-C2)."""

    model_config = _FORBID

    name: str = Field(min_length=1, max_length=80)
    kind: Kind
    category: Category
    unit: Unit
    quantity_source: str = Field(default="typed", pattern=QUANTITY_SOURCE_PATTERN)
    quantity: Finite | None = None
    quantity_by_year: dict[int, Finite] = Field(default_factory=dict)
    price: Finite | None = None
    price_by_year: dict[int, Finite] = Field(default_factory=dict)
    money_year: int | None = None
    escalation: Escalation
    indexation: Finite | None = None  # fraction per year, `contract` only
    start_year: int | None = None
    end_year: int | None = None
    depreciation: Depreciation | None = None  # capex: None reads straight_line
    life_years: Finite | None = Field(default=None, ge=1.0)
    rate: Finite | None = Field(default=None, gt=0.0, le=1.0)
    refresh_years: Finite | None = Field(default=None, ge=1.0)
    note: str = ""
    # Column -> the pack default it holds. Empty = every column is the user's (`stated`). A
    # column listed here must hold the default's value: an edit drops the entry.
    assumed: dict[DefaultColumn, CostDefault] = Field(default_factory=dict)
    # Column -> its defaults-pack key, written by the preset (07).
    default_keys: dict[DefaultColumn, str] = Field(default_factory=dict)
    preset_rule: PresetRule | None = None

    # -- shape faults (GK B Q3): a column the line does not take is a parse error

    @field_validator("depreciation", "life_years", "rate", "refresh_years")
    @classmethod
    def _capex_only(cls, v: Any, info: ValidationInfo) -> Any:
        if v is None:
            return v
        if info.data.get("kind") != "capex":
            raise ValueError(f"{info.field_name} applies to capex lines only")
        if (
            info.field_name == "rate"
            and info.data.get("depreciation") != "declining_balance"
        ):
            raise ValueError("rate applies to declining_balance only")
        return v

    @field_validator("indexation")
    @classmethod
    def _contract_only(cls, v: Any, info: ValidationInfo) -> Any:
        if v is not None and info.data.get("escalation") != "contract":
            raise ValueError("indexation applies to escalation `contract` only")
        return v

    @field_validator("quantity", "quantity_by_year")
    @classmethod
    def _not_on_linked(cls, v: Any, info: ValidationInfo) -> Any:
        if v not in (None, {}) and str(info.data.get("quantity_source")).startswith(
            "linked:"
        ):
            raise ValueError(f"a linked line takes no {info.field_name}")
        return v

    @field_validator("price", "price_by_year")
    @classmethod
    def _not_on_share(cls, v: Any, info: ValidationInfo) -> Any:
        if v not in (None, {}) and str(info.data.get("quantity_source")).startswith(
            "share_of:"
        ):
            raise ValueError(f"a share_of line takes no {info.field_name}")
        return v

    @field_validator("assumed", "default_keys")
    @classmethod
    def _costs_only(cls, v: Any, info: ValidationInfo) -> Any:
        if v and info.data.get("kind") == "revenue":
            raise ValueError(
                f"a revenue line takes no {info.field_name}: never a default"
            )
        return v

    @field_validator("preset_rule")
    @classmethod
    def _revenue_only(cls, v: Any, info: ValidationInfo) -> Any:
        if v is not None and info.data.get("kind") != "revenue":
            raise ValueError("preset_rule applies to revenue lines only")
        return v

    @model_validator(mode="after")
    def _assumed_matches_and_method(self) -> LineItem:
        for col, d in self.assumed.items():
            if getattr(self, col) != d.value:
                raise ValueError(
                    f"assumed[{col}] holds {d.value} but the column is {getattr(self, col)}"
                )
        if self.kind == "capex" and self.depreciation is None:
            self.depreciation = "straight_line"
        return self

    # -- reads

    @property
    def provenance(self) -> Literal["stated", "assumed"]:
        return "assumed" if self.assumed else "stated"

    @property
    def source(self) -> Literal["typed", "linked", "share_of"]:
        return self.quantity_source.split(":", 1)[0]  # type: ignore[return-value]

    def cell(self, column: Literal["price", "quantity"], year: int) -> Cell:
        """`year`'s value of `column`. A grid cell is that year's value in the line's
        `money_year` money and the row's escalation does not apply to it (GK B 2026-11-10);
        any other year reads the column, escalated."""
        grid = self.price_by_year if column == "price" else self.quantity_by_year
        if year in grid:
            return Cell(grid[year], self.money_year, False)
        return Cell(getattr(self, column), self.money_year, True)


class BusinessCaseInputs(BaseModel):
    """The business case's inputs (spec §4 case-level fields). Saved partial: a refusal is
    reported by `refusals()` and blocks the run, not the save."""

    model_config = _FORBID

    preset: str | None = Field(
        default=None, pattern=PRESET_PATTERN
    )  # preset_id@version
    # Days of revenue outstanding (cash bridge only, never EBITDA: GK B BC3). None = not stated.
    receivable_days: Finite | None = Field(default=None, ge=0.0, le=365.0)
    lines: list[LineItem] = Field(default_factory=list)

    def refusals(
        self, *, horizon: tuple[int, int], base_year: int
    ) -> list[BusinessCaseRefused]:
        """Every refusal (G9), deterministic: line order, then the column order of
        `_line_refusals`, "not established" last on each line (GK B P4). `horizon` is the
        analysis's first and last year (a line's default span); `base_year` bounds money
        years."""
        excluded = {
            c for ln in self.lines if ln.preset_rule for c in ln.preset_rule.excludes
        }
        energy_in_price = any(
            ln.preset_rule and ln.preset_rule.price_includes_energy for ln in self.lines
        )
        capex = {ln.category for ln in self.lines if ln.kind == "capex"}
        out: list[BusinessCaseRefused] = []
        seen: set[str] = set()
        for i, ln in enumerate(self.lines):
            out += self._line_refusals(
                i, ln, seen, horizon, base_year, excluded, energy_in_price, capex
            )
            seen.add(ln.name)
        return out

    def require_runnable(self, *, horizon: tuple[int, int], base_year: int) -> None:
        """Refuse a case the engine cannot run, naming the line and column (G9, BC7)."""
        found = self.refusals(horizon=horizon, base_year=base_year)
        if found:
            raise found[0]

    def _line_refusals(
        self,
        i: int,
        ln: LineItem,
        seen: set[str],
        horizon: tuple[int, int],
        base_year: int,
        excluded: set[str],
        energy_in_price: bool,
        capex: set[str],
    ) -> list[BusinessCaseRefused]:
        at = f"lines[{i}]"
        out: list[BusinessCaseRefused] = []
        if ln.name in seen:
            out.append(NameDuplicate(f"{at}.name"))
        if ln.category not in CATEGORIES[ln.kind]:
            out.append(CategoryNotInKind(f"{at}.category"))
        if ln.kind == "capex" and ln.category in excluded:
            out.append(CapexNotOwned(f"{at}.category"))
        if energy_in_price and ln.kind == "revenue" and ln.category == "cost_recovery":
            out.append(PassThroughPriceIncludesEnergy(f"{at}.category"))
        share = ln.source == "share_of"
        if (ln.unit == "share") != share or (ln.unit == "lump" and ln.kind != "capex"):
            out.append(UnitNotAllowed(f"{at}.unit"))
        if share:
            out += self._share_refusals(i, ln)
        first, last = ln.start_year or horizon[0], ln.end_year or horizon[1]
        years = range(first, last + 1)
        if (
            ln.source != "linked"
            and ln.quantity is None
            and not _covers(ln.quantity_by_year, years)
        ):
            out.append(QuantityMissing(f"{at}.quantity"))
        if share and ln.quantity is not None and not 0.0 <= ln.quantity <= 1.0:
            out.append(ShareOutOfRange(f"{at}.quantity"))
        for y, v in sorted(ln.quantity_by_year.items()):
            if y not in years:
                out.append(YearOutOfRange(f"{at}.quantity_by_year[{y}]"))
            elif share and not 0.0 <= v <= 1.0:
                out.append(ShareOutOfRange(f"{at}.quantity_by_year[{y}]"))
        for y in sorted(ln.price_by_year):
            if y not in years:
                out.append(YearOutOfRange(f"{at}.price_by_year[{y}]"))
        if ln.money_year is not None and not (
            MONEY_YEAR_MIN <= ln.money_year <= base_year + MONEY_YEAR_AFTER_BASE
        ):
            out.append(YearOutOfRange(f"{at}.money_year"))
        if ln.escalation == "contract" and ln.indexation is None:
            out.append(IndexationMissing(f"{at}.indexation"))
        if ln.start_year is not None and not horizon[0] <= ln.start_year <= horizon[1]:
            out.append(OutsideHorizon(f"{at}.start_year"))
        if ln.end_year is not None and not horizon[0] <= ln.end_year <= horizon[1]:
            out.append(OutsideHorizon(f"{at}.end_year"))
        if last < first:
            out.append(EndBeforeStart(f"{at}.end_year"))
        if ln.kind == "capex" and ln.life_years is None:
            out.append(LifeMissing(f"{at}.life_years"))
        if ln.depreciation == "declining_balance" and ln.rate is None:
            out.append(RateMissing(f"{at}.rate"))
        if ln.preset_rule:
            for c in ln.preset_rule.requires:
                if c not in capex:
                    out.append(
                        _CAPEX_MISSING.get(c, CapexMissing)(
                            f"{at}.preset_rule.requires[{c}]"
                        )
                    )
        if not share and ln.price is None and not _covers(ln.price_by_year, years):
            cls = PriceNotEstablished if ln.kind == "revenue" else CostNotEstablished
            out.append(cls(f"{at}.price"))
        return out

    def _share_refusals(self, i: int, ln: LineItem) -> list[BusinessCaseRefused]:
        """G6: a share reads only non-share lines, one level; an empty base is refused."""
        at = f"lines[{i}].quantity_source"
        base_kind, _, base_cat = ln.quantity_source.split(":", 1)[1].partition("/")
        if base_cat and base_cat not in CATEGORIES[base_kind]:
            return [CategoryNotInKind(at)]
        base = [
            (j, b)
            for j, b in enumerate(self.lines)
            if b.kind == base_kind and (not base_cat or b.category == base_cat)
        ]
        if any(j == i or b.source == "share_of" for j, b in base):
            return [ShareCycle(at)]
        if not base:
            return [ShareBaseEmpty(at)]
        return []

    def with_cost_defaults(self, pack: _Pack, *, currency: str) -> BusinessCaseInputs:
        """A copy with each unset column a cost line lists in `default_keys` filled from
        `pack` and recorded in `assumed`. A stated value is never overwritten; a revenue line
        has no defaults. A money row in another currency is skipped, never converted. A price
        default carries its row's money year into the line (G-C1); a line whose stated
        money year differs is skipped, never converted (GK B Q4). A skipped price stays None
        and the run refuses it `cost_not_established` (GK B P1)."""
        lines = []
        for ln in self.lines:
            data = ln.model_dump()
            assumed = dict(ln.assumed)
            for col, key in ln.default_keys.items():
                if data[col] is not None:
                    continue
                row = _pack_row(pack, key)
                if row is None or row.value is None:
                    continue
                if row.currency is not None and row.currency != currency:
                    continue
                if col == "price" and row.money_year is not None:
                    if data["money_year"] is None:
                        data["money_year"] = row.money_year
                    elif data["money_year"] != row.money_year:
                        continue
                data[col] = row.value
                assumed[col] = CostDefault(
                    key=key,
                    value=row.value,
                    unit=row.unit,
                    source=row.source,
                    source_year=row.source_year,
                    currency=row.currency,
                    money_year=row.money_year,
                    pack=pack.stamp,
                )
            lines.append({**data, "assumed": assumed})
        # Re-validate: a pack value outside a column's range is refused, never stored.
        return type(self).model_validate({**self.model_dump(), "lines": lines})


def _covers(grid: dict[int, float], years: range) -> bool:
    """True when the per-year grid gives a value for every year of the line's span."""
    return len(years) > 0 and all(y in grid for y in years)


# ------------------------------------------------------------------ refusals


class BusinessCaseRefused(ValueError):
    """The business case cannot run; `field` names the line and column
    (`lines[<i>].<column>`) and `kind` the reason, as `business_case_refused:<kind>`."""

    kind = "refused"

    def __init__(self, field: str):
        self.field = field
        super().__init__(f"business_case_refused:{self.kind}: {field}")

    @property
    def code(self) -> str:
        return f"business_case_refused:{self.kind}"


class NameDuplicate(BusinessCaseRefused):
    kind = "name_duplicate"


class CategoryNotInKind(BusinessCaseRefused):
    kind = "category_not_in_kind"


class UnitNotAllowed(BusinessCaseRefused):
    kind = "unit_not_allowed"


class QuantityMissing(BusinessCaseRefused):
    kind = "quantity_missing"


class ShareOutOfRange(BusinessCaseRefused):
    kind = "share_out_of_range"


class ShareCycle(BusinessCaseRefused):
    kind = "share_cycle"


class ShareBaseEmpty(BusinessCaseRefused):
    kind = "share_base_empty"


class EndBeforeStart(BusinessCaseRefused):
    kind = "end_before_start"


class OutsideHorizon(BusinessCaseRefused):
    kind = "outside_horizon"


class YearOutOfRange(BusinessCaseRefused):
    kind = "year_out_of_range"


class IndexationMissing(BusinessCaseRefused):
    kind = "indexation_missing"


class LifeMissing(BusinessCaseRefused):
    kind = "life_missing"


class RateMissing(BusinessCaseRefused):
    kind = "rate_missing"


class PriceNotEstablished(BusinessCaseRefused):
    kind = "price_not_established"


class CostNotEstablished(BusinessCaseRefused):
    kind = "cost_not_established"


class CapexMissing(BusinessCaseRefused):
    kind = "capex_missing"


class ItCapexMissing(BusinessCaseRefused):
    kind = "it_capex_missing"


class FacilityCapexMissing(BusinessCaseRefused):
    kind = "facility_capex_missing"


class CapexNotOwned(BusinessCaseRefused):
    kind = "capex_not_owned"


class PassThroughPriceIncludesEnergy(BusinessCaseRefused):
    kind = "pass_through_price_includes_energy"


# The DC spec names these two; every other required category is `capex_missing` (GK B Q2).
_CAPEX_MISSING: dict[str, type[BusinessCaseRefused]] = {
    "it_hardware": ItCapexMissing,
    "facility": FacilityCapexMissing,
}

#: Every refusal kind this module raises (GK B P3: a test pins it to the subclasses).
REFUSAL_KINDS: tuple[str, ...] = (
    "name_duplicate",
    "category_not_in_kind",
    "unit_not_allowed",
    "quantity_missing",
    "share_out_of_range",
    "share_cycle",
    "share_base_empty",
    "end_before_start",
    "outside_horizon",
    "year_out_of_range",
    "indexation_missing",
    "life_missing",
    "rate_missing",
    "price_not_established",
    "cost_not_established",
    "capex_missing",
    "it_capex_missing",
    "facility_capex_missing",
    "capex_not_owned",
    "pass_through_price_includes_energy",
)

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
    """The pack's row `key`, or None in a pack version without it."""
    try:
        return pack.value(key)
    except LookupError:  # UnknownPackValue; a regional pack without the row
        return None
