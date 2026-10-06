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


@dataclass(frozen=True)
class AssetFinance:
    """An owner asset's capital side (plan C6). `overnight_cost` is the total
    installed cost before contingency (currency); None = not established."""

    name: str
    component: str
    overnight_cost: float | None
    lifetime_years: float | None = None
    carrier: str | None = None                  # incentive eligibility (WP4.4)


@dataclass(frozen=True)
class LpBasis:
    """What the dispatch LP's annuities used (plan C10): the config's discount
    rate, its inflation and whether `auto_discount_periods` was on, and each
    owner asset's own `discount_rate` (None = none set)."""

    discount_rate: float | None
    inflation_rate: float | None = None
    auto_discount_periods: bool = False
    asset_discount_rates: dict[str, float | None] = field(default_factory=dict)


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
