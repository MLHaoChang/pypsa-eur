"""
Contracts on the config: round trips and the double-count preflight (Edge
Investment Case P2 WP2.2c).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2c.
Contracts survive `PUT /solver_config`, save → load and a bundle export →
import (their reference series pinned). `commercial.contract_asset_missing` /
`contract_tariff_mismatch`: refused when the config is bound; at preflight and
solve time an ERROR only for a contract that changes dispatch, a warning for a
settlement-only one (review round 1 #4). Warnings: `ppa_export_double_count`
(the site SELLS a PPA's behind-the-meter output that also earns export revenue),
`dr_without_dsr` (a DR load on a bus without active DSR cannot settle — the
round-1 redesign of the plan's `dr_double_count`), `sleeved_commodity_double_count`,
`eaas_on_poc`. A settlement records its contracts (`contracts_record`), a later
edit is drift on THAT settlement. Closes P1 WP1.8's recorded deviation.
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


BAD_CONTRACTS = [
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
]


@pytest.mark.parametrize("contract", BAD_CONTRACTS)
def test_a_settlement_contract_naming_what_cannot_settle_warns_and_binding_refuses_it(contract):
    """Round 1 #4: it does not shape the LP, so the solve still runs."""
    from services.commercial import lp_bindings as L

    n = build_edge_15min()
    codes = _codes(n, _cfg(contract))
    assert ("warning", "commercial.contract_asset_missing") in codes
    assert not [c for c in codes if c[0] == "error"]
    with pytest.raises(L.CommercialBindingError, match="contract_asset_missing"):
        L.validate_for_network(n, CommercialConfig.model_validate(_cfg(contract)),
                               refuse_settlement_contracts=True)


def test_a_dispatch_ppa_naming_what_cannot_settle_is_an_error():
    bad = {**PPA, "asset_ids": ["ghost"], "changes_dispatch": True}
    assert ("error", "commercial.contract_asset_missing") in _codes(build_edge_15min(),
                                                                     _cfg(bad))


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
    dsr = {"buses": ["site"], "price": 100.0, "share": 0.1}
    assert _codes(build_edge_15min(), _cfg(*ok), dsr=dsr) == set()


def test_a_retail_contract_naming_another_or_no_tariff_is_a_mismatch():
    """Round 1 #8: no import tariff at all is a mismatch too."""
    retail = {"type": "retail", "id": "r", "retailer": "a", "customer": "site",
              "tariff_id": "other", "tenor_years": 1}
    n = build_edge_15min()
    assert ("warning", "commercial.contract_tariff_mismatch") in _codes(n, _cfg(retail))
    none = {"poc_link": "import", "contracts": [{**retail, "tariff_id": "t"}]}
    assert ("warning", "commercial.contract_tariff_mismatch") in _codes(n, none)


# ── the site behind the meter ──────────────────────────────────────────────


def test_site_generators_are_those_behind_the_meter():
    """Round 1 #3: reachable from the import members' site side without
    crossing an import or export Link."""
    from services.commercial import lp_bindings as L

    n = _exporting()
    n.add("Bus", "farm", carrier="AC")
    n.add("Line", "farm_line", bus0="farm", bus1="grid", x=0.1, s_nom=100.0)
    n.add("Generator", "farm_wind", bus="farm", p_nom=10.0, carrier="solar")
    n.add("Bus", "roof", carrier="AC")
    n.add("Line", "roof_line", bus0="roof", bus1="site", x=0.1, s_nom=100.0)
    n.add("Generator", "roof_pv", bus="roof", p_nom=1.0, carrier="solar")
    cfg = CommercialConfig.model_validate(_cfg(export_link="export"))
    assert sorted(L.site_generators(n, cfg)) == ["pv", "roof_pv"]


# ── double-count warnings ──────────────────────────────────────────────────


FIT = {"id": "fit", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
       "direction": "revenue", "periods": [{"name": "all", "rate": 0.05}]}


def _exporting():
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=50.0, carrier="AC")
    return n


def _selling(*contracts, **extra):
    return {**_cfg(*contracts, export_link="export", **extra),
            "import_tariff": {**TARIFF, "items": [TOU, FIT]}}


def test_a_ppa_the_site_sells_while_earning_export_revenue_is_a_double_count_warning():
    n = _exporting()
    sold = {**PPA, "seller": "site", "buyer": "Offtaker"}
    code = ("warning", "commercial.ppa_export_double_count")
    assert code in _codes(n, _selling(sold))
    assert code in _codes(n, _selling({**sold, "seller": " Site "}))      # trimmed, any case
    assert code not in _codes(n, _selling(PPA))                           # the buyer case
    assert code not in _codes(n, _selling({**sold, "changes_dispatch": True}))
    # An export Link that earns nothing (no price, no export item): no warning.
    assert code not in _codes(n, _cfg(sold, export_link="export"))
    assert code not in _codes(build_edge_15min(), _cfg(sold))            # no export at all


def test_a_generator_not_behind_the_meter_is_not_a_double_count():
    n = _exporting()
    n.add("Bus", "farm", carrier="AC")
    n.add("Line", "farm_line", bus0="farm", bus1="grid", x=0.1, s_nom=100.0)
    n.add("Generator", "farm_wind", bus="farm", p_nom=10.0, carrier="solar")
    sold = {**PPA, "seller": "site", "buyer": "Offtaker", "asset_ids": ["farm_wind"]}
    assert ("warning", "commercial.ppa_export_double_count") not in _codes(n, _selling(sold))


def test_the_site_party_names_who_the_site_is():
    n = _exporting()
    sold = {**PPA, "seller": "Hub BV", "buyer": "Offtaker"}
    assert ("warning", "commercial.ppa_export_double_count") in _codes(
        n, _selling(sold, site_party="Hub BV"))


def test_a_dr_contract_without_active_dsr_on_its_bus_cannot_settle():
    """Round 1 #1 (HIGH): DR activation reads the DSR dispatch, so DR WITH DSR
    is the working case; a DR load's bus without active DSR is the warning."""
    dr = {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0,
          "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"]}
    n = build_edge_15min()
    code = ("warning", "commercial.dr_without_dsr")
    assert code not in _codes(n, _cfg(dr), dsr={"buses": ["site"], "price": 100.0,
                                                 "share": 0.1})
    assert code in _codes(n, _cfg(dr), dsr={"buses": ["grid"], "price": 100.0, "share": 0.1})
    assert code in _codes(n, _cfg(dr), dsr={"buses": ["site"], "price": 0.0, "share": 0.1})
    assert code in _codes(n, _cfg(dr))


def test_a_sleeved_ppa_with_a_per_kwh_item_may_double_count():
    sleeved = {**PPA, "kind": "sleeved", "sleeving_fee_eur_per_mwh": 2.0}
    n = build_edge_15min()
    found = [m for sev, code, _c, _n, m in commercial_findings(n, _cfg(sleeved))
             if code == "commercial.sleeved_commodity_double_count"]
    assert found and "may charge it again" in found[0]
    fixed_only = {**TARIFF, "items": [{"id": "standing", "kind": "fixed", "unit": "per_month",
                                       "periods": [{"name": "all", "rate": 10.0}]}]}
    assert ("warning", "commercial.sleeved_commodity_double_count") not in _codes(
        n, {**_cfg(sleeved), "import_tariff": fixed_only})


def test_an_eaas_on_the_poc_link_warns():
    """Round 1 #7."""
    eaas = {"type": "eaas", "id": "e", "provider": "a", "customer": "b", "fee_eur_per_mwh": 1.0,
            "tenor_years": 5, "asset_ids": ["import"]}
    assert ("warning", "commercial.eaas_on_poc") in _codes(build_edge_15min(), _cfg(eaas))


def test_validation_passes_the_solver_configs_dsr_settings():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    dr = {"type": "dr", "id": "d", "availability_eur_per_mw_year": 1.0,
          "activation_eur_per_mwh": 1.0, "load_ids": ["site_load"]}
    on = SolverConfig(commercial=_cfg(dr), dsr_price_eur_per_mwh=100.0, dsr_share_of_load=0.1,
                      dsr_buses=["site"])
    off = SolverConfig(commercial=_cfg(dr))
    codes = lambda cfg: {i.code for i in validate_for_run(build_edge_15min(), cfg)}  # noqa: E731
    assert "commercial.dr_without_dsr" not in codes(on)
    assert "commercial.dr_without_dsr" in codes(off)


# ── the settlement's contracts record ──────────────────────────────────────


def test_a_contract_changed_after_a_settlement_is_drift_on_that_settlement():
    """Round 1 #5: order-insensitive; `site_party` counts; the SOLVE records no
    settlement-only contract."""
    cfg = CommercialConfig.model_validate(_cfg(PPA, {**PPA, "id": "b"}))
    rec = SI.contracts_record(cfg)
    assert SI.contracts_state(rec, cfg) is None
    swapped = CommercialConfig.model_validate(_cfg({**PPA, "id": "b"}, PPA))
    assert SI.contracts_state(rec, swapped) is None                     # reordering is no edit
    edited = CommercialConfig.model_validate(_cfg({**PPA, "price": 60.0}, {**PPA, "id": "b"}))
    assert SI.contracts_state(rec, edited) == "config"
    other_site = CommercialConfig.model_validate(
        _cfg(PPA, {**PPA, "id": "b"}, site_party="Hub BV"))
    assert SI.contracts_state(rec, other_site) == "config"
    assert SI.contracts_state(None, edited) == "not_recorded"


@pytest.mark.live_solve
def test_a_solve_records_no_settlement_only_contracts_and_clearing_drops_a_legacy_record():
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    def solve(n, commercial):
        PyPSAService.set_network(n)
        status, _ = run_simulation(SolverConfig(commercial=commercial), n,
                                   PyPSAService.get_lock(), threading.Event(),
                                   queue.SimpleQueue(), state_update=lambda **kw: None)
        assert status in ("ok", "optimal")

    n = build_edge_15min()
    solve(n, _cfg(PPA))
    assert SI.META_CONTRACTS not in n.meta
    n.meta[SI.META_CONTRACTS] = {"hash": "x"}                            # a pre-review record
    solve(n, None)
    assert SI.META_CONTRACTS not in n.meta


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
