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
from dataclasses import dataclass, field

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
        raise ValueFlowsInvalid(f"value_flows is not valid ({where or 'value_flows'}: "
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
        if not pid.strip():
            problems.append(f"a participant id is empty after trimming: {pid!r}")
        elif _in(pid, seen):
            problems.append(f"participant ids must be unique (trimmed, case-insensitive): {pid!r}")
        seen.append(pid)
    ext_seen: list[str] = []
    for ext in vf.externals:
        if not ext.strip():
            problems.append(f"an external is empty after trimming: {ext!r}")
        elif _in(ext, ext_seen):
            problems.append(f"externals must be unique (trimmed, case-insensitive): {ext!r}")
        ext_seen.append(ext)
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

    item_ids = ([i.id for i in commercial.import_tariff.items]
                if commercial.import_tariff is not None else None)
    if vf.connection_fee_payee is not None and not _in(vf.connection_fee_payee, parties):
        problems.append(f"connection_fee_payee {vf.connection_fee_payee!r} is neither a "
                        "participant nor an external")
    for rule in vf.tariff_payees:
        if not _in(rule.payee, parties):
            problems.append(f"tariff payee {rule.payee!r} is neither a participant nor an "
                            "external")
        if rule.item_id is not None and item_ids is not None and rule.item_id not in item_ids:
            # A typo would silently fall back to the default payee.
            problems.append(f"tariff payee rule names item {rule.item_id!r}, which the import "
                            f"tariff does not have (items: {item_ids})")

    grouped = commercial.group_contract is not None
    if vf.template == "energy_hub" and not grouped:
        problems.append("the energy_hub template needs a group contract")
    if vf.hub_members and not grouped:
        problems.append("hub_members need a group contract")
    if vf.allocation is not None and not grouped:
        problems.append("an allocation key needs a group contract")
    hub_parts: list[str] = []
    hub_links: list[str] = []
    for m in vf.hub_members:
        if m.link not in commercial.group_members:
            problems.append(f"hub member link {m.link!r} is not a group member")
        if m.link in hub_links:
            problems.append(f"hub member link {m.link!r} is listed twice")
        if not _in(m.participant, ids):
            problems.append(f"hub member {m.participant!r} is not a participant")
        elif _in(m.participant, hub_parts):
            problems.append(f"hub member participant {m.participant!r} is listed twice")
        hub_parts.append(m.participant)
        hub_links.append(m.link)
    if vf.allocation is not None and grouped:
        # The allocation splits the WHOLE group bill: a group member that is no
        # hub member would leave its share with nobody (WP3.3a).
        unlisted = [link for link in commercial.group_members if link not in hub_links]
        if unlisted:
            problems.append("an allocation key needs every group member as a hub member "
                            f"(missing: {unlisted})")
    if vf.allocation is not None and vf.allocation.basis == "contracted_capacity":
        missing = [m.link for m in vf.hub_members if m.contracted_mw is None]
        if missing or not vf.hub_members:
            problems.append("the contracted_capacity key needs contracted_mw on every hub "
                            f"member (missing: {missing or 'no hub members'})")
    if vf.allocation is not None and vf.allocation.basis == "fixed_shares":
        keys = list((vf.allocation.shares or {}).keys())
        if sorted(k.strip().casefold() for k in keys) != \
                sorted(p.strip().casefold() for p in hub_parts):
            problems.append("fixed_shares keys must be exactly the hub members' participants "
                            f"(got {sorted(keys)}, hub members {sorted(hub_parts)})")
    return problems


# ── the ledger (WP3.1) ─────────────────────────────────────────────────────
#
# One `ValueFlowLine` per money flow, payer → payee, amount ≥ 0 or None, per
# modelled period and UNWEIGHTED (the `cost_rows` basis; P4 applies years).
# `LedgerInputs` is plain data built by the results layer
# (`services.results.value_flows`), so this package never reads the physical
# seam (it imports `solver_service`).
#
# Every line comes from a SOURCE (`_sources`): one per bill item, connection
# fee, export-price revenue, contract line, unsettled contract and asset cost,
# each with its expected LEGS (debtor, creditor, signed value). The builder
# emits the legs as lines; the coverage check compares the lines with the legs
# — payer AND payee, signed, and the stream — so a swapped direction, a moved
# payer, a dropped or duplicated line or a relabelled stream fails.
#
# What check 4 (reconciliation) can and cannot see: the bill, fee, settlement
# and export terms enter both sides from the same inputs, so they cancel by
# construction; what remains is that the asset lines add up to
# `cost_breakdown` minus the LP commercial rows. It catches a corrupted ledger
# and an external payee sent to the wrong party, not a wrong bill, a wrong
# export revenue or a wrong payee resolution (those are the P2 gate's and the
# resolution tests' job). Check 2 is a bookkeeping identity (kept as the
# spec's literal statement); checks 3 and 4 carry the weight.
#
# Inputs that make money unknown — a config edited since the solve, a partial
# tariff import, an unsettled contract, a commercial term not established —
# make the period's result None (`inputs_established`), never True (ADR-0001).
#
# P4 mapping (plan WP3.1, pinned): a line becomes one `CashflowLine` per
# operating year for each INTERNAL side (participant = that side, counterparty
# = the other, amount signed from the participant; `provenance.source` carries
# `source` / `source_id` / `contract_id`). `basis="annuity"` and
# `basis="model_only"` lines are not converted (P4 assigns overnight capex to
# the owner from `asset_owners`); a None line makes that participant's returns
# `not_established`.

CENT = 0.005

# P2 settlement `value_stream` → `ValueStreamKind`. `ppa_excess_mwh` is a volume
# with no money (disclosed in notes, no line).
_SETTLEMENT_STREAMS = {
    "ppa_energy": "ppa_settlement", "ppa_sleeving_fee": "ppa_settlement",
    "cfd_difference": "cfd_settlement", "dr_availability": "dr_availability",
    "dr_activation": "dr_activation", "lease_payment": "lease", "eaas_fee": "eaas_fee",
}
_VOLUME_ONLY = {"ppa_excess_mwh"}
_KIND_STREAMS = {"demand": "demand_charge", "capacity": "network_capacity",
                 "fixed": "retail_fixed", "certificate": "certificates", "tax_levy": "tax"}
_DEFAULT_PAYEE = {"energy": "retailer", "fixed": "retailer", "certificate": "retailer",
                  "demand": "dso", "capacity": "dso", "tax_levy": "tax_authority"}
# Input flags that make the money of a period unknown: exact names and prefixes
# (WP3.1 review round 2 #2). Everything else is a disclosure — e.g.
# `demand_months_not_established` (months absent from a representative-week
# dispatch: `per_item_sampled` leaves them out on both sides),
# `group_energy_share_not_established` (a report field), `*_recipe_changed`
# (the dispatch and the bill are still known).
_BLOCKING_EXACT = frozenset({
    "config_changed_since_solve", "config_cleared_since_solve", "not_solved",
    "demand_charge_not_established", "energy_tiers_not_established",
    "network_capacity_not_established", "ppa_settlement_not_established",
    "tariff_capacity_not_established"})
_BLOCKING_PREFIXES = ("tariff_incomplete", "period_not_billed:", "contract_not_settled:",
                      "export_split_not_established:")


@dataclass
class BillItem:
    item_id: str
    kind: str
    measured_on: str
    direction: str


@dataclass
class AssetCost:
    """One asset's unweighted per-period cost: `capex` (annuitised investment),
    `fom`, `opex` (operational expenditure), keyed like the ledger periods.
    `flags` carries the side's flags (`meter_bypass`) and
    `fuel_supply_generator` (its opex is a fuel purchase)."""

    component: str
    name: str
    side: str                       # "site" | "grid" | "unclassified"
    flags: list[str]
    capex: dict[str, float]
    fom: dict[str, float]
    opex: dict[str, float]


@dataclass
class HubPeriod:
    """One period's allocation inputs of an energy hub (WP3.3a; built by
    `hub_allocation.period_hub`): per participant, the import energy (MWh),
    the metered linear import items and the peak-contribution split of the
    demand items — site-view amounts; None = not established."""

    energy_mwh: dict[str, float | None]
    metered: dict[str, dict[str, float | None] | None]
    peak: dict[str, dict[str, float] | None]
    flags: list[str] = field(default_factory=list)


@dataclass
class HubInputs:
    members: list[tuple[str, str]]                    # (group member Link, participant)
    periods: dict[str, HubPeriod]


@dataclass
class LedgerInputs:
    periods: list[str]
    site_party: str
    bill_items: dict[str, BillItem]
    bill: dict[str, dict[str, float | None]]          # site view: + the site pays
    bill_flags: dict[str, list[str]]                  # per period, "<item>:<flag>"
    retailer: str | None                              # the RetailContract's, if any
    settlement: list[dict]                            # P2 lines, period keyed "_"/"<year>"
    connection_fee: dict[str, float | None]
    connection_fixed_fee: dict[str, float | None]
    curtailment_compensation: bool
    export_revenue: dict[str, float | None]           # + participants receive
    # period → source ("export_price" or an export item id) → asset key
    # ((component, name), or None for the share no site generator produced) →
    # amount (export price: + received; bill item: site view).
    export_split: dict[str, dict[str, dict]] | None
    assets: list[AssetCost]
    cost_breakdown_total: dict[str, float | None]     # unweighted per period
    lp_commercial: dict[str, float | None]            # Σ commercial_cost_terms items
    disclosures: dict[str, dict[str, float | None]]
    # Site-level flags of the bill, the cost terms and the settlement; the
    # blocking ones make every period None.
    input_flags: list[str] = field(default_factory=list)
    # (contract id, reason) of contracts that did not settle: a None line each.
    unsettled_contracts: list[tuple[str, str]] = field(default_factory=list)
    # `cost_breakdown["curtailment_cost"]` (years-weighted, horizon): a penalty
    # term outside every total, disclosed like DSR and VoLL.
    curtailment_penalty: float | None = None
    # Contracts whose seller is external but whose assets are modelled here.
    external_ppa_assets: list[str] = field(default_factory=list)
    # An energy hub's allocation inputs (WP3.3a); None without an allocation key.
    hub: HubInputs | None = None


@dataclass
class ValueFlowLine:
    period: str
    payer: str | None
    payee: str | None
    value_stream: str
    source: str
    source_id: str
    amount: float | None
    basis: str = "cash"
    tariff_item: str | None = None
    tariff_item_kind: str | None = None
    contract_id: str | None = None
    asset: str | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class Ledger:
    periods: dict[str, list[ValueFlowLine]]
    tariff_payees: dict[str, str]
    flags: list[str]
    notes: list[str]
    disclosures: dict[str, dict[str, float | None]]


@dataclass
class PeriodCheck:
    ok: bool | None
    checks: list[dict]


@dataclass
class ConservationResult:
    ok: bool | None
    periods: dict[str, PeriodCheck]
    flags: list[str]


@dataclass
class _Source:
    source: str
    source_id: str
    stream: str
    legs: list[tuple[str | None, str | None, float | None]]
    basis: str = "cash"
    meta: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def _finite(v) -> bool:
    return v is not None and v == v and abs(v) != float("inf")


def resolve_tariff_payees(items: dict[str, BillItem], vf: ValueFlowConfig,
                          retailer: str | None) -> dict[str, str]:
    """Each item's payee: an item-id rule, then a kind rule, then the
    RetailContract's retailer, then the default by kind (plan WP3.0)."""
    out = {}
    for item_id, it in items.items():
        by_id = next((r.payee for r in vf.tariff_payees if r.item_id == item_id), None)
        by_kind = next((r.payee for r in vf.tariff_payees
                        if r.item_id is None and r.kind == it.kind), None)
        out[item_id] = by_id or by_kind or retailer or _DEFAULT_PAYEE.get(it.kind, "retailer")
    return out


def _conn_payee(vf: ValueFlowConfig) -> str:
    return vf.connection_fee_payee or "dso"


def _item_stream(it: BillItem, payee: str) -> str:
    if it.kind == "energy":
        if it.direction == "revenue" or it.measured_on == "export":
            return "energy_export"
        return "network_energy" if same_party(payee, "dso") else "energy_import"
    if it.kind == "demand" or (it.measured_on == "peak_import" and it.kind != "capacity"):
        return "demand_charge"
    return _KIND_STREAMS.get(it.kind, "other")


def _blocking(flags) -> list[str]:
    return sorted({f for f in flags
                   if f in _BLOCKING_EXACT or f.startswith(_BLOCKING_PREFIXES)})


def _merge_legs(legs):
    """Legs with the same (debtor, creditor) — same_party — summed into one:
    two split shares resolving to the same owner must not collide in coverage
    (WP3.1 review round 2 #1). A None in a group makes the group None."""
    out: list[list] = []
    for d, c, v in legs:
        for row in out:
            if same_party(row[0], d) and same_party(row[1], c):
                row[2] = None if (row[2] is None or v is None) else row[2] + v
                break
        else:
            out.append([d, c, v])
    return [tuple(r) for r in out]


def _sources(inputs: LedgerInputs, vf: ValueFlowConfig, p: str) -> list[_Source]:
    """Every source of period `p` with its expected legs — the one definition
    the builder and the coverage check share."""
    site = inputs.site_party
    payees = resolve_tariff_payees(inputs.bill_items, vf, inputs.retailer)
    conn = _conn_payee(vf)
    owners = {(o.component, o.asset_id): o.owner for o in vf.asset_owners}
    split = (inputs.export_split or {}).get(p, {}) \
        if vf.export_revenue_to == "asset_owner" else {}

    def owner_of(key) -> str:
        return site if key is None else owners.get(tuple(key), site)

    out: list[_Source] = []
    for item_id, it in inputs.bill_items.items():
        v = (inputs.bill.get(p) or {}).get(item_id)
        payee = payees[item_id]
        src = _Source("bill", item_id, _item_stream(it, payee), legs=[],
                      meta={"tariff_item": item_id, "tariff_item_kind": it.kind})
        parts = split.get(item_id) if _finite(v) else None
        if parts:
            src.legs = _merge_legs([(owner_of(k), payee, a) for k, a in
                                    sorted(parts.items(), key=lambda kv: str(kv[0]))])
        else:
            src.legs = [(site, payee, v if _finite(v) else None)]
            if not _finite(v):
                src.flags.append("bill_item_not_established")
        out.append(src)
    for sid, table in (("fee", inputs.connection_fee), ("fixed_fee",
                                                        inputs.connection_fixed_fee)):
        if p in table:
            v = table[p]
            src = _Source("connection", sid, "network_capacity",
                          legs=[(site, conn, v if _finite(v) else None)])
            if not _finite(v):
                src.flags.append("connection_fee_not_established")
            out.append(src)
    if inputs.curtailment_compensation:
        out.append(_Source("curtailment_compensation", "curtailment_compensation",
                           "network_capacity", legs=[(conn, site, None)],
                           flags=["curtailment_compensation_not_computed"]))
    if p in inputs.export_revenue:
        v = inputs.export_revenue[p]
        src = _Source("export_price", "export_price", "energy_export", legs=[])
        parts = split.get("export_price") if _finite(v) else None
        if parts:
            src.legs = _merge_legs([("market", owner_of(k), a) for k, a in
                                    sorted(parts.items(), key=lambda kv: str(kv[0]))])
        else:
            src.legs = [("market", site, v if _finite(v) else None)]
            if not _finite(v):
                src.flags.append("export_revenue_not_established")
        out.append(src)
    for i, s in enumerate(inputs.settlement):
        if s.get("period") != p or s.get("value_stream") in _VOLUME_ONLY:
            continue
        kind = _SETTLEMENT_STREAMS.get(s.get("value_stream"), "other")
        v = s.get("amount")
        src = _Source("contract", f"{s.get('contract_id')}:{i}", kind,
                      legs=[(s.get("payer"), s.get("payee"), v if _finite(v) else None)],
                      meta={"contract_id": s.get("contract_id")},
                      flags=list(s.get("flags") or []))
        if kind == "other":
            src.flags.append(f"unmapped_stream:{s.get('value_stream')}")
        out.append(src)
    for cid, reason in inputs.unsettled_contracts:
        out.append(_Source("contract", f"{cid}:unsettled", "other", legs=[(None, None, None)],
                           meta={"contract_id": cid},
                           flags=[f"contract_not_settled:{reason}"[:200]]))
    for a in inputs.assets:
        capex, fom, opex = (a.capex.get(p, 0.0), a.fom.get(p, 0.0), a.opex.get(p, 0.0))
        meta = {"asset": a.name}
        tag = f"{a.component}:{a.name}"
        if a.side == "grid":
            if capex + fom:
                out.append(_Source("asset", f"fixed:{tag}", "other",
                                   legs=[(site, "market", capex + fom)], basis="model_only",
                                   meta=meta, flags=[*a.flags, "grid_side_asset_cost"]))
            if opex:
                gen = a.component == "Generator"
                out.append(_Source(
                    "asset", f"opex:{tag}", "energy_import" if gen else "other",
                    legs=[(site, "market", opex)], basis="cash" if gen else "model_only",
                    meta=meta, flags=[*a.flags, "commodity_from_grid_side_generator" if gen
                                      else "grid_side_asset_cost"]))
            continue
        owner = owners.get((a.component, a.name), site)
        extra = ["asset_side_unclassified"] if a.side == "unclassified" else []
        fuel = "fuel_supply_generator" in a.flags
        for kind, value, payee, stream, basis in (
                ("capex", capex, "capex_supplier", "capex", "annuity"),
                ("fom", fom, "om_contractor", "fom", "cash"),
                ("opex", opex, "market" if fuel else "om_contractor",
                 "fuel" if fuel else "vom", "cash")):
            if value:
                out.append(_Source("asset", f"{kind}:{tag}", stream,
                                   legs=[(owner, payee, value)], basis=basis, meta=meta,
                                   flags=[*a.flags, *extra]))
    return out + _allocation_sources(out, inputs, vf, p)


# ── energy-hub allocation (WP3.3a) ─────────────────────────────────────────
#
# The hub (= site_party, the group-contract holder) pays the group's bill, fees
# and receives its export revenue; each shared source's HUB amount (site view:
# + the hub pays) is split into internal lines member → hub (a revenue share,
# negative, runs hub → member). Linear import items are metered per member;
# the rest is keyed (`vf.allocation.basis`). Shares are exact floats; the last
# member in sorted-id order takes `amount − Σ others`, after the residual of
# the unrounded split is checked (≤ 1e-9 relative). Contract and asset lines
# name their parties already and are never allocated.

_SHARED = ("bill", "connection", "export_price")
_RESIDUAL = 1e-9


def _hub_amount(src: _Source, site: str) -> tuple[bool, float | None]:
    """(touches the hub, its site-view amount of `src`)."""
    total, touched = 0.0, False
    for d, c, v in src.legs:
        sign = 1.0 if same_party(d, site) else (-1.0 if same_party(c, site) else 0.0)
        if not sign:
            continue
        touched = True
        if v is None or total is None:
            total = None
        else:
            total += sign * v
    return touched, total


def _key_weights(vf: ValueFlowConfig, hp: HubPeriod | None,
                 members: list[str]) -> tuple[dict[str, float] | None, str]:
    basis = vf.allocation.basis
    if basis == "contracted_capacity":
        by = {m.participant.strip().casefold(): m.contracted_mw for m in vf.hub_members}
        w = {p: by.get(p.strip().casefold()) for p in members}
    elif basis == "fixed_shares":
        by = {k.strip().casefold(): v for k, v in (vf.allocation.shares or {}).items()}
        w = {p: by.get(p.strip().casefold()) for p in members}
    else:                                   # energy, and peak_contribution's fallback
        w = {p: (hp.energy_mwh.get(p) if hp is not None else None) for p in members}
        basis = "energy"
    if any(v is None or not _finite(v) or v < 0 for v in w.values()) or sum(w.values()) <= 0:
        return None, basis
    return w, basis


def _allocation_sources(shared: list[_Source], inputs: LedgerInputs, vf: ValueFlowConfig,
                        p: str) -> list[_Source]:
    hub = inputs.hub
    if hub is None or vf.allocation is None or not vf.hub_members:
        return []
    site = inputs.site_party
    hp = hub.periods.get(p)
    members = sorted((m.participant for m in vf.hub_members),
                     key=lambda x: x.strip().casefold())
    out: list[_Source] = []
    for src in shared:
        if src.source not in _SHARED:
            continue
        touched, amount = _hub_amount(src, site)
        if not touched or amount == 0:
            continue
        item = src.source_id if src.source == "bill" else None
        raw: dict[str, float] | None = None
        method, reason = vf.allocation.basis, None
        if item is not None and hp is not None and item in hp.metered:
            method = "metered"
            got = hp.metered[item]
            if got is None or any(v is None or not _finite(v)
                                  for v in (got.get(m) for m in members)):
                reason = "member_rating_unknown"
            else:
                raw = {m: got[m] for m in members}
        elif item is not None and method == "peak_contribution" and hp is not None \
                and item in hp.peak:
            got = hp.peak[item]
            if got is None:
                reason = "peak_split_unknown"
            else:
                raw = {m: got.get(m, 0.0) for m in members}
        if raw is None and reason is None:
            weights, method = _key_weights(vf, hp, members)
            if weights is None:
                reason = f"{method}_key_not_established"
            elif amount is not None:
                tot = sum(weights.values())
                raw = {m: amount * weights[m] / tot for m in members}
        flags = [f"allocation:{method}"]
        if vf.allocation.basis == "peak_contribution" and method == "energy":
            flags.append("allocation_fallback_energy")
        if raw is not None and amount is not None and \
                abs(sum(raw.values()) - amount) > _RESIDUAL * max(1.0, abs(amount)):
            raw, reason = None, "residual"
        if amount is None:
            reason = reason or "shared_amount_unknown"
        if raw is None or amount is None:
            legs = [(m, site, None) for m in members if not same_party(m, site)]
            flags.append(f"allocation_not_established:{src.source_id}:{reason}")
        else:
            shares = [raw[m] for m in members[:-1]]
            shares.append(amount - sum(shares))            # the last member: the remainder
            legs = [(m, site, v) for m, v in zip(members, shares) if not same_party(m, site)]
        if legs:
            out.append(_Source("allocation", f"{src.source}:{src.source_id}", src.stream,
                               legs=legs, meta=dict(src.meta), flags=flags))
    return out


def _signed_line(period, debtor, creditor, value, **kw) -> ValueFlowLine:
    """A line for a SIGNED value (+ debtor pays creditor, − the reverse)."""
    if value is None:
        return ValueFlowLine(period=period, payer=debtor, payee=creditor, amount=None, **kw)
    if value < 0:
        return ValueFlowLine(period=period, payer=creditor, payee=debtor, amount=-value, **kw)
    return ValueFlowLine(period=period, payer=debtor, payee=creditor, amount=value, **kw)


def build_ledger(inputs: LedgerInputs, vf: ValueFlowConfig) -> Ledger:
    """The ledger of `inputs` under `vf`: every source's legs as lines (zero
    legs dropped, None kept), each line flagged `party_not_established:<p>`
    when a party is neither a participant nor an external; notes for volume
    lines, defaulted owners and lines between two externals; the input,
    bill and disclosure flags. (The plan's `site_party` argument is
    `inputs.site_party`.)"""
    ids = [x.id for x in vf.participants]
    flags: set[str] = set(inputs.input_flags)
    notes: list[str] = []
    periods: dict[str, list[ValueFlowLine]] = {}
    for p in inputs.periods:
        lines: list[ValueFlowLine] = []
        for src in _sources(inputs, vf, p):
            for debtor, creditor, value in src.legs:
                if value is not None and value == 0 and len(src.legs) > 1:
                    continue
                lines.append(_signed_line(p, debtor, creditor, value,
                                          value_stream=src.stream, source=src.source,
                                          source_id=src.source_id, basis=src.basis,
                                          flags=list(src.flags), **src.meta))
        for s in inputs.settlement:
            if s.get("period") == p and s.get("value_stream") in _VOLUME_ONLY:
                notes.append(f"{s['value_stream']}:{s.get('contract_id')}:{p}:"
                             f"{s.get('quantity_mwh')}")
        for ln in lines:
            for party in (ln.payer, ln.payee):
                if party is None:
                    if "party_not_established" not in ln.flags:
                        ln.flags.append("party_not_established")
                elif not _in(party, ids) and not _in(party, vf.externals):
                    tag = f"party_not_established:{party}"
                    if tag not in ln.flags:
                        ln.flags.append(tag)
            if ln.payer is not None and ln.payee is not None and \
                    _in(ln.payer, vf.externals) and _in(ln.payee, vf.externals):
                notes.append(f"between_externals:{ln.source}:{ln.source_id}:{p}")
        for f in (inputs.bill_flags.get(p) or []):
            flags.add(f"bill:{p}:{f}")
        periods[p] = lines
    owned = {(o.component, o.asset_id) for o in vf.asset_owners}
    defaulted = sum(1 for a in inputs.assets if a.side != "grid"
                    and (a.component, a.name) not in owned
                    and any(a.capex.values()) | any(a.fom.values()) | any(a.opex.values()))
    if defaulted:
        notes.append(f"asset_owner_defaulted:{defaulted}")
    sides = {(a.component, a.name): a.side for a in inputs.assets}
    for key in owned:
        if sides.get(key) == "grid":
            flags.add(f"grid_side_asset_not_ownable:{key[1]}")
    for cid in inputs.external_ppa_assets:
        notes.append(f"asset_under_external_ppa:{cid}")
    disclosures = {p: dict(inputs.disclosures.get(p) or {}) for p in inputs.periods}
    for d in disclosures.values():
        for key, name in (("dsr_slack", "dsr_slack"), ("voll", "voll")):
            if key not in d:
                continue
            if d[key] is None:
                flags.add(f"{name}_not_established")
            elif abs(d[key]) >= CENT:            # LP noise is not a disclosure
                flags.add(f"{name}_not_a_cash_flow")
    if inputs.curtailment_penalty:
        flags.add("curtailment_penalty_not_a_cash_flow")
    return Ledger(periods=periods,
                  tariff_payees=resolve_tariff_payees(inputs.bill_items, vf, inputs.retailer),
                  flags=sorted(flags), notes=notes, disclosures=disclosures)


def _side(party: str | None, vf: ValueFlowConfig) -> str | None:
    if party is None:
        return None
    if _in(party, [x.id for x in vf.participants]):
        return "internal"
    if _in(party, vf.externals):
        return "external"
    return None


def _coverage(lines: list[ValueFlowLine], sources: list[_Source]) -> list[str]:
    """Lines against the sources' legs: each line must match a leg of its
    source by (payer, payee) in either direction (same_party), the legs' signed
    sums must equal their values, None legs need None lines, the stream must be
    the source's."""
    problems: list[str] = []
    by_key: dict[tuple[str, str], list[ValueFlowLine]] = {}
    for ln in lines:
        by_key.setdefault((ln.source, ln.source_id), []).append(ln)
    expected = {(s.source, s.source_id): s for s in sources}
    for key in sorted(set(by_key) - set(expected), key=str):
        problems.append(f"{key[0]}:{key[1]}: a line with no source")
    for key, src in expected.items():
        have = by_key.get(key, [])
        label = f"{key[0]}:{key[1]}"
        if any(ln.value_stream != src.stream for ln in have):
            problems.append(f"{label}: stream is not {src.stream}")
        sums = [0.0] * len(src.legs)
        unknown = False
        for ln in have:
            if ln.amount is None or ln.payer is None or ln.payee is None:
                unknown = True               # unknown money or party: None, not a failure
                continue
            for j, (d, c, _v) in enumerate(src.legs):
                if same_party(ln.payer, d) and same_party(ln.payee, c):
                    sums[j] += ln.amount
                    break
                if same_party(ln.payer, c) and same_party(ln.payee, d):
                    sums[j] -= ln.amount
                    break
            else:
                problems.append(f"{label}: a line {ln.payer} → {ln.payee} matches no leg")
        for j, (d, c, v) in enumerate(src.legs):
            if v is None:
                if not unknown:
                    problems.append(f"{label}: an unknown source needs a None line")
            elif not have and v:
                problems.append(f"{label}: no line for {v:.2f}")
            elif not unknown and abs(sums[j] - v) >= CENT:
                problems.append(f"{label}: {d} → {c} lines {sums[j]:.4f} ≠ source {v:.4f}")
    return problems


def check_conservation(ledger: Ledger, inputs: LedgerInputs,
                       vf: ValueFlowConfig) -> ConservationResult:
    """Per period: double entry, internal nets to zero, coverage,
    reconciliation, inputs established (plan § Conservation and
    reconciliation; see the module notes on what each can see)."""
    out: dict[str, PeriodCheck] = {}
    incomplete = 0
    blocking = _blocking(inputs.input_flags)
    for p in inputs.periods:
        lines = ledger.periods.get(p, [])
        checks: list[dict] = []
        unknown = [ln for ln in lines if ln.amount is None or _side(ln.payer, vf) is None
                   or _side(ln.payee, vf) is None]
        incomplete += len(unknown)

        # 1. double entry
        bad = [f"{ln.source}:{ln.source_id}" for ln in lines
               if ln.payer is not None and ln.payee is not None
               and (same_party(ln.payer, ln.payee)
                    or (ln.amount is not None and (not _finite(ln.amount) or ln.amount < 0)))]
        checks.append({"name": "double_entry", "ok": not bad, "detail": bad[:10]})

        # 2. internal streams net to zero (a bookkeeping identity)
        net: dict[str, float] = {}
        for ln in lines:
            if ln.amount is None or _side(ln.payer, vf) != "internal" or \
                    _side(ln.payee, vf) != "internal":
                continue
            payer, payee = ln.payer.strip().casefold(), ln.payee.strip().casefold()
            net[payer] = net.get(payer, 0.0) - ln.amount
            net[payee] = net.get(payee, 0.0) + ln.amount
        total = sum(net.values())
        checks.append({"name": "internal_nets_to_zero", "ok": abs(total) < CENT,
                       "detail": total})

        # 3. coverage: payer, payee, signed amount and stream per source
        problems = _coverage(lines, _sources(inputs, vf, p))
        checks.append({"name": "coverage", "ok": not problems, "detail": problems[:10]})

        # 4. reconciliation to cost_breakdown
        rec_ok: bool | None
        if unknown:
            rec_ok, detail = None, f"{len(unknown)} line(s) unknown"
        else:
            lhs = 0.0
            for ln in lines:
                a, b = _side(ln.payer, vf), _side(ln.payee, vf)
                if a == "internal" and b == "external":
                    lhs += ln.amount
                elif a == "external" and b == "internal":
                    lhs -= ln.amount
            cb, lp_rows = inputs.cost_breakdown_total.get(p), inputs.lp_commercial.get(p)
            payees = resolve_tariff_payees(inputs.bill_items, vf, inputs.retailer)
            terms = [cb, None if lp_rows is None else -lp_rows]
            for item_id, v in (inputs.bill.get(p) or {}).items():
                if _side(payees.get(item_id), vf) == "external":
                    terms.append(v)
            for table in (inputs.connection_fee, inputs.connection_fixed_fee):
                if p in table and _side(_conn_payee(vf), vf) == "external":
                    terms.append(table[p])
            for s in inputs.settlement:
                if s.get("period") != p or s.get("value_stream") in _VOLUME_ONLY:
                    continue
                a, b = _side(s.get("payer"), vf), _side(s.get("payee"), vf)
                if a == "internal" and b == "external":
                    terms.append(s.get("amount"))
                elif a == "external" and b == "internal":
                    terms.append(None if s.get("amount") is None else -s["amount"])
            if p in inputs.export_revenue:
                v = inputs.export_revenue[p]
                terms.append(None if v is None else -v)
            if any(not _finite(t) for t in terms):
                rec_ok, detail = None, "a bridge term is unknown"
            else:
                rhs = float(sum(terms))
                rec_ok = abs(lhs - rhs) < CENT
                detail = {"ledger": lhs, "bridge": rhs, "difference": lhs - rhs}
        checks.append({"name": "reconciliation", "ok": rec_ok, "detail": detail})

        # 5. the inputs are established (no drift, partial import, unsettled contract)
        checks.append({"name": "inputs_established", "ok": None if blocking else True,
                       "detail": blocking})

        oks = [c["ok"] for c in checks]
        if any(o is False for o in oks):
            ok: bool | None = False
        elif unknown or any(o is None for o in oks):
            ok = None
        else:
            ok = True
        out[p] = PeriodCheck(ok=ok, checks=checks)
    oks = [pc.ok for pc in out.values()]
    overall = False if any(o is False for o in oks) else (None if any(o is None for o in oks)
                                                          else True)
    flags = [f"ledger_incomplete:{incomplete}"] if incomplete else []
    flags += [f"input_not_established:{b}" for b in blocking]
    return ConservationResult(ok=overall, periods=out, flags=flags)


def by_participant(ledger: Ledger) -> dict[str, dict[str, dict]]:
    """Per period and party (as named on the lines): paid, received, net
    (received − paid) and the net by stream. Unknown amounts are skipped (the
    ledger's flags say so)."""
    out: dict[str, dict[str, dict]] = {}
    for p, lines in ledger.periods.items():
        tab: dict[str, dict] = {}

        def row(party):
            return tab.setdefault(party, {"paid": 0.0, "received": 0.0, "net": 0.0,
                                          "by_stream": {}})
        for ln in lines:
            if ln.amount is None or ln.payer is None or ln.payee is None:
                continue
            payer, payee = row(ln.payer), row(ln.payee)
            payer["paid"] += ln.amount
            payee["received"] += ln.amount
            payer["by_stream"][ln.value_stream] = \
                payer["by_stream"].get(ln.value_stream, 0.0) - ln.amount
            payee["by_stream"][ln.value_stream] = \
                payee["by_stream"].get(ln.value_stream, 0.0) + ln.amount
        for r in tab.values():
            r["net"] = r["received"] - r["paid"]
        out[p] = tab
    return out


# ── meter sides (WP3.1, review D1 and WP3.1 review #5) ─────────────────────


@dataclass
class MeterSides:
    site: set[str]
    grid: set[str]
    bypass: set[str]
    meter_links: set[str]


def branch_edges(n, exclude=()) -> dict[str, set[str]]:
    """Bus adjacency over Lines, Transformers and Links (every bus of a
    multi-output Link), leaving out the Links in `exclude` (the meter)."""
    edges: dict[str, set[str]] = {}
    for comp, ends in (("lines", ("bus0", "bus1")), ("transformers", ("bus0", "bus1")),
                       ("links", ("bus0", "bus1", "bus2", "bus3", "bus4"))):
        df = getattr(n, comp)
        cols = [c for c in ends if c in df.columns]
        for name, row in df[cols].iterrows():
            if comp == "links" and name in exclude:
                continue
            buses = [str(row[c]).strip() for c in cols
                     if str(row[c]).strip() and str(row[c]).strip() != "nan"]
            for x in buses[1:]:
                edges.setdefault(buses[0], set()).add(x)
                edges.setdefault(x, set()).add(buses[0])
    return edges


def _bfs(starts, edges) -> dict[str, int]:
    dist = {b: 0 for b in starts}
    frontier = list(starts)
    while frontier:
        nxt = []
        for b in frontier:
            for x in edges.get(b, ()):
                if x not in dist:
                    dist[x] = dist[b] + 1
                    nxt.append(x)
        frontier = nxt
    return dist


def classify_buses(n, commercial: CommercialConfig) -> MeterSides:
    """Which side of the commercial meter each bus is on. Two searches over
    every branch except the meter Links: from the meter's site buses (import
    members' bus1) and from its grid buses (their bus0, the export Link's
    bus1). A bus only the site search reaches is site-side, only the grid
    search grid-side; a bus BOTH reach is behind a connection that bypasses the
    meter — flagged `meter_bypass` and placed on the nearer side, a tie on the
    site side (the meter's site is the modelled connection; the flag says the
    boundary is breached). A bus neither reaches is unclassified."""
    from services.commercial import lp_bindings as _lp

    members = [m for m in _lp.import_links(commercial) if m in n.links.index]
    meter = set(members) | ({commercial.export_link} if commercial.export_link and
                            commercial.export_link in n.links.index else set())
    site_starts = {str(n.links.at[m, "bus1"]) for m in members}
    grid_starts = {str(n.links.at[m, "bus0"]) for m in members}
    if commercial.export_link and commercial.export_link in n.links.index:
        grid_starts.add(str(n.links.at[commercial.export_link, "bus1"]))
    grid_starts -= site_starts
    edges = branch_edges(n, meter)
    ds, dg = _bfs(site_starts, edges), _bfs(grid_starts, edges)
    site, grid, bypass = set(), set(), set()
    for b in set(ds) | set(dg):
        s_, g_ = ds.get(b), dg.get(b)
        if s_ is not None and g_ is not None:
            bypass.add(b)
            (site if s_ <= g_ else grid).add(b)
        elif s_ is not None:
            site.add(b)
        else:
            grid.add(b)
    return MeterSides(site=site, grid=grid, bypass=bypass, meter_links=meter)


def asset_side(n, component: str, name: str, sides: MeterSides) -> tuple[str, list[str]]:
    """(side, flags) of one asset: its bus, or its ends for a branch. A branch
    with an end on each side is a connection across the meter: site-owned and
    flagged `meter_bypass`; any bus in the bypass region flags it too. Meter
    Links are site-side."""
    if component == "Link" and name in sides.meter_links:
        return "site", []
    frame = getattr(n, _COMPONENT_FRAMES.get(component, ""), None)
    if frame is None or name not in frame.index:
        return "unclassified", []
    cols = ["bus"] if "bus" in frame.columns and component not in (
        "Line", "Transformer", "Link") else \
        [c for c in ("bus0", "bus1", "bus2", "bus3", "bus4") if c in frame.columns]
    buses = [str(frame.at[name, c]).strip() for c in cols]
    buses = [b for b in buses if b and b != "nan"]
    on_site = any(b in sides.site for b in buses)
    on_grid = any(b in sides.grid for b in buses)
    flags = ["meter_bypass"] if any(b in sides.bypass for b in buses) else []
    if on_site and on_grid:
        return "site", sorted(set(flags) | {"meter_bypass"})
    if on_site:
        return "site", flags
    if on_grid:
        return "grid", flags
    return "unclassified", flags


# Electric bus carriers (PyPSA and PyPSA-Eur conventions): a generator on one of
# these is never a fuel supply, whatever the PoC bus's own carrier.
_ELECTRIC = frozenset({"ac", "dc", "low voltage", "lv", "mv", "hv", "electricity", ""})


def is_fuel_supply(n, parsed, generator: str) -> bool:
    """A site-side Generator whose output is a FUEL bought for conversion (gas
    behind a CHP Link), not electricity (WP3.1 review round 2 #3): its bus
    carries a non-electric carrier different from the PoC's site bus, no Load,
    and feeds the rest of the site only as the INPUT (bus0) of Links — no Line,
    no Transformer, no Link delivering into it."""
    if generator not in n.generators.index:
        return False
    bus = str(n.generators.at[generator, "bus"])
    carriers = n.buses["carrier"] if "carrier" in n.buses.columns else None
    carrier = str(carriers.get(bus, "")) if carriers is not None else ""
    poc_bus = str(n.links.at[parsed.poc_link, "bus1"]) if parsed.poc_link in n.links.index \
        else None
    site_carrier = str(carriers.get(poc_bus, "")) if (carriers is not None and poc_bus) else ""
    if carrier.strip().casefold() in _ELECTRIC or carrier == site_carrier:
        return False
    if not n.loads.empty and (n.loads["bus"].astype(str) == bus).any():
        return False
    for comp in ("lines", "transformers"):
        df = getattr(n, comp)
        if not df.empty and ((df["bus0"].astype(str) == bus) | (df["bus1"].astype(str) == bus)).any():
            return False
    links = n.links
    outs = [c for c in ("bus1", "bus2", "bus3", "bus4") if c in links.columns]
    if any((links[c].astype(str) == bus).any() for c in outs):
        return False
    return bool((links["bus0"].astype(str) == bus).any()) if not links.empty else False
