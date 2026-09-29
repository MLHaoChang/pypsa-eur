"""
The five value-flow templates of spec §7 (Edge Investment Case P3 WP3.2).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.2.
A builder reads the network and the commercial config and returns a
`TemplateResult(config, draft_contracts, notes)`; nothing is saved. It never
changes `site_party` (the participant with that id gets the template's role),
never invents a saved contract (a missing one is an unsaved draft whose money
fields are null, so it cannot be saved until priced), lists every contract
party that is not a participant as an external, and pins its
`template_version`; `built_digest` / `built_inputs_digest` let the ledger say
`template_edited` / `template_stale` / `template_outdated`. A template config,
with its drafts priced and saved, passes the value-flows route — and, on V1,
solves and closes the ledger through the real routes (WP3.2 review #1–#3).
"""
from __future__ import annotations

import copy
import json
import queue
import threading
from pathlib import Path

import pytest

from models.commercial import CommercialConfig, ValueFlowConfig
from services.commercial import participants as P
from services.commercial import value_flow_templates as T
from tests.fixtures.investment_case.edge_15min import build_edge_15min

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "investment_case" / \
    "value_flow_templates.json"
TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "all", "rate": 0.2}]}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 20.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"]}
LEASE = {"type": "lease", "id": "lease1", "lessor": "Leasing GmbH", "lessee": "site",
         "annual_payment": 120_000.0, "tenor_years": 10, "asset_ids": ["bess"]}
# What a user types into a draft's null money fields before saving it.
FILL = {"ppa": {"price": 20.0}, "lease": {"annual_payment": 50_000.0},
        "dr": {"availability_eur_per_mw_year": 1_000.0, "activation_eur_per_mwh": 100.0,
               "contracted_mw": 2.0}}


def _commercial(**extra) -> CommercialConfig:
    return CommercialConfig.model_validate({"poc_link": "import", "import_tariff": TARIFF,
                                            **extra})


def _hub_network():
    n = build_edge_15min()
    n.add("Bus", "poc2", carrier="AC")
    n.add("Link", "import2", bus0="grid", bus1="poc2", p_nom=10.0)
    n.add("Generator", "pv2", bus="poc2", carrier="solar", p_nom=3.0)
    n.add("Load", "load2", bus="poc2", p_set=1.0)
    return n


def _priced(drafts: list[dict]) -> list[dict]:
    return [{**d, **FILL[d["type"]]} for d in drafts]


def _saved(result, commercial: CommercialConfig) -> CommercialConfig:
    """The commercial config with the template's drafts priced and confirmed."""
    raw = commercial.model_dump(mode="json")
    raw["contracts"] = [*raw["contracts"], *_priced(result.draft_contracts)]
    return CommercialConfig.model_validate(raw)


def _build(name, n, commercial, **kw):
    result = T.build(name, n, commercial, **kw)
    assert result.config.template == name
    assert result.config.template_version == f"{name}@2"
    saved = _saved(result, commercial)
    assert P.value_flows_problems(result.config, saved, n) == [], result.config
    assert T.template_status(result.config, n, saved) == []     # the normal flow is clean
    return result


# ── the registry is pinned ─────────────────────────────────────────────────


def test_every_template_of_the_spec_is_registered():
    assert set(T.TEMPLATES) == {"single_owner", "btm_ppa", "landlord_tenant",
                                "dso_developer", "energy_hub"}


def test_a_template_code_change_needs_a_version_bump():
    """The fixture pins a hash of the template module and the classifier code
    it relies on (review #7), with the versions at that hash. A code change
    appends an entry; an entry that bumps no version fails."""
    history = json.loads(FIXTURE.read_text())["history"]
    current = {name: t.version for name, t in T.TEMPLATES.items()}
    assert history[-1]["code_sha"] == T.code_sha(), (
        "template code changed: bump the version of every builder whose output may "
        "change and append {code_sha, versions} to the fixture")
    assert history[-1]["versions"] == current
    for before, after in zip(history, history[1:]):
        assert before["versions"] != after["versions"], after["code_sha"]


def test_the_code_hash_covers_the_meter_side_classifier(monkeypatch):
    before = T.code_sha()

    def classify_buses(n, commercial):          # a different classifier
        return None

    monkeypatch.setattr(T.P, "classify_buses", classify_buses)
    assert T.code_sha() != before


# ── each template ──────────────────────────────────────────────────────────


def test_single_owner_owns_every_site_side_asset():
    n = build_edge_15min()
    r = _build("single_owner", n, _commercial())
    assert [p.id for p in r.config.participants] == ["site"]
    assert r.config.participants[0].role == "site_owner"
    owned = {(o.component, o.asset_id) for o in r.config.asset_owners}
    assert {("Generator", "pv"), ("StorageUnit", "bess")} <= owned
    assert ("Generator", "grid_supply") not in owned          # grid side: never owned
    assert r.draft_contracts == []


def test_single_owner_keeps_a_custom_site_party():
    r = _build("single_owner", build_edge_15min(), _commercial(site_party="Acme DC"))
    assert [p.id for p in r.config.participants] == ["Acme DC"]


def test_a_stale_poc_is_noted_not_silent():
    """Review #9: an unknown poc_link leaves nothing site-side — say so."""
    r = T.build("single_owner", build_edge_15min(), _commercial(poc_link="nope"))
    assert r.config.asset_owners == [] and "no_site_side_assets" in r.notes


@pytest.mark.parametrize("name", ["single_owner", "btm_ppa", "landlord_tenant",
                                  "dso_developer"])
def test_every_template_lists_the_v1_contract_parties_as_externals(name):
    """Review #1: a config with contracts (V1: a PPA and a lease) passes the
    value-flows route — each party is a participant or an external, never both."""
    commercial = _commercial(contracts=[PPA, LEASE])
    r = _build(name, build_edge_15min(), commercial)
    ids = {p.id for p in r.config.participants}
    assert not ids & set(r.config.externals)
    for party in ("Solar BV", "Leasing GmbH"):
        assert party in ids | set(r.config.externals), (name, party)


def test_energy_hub_lists_the_v1_contract_parties_as_externals():
    commercial = _commercial(contracts=[PPA, LEASE], group_contract="hub",
                             group_members=["import", "import2"], group_cap_mw=60.0)
    r = _build("energy_hub", _hub_network(), commercial)
    assert {"Solar BV", "Leasing GmbH"} <= set(r.config.externals)


def test_a_party_that_is_a_default_external_is_not_listed_twice():
    ppa = {**PPA, "seller": "retailer"}
    r = _build("single_owner", build_edge_15min(), _commercial(contracts=[ppa]))
    assert r.config.externals.count("retailer") == 1


def test_btm_ppa_with_its_ppa_gives_the_developer_only_the_ppa_assets():
    ppa = {**PPA, "kind": "as_consumed_btm", "seller": "SunCo", "tenor_years": 15}
    r = _build("btm_ppa", build_edge_15min(), _commercial(contracts=[ppa]))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "offtaker", "SunCo": "developer"}
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["pv"] == "SunCo" and owners["bess"] == "site"
    assert r.config.export_revenue_to == "asset_owner"
    assert r.draft_contracts == []
    assert "site_keeps_assets_developer_needs_eaas_or_lease:bess" in r.notes


def test_btm_ppa_without_a_ppa_drafts_one_with_no_price():
    """Review #3: the draft's price is null — it cannot be saved (or solve at
    a silent 0) until the user prices it."""
    commercial = _commercial()
    r = _build("btm_ppa", build_edge_15min(), commercial)
    (draft,) = r.draft_contracts
    assert draft["type"] == "ppa" and draft["kind"] == "as_consumed_btm"
    assert draft["seller"] == "developer" and draft["buyer"] == "site"
    assert draft["asset_ids"] == ["pv"] and draft["price"] is None
    assert commercial.contracts == []                       # nothing was saved
    assert f"draft_needs:{draft['id']}:price" in r.notes
    assert "developer" in {p.id for p in r.config.participants}
    raw = commercial.model_dump(mode="json")
    with pytest.raises(ValueError):
        CommercialConfig.model_validate({**raw, "contracts": [draft]})


def test_btm_ppa_keeps_only_the_site_side_assets_of_its_ppa():
    """Review #4: an off-site or missing asset of the PPA is noted, never owned."""
    n = build_edge_15min()
    ppa = {**PPA, "asset_ids": ["pv", "grid_supply", "ghost"]}
    r = T.build("btm_ppa", n, _commercial(contracts=[ppa]))
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["pv"] == "Solar BV" and "grid_supply" not in owners
    assert "ppa_assets_not_on_site:ppa1:grid_supply,ghost" in r.notes


def test_btm_ppa_notes_which_of_several_ppas_it_used():
    n = build_edge_15min()
    a, b = {**PPA, "id": "a"}, {**PPA, "id": "b", "seller": "WindCo"}
    r = _build("btm_ppa", n, _commercial(contracts=[a, b]))
    assert "several_btm_ppas_first_used:a,b" in r.notes
    assert {"Solar BV": "developer"}.items() <= {p.id: p.role for p in
                                                  r.config.participants}.items()
    assert "WindCo" in r.config.externals


def test_btm_ppa_refuses_to_draft_over_a_ppa_the_site_does_not_buy():
    """Review #4: the site SELLS pv under a PPA — a second PPA on it would
    double-contract the same output."""
    sold = {**PPA, "seller": "site", "buyer": "Offtaker AG"}
    with pytest.raises(T.TemplateRefused) as exc:
        T.build("btm_ppa", build_edge_15min(), _commercial(contracts=[sold]))
    assert exc.value.code == "template_conflicting_ppa"


def test_landlord_tenant_uses_an_existing_lease():
    lease = {**LEASE, "lessor": "Estates Ltd", "asset_ids": ["pv", "bess"]}
    r = _build("landlord_tenant", build_edge_15min(), _commercial(contracts=[lease]))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"Estates Ltd": "landlord", "site": "tenant"}
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["pv"] == owners["bess"] == "Estates Ltd"
    assert owners["import"] == "site"            # the connection stays the tenant's
    assert "connection_costs_on_tenant" in r.notes
    assert r.draft_contracts == []


def test_landlord_owns_only_the_leased_assets():
    """Review #5: a lease on the battery alone leaves the PV with the tenant."""
    r = _build("landlord_tenant", build_edge_15min(), _commercial(contracts=[LEASE]))
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["bess"] == "Leasing GmbH" and owners["pv"] == "site"


def test_landlord_tenant_without_a_lease_drafts_one_with_no_payment():
    r = _build("landlord_tenant", build_edge_15min(), _commercial())
    (draft,) = r.draft_contracts
    assert (draft["type"], draft["lessor"], draft["lessee"]) == ("lease", "landlord", "site")
    assert set(draft["asset_ids"]) == {"pv", "bess"} and draft["annual_payment"] is None
    assert f"draft_needs:{draft['id']}:annual_payment" in r.notes


def test_dso_developer_drafts_a_dr_with_every_money_field_open():
    """Review #2: `contracted_mw` is part of the draft (null, like the prices)
    and DSR that is off on the load's bus is noted."""
    r = _build("dso_developer", build_edge_15min(), _commercial())
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "developer", "dso": "dso"}
    assert "dso" not in r.config.externals
    (draft,) = r.draft_contracts
    assert (draft["type"], draft["counterparty"], draft["load_ids"]) == \
        ("dr", "dso", ["site_load"])
    assert draft["contracted_mw"] is None and draft["availability_eur_per_mw_year"] is None
    assert (f"draft_needs:{draft['id']}:availability_eur_per_mw_year,"
            "activation_eur_per_mwh,contracted_mw") in r.notes
    assert "dsr_not_enabled:site" in r.notes
    on = T.build("dso_developer", build_edge_15min(), _commercial(), dsr_buses=("site",))
    assert not any(x.startswith("dsr_not_enabled") for x in on.notes)


def test_dso_developer_reuses_an_existing_dso_counterparty():
    dr = {"type": "dr", "id": "dr1", "availability_eur_per_mw_year": 1.0,
          "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"], "counterparty": "Netz AG"}
    r = _build("dso_developer", build_edge_15min(), _commercial(contracts=[dr]))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "developer", "Netz AG": "dso"}
    assert r.draft_contracts == []
    assert "dr_needs_contracted_mw:dr1" in r.notes


def test_dso_developer_never_makes_the_retailer_the_dso():
    """Review #6: a DR bought by the retailer leaves the retailer external
    (its tariff flows stay external); a DR with no counterparty is noted, not
    drafted over."""
    base = {"type": "dr", "availability_eur_per_mw_year": 1.0, "activation_eur_per_mwh": 1.0,
            "contracted_mw": 1.0, "load_ids": ["site_load"]}
    drs = [{**base, "id": "r", "counterparty": "retailer"}, {**base, "id": "x"}]
    r = _build("dso_developer", build_edge_15min(), _commercial(contracts=drs))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "developer", "dso": "dso"}
    assert "retailer" in r.config.externals
    assert "dr_counterparty_not_a_dso:r:retailer" in r.notes
    assert "dr_without_counterparty:x" in r.notes
    assert r.draft_contracts == []


def test_energy_hub_gives_each_member_the_assets_behind_its_link():
    n = _hub_network()
    commercial = _commercial(group_contract="hub", group_members=["import", "import2"],
                             group_cap_mw=60.0)
    r = _build("energy_hub", n, commercial)
    members = {m.link: m.participant for m in r.config.hub_members}
    assert set(members) == {"import", "import2"}
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["pv"] == members["import"] and owners["pv2"] == members["import2"]
    roles = {p.id: p.role for p in r.config.participants}
    assert roles["site"] == "site_owner"
    assert {roles[members[k]] for k in members} == {"hub_member"}
    assert r.config.allocation is not None and r.config.allocation.basis == "energy"


def test_energy_hub_member_ids_never_collide_with_the_site_party():
    commercial = _commercial(site_party="member_import", group_contract="hub",
                             group_members=["import", "import2"], group_cap_mw=60.0)
    r = _build("energy_hub", _hub_network(), commercial)
    members = {m.link: m.participant for m in r.config.hub_members}
    assert members["import"] == "member_import_2"


def test_energy_hub_needs_a_group_contract():
    with pytest.raises(T.TemplateRefused) as exc:
        T.build("energy_hub", build_edge_15min(), _commercial())
    assert exc.value.code == "template_needs_group_contract"


def test_an_unknown_template_is_refused():
    with pytest.raises(T.TemplateRefused) as exc:
        T.build("co_op", build_edge_15min(), _commercial())
    assert exc.value.code == "template_unknown"


# ── edited / stale / outdated disclosures ──────────────────────────────────


def test_an_untouched_template_is_neither_edited_nor_stale():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    assert T.template_status(r.config, n, _commercial()) == []


def test_editing_a_built_config_is_disclosed():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    edited = r.config.model_copy(update={"export_revenue_to": "asset_owner"})
    assert T.template_status(edited, n, _commercial()) == ["template_edited"]


def test_reordering_or_restating_a_default_is_not_an_edit():
    """Review #8: canonical order, defaults left out."""
    commercial = _commercial(contracts=[PPA])
    n = build_edge_15min()
    r = T.build("btm_ppa", n, commercial)
    raw = r.config.model_dump(mode="json")
    raw["participants"].reverse()
    raw["asset_owners"].reverse()
    raw["externals"].reverse()
    raw["tariff_payees"] = []
    assert T.template_status(ValueFlowConfig.model_validate(raw), n, commercial) == []


def test_a_changed_network_makes_the_template_stale():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    n.add("Generator", "pv_new", bus="site", carrier="solar", p_nom=1.0)
    assert T.template_status(r.config, n, _commercial()) == ["template_stale"]


def test_a_changed_contract_makes_the_template_stale_a_repricing_does_not():
    """Review #9: the builder reads parties and assets, not prices."""
    n = build_edge_15min()
    r = T.build("btm_ppa", n, _commercial(contracts=[PPA]))
    repriced = _commercial(contracts=[{**PPA, "price": 35.0}])
    assert T.template_status(r.config, n, repriced) == []
    moved = _commercial(contracts=[{**PPA, "seller": "SunCo"}])
    assert T.template_status(r.config, n, moved) == ["template_stale"]
    assert T.template_status(r.config, n, _commercial(contracts=[PPA],
                                                       poc_link="poc_site")) == \
        ["template_stale"]


def test_an_unsaved_draft_is_a_changed_input():
    n = build_edge_15min()
    r = T.build("btm_ppa", n, _commercial())
    assert T.template_status(r.config, n, _commercial()) == ["template_stale"]
    assert T.template_status(r.config, n, _saved(r, _commercial())) == []


def test_a_config_built_by_an_older_builder_is_outdated():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    old = r.config.model_copy(update={"template_version": "single_owner@1"})
    assert "template_outdated" in T.template_status(old, n, _commercial())


def test_a_custom_config_has_no_template_status():
    assert T.template_status(ValueFlowConfig(), build_edge_15min(), _commercial()) == []


# ── the route ──────────────────────────────────────────────────────────────


@pytest.fixture
def site(client, install_network):
    install_network(build_edge_15min(), name="tpl_site")
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": _commercial().model_dump(mode="json")})
    assert r.status_code == 200, r.text
    return client


def test_the_template_route_builds_without_saving(site):
    r = site.post("/api/simulation/value_flows/template", json={"template": "btm_ppa"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["template"] == "btm_ppa"
    assert body["draft_contracts"] and body["draft_contracts"][0]["type"] == "ppa"
    stored = site.get("/api/simulation/solver_config").json()["commercial"]
    assert stored["value_flows"] is None and stored["contracts"] == []


def test_an_unpriced_draft_cannot_be_saved(site):
    body = site.post("/api/simulation/value_flows/template",
                     json={"template": "btm_ppa"}).json()
    stored = site.get("/api/simulation/solver_config").json()["commercial"]
    stored.pop("value_flows", None)
    r = site.put("/api/simulation/solver_config",
                 json={"commercial": {**stored, "contracts": body["draft_contracts"]}})
    assert r.status_code == 422, r.text


def test_the_template_route_reads_the_enabled_dsr_buses(site):
    """Only buses the solve actually enables (price > 0, share > 0) count."""
    def notes():
        r = site.post("/api/simulation/value_flows/template", json={"template": "dso_developer"})
        assert r.status_code == 200, r.text
        return r.json()["notes"]

    assert "dsr_not_enabled:site" in notes()
    r = site.put("/api/simulation/solver_config", json={"dsr_buses": ["site"]})
    assert r.status_code == 200, r.text
    assert "dsr_not_enabled:site" in notes()                   # share and price still 0
    r = site.put("/api/simulation/solver_config",
                 json={"dsr_price_eur_per_mwh": 40.0, "dsr_share_of_load": 0.1})
    assert r.status_code == 200, r.text
    assert "dsr_not_enabled:site" not in notes()


def test_the_template_route_refusals(site, client, install_network):
    r = site.post("/api/simulation/value_flows/template", json={"template": "energy_hub"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "template_needs_group_contract"
    r = site.post("/api/simulation/value_flows/template", json={"template": "nope"})
    assert r.status_code == 422
    install_network(build_edge_15min(), name="tpl_bare")
    r = client.post("/api/simulation/value_flows/template", json={"template": "single_owner"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_commercial_config"


# ── end to end on V1, through the routes (review #1–#3) ────────────────────


def _v1_hub():
    from tests.test_value_flow_reconciliation import _network

    n = _network()
    n.add("Bus", "poc2", carrier="AC")
    n.add("Link", "import2", bus0="grid", bus1="poc2", p_nom=10.0, carrier="AC")
    n.add("Generator", "pv2", bus="poc2", carrier="solar", p_nom=3.0, capital_cost=1000.0)
    n.add("Load", "load2", bus="poc2", p_set=1.0)
    return n


def _v1(contracts, **extra):
    from tests.test_value_flow_reconciliation import _commercial as v1

    out = v1(vf=None, contracts=contracts)
    out.pop("value_flows")
    out.update(extra)
    return out


_DSR = {"dsr_price_eur_per_mwh": 40.0, "dsr_share_of_load": 0.1, "dsr_buses": ["site"]}
_HUB = {"group_contract": "hub", "group_members": ["import", "import2"],
        "group_cap_mw": 60.0}
E2E = {   # case: (template, contracts, hub network, solver patch, drafts expected)
    "single_owner": ("single_owner", [PPA, LEASE], False, None, 0),
    "btm_ppa": ("btm_ppa", [PPA, LEASE], False, None, 0),
    "btm_ppa_drafted": ("btm_ppa", [LEASE], False, None, 1),
    "landlord_tenant": ("landlord_tenant", [PPA, LEASE], False, None, 0),
    "landlord_tenant_drafted": ("landlord_tenant", [PPA], False, None, 1),
    "dso_developer": ("dso_developer", [PPA, LEASE], False, _DSR, 1),
    "energy_hub": ("energy_hub", [PPA, LEASE], True, None, 0),
}


@pytest.mark.live_solve
@pytest.mark.parametrize("case", list(E2E))
def test_a_template_saved_through_the_routes_solves_and_closes_the_ledger(
        case, client, install_network, session_ctx):
    """POST template → price and save the drafts → PUT value_flows (If-Match)
    → solve on the session → the ledger closes (conservation ok) and the
    stored config reads as neither edited nor stale."""
    import routers.results as R
    import routers.simulation as sim_router
    from services.results.value_flows import value_flow_ledger
    from services.solver_service import run_simulation
    from tests.test_value_flow_reconciliation import _network

    name, contracts, hub, solver, n_drafts = E2E[case]
    n = _v1_hub() if hub else _network()
    install_network(n, name=f"tpl_e2e_{case}")
    commercial = _v1(contracts, **(_HUB if hub else {}))
    if hub:
        commercial.pop("connection")
    price = n.links_t["ic_export_price"]["export"]
    ref = client.post("/api/library/series", json={
        "name": f"px_{case}", "timestamps": [t.isoformat() for t in n.snapshots],
        "values": [float(v) for v in price], "meta": {"source": "test"}})
    assert ref.status_code in (200, 201), ref.text
    commercial["export_price_ref"] = ref.json()
    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})
    assert r.status_code == 200, r.text
    if solver:
        r = client.put("/api/simulation/solver_config", json=solver)
        assert r.status_code == 200, r.text

    r = client.post("/api/simulation/value_flows/template", json={"template": name})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["draft_contracts"]) == n_drafts, body
    assert not any(x.startswith("dsr_not_enabled") for x in body["notes"])
    if body["draft_contracts"]:
        stored = copy.deepcopy(client.get("/api/simulation/solver_config").json()["commercial"])
        stored.pop("value_flows", None)
        stored["contracts"] = [*stored["contracts"], *_priced(body["draft_contracts"])]
        r = client.put("/api/simulation/solver_config", json={"commercial": stored})
        assert r.status_code == 200, r.text
    tag = client.get("/api/simulation/commercial/value_flows").json()["digest"]
    r = client.put("/api/simulation/commercial/value_flows",
                   json={"value_flows": body["config"]}, headers={"If-Match": tag})
    assert r.status_code == 200, r.text

    ctx = session_ctx(client)
    cfg = ctx.solver_state["solver_config"]
    vf = ValueFlowConfig.model_validate(cfg.commercial["value_flows"])
    assert T.template_status(vf, ctx.network, CommercialConfig.model_validate(
        cfg.commercial)) == []
    status, cond = run_simulation(cfg, ctx.network, ctx.mutation_lock, threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal"), (status, cond)
    sim_router._state["solver_config"] = cfg
    got = value_flow_ledger(ctx.network, cfg, result_df=R._result_df)
    assert got is not None
    _inputs, _vf, ledger, res = got
    bad = {p: [c for c in res.periods[p].checks if c["ok"] is not True] for p in res.periods}
    assert res.ok is True, bad
    if n_drafts:
        drafted = {ln.contract_id for p in ledger.periods for ln in ledger.periods[p]
                   if ln.contract_id}
        assert body["draft_contracts"][0]["id"] in drafted
