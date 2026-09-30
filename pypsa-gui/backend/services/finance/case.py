"""
The finance engine's input contract: a plain `FinanceCase` (IC P4 plan C1).

The engine never reads a solved network. The results-layer adapter
(`services/results/finance_case.py`, WP4.6a) — or a parity test's SAM mapping
— builds a `FinanceCase` from the ledger, the seam and the stored
`FinanceInputs`, and hands plain numbers over. Everything here is frozen;
money is in the case currency, energy in MWh.

Operating-year template (plan C3): one year of operating cash in BASE-YEAR
money, each line signed from the owner's side (+ = cash in). Several templates
apply to a multi-period network, each from its first operating year on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from models.finance import FinanceInputs

# Escalation classes of a template line (plan C4): the six nominal classes, or
# the line's own contract indexation.
CONTRACT_CLASS = "contract"


class FinanceRefused(ValueError):
    """The case cannot be valued as stated. `code` is stable (plan C2, C3 …)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TemplateLine:
    """One operating-year cash line, in base-year money, signed from the owner
    (+ = inflow). `amount` None = not established (plan C12)."""

    key: str
    stream: str
    amount: float | None
    esc_class: str                              # an escalation class or CONTRACT_CLASS
    indexation: float | None = None             # a contract's own rate per year (plan C4)
    index_base_year: int | None = None          # the contract's indexation base
    tenor_years: int | None = None              # operating years from COD (plan C14)
    contract_id: str | None = None
    degrades_with: str | None = None            # the asset whose degradation scales it (plan C5)
    tariff_item: str | None = None
    counterparty: str = "external"
    source: str = "template"
    source_id: str | None = None
    period: str | None = None


@dataclass(frozen=True)
class Template:
    """The operating year from `first_year` on (calendar year)."""

    first_year: int
    lines: tuple[TemplateLine, ...]
    # Generation per asset in this operating year (MWh), before degradation:
    # PTC and LCOE read it.
    energy_mwh: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetFinance:
    """An owner asset's capital side (plan C6). `overnight_cost` is the total
    installed cost before contingency (currency); None = not established."""

    name: str
    component: str
    overnight_cost: float | None
    lifetime_years: float | None = None


@dataclass(frozen=True)
class FinanceCase:
    inputs: FinanceInputs
    owner: str
    base_year: int
    cod: date
    templates: tuple[Template, ...]
    assets: tuple[AssetFinance, ...]
    flags: tuple[str, ...] = ()
