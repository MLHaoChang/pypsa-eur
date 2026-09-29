"""
Contracts on the config: round trips and the double-count preflight (Edge
Investment Case P2 WP2.2c).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2c.
Contracts survive `PUT /solver_config`, save → load and a bundle export →
import (their reference series pinned). Preflight: `commercial.contract_asset_missing`
(error — an asset or load a contract names that the network cannot settle);
`commercial.ppa_export_double_count` (warning — the site SELLS a PPA's output
that also earns the export price); `commercial.dr_double_count` (warning — a DR
contract's load sits on a DSR bus whose slack the LP already pays);
`commercial.sleeved_commodity_double_count` (warning — a sleeved PPA and an
import-tariff energy item both buy the commodity). A contract changed after the
solve is drift on the settlement (`contracts_state`). Closes P1 WP1.8's
recorded deviation.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from models.commercial import CommercialConfig
from services.commercial import settlement_inputs as SI
from services.commercial.preflight import commercial_findings
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh",
       "periods": [{"name": "all", "rate": 0.2}]}
TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [TOU]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 55.0,
       "tenor_years": 10, "seller": "Wind BV", "buyer": "site", "asset_ids": ["pv"]}


def _codes(n, commercial, **kw):
    return {(sev, code) for sev, code, *_ in commercial_findings(n, commercial, **kw)}


def _cfg(*contracts, **extra):
    return {"poc_link": "import", "import_tariff": TARIFF, "contracts": list(contracts), **extra}


# ── contract_asset_missing ─────────────────────────────────────────────────


@pytest.mark.parametrize("contract", [
    {**PPA, "asset_ids": ["ghost"]},
    {**PPA, "asset_ids": ["bess"]},                                   # not a Generator
    {"type": "cfd", "id": "c", "strike": 70.0, "tenor_years": 5, "asset_ids": ["bess"]},
    {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0, "activation_eur_per_mwh": 1.0,
     "load_ids": ["nope"]},
    {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0, "activation_eur_per_mwh": 1.0,
     "asset_ids": ["bess"]},                                          # DR on assets: P5
    {"type": "lease", "id": "l", "lessor": "a", "lessee": "b", "annual_payment": 1.0,
     "tenor_years": 5, "asset_ids": ["ghost"]},
    {"type": "eaas", "id": "e", "provider": "a", "customer": "b", "fee_eur_per_mwh": 1.0,
     "tenor_years": 5, "asset_ids": ["site_load"]},                  # a load delivers nothing
])
def test_a_contract_naming_what_the_network_cannot_settle_is_an_error(contract):
    assert ("error", "commercial.contract_asset_missing") in _codes(build_edge_15min(),
                                                                     _cfg(contract))


def test_contracts_on_the_right_components_pass():
    ok = [PPA,
          {"type": "cfd", "id": "c", "strike": 70.0, "tenor_years": 5, "asset_ids": ["pv"]},
          {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0,
           "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"]},
          {"type": "lease", "id": "l", "lessor": "a", "lessee": "b", "annual_payment": 1.0,
           "tenor_years": 5, "asset_ids": ["bess"]},
          {"type": "eaas", "id": "e", "provider": "a", "customer": "b", "fee_eur_per_mwh": 1.0,
           "tenor_years": 5, "asset_ids": ["pv", "bess", "poc_site"]},
          {"type": "retail", "id": "r", "retailer": "a", "customer": "site", "tariff_id": "t",
           "tenor_years": 1}]
    assert not [c for c in _codes(build_edge_15min(), _cfg(*ok)) if c[0] == "error"]


def test_a_retail_contract_naming_another_tariff_is_an_error():
    retail = {"type": "retail", "id": "r", "retailer": "a", "customer": "site",
              "tariff_id": "other", "tenor_years": 1}
    assert ("error", "commercial.contract_tariff_mismatch") in _codes(build_edge_15min(),
                                                                       _cfg(retail))


# ── double-count warnings ──────────────────────────────────────────────────


def _exporting():
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=50.0, carrier="AC")
    return n


def test_a_ppa_the_site_sells_while_exporting_is_a_double_count_warning():
    n = _exporting()
    sold = {**PPA, "seller": "site", "buyer": "Offtaker"}
    assert ("warning", "commercial.ppa_export_double_count") in _codes(
        n, _cfg(sold, export_link="export"))
    # The buyer case: exporting surplus is correct, no warning.
    assert ("warning", "commercial.ppa_export_double_count") not in _codes(
        n, _cfg(PPA, export_link="export"))
    # changes_dispatch: the LP carries the PPA (WP2.2d), no double count here.
    assert ("warning", "commercial.ppa_export_double_count") not in _codes(
        n, _cfg({**sold, "changes_dispatch": True}, export_link="export"))
    # Nothing is exported: no warning.
    assert ("warning", "commercial.ppa_export_double_count") not in _codes(
        build_edge_15min(), _cfg(sold))


def test_the_site_party_names_who_the_site_is():
    n = _exporting()
    sold = {**PPA, "seller": "Hub BV", "buyer": "Offtaker"}
    assert ("warning", "commercial.ppa_export_double_count") in _codes(
        n, _cfg(sold, export_link="export", site_party="Hub BV"))


def test_a_dr_contract_on_a_dsr_bus_is_a_double_count_warning():
    dr = {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0,
          "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"]}
    n = build_edge_15min()
    assert ("warning", "commercial.dr_double_count") in _codes(n, _cfg(dr), dsr_buses=["site"])
    assert ("warning", "commercial.dr_double_count") not in _codes(n, _cfg(dr),
                                                                    dsr_buses=["grid"])


def test_a_sleeved_ppa_with_a_commodity_item_is_a_double_count_warning():
    sleeved = {**PPA, "kind": "sleeved", "sleeving_fee_eur_per_mwh": 2.0}
    n = build_edge_15min()
    assert ("warning", "commercial.sleeved_commodity_double_count") in _codes(n, _cfg(sleeved))
    fixed_only = {**TARIFF, "items": [{"id": "standing", "kind": "fixed", "unit": "per_month",
                                       "periods": [{"name": "all", "rate": 10.0}]}]}
    assert ("warning", "commercial.sleeved_commodity_double_count") not in _codes(
        n, {**_cfg(sleeved), "import_tariff": fixed_only})


def test_validation_reports_the_contract_codes_with_the_solver_configs_dsr_buses():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    dr = {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0,
          "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"]}
    cfg = SolverConfig(commercial=_cfg(dr), dsr_price_eur_per_mwh=100.0, dsr_share_of_load=0.1,
                       dsr_buses=["site"])
    codes = {i.code for i in validate_for_run(build_edge_15min(), cfg)}
    assert "commercial.dr_double_count" in codes


# ── the settlement's drift record ──────────────────────────────────────────


def test_a_contract_changed_after_the_solve_is_drift():
    n = build_edge_15min()
    cfg = CommercialConfig.model_validate(_cfg(PPA))
    n.meta[SI.META_CONTRACTS] = SI.contracts_record(cfg)
    assert SI.contracts_state(n, cfg) is None
    edited = CommercialConfig.model_validate(_cfg({**PPA, "price": 60.0}))
    assert SI.contracts_state(n, edited) == "config"
    assert SI.contracts_state(build_edge_15min(), edited) == "not_recorded"


@pytest.mark.live_solve
def test_a_solve_records_the_contracts_it_ran_with():
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = build_edge_15min()
    PyPSAService.set_network(n)
    commercial = _cfg(PPA)
    status, _ = run_simulation(SolverConfig(commercial=commercial), n, PyPSAService.get_lock(),
                               threading.Event(), queue.SimpleQueue(),
                               state_update=lambda **kw: None)
    assert status in ("ok", "optimal")
    assert SI.contracts_state(n, CommercialConfig.model_validate(commercial)) is None


# ── round trips ────────────────────────────────────────────────────────────


def _series(client, n, name="px"):
    idx = n.snapshots.tz_localize("UTC")
    r = client.post("/api/library/series", json={
        "name": name, "timestamps": [t.isoformat() for t in idx],
        "values": [40.0] * len(idx), "meta": {"source": "pytest"}})
    assert r.status_code == 200, r.text
    return r.json()


def test_contracts_round_trip_through_the_route_save_load_and_a_bundle(
        client, other_org_client, install_network, session_ctx):
    n = build_edge_15min()
    install_network(n, name="contracted")
    assert client.post("/api/projects/contracted",
                       params={"force": True, "rebind": True}).status_code == 200
    ref = _series(client, n)
    contracts = [{**PPA, "reference_price": ref},
                 {"type": "cfd", "id": "c", "strike": 70.0, "tenor_years": 5,
                  "asset_ids": ["pv"], "reference_price": ref}]
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "timezone": "UTC", "import_tariff": TARIFF,
        "contracts": contracts}})
    assert r.status_code == 200, r.text
    stored = session_ctx(client).solver_state["solver_config"].commercial
    assert [c["type"] for c in stored["contracts"]] == ["ppa", "cfd"]
    # save → load
    assert client.post("/api/projects/contracted",
                       params={"expect": "contracted"}).status_code == 200
    assert client.get("/api/projects/contracted").status_code == 200
    reloaded = session_ctx(client)
    back = CommercialConfig.model_validate(reloaded.solver_state["solver_config"].commercial)
    assert [c.id for c in back.contracts] == ["ppa1", "c"]
    assert SI.reference_price(reloaded.network, back.contracts[0])[1] == []
    # bundle: the reference series is pinned; another org reports it missing
    b = client.get("/api/projects/contracted/bundle")
    side = json.loads(zipfile.ZipFile(io.BytesIO(b.content)).read("library_refs.json"))
    assert [(p["id"], p["version"]) for p in side["refs"]] == [("px", 1)]
    other = other_org_client.post("/api/projects/import_bundle?name=contracted_there",
                                  files={"file": ("b.zip", b.content, "application/zip")})
    assert [i["reason"] for i in other.json()["library_issues"]] == ["missing"]
