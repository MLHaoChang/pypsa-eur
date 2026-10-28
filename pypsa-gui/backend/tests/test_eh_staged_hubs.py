"""The staged Industrial Hydrogen Hub and Island Microgrid (generic-site E2E,
H1/M1: template data only).

Each is its single-year template over 2027/2030/2035 with one candidate row
per period, the existing hub sunk and finance inputs shipped. The H2 hub
ships a grey-H2 reference purchase and a CO2 price (synthetic demo values):
its case compares against the hub as it stands today, which buys grey H2
whenever that is cheaper than running its electrolyser (#312). The
microgrid's case compares against the island as it stands today (owner,
M2e (a)): the open tie is its PoC, with no tariff, and the investment-case
route re-solves the island without the candidates (#302).
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading
from datetime import date

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_staged_hubs as H  # noqa: E402
import eh_templates as T  # noqa: E402

IDS = ("eh_h2_hub_staged", "eh_microgrid_staged")
EXISTING = {"eh_h2_hub_staged": H.H2_EXISTING, "eh_microgrid_staged": H.MG_EXISTING}


@pytest.fixture(scope="module", params=IDS)
def staged(request):
    return request.param, T.BUILDERS[request.param]()


def _candidates(n):
    return [c for frame in ("generators", "storage_units", "links")
            for c in getattr(n, frame).index if "_new_" in c]


def test_three_periods_each_a_year_of_hours(staged):
    _tid, n = staged
    assert list(n.investment_periods) == list(H.YEARS)
    assert n.investment_period_weightings["years"].tolist() == [3.0, 5.0, 10.0]
    for y in H.YEARS:
        assert n.snapshot_weightings.objective.loc[y].sum() == pytest.approx(8760.0)


def test_one_candidate_per_period_that_outlives_the_horizon(staged):
    _tid, n = staged
    cands = _candidates(n)
    assert cands
    for c in cands:
        y = int(c.rsplit("_", 1)[1])
        df = next(getattr(n, f) for f in ("generators", "storage_units", "links")
                  if c in getattr(n, f).index)
        assert df.at[c, "build_year"] == y and bool(df.at[c, "p_nom_extendable"])
        assert y + float(df.at[c, "lifetime"]) >= H.HORIZON_END, c
        assert float(df.at[c, "overnight_cost"]) > 0 or c.startswith("bess"), c


def test_the_existing_hub_is_sunk_and_in_service_in_every_period(staged):
    tid, n = staged
    cls = {"generators": "Generator", "storage_units": "StorageUnit", "links": "Link",
           "stores": "Store"}
    for a in EXISTING[tid]:
        frame = next(f for f in ("generators", "storage_units", "links", "stores")
                     if a in getattr(n, f).index)
        assert getattr(n, frame).at[a, "overnight_cost"] == 0.0, a
        for p in H.YEARS:
            assert bool(n.components[cls[frame]].get_active_assets(p).loc[a]), (a, p)


def test_storage_cycles_within_each_period(staged):
    _tid, n = staged
    if len(n.stores):
        assert n.stores["e_cyclic_per_period"].all() and n.stores["e_cyclic"].all()
    assert n.storage_units["cyclic_state_of_charge_per_period"].all()


def test_the_load_grows_by_period(staged):
    _tid, n = staged
    peaks = [n.loads_t.p_set.loc[y].sum(axis=1).max() for y in H.YEARS]
    assert peaks[0] < peaks[1] < peaks[2]


def test_the_single_year_templates_are_unchanged():
    n = T.build_eh_h2_hub()
    assert "wind_new" in n.generators.index and bool(n.links.at["fuel_cell", "p_nom_extendable"])
    # Study harness 19: the single-year hub's existing rows are sunk for its
    # investment case (overnight 0, in service), as the plain microgrid's.
    # None is extendable, so the solve is unchanged.
    for name in T.H2_HUB_EXISTING:
        df = next(df for df in (n.generators, n.storage_units, n.links, n.stores)
                  if name in df.index)
        assert df.at[name, "overnight_cost"] == 0.0 and df.at[name, "lifetime"] == float("inf")
        assert not bool(df.at[name, "e_nom_extendable" if df is n.stores else "p_nom_extendable"])
    m = T.build_eh_microgrid()
    assert "diesel_new" in m.generators.index and len(m.investment_periods) == 0


def test_the_single_year_h2_hub_ships_the_staged_hubs_case_on_its_own_network():
    """Study harness 19: the staged hub's grey-H2 purchase, CO2 path, tariff
    and finance on one period; every owned row but the PoC has the one COD.
    The offtake stays demand: nothing makes it tunable (design §6.0)."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs

    n = T.build_eh_h2_hub()
    cfg = T.solver_config("eh_h2_hub")
    com = CommercialConfig.model_validate(cfg["commercial"])
    staged = CommercialConfig.model_validate(T.solver_config("eh_h2_hub_staged")["commercial"])
    assert com.poc_link == "grid_import" and com.timezone == "UTC"
    assert com.reference_purchases == staged.reference_purchases
    assert com.co2_price == staged.co2_price and com.import_tariff == staged.import_tariff
    fin = FinanceInputs.model_validate(cfg["finance"])
    owned = {o["asset_id"] for o in cfg["commercial"]["value_flows"]["asset_owners"]}
    assert owned - {"grid_import"} == set(fin.cod_by_asset)
    assert set(fin.cod_by_asset.values()) == {date(2030, 1, 1)}
    assert (fin.financial_close, fin.analysis_years, fin.annualise) == (date(2029, 1, 1), 15, False)
    assert float(n.snapshot_weightings.objective.sum()) == pytest.approx(8760.0)
    # The offtake is fixed demand; the block names it only as what the
    # counterfactual buys.
    assert n.loads.at["h2_offtake", "p_set"] == 25.0
    assert "h2_offtake" not in json.dumps({**cfg["commercial"], "reference_purchases": None})
    items = {r["item"] for r in T.TEMPLATE_META["eh_h2_hub"]["ledger"]}
    assert {"investment case", "grey H2 reference price", "CO2 price"} <= items


def test_the_blocks_validate_and_say_what_is_missing():
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs

    h2 = T.solver_config("eh_h2_hub_staged")
    h2c = CommercialConfig.model_validate(h2["commercial"])
    assert h2c.poc_link == "grid_import"
    (grey,) = h2c.reference_purchases
    assert (grey.load, grey.price_eur_per_kg, grey.co2_t_per_kg) == ("h2_offtake", 2.0, 0.010)
    assert h2c.co2_price.eur_per_t_by_year == {2027: 80.0, 2030: 120.0}
    assert h2c.co2_price.money_year == 2020 and h2c.co2_price.grid_t_per_mwh is None
    fin = FinanceInputs.model_validate(h2["finance"])
    assert fin.cod_by_asset == {a: date(2027, 1, 1) for a in H.H2_EXISTING}
    mg = T.solver_config("eh_microgrid_staged")
    com = CommercialConfig.model_validate(mg["commercial"])
    assert com.poc_link == "subsea_tie" and com.import_tariff is None
    assert com.export_link is None and mg["island_constraints"] is True
    fin = FinanceInputs.model_validate(mg["finance"])
    assert fin.cod_by_asset == {a: date(2027, 1, 1) for a in H.MG_EXISTING}
    from services.library.defaults_pack.loader import load_defaults_pack
    pack_year = load_defaults_pack(T.TEMPLATE_PACK_VERSION).finance.currency_year
    for tid in IDS:
        items = {r["item"]: r for r in T.TEMPLATE_META[tid]["ledger"]}
        assert "investment case" in items
        assert "real base-year EUR" in items["money year"]["value"]
        assert f"pack prices ({pack_year} EUR)" in items["money year"]["value"]
        assert "2020 money" not in items["finance inputs"]["value"]
    h2_ledger = {r["item"]: r for r in T.TEMPLATE_META["eh_h2_hub_staged"]["ledger"]}
    for item in ("grey H2 reference price", "CO2 price"):
        assert h2_ledger[item]["source"] == "synthetic"
        assert "synthetic demo" in h2_ledger[item]["note"]
    assert "2027" in h2_ledger["grey H2 reference price"]["note"]
    assert "converted at the case's 2 % inflation" in h2_ledger["CO2 price"]["note"]
    assert h2_ledger["grid CO2 intensity"]["value"] == "not set"
    mg_case = {r["item"]: r for r in T.TEMPLATE_META["eh_microgrid_staged"]["ledger"]}
    assert mg_case["investment case"]["value"] == (
        "compared with the island as it stands today: the existing diesels, PV, wind and "
        "battery, without the planned candidates, and no grid")


def test_the_microgrid_island_sidecar_is_valid():
    from services.adequacy.island_config import validate

    isl = validate(T.ISLAND_CONFIG["eh_microgrid_staged"])
    assert isl["requirements"]["scenarios"] == ["seamless"]
    n = T.BUILDERS["eh_microgrid_staged"]()
    for u in T.ISLAND_CONFIG["eh_microgrid_staged"]["units"]:
        assert u in n.generators.index or u in n.storage_units.index or u in n.loads.index, u


def test_from_template_creates_both_with_their_blocks(client, tmp_path, monkeypatch,
                                                     tmp_projects_dir):
    import shutil

    from routers import projects as projects_router

    for f in ("eh_templates.py", "eh_staged_hubs.py", "eh_site.py"):
        shutil.copy(TPL_DIR / f, tmp_path / f)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    for tid in IDS:
        src = tmp_path / tid
        T.write_sidecars(src, tid)                       # sidecars, no .nc: built on demand
        r = client.post(f"/api/projects/from_template/{tid}", params={"name": tid})
        assert r.status_code == 200, r.text
        assert r.json().get("solver_config_issues", []) == [], tid
        meta = client.get(f"/api/projects/{tid}/eh_template").json()
        assert meta["id"] == tid


# ── the plan solves ─────────────────────────────────────────────────────────

def _solve(tid, monkeypatch, tmp_path):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.pypsa_service import PyPSAService
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    if tid in T.ISLAND_CONFIG:
        (tmp_path / SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG[tid]))
        isl = load_island_config(tmp_path / SIDECAR_NAME)
        isl["cuts"] = []
        monkeypatch.setattr(I, "load_project_island", lambda: isl)
    install_network_into_backend(T.BUILDERS[tid]())
    cfg = _solver_config_from_dict(T.solver_config(tid))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (tid, status, cond)
    return n, cfg, state


@pytest.mark.live_solve
def test_the_h2_hub_plans_electrolysis_for_the_growing_offtake(reset_backend, monkeypatch,
                                                              tmp_path):
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case

    n, cfg, state = _solve("eh_h2_hub_staged", monkeypatch, tmp_path)
    added = sum(float(n.links.at[f"electrolyser_new_{y}", "p_nom_opt"]) for y in H.YEARS)
    assert added > 0.1                                # 30 MW cannot make 25 MW of H2
    # The H2 balance closes within each period: no energy is carried between
    # periods, so the period's electrolysis makes its own offtake (and what
    # the fuel cell burns). Hours, not weights: the store's step is one hour.
    eff = n.links.efficiency
    els = [c for c in n.links.index if c.startswith("electrolyser")]
    for p in H.YEARS:
        made = sum(float((n.links_t.p0[c].loc[p] * eff[c]).sum()) for c in els)
        used = float(n.loads_t.p_set["h2_offtake"].loc[p].sum()) \
            + float(n.links_t.p0["fuel_cell"].loc[p].sum())
        assert made == pytest.approx(used, rel=1e-6, abs=1e-3), p
        e = n.stores_t.e["h2_storage"].loc[p]
        p_store = n.stores_t.p["h2_storage"].loc[p]
        assert float(e.iloc[-1]) == pytest.approx(float(e.iloc[0] + p_store.iloc[0]), abs=1e-3)
    # The existing fuel cell can run (it is in service).
    assert n.links.at["fuel_cell", "p_nom"] == 10.0
    # The investment case from the template's own shipped config, on the
    # site-today basis (as the IC route): the hub as it stands today buys grey
    # H2 whenever that is cheaper (#312). The demo's figures, pinned (the
    # gatekeeper's plan review): re-derive them if the baseline or the solver
    # changes, never loosen the tolerance.
    Bl.clear_cache()
    base = Bl.solve_baseline(n, cfg, baseline_plan(n, cfg))
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    Bl.clear_cache()
    assert "counterfactual_basis:site_today" in case.flags
    assert "counterfactual_buys:h2_offtake" in case.flags
    assert not any(f.startswith("counterfactual_not_established") for f in case.flags)
    bn = base.network
    w = bn.snapshot_weightings.objective
    bought = {p: float((w.loc[p] * bn.generators_t.p["reference_supply:h2_offtake"].loc[p]).sum())
              for p in (2030, 2035)}
    assert bought[2030] == pytest.approx(51_041.0, rel=0.05)
    assert bought[2035] == pytest.approx(112_361.0, rel=0.05)
    r = run_case(case)
    # Re-measured 2026-10-21 (GS-2b): the electrolysers cost the row's price per MW of electrical
    # input, not x0.68; the demo NPV was +14.6 M EUR, is -2.65 M (same tolerance).
    assert r.metrics["project_pre_tax_npv"] == pytest.approx(-2_645_035.0, rel=0.01)


def _h2_case(n, cfg, state):
    """(baseline, case) of a solved H2 hub, as the IC route builds them."""
    import routers.results as R
    from models.finance import FinanceInputs
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case

    Bl.clear_cache()
    base = Bl.solve_baseline(n, cfg, baseline_plan(n, cfg))
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    Bl.clear_cache()
    return base, case


@pytest.mark.live_solve
def test_the_single_year_h2_hub_case_buys_grey_h2_in_its_counterfactual(reset_backend,
                                                                       monkeypatch, tmp_path):
    """Study harness 19: the single-year hub ships the staged hub's grey-H2
    basis, so its case compares with the hub as it stands today, which buys
    grey H2 whenever that is cheaper than running its own electrolyser."""
    from services.finance.engine import run_case

    n, cfg, state = _solve("eh_h2_hub", monkeypatch, tmp_path)
    base, case = _h2_case(n, cfg, state)
    assert "counterfactual_basis:site_today" in case.flags
    assert "counterfactual_buys:h2_offtake" in case.flags
    assert not any(f.startswith("counterfactual_not_established") for f in case.flags)
    bn = base.network
    bought = float((bn.snapshot_weightings.objective
                    * bn.generators_t.p["reference_supply:h2_offtake"]).sum())
    assert bought == pytest.approx(64_120.0, rel=0.05)              # MWh of H2 a year
    # The demo's NPV, pinned (measured 2026-10-28, live solve): re-derive it if the
    # baseline or the solver changes, never loosen the tolerance.
    assert run_case(case).metrics["project_pre_tax_npv"] == pytest.approx(7_823_212.0, rel=0.01)


@pytest.mark.live_solve
def test_the_microgrid_plans_for_its_growing_load_with_the_island_rows(reset_backend,
                                                                     monkeypatch, tmp_path):
    n, _cfg, _state = _solve("eh_microgrid_staged", monkeypatch, tmp_path)
    built = sum(float(df.at[c, "p_nom_opt"]) for df in (n.generators, n.storage_units)
                for c in df.index if "_new_" in c)
    assert built > 0.1
    # the tie stays open: the island serves itself
    assert float(n.links_t.p0["subsea_tie"].abs().max()) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.live_solve
def test_the_microgrid_case_compares_with_the_island_as_it_stands_today(reset_backend,
                                                                       monkeypatch, tmp_path):
    """Owner, M2e (a): the baseline re-solve keeps the existing diesels, PV,
    wind and battery and removes the candidates the plan builds; nothing is
    shed, so no part of the NPV is the value of lost load; the avoided cost
    is the existing diesels' running cost."""
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case

    n, cfg, state = _solve("eh_microgrid_staged", monkeypatch, tmp_path)
    Bl.clear_cache()
    plan = baseline_plan(n, cfg)
    assert plan is not None and plan.needed
    removed = {name for _c, name, _k in plan.removed}
    built = {c for df in (n.generators, n.storage_units) for c in df.index
             if "_new_" in c and float(df.at[c, "p_nom_opt"]) > 1e-6}
    assert built and built <= removed and not removed & set(H.MG_EXISTING)
    base = Bl.solve_baseline(n, cfg, plan)
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    Bl.clear_cache()
    assert "counterfactual_basis:site_today" in case.flags
    assert not any(f.startswith("counterfactual_not_established") for f in case.flags)
    keys = {ln.key for t in case.counterfactual for ln in t.lines}
    assert "counterfactual:shed_at_voll" not in keys
    assert {f"asset:opex:Generator:diesel_{i}" for i in (1, 2, 3)} <= keys
    r = run_case(case)
    # The demo's figure, pinned (measured 2026-10-16 with the seamless event
    # rows; 43,857,725 before them): re-derive it if the plan or the solver
    # changes, never loosen the tolerance.
    assert r.metrics["project_pre_tax_npv"] == pytest.approx(43_231_055.0, rel=0.01)

