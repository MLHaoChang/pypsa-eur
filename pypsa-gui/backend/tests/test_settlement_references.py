"""
Settlement inputs, part b: contracts on the config, reference series and the
`ic:` namespace (Edge Investment Case P2 WP2.2-0b).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2-0.
`CommercialConfig.contracts` is a list discriminated by `type`; untagged P0-era
payloads are tagged from their shape. A contract's reference price and the
config's `grid_cfe_share_ref` are Library series resolved at
`PUT /solver_config` (the export price's path) and written, fully covered only,
to `buses_t["ic_ref_price"]` (column `ic:contract:<id>`) and
`buses_t["ic_grid_cfe_share"]` (column `ic:cfe:grid`) with the ref and the axis
hash in `meta["ic_ref_series"]`. A reader returns the series or `None` + a
`*_missing` flag (not bound, another axis, NaN, or a ref other than the
config's). Bus names starting `ic:` are refused, so the columns cannot collide.
"""
from __future__ import annotations

import io
import logging

import numpy as np
import pandas as pd
import pytest

from models.commercial import CommercialConfig
from services.commercial import settlement_inputs as SI
from tests.fixtures.investment_case.edge_15min import build_edge_15min

PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 55.0,
       "tenor_years": 10, "seller": "Wind BV", "buyer": "site", "asset_ids": ["pv"]}


def _series(client, n, name="ref", value=40.0, drop=0):
    idx = n.snapshots.tz_localize("UTC")[: len(n.snapshots) - drop]
    r = client.post("/api/library/series", json={
        "name": name, "timestamps": [t.isoformat() for t in idx],
        "values": [value] * len(idx), "meta": {"source": "pytest"}})
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, contracts, **extra):
    return client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "timezone": "UTC", "contracts": contracts, **extra}})


# ── the config field ───────────────────────────────────────────────────────


def test_contracts_are_discriminated_by_type_and_untagged_p0_payloads_are_tagged():
    cfg = CommercialConfig.model_validate({"poc_link": "import", "contracts": [
        PPA,
        {"id": "c", "strike": 60.0, "tenor_years": 15, "asset_ids": ["pv"]},
        {"id": "d", "availability_eur_per_mw_year": 1.0, "activation_eur_per_mwh": 2.0,
         "load_ids": ["site_load"]},
        {"id": "l", "lessor": "a", "lessee": "b", "annual_payment": 1.0, "tenor_years": 5,
         "asset_ids": ["bess"]},
        {"id": "e", "provider": "a", "customer": "b", "fee_eur_per_mwh": 1.0, "tenor_years": 5,
         "asset_ids": ["pv"]},
        {"id": "r", "retailer": "a", "customer": "b", "tariff_id": "t", "tenor_years": 1},
        {k: v for k, v in PPA.items() if k != "type"}]})
    assert [c.type for c in cfg.contracts] == ["ppa", "cfd", "dr", "lease", "eaas", "retail",
                                               "ppa"]
    with pytest.raises(ValueError):
        CommercialConfig.model_validate({"poc_link": "import",
                                         "contracts": [{**PPA, "type": "gadget"}]})


def test_new_config_fields_are_registered_for_hash_recipe_1():
    from services.commercial import hashing as H

    assert H.FIELDS_AFTER_V1[("CommercialConfig", "contracts")] == []
    assert H.FIELDS_AFTER_V1[("CommercialConfig", "grid_cfe_share_ref")] is None


# ── reference series through the route ────────────────────────────────────


def test_the_route_writes_reference_series_under_the_ic_namespace(
        client, install_network, session_ctx):
    n = build_edge_15min()
    install_network(n)
    ref = _series(client, n, "px")
    cfe = _series(client, n, "cfe", value=0.4)
    r = _put(client, [{**PPA, "reference_price": ref}], grid_cfe_share_ref=cfe)
    assert r.status_code == 200, r.text
    live = session_ctx(client).network
    assert list(live.buses_t[SI.REF_PRICE_ATTR].columns) == ["ic:contract:ppa1"]
    assert list(live.buses_t[SI.CFE_ATTR].columns) == ["ic:cfe:grid"]
    cfg = CommercialConfig.model_validate(
        session_ctx(client).solver_state["solver_config"].commercial)
    s, flags = SI.reference_price(live, cfg.contracts[0])
    assert flags == [] and np.allclose(s, 40.0)
    s, flags = SI.grid_cfe_share(live, cfg)
    assert flags == [] and np.allclose(s, 0.4)
    # Re-binding without the contract drops its column.
    assert _put(client, []).status_code == 200
    assert "ic:contract:ppa1" not in session_ctx(client).network.buses_t[SI.REF_PRICE_ATTR]


def test_an_uncovered_reference_series_is_refused_and_nothing_is_written(
        client, install_network, session_ctx):
    n = build_edge_15min()
    install_network(n)
    ref = _series(client, n, "short", drop=4)
    r = _put(client, [{**PPA, "reference_price": ref}])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "reference_price_coverage"
    assert SI.REF_PRICE_ATTR not in session_ctx(client).network.buses_t or \
        session_ctx(client).network.buses_t[SI.REF_PRICE_ATTR].empty


# ── readers ────────────────────────────────────────────────────────────────


def _bound(n, value=40.0):
    cfg = CommercialConfig.model_validate({"poc_link": "import", "contracts": [
        {**PPA, "reference_price": {"id": "px", "version": 1, "hash": "a" * 64,
                                    "source": "t"}}]})
    SI.write_reference_series(n, {"ic:contract:ppa1": (pd.Series(value, index=n.snapshots),
                                                        cfg.contracts[0].reference_price)},
                              frame=SI.REF_PRICE_ATTR)
    return cfg


def test_a_reference_is_missing_when_unbound_changed_or_on_another_axis():
    n = build_edge_15min()
    cfg = CommercialConfig.model_validate({"poc_link": "import", "contracts": [PPA]})
    assert SI.reference_price(n, cfg.contracts[0]) == (None, ["reference_price_missing"])
    cfg = _bound(n)
    assert SI.reference_price(n, cfg.contracts[0])[1] == []
    moved = cfg.contracts[0].model_copy(update={
        "reference_price": cfg.contracts[0].reference_price.model_copy(update={"version": 2})})
    assert SI.reference_price(n, moved) == (None, ["reference_price_missing",
                                                   "reference_changed_since_binding"])
    n.set_snapshots(n.snapshots[:10])                              # axis changed
    assert SI.reference_price(n, cfg.contracts[0]) == (None, ["reference_price_missing"])


def test_reference_frames_survive_netcdf_copy_bus_removal_and_rename(tmp_path):
    import pypsa

    n = build_edge_15min()
    cfg = _bound(n)
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    for m in (pypsa.Network(path), n.copy()):
        assert SI.reference_price(m, cfg.contracts[0])[1] == []
    n.rename_component_names("Bus", site="site2")
    n.remove("Bus", "grid")
    assert SI.reference_price(n, cfg.contracts[0])[1] == []


def test_loading_a_network_with_reference_frames_logs_no_bus_mismatch_warning(tmp_path,
                                                                                caplog):
    from services.pypsa_service import PyPSAService

    n = build_edge_15min()
    _bound(n)
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    import pypsa

    with caplog.at_level(logging.WARNING):
        PyPSAService.import_network_from_netcdf(pypsa.Network(), path)
    assert not [r for r in caplog.records if "ic:contract" in r.getMessage()]


# ── the ic: namespace ──────────────────────────────────────────────────────


def test_bus_names_starting_ic_are_refused(client, install_network):
    install_network(build_edge_15min())
    r = client.post("/api/network/buses", json={"name": "ic:contract:x"})
    assert r.status_code == 422
    r = client.post("/api/network/buses/site/rename", json={"new_name": "ic:cfe:grid"})
    assert r.status_code == 422
    assert client.post("/api/network/buses", json={"name": "icing"}).status_code == 201


def test_a_netcdf_import_with_an_ic_bus_is_refused(client, tmp_path):
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "ic:x")
    path = tmp_path / "bad.nc"
    n.export_to_netcdf(path)
    r = client.post("/api/io/import/netcdf",
                    files={"file": ("bad.nc", path.read_bytes(), "application/x-netcdf")})
    assert r.status_code == 422, r.text


def test_reference_frames_are_not_user_time_series(client, install_network, session_ctx):
    n = build_edge_15min()
    _bound(n)
    install_network(n)
    listed = {(e["component"], e["attribute"])
              for e in client.get("/api/network/timeseries").json()}
    assert ("buses", SI.REF_PRICE_ATTR) not in listed
    assert client.get(f"/api/network/timeseries/buses/{SI.REF_PRICE_ATTR}").status_code == 404
