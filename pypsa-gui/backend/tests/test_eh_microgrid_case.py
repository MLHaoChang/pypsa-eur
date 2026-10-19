"""The single-year Island Microgrid's own investment case.

The plain template ships a commercial config (the open subsea tie as the
PoC, no tariff, single-owner value flows) and finance inputs built on its
own network, so the Investment check gives an NPV instead of opening on an
empty "Site connection" form. One representative week weighted to 8,760 h
stands for each of the 15 operating years (2030-2044, the staged
templates' 2045 horizon end); every asset has one COD (2030-01-01). The case
compares against the island as it stands today (the staged microgrid's
basis, owner M2e (a)): the existing diesels, PV, wind and battery, without
the candidates, re-solved.
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading
from datetime import date

import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TID = "eh_microgrid"
_FRAMES = {"Generator": "generators", "StorageUnit": "storage_units", "Link": "links"}


def _owned(n, cfg):
    """(component, name) of every row the value flows give the owner, read
    from the plain network (never a hard-coded list)."""
    owners = cfg["commercial"]["value_flows"]["asset_owners"]
    rows = [(o["component"], o["asset_id"]) for o in owners]
    for comp, name in rows:
        assert name in getattr(n, _FRAMES[comp]).index, (comp, name)
    return rows


def test_the_plain_microgrid_ships_a_case_on_its_own_network():
    n = T.BUILDERS[TID]()
    cfg = T.solver_config(TID)
    poc = cfg["commercial"]["poc_link"]
    assert poc == "subsea_tie"
    assert cfg["commercial"]["timezone"] == "UTC"
    assert cfg["commercial"].get("import_tariff") is None
    fin = cfg["finance"]
    owned = _owned(n, cfg)
    names = {name for _c, name in owned}
    assert names >= set(n.generators.index) - {"mainland_supply"}
    assert names >= set(n.storage_units.index)
    assert names >= set(n.links.index)
    # Every owned row but the PoC meter Link (not an investment) has a COD,
    # all on one date: a single period has no stage.
    for _comp, name in owned:
        if name != poc:
            assert fin["cod_by_asset"].get(name) == "2030-01-01", name
    assert poc not in fin["cod_by_asset"]
    # Every owned generator and battery has a degradation key (the engine
    # never reads a missing one as 0).
    for comp, name in owned:
        if comp in ("Generator", "StorageUnit"):
            assert name in fin["degradation_by_asset"], name
    assert fin["financial_close"] == "2029-01-01"
    assert fin["analysis_years"] == 15
    assert fin["annualise"] is False
    # The week stands for a year (factor 1, no annualising).
    assert float(n.snapshot_weightings.objective.sum()) == pytest.approx(8760.0)
    assert {ts.year for ts in n.snapshots} == {2030}
    # Island rows stay off: the template's own solve is unchanged.
    assert not cfg.get("island_constraints")


def test_the_existing_island_is_sunk_and_in_service():
    """Every owned row that is not a candidate (not extendable) and not the
    PoC is sunk: a typed overnight cost of 0, in service throughout."""
    n = T.BUILDERS[TID]()
    cfg = T.solver_config(TID)
    poc = cfg["commercial"]["poc_link"]
    existing = []
    for comp, name in _owned(n, cfg):
        df = getattr(n, _FRAMES[comp])
        if name == poc or bool(df.at[name, "p_nom_extendable"]):
            continue
        existing.append(name)
        assert df.at[name, "overnight_cost"] == 0.0, name
        assert df.at[name, "lifetime"] == float("inf"), name
    assert sorted(existing) == sorted(T.MG_EXISTING)
    # The PoC meter Link stays untyped: it is not an investment.
    assert pd.isna(n.links.at[poc, "overnight_cost"])


@pytest.mark.live_solve
@pytest.mark.slow
def test_the_plain_microgrid_case_gives_an_npv_against_the_island_today(reset_backend):
    import routers.results as R
    import routers.simulation as sim_router
    from models.finance import FinanceInputs
    from routers.projects import _solver_config_from_dict
    from services.finance.breakeven import island_breakeven
    from services.finance.engine import run_case
    from services.pypsa_service import PyPSAService
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    install_network_into_backend(T.BUILDERS[TID]())
    cfg = _solver_config_from_dict(T.solver_config(TID))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    Bl.clear_cache()
    plan = baseline_plan(n, cfg)
    assert plan is not None and plan.needed
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
    assert case.base_year == 2030 and case.cod == date(2030, 1, 1)
    built = {c: float(df.at[c, "p_nom_opt"]) for df in (n.generators, n.storage_units)
             for c in T.MG_CANDIDATES if c in df.index}
    assert built["bess_new"] == pytest.approx(7.519, rel=0.01)
    assert built["diesel_new"] == pytest.approx(0.0, abs=1e-6)
    r = run_case(case)
    years = list(r.op.tl.years)
    assert (years[0], years[-1]) == (2029, 2044)           # close, then 15 operating years
    # The battery inverter (10 years) is re-bought once, in 2039: the cash then
    # changes sign twice, which the IRR flags.
    assert [y for y, v in zip(years, r.op.replacement) if v] == [2039]
    assert "project_pre_tax:irr_multiple_sign_changes" in r.flags
    # Island rows off: no island capability to break even on.
    assert island_breakeven(r, case) is None
    # The demo's figure, pinned (measured 2026-10-19): re-derive it if the
    # plan or the solver changes, never loosen the tolerance.
    assert r.metrics["project_pre_tax_npv"] == pytest.approx(4_588_396.0, rel=0.01)
