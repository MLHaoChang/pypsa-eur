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
    from services.commercial import lp_bindings as LP
    from services.commercial.preflight import commercial_findings

    assert "commercial.arbitrage_loop" not in {c for _s, c, *_ in
                                               commercial_findings(n, _cfg.commercial)}
    # The count the solve itself reports (`simultaneous_flow_risk_snapshots`), built as it is.
    com = LP._parse(_cfg.commercial)
    adders = LP._adders(n, com)[0]
    adders["import"] = adders["import"] + LP._import_price(n, com)
    assert LP.circulation_risk_snapshots(n, com, adders, LP._export_price(n, com)) == 0


@pytest.mark.live_solve
def test_curtailment_splits_by_cause_on_the_base_case(solved):
    """Ticket 05 (G6): both causes occur on the base case, and the split sums to the total."""
    from services.results.curtailment import compute_curtailment, curtailment_by_cause

    n, cfg, _da = solved
    out = curtailment_by_cause(n, cfg.commercial)
    assert out["status"] == "ok" and out["export_capacity_mw"] == pytest.approx(70.0)
    hourly = compute_curtailment(n, None, None)
    w = n.snapshot_weightings.generators.to_numpy()
    for i, plant in enumerate(hourly["columns"]):
        total = float(sum(row[i] * w[k] for k, row in enumerate(hourly["data"])))
        assert sum(out["plants"][plant]["by_cause_mwh"].values()) == pytest.approx(total)
    causes = {c: sum(p["by_cause_mwh"][c] for p in out["plants"].values())
              for c in ("export_limit", "negative_price")}
    assert causes["export_limit"] > 0 and causes["negative_price"] > 0, causes


@pytest.mark.live_solve
def test_curtailment_by_cause_survives_a_reload(solved, tmp_path):
    """The split reads the price the dispatch was solved with; the solve's record and the
    price frame travel in network.nc, so a saved and reopened project splits the same."""
    import pypsa

    from services.results.curtailment import curtailment_by_cause

    n, cfg, _da = solved
    n.export_to_netcdf(str(tmp_path / "n.nc"))
    reopened = pypsa.Network(str(tmp_path / "n.nc"))
    assert curtailment_by_cause(reopened, cfg.commercial) == curtailment_by_cause(
        n, cfg.commercial)


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
    from services.results.cfe_score import compute_cfe_score

    n, cfg, _da = solved
    _case_, res = _case(n, cfg)
    for k, v in res.metrics.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert np.isfinite(v), k
    # The load-normalised figure (the 24/7 CFE score, matched / load) is None, with its reason.
    cfe = compute_cfe_score(n, cfg)
    assert cfe["per_period"] and all(p["score"] is None and p["load_mwh"] == 0.0
                                     for p in cfe["per_period"].values())
    assert any(f.startswith("no_site_load") for f in cfe["flags"])


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


def _create(client, name: str = "ipp"):
    """From the shipped template directory, as the app does: `network.nc` is a gitignored
    build artefact, so the route builds the network when it is absent (CI)."""
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": name})
    assert r.status_code == 200, r.text
    return r


def test_from_template_mints_the_series_and_binds_every_block(client, tmp_projects_dir,
                                                             bound_ctx):
    r = _create(client)
    assert r.json().get("solver_config_issues", []) == []
    com = client.get("/api/simulation/solver_config").json()["commercial"]
    assert com["export_price_ref"]["id"] == M.SERIES["base"]
    assert com["export_price_ref"]["hash"] != "template-placeholder"
    assert com["import_price_ref"] == com["export_price_ref"]
    assert all(c["reference_price"] == com["export_price_ref"] for c in com["contracts"])
    assert client.get("/api/simulation/finance").json()["status"] == "ok"
    assert client.get("/api/simulation/commercial/value_flows").json()["status"] == "ok"
    # The minted refs are pinned at create, as a save pins them: none reads stale.
    from services import network_lengths
    from services.library import bundle_pins

    with bound_ctx(client):
        project_dir = network_lengths.active_project_dir()
    pins, issues = bundle_pins.read_pins(project_dir)
    cfg = json.loads((project_dir / T.SOLVER_FILE).read_text())
    assert issues == [] and pins == bundle_pins.collect_pins(cfg) and pins
    flow = client.get("/api/projects/ipp/flow").json()
    assert "library_ref_stale" not in {i["code"] for s in flow["sections"] for i in s["items"]}


def test_a_project_reopened_before_its_first_save_binds_its_prices(client, tmp_projects_dir):
    """The minted series' prices travel in the created project's network.nc: reopened from
    disk before any save (a restart, another tab), it still prices its export (ticket 02)."""
    _create(client)
    assert client.post("/api/simulation/preflight").json()["ok"] is True
    assert client.get("/api/projects/ipp").status_code == 200           # reload from disk
    pre = client.post("/api/simulation/preflight").json()
    assert pre["ok"] is True, pre["issues"]


def test_the_flow_states_at_create_hold_spec_8_2(client, tmp_projects_dir):
    """Issue 03's acceptance (spec §8.2), through GET /flow on the created project. `assumed`
    rows read `warning` until confirmed, as on every template (B1's rule), where the table says
    "complete, with assumed badges"."""
    name = _create(client).json().get("name", "ipp")
    body = client.get(f"/api/projects/{name}/flow").json()
    states = {s["section"]: s["state"] for s in body["sections"]}
    codes = {s["section"]: [i["code"] for i in s["items"]] for s in body["sections"]}
    assert {s: states[s] for s in list(states)[:8]} == {
        "project": "warning", "carriers": "complete", "connection": "warning",
        "loads": "not_needed", "assets": "complete", "profiles": "complete",
        "commercial": "warning", "scenarios": "complete"}, json.dumps(codes, indent=1)
    loads = next(s for s in body["sections"] if s["section"] == "loads")
    assert loads["summary"] == "Generation-only: export through the POI in section 3"
    assert loads["required"] is False
    # No reliability study for a generation-only site; the optimisation verdict is the server's.
    assert body["run_request"] == {"optimisation": True, "study": False}
    assert body["verdicts"]["study"] is None
    assert body["run_ready"] is True
    assert body["run_ready"] == client.post("/api/simulation/preflight").json()["ok"]


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


# ── both modes: every pre-filled value is editable (spec §8.3) ─────────────


def _row(client, path: str, name: str) -> dict:
    return next(r for r in client.get(f"/api/network/{path}").json() if r["name"] == name)


def _edit_component(path: str, name: str, field: str, value):
    """The asset or Link form (section 3 or 5): the same editor in Guided and Expert."""
    def edit(client):
        body = {k: v for k, v in _row(client, path, name).items()
                if not k.endswith("_opt")} | {field: value}
        r = client.put(f"/api/network/{path}/{name}", json=body)
        assert r.status_code == 200, r.text
        return _row(client, path, name)[field]
    return edit


def _edit_commercial(change):
    """A section 7a/7b/7e editor: the commercial block through the config route."""
    def edit(client):
        com = client.get("/api/simulation/solver_config").json()["commercial"]
        want = change(com)
        r = client.put("/api/simulation/solver_config", json={"commercial": com})
        assert r.status_code == 200, r.text
        return want(client.get("/api/simulation/solver_config").json()["commercial"])
    return edit


def _edit_finance(field: str, value):
    """The 7d `FinanceInputs` editor."""
    def edit(client):
        fin = client.get("/api/simulation/finance").json()["finance"] | {field: value}
        r = client.put("/api/simulation/finance", json={"finance": fin})
        assert r.status_code == 200, r.text
        return client.get("/api/simulation/finance").json()["finance"][field]
    return edit


def _ppa(field, value, **also):
    def change(com):
        com["contracts"][0].update({field: value, **also})
        return lambda c: c["contracts"][0][field]
    return _edit_commercial(change), value


def _tariff_rate(com):
    com["import_tariff"]["items"][0]["periods"][0]["rate"] = 0.035
    return lambda c: c["import_tariff"]["items"][0]["periods"][0]["rate"]


def _import_series(client):
    """7a import price: another minted series, picked from the Library list."""
    low = next(s for s in client.get("/api/library/series").json() if s["id"] == M.SERIES["low"])

    def change(com):
        com["import_price_ref"] = low
        return lambda c: c["import_price_ref"]["id"]
    return _edit_commercial(change)(client)


def _parties(client):
    """7e, the party board: its own route (it validates the parties)."""
    got = client.get("/api/simulation/commercial/value_flows").json()
    vf = got["value_flows"]
    vf["participants"].append({"id": "investor", "name": "Co-investor", "role": "investor"})
    r = client.put("/api/simulation/commercial/value_flows", json={"value_flows": vf},
                   headers={"If-Match": got["digest"]})
    assert r.status_code == 200, r.text
    vf = client.get("/api/simulation/commercial/value_flows").json()["value_flows"]
    return [p["id"] for p in vf["participants"]]


#: (spec §8.3 row, editor, expected value read back).
BOTH_MODES = [
    ("export limit 70 MW", _edit_component("links", "grid_export", "p_nom", 80.0), 80.0),
    ("import limit 20 MW", _edit_component("links", "grid_import", "p_nom", 10.0), 10.0),
    ("wind 60 MW (forced size)", _edit_component("generators", "wind_farm", "p_nom_max", 90.0),
     90.0),
    ("PV 50 MWp (forced size)", _edit_component("generators", "pv_plant", "p_nom_min", 40.0),
     40.0),
    ("plant lifetime", _edit_component("generators", "wind_farm", "lifetime", 30.0), 30.0),
    ("battery max 50 MW", _edit_component("storage_units", "bess", "p_nom_max", 30.0), 30.0),
    ("battery duration 2 h", _edit_component("storage_units", "bess", "max_hours", 4.0), 4.0),
    ("battery efficiency", _edit_component("storage_units", "bess", "efficiency_store", 0.95),
     0.95),
    ("battery throughput cost", _edit_component("storage_units", "bess", "marginal_cost", 3.0),
     3.0),
    ("battery power cost (pack)",
     _edit_component("storage_units", "bess", "inv_power_overnight", 150_000.0), 150_000.0),
    ("import price series (G7)", _import_series, M.SERIES["low"]),
    ("import tariff network charge", _edit_commercial(_tariff_rate), 0.035),
    ("PPA price", *_ppa("price", 70.0)),
    ("PPA share 70 %", *_ppa("share", 0.5)),
    ("PPA tenor 10 y", *_ppa("tenor_years", 15)),
    # Physical has no negative-price clause (the model validator): the editor clears it.
    ("PPA settlement financial",
     *_ppa("settlement", "physical", suspend_on_negative_price=None)),
    ("PPA negative-price clause", *_ppa("suspend_on_negative_price", False)),
    ("PPA indexation", *_ppa("indexation_pct_per_year", 1.5)),
    ("parties", _parties, ["ipp", "investor"]),
    ("WACC", _edit_finance("wacc_nominal", 0.08), 0.08),
    ("inflation", _edit_finance("inflation", 0.025), 0.025),
    ("analysis years 25", _edit_finance("analysis_years", 20), 20),
    ("COD", _edit_finance("cod_by_asset", {"bess": "2031-01-01", "pv_plant": "2030-01-01",
                                           "wind_farm": "2030-01-01"}),
     {"bess": "2031-01-01", "pv_plant": "2030-01-01", "wind_farm": "2030-01-01"}),
    ("degradation", _edit_finance("degradation_by_asset", {"bess": 0.0, "pv_plant": 0.01,
                                                           "wind_farm": 0.006}),
     {"bess": 0.0, "pv_plant": 0.01, "wind_farm": 0.006}),
    ("tax pack", _edit_finance("hebesatz_pct", 450.0), 450.0),
]


@pytest.mark.parametrize("label,edit,want", BOTH_MODES, ids=[r[0] for r in BOTH_MODES])
def test_every_prefilled_value_is_editable(label, edit, want, client, tmp_projects_dir):
    """Spec §8.3: each value is edited through the route its editor writes (one editor
    component serves Guided and Expert, #444 §4) and reads back as set. The locks of §8.3
    (the meter roles, the grid slack, a baseload share) are not in the table."""
    _create(client)
    got = edit(client)
    assert got == pytest.approx(want) if isinstance(want, float) else got == want, label


@pytest.mark.live_solve
def test_each_ppa_line_is_its_two_legs(solved):
    """Ticket 05 (GK): each financial PPA's difference line is its price leg plus its
    reference leg, every year. Not a byte pin of the lines: they come from a solve, which
    differs in the last bits by machine. The helper's arithmetic is pinned on fixed cases
    in test_operating_pin.py."""
    from services.finance.cashflow import build_operating
    from services.finance.timeline import build_timeline

    n, cfg, _da = solved
    case, _res = _case(n, cfg)
    op = build_operating(case, build_timeline(case))
    assert sorted(op.legs) == sorted(f"contract:{cid}:ppa_energy" for cid, _p in M.PPA.values())
    for key, (price_leg, reference_leg) in op.legs.items():
        np.testing.assert_allclose(price_leg + reference_leg, op.lines[key], rtol=1e-12,
                                   atol=1e-9)
        assert (price_leg[op.tl.operating_mask()] > 0).any()


@pytest.mark.live_solve
def test_revenue_by_stream_on_the_base_case(solved):
    """Ticket 05 (G6): the PPAs show as PPA sales and market sales, nothing lands in Other,
    and every year the rows sum to the operating cash; the report's project payload has them."""
    from services.finance.report import assemble_finance_sections
    from services.finance.revenue_streams import revenue_by_stream

    n, cfg, _da = solved
    case, res = _case(n, cfg)
    out = revenue_by_stream(res.op)
    assert out["other_keys"] == []
    rows = {r["id"]: np.array(r["values"], dtype=float) for r in out["rows"]}
    np.testing.assert_allclose(sum(rows.values()), res.op.revenue - res.op.costs,
                               rtol=1e-9, atol=1e-6)
    on = res.op.tl.operating_mask()
    tenor = np.flatnonzero(on)[:M.PPA_TENOR]                  # the PPAs end after their tenor
    assert (rows["ppa"][tenor] > 0).all() and not rows["ppa"][np.flatnonzero(on)[M.PPA_TENOR:]].any()
    assert (rows["merchant"][on] > 0).all()
    assert not rows["cfd"].any() and not rows["other"].any()
    assert (rows["om"][on] < 0).all()
    payload = assemble_finance_sections(res, case).sections["project"].payload
    assert payload["revenue_streams"]["rows"][0]["id"] == "ppa"
