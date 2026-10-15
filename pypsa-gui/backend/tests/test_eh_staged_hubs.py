"""The staged Industrial Hydrogen Hub and Island Microgrid (generic-site E2E,
H1/M1: template data only).

Each is its single-year template over 2027/2030/2035 with one candidate row
per period, the existing hub sunk and finance inputs shipped. The H2 hub's
investment case is refused today (its H2 offtake has no source without the
investment: the grey-H2 counterfactual is a later engine slice) and the
microgrid ships no commercial config (no grid comparison: the diesel-only
baseline is a later engine slice); both say so in their notes and ledger.
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
    m = T.build_eh_microgrid()
    assert "diesel_new" in m.generators.index and len(m.investment_periods) == 0


def test_the_blocks_validate_and_say_what_is_missing():
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs

    h2 = T.solver_config("eh_h2_hub_staged")
    assert CommercialConfig.model_validate(h2["commercial"]).poc_link == "grid_import"
    fin = FinanceInputs.model_validate(h2["finance"])
    assert fin.cod_by_asset == {a: date(2027, 1, 1) for a in H.H2_EXISTING}
    mg = T.solver_config("eh_microgrid_staged")
    assert "commercial" not in mg and mg["island_constraints"] is True
    FinanceInputs.model_validate(mg["finance"])
    for tid in IDS:
        items = {r["item"]: r for r in T.TEMPLATE_META[tid]["ledger"]}
        assert "investment case" in items
        assert any("investment case is not available yet" in s
                   for s in T.TEMPLATE_META[tid]["study_notes"])


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
    from services.results.finance_case import FinanceRefused, build_finance_case

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
    # The investment case is refused as the notes say, until the grey-H2
    # counterfactual lands (the engine slice).
    try:
        case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                                  result_df=R._result_df, lost_load=state.get("last_lost_load"))
    except FinanceRefused as exc:
        assert "counterfactual_not_established" in str(exc.code)
    else:
        assert "counterfactual_not_established:owner_conversion_load" in case.flags


@pytest.mark.live_solve
def test_the_microgrid_plans_for_its_growing_load_with_the_island_rows(reset_backend,
                                                                     monkeypatch, tmp_path):
    n, _cfg, _state = _solve("eh_microgrid_staged", monkeypatch, tmp_path)
    built = sum(float(df.at[c, "p_nom_opt"]) for df in (n.generators, n.storage_units)
                for c in df.index if "_new_" in c)
    assert built > 0.1
    # the tie stays open: the island serves itself
    assert float(n.links_t.p0["subsea_tie"].abs().max()) == pytest.approx(0.0, abs=1e-6)
