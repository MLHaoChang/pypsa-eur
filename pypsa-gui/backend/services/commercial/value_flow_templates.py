"""
Value-flow templates: the five participant structures of spec §7 (Edge
Investment Case P3 WP3.2).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.2.

`build(name, n, commercial, *, dsr_buses=())` → `TemplateResult(config,
draft_contracts, notes)`. Nothing is saved: the caller shows the config and the
drafts and saves them through the solver-config and value-flows routes. The
builders

  * never change `site_party` — the participant with that id gets the
    template's role (its hash is in `contracts_record` / `ppa_dispatch_hash`);
  * never invent a saved contract — a structure that needs one the config does
    not have gets an unsaved DRAFT whose money fields are `null`, so it cannot
    be saved (or solved) until the user prices it (`draft_needs:<id>:<fields>`);
  * own only site-side assets (`participants.classify_buses`); grid-side
    assets are the market's; meter Links and network branches stay with the
    site party (a tenant carries the connection);
  * list every party the contracts name that is not a participant as an
    external, and never keep a participant among the externals — so the
    config passes the value-flows route on a config that has contracts;
  * pin a `template_version`; the fixture pins the versions and a hash of this
    module and the classifier code it relies on, so a change without a
    version bump fails a test.

`template_status` reports `template_edited` (the config changed since it was
built), `template_stale` (the site's assets, contracts or PoC changed) and
`template_outdated` (built by an older version of the builder).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from models.commercial import CommercialConfig, ValueFlowConfig
from services.commercial import participants as P
from services.commercial.lp_bindings import same_party

_FRAMES = (("Generator", "generators"), ("StorageUnit", "storage_units"),
           ("Store", "stores"), ("Link", "links"), ("Line", "lines"),
           ("Transformer", "transformers"))
_DEFAULT_EXTERNALS = tuple(ValueFlowConfig().externals)


class TemplateRefused(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class TemplateResult:
    config: ValueFlowConfig
    draft_contracts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class _Site:
    """What a builder reads from the network."""

    assets: list[tuple[str, str]]              # site-side (component, name)
    generators: list[str]                      # site-side, not fuel supplies
    storage: list[tuple[str, str]]             # site-side StorageUnits and Stores
    loads: list[str]                           # loads on site-side buses
    load_bus: dict[str, str]
    meter_links: set[str]
    member_assets: dict[str, list[tuple[str, str]]]   # group member Link → its assets
    dsr_buses: tuple[str, ...]


@dataclass
class Template:
    name: str
    version: str
    builder: Callable[[CommercialConfig, _Site], TemplateResult]


def code_sha() -> str:
    """Hash of everything a template's output depends on: this module and the
    classifier code in `participants` (WP3.2 review #7)."""
    parts = [inspect.getsource(sys.modules[__name__])]
    parts += [inspect.getsource(f) for f in (P.classify_buses, P.asset_side, P.is_fuel_supply,
                                             P.branch_edges, P._bfs)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()[:16]


def _canonical(dump: dict) -> dict:
    for key, sort_key in (("participants", lambda x: x["id"].strip().casefold()),
                          ("asset_owners", lambda x: (x["component"], x["asset_id"])),
                          ("hub_members", lambda x: x["link"]),
                          ("tariff_payees", lambda x: json.dumps(x, sort_keys=True))):
        if key in dump:
            dump[key] = sorted(dump[key], key=sort_key)
    if "externals" in dump:
        dump["externals"] = sorted(e.strip().casefold() for e in dump["externals"])
    return dump


def config_digest(vf: ValueFlowConfig) -> str:
    """The digest a built config is stamped with: defaults left out (nested
    ones too, and a top-level value equal to its default in any order) and
    lists in canonical order, so a reordering or a new defaulted field is not
    an edit (WP3.2 review #8); its own `built_digest` left out."""
    dump = _canonical(vf.model_dump(mode="json", exclude_defaults=True))
    default = _canonical(ValueFlowConfig().model_dump(mode="json"))
    dump = {k: v for k, v in dump.items() if k != "built_digest" and default.get(k) != v}
    return _digest(dump)


def _site(n, commercial: CommercialConfig, dsr_buses=()) -> _Site:
    sides = P.classify_buses(n, commercial)
    assets = []
    for comp, frame in _FRAMES:
        for name in getattr(n, frame).index:
            if P.asset_side(n, comp, str(name), sides)[0] == "site":
                assets.append((comp, str(name)))
    gens = [name for comp, name in assets
            if comp == "Generator" and not P.is_fuel_supply(n, commercial, name)]
    storage = [(c, a) for c, a in assets if c in ("StorageUnit", "Store")]
    load_bus = {str(ld): str(n.loads.at[ld, "bus"]) for ld in n.loads.index}
    loads = [ld for ld, b in load_bus.items() if b in sides.site]
    members: dict[str, list[tuple[str, str]]] = {}
    if commercial.group_members:
        edges = P.branch_edges(n, sides.meter_links)
        region = {m: set(P._bfs({str(n.links.at[m, "bus1"])}, edges))
                  for m in commercial.group_members if m in n.links.index}
        for comp, name in assets:
            if comp == "Link" and name in sides.meter_links:
                continue
            frame = getattr(n, dict(_FRAMES)[comp])
            cols = ["bus"] if comp in ("Generator", "StorageUnit", "Store") else \
                [c for c in ("bus0", "bus1", "bus2", "bus3", "bus4") if c in frame.columns]
            buses = {str(frame.at[name, c]) for c in cols if str(frame.at[name, c]) not in ("", "nan")}
            hits = [m for m, reg in region.items() if buses & reg]
            if len(hits) == 1:
                members.setdefault(hits[0], []).append((comp, name))
    return _Site(assets=assets, generators=gens, storage=storage, loads=loads,
                 load_bus=load_bus, meter_links=sides.meter_links, member_assets=members,
                 dsr_buses=tuple(dsr_buses or ()))


_PARTY_KEYS = ("seller", "buyer", "sleeving_party", "lessor", "lessee", "counterparty",
               "provider", "customer", "retailer", "generator_owner")


def _contract_key(d: dict):
    """What a builder reads of one contract (a saved one dumped, or a draft):
    type, id, parties, assets, loads — never money fields, which a draft
    leaves for the user to fill."""
    return (d.get("type"), d.get("id"),
            sorted(str(d[k]) for k in _PARTY_KEYS if d.get(k) is not None),
            sorted(d.get("asset_ids") or []), sorted(d.get("load_ids") or []))


def inputs_digest(n, commercial: CommercialConfig, drafts=()) -> str:
    """What a builder reads: the site-side assets, the contracts' parties and
    assets, the PoC (WP3.2 review #9). A build stamps it with its own drafts
    counted as saved, so the normal flow — save the drafts once priced — is
    not stale; a changed party, asset or load of a draft is."""
    site = _site(n, commercial)
    contracts = sorted(
        [_contract_key(c.model_dump(mode="json")) for c in commercial.contracts]
        + [_contract_key(d) for d in drafts], key=json.dumps)
    return _digest({"assets": sorted(site.assets), "contracts": contracts,
                    "poc": commercial.poc_link, "group": list(commercial.group_members)})


def _free_id(commercial: CommercialConfig, base: str) -> str:
    taken = {c.id for c in commercial.contracts}
    cid, i = base, 1
    while cid in taken:
        i += 1
        cid = f"{base}_{i}"
    return cid


def _owned(assets, owner, skip=()) -> list[dict]:
    return [{"asset_id": name, "component": comp, "owner": owner}
            for comp, name in assets if (comp, name) not in skip]


def _participant(pid, name, role):
    return {"id": pid, "name": name, "role": role}


def _single_owner(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes = [] if site.assets else ["no_site_side_assets"]
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(me, me, "site_owner")],
        "asset_owners": _owned(site.assets, me)}), notes=notes)


def _btm_ppa(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes: list[str] = []
    drafts: list[dict] = []
    eligible = [c for c in commercial.contracts if c.type == "ppa"
                and c.kind in ("as_consumed_btm", "pay_as_produced")
                and same_party(c.buyer, me) and not same_party(c.seller, me)]
    if len(eligible) > 1:
        notes.append("several_btm_ppas_first_used:" + ",".join(c.id for c in eligible))
    ppa = eligible[0] if eligible else None
    if ppa is not None:
        developer = ppa.seller
        if any(same_party(developer, e) for e in _DEFAULT_EXTERNALS):
            # Its default-payee tariff items become internal flows to the developer.
            notes.append(f"developer_is_default_external:{developer}")
        assets = [a for a in ppa.asset_ids if a in site.generators]
        off = [a for a in ppa.asset_ids if a not in site.generators]
        if off:
            notes.append(f"ppa_assets_not_on_site:{ppa.id}:" + ",".join(off))
    else:
        covered = {a for c in commercial.contracts if c.type == "ppa" for a in c.asset_ids}
        if covered & set(site.generators):
            raise TemplateRefused(
                "template_conflicting_ppa",
                "a PPA not bought by the site already covers the site's generation; "
                "btm_ppa would draft a second one on the same assets")
        developer, assets = "developer", list(site.generators)
        if not assets:
            raise TemplateRefused("template_needs_site_generation",
                                  "btm_ppa needs a site-side generator to sell as consumed")
        cid = _free_id(commercial, "ppa_draft")
        drafts.append({"type": "ppa", "id": cid, "kind": "as_consumed_btm", "price": None,
                       "tenor_years": 15, "seller": developer, "buyer": me,
                       "asset_ids": assets})
        notes.append(f"draft_needs:{cid}:price")
    dev_owned = [("Generator", a) for a in assets]
    kept = [name for _c, name in site.storage]
    if kept:
        # M5 deviation: no EaaS draft — the site keeps its storage; a developer
        # owning it needs an EaaS or lease contract (stated, not drafted).
        notes.append("site_keeps_assets_developer_needs_eaas_or_lease:" + ",".join(kept))
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(me, me, "offtaker"),
                         _participant(developer, developer, "developer")],
        "asset_owners": _owned(dev_owned, developer) + _owned(site.assets, me, skip=dev_owned),
        "export_revenue_to": "asset_owner"}), draft_contracts=drafts, notes=notes)


def _landlord_tenant(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes: list[str] = ["connection_costs_on_tenant"]
    drafts: list[dict] = []
    lease = next((c for c in commercial.contracts if c.type == "lease"
                  and same_party(c.lessee, me) and not same_party(c.lessor, me)), None)
    landlord = lease.lessor if lease is not None else "landlord"
    candidates = [("Generator", g) for g in site.generators] + list(site.storage)
    if lease is not None:
        names = set(lease.asset_ids)
        leased = [(c, a) for c, a in site.assets if a in names]
        off = sorted(names - {a for _c, a in leased})
        if off:
            notes.append(f"lease_assets_not_on_site:{lease.id}:" + ",".join(off))
    else:
        leased = candidates
        if not leased:
            raise TemplateRefused("template_needs_site_assets",
                                  "landlord_tenant needs a site-side asset to lease")
        cid = _free_id(commercial, "lease_draft")
        drafts.append({"type": "lease", "id": cid, "lessor": landlord, "lessee": me,
                       "annual_payment": None, "tenor_years": 10,
                       "asset_ids": [name for _c, name in leased]})
        notes.append(f"draft_needs:{cid}:annual_payment")
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(landlord, landlord, "landlord"),
                         _participant(me, me, "tenant")],
        "asset_owners": _owned(leased, landlord) + _owned(site.assets, me, skip=leased)}),
        draft_contracts=drafts, notes=notes)


def _dso_developer(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes: list[str] = []
    drafts: list[dict] = []
    drs = [c for c in commercial.contracts if c.type == "dr"]
    # Only a counterparty that IS a DSO (named "dso", or not one of the other
    # default externals) becomes the DSO participant (WP3.2 review #6).
    dso_dr = next((c for c in drs if c.counterparty and not same_party(c.counterparty, me)
                   and (same_party(c.counterparty, "dso")
                        or not any(same_party(c.counterparty, e) for e in _DEFAULT_EXTERNALS))),
                  None)
    dso = dso_dr.counterparty if dso_dr is not None else "dso"
    for c in drs:
        if c is dso_dr:
            continue
        notes.append(f"dr_without_counterparty:{c.id}" if not c.counterparty
                     else f"dr_counterparty_not_a_dso:{c.id}:{c.counterparty}")
    loads = list(dso_dr.load_ids) if dso_dr is not None else list(site.loads)
    if not drs:
        if not site.loads:
            raise TemplateRefused("template_needs_site_load",
                                  "dso_developer needs a site-side load for demand response")
        cid = _free_id(commercial, "dr_draft")
        drafts.append({"type": "dr", "id": cid, "availability_eur_per_mw_year": None,
                       "activation_eur_per_mwh": None, "contracted_mw": None,
                       "load_ids": loads, "counterparty": dso})
        notes.append(f"draft_needs:{cid}:availability_eur_per_mw_year,"
                     "activation_eur_per_mwh,contracted_mw")
    elif dso_dr is not None and dso_dr.contracted_mw is None:
        notes.append(f"dr_needs_contracted_mw:{dso_dr.id}")
    no_dsr = sorted({site.load_bus[ld] for ld in loads if ld in site.load_bus
                     and site.load_bus[ld] not in site.dsr_buses})
    if no_dsr:
        # DR activation settles on the DSR dispatch of the load's bus.
        notes.append("dsr_not_enabled:" + ",".join(no_dsr))
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(me, me, "developer"), _participant(dso, dso, "dso")],
        "asset_owners": _owned(site.assets, me)}), draft_contracts=drafts, notes=notes)


def _energy_hub(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    if commercial.group_contract is None:
        raise TemplateRefused("template_needs_group_contract",
                              "energy_hub needs a group contract (group_members, group_cap_mw)")
    participants = [_participant(me, me, "site_owner")]
    members, owners = [], []
    claimed: set[tuple[str, str]] = set()
    taken = [me]
    for link in commercial.group_members:
        pid, i = f"member_{link}", 1
        while any(same_party(pid, t) for t in taken):
            i += 1
            pid = f"member_{link}_{i}"
        taken.append(pid)
        participants.append(_participant(pid, f"Member {link}", "hub_member"))
        members.append({"link": link, "participant": pid})
        mine = site.member_assets.get(link, [])
        owners += _owned(mine, pid)
        claimed |= set(mine)
    shared = [a for a in site.assets if a not in claimed]
    shown = [name for c, name in shared if not (c == "Link" and name in site.meter_links)]
    notes = ["assets_shared_by_members_owned_by_hub:" + ",".join(shown)] if shown else []
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": participants, "hub_members": members,
        "asset_owners": owners + _owned(shared, me),
        "allocation": {"basis": "energy"}}), notes=notes)


TEMPLATES: dict[str, Template] = {t.name: t for t in (
    Template("single_owner", "2", _single_owner),
    Template("btm_ppa", "3", _btm_ppa),
    Template("landlord_tenant", "2", _landlord_tenant),
    Template("dso_developer", "2", _dso_developer),
    Template("energy_hub", "2", _energy_hub),
)}


def _with_parties(cfg: ValueFlowConfig, commercial: CommercialConfig,
                  drafts: list[dict]) -> ValueFlowConfig:
    """Every party the contracts (and drafts) name that is not a participant
    becomes an external; no participant stays an external (review #1)."""
    ids = [p.id for p in cfg.participants]
    externals = [e for e in cfg.externals if not any(same_party(e, i) for i in ids)]
    named = [p for c in commercial.contracts for _r, p in P._contract_parties(c) if p]
    for d in drafts:
        named += [d.get(k) for k in _PARTY_KEYS if d.get(k)]
    for party in named:
        if not any(same_party(party, i) for i in ids) and \
                not any(same_party(party, e) for e in externals):
            externals.append(party)
    return cfg.model_copy(update={"externals": externals})


def build(name: str, n, commercial: CommercialConfig, *, dsr_buses=()) -> TemplateResult:
    """Build template `name` for network `n` and `commercial` (nothing saved).
    `dsr_buses`: the solver config's DSR buses (DR activation settles on them)."""
    tpl = TEMPLATES.get(name)
    if tpl is None:
        raise TemplateRefused("template_unknown",
                              f"unknown template {name!r}; one of {sorted(TEMPLATES)}")
    result = tpl.builder(commercial, _site(n, commercial, dsr_buses))
    cfg = _with_parties(result.config, commercial, result.draft_contracts)
    cfg = cfg.model_copy(update={"template": name, "template_version": f"{name}@{tpl.version}",
                                 "built_inputs_digest": inputs_digest(
                                     n, commercial, result.draft_contracts)})
    result.config = cfg.model_copy(update={"built_digest": config_digest(cfg)})
    return result


def template_status(vf: ValueFlowConfig, n, commercial: CommercialConfig) -> list[str]:
    """Disclosures about a template-built config (see the module notes). Read
    once the drafts are saved: an unsaved draft counts as a changed input."""
    if vf.template == "custom" or not vf.template_version:
        return []
    out = []
    if vf.built_digest and config_digest(vf) != vf.built_digest:
        out.append("template_edited")
    if vf.built_inputs_digest and inputs_digest(n, commercial) != vf.built_inputs_digest:
        out.append("template_stale")
    name, _, version = vf.template_version.partition("@")
    tpl = TEMPLATES.get(name)
    if tpl is not None and version != tpl.version:
        out.append("template_outdated")
    return out
