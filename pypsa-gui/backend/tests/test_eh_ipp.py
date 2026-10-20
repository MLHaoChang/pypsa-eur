"""
The renewables IPP with a battery, ``eh_ipp_hybrid`` (IPP renewables + BESS ticket 03;
`.scratch/ipp-renewables-bess/spec.md`).

A generation-only site: wind 60 MW + PV 50 MWp (forced-size candidates) and a battery candidate
behind a 70 MW export limit, one hourly 2030 year, illustrative day-ahead prices on export and
import (tickets 02, 07), two financial pay-as-produced PPAs (ticket 01). Pinned here:

* the template: the network, the ledger labels, the shipped price series and their notes;
* the plan: the battery within bounds, export at most the limit, curtailment at the limit and at
  negative prices, the PV capture rate below 1;
* the gatekeeper's G5 traps: the grid slack is no `ipp` asset and costs 0 both ways, the import
  cost is booked once (wholesale apart from the tariff) with no Link cost of its own, circulation
  0, no load metric divides by zero, the throughput cost is `marginal_cost` (per MWh discharged);
* the investment case: an empty counterfactual, incremental = total, NPV and IRR established;
* section 7 is the only home of the commercial inputs (owner, 2026-10-20): changing one there
  changes the solve or the case, with no shadow copy;
* create-from-template: the series are minted and every commercial block binds.
"""
from __future__ import annotations

import copy
import json
import queue
import threading

import numpy as np
import pandas as pd
import pytest

import project_templates.eh_templates as T

TID = "eh_ipp_hybrid"
TPL_DIR = __import__("pathlib").Path(T.__file__).parent


def _mod():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_eh_ipp_t", TPL_DIR / "eh_ipp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _mod()


# ── the template ───────────────────────────────────────────────────────────


def test_it_is_registered_with_its_sidecars_and_series():
    assert TID in T.BUILDERS and TID in T.TEMPLATE_META and TID in T.PRICE_SERIES
    meta = json.loads((TPL_DIR / TID / T.META_FILE).read_text())
    assert meta == json.loads(json.dumps(T.TEMPLATE_META[TID]))
    assert json.loads((TPL_DIR / TID / T.SOLVER_FILE).read_text()) == \
        json.loads(json.dumps(T.solver_config(TID)))
    shipped = json.loads((TPL_DIR / TID / "price_series" / "meta.json").read_text())
    assert shipped == json.loads(json.dumps(M.series_meta()))


def test_the_shipped_series_are_the_shapes_and_read_as_illustrative():
    from services.library import template_series as TS

    series = {s.name: s for s in TS.read(TPL_DIR / TID)}
    assert set(series) == set(M.SERIES.values())
    base = series[M.SERIES["base"]]
    assert np.allclose(base.series.to_numpy(), M.price_shape("base").to_numpy())
    assert "illustrative" in base.meta["notes"] and len(base.series) == 8760
    assert TS.note_values(base.meta) == {"currency": "EUR", "price_basis": "real",
                                         "currency_year": "2030", "region": "DE-LU",
                                         "source_time_basis": "UTC"}
    fin = T.solver_config(TID)["finance"]
    assert TS.basis_conflicts(list(series.values()), fin) == []


def test_the_network_matches_the_spec():
    n = T.BUILDERS[TID]()
    assert len(n.snapshots) == 8760 and n.snapshots[0] == pd.Timestamp("2030-01-01")
    assert n.links.at["grid_export", "p_nom"] == 70.0 and n.links.at["grid_import", "p_nom"] == 20.0
    for g, mw in (("wind_farm", 60.0), ("pv_plant", 50.0)):
        assert n.generators.at[g, "p_nom_extendable"]
        assert n.generators.at[g, "p_nom_min"] == n.generators.at[g, "p_nom_max"] == mw
        assert n.generators.at[g, "overnight_cost"] > 0
    assert n.storage_units.at["bess", "p_nom_max"] == 50.0
    assert n.storage_units.at["bess", "max_hours"] == 2.0
    # The throughput cost prices discharge only (G5 trap 5).
    assert n.storage_units.at["bess", "marginal_cost"] == M.BESS_THROUGHPUT_EUR_PER_MWH
    assert float(n.storage_units.get("marginal_cost_storage", pd.Series({"bess": 0.0}))["bess"]) == 0.0
    # The grid slack and the meter carry no cost of their own (G5 traps 1, 2).
    assert n.generators.at["grid", "marginal_cost"] == 0.0 and n.generators.at["grid", "p_min_pu"] == -1.0
    assert (n.links.loc[["grid_import", "grid_export"], "marginal_cost"] == 0.0).all()
    assert n.loads.empty


def test_the_ledger_labels_every_price_illustrative_or_assumed():
    ledger = {r["item"]: r for r in T.TEMPLATE_META[TID]["ledger"]}
    assert ledger["day-ahead price (base)"]["source"] == "illustrative"
    assert ledger["PPAs"]["source"] == "illustrative"
    assert ledger["site location"]["value"] == "not set"
    assert ledger["cases"]["value"].startswith("8 planned cases")
    # The synthetic profiles sit at an assumed reference point until the pin is set (GK 5).
    assert ledger["site location"]["source"] == "assumed"
    assert "reference point" in ledger["site location"]["note"]


# ── the plan ───────────────────────────────────────────────────────────────


def _bind_prices(n, cfg_dict):
    """What binding writes at create: the series on the meter Links and the PPA references."""
    from services.commercial import lp_bindings as LP
    from services.commercial import settlement_inputs as SI

    da = M.price_shape("base")
    assert LP.write_export_price(n, "grid_export", da) == 0
    assert LP.write_import_price(n, ["grid_import"], da) == 0
    refs = {SI.contract_column(c["id"]): (da.to_numpy(), c["reference_price"])
            for c in cfg_dict["commercial"]["contracts"]}
    SI.write_reference_series(n, refs, frame=SI.REF_PRICE_ATTR)
    return da


def _solve(cfg_dict, *, network=None):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    install_network_into_backend(network if network is not None else T.BUILDERS[TID]())
    n = PyPSAService.get_network()
    da = _bind_prices(n, cfg_dict)
    cfg = _solver_config_from_dict(cfg_dict)
    sim_router._state["solver_config"] = cfg
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    _STATE.clear()
    _STATE.update(state)
    return n, cfg, da


#: The last solve's state (its lost-load capture), handed to the case as the IC route does.
_STATE: dict = {}


def _case(n, cfg, fin=None):
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.results.finance_case import build_finance_case

    from services.finance.packs.base import load_pack

    case = build_finance_case(n, cfg, FinanceInputs.model_validate(fin or cfg.finance),
                              result_df=R._result_df, lost_load=_STATE.get("last_lost_load"))
    pack = (load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
            if case.inputs.tax_pack_id else None)        # as the investment-case runner does
    return case, run_case(case, pack)


@pytest.fixture(scope="module")
def solved(_module_backend):
    return _solve(T.solver_config(TID))


@pytest.fixture(scope="module")
def _module_backend():
    """One backend reset for the module's shared solve."""
    from tests.conftest import _reset_backend_state

    _reset_backend_state()
    yield
    _reset_backend_state()


@pytest.mark.live_solve
def test_the_plan_sizes_the_battery_and_respects_the_export_limit(solved):
    n, _cfg, da = solved
    bess = float(n.storage_units.at["bess", "p_nom_opt"])
    assert 0.0 <= bess <= 50.0 + 1e-6
    assert float(n.links_t.p0["grid_export"].max()) <= 70.0 + 1e-6


@pytest.mark.live_solve
def test_the_plants_curtail_at_the_limit_and_at_negative_prices(solved):
    n, _cfg, da = solved
    plants = ["wind_farm", "pv_plant"]
    avail = n.generators_t.p_max_pu[plants] * n.generators.loc[plants, "p_nom_opt"]
    curt = (avail - n.generators_t.p[plants]).sum(axis=1)
    exp = n.links_t.p0["grid_export"]
    neg = da.to_numpy() < 0
    at_limit = exp.to_numpy() >= 70.0 - 1e-6
    assert curt[neg].sum() > 0, "no curtailment at negative prices"
    assert curt[at_limit & ~neg].sum() > 0, "no curtailment at the export limit"
    # Circulation (G5 trap 3): import (DA + 20) costs more than export (DA) earns every hour,
    # so the preflight finds no arbitrage loop.
    from services.commercial.preflight import commercial_findings

    assert "commercial.arbitrage_loop" not in {c for _s, c, *_ in
                                               commercial_findings(n, _cfg.commercial)}


@pytest.mark.live_solve
def test_the_pv_capture_rate_is_below_one(solved):
    n, _cfg, da = solved
    pv = n.generators_t.p["pv_plant"]
    assert float((pv * da).sum() / pv.sum() / da.mean()) < 1.0


@pytest.mark.live_solve
def test_the_investment_case_is_the_developers_standalone_case(solved):
    n, cfg, _da = solved
    case, res = _case(n, cfg)
    # Every asset is a candidate: the counterfactual site builds and bills nothing.
    assert all(ln.amount == 0.0 for t in case.counterfactual for ln in t.lines)
    assert {a.name for a in case.assets} == {"wind_farm", "pv_plant", "bess"}
    assert "grid" not in {a.name for a in case.assets}                 # the slack (trap 1)
    assert res.metrics["equity_post_tax_npv"] is not None
    assert res.metrics["project_post_tax_irr"] is not None
    assert res.reasons == {} or all(not v for v in res.reasons.values())
    lines = {ln.key: ln for ln in case.templates[0].lines}
    # The PPAs settle; the wholesale import is one line apart from the tariff (trap 2).
    assert lines["contract:ppa_wind:ppa_energy"].amount is not None
    assert lines["contract:ppa_wind:ppa_energy"].price_qty_mwh is not None
    wholesale = [k for k in lines if k.startswith("import_price")]
    assert len(wholesale) <= 1
    assert not any(f.startswith("difference_line_single_class") for f in case.flags)


@pytest.mark.live_solve
def test_no_load_metric_divides_by_zero(solved):
    """G5 trap 4: a site with no load reports load metrics as None, never a crash."""
    n, cfg, _da = solved
    _case_, res = _case(n, cfg)
    for k, v in res.metrics.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert np.isfinite(v), k


# ── section 7 is the one home (no shadow value) ────────────────────────────


@pytest.mark.live_solve
def test_a_section_7_change_moves_the_case_or_the_solve(solved):
    n, cfg, _da = solved
    _c, base = _case(n, cfg)
    # 7d: the WACC moves the project NPV.
    fin = copy.deepcopy(cfg.finance)
    fin["wacc_nominal"] = 0.09
    _c, wacc = _case(n, cfg, fin)
    assert wacc.metrics["project_post_tax_npv"] != base.metrics["project_post_tax_npv"]
    # 7b: the PPA price moves the equity NPV (settled after the solve, no re-solve needed).
    cheap = copy.deepcopy(T.solver_config(TID))
    for c in cheap["commercial"]["contracts"]:
        c["price"] = c["price"] * 0.9
    import dataclasses

    cfg2 = dataclasses.replace(cfg, commercial=cheap["commercial"])
    _c, ppa = _case(n, cfg2)
    assert ppa.metrics["equity_post_tax_npv"] < base.metrics["equity_post_tax_npv"]


@pytest.mark.live_solve
def test_the_network_charge_in_7a_reaches_the_solve(_module_backend):
    """7a: the import tariff's charge is what the LP prices (no Link cost of its own)."""
    from services.commercial import lp_bindings as LP

    hi = copy.deepcopy(T.solver_config(TID))
    hi["commercial"]["import_tariff"]["items"][0]["periods"][0]["rate"] = 0.2   # 200 EUR/MWh
    n, cfg, _da = _solve(hi)
    rows = LP.energy_cost_rows(n, cfg.commercial)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    imp = n.links_t.p0["grid_import"].to_numpy(dtype=float)
    assert rows["energy_import"] == pytest.approx(
        float((w * imp * (M.price_shape("base").to_numpy() + 200.0)).sum()), rel=1e-9)


# ── create from template ───────────────────────────────────────────────────


def test_from_template_mints_the_series_and_binds_every_block(
        client, tmp_path, monkeypatch, tmp_projects_dir):
    import shutil

    from routers import projects as projects_router

    shutil.copytree(TPL_DIR / TID, tmp_path / TID)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "ipp"})
    assert r.status_code == 200, r.text
    assert r.json().get("solver_config_issues", []) == []
    com = client.get("/api/simulation/solver_config").json()["commercial"]
    assert com["export_price_ref"]["id"] == M.SERIES["base"]
    assert com["export_price_ref"]["hash"] != "template-placeholder"
    assert com["import_price_ref"] == com["export_price_ref"]
    assert all(c["reference_price"] == com["export_price_ref"] for c in com["contracts"])
    assert client.get("/api/simulation/finance").json()["status"] == "ok"
    assert client.get("/api/simulation/commercial/value_flows").json()["status"] == "ok"


@pytest.mark.live_solve
def test_the_wacc_gate_passes(solved):
    """GK 1: the LP discount rate (7 %) and the pack-priced assets sit within the gate."""
    from services.finance.engine import wacc_gate

    n, cfg, _da = solved
    case, _res = _case(n, cfg)
    gate = wacc_gate(case)
    assert gate["wacc_vs_discount_rate_consistent"] is True, gate


@pytest.mark.live_solve
def test_solve_ppa_names_the_wind_ppa_and_an_unnamed_call_is_ambiguous(solved):
    """GK 3: two owner-sold PPAs; the template names ppa_wind."""
    from services.finance.engine import solve_ppa
    from services.finance.packs.base import load_pack

    n, cfg, _da = solved
    case, res = _case(n, cfg)
    assert res.metrics["solved_equity_irr_at_target_year"] == pytest.approx(0.08, abs=1e-6)
    import dataclasses

    unnamed = dataclasses.replace(case, inputs=case.inputs.model_copy(update={
        "solve_ppa": case.inputs.solve_ppa.model_copy(update={"contract_id": None})}))
    pack = load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
    assert solve_ppa(unnamed, pack)["solve_ppa_status"] == "solve_ppa_ambiguous_contract"


@pytest.mark.live_solve
def test_a_negative_price_hour_settles_nothing_and_the_plants_curtail(solved):
    """GK 4: at ref < 0 the PPA clause suspends the settlement, and the plants curtail."""
    from services.commercial import contracts as K
    from models.commercial import PpaContract

    n, cfg, da = solved
    neg = np.flatnonzero(da.to_numpy() < 0)
    assert len(neg) > 0
    h = int(neg[0])
    avail = float(n.generators_t.p_max_pu["pv_plant"].iloc[h] * n.generators.at["pv_plant",
                                                                                  "p_nom_opt"])
    assert float(n.generators_t.p["pv_plant"].iloc[h]) <= avail
    wind_c = next(c for c in cfg.commercial["contracts"] if c["id"] == "ppa_wind")
    one = n.snapshots[h:h + 1]
    inputs = K.SettlementInputs(period=None, index=one, weights=np.array([1.0]),
                                generators=n.generators_t.p.loc[one],
                                references={"ppa_wind": (da.loc[one], [])})
    (line,) = K.settle(PpaContract.model_validate(wind_c), inputs)
    assert line.amount == 0.0 and line.quantity_mwh == 0.0
