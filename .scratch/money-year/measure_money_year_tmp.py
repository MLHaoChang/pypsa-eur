"""TEMPORARY (never committed): before/after headline table for money-year issue 01.

Writes one JSON line per case to $MY_OUT.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import sys
import threading

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_site as S  # noqa: E402
import eh_templates as T  # noqa: E402

OUT = os.environ.get("MY_OUT", "/dev/null")


def _emit(name, **kw):
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"case": name, **kw}, default=str) + "\n")


def _metrics(n, case, r):
    m = r.metrics
    return {"npv": m.get("project_pre_tax_npv"), "lcoe_nominal": m.get("lcoe_nominal_per_mwh"),
            "lcoe_real": m.get("lcoe_real_per_mwh"), "objective": float(n.objective),
            "currency_year": case.inputs.currency_year, "base_year": case.base_year,
            "flags": sorted(f for f in r.flags if "currency" in f or "money" in f)}


def _solve(n_in, cfg_dict, bind=None):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n_in)
    n = PyPSAService.get_network()
    if bind:
        bind(n, cfg_dict)
    cfg = _solver_config_from_dict(cfg_dict)
    sim_router._state["solver_config"] = cfg
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    return n, cfg, state


def _case(n, cfg, state, *, baseline=True):
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.finance.packs.base import load_pack
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case

    base = None
    Bl.clear_cache()
    if baseline:
        plan = baseline_plan(n, cfg)
        if plan is not None and plan.needed:
            base = Bl.solve_baseline(n, cfg, plan)
    try:
        case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                                  result_df=R._result_df, lost_load=state.get("last_lost_load"),
                                  baseline=base)
    finally:
        Bl.clear_cache()
    pack = (load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
            if case.inputs.tax_pack_id else None)
    return case, run_case(case, pack)


@pytest.mark.parametrize("tid", list(T.BUILDERS))
def test_template(tid, reset_backend, monkeypatch):
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.solver import island as I

    side = BACKEND / "project_templates" / tid / SIDECAR_NAME
    if side.is_file():
        isl = load_island_config(side)
        isl["cuts"] = []
        monkeypatch.setattr(I, "load_project_island", lambda: isl)
    bind = None
    if tid == "eh_ipp_hybrid":
        from tests.test_eh_ipp import _bind_prices as bind
    cfg_dict = json.loads(json.dumps(T.solver_config(tid)))
    n, cfg, state = _solve(T.BUILDERS[tid](), cfg_dict, bind)
    if cfg.finance is None:
        _emit(tid, objective=float(n.objective), finance=None)
        return
    case, r = _case(n, cfg, state, baseline=tid != "eh_ipp_hybrid")
    _emit(tid, **_metrics(n, case, r))


@pytest.mark.parametrize("site_type", S.SITE_TYPES)
def test_site(site_type, reset_backend, tmp_path, monkeypatch):
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.solver import island as I

    p = S.site_params({"site_type": site_type})
    (tmp_path / SIDECAR_NAME).write_text(json.dumps(S.island_config(p)))
    isl = load_island_config(tmp_path / SIDECAR_NAME)
    isl["cuts"] = []
    monkeypatch.setattr(I, "load_project_island", lambda: isl)
    n, cfg, state = _solve(S.build_eh_site(p), S.solver_config(p))
    case, r = _case(n, cfg, state, baseline=False)
    _emit(f"eh_site:{site_type}", **_metrics(n, case, r))


def test_redundancy_pricing(reset_backend):
    from services.adequacy import redundancy as R
    from services.library.defaults_pack.pricing import price_from_pack

    n = T.BUILDERS["eh_microgrid"]()
    out = {"version": R.REDUNDANCY_PACK_VERSION}
    for comp, tech in (("generators", "gas-genset"), ("links", "fuel-cell"),
                       ("storage_units", "battery")):
        name = n.__getattribute__(comp).index[0]
        p = price_from_pack(n, comp, name, tech, version=R.REDUNDANCY_PACK_VERSION)
        out[tech] = {k: p.get(k) for k in ("capital_cost", "fom_cost",
                                           "annualised_cost_eur_per_mw_yr")}
    _emit("redundancy", **out)


def test_guided_study():
    from services.study import library as L
    from tests import u2_record_pre_numbers as REC

    _emit("study:pack", version=L.PACK_VERSION, library_version=L.load_defaults().version,
          finance=dict(L.load_defaults().finance))
    _emit("study:golden_s5", **REC.golden_s5())
    _emit("study:seed_bills", **REC.seed_bills())
