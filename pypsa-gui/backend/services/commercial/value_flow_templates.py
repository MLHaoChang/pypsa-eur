"""
Value-flow templates: the five participant structures of spec §7 (Edge
Investment Case P3 WP3.2).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.2.

`build(name, n, commercial)` → `TemplateResult(config, draft_contracts, notes)`.
Nothing is saved: the caller shows the config and the drafts, and saves them
through the value-flows and solver-config routes. The builders

  * never change `site_party` — the participant with that id gets the
    template's role (its hash is in `contracts_record` / `ppa_dispatch_hash`);
  * never invent a saved contract — a structure that needs one the config does
    not have gets an unsaved DRAFT with placeholder money (noted
    `draft_needs_price:<id>`) for the user to confirm;
  * own only site-side assets (`participants.classify_buses`): grid-side
    assets are the market's; meter Links and network branches stay with the
    site party;
  * pin `template_version` (the fixture `value_flow_templates.json` pins each
    builder's version and source hash, so an edit without a version bump fails
    a test) and stamp `built_digest` / `built_assets_digest`, from which
    `template_status` reports `template_edited` / `template_stale`.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from typing import Callable

from models.commercial import CommercialConfig, ValueFlowConfig
from services.commercial import participants as P
from services.commercial.lp_bindings import same_party

_FRAMES = (("Generator", "generators"), ("StorageUnit", "storage_units"),
           ("Store", "stores"), ("Link", "links"), ("Line", "lines"),
           ("Transformer", "transformers"))


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
    meter_links: set[str]
    member_assets: dict[str, list[tuple[str, str]]]   # group member Link → its assets


@dataclass
class Template:
    name: str
    version: str
    builder: Callable[[CommercialConfig, _Site], TemplateResult]


def source_sha(fn) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()[:16]


def config_digest(vf: ValueFlowConfig) -> str:
    """The digest a built config is stamped with (its own `built_digest` left out)."""
    dump = vf.model_dump(mode="json")
    dump.pop("built_digest", None)
    return _digest(dump)


def _site(n, commercial: CommercialConfig) -> _Site:
    sides = P.classify_buses(n, commercial)
    assets = []
    for comp, frame in _FRAMES:
        for name in getattr(n, frame).index:
            if P.asset_side(n, comp, str(name), sides)[0] == "site":
                assets.append((comp, str(name)))
    gens = [name for comp, name in assets
            if comp == "Generator" and not P.is_fuel_supply(n, commercial, name)]
    storage = [(c, a) for c, a in assets if c in ("StorageUnit", "Store")]
    loads = [str(ld) for ld in n.loads.index if str(n.loads.at[ld, "bus"]) in sides.site]
    members: dict[str, list[tuple[str, str]]] = {}
    if commercial.group_members:
        edges = P.branch_edges(n, sides.meter_links)
        region = {m: set(P._bfs({str(n.links.at[m, "bus1"])}, edges))
                  for m in commercial.group_members if m in n.links.index}
        for comp, name in assets:
            if comp == "Link" and name in sides.meter_links:
                continue
            frame = getattr(n, dict(_FRAMES)[comp])
            cols = ["bus"] if comp in ("Generator", "StorageUnit", "Store") else ["bus0", "bus1"]
            buses = {str(frame.at[name, c]) for c in cols if c in frame.columns}
            hits = [m for m, reg in region.items() if buses & reg]
            if len(hits) == 1:
                members.setdefault(hits[0], []).append((comp, name))
    return _Site(assets=assets, generators=gens, storage=storage, loads=loads,
                 meter_links=sides.meter_links, member_assets=members)


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
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(me, me, "site_owner")],
        "asset_owners": _owned(site.assets, me)}))


def _btm_ppa(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes: list[str] = []
    drafts: list[dict] = []
    ppa = next((c for c in commercial.contracts if c.type == "ppa"
                and c.kind in ("as_consumed_btm", "pay_as_produced")
                and same_party(c.buyer, me) and not same_party(c.seller, me)), None)
    if ppa is not None:
        developer, assets = ppa.seller, list(ppa.asset_ids)
    else:
        developer, assets = "developer", list(site.generators)
        if not assets:
            raise TemplateRefused("template_needs_site_generation",
                                  "btm_ppa needs a site-side generator to sell as consumed")
        cid = _free_id(commercial, "ppa_draft")
        drafts.append({"type": "ppa", "id": cid, "kind": "as_consumed_btm", "price": 0.0,
                       "tenor_years": 15, "seller": developer, "buyer": me,
                       "asset_ids": assets})
        notes.append(f"draft_needs_price:{cid}")
    dev_owned = [("Generator", a) for a in assets]
    others = [x for x in site.storage if x not in dev_owned]
    if others:
        # The developer owns only what it sells; owning more needs an EaaS or lease.
        notes.append("other_developer_assets_need_eaas_or_lease:" +
                     ",".join(name for _c, name in others))
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(me, me, "offtaker"),
                         _participant(developer, developer, "developer")],
        "asset_owners": _owned(dev_owned, developer) + _owned(site.assets, me, skip=dev_owned),
        "export_revenue_to": "asset_owner"}), draft_contracts=drafts, notes=notes)


def _landlord_tenant(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes: list[str] = []
    drafts: list[dict] = []
    lease = next((c for c in commercial.contracts if c.type == "lease"
                  and same_party(c.lessee, me) and not same_party(c.lessor, me)), None)
    landlord = lease.lessor if lease is not None else "landlord"
    leased = [("Generator", g) for g in site.generators] + list(site.storage)
    if lease is None:
        if not leased:
            raise TemplateRefused("template_needs_site_assets",
                                  "landlord_tenant needs a site-side asset to lease")
        cid = _free_id(commercial, "lease_draft")
        drafts.append({"type": "lease", "id": cid, "lessor": landlord, "lessee": me,
                       "annual_payment": 0.0, "tenor_years": 10,
                       "asset_ids": [name for _c, name in leased]})
        notes.append(f"draft_needs_price:{cid}")
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(landlord, landlord, "landlord"),
                         _participant(me, me, "tenant")],
        "asset_owners": _owned(leased, landlord) + _owned(site.assets, me, skip=leased)}),
        draft_contracts=drafts, notes=notes)


def _dso_developer(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    notes: list[str] = []
    drafts: list[dict] = []
    dr = next((c for c in commercial.contracts if c.type == "dr"
               and c.counterparty and not same_party(c.counterparty, me)), None)
    dso = dr.counterparty if dr is not None else "dso"
    if dr is None:
        if not site.loads:
            raise TemplateRefused("template_needs_site_load",
                                  "dso_developer needs a site-side load for demand response")
        cid = _free_id(commercial, "dr_draft")
        drafts.append({"type": "dr", "id": cid, "availability_eur_per_mw_year": 0.0,
                       "activation_eur_per_mwh": 0.0, "load_ids": list(site.loads),
                       "counterparty": dso})
        notes.append(f"draft_needs_price:{cid}")
    externals = [e for e in ValueFlowConfig().externals if not same_party(e, dso)]
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": [_participant(me, me, "developer"), _participant(dso, dso, "dso")],
        "externals": externals, "asset_owners": _owned(site.assets, me)}),
        draft_contracts=drafts, notes=notes)


def _energy_hub(commercial: CommercialConfig, site: _Site) -> TemplateResult:
    me = commercial.site_party
    if commercial.group_contract is None:
        raise TemplateRefused("template_needs_group_contract",
                              "energy_hub needs a group contract (group_members, group_cap_mw)")
    participants = [_participant(me, me, "site_owner")]
    members, owners = [], []
    claimed: set[tuple[str, str]] = set()
    for link in commercial.group_members:
        pid = f"member_{link}"
        participants.append(_participant(pid, f"Member {link}", "hub_member"))
        members.append({"link": link, "participant": pid})
        mine = site.member_assets.get(link, [])
        owners += _owned(mine, pid)
        claimed |= set(mine)
    shared = [a for a in site.assets if a not in claimed]
    notes = ["assets_shared_by_members_owned_by_hub:" +
             ",".join(name for c, name in shared if not (c == "Link" and name in site.meter_links))]\
        if any(not (c == "Link" and name in site.meter_links) for c, name in shared) else []
    return TemplateResult(config=ValueFlowConfig.model_validate({
        "participants": participants, "hub_members": members,
        "asset_owners": owners + _owned(shared, me),
        "allocation": {"basis": "energy"}}), notes=notes)


TEMPLATES: dict[str, Template] = {t.name: t for t in (
    Template("single_owner", "1", _single_owner),
    Template("btm_ppa", "1", _btm_ppa),
    Template("landlord_tenant", "1", _landlord_tenant),
    Template("dso_developer", "1", _dso_developer),
    Template("energy_hub", "1", _energy_hub),
)}


def assets_digest(n, commercial: CommercialConfig) -> str:
    return _digest(sorted(_site(n, commercial).assets))


def build(name: str, n, commercial: CommercialConfig) -> TemplateResult:
    """Build template `name` for network `n` and `commercial` (nothing saved)."""
    tpl = TEMPLATES.get(name)
    if tpl is None:
        raise TemplateRefused("template_unknown",
                              f"unknown template {name!r}; one of {sorted(TEMPLATES)}")
    site = _site(n, commercial)
    result = tpl.builder(commercial, site)
    cfg = result.config.model_copy(update={
        "template": name, "template_version": f"{name}@{tpl.version}",
        "built_assets_digest": _digest(sorted(site.assets))})
    result.config = cfg.model_copy(update={"built_digest": config_digest(cfg)})
    return result


def template_status(vf: ValueFlowConfig, n, commercial: CommercialConfig) -> list[str]:
    """Disclosures about a template-built config: `template_edited` (changed
    since it was built), `template_stale` (the site's assets changed)."""
    if vf.template == "custom" or not vf.template_version:
        return []
    out = []
    if vf.built_digest and config_digest(vf) != vf.built_digest:
        out.append("template_edited")
    if vf.built_assets_digest and assets_digest(n, commercial) != vf.built_assets_digest:
        out.append("template_stale")
    return out
