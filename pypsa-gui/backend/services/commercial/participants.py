"""
Participants and value flows (Edge Investment Case P3; spec §7).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.0 (this
part: the config) and WP3.1 (the ledger, added there).

`CommercialConfig.value_flows` is stored RAW; `parse_value_flows` validates it
into `ValueFlowConfig` lazily, so a stored value that later fails a rule is a
named error of the ledger, never a failed solve (plan C8). `value_flows_problems`
checks the parties against the contracts, the network and the group contract —
at the value-flows route only, never in a model validator (plan M6): a stale
party at ledger time is a flag, not an invalid config. Parties compare trimmed
and case-insensitive (`lp_bindings.same_party`).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from models.commercial import CommercialConfig, ValueFlowConfig
from services.commercial.lp_bindings import same_party

_COMPONENT_FRAMES = {"Generator": "generators", "StorageUnit": "storage_units",
                     "Store": "stores", "Link": "links", "Line": "lines",
                     "Transformer": "transformers"}


class ValueFlowsInvalid(ValueError):
    """A stored `value_flows` that does not validate into `ValueFlowConfig`."""

    code = "value_flows_invalid"


def parse_value_flows(raw) -> ValueFlowConfig | None:
    """The stored raw value as a `ValueFlowConfig` (None when unset); raises
    `ValueFlowsInvalid` naming the first error."""
    if raw is None:
        return None
    if isinstance(raw, ValueFlowConfig):
        return raw
    try:
        return ValueFlowConfig.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise ValueFlowsInvalid(f"value_flows is not valid ({where}: "
                                f"{first.get('msg', str(exc))})") from exc


def value_flows_digest(raw) -> str:
    """The `If-Match` token of a stored value: sha256 of its canonical JSON
    (the raw value, so an unparseable stored value still has one)."""
    blob = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _contract_parties(contract) -> list[tuple[str, str | None]]:
    """(role, party) of every party a contract names (P2 models)."""
    t = contract.type
    if t == "ppa":
        return [("seller", contract.seller), ("buyer", contract.buyer),
                ("sleeving_party", contract.sleeving_party)]
    if t == "cfd":
        return [("generator_owner", contract.generator_owner),
                ("counterparty", contract.counterparty)]
    if t == "dr":
        return [("counterparty", contract.counterparty)]
    if t == "lease":
        return [("lessor", contract.lessor), ("lessee", contract.lessee)]
    if t == "eaas":
        return [("provider", contract.provider), ("customer", contract.customer)]
    if t == "retail":
        return [("retailer", contract.retailer), ("customer", contract.customer)]
    return []


def _in(name: str, pool) -> bool:
    return any(same_party(name, p) for p in pool)


def value_flows_problems(vf: ValueFlowConfig, commercial: CommercialConfig, n) -> list[str]:
    """Every reason `vf` cannot be saved for `commercial` on network `n`, named
    (empty when valid). A contract party left `None` is P2's
    `party_not_established` at settlement, not a problem here."""
    problems: list[str] = []
    ids = [p.id for p in vf.participants]
    seen: list[str] = []
    for pid in ids:
        if _in(pid, seen):
            problems.append(f"participant ids must be unique (trimmed, case-insensitive): {pid!r}")
        seen.append(pid)
    for pid in ids:
        if _in(pid, vf.externals):
            problems.append(f"participant {pid!r} is also an external; a party is one or the other")
    if not _in(commercial.site_party, ids):
        problems.append(f"site_party {commercial.site_party!r} must be a participant "
                        "(templates keep its id and give it the template's role)")
    parties = ids + list(vf.externals)

    for c in commercial.contracts:
        for role, party in _contract_parties(c):
            if party is not None and not _in(party, parties):
                problems.append(f"contract {c.id!r} {role} {party!r} is neither a participant "
                                "nor an external")

    owned: list[tuple[str, str]] = []
    for own in vf.asset_owners:
        if not _in(own.owner, ids):
            where = "an external" if _in(own.owner, vf.externals) else "unknown"
            problems.append(f"asset {own.asset_id!r}: owner {own.owner!r} is {where}; an "
                            "owner must be a participant (an external owner's costs could "
                            "not reconcile)")
        frame = getattr(n, _COMPONENT_FRAMES[own.component])
        if own.asset_id not in frame.index:
            problems.append(f"asset {own.asset_id!r} is not a {own.component} of the network")
        key = (own.component, own.asset_id)
        if key in owned:
            problems.append(f"{own.component} {own.asset_id!r} is owned twice")
        owned.append(key)

    for rule in vf.tariff_payees:
        if not _in(rule.payee, parties):
            problems.append(f"tariff payee {rule.payee!r} is neither a participant nor an "
                            "external")

    grouped = commercial.group_contract is not None
    if vf.template == "energy_hub" and not grouped:
        problems.append("the energy_hub template needs a group contract")
    if vf.hub_members and not grouped:
        problems.append("hub_members need a group contract")
    if vf.allocation is not None and not grouped:
        problems.append("an allocation key needs a group contract")
    hub_parts = []
    for m in vf.hub_members:
        if m.link not in commercial.group_members:
            problems.append(f"hub member link {m.link!r} is not a group member")
        if not _in(m.participant, ids):
            problems.append(f"hub member {m.participant!r} is not a participant")
        hub_parts.append(m.participant)
    if vf.allocation is not None and vf.allocation.basis == "fixed_shares":
        keys = list((vf.allocation.shares or {}).keys())
        if sorted(k.strip().casefold() for k in keys) != \
                sorted(p.strip().casefold() for p in hub_parts):
            problems.append("fixed_shares keys must be exactly the hub members' participants "
                            f"(got {sorted(keys)}, hub members {sorted(hub_parts)})")
    return problems
