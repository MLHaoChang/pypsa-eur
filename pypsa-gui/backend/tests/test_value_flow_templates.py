"""
The five value-flow templates of spec §7 (Edge Investment Case P3 WP3.2).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.2.
A builder reads the network and the commercial config and returns a
`TemplateResult(config, draft_contracts, notes)`; nothing is saved. It never
changes `site_party` (the participant with that id gets the template's role),
never invents a saved contract (a missing one is an unsaved draft), and pins its
`template_version`; `built_digest` / `built_assets_digest` let the ledger say
`template_edited` / `template_stale`. A template config, with its drafts saved,
passes the value-flows route's party checks.
"""
from __future__ import annotations

import inspect
import json
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


def _saved(result, commercial: CommercialConfig) -> CommercialConfig:
    """The commercial config with the template's drafts confirmed."""
    raw = commercial.model_dump(mode="json")
    raw["contracts"] = [*raw["contracts"], *result.draft_contracts]
    return CommercialConfig.model_validate(raw)


def _build(name, n, commercial):
    result = T.build(name, n, commercial)
    assert result.config.template == name
    assert result.config.template_version == f"{name}@{T.TEMPLATES[name].version}"
    saved = _saved(result, commercial)
    assert P.value_flows_problems(result.config, saved, n) == [], result.config
    return result


# ── the registry is pinned ─────────────────────────────────────────────────


def test_every_template_of_the_spec_is_registered():
    assert set(T.TEMPLATES) == {"single_owner", "btm_ppa", "landlord_tenant",
                                "dso_developer", "energy_hub"}


def test_a_builder_change_needs_a_version_bump():
    """The fixture pins each builder's version and source hash: editing a
    builder without bumping its version fails here."""
    pinned = json.loads(FIXTURE.read_text())
    for name, tpl in T.TEMPLATES.items():
        assert pinned[name]["version"] == tpl.version, name
        assert pinned[name]["source_sha"] == T.source_sha(tpl.builder), (
            f"{name}: builder changed — bump its version and the fixture")


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


def test_btm_ppa_with_its_ppa_gives_the_developer_only_the_ppa_assets():
    ppa = {"type": "ppa", "id": "ppa1", "kind": "as_consumed_btm", "price": 60.0,
           "tenor_years": 15, "seller": "SunCo", "buyer": "site", "asset_ids": ["pv"]}
    r = _build("btm_ppa", build_edge_15min(), _commercial(contracts=[ppa]))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "offtaker", "SunCo": "developer"}
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["pv"] == "SunCo" and owners["bess"] == "site"
    assert r.config.export_revenue_to == "asset_owner"
    assert r.draft_contracts == []


def test_btm_ppa_without_a_ppa_returns_a_draft_not_a_saved_contract():
    commercial = _commercial()
    r = _build("btm_ppa", build_edge_15min(), commercial)
    (draft,) = r.draft_contracts
    assert draft["type"] == "ppa" and draft["kind"] == "as_consumed_btm"
    assert draft["seller"] == "developer" and draft["buyer"] == "site"
    assert draft["asset_ids"] == ["pv"]
    assert commercial.contracts == []                       # nothing was saved
    assert any(n.startswith("draft_needs_price:") for n in r.notes)


def test_landlord_tenant_uses_an_existing_lease():
    lease = {"type": "lease", "id": "l1", "lessor": "Estates Ltd", "lessee": "site",
             "annual_payment": 50_000.0, "tenor_years": 10, "asset_ids": ["pv", "bess"]}
    r = _build("landlord_tenant", build_edge_15min(), _commercial(contracts=[lease]))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"Estates Ltd": "landlord", "site": "tenant"}
    owners = {o.asset_id: o.owner for o in r.config.asset_owners}
    assert owners["pv"] == owners["bess"] == "Estates Ltd"
    assert owners["import"] == "site"            # the connection stays the tenant's
    assert r.draft_contracts == []


def test_landlord_tenant_without_a_lease_drafts_one():
    r = _build("landlord_tenant", build_edge_15min(), _commercial())
    (draft,) = r.draft_contracts
    assert (draft["type"], draft["lessor"], draft["lessee"]) == ("lease", "landlord", "site")
    assert set(draft["asset_ids"]) == {"pv", "bess"}


def test_dso_developer_makes_the_dso_a_participant():
    r = _build("dso_developer", build_edge_15min(), _commercial())
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "developer", "dso": "dso"}
    assert "dso" not in r.config.externals
    (draft,) = r.draft_contracts
    assert (draft["type"], draft["counterparty"], draft["load_ids"]) == \
        ("dr", "dso", ["site_load"])


def test_dso_developer_reuses_an_existing_dr_counterparty():
    dr = {"type": "dr", "id": "dr1", "availability_eur_per_mw_year": 1.0,
          "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"], "counterparty": "Netz AG"}
    r = _build("dso_developer", build_edge_15min(), _commercial(contracts=[dr]))
    roles = {p.id: p.role for p in r.config.participants}
    assert roles == {"site": "developer", "Netz AG": "dso"}
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


def test_energy_hub_needs_a_group_contract():
    with pytest.raises(T.TemplateRefused) as exc:
        T.build("energy_hub", build_edge_15min(), _commercial())
    assert exc.value.code == "template_needs_group_contract"


def test_an_unknown_template_is_refused():
    with pytest.raises(T.TemplateRefused) as exc:
        T.build("co_op", build_edge_15min(), _commercial())
    assert exc.value.code == "template_unknown"


# ── edited / stale disclosures ─────────────────────────────────────────────


def test_an_untouched_template_is_neither_edited_nor_stale():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    assert T.template_status(r.config, n, _commercial()) == []


def test_editing_a_built_config_is_disclosed():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    edited = r.config.model_copy(update={"export_revenue_to": "asset_owner"})
    assert T.template_status(edited, n, _commercial()) == ["template_edited"]


def test_a_changed_network_makes_the_template_stale():
    n = build_edge_15min()
    r = T.build("single_owner", n, _commercial())
    n.add("Generator", "pv_new", bus="site", carrier="solar", p_nom=1.0)
    assert T.template_status(r.config, n, _commercial()) == ["template_stale"]


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


def test_the_template_route_refusals(site, client, install_network):
    r = site.post("/api/simulation/value_flows/template", json={"template": "energy_hub"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "template_needs_group_contract"
    r = site.post("/api/simulation/value_flows/template", json={"template": "nope"})
    assert r.status_code == 422
    install_network(build_edge_15min(), name="tpl_bare")
    r = client.post("/api/simulation/value_flows/template", json={"template": "single_owner"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_commercial_config"


def test_source_sha_is_stable():
    fn = T.TEMPLATES["single_owner"].builder
    assert T.source_sha(fn) == T.source_sha(fn)
    assert len(T.source_sha(fn)) == 16
    assert inspect.getsource(fn)
