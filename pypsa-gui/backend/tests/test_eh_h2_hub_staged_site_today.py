"""The staged H2 hub on the site-today basis, end to end (owner decision
2026-10-15 (a); the gatekeeper's checklist for the grey-H2 slice).

The template is solved with a reference purchase (grey H2) and a CO2 price
by year. The baseline re-solve keeps the existing 30 MW electrolyser (about
20 MW of H2) and removes the per-period candidates, so it meets the 10 and
18 MW offtake of 2027 and 2030 and sheds part of the 25 MW of 2035. The
counterfactual buys exactly that shed at the reference price, and its CO2 is
priced per operating year; the engine's yearly cash is checked by hand.
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

TID = "eh_h2_hub_staged"
PRICE_KG, CO2_KG, LHV = 2.0, 0.010, 33.33
CO2 = {"eur_per_t_by_year": {"2027": 80.0, "2030": 120.0}, "money_year": 2020,
       "source": "test: an ETS path"}


@pytest.fixture
def h2_site_today(reset_backend, monkeypatch):
    import routers.results as R
    import routers.simulation as sim_router
    from models.finance import FinanceInputs
    from routers.projects import _solver_config_from_dict
    from services.finance.engine import run_case
    from services.pypsa_service import PyPSAService
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    monkeypatch.setattr(I, "load_project_island", lambda: None)
    raw = T.solver_config(TID)
    raw["commercial"]["reference_purchases"] = [
        {"load": "h2_offtake", "price_eur_per_kg": PRICE_KG, "lhv_kwh_per_kg": LHV,
         "co2_t_per_kg": CO2_KG, "source": "test: grey H2 by SMR"}]
    raw["commercial"]["co2_price"] = CO2
    install_network_into_backend(T.BUILDERS[TID]())
    cfg = _solver_config_from_dict(raw)
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    Bl.clear_cache()
    base = Bl.solve_baseline(n, cfg, baseline_plan(n, cfg))
    fin = FinanceInputs.model_validate(cfg.finance)
    case = build_finance_case(n, cfg, fin, result_df=R._result_df,
                              lost_load=state.get("last_lost_load"), baseline=base)
    yield n, state, base, fin, case, run_case(case)
    Bl.clear_cache()


def _period_kg(n, base, state, period) -> float:
    """By hand: the baseline's H2 shed over the actual's in `period`, in kg."""
    def shed(net, ll):
        s = (ll or {}).get("lost_load_t")
        if s is None or "h2_offtake" not in s.columns:
            return np.zeros(len(net.snapshots))
        return np.clip(s["h2_offtake"].reindex(net.snapshots).fillna(0.0).to_numpy(float), 0, None)
    m = n.snapshots.get_level_values(0) == period
    w = n.snapshot_weightings.objective.to_numpy(float)
    diff = np.clip(shed(base.network, base.lost_load) - shed(n, state.get("last_lost_load")),
                   0.0, None)
    return float((w[m] * diff[m]).sum()) * 1000.0 / LHV


@pytest.mark.live_solve
def test_the_staged_h2_hub_buys_its_unmet_h2_and_prices_its_co2(h2_site_today):
    n, state, base, fin, case, r = h2_site_today
    # (1) The baseline runs without the new electrolysers and keeps the 30 MW one.
    removed = {name for _c, name, _k in base.plan.removed}
    assert {f"electrolyser_new_{y}" for y in (2027, 2030, 2035)} & removed
    assert "electrolyser" not in removed
    assert "counterfactual_basis:site_today" in case.flags
    assert "counterfactual_buys:h2_offtake" in case.flags
    assert not any(f.startswith(("counterfactual_not_established", "co2_price_not_applied",
                                 "reference_purchases_not_applied")) for f in case.flags)
    kg = {p: _period_kg(n, base, state, p) for p in (2027, 2030, 2035)}
    assert kg[2027] == pytest.approx(0.0, abs=1.0) and kg[2030] == pytest.approx(0.0, abs=1.0)
    assert kg[2035] > 1e5                                 # 2035's 25 MW exceeds the ~20 MW
    # Never priced at VoLL as well: the H2 offtake is not an electric site load.
    years = list(r.op.tl.years)
    cf = r.op_counterfactual.lines
    assert "counterfactual:shed_at_voll" not in cf or all(
        v == 0 for v in cf["counterfactual:shed_at_voll"])
    # (2) The purchase: base-year (2027) money, escalating as fuel at 2 %.
    base_year = case.base_year
    esc = fin.escalation["fuel"]
    for y in (2035, 2036):
        want = -kg[2035] * PRICE_KG * (1 + esc) ** (y - base_year)
        assert cf["counterfactual:purchase:h2_offtake"][years.index(y)] == pytest.approx(
            want, rel=1e-6), y
    for y in (2027, 2031):
        assert cf["counterfactual:purchase:h2_offtake"][years.index(y)] == pytest.approx(
            0.0, abs=1.0)
    # The CO2: real prices in 2020 money, escalated at the case's inflation; 2035
    # and 2036 take the 2030 price (the last listed year at or before them).
    for y in (2035, 2036):
        want = -kg[2035] * CO2_KG * 120.0 * (1 + fin.inflation) ** (y - 2020)
        assert cf["co2:purchase:h2_offtake"][years.index(y)] == pytest.approx(want, rel=1e-6), y
    # Purchase CO2 sits on the counterfactual side only.
    assert not any(ln.key.startswith("co2:purchase") for t in case.templates for ln in t.lines)


@pytest.mark.live_solve
def test_the_report_states_the_avoided_co2_cost(h2_site_today):
    from services.finance.report import _counterfactual_block

    _n, _state, _base, _fin, case, r = h2_site_today
    block = _counterfactual_block(r, case)
    assert block["counterfactual_basis"] == "site_today"
    assert block["co2_priced"] is True
    avoided = block["avoided_co2_cost"]
    assert avoided is not None and sum(avoided) > 0
