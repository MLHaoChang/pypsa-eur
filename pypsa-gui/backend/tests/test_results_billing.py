"""
Billing and CFE results (Edge Investment Case P2 WP2.5).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.5.
`compute_billing(n, cfg, *, state, result_df)` is the network-level driver:
the site bill (`commercial.billing.bill_site`), every contract settled per
period from the physical seam and the settlement readers (a dispatch PPA at
the price the LP committed, so its line equals its row), the billing-vs-LP gap
(WP2.3) and the provenance with the contracts record; the compact frames are
stored in the solver state. `compute_cfe_score(n, cfg)` is hourly 24/7
matching. Both answer 204 before a solve or without a commercial config.
"""
from __future__ import annotations

import copy
import json
import queue
import threading

import numpy as np
import pandas as pd
import pytest
from fastapi import Response

from models.commercial import CommercialConfig
from services.commercial import settlement_inputs as SI
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
    {"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
    {"name": "peak", "rate": 0.40, "start_hour": 17, "end_hour": 21},
    {"name": "day", "rate": 0.20}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 12.0}], "measured_on": "import"}
FIXED = {"id": "standing", "kind": "fixed", "unit": "per_month",
         "periods": [{"name": "all", "rate": 250.0}]}
LEASE = {"type": "lease", "id": "lease1", "lessor": "Leasing GmbH", "lessee": "site",
         "annual_payment": 52_000.0, "tenor_years": 10, "asset_ids": ["bess"]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 10.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"],
       "changes_dispatch": True}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": [copy.deepcopy(i) for i in items]}


def _commercial():
    return {"poc_link": "import", "import_tariff": _tariff(TOU, DEMAND, FIXED),
            "contracts": [LEASE, PPA]}


def _solved(commercial, *, multi=False):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    import routers.simulation as sim_router
    from tests.conftest import install_network_into_backend

    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0       # the PV runs at 10
    if multi:
        n.set_investment_periods([2030, 2040])
        n.investment_period_weightings["years"] = 10.0
        n.investment_period_weightings["objective"] = 10.0
    install_network_into_backend(n)
    cfg = SolverConfig(commercial=commercial, multi_investment_periods=multi)
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal"), (status, cond)
    return n, cfg


# ── billing ────────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_the_billing_payload_bills_settles_and_reconciles(reset_backend):
    import routers.results as R
    import routers.simulation as sim_router
    from services.finance.report import load_billing_frames
    from services.results.cost_breakdown import compute_cost_breakdown

    n, cfg = _solved(_commercial())
    out = R.get_billing()
    assert not isinstance(out, Response)
    json.dumps(out, allow_nan=False)                        # JSON as served
    (key,) = out["per_period"]
    assert key == "_"
    per = out["per_period"]["_"]
    assert set(per["per_item"]) == {"energy", "demand", "standing"}
    assert per["total"] == pytest.approx(sum(per["per_item"].values()))
    by_id = {}
    for line in out["contracts"]["lines"]:
        by_id.setdefault(line["contract_id"], []).append(line)
    assert set(by_id) == {"lease1", "ppa1"}
    rows = compute_cost_breakdown(n, cfg)["commercial"]
    (ppa_line,) = by_id["ppa1"]
    assert ppa_line["amount"] == pytest.approx(rows["ppa_settlement"], rel=1e-12)
    assert ppa_line["payer"] == "site" and ppa_line["payee"] == "Solar BV"
    gap = out["gap"]
    assert gap["gates"] == []
    for kind, v in gap["periods"]["_"].items():
        assert v["unattributed_pct"] is not None and v["unattributed_pct"] < 1e-6, kind
    rec = SI.contracts_record(CommercialConfig.model_validate(cfg.commercial))
    assert out["provenance"]["contracts"] == rec
    frames = load_billing_frames(sim_router._state)
    assert "_:lines" in frames and "_:monthly" in frames


@pytest.mark.live_solve
def test_two_periods_bill_and_settle_per_period(reset_backend):
    import routers.results as R

    n, cfg = _solved(_commercial(), multi=True)
    out = R.get_billing()
    assert set(out["per_period"]) == {"2030", "2040"}
    periods = {line["period"] for line in out["contracts"]["lines"]}
    assert periods == {2030, 2040}
    assert set(out["gap"]["periods"]) == {"2030", "2040"}
    assert out["gap"]["gates"] == []


def test_billing_and_cfe_are_204_before_a_solve_or_without_a_config(reset_backend):
    import routers.results as R
    import routers.simulation as sim_router
    from services.solver_service import SolverConfig
    from tests.conftest import install_network_into_backend

    install_network_into_backend(build_edge_15min())
    sim_router._state["solver_config"] = SolverConfig(commercial=_commercial())
    assert R.get_billing().status_code == 204
    assert R.get_cfe_score().status_code == 204


@pytest.mark.live_solve
def test_the_handler_and_the_compute_function_agree(reset_backend):
    """The seam (tests/test_results_seam.py convention) on a solved site."""
    import routers.results as R
    import routers.simulation as sim_router
    from services.results.billing import compute_billing
    from services.results.cfe_score import compute_cfe_score

    n, cfg = _solved(_commercial())
    state = {}
    direct = compute_billing(n, cfg, state=state, result_df=R._result_df)
    assert json.dumps(R.get_billing(), sort_keys=True) == json.dumps(direct, sort_keys=True)
    assert json.dumps(R.get_cfe_score(), sort_keys=True) == \
        json.dumps(compute_cfe_score(n, cfg, result_df=R._result_df), sort_keys=True)


@pytest.mark.live_solve
def test_get_results_serves_both_kinds_to_the_chat(reset_backend):
    from services import chat_tools

    _solved(_commercial())
    assert "per_period" in chat_tools.get_results(result_kind="billing")
    assert "per_period" in chat_tools.get_results(result_kind="cfe_score")


# ── CFE hand fixture ───────────────────────────────────────────────────────


def _cfe_site():
    """One 15-min day: a 100 MW load, PV (clean) and a 20 MW gas unit behind
    the PoC, an export Link; the dispatch is written by hand (no solve)."""
    import pypsa

    idx = pd.date_range("2030-06-01", periods=96, freq="15min")
    n = pypsa.Network()
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 0.25
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "AC")
    for b in ("grid", "site"):
        n.add("Bus", b, carrier="AC")
    n.add("Link", "import", bus0="grid", bus1="site", p_nom=200.0, carrier="AC")
    n.add("Link", "export", bus0="site", bus1="grid", p_nom=200.0, carrier="AC")
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=200.0)
    n.add("Generator", "gas", bus="site", carrier="gas", p_nom=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="gas", p_nom=500.0)
    n.add("Load", "site_load", bus="site", p_set=100.0)
    hour = np.asarray(idx.hour)
    pv = np.where((hour >= 10) & (hour < 14), 150.0, np.where((hour >= 7) & (hour < 18), 40.0,
                                                               0.0))
    gas = np.full(96, 20.0)
    imp = np.clip(100.0 - pv - gas, 0.0, None)
    exp = np.clip(pv + gas - 100.0, 0.0, None)
    n.generators_t.p = pd.DataFrame({"pv": pv, "gas": gas, "grid_supply": imp}, index=idx)
    n.loads_t.p = pd.DataFrame({"site_load": 100.0}, index=idx)
    n.links_t.p0 = pd.DataFrame({"import": imp, "export": exp}, index=idx)
    n.links_t.p1 = -n.links_t.p0
    return n, pv, gas, imp, exp


def _cfg(**extra):
    from services.solver_service import SolverConfig

    return SolverConfig(commercial={"poc_link": "import", "export_link": "export", **extra})


def test_cfe_hand_fixture_without_a_grid_share():
    from services.results.cfe_score import compute_cfe_score

    n, pv, gas, imp, exp = _cfe_site()
    out = compute_cfe_score(n, _cfg())
    # Hand: the PV's share of the export is pro rata to site output.
    clean = pv - np.minimum(pv, exp * pv / (pv + gas))
    load_h = (100.0 * 0.25 * np.ones(96)).reshape(24, 4).sum(axis=1)
    clean_h = (clean * 0.25).reshape(24, 4).sum(axis=1)
    expected = float(np.minimum(load_h, clean_h).sum() / load_h.sum())
    per = out["per_period"]["_"]
    assert per["score"] == pytest.approx(expected, abs=1e-9)
    assert per["onsite_clean_consumed_mwh"] == pytest.approx(float(clean_h.sum()), abs=1e-9)
    assert per["grid_clean_mwh"] == 0.0
    assert "grid_cfe_share_missing" in out["flags"]
    assert "storage_shifted_clean_not_credited" in out["notes"]


def test_cfe_hand_fixture_with_a_grid_share_and_an_offsite_ppa():
    from services.results.cfe_score import compute_cfe_score

    n, pv, gas, imp, exp = _cfe_site()
    n.add("Bus", "farm", carrier="AC")
    n.add("Generator", "wind_farm", bus="farm", carrier="solar", p_nom=10.0)
    wind = np.full(96, 5.0)
    n.generators_t.p["wind_farm"] = wind
    ref = {"id": "cfe", "version": 1, "hash": "a" * 64, "source": "t"}
    share = np.linspace(0.2, 0.6, 96)
    SI.write_reference_series(n, {SI.CFE_COLUMN: (share, ref)}, frame=SI.CFE_ATTR)
    ppa = {"type": "ppa", "id": "vppa", "kind": "pay_as_produced", "price": 40.0,
           "tenor_years": 10, "seller": "Wind BV", "buyer": "site", "asset_ids": ["wind_farm"]}
    out = compute_cfe_score(n, _cfg(grid_cfe_share_ref=ref, contracts=[ppa]))
    clean = pv - np.minimum(pv, exp * pv / (pv + gas)) + wind + imp * share
    load_h = np.full(24, 100.0)
    clean_h = (clean * 0.25).reshape(24, 4).sum(axis=1)
    expected = float(np.minimum(load_h, clean_h).sum() / load_h.sum())
    per = out["per_period"]["_"]
    assert per["score"] == pytest.approx(expected, abs=1e-9)
    assert per["offsite_ppa_mwh"] == pytest.approx(float((wind * 0.25).sum()), abs=1e-9)
    assert per["grid_clean_mwh"] == pytest.approx(float((imp * share * 0.25).sum()), abs=1e-9)
    assert "grid_cfe_share_missing" not in out["flags"]


# ── review round 1 ─────────────────────────────────────────────────────────


RETAIL = {"type": "retail", "id": "r1", "retailer": "Energy Co", "customer": "site",
          "tariff_id": "t", "tenor_years": 3}


@pytest.mark.live_solve
def test_retail_contracts_and_the_payload_order(reset_backend):
    """F1 (retail crashed the payload) and F7 (the summary comes first)."""
    import routers.results as R

    commercial = {**_commercial(), "contracts": [LEASE, RETAIL]}
    _solved(commercial)
    out = R.get_billing()
    assert out["contracts"]["retail"]["r1"] is not None
    assert list(out)[:4] == ["summary", "flags", "contracts", "gap_summary"]
    assert out["summary"]["_"]["total"] == out["per_period"]["_"]["total"]
    # A retail contract naming another tariff is not settled, and says so.
    bad = {**commercial, "contracts": [LEASE, {**RETAIL, "tariff_id": "other"}]}
    import routers.simulation as sim_router
    sim_router._state["solver_config"].commercial = bad
    out = R.get_billing()
    assert out["contracts"]["retail"]["r1"] is None
    assert any(f.startswith("contract_not_settled:r1:") for f in out["contracts"]["flags"])


@pytest.mark.live_solve
def test_a_dispatch_ppa_edited_after_the_solve_settles_on_its_current_terms(reset_backend):
    """F3: the committed price stands only while the record matches."""
    import routers.results as R
    import routers.simulation as sim_router

    n, cfg = _solved(_commercial())
    edited = copy.deepcopy(cfg.commercial)
    edited["contracts"][1]["price"] = 99.0
    sim_router._state["solver_config"].commercial = edited
    out = R.get_billing()
    (line,) = [ln for ln in out["contracts"]["lines"] if ln["contract_id"] == "ppa1"]
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    assert line["amount"] == pytest.approx(99.0 * float((w * n.generators_t.p["pv"]).sum()))
    assert "ppa_changed_since_solve" in line["flags"]
    assert "config_changed_since_solve" in out["flags"]


@pytest.mark.live_solve
def test_nan_output_and_no_export_link_are_handled_honestly(reset_backend):
    """F5 (NaN output is unknown, never 0) and F6 (no export Link ⇒ export 0)."""
    import routers.results as R
    import routers.simulation as sim_router

    btm = {"type": "ppa", "id": "btm", "kind": "as_consumed_btm", "price": 30.0,
           "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"]}
    lease_only = {"poc_link": "import", "import_tariff": _tariff(TOU), "contracts": [btm]}
    n, cfg = _solved(lease_only)
    (line,) = R.get_billing()["contracts"]["lines"]
    assert line["amount"] is not None and line["amount"] > 0          # export is 0, known
    n.generators_t.p.iloc[40, n.generators_t.p.columns.get_loc("pv")] = np.nan
    (line,) = R.get_billing()["contracts"]["lines"]
    assert line["amount"] is None and "generation_not_established" in line["flags"]


def test_the_routes_refuse_during_a_solve(reset_backend, monkeypatch):
    """F9: mid-solve the network carries the LP transforms."""
    import routers.results as R
    from fastapi import HTTPException as _E

    monkeypatch.setattr(R, "_solver_in_flight", lambda: True)
    for handler in (R.get_billing, R.get_cfe_score):
        with pytest.raises(_E) as exc:
            handler()
        assert exc.value.status_code == 409


def test_cfe_discloses_zero_co2_carriers_and_excludes_output_it_sells():
    """F8 / F12."""
    from services.results.cfe_score import compute_cfe_score

    n, pv, gas, imp, exp = _cfe_site()
    out = compute_cfe_score(n, _cfg())
    assert "clean_by_zero_co2_emissions:solar" in out["notes"]
    sold = {"type": "ppa", "id": "sold", "kind": "pay_as_produced", "price": 40.0,
            "tenor_years": 10, "seller": "site", "buyer": "Offtaker", "asset_ids": ["pv"]}
    out = compute_cfe_score(n, _cfg(contracts=[sold]))
    assert out["per_period"]["_"]["onsite_clean_consumed_mwh"] == 0.0
    n.loads_t.p.iloc[3, 0] = np.nan
    out = compute_cfe_score(n, _cfg())
    assert out["per_period"]["_"]["score"] is None
    assert "load_not_established:site_load" in out["flags"]


@pytest.mark.live_solve
def test_an_edited_dispatch_ppa_without_a_tariff_is_the_change_not_a_gate(reset_backend):
    """Round 2 R2-1."""
    import routers.results as R
    import routers.simulation as sim_router

    n, cfg = _solved({"poc_link": "import", "contracts": [PPA]})
    edited = copy.deepcopy(cfg.commercial)
    edited["contracts"][0]["price"] = 99.0
    sim_router._state["solver_config"].commercial = edited
    out = R.get_billing()
    assert "config_changed_since_solve" in out["flags"]
    assert out["gap_summary"]["gates"] == []
    causes = out["gap"]["periods"]["_"]["contracts"]["causes"]
    assert [c["cause"] for c in causes] == ["config_changed_since_solve"]


def test_the_in_flight_refusal_carries_an_error_kind(reset_backend, monkeypatch):
    """Round 2 R2-2."""
    import routers.results as R
    from fastapi import HTTPException as _E

    monkeypatch.setattr(R, "_solver_in_flight", lambda: True)
    with pytest.raises(_E) as exc:
        R.get_cfe_score()
    assert exc.value.detail["error_kind"] == "solver_in_flight"
    assert "score" in exc.value.detail["message"]
