"""
Placeholders in a shipped contract template (SCU06 S2a).

The common Library's contract templates (`services/library/common_items/contract/`) cannot
name the user's parties or assets, so each such field holds a marker: `MARK` followed by
what to fill, e.g. `fill-in:seller`. The marker starts with U+2060 WORD JOINER, a
format character (Unicode Cf) that `services.component_names` refuses in every new
component name, so a marker can never be the name of a real asset. The visible text after
it says what to fill. A value whose visible text starts with `fill-in:` counts as a
placeholder too, in case a client dropped the invisible character.

The server refuses a contract that still holds one where it is saved
(`binding.bind_commercial`, behind every route that stores a commercial config) and where
it is settled (`contracts.settle`, whose lines are what billing and finance read), so a
template never reaches a bill or a business case unfilled.
"""

from __future__ import annotations

MARK = "⁠fill-in:"
CODE = "contract_placeholder"
_VISIBLE = MARK[1:]


def _is_placeholder(value) -> bool:
    return isinstance(value, str) and value.replace(
        MARK[0], ""
    ).strip().casefold().startswith(_VISIBLE)


def unfilled(contract) -> list[str]:
    """The fields of `contract` (a contract model) that still hold a placeholder, in
    field order. A list field counts when any of its entries is one."""
    out = []
    for key, value in contract.model_dump().items():
        values = value if isinstance(value, list) else [value]
        if any(_is_placeholder(v) for v in values):
            out.append(key)
    return out


# The Contracts panel's own words for the fields a template leaves to fill in
# (frontend `contractModel.CONTRACT_FIELDS`), so the server's refusal and the
# panel's say the same thing, in the form's order (SCU06 S2b, UX 27 condition 4).
FIELD_WORDS = {
    "seller": "Seller",
    "buyer": "Buyer",
    "generator_owner": "Generator owner",
    "counterparty": "Counterparty",
    "asset_ids": "Assets (generators)",
}
_ORDER = list(FIELD_WORDS)


def problem(contract) -> str | None:
    """A plain refusal for a contract that is still a template, or None."""
    fields = unfilled(contract)
    if not fields:
        return None
    fields.sort(key=lambda f: _ORDER.index(f) if f in _ORDER else len(_ORDER))
    return f"Contract {contract.id}: {need_own_values(fields)}."


def need_own_values(fields: list[str]) -> str:
    """The words the Contracts panel uses too (frontend `contractModel.needOwnValues`):
    "Seller, Buyer and Assets (generators) need your own values"."""
    words = [FIELD_WORDS.get(f, f) for f in fields]
    if len(words) == 1:
        return f"{words[0]} needs your own value"
    return f"{', '.join(words[:-1])} and {words[-1]} need your own values"


def first_problem(contracts) -> str | None:
    """`problem` of the first contract in `contracts` that has one."""
    for contract in contracts:
        why = problem(contract)
        if why:
            return why
    return None
