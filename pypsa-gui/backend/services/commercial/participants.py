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
# P4 mapping (plan WP3.1, pinned): a line becomes one `CashflowLine` per
# operating year for each INTERNAL side (participant = that side, counterparty
# = the other, amount signed from the participant; `provenance.source` carries
# `source` / `source_id` / `contract_id`). `basis="annuity"` and
# `basis="model_only"` lines are not converted (P4 assigns overnight capex to
# the owner from `asset_owners`); a None line makes that participant's returns
# `not_established`.

from dataclasses import dataclass, field  # noqa: E402

from models.commercial import TariffPayeeRule  # noqa: E402,F401  (re-exported for callers)

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
CONNECTION_FEE_ID = "connection_fee"   # a TariffPayeeRule item_id that names the fee's payee


@dataclass
class BillItem:
    item_id: str
    kind: str
    measured_on: str
    direction: str


@dataclass
class AssetCost:
    """One asset's unweighted per-period cost: `capex` (annuitised investment),
    `fom`, `opex` (operational expenditure), keyed like the ledger periods."""

    component: str
    name: str
    side: str                       # "site" | "grid" | "unclassified"
    flags: list[str]
    capex: dict[str, float]
    fom: dict[str, float]
    opex: dict[str, float]


@dataclass
class LedgerInputs:
    periods: list[str]
    site_party: str
    bill_items: dict[str, BillItem]
    bill: dict[str, dict[str, float | None]]          # site view: + the site pays
    bill_flags: dict[str, list[str]]
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


def _item_stream(it: BillItem, payee: str) -> str:
    if it.kind == "energy":
        if it.direction == "revenue" or it.measured_on == "export":
            return "energy_export"
        return "network_energy" if same_party(payee, "dso") else "energy_import"
    if it.measured_on == "peak_import":
        return "demand_charge"
    return _KIND_STREAMS.get(it.kind, "other")


def _signed_line(period, debtor, creditor, value, **kw) -> ValueFlowLine:
    """A line for a SIGNED value (+ debtor pays creditor, − the reverse)."""
    if value is None:
        return ValueFlowLine(period=period, payer=debtor, payee=creditor, amount=None, **kw)
    if value < 0:
        return ValueFlowLine(period=period, payer=creditor, payee=debtor, amount=-value, **kw)
    return ValueFlowLine(period=period, payer=debtor, payee=creditor, amount=value, **kw)


def _owners(vf: ValueFlowConfig) -> dict[tuple[str, str], str]:
    return {(o.component, o.asset_id): o.owner for o in vf.asset_owners}


def build_ledger(inputs: LedgerInputs, vf: ValueFlowConfig) -> Ledger:
    """Every source of `inputs` as lines (see the module notes)."""
    site = inputs.site_party
    payees = resolve_tariff_payees(inputs.bill_items, vf, inputs.retailer)
    conn_payee = next((r.payee for r in vf.tariff_payees if r.item_id == CONNECTION_FEE_ID),
                      "dso")
    owners = _owners(vf)
    flags: set[str] = set()
    notes: list[str] = []
    defaulted = 0
    periods: dict[str, list[ValueFlowLine]] = {p: [] for p in inputs.periods}
    split = inputs.export_split if vf.export_revenue_to == "asset_owner" else None

    def owner_of(key) -> str:
        return site if key is None else owners.get(tuple(key), site)

    for p in inputs.periods:
        out = periods[p]
        # The bill, per item (signed site view).
        for item_id, it in inputs.bill_items.items():
            v = (inputs.bill.get(p) or {}).get(item_id)
            payee = payees[item_id]
            common = dict(value_stream=_item_stream(it, payee), source="bill",
                          source_id=item_id, tariff_item=item_id, tariff_item_kind=it.kind)
            parts = (split or {}).get(p, {}).get(item_id) if _finite(v) and v < 0 else None
            if parts:
                for key, amount in sorted(parts.items(), key=lambda kv: str(kv[0])):
                    out.append(_signed_line(p, owner_of(key), payee, amount, **common))
                continue
            line = _signed_line(p, site, payee, v if _finite(v) else None, **common)
            if not _finite(v):
                line.flags.append("bill_item_not_established")
            out.append(line)
        # Connection-agreement fees.
        for sid, table in (("fee", inputs.connection_fee), ("fixed_fee",
                                                            inputs.connection_fixed_fee)):
            if p in table:
                v = table[p]
                line = _signed_line(p, site, conn_payee, v if _finite(v) else None,
                                    value_stream="network_capacity", source="connection",
                                    source_id=sid)
                if not _finite(v):
                    line.flags.append("connection_fee_not_established")
                out.append(line)
        if inputs.curtailment_compensation:
            out.append(ValueFlowLine(period=p, payer=conn_payee, payee=site, amount=None,
                                     value_stream="network_capacity",
                                     source="curtailment_compensation",
                                     source_id="curtailment_compensation",
                                     flags=["curtailment_compensation_not_computed"]))
        # The export price (+ participants receive).
        if p in inputs.export_revenue:
            v = inputs.export_revenue[p]
            common = dict(value_stream="energy_export", source="export_price",
                          source_id="export_price")
            parts = (split or {}).get(p, {}).get("export_price") if _finite(v) else None
            if parts:
                for key, amount in sorted(parts.items(), key=lambda kv: str(kv[0])):
                    out.append(_signed_line(p, "market", owner_of(key), amount, **common))
            else:
                line = _signed_line(p, "market", site, v if _finite(v) else None, **common)
                if not _finite(v):
                    line.flags.append("export_revenue_not_established")
                out.append(line)
        # Contract settlement.
        for i, s in enumerate(inputs.settlement):
            if s.get("period") != p:
                continue
            stream = s.get("value_stream")
            if stream in _VOLUME_ONLY:
                notes.append(f"{stream}:{s.get('contract_id')}:{p}:{s.get('quantity_mwh')}")
                continue
            kind = _SETTLEMENT_STREAMS.get(stream, "other")
            line = _signed_line(p, s.get("payer"), s.get("payee"),
                                s.get("amount") if _finite(s.get("amount")) else None,
                                value_stream=kind, source="contract",
                                source_id=f"{s.get('contract_id')}:{i}",
                                contract_id=s.get("contract_id"),
                                flags=list(s.get("flags") or []))
            if kind == "other":
                line.flags.append(f"unmapped_stream:{stream}")
            out.append(line)
        # Assets.
        for a in inputs.assets:
            key = (a.component, a.name)
            capex, fom, opex = (a.capex.get(p, 0.0), a.fom.get(p, 0.0), a.opex.get(p, 0.0))
            base = dict(asset=a.name)
            if a.side == "grid":
                fixed = capex + fom
                if fixed:
                    out.append(_signed_line(p, site, "market", fixed, value_stream="other",
                                            source="asset", source_id=f"fixed:{a.component}:{a.name}",
                                            basis="model_only",
                                            flags=[*a.flags, "grid_side_asset_cost"], **base))
                if opex:
                    gen = a.component == "Generator"
                    out.append(_signed_line(
                        p, site, "market", opex,
                        value_stream="energy_import" if gen else "other", source="asset",
                        source_id=f"opex:{a.component}:{a.name}",
                        basis="cash" if gen else "model_only",
                        flags=[*a.flags, "commodity_from_grid_side_generator" if gen
                               else "grid_side_asset_cost"], **base))
                continue
            owner = owners.get(key)
            if owner is None:
                owner = site
                if capex or fom or opex:
                    defaulted += 1 if p == inputs.periods[0] else 0
            extra = ["asset_side_unclassified"] if a.side == "unclassified" else []
            for kind, value, payee, stream, basis in (
                    ("capex", capex, "capex_supplier", "capex", "annuity"),
                    ("fom", fom, "om_contractor", "fom", "cash"),
                    ("opex", opex, "om_contractor", "vom", "cash")):
                if value:
                    out.append(_signed_line(p, owner, payee, value, value_stream=stream,
                                            source="asset",
                                            source_id=f"{kind}:{a.component}:{a.name}",
                                            basis=basis, flags=[*a.flags, *extra], **base))
    for (component, name), owner in owners.items():
        side = next((a.side for a in inputs.assets
                     if a.component == component and a.name == name), None)
        if side == "grid":
            flags.add(f"grid_side_asset_not_ownable:{name}")
    if defaulted:
        notes.append(f"asset_owner_defaulted:{defaulted}")
    # Party resolution and external↔external lines.
    ids = [x.id for x in vf.participants]
    for p, lines in periods.items():
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
    disclosures = {p: dict(inputs.disclosures.get(p) or {}) for p in inputs.periods}
    for p, d in disclosures.items():
        if d.get("dsr_slack"):
            flags.add("dsr_slack_not_a_cash_flow")
        if d.get("voll"):
            flags.add("voll_not_a_cash_flow")
    return Ledger(periods=periods, tariff_payees=payees, flags=sorted(flags), notes=notes,
                  disclosures=disclosures)


def _side(party: str | None, vf: ValueFlowConfig) -> str | None:
    if party is None:
        return None
    if _in(party, [x.id for x in vf.participants]):
        return "internal"
    if _in(party, vf.externals):
        return "external"
    return None


def _expected_sources(inputs: LedgerInputs, vf: ValueFlowConfig, p: str) -> dict:
    """(source, source_id) → (expected signed value or None, how to sign a line)."""
    site = inputs.site_party
    payees = resolve_tariff_payees(inputs.bill_items, vf, inputs.retailer)
    conn_payee = next((r.payee for r in vf.tariff_payees if r.item_id == CONNECTION_FEE_ID),
                      "dso")
    exp: dict[tuple[str, str], tuple] = {}

    def toward(creditor):
        # + when someone pays the creditor, − when the creditor pays.
        return lambda ln: (ln.amount if same_party(ln.payee, creditor)
                           else -ln.amount if same_party(ln.payer, creditor) else 0.0)

    for item_id in inputs.bill_items:
        v = (inputs.bill.get(p) or {}).get(item_id)
        exp[("bill", item_id)] = (v if _finite(v) else None, toward(payees[item_id]))
    for sid, table in (("fee", inputs.connection_fee), ("fixed_fee",
                                                        inputs.connection_fixed_fee)):
        if p in table:
            v = table[p]
            exp[("connection", sid)] = (v if _finite(v) else None, toward(conn_payee))
    if inputs.curtailment_compensation:
        exp[("curtailment_compensation", "curtailment_compensation")] = (None, toward(site))
    if p in inputs.export_revenue:
        v = inputs.export_revenue[p]
        exp[("export_price", "export_price")] = (
            v if _finite(v) else None,
            lambda ln: (ln.amount if same_party(ln.payer, "market")
                        else -ln.amount if same_party(ln.payee, "market") else 0.0))
    for i, s in enumerate(inputs.settlement):
        if s.get("period") != p or s.get("value_stream") in _VOLUME_ONLY:
            continue
        payer, payee = s.get("payer"), s.get("payee")
        v = s.get("amount")

        def sign(ln, payer=payer, payee=payee):
            if ln.payer == payer and ln.payee == payee:
                return ln.amount
            if ln.payer == payee and ln.payee == payer:
                return -ln.amount
            return 0.0
        exp[("contract", f"{s.get('contract_id')}:{i}")] = (v if _finite(v) else None, sign)
    for a in inputs.assets:
        capex, fom, opex = (a.capex.get(p, 0.0), a.fom.get(p, 0.0), a.opex.get(p, 0.0))
        if a.side == "grid":
            for kind, value in (("fixed", capex + fom), ("opex", opex)):
                if value:
                    exp[("asset", f"{kind}:{a.component}:{a.name}")] = (value, toward("market"))
            continue
        for kind, value, supplier in (("capex", capex, "capex_supplier"),
                                      ("fom", fom, "om_contractor"),
                                      ("opex", opex, "om_contractor")):
            if value:
                exp[("asset", f"{kind}:{a.component}:{a.name}")] = (value, toward(supplier))
    return exp


def check_conservation(ledger: Ledger, inputs: LedgerInputs,
                       vf: ValueFlowConfig) -> ConservationResult:
    """The four checks per period (plan § Conservation and reconciliation)."""
    out: dict[str, PeriodCheck] = {}
    incomplete = 0
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

        # 2. internal streams net to zero (an identity over well-formed lines)
        net: dict[str, float] = {}
        for ln in lines:
            if ln.amount is None or _side(ln.payer, vf) != "internal" or \
                    _side(ln.payee, vf) != "internal":
                continue
            net[ln.payer.strip().casefold()] = net.get(ln.payer.strip().casefold(), 0.0) \
                - ln.amount
            net[ln.payee.strip().casefold()] = net.get(ln.payee.strip().casefold(), 0.0) \
                + ln.amount
        total = sum(net.values())
        checks.append({"name": "internal_nets_to_zero", "ok": abs(total) < CENT,
                       "detail": total})

        # 3. coverage, signed
        exp = _expected_sources(inputs, vf, p)
        got: dict[tuple[str, str], list[ValueFlowLine]] = {}
        for ln in lines:
            got.setdefault((ln.source, ln.source_id), []).append(ln)
        problems = []
        for key in sorted(set(exp) | set(got), key=str):
            if key not in exp:
                problems.append(f"{key[0]}:{key[1]}: a line with no source")
                continue
            want, sign = exp[key]
            have = got.get(key, [])
            if want is None:
                if not have or any(ln.amount is not None for ln in have):
                    problems.append(f"{key[0]}:{key[1]}: an unknown source needs a None line")
                continue
            if not have:
                problems.append(f"{key[0]}:{key[1]}: no line for {want:.2f}")
                continue
            if any(ln.amount is None for ln in have):
                continue                     # unknown; counted as incomplete above
            s = sum(sign(ln) for ln in have)
            if abs(s - want) >= CENT:
                problems.append(f"{key[0]}:{key[1]}: lines {s:.4f} ≠ source {want:.4f}")
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
            conn_payee = next((r.payee for r in vf.tariff_payees
                               if r.item_id == CONNECTION_FEE_ID), "dso")
            terms = [cb, None if lp_rows is None else -lp_rows]
            for item_id, v in (inputs.bill.get(p) or {}).items():
                if _side(payees.get(item_id), vf) == "external":
                    terms.append(v)
            for table in (inputs.connection_fee, inputs.connection_fixed_fee):
                if p in table and _side(conn_payee, vf) == "external":
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


# ── meter sides (WP3.1, review D1) ─────────────────────────────────────────


@dataclass
class MeterSides:
    site: set[str]
    grid: set[str]
    bypass: set[str]
    meter_links: set[str]


def classify_buses(n, commercial: CommercialConfig) -> MeterSides:
    """Site side = `lp_bindings._meter_sides`' search from behind the meter;
    grid side = the meter Links' grid buses plus everything reachable from
    them without crossing the meter or entering the site; `bypass` = grid
    buses the site reaches without the meter (flagged `meter_bypass`). A bus
    in neither set is unclassified."""
    from services.commercial import lp_bindings as _lp

    site, bypass = _lp._meter_sides(n, commercial)
    meter = set(_lp.import_links(commercial)) | (
        {commercial.export_link} if commercial.export_link else set())
    starts = {str(n.links.at[m, "bus0"]) for m in _lp.import_links(commercial)
              if m in n.links.index}
    if commercial.export_link and commercial.export_link in n.links.index:
        starts.add(str(n.links.at[commercial.export_link, "bus1"]))
    starts -= site
    edges: dict[str, set[str]] = {}
    for comp, ends in (("lines", ("bus0", "bus1")), ("transformers", ("bus0", "bus1")),
                       ("links", ("bus0", "bus1", "bus2", "bus3", "bus4"))):
        df = getattr(n, comp)
        cols = [c for c in ends if c in df.columns]
        for name, row in df[cols].iterrows():
            if comp == "links" and name in meter:
                continue
            buses = [str(row[c]).strip() for c in cols
                     if str(row[c]).strip() and str(row[c]).strip() != "nan"]
            for x in buses[1:]:
                edges.setdefault(buses[0], set()).add(x)
                edges.setdefault(x, set()).add(buses[0])
    grid, todo = set(), list(starts)
    while todo:
        b = todo.pop()
        if b in grid or b in site:
            continue
        grid.add(b)
        todo.extend(edges.get(b, ()))
    return MeterSides(site=set(site), grid=grid | set(bypass), bypass=set(bypass),
                      meter_links={m for m in meter if m in n.links.index})


def asset_side(n, component: str, name: str, sides: MeterSides) -> tuple[str, list[str]]:
    """(side, flags) of one asset: its bus, or its ends for a branch. A branch
    with an end on each side is the bypass connection itself: site-owned and
    flagged `meter_bypass`. Meter Links are site-side."""
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
