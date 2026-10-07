"""
U2 WP6 — the LP switch: the guided study's options are priced and solved by
the Investment Case engine's commercial chain, not by GS's link prices and
demand wrapper.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §2
(C2, C3, C12), §4.6 (2), §4.7, WP6; stage-1 gate note
docs/superpowers/notes/2026-10-06-u2-stage1-gate.md (C6, the year-billed
demand basis ask).

* `compile.solver_config` — the option fork's explicit `SolverConfig` carries
  the compiled `CommercialConfig` and no GS `demand_charge` (C6).
* The site golden `bess_2h`, run by the PRODUCTION runner through the solve
  queue (pack → compile → mint → bind → fork → queue → `run_simulation`),
  equals WP0: `p_nom_opt` within 1e-4 MW, the objective within 1e-6
  relative; its bill on the solved fork equals the stage-1 `ic_solved_fork`
  recordings (gate C6, last bullet); `billing_vs_lp_gap` leaves nothing
  unattributed for energy and demand (§4.6 (2)); the run's demand charge is
  the engine's committed amount (`engine_adapter.demand_charge_eur`).
* A fork passes `validate_for_run` with no error and no
  `commercial.demand_resolution` warning (`settlement="h"`).
* The year-billed demand basis (an IC annual measured peak, `capacity` on
  `peak_import`) is the GS wrapper's formulation on a toy year (§4.7).
* The guided path no longer reaches GS's demand wrapper, its config field or
  its link prices; `bill_meter` (no solve) is called only by the preview.
"""
from __future__ import annotations

import ast
import json
import pathlib
import queue
import threading

import numpy as np
import pandas as pd
import pytest

from tests.golden import site_fixture as SF
from tests.study_s4_support import create_pack_study, enable_studies, wait_run
from tests.u2_targets import DE, FAKE_REF, WP0, close, flat_resolver

BACKEND = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = WP0["golden_s5"]


def _C():
    from services.study import compile as C

    return C


def _A():
    from services.study import engine_adapter as A

    return A


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def _site(option: str):
    """The golden site option built by the pack, compiled and bound in memory."""
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q

    intake = SF.site_intake()
    lib = SF.site_library()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, intake, lib)
    n = packs.build_site_network(intake, ledger, option, library=lib)
    c = _C().commercial_from_ledger(intake, ledger, lib, n.snapshots, export_series=FAKE_REF)
    c = _C().bind_on_network(n, c, resolve_ref=flat_resolver(40.0, n.snapshots))
    return n, c, ledger


# ── compile.solver_config ─────────────────────────────────────────────────

def test_the_option_solver_config_carries_the_compiled_commercial_and_no_gs_demand_charge():
    n, c, ledger = _site("bess_2h")
    cfg = _C().solver_config(ledger, c)
    assert (cfg.mode, cfg.solver_name, cfg.solve_strategy) == ("lopf", "highs", "full")
    assert cfg.sclopf is False and cfg.run_ac_pf_after_lopf is False
    assert cfg.multi_investment_periods is False and cfg.extra_functionality_code == ""
    assert cfg.demand_charge is None
    assert cfg.commercial == c.commercial()
    assert cfg.discount_rate == 0.07 and cfg.default_lifetime == 25.0
    assert cfg.finance is None          # WP7
    assert [i["id"] for i in cfg.commercial["import_tariff"]["items"]] == [
        "energy", "network:energy:0", "demand", "fixed"]


def test_the_pack_option_config_is_the_compiled_one():
    from services.study import packs

    n, c, ledger = _site("none")
    assert packs.option_solver_config(ledger, c) == _C().solver_config(ledger, c)


# ── a fork passes preflight ───────────────────────────────────────────────

@pytest.mark.parametrize("option", ["none", "bess_2h", "bess_pv_2h"])
def test_validate_for_run_on_a_compiled_fork_raises_no_error(option):
    from services.validation_service import validate_for_run

    n, c, ledger = _site(option)
    issues = validate_for_run(n, _C().solver_config(ledger, c))
    assert [(i.code, i.message) for i in issues if i.severity == "error"] == []
    # settlement="h" on an hourly axis: no demand-resolution warning (WP6 mutation).
    assert not [i for i in issues if "demand_resolution" in i.code]


# ── the year-billed demand basis (§4.7; stage-1 engine ask) ───────────────

def _toy_year(*, gs_prices: bool):
    from tests import u2_record_pre_numbers as REC

    import pypsa

    sn = pd.date_range("2030-01-01", "2030-12-31 23:00", freq="h")
    n = pypsa.Network()
    n.set_snapshots(sn)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=1e3, p_min_pu=-1.0, marginal_cost=0.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=60.0)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=60.0)
    hr = np.asarray(sn.hour)
    rng = np.random.default_rng(0)
    load = 20 + 15 * np.sin((hr - 6) / 24 * 2 * np.pi).clip(0) + 5 * rng.random(len(sn))
    # One winter evening a year far above the rest: the annual peak the
    # year-billed charge prices.
    load[(sn.month == 1) & (sn.day == 15) & (hr == 18)] += 25.0
    n.add("Load", "site_load", bus="site", p_set=pd.Series(load, index=sn))
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, p_nom_max=40.0,
          max_hours=2.0, overnight_cost=650_000.0, lifetime=15, discount_rate=0.07,
          fom_cost=5_000.0, efficiency_store=0.95 ** 0.5,
          efficiency_dispatch=0.95 ** 0.5, cyclic_state_of_charge=True)
    form = REC.toy_tariff().model_copy(update={
        "billing_period": "year",
        "demand_charge": REC.toy_tariff().demand_charge.model_copy(
            update={"price_per_mw_per_period": 120_000.0})})
    if gs_prices:
        from services.study.tariff import write_tariff_prices

        write_tariff_prices(n, form, "grid_import", "grid_export")
    return n, form


@pytest.mark.live_solve
def test_the_year_billed_demand_charge_is_the_gs_wrappers_formulation_on_a_toy_year():
    """
    GS: one `peak_import` per year, `objective += price × peak`. IC: the
    `demand` item compiled as an annual measured peak (`capacity` on
    `peak_import`, EUR/kW-year × represented hours / 8760). On a whole year
    the two are one formulation: objectives ≤ 1e-7 relative, the same
    battery and the same annual peak.
    """
    from services.pypsa_service import PyPSAService
    from services.solver.objective import _wrap_with_demand_charge
    from services.solver_service import (
        SolverConfig,
        run_simulation,
        with_periodized_cost_defaults,
    )
    from services.study.tariff import demand_charge_config

    gs, form = _toy_year(gs_prices=True)
    gcfg = SolverConfig(solver_name="highs", discount_rate=0.07,
                        demand_charge=demand_charge_config(form, ["grid_import"]))
    with with_periodized_cost_defaults(gs, gcfg):
        assert gs.optimize(solver_name="highs",
                           extra_functionality=_wrap_with_demand_charge(gs, None, gcfg)) \
            == ("ok", "optimal")
    gs_peak = float(gs.model.variables["peak_import"].solution.sum())

    ic, form = _toy_year(gs_prices=False)
    c = _C().commercial_from_form(form, ic.snapshots, export_series=FAKE_REF)
    assert [(i.id, i.kind, i.measured_on, i.unit) for i in c.config.import_tariff.items
            if i.id == "demand"] == [("demand", "capacity", "peak_import", "per_kw_year")]
    c = _C().bind_on_network(ic, c, resolve_ref=flat_resolver(40.0, ic.snapshots))
    cfg = _C().solver_config(None, c, discount_rate=0.07)
    PyPSAService.set_network(ic)
    status, cond = run_simulation(cfg, ic, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue())
    assert (status, cond) == ("ok", "optimal")
    assert abs(float(ic.objective) - float(gs.objective)) <= 1e-7 * abs(float(gs.objective))
    assert float(ic.storage_units.at["bess", "p_nom_opt"]) == pytest.approx(
        float(gs.storage_units.at["bess", "p_nom_opt"]), abs=1e-4)
    assert float(ic.links_t.p0["grid_import"].max()) == pytest.approx(gs_peak, abs=1e-5)
    # The engine's committed amount is the GS wrapper's term.
    assert _A().demand_charge_eur(ic, c) == pytest.approx(120_000.0 * gs_peak, rel=1e-6)


# ── the production runner through the queue (the site golden) ─────────────

@pytest.fixture(scope="module")
def golden_run(tmp_path_factory):
    """Filled by the first test that runs the study (module-scoped cache)."""
    return {}


def _run_golden(client, api_project, project_row, project_storage_dir, cache):
    if cache:
        return cache
    intake = {**SF.site_intake(), "pv": {"enabled": False}}
    api_project("wp6-golden-src")
    r = create_pack_study(client, "wp6-golden-src", "wp6-golden", intake=intake)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    r = client.post(f"/api/projects/wp6-golden/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    rec = wait_run(client, "wp6-golden", sid, timeout=1200.0)
    assert rec["status"] == "done", rec
    base = project_row("wp6-golden")
    forks = {o: project_row(f"wp6-golden-opt-{o}") for o in ("none", "bess_2h")}
    # The finished run's record on disk (details included); the client and
    # its sandbox are per test, so everything later tests read is kept here.
    run = client.get(f"/api/projects/wp6-golden/studies/{sid}/run").json()
    cache.update(sid=sid, rec=rec, run=run, base=base, forks=forks,
                 dirs={o: project_storage_dir(f"wp6-golden-opt-{o}") for o in forks})
    return cache


def _fork(cache, option):
    import pypsa

    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, cache["dirs"][option] / "network.nc")
    cfg = json.loads((cache["dirs"][option] / "solver_config.json").read_text())
    return n, cfg


def _compiled_of(cfg: dict):
    from models.commercial import CommercialConfig

    C = _C()
    item_component = {"energy": "energy", "network:energy:0": "network", "demand": "demand",
                      "fixed": "fixed"}
    return C.CompiledCommercial(config=CommercialConfig.model_validate(cfg["commercial"]),
                                item_component=item_component,
                                tariff_meta={"currency": "EUR", "currency_year": 2020,
                                             "billing_period": "month"})


@pytest.mark.live_solve
def test_the_site_golden_through_the_queue_equals_wp0(
        client, api_project, studies_on, project_row, project_storage_dir, golden_run):
    cache = _run_golden(client, api_project, project_row, project_storage_dir, golden_run)
    for option in ("none", "bess_2h"):
        n, cfg = _fork(cache, option)
        want = GOLDEN[option]
        assert abs(float(n.objective) - want["objective"]) <= 1e-6 * abs(want["objective"]), option
        if option == "bess_2h":
            assert abs(float(n.storage_units.at["battery", "p_nom_opt"])
                       - want["battery_p_nom_mw"]) <= 1e-4


@pytest.mark.live_solve
def test_each_fork_carries_the_compiled_commercial_and_the_studys_minted_series(
        client, api_project, studies_on, project_row, project_storage_dir, golden_run):
    from services.commercial.lp_bindings import EXPORT_PRICE_ATTR, META_DEMAND

    cache = _run_golden(client, api_project, project_row, project_storage_dir, golden_run)
    name = _C().export_series_name(cache["base"].id, cache["sid"])
    for option in ("none", "bess_2h"):
        n, cfg = _fork(cache, option)
        assert cfg.get("demand_charge") is None
        assert cfg["commercial"]["poc_link"] == "grid_import"
        assert cfg["commercial"]["export_price_ref"]["id"] == name
        assert cfg["solve_strategy"] == "full"
        # Prices live in the engine, not on the Links (C2); the export price
        # was bound before the fork was written (C6).
        assert "grid_import" not in n.links_t.marginal_cost.columns
        assert np.allclose(n.links_t[EXPORT_PRICE_ATTR]["grid_export"].to_numpy(), 40.0)
        assert n.meta.get(META_DEMAND)
    assert cache["rec"]["export_series"]["id"] == name


@pytest.mark.live_solve
def test_the_run_records_the_engines_committed_demand_charge(
        client, api_project, studies_on, project_row, project_storage_dir, golden_run):
    cache = _run_golden(client, api_project, project_row, project_storage_dir, golden_run)
    run = cache["run"]
    for option in ("none", "bess_2h"):
        want = WP0["seed_bills"][DE][option]["by_component"]["demand"]
        got = run["details"][option]["demand_charge_eur"]
        assert got == pytest.approx(want, rel=1e-6), option


@pytest.mark.live_solve
@pytest.mark.parametrize("option", ["none", "bess_2h"])
def test_the_ic_solved_fork_bills_equal_the_stage1_recordings(
        client, api_project, studies_on, project_row, project_storage_dir, golden_run, option):
    """Gate C6 (WP6 side): the `ic_solved_fork[none|bess_2h]` rows as tests."""
    cache = _run_golden(client, api_project, project_row, project_storage_dir, golden_run)
    n, cfg = _fork(cache, option)
    bill = _A().bill(n, _compiled_of(cfg))
    assert bill.unavailable == {}, bill.unavailable
    rows = [r for r in json.loads((BACKEND / "tests" / "fixtures" / "u2_deltas.json")
                                  .read_text())["deltas"]
            if r["test"] == f"ic_solved_fork[{DE}-{option}]"]
    assert len(rows) == 8
    for r in rows:
        if r["figure"] == "objective":
            got = float(n.objective)
            assert abs(got - r["post"]) <= 1e-6 * abs(r["post"])
        elif r["figure"] == "total":
            assert close(bill.total, r["post"])
        else:
            key = r["figure"].split(".", 1)[1]
            assert close(getattr(bill.by_component, key), r["post"]), key


@pytest.mark.live_solve
def test_billing_vs_lp_gap_leaves_nothing_unattributed_for_energy_and_demand(
        client, api_project, studies_on, project_row, project_storage_dir, golden_run):
    """§4.6 (2): the bill saving is the LP's grid-cost saving."""
    from services.commercial.billing import bill_site
    from services.commercial.gap import billing_vs_lp_gap

    cache = _run_golden(client, api_project, project_row, project_storage_dir, golden_run)
    for option in ("none", "bess_2h"):
        n, cfg = _fork(cache, option)
        gap = billing_vs_lp_gap(n, cfg["commercial"], bill_site(n, cfg["commercial"]))
        assert gap["periods"], gap
        for _period, kinds in gap["periods"].items():
            for kind in ("energy", "demand"):
                assert kinds[kind]["unattributed"] == pytest.approx(0.0, abs=1e-6), (
                    option, kind, kinds[kind])
        assert gap["gates"] == []


# ── the guided path no longer reaches the GS LP pieces ────────────────────

_GS_LP_NAMES = {"_wrap_with_demand_charge", "demand_charge_config", "write_tariff_prices"}
#: Removed in WP10 with the code they define or carry (plan §5.1).
_STILL_DEFINED_IN = {"services/study/tariff.py"}


def _guided_modules():
    for p in sorted((BACKEND / "services" / "study").glob("*.py")):
        rel = str(p.relative_to(BACKEND))
        if rel not in _STILL_DEFINED_IN:
            yield rel, p
    yield "routers/studies.py", BACKEND / "routers" / "studies.py"


def test_the_guided_path_does_not_use_the_gs_demand_wrapper_or_link_prices():
    used = {}
    for rel, p in _guided_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        hit = sorted(names & _GS_LP_NAMES)
        kw = sorted({k.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
                     for k in n.keywords if k.arg == "demand_charge"
                     and not (isinstance(k.value, ast.Constant) and k.value.value is None)})
        if hit or kw:
            used[rel] = hit + kw
    assert used == {}


def test_bill_meter_is_called_only_by_the_preview():
    """
    Gate C6: a bill without a solve (`bill_meter`) is the intake preview's
    or, since WP7, the case's counterfactual baseline (`option_case`: the
    served load, export ≡ 0 — the baseline at the case's tariff, BC-7); every
    option bill is a solved fork's.
    """
    callers = []
    for rel, p in _guided_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(
                        node.func, "id", None)) == "bill_meter":
                    callers.append((rel, fn.name))
    assert set(callers) <= {("routers/studies.py", "preview_intake"),
                            ("services/study/engine_adapter.py", "bill_meter"),
                            ("services/study/engine_adapter.py", "option_case")}
    # The case's counterfactual meter exports nothing.
    import inspect

    from services.study import engine_adapter as A

    src = inspect.getsource(A.option_case)
    assert "bill_meter(n, compiled, _served_load(n), np.zeros(len(n.snapshots))" in src
