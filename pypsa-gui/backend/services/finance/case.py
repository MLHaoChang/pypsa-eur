"""
The finance engine's input contract: a plain `FinanceCase` (IC P4 plan C1).

The engine never reads a solved network. The results-layer adapter
(`services/results/finance_case.py`, WP4.6a) — or a parity test's SAM mapping
— builds a `FinanceCase` from the ledger, the seam and the stored
`FinanceInputs`, and hands plain numbers over. Everything here is frozen;
money is in the case currency, energy in MWh.

Operating-year template (plan C3): one year of operating cash in the
template's MONEY YEAR (`Template.money_year`, default the case's base year —
a multi-period network's templates are each in their own period year, as P2
indexes contract prices to it), each line signed from the owner's side (+ =
cash in). A line may state its own money year (`TemplateLine.money_year`: the
adapter puts every non-contract line in the base year, since P2 escalates only
contracts between periods — WP4.6a review B4). Escalation runs from the
line's money year, so no line is escalated twice (WP4.1 review #4). Several templates apply to a multi-period
network, each from its first operating year on; an asset absent from a later
template's `energy_mwh` does not generate in those years (retired or not built
in that period).
"""
from __future__ import annotations

import dataclasses
import math
import numbers
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from models.finance import FinanceInputs

# Escalation classes of a template line (plan C4): the six nominal classes, or
# the line's own contract indexation.
CONTRACT_CLASS = "contract"
# The source of the adapter's C5 first-order bill-effect pair (+S·g degrading
# with the asset, −S·g not): part of the avoided-bill VALUE, never a cost of
# the investment itself — the LCOE's asset costs skip it (WP4.6a review B1).
DEGRADATION_SOURCE = "degradation"
# The adapter's flag for an asset whose only cost is a `capital_cost`
# back-calculation (IC S0b plan S1): its capex is not established, and the
# engine gives this as the reason beside `overnight_cost_missing:<asset>`.
UPFRONT_FROM_CAPITAL_COST = "upfront_only_from_capital_cost"


class FinanceRefused(ValueError):
    """The case cannot be valued as stated. `code` is stable (plan C2, C3 …)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TemplateLine:
    """One operating-year cash line, in its template's money year, signed from
    the owner (+ = inflow). `amount` None = not established (plan C12).
    `indexation` is a contract's own rate as a FRACTION per year (the adapter
    converts P2's percent; P2 prices are already indexed to the money year, so
    the rate continues from there — plan C4)."""

    key: str
    stream: str
    amount: float | None
    esc_class: str                              # an escalation class or CONTRACT_CLASS
    indexation: float | None = None             # a contract's own rate per year (plan C4)
    tenor_years: int | None = None              # operating years from COD (plan C14)
    contract_id: str | None = None
    degrades_with: str | None = None            # the asset whose degradation scales it (plan C5)
    tariff_item: str | None = None
    counterparty: str = "external"
    source: str = "template"
    source_id: str | None = None
    period: str | None = None
    # Solve-for-PPA (plan C9): the contract price (currency/MWh, the line's
    # money year) the amount is linear in, and whether the contract changes the
    # dispatch (then a finance-only solve is refused).
    price: float | None = None
    changes_dispatch: bool = False
    # The line's own money year (None = its template's): P2 indexes contract
    # prices to the period year but not tariffs, connection fees, export
    # prices or costs, so a multi-period adapter states those in the base year
    # (WP4.6a review B4). Escalation runs from it.
    money_year: int | None = None


@dataclass(frozen=True)
class StorageYear:
    """A storage asset's throughput in one operating year of its template —
    the storage LCOS reads it (owner decision 6; IC U1 follow-up). Energy at
    the asset's bus (MWh, objective-weighted), before degradation; money in
    the line's money year (`money_year`, else its template's, else the base
    year), signed as a cost (> 0 = the site paid).

    * `charge_import_cost` — what the site actually paid for the charged
      energy it imported in the dispatch (the committed per-interval import
      price × the grid share of each interval's charge); escalates with
      `tariff`.
    * `charge_surplus_cost` — the charged energy that came from on-site
      surplus (PV): the export revenue the site forwent (0 with no export
      route); escalates with `export`.
    * `om_keys` — the template lines that are this asset's own O&M (fom, vom).

    None = not established (plan C12): the LCOS is then None with the reason.
    """

    discharge_mwh: float
    charge_mwh: float
    charge_import_cost: float | None
    charge_surplus_cost: float | None
    om_keys: tuple[str, ...] = ()
    money_year: int | None = None


@dataclass(frozen=True)
class Template:
    """The operating year from `first_year` on (calendar year)."""

    first_year: int
    lines: tuple[TemplateLine, ...]
    # Generation per asset in this operating year (MWh), before degradation:
    # PTC and LCOE read it.
    energy_mwh: dict[str, float] = field(default_factory=dict)
    # The money year of the lines (None = the case's base year). Keyword-only,
    # so a positional `energy_mwh` never binds here (WP4.1 review round 2 #2).
    money_year: int | None = field(default=None, kw_only=True)
    # The owner's storage assets' throughput and charging cost this year (the
    # LCOS; additive — empty for a case without storage).
    storage: dict[str, StorageYear] = field(default_factory=dict, kw_only=True)


@dataclass(frozen=True)
class AssetPart:
    """One investment part of an owner asset (IC S0b plan S2): what it cost
    (`overnight_cost` = the part's upfront per unit × the asset's capacity,
    currency, before contingency; None = not established), its lifetime
    (years; `math.inf` = never replaced, None = not stated) and its fixed O&M
    share (a fraction of `overnight_cost` a year; None = not stated)."""

    name: str
    overnight_cost: float | None
    lifetime_years: float | None
    fom_share: float | None = None

    def __post_init__(self):
        # A NaN lifetime is not stated (None: `part_lifetime_missing`), never
        # read as infinite (review r1 note 2).
        if self.lifetime_years is not None and math.isnan(self.lifetime_years):
            object.__setattr__(self, "lifetime_years", None)


@dataclass(frozen=True)
class AssetFinance:
    """An owner asset's capital side (plan C6). `overnight_cost` is the total
    installed cost before contingency (currency); None = not established.

    `parts` (IC S0b plan S2; additive, after `carrier` so positional
    construction keeps working): the asset's investment parts as the adapter
    read them through `asset_schema.access.upfront_parts`; empty for a hand
    case, which then has one effective part (`effective_parts`). With parts,
    `overnight_cost` is their sum (None exactly when some part's cost is None)
    and `lifetime_years` the asset's own `lifetime` column (the longest part
    for a composite) — refused (`ValueError`) otherwise, so capex has one
    source of truth (`scale_capex` moves them together)."""

    name: str
    component: str
    overnight_cost: float | None
    lifetime_years: float | None = None
    carrier: str | None = None                  # incentive eligibility (WP4.4)
    parts: tuple[AssetPart, ...] = ()

    def __post_init__(self):
        if not self.parts:
            return
        costs = [p.overnight_cost for p in self.parts]
        if any(c is None for c in costs) != (self.overnight_cost is None):
            raise ValueError(f"{self.name}: overnight_cost {self.overnight_cost!r} disagrees with "
                             f"its parts {costs} (None exactly when a part's cost is None)")
        if self.overnight_cost is not None and not math.isclose(
                sum(costs), self.overnight_cost, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"{self.name}: overnight_cost {self.overnight_cost!r} is not the sum "
                             f"of its parts {costs}")


def effective_parts(a: AssetFinance) -> tuple[AssetPart, ...]:
    """The parts every capex reader reads (IC S0b plan S2, review B4/B5): the
    asset's own, or — a hand case with none — one part `investment` of its
    `overnight_cost` over its `lifetime_years`."""
    if a.parts:
        return a.parts
    return (AssetPart("investment", a.overnight_cost, a.lifetime_years, None),)


def scale_capex(case: FinanceCase, f: float) -> FinanceCase:
    """The case with every purchase's cost × `f` (IC S0b plan S2): each
    asset's parts and `overnight_cost` together, and the amounts of the
    `fixed` `replacement_capex` entries — one CAPEX bound moves every purchase
    (a `part_lifetimes` replacement is its part's cost, so it moves with it).
    Not established costs stay None; the case is not mutated."""
    def x(v):
        return None if v is None else v * f

    assets = tuple(dataclasses.replace(
        a, overnight_cost=x(a.overnight_cost),
        parts=tuple(dataclasses.replace(p, overnight_cost=x(p.overnight_cost)) for p in a.parts))
        for a in case.assets)
    inputs = case.inputs.model_copy(update={"replacement_capex": [
        (year, asset, amount * f) for year, asset, amount in case.inputs.replacement_capex]})
    return dataclasses.replace(case, inputs=inputs, assets=assets)


@dataclass(frozen=True)
class LpBasis:
    """What the dispatch LP's annuities used (plan C10): the config's discount
    rate, its inflation and whether `auto_discount_periods` was on, and each
    owner asset's own `discount_rate` (None = none set)."""

    discount_rate: float | None
    inflation_rate: float | None = None
    auto_discount_periods: bool = False
    asset_discount_rates: dict[str, float | None] = field(default_factory=dict)


# Campus equipment an owner buys outside the network (IC G2 plan G-4): its kinds and
# the bases of its upfront cost (the defaults pack's campus rows, G1).
EXTRA_ASSET_KINDS = ("transformer", "cable", "capacitor_bank", "shunt_reactor", "statcom",
                     "switchgear")
EXTRA_ASSET_BASES = ("lump", "per_km", "per_bay")
_UPFRONT_PART_ATTRS = ("name", "upfront_per_unit", "lifetime", "fom_share",
                       "derived_from_capital_cost")
_UPFRONT_PART_KEYS = _UPFRONT_PART_ATTRS[:4]            # a mapping part: the flag is optional
_SOURCE_HASH_RE = re.compile(r"[0-9a-fA-F]{16}")


def _real(v) -> float | None:
    """A finite real number, or None (a bool is not a number here)."""
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


@dataclass(frozen=True)
class ExtraAssetPart:
    """One investment part of an `ExtraOwnerAsset`, finance-side (gate r1 note 2): the
    fields of `asset_schema.access.UpfrontPart`, which the finance package cannot
    import (it loads the solve stack). `ExtraOwnerAsset` normalises its parts to it, so
    the case hash reads these fields, never a repr."""

    name: str
    upfront_per_unit: float
    lifetime: float
    fom_share: float
    derived_from_capital_cost: bool = False


def _extra_part(p) -> ExtraAssetPart | None:
    """A part as passed (a dataclass with the `UpfrontPart` attributes, e.g. an
    `asset_schema.access.UpfrontPart`, or a mapping with the keys `name`,
    `upfront_per_unit`, `lifetime`, `fom_share` and an optional
    `derived_from_capital_cost` — the campus producer's shape) as an
    `ExtraAssetPart`; None for anything else."""
    if isinstance(p, Mapping):
        if not all(k in p for k in _UPFRONT_PART_KEYS):
            return None
        get = p.__getitem__
        derived = p.get("derived_from_capital_cost", False)
    elif dataclasses.is_dataclass(p) and not isinstance(p, type) and \
            all(hasattr(p, a) for a in _UPFRONT_PART_ATTRS):
        def get(a):
            return getattr(p, a)
        derived = p.derived_from_capital_cost
    else:
        return None
    return ExtraAssetPart(get("name"), get("upfront_per_unit"), get("lifetime"), get("fom_share"),
                          bool(derived))


@dataclass(frozen=True)
class ExtraOwnerAsset:
    """Equipment the owner buys that is not a network component (IC G2 plan G-4,
    U1 landing §8): a campus study's chosen transformer, cable, capacitor bank,
    shunt reactor, STATCOM or switchgear. `parts` (a list or a tuple) are
    `asset_schema.access.UpfrontPart`s (read by attribute; any dataclass with `name`,
    `upfront_per_unit`, `lifetime`, `fom_share`, `derived_from_capital_cost`) or
    mappings with those keys (the flag optional: the campus producer's
    `extra_owner_assets` shape), normalised to a tuple of `ExtraAssetPart`;
    `upfront_per_unit` per basis unit in the
    case's `currency` and `currency_year` (the caller converts); `quantity` is units
    (`lump`), km (`per_km`: units × length) or bays (`per_bay`); `build_year` the year
    it is built; `source_hash` sha256[:16] of the solved campus study that chose it.
    The name may contain ':' (§8's `campus:<library_id>#<k>`): reason codes that end in
    `:<name>` or `:<name>:<part>` are matched whole, never split on ':'.

    Refused at construction, `ValueError("extra_asset_invalid:<name>:<field>")`: an
    empty name, an unknown kind or basis, a quantity that is not finite and > 0 (or
    not whole for `lump` / `per_bay`), no part or a part that is neither such a dataclass
    nor such a mapping (`parts`), a build year outside 1900..2200, a `source_hash` that is not 16 hex
    characters, a part lifetime that is None, NaN, infinite or below a year
    (`lifetime`: every part needs a finite typed lifetime, C12), an upfront cost that
    is not finite or below 0 (`upfront_per_unit`), a `fom_share` outside [0, 1]
    (`fom_share`)."""

    name: str
    kind: str
    basis: str
    quantity: float
    parts: tuple[ExtraAssetPart, ...]
    build_year: int
    source: str
    source_hash: str

    def __post_init__(self):
        def bad(field_: str, detail: str):
            raise ValueError(f"extra_asset_invalid:{self.name}:{field_}: {detail}")

        if not isinstance(self.name, str) or not self.name.strip():
            bad("name", "an extra asset needs a name")
        if self.kind not in EXTRA_ASSET_KINDS:
            bad("kind", f"{self.kind!r} is not one of {list(EXTRA_ASSET_KINDS)}")
        if self.basis not in EXTRA_ASSET_BASES:
            bad("basis", f"{self.basis!r} is not one of {list(EXTRA_ASSET_BASES)}")
        q = _real(self.quantity)
        if q is None or q <= 0:
            bad("quantity", f"{self.quantity!r} is not a finite number > 0")
        if self.basis in ("lump", "per_bay") and not float(q).is_integer():
            bad("quantity", f"{self.quantity!r} {self.basis} is not a whole number of units")
        if not isinstance(self.parts, (tuple, list)) or not self.parts:
            bad("parts", "an extra asset needs at least one investment part")
        parts = []
        for p in self.parts:
            got = _extra_part(p)
            if got is None:
                bad("parts", f"{p!r} is neither a dataclass with {list(_UPFRONT_PART_ATTRS)} "
                             f"nor a mapping with {list(_UPFRONT_PART_KEYS)}")
            parts.append(got)
        object.__setattr__(self, "parts", tuple(parts))          # frozen: normalised once
        if isinstance(self.build_year, bool) or not isinstance(self.build_year, numbers.Integral) \
                or not 1900 <= self.build_year <= 2200:
            bad("build_year", f"{self.build_year!r} is not a year in 1900..2200")
        if not isinstance(self.source_hash, str) or not _SOURCE_HASH_RE.fullmatch(self.source_hash):
            bad("source_hash", f"{self.source_hash!r} is not 16 hex characters (sha256[:16])")
        for p in self.parts:
            life = _real(p.lifetime)
            if life is None or life < 1.0:
                bad("lifetime", f"part {p.name!r}: lifetime {p.lifetime!r} is not a finite "
                                "typed lifetime of a year or more")
            cost = _real(p.upfront_per_unit)
            if cost is None or cost < 0:
                bad("upfront_per_unit", f"part {p.name!r}: {p.upfront_per_unit!r} is not a "
                                        "finite cost >= 0")
            fom = _real(p.fom_share)
            if fom is None or not 0.0 <= fom <= 1.0:
                bad("fom_share", f"part {p.name!r}: {p.fom_share!r} is not a share in [0, 1]")

    def asset_finance(self) -> AssetFinance:
        """The one `AssetFinance` it becomes (G-5): `campus:<kind>`, no carrier, one
        `AssetPart` per part with overnight = upfront per unit × quantity, the longest
        part lifetime; the S0b parts check holds by construction."""
        parts = tuple(AssetPart(p.name, float(p.upfront_per_unit) * float(self.quantity),
                                float(p.lifetime), float(p.fom_share)) for p in self.parts)
        return AssetFinance(name=self.name, component=f"campus:{self.kind}",
                            overnight_cost=float(sum(p.overnight_cost for p in parts)),
                            lifetime_years=max(p.lifetime_years for p in parts), carrier=None,
                            parts=parts)


@dataclass(frozen=True)
class FinanceCase:
    inputs: FinanceInputs
    owner: str
    base_year: int
    cod: date
    templates: tuple[Template, ...]
    assets: tuple[AssetFinance, ...]
    flags: tuple[str, ...] = ()
    # The counterfactual supply cost (plan C13): the same site's bill,
    # commodity and connection lines WITHOUT the owner's investable assets, as
    # templates in the same shape (signed from the owner: costs < 0). Returns
    # are on the owner's cash minus these; the lifecycle NPV on the total.
    counterfactual: tuple[Template, ...] = ()
    lp_basis: LpBasis | None = None
    # sha256[:16] of what the counterfactual was built from — the tariff, the
    # served load, the connection and the commodity (plan C13; the report's
    # provenance). None when there is no counterfactual.
    counterfactual_hash: str | None = None
    # The P3 ledger's conservation check (True / False / None = not
    # established) for the report's `gates.conservation_ok`; None without a
    # ledger (a hand or SAM case) — P4 gate assessor condition 3c.
    conservation_ok: bool | None = None
    # Campus equipment bought as owner capex (IC G2 plan G-5): each one is also in
    # `assets` (after the network assets); this keeps the record (source, hash,
    # quantity) for the report and the case hash. Never in the counterfactual.
    extra_assets: tuple[ExtraOwnerAsset, ...] = ()
