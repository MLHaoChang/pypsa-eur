"""
The value-flow config: model, validation, route, hashing (Edge Investment Case
P3 WP3.0).

Plan: docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md WP3.0.
`CommercialConfig.value_flows` is stored RAW and validated lazily into
`ValueFlowConfig` (a stored bad value never fails a solve); parties are checked
at the dedicated route (`PUT /api/simulation/commercial/value_flows`), never in a
model validator. `PUT /solver_config` keeps the stored value when its body omits
the key, accepts an unchanged echo and refuses a change. No committed commercial
hash and no AdequacyReport `assumptions_hash` reads the value-flow config
(decision 9: participant splits never touch the LP).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from models.commercial import CommercialConfig, ValueFlowConfig
from tests.fixtures.investment_case.edge_15min import build_edge_15min

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "investment_case"
TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh",
       "periods": [{"name": "all", "rate": 0.2}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 10.0}]}
TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [TOU, DEMAND]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 55.0,
       "tenor_years": 10, "seller": "developer", "buyer": "site", "asset_ids": ["pv"]}
VF = {"template": "btm_ppa",
      "participants": [{"id": "site", "name": "Site", "role": "offtaker"},
                       {"id": "developer", "name": "Dev Co", "role": "developer"}],
      "asset_owners": [{"asset_id": "pv", "component": "Generator", "owner": "developer"}],
      "tariff_payees": [{"kind": "demand", "payee": "dso"}]}


def _commercial(vf=None, contracts=(PPA,), **extra):
    out = {"poc_link": "import", "import_tariff": TARIFF, "contracts": list(contracts), **extra}
    if vf is not None:
        out["value_flows"] = vf
    return out


# ── model ──────────────────────────────────────────────────────────────────


def test_the_value_flow_config_round_trips_with_its_defaults():
    vf = ValueFlowConfig.model_validate(VF)
    assert ValueFlowConfig.model_validate(vf.model_dump(mode="json")) == vf
    assert vf.externals == ["retailer", "dso", "tso", "market", "tax_authority",
                            "capex_supplier", "om_contractor"]
    assert vf.export_revenue_to == "site_party"
    assert ValueFlowConfig().template == "custom"


def test_a_tariff_payee_rule_names_a_kind_or_an_item():
    with pytest.raises(ValidationError):
        ValueFlowConfig.model_validate({"tariff_payees": [{"payee": "dso"}]})


def test_the_commercial_config_stores_value_flows_raw_so_a_bad_value_never_fails_it():
    """C8: `_lp._parse` runs on every solve and in cost_rows / billing / gap."""
    bad = {"participants": [{"id": "x"}], "allocation": {"basis": "fixed_shares",
                                                         "shares": {"a": 0.2}}}
    cfg = CommercialConfig.model_validate(_commercial(bad))
    assert cfg.value_flows == bad


def test_an_earlier_config_validates_and_its_recipe_1_form_is_unchanged():
    """A P0-era payload validates, and the new field is invisible to recipe 1
    at its default, so a recipe-1 hash stored before P3 still compares equal."""
    from services.commercial import hashing as H

    raw = json.loads((FIXTURES / "commercial_config_minimal.json").read_text())
    cfg = CommercialConfig.model_validate(raw)
    assert cfg.value_flows is None
    assert "value_flows" not in H.canonical(cfg, version=1)
    assert "value_flows" not in H.canonical(cfg, version=2)
    with_vf = CommercialConfig.model_validate({**raw, "value_flows": VF})
    assert "value_flows" in H.canonical(with_vf, version=1)   # a set value is not hidden


# ── lazy parsing and party validation ──────────────────────────────────────


def _problems(vf, **extra):
    from services.commercial import participants as P

    commercial = CommercialConfig.model_validate(_commercial(vf, **extra))
    return P.value_flows_problems(P.parse_value_flows(vf), commercial, build_edge_15min())


def test_parse_value_flows_answers_a_named_error_for_a_bad_stored_value():
    from services.commercial import participants as P

    with pytest.raises(P.ValueFlowsInvalid):
        P.parse_value_flows({"participants": [{"id": "x"}]})
    assert P.parse_value_flows(None) is None
    assert P.parse_value_flows(VF).template == "btm_ppa"


def test_a_valid_config_has_no_problems():
    assert _problems(VF) == []


def test_parties_compare_trimmed_and_case_insensitive():
    vf = copy.deepcopy(VF)
    vf["participants"][1]["id"] = " Developer "
    assert _problems(vf) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda v: v["participants"].append({"id": "SITE", "name": "dup", "role": "other"}),
     "participant ids must be unique"),
    (lambda v: v["participants"].append({"id": "market", "name": "m", "role": "other"}),
     "also an external"),
    (lambda v: v.update(participants=[v["participants"][1]]), "site_party 'site'"),
    (lambda v: v["asset_owners"][0].update(owner="developr"), "developr"),
    (lambda v: v["asset_owners"][0].update(owner="market"), "owner must be a participant"),
    (lambda v: v["asset_owners"][0].update(asset_id="ghost"), "ghost"),
    (lambda v: v["asset_owners"].append(dict(v["asset_owners"][0])), "owned twice"),
    (lambda v: v["tariff_payees"][0].update(payee="nobody"), "nobody"),
    (lambda v: v.update(hub_members=[{"link": "import", "participant": "site"}]),
     "group contract"),
    (lambda v: v.update(allocation={"basis": "energy"}), "group contract"),
])
def test_party_problems_are_named(mutate, needle):
    vf = copy.deepcopy(VF)
    mutate(vf)
    problems = _problems(vf)
    assert any(needle in p for p in problems), problems


def test_a_contract_party_that_is_neither_participant_nor_external_is_named():
    problems = _problems(VF, contracts=({**PPA, "seller": "Devloper"},))
    assert any("Devloper" in p and "ppa1" in p for p in problems), problems


def test_a_contract_party_left_none_is_not_a_config_problem():
    """P2's rule: a `None` CfD owner / DR counterparty is `party_not_established`
    at settlement (and in the ledger), never defaulted — not a refusal here."""
    cfd = {"type": "cfd", "id": "c", "strike": 50.0, "tenor_years": 5, "asset_ids": ["pv"]}
    assert _problems(VF, contracts=(PPA, cfd)) == []


def test_hub_members_need_hub_participants_and_group_links():
    vf = {**copy.deepcopy(VF), "template": "energy_hub",
          "hub_members": [{"link": "ghost", "participant": "developer"}],
          "allocation": {"basis": "fixed_shares", "shares": {"developer": 0.5, "x": 0.5}}}
    problems = _problems(vf, group_contract="g", group_members=["import"], group_cap_mw=5.0)
    assert any("ghost" in p for p in problems), problems
    assert any("fixed_shares" in p for p in problems), problems


# ── hashes: participant edits never touch the LP ───────────────────────────


def _hashes(commercial: dict):
    from services.adequacy.report import _config_hash
    from services.commercial import lp_bindings as L
    from services.commercial import settlement_inputs as SI
    from services.commercial.connection import agreement_hash
    from services.solver_service import SolverConfig

    n = build_edge_15min()
    cfg = CommercialConfig.model_validate(commercial)
    items = [i for i in cfg.import_tariff.items if i.kind == "demand"]
    solver_cfg = SolverConfig(commercial=cfg.model_dump(mode="json"))
    return {
        "tiers": L.tier_items_hash(cfg), "energy": L.energy_hash(n, cfg),
        "demand": L.demand_hash(n, cfg, items), "capacity": L.capacity_items_hash(cfg),
        "ppa": L.ppa_dispatch_hash(cfg), "contracts": SI.contracts_record(cfg)["hash"],
        "agreement": agreement_hash(cfg.connection), "adequacy": _config_hash(solver_cfg),
    }


def test_a_value_flow_edit_changes_no_committed_hash_and_no_adequacy_hash():
    base = _hashes(_commercial())
    assert _hashes(_commercial(VF)) == base
    other = {**copy.deepcopy(VF), "export_revenue_to": "asset_owner"}
    assert _hashes(_commercial(other)) == base


def test_the_new_field_is_registered_for_recipe_1():
    from services.commercial import hashing as H

    assert H.FIELDS_AFTER_V1[("CommercialConfig", "value_flows")] is None


# ── the routes ─────────────────────────────────────────────────────────────


@pytest.fixture
def site(client, install_network):
    install_network(build_edge_15min(), name="vf_site")
    r = client.put("/api/simulation/solver_config", json={"commercial": _commercial()})
    assert r.status_code == 200, r.text
    return client


def _get(client):
    r = client.get("/api/simulation/commercial/value_flows")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_value_flows_route_saves_and_answers_a_digest(site):
    empty = _get(site)
    assert empty["value_flows"] is None
    r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF},
                 headers={"If-Match": empty["digest"]})
    assert r.status_code == 200, r.text
    got = _get(site)
    assert got["value_flows"]["template"] == "btm_ppa"
    assert got["digest"] != empty["digest"]
    stored = site.get("/api/simulation/solver_config").json()["commercial"]
    assert stored["value_flows"]["participants"][1]["id"] == "developer"
    assert stored["import_tariff"]["id"] == "t"            # the rest untouched


def test_a_stale_if_match_is_412(site):
    r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF},
                 headers={"If-Match": "0" * 16})
    assert r.status_code == 412, r.text
    assert r.json()["detail"]["code"] == "value_flows_changed"


def test_party_problems_are_422_naming_them(site):
    bad = copy.deepcopy(VF)
    bad["asset_owners"][0]["owner"] = "developr"
    r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": bad})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "value_flows_invalid"
    assert "developr" in r.json()["detail"]["message"]
    r = site.put("/api/simulation/commercial/value_flows",
                 json={"value_flows": {"participants": [{"id": "x"}]}})
    assert r.status_code == 422


def test_the_value_flows_route_needs_a_commercial_config(client, install_network):
    install_network(build_edge_15min(), name="vf_bare")
    r = client.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_commercial_config"


def test_the_value_flows_route_refuses_during_a_solve(site, monkeypatch):
    import routers.simulation as S

    monkeypatch.setattr(S, "_solver_in_flight_ctx", lambda ctx: True)
    r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "solver_in_flight"


def test_clearing_value_flows(site):
    site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": None})
    assert r.status_code == 200
    assert _get(site)["value_flows"] is None


def test_solver_config_keeps_value_flows_its_body_omits(site):
    """C7 / D2: a tariff save after a value-flows edit keeps the edit."""
    site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    new_tariff = {**TARIFF, "items": [{**TOU, "periods": [{"name": "all", "rate": 0.3}]}]}
    r = site.put("/api/simulation/solver_config",
                 json={"commercial": _commercial(import_tariff=new_tariff)})
    assert r.status_code == 200, r.text
    stored = site.get("/api/simulation/solver_config").json()["commercial"]
    assert stored["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.3
    assert stored["value_flows"]["template"] == "btm_ppa"


def test_solver_config_accepts_an_unchanged_echo_and_refuses_a_change(site):
    site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    echo = site.get("/api/simulation/solver_config").json()["commercial"]
    r = site.put("/api/simulation/solver_config", json={"commercial": echo})
    assert r.status_code == 200, r.text
    changed = {**echo, "value_flows": {**echo["value_flows"], "template": "custom"}}
    r = site.put("/api/simulation/solver_config", json={"commercial": changed})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "value_flows_via_dedicated_route"
    r = site.put("/api/simulation/solver_config", json={"commercial": {**echo, "value_flows": None}})
    assert r.status_code == 422
    assert _get(site)["value_flows"]["template"] == "btm_ppa"


def test_an_explicit_null_with_nothing_stored_is_a_no_op(site):
    r = site.put("/api/simulation/solver_config",
                 json={"commercial": {**_commercial(), "value_flows": None}})
    assert r.status_code == 200, r.text


def test_a_library_tariff_ref_config_keeps_value_flows(site):
    """D2: `resolve_tariff_ref` re-validates from a dump (every field reads as
    set), so the omission is read before binding."""
    r = site.put("/api/library/items/tariff/vf_tariff",
                 json={"payload": TARIFF, "meta": {"source": "test"}})
    assert r.status_code in (200, 201), r.text
    ref = r.json()
    site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    body = {"poc_link": "import", "import_tariff_ref": ref, "contracts": [PPA]}
    r = site.put("/api/simulation/solver_config", json={"commercial": body})
    assert r.status_code == 200, r.text
    assert _get(site)["value_flows"]["template"] == "btm_ppa"


def test_a_corrupted_stored_value_still_solves_the_commercial_layer(site, session_ctx):
    """C8: a stored value that no longer validates is data, not a solve failure."""
    from services.commercial import lp_bindings as L

    ctx = session_ctx(site)
    commercial = dict(ctx.solver_state["solver_config"].commercial)
    commercial["value_flows"] = {"participants": "not a list"}
    ctx.solver_state["solver_config"].commercial = commercial
    cfg = L._parse(ctx.solver_state["solver_config"].commercial)
    assert cfg is not None and cfg.poc_link == "import"
    r = site.get("/api/simulation/commercial/value_flows")
    assert r.status_code == 200
    assert r.json()["status"] == "value_flows_invalid"


# ── WP3.0 review round 1 ───────────────────────────────────────────────────


@pytest.mark.parametrize("body", [VF, {}, {"value_flows": VF, "extra": 1}])
def test_a_misshaped_body_is_422_never_a_silent_clear(site, body):
    """#1: an unwrapped config or an empty body used to default to `null` and
    clear the stored value."""
    site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    r = site.put("/api/simulation/commercial/value_flows", json=body)
    assert r.status_code == 422, r.text
    assert _get(site)["value_flows"]["template"] == "btm_ppa"


def test_a_quoted_or_weak_if_match_is_the_same_token(site):
    """#6: HTTP clients may quote an entity tag."""
    d = _get(site)["digest"]
    for header in (f'"{d}"', f'W/"{d}"'):
        r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": None},
                     headers={"If-Match": header})
        assert r.status_code == 200, (header, r.text)


def test_a_stored_commercial_config_that_no_longer_validates_is_409_not_500(site, session_ctx):
    """#4: the load path does not re-validate a stored config."""
    ctx = session_ctx(site)
    commercial = dict(ctx.solver_state["solver_config"].commercial)
    commercial["poc_link"] = ""
    ctx.solver_state["solver_config"].commercial = commercial
    r = site.put("/api/simulation/commercial/value_flows", json={"value_flows": VF})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "commercial_config_invalid"


@pytest.mark.parametrize("mutate,needle", [
    (lambda v: v["participants"].append({"id": "  ", "name": "blank", "role": "other"}),
     "empty"),
    (lambda v: v.update(externals=["", "dso"]), "empty"),
    (lambda v: v.update(externals=["dso", "DSO"]), "externals must be unique"),
    (lambda v: v.update(tariff_payees=[{"item_id": "demnd", "payee": "dso"}]), "demnd"),
])
def test_further_party_problems_are_named(mutate, needle):
    """#5."""
    vf = copy.deepcopy(VF)
    mutate(vf)
    problems = _problems(vf)
    assert any(needle in p for p in problems), problems


def test_hub_members_are_unique_and_contracted_capacity_needs_every_member_mw():
    hub = {"template": "energy_hub",
           "participants": [{"id": "site", "name": "Hub", "role": "site_owner"},
                            {"id": "a", "name": "A", "role": "hub_member"}],
           "hub_members": [{"link": "import", "participant": "a"},
                           {"link": "import", "participant": "a"}],
           "allocation": {"basis": "contracted_capacity"}}
    problems = _problems(hub, contracts=(), group_contract="g", group_members=["import"],
                         group_cap_mw=5.0)
    assert any("twice" in p for p in problems), problems
    assert any("contracted_mw" in p for p in problems), problems


# `commercial_config_minimal.json` carries only an `import_tariff_id` label, which the
# binding refuses on a network (P2 WP2.4a); it is covered at the model level above.
@pytest.mark.parametrize("commercial", [
    _commercial(),
    _commercial(contracts=(PPA, {"type": "lease", "id": "l", "lessor": "a", "lessee": "site",
                                 "annual_payment": 1000.0, "tenor_years": 5,
                                 "asset_ids": ["bess"]})),
    _commercial(group_contract="g", group_members=["import"], group_cap_mw=5.0),
], ids=["tariff_ppa", "lease", "group"])
def test_p1_and_p2_configs_round_trip_through_the_route_unchanged(client, install_network,
                                                                  commercial):
    """#2: every earlier shape binds and comes back as it went in, with only
    `value_flows: null` added."""
    install_network(build_edge_15min(), name="vf_rt")
    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})
    assert r.status_code == 200, r.text
    back = client.get("/api/simulation/solver_config").json()["commercial"]
    assert back["value_flows"] is None
    want = CommercialConfig.model_validate(commercial).model_dump(mode="json")
    assert {k: v for k, v in back.items() if k != "value_flows"} == \
        {k: v for k, v in want.items() if k != "value_flows"}


@pytest.mark.live_solve
@pytest.mark.parametrize("garbage", ["garbage", {"participants": "x"}, [1, 2]])
def test_a_corrupted_value_flows_still_solves_bills_and_reconciles(reset_backend, garbage):
    """#3 (C8): a stored value of any shape is data, never a solve failure."""
    import routers.results as R
    from tests.test_results_billing import _commercial as billed, _solved

    commercial = {**billed(), "value_flows": garbage}
    n, cfg = _solved(commercial)
    out = R.get_billing()
    assert out["gap"]["gates"] == []
    for kind, v in out["gap"]["periods"]["_"].items():
        assert v["unattributed_pct"] is not None and v["unattributed_pct"] < 1e-6, kind


@pytest.mark.live_solve
def test_a_value_flows_edit_after_a_solve_raises_no_drift(reset_backend):
    """#8: the plan's 'no config_changed_since_solve' — solve, edit the
    participants through the route, and read every commercial surface."""
    import routers.results as R
    import routers.simulation as S
    from services.results.cost_breakdown import compute_cost_breakdown
    from tests.test_results_billing import _commercial as billed, _solved

    n, cfg = _solved(billed())
    before = json.dumps(R.get_billing(), sort_keys=True, default=str)
    assert "config_changed_since_solve" not in before
    vf = {"participants": [{"id": "site", "name": "Site", "role": "site_owner"},
                           {"id": "Solar BV", "name": "Solar", "role": "developer"},
                           {"id": "Leasing GmbH", "name": "L", "role": "landlord"}]}
    S.put_value_flows(S.ValueFlowsIn(value_flows=vf), if_match=None)
    cfg2 = S._state["solver_config"]
    assert cfg2.commercial["value_flows"]["participants"][1]["id"] == "Solar BV"
    after = R.get_billing()
    text = json.dumps(after, sort_keys=True, default=str)
    assert "config_changed_since_solve" not in text and "lp_recipe_changed" not in text
    rows = compute_cost_breakdown(S.PyPSAService.get_network(), cfg2)["commercial"]
    assert "config_changed_since_solve" not in json.dumps(rows, default=str)
