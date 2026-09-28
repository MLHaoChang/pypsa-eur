"""
Settlement inputs, part a: interval quantities and the DSR dispatch record
(Edge Investment Case P2 WP2.2-0a).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2-0.
`physical_quantities` additionally returns interval frames (MW per snapshot)
per asset and per load, and the PoC flows ON THE COMMERCIAL METER (Σ import
Links, the export Link) — the period totals stay the WP0.3 numbers. The DSR
slack's dispatch, captured when the transient slacks are removed, is committed
as `buses_t["ic_dsr_p"]` only after a successful, non-operational solve, and
cleared after a successful solve without DSR; it rides network.nc.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.commercial import settlement_inputs as SI
from services.pypsa_service import PyPSAService
from services.results.physical_quantities import physical_quantities
from services.solver_service import SolverConfig, run_simulation
from tests.fixtures.investment_case.edge_15min import build_edge_15min


def _result_df(n, accessor, attr, source="lopf"):
    acc = getattr(n, accessor, None)
    return None if acc is None else getattr(acc, attr, None)


def _solve(n, **cfg_kw):
    PyPSAService.set_network(n)
    cfg = SolverConfig(**cfg_kw)
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, cond)
    return cfg


# ── interval quantities ────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_interval_frames_sum_to_the_seams_totals():
    n = build_edge_15min()
    cfg = _solve(n)
    pq = physical_quantities(n, cfg, result_df=_result_df)
    iv, w = pq["intervals"], pq["weights"]["energy"]
    comp = pq["components"]
    assert np.allclose(iv["generators"].mul(w, axis=0).sum(), comp["generators"]["energy_mwh"])
    su = comp["storage_units"]
    assert np.allclose(iv["storage_units_discharge"].mul(w, axis=0).sum(), su["discharge_mwh"])
    assert np.allclose(iv["storage_units_charge"].mul(w, axis=0).sum(), su["charge_mwh"])
    links = comp["links"]
    assert np.allclose(iv["links_p0"].mul(w, axis=0).sum(), links["input_mwh"])
    assert np.allclose(iv["links_output"].mul(w, axis=0).sum(), links["output_mwh"])
    load = iv["loads"]["site_load"]
    assert np.allclose(load, n.loads_t.p_set["site_load"])     # served load (no shedding)
    assert (iv["storage_units_charge"] >= 0).all().all()


@pytest.mark.live_solve
def test_the_commercial_meter_is_the_sum_of_the_import_links_and_the_export_link():
    from tests.test_group_contract import _two_members

    n = _two_members()
    commercial = {"poc_link": "import", "group_contract": "hub",
                  "group_members": ["import", "import_b"], "group_cap_mw": 60.0,
                  "import_tariff": {"id": "t", "name": "t", "jurisdiction": "DE",
                                    "valid_from": "2029-01-01", "items": [
                                        {"id": "e", "kind": "energy", "unit": "per_kwh",
                                         "periods": [{"name": "all", "rate": 0.1}]}]}}
    cfg = _solve(n, commercial=commercial)
    meter = physical_quantities(n, cfg, result_df=_result_df)["commercial_meter"]
    p0 = n.links_t.p0
    assert meter["import_links"] == ["import", "import_b"]
    assert np.allclose(meter["import_mw"], p0["import"] + p0["import_b"])
    assert meter["export_mw"] is None                               # no export link


def test_no_commercial_config_has_no_meter():
    n = build_edge_15min()
    n.links_t.p0 = pd.DataFrame({"import": 1.0}, index=n.snapshots)

    class _Cfg:
        commercial = None

    pq = physical_quantities(n, _Cfg(), result_df=_result_df)
    assert pq["commercial_meter"] is None


# ── DSR dispatch record ────────────────────────────────────────────────────


def _dsr_network():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas", p_nom=40.0, marginal_cost=200.0)
    return n


DSR = dict(dsr_price_eur_per_mwh=50.0, dsr_share_of_load=0.2, dsr_buses=["b"])


@pytest.mark.live_solve
def test_a_successful_solve_commits_the_dsr_dispatch_on_the_bus():
    n = _dsr_network()
    _solve(n, **DSR)
    frame = n.buses_t[SI.DSR_ATTR]
    assert list(frame.columns) == ["b"]
    assert np.allclose(frame["b"], 20.0)                           # 20 % of the 100 MW peak
    rec = n.meta[SI.META_DSR]
    assert rec["axis_hash"] == SI.axis_hash(n.snapshots)
    assert rec["total_mwh"] == pytest.approx(20.0 * 4 * 3.0)
    assert "__dsr_b" not in n.generators.index                      # the slack is gone


@pytest.mark.live_solve
def test_a_successful_solve_without_dsr_clears_the_record_and_an_operational_one_keeps_it():
    n = _dsr_network()
    _solve(n, **DSR)
    n._ic_operational = True                                       # an adequacy sweep solve
    try:
        _solve(n)
    finally:
        del n._ic_operational
    assert SI.DSR_ATTR in n.buses_t and SI.META_DSR in n.meta       # not overwritten
    _solve(n)
    assert n.buses_t.get(SI.DSR_ATTR) is None or n.buses_t[SI.DSR_ATTR].empty
    assert SI.META_DSR not in n.meta


def test_dsr_activation_reads_the_record_or_says_it_is_not_established():
    n = _dsr_network()
    assert SI.dsr_activation(n) == (None, ["dr_activation_not_established"])
    n.buses_t[SI.DSR_ATTR] = pd.DataFrame({"b": 5.0}, index=n.snapshots)
    n.meta[SI.META_DSR] = {"axis_hash": SI.axis_hash(n.snapshots), "total_mwh": 60.0}
    frame, flags = SI.dsr_activation(n)
    assert flags == [] and np.allclose(frame["b"], 5.0)
    n.set_snapshots(pd.date_range("2031-01-01", periods=4, freq="h"))  # axis changed
    assert SI.dsr_activation(n) == (None, ["dr_activation_not_established"])


@pytest.mark.live_solve
def test_the_dsr_record_survives_a_netcdf_round_trip(tmp_path):
    n = _dsr_network()
    _solve(n, **DSR)
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    back = pypsa.Network(path)
    frame, flags = SI.dsr_activation(back)
    assert flags == [] and np.allclose(frame["b"], 20.0)
