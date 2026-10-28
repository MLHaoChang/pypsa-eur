"""
`eh_concentrator_staged` (grid-to-process ticket 10, design §3.B "Acceptance checks (B)").

A mining concentrator on the hourly `year` time base (ticket 09): the mine, crusher, coarse-ore stockpile, mill lines and
flotation feed a concentrate shed that loads a vessel every 21 days. Everything here is template data, except the year
base's unit commitment (`services.solver.process_uc`: a plain-LP plan, then the binary check on one week per season).

The planning solves go through `run_simulation`, the way the app runs them. Most checks plan 2027 alone (one year of
8,760 hours, about a minute); `test_the_three_period_year_solves_within_the_budget` plans the whole template (about
20 minutes), as the acceptance's budget check. The investment case (ticket 11) and the motor start (ticket 12) are not
here.
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading
import time

import numpy as np
import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_concentrator as C  # noqa: E402
import eh_templates as T  # noqa: E402

from services.process_template_rules import refusals  # noqa: E402
from services.solver import process_uc  # noqa: E402

TID = "eh_concentrator_staged"
YEARS = C.YEARS
#: The solve budget of ticket 09 (design §3.B): 30 minutes.
BUDGET_S = 30 * 60
_PLANS: dict[str, tuple] = {}
MILLS = list(C.MILLS)


def _plan(key: str, years=YEARS, edit=None):
    """Plan the template over ``years`` (or a changed copy) once per module; returns (network, state, seconds)."""
    if key not in _PLANS:
        from services.pypsa_service import PyPSAService
        from services.solver_service import SolverConfig, run_simulation
        import routers.simulation as sim_router
        from tests.conftest import install_network_into_backend

        n = T.BUILDERS[TID]() if tuple(years) == YEARS else C.build_eh_concentrator_staged(T, years=years)
        if edit is not None:
            edit(n)
        install_network_into_backend(n)
        cfg = SolverConfig(**T.solver_config(TID))
        sim_router._state["solver_config"] = cfg
        n = PyPSAService.get_network()
        state: dict = {}
        t0 = time.monotonic()
        status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue(),
                                      state_update=lambda **k: state.update(k))
        assert status in ("ok", "optimal"), (status, cond)
        _PLANS[key] = (n, state, time.monotonic() - t0)
    return _PLANS[key]


def _no_candidates(kinds):
    def edit(n):
        for comp, prefix in (("Generator", "pv_new"), ("StorageUnit", "bess_new")):
            if prefix.split("_")[0] in kinds:
                df = n.static(comp)
                n.remove(comp, [a for a in df.index if a.startswith(prefix)])
    return edit


def _bess_installed(n):
    _no_candidates({"pv"})(n)
    n.storage_units.loc["bess_new_2027", "p_nom_min"] = C.BESS_MAX_MW


@pytest.fixture(scope="module")
def n0():
    return T.BUILDERS[TID]()


def _by_period(series: pd.Series) -> pd.Series:
    return series.groupby(level=0).sum()


def _lost(n) -> pd.Series:
    return _by_period(n.generators_t.p["__lost_concentrate"])


# ── the template as built ──────────────────────────────────────────────────


def test_it_is_registered_and_passes_the_template_rules(n0):
    assert TID in T.BUILDERS and TID in T.TEMPLATE_META and TID in T.STRESS_SCENARIOS
    assert refusals(n0, T.solver_config(TID)) == []


def test_each_period_is_one_chronological_year(n0):
    assert list(n0.investment_periods) == list(YEARS)
    assert process_uc.is_year_base(n0)
    for y in YEARS:
        assert (n0.snapshots.get_level_values(0) == y).sum() == 8760
    assert (n0.snapshot_weightings == 1.0).all().all()


def test_the_flowsheet_draws_its_feed_on_bus2_and_its_power_on_bus0(n0):
    L = n0.links
    for unit, out, feed, kwh in (("crusher", "ore_fine", "ore_rom", C.CRUSH_KWH_T),
                                 ("mill_1", "slurry", "ore_fine", C.GRIND_KWH_T),
                                 ("mill_3", "slurry", "ore_fine", C.GRIND_KWH_T),
                                 ("flotation", "concentrate", "slurry", C.FLOT_KWH_T)):
        assert (L.at[unit, "bus0"], L.at[unit, "bus1"], L.at[unit, "bus2"]) == ("site", out, feed)
        assert bool(L.at[unit, "eh_process"])
        assert L.at[unit, "efficiency2"] == pytest.approx(-1000.0 / kwh)
        assert L.at[unit, "specific_energy_kwh_per_t"] == kwh
    assert L.at["crusher", "efficiency"] == pytest.approx(1250.0)
    assert L.at["flotation", "efficiency"] == pytest.approx(C.MASS_PULL * 500.0)
    assert C.MASS_PULL == pytest.approx(0.0189, abs=5e-5)
    assert C.bond_kwh_t() == pytest.approx(8.3, abs=0.05)
    assert L.at["mill_1", "p_nom"] == pytest.approx(8.8)
    assert (L.at["crusher", "criticality_tier"], L.at["mill_1", "criticality_tier"]) == \
        ("process_flexible", "process_critical")


def test_the_mills_and_flotation_are_fixed_size_committable_units_with_their_restart(n0):
    for m in MILLS:
        row = n0.links.loc[m]
        assert bool(row.committable) and not bool(row.p_nom_extendable)
        assert (row.p_min_pu, row.min_up_time, row.min_down_time) == (0.6, 12, 4)
        assert row.start_up_cost == 0.0 and row.shut_down_cost == 0.0
    assert n0.links.at["mill_3", "build_year"] == 2035
    assert n0.links.at["flotation", "p_min_pu"] == 0.5
    assert not bool(n0.links.at["crusher", "committable"])


def test_the_mine_feed_and_the_shipments_follow_the_period(n0):
    mine = n0.generators_t.p_max_pu["mine"] * n0.generators.at["mine", "p_nom"]
    assert mine.groupby(level=0).max().to_dict() == pytest.approx(C.FEED_TPH)
    shipped = _by_period(n0.loads_t.p_set["shipments"])
    for y in YEARS:
        assert shipped[y] == pytest.approx(C.conc_tph(y) * 8760)
        assert shipped[y] == pytest.approx(C.VESSELS * C.vessel_t(y))
    assert n0.stores.at["concentrate_shed", "delivery_interval_h"] == 504
    assert n0.stores.at["concentrate_shed", "e_nom"] == C.SHED_T


def test_the_concentrate_has_only_the_lost_production_slack(n0):
    roles = n0.generators["process_role"]
    assert roles[roles != ""].to_dict() == {"__lost_concentrate": "shortfall"}
    assert n0.generators.at["__lost_concentrate", "marginal_cost"] == 1300.0


def test_the_connection_is_non_firm_in_2027_and_firm_after(n0):
    from services.adequacy.energization import apply_energization

    m = n0.copy()
    apply_energization(m)
    cap = m.links_t.p_max_pu["grid_import"] * m.links.at["grid_import", "p_nom"]
    by = cap.groupby(level=0)
    assert by.max().to_dict() == pytest.approx({2027: 25.0, 2030: 25.0, 2035: 35.0})
    assert by.min().to_dict() == pytest.approx({2027: 15.0, 2030: 25.0, 2035: 35.0})
    assert int((cap.loc[2027] < 25.0 - 1e-9).sum()) == 300
    assert n0.buses.at["grid", "eh_sk_mva"] == 150.0 and bool(n0.buses.at["grid", "eh_poc"])


def test_pv_and_bess_are_candidates_per_period_priced_from_the_pack(n0):
    for y in YEARS:
        assert bool(n0.generators.at[f"pv_new_{y}", "p_nom_extendable"])
        assert bool(n0.storage_units.at[f"bess_new_{y}", "p_nom_extendable"])
        assert n0.generators.at[f"pv_new_{y}", "overnight_cost"] > 0
        assert n0.storage_units.at[f"bess_new_{y}", "capital_cost"] > 0


def test_the_week_build_is_refused_with_the_shed_named():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    week = C.build_eh_concentrator_staged(T, time_base="week")
    issues = [i for i in validate_for_run(week, SolverConfig(**T.solver_config(TID)))
              if i.code == "inventory_cadence_needs_year"]
    assert [(i.severity, i.name) for i in issues] == [("error", "concentrate_shed")]


def test_the_year_build_passes_the_validator(n0):
    from services.solver_service import SolverConfig
    from services.validation_service import has_errors, validate_for_run

    assert not has_errors(validate_for_run(n0, SolverConfig(**T.solver_config(TID))))


def test_every_ledger_row_is_demo_or_sourced():
    ledger = T.TEMPLATE_META[TID]["ledger"]
    assert {r["source"] for r in ledger} <= {"demo", "sourced"}
    items = {r["item"] for r in ledger}
    for item in ("ore feed (mine)", "grinding specific energy (Bond)", "crushing", "flotation", "site services",
                 "mill lines", "crusher capacity", "coarse-ore stockpile", "mill p_min_pu, min up / min down",
                 "flotation p_min_pu", "mass pull to concentrate", "shipments", "concentrate shed",
                 "lost-production value (__lost_concentrate)", "grid connection", "PoC fault level",
                 "PV, BESS candidates", "tariff, finance", "unit commitment in planning"):
        assert item in items, item


def test_the_commercial_and_finance_blocks_are_the_industrial_presets():
    import eh_site as S

    cfg = T.solver_config(TID)
    assert cfg["commercial"]["import_tariff"]["id"] == S.SITE_TARIFF["id"]
    assert cfg["finance"]["wacc_nominal"] == S.DISCOUNT_RATE
    assert set(cfg["finance"]["cod_by_asset"]) >= {"mill_1", "mill_3", "concentrate_shed"}
    assert cfg["finance"]["cod_by_asset"]["mill_3"] == "2035-01-01"


def test_the_sidecars_are_current():
    """The shipped sidecars match what the builders write (regenerate with `_build.py`)."""
    out = TPL_DIR / TID
    meta = json.loads((out / T.META_FILE).read_text())
    assert meta == json.loads(json.dumps(T.TEMPLATE_META[TID]))
    assert json.loads((out / T.SOLVER_FILE).read_text()) == json.loads(json.dumps(T.solver_config(TID)))


def test_the_assistant_lists_the_template():
    from harness import catalogue

    tool = next(t for t in catalogue.TOOLS if t["name"] == "create_project_from_template")
    assert TID in tool["input_schema"]["properties"]["template_id"]["enum"]
    assert TID in tool["description"]


def test_create_project_from_template_creates_it(client, tmp_projects_dir, bound_ctx):
    import pypsa

    from services import network_lengths

    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "conc"})
    assert r.status_code == 200, r.text
    with bound_ctx(client):
        d = network_lengths.active_project_dir()
    n = pypsa.Network(str(d / "network.nc"))
    assert list(n.investment_periods) == list(YEARS) and len(n.snapshots) == 3 * 8760
    assert json.loads((d / T.META_FILE).read_text())["id"] == TID


# ── the year base's unit commitment ──────────────────────────────────────────


def test_auto_plans_the_year_base_as_a_plain_lp_and_puts_the_units_back(n0):
    from services.solver_service import SolverConfig

    cfg = SolverConfig(**T.solver_config(TID))
    assert process_uc.planning_uc_mode(n0, cfg) == "plain"
    assert process_uc.planning_uc_kwargs(n0, cfg) == {}
    m = n0.copy()
    undo = process_uc.relax_for_plan(m, cfg)
    units = [*MILLS, *C.FLOTATION]
    assert not m.links.loc[units, "committable"].any() and (m.links.loc[units, "p_min_pu"] == 0).all()
    undo()
    undo()
    assert m.links.loc[units, "committable"].all()
    assert m.links.loc[units, "p_min_pu"].to_dict() == n0.links.loc[units, "p_min_pu"].to_dict()


def test_the_check_takes_one_week_per_season_on_the_year_and_the_first_week_otherwise(n0):
    starts = [h for h, _ in process_uc._windows(n0, 2030)]
    assert starts == [14 * 24, 104 * 24, 195 * 24, 287 * 24]
    assert {pd.Timestamp(sns[0][1]).month for _, sns in process_uc._windows(n0, 2030)} == {1, 4, 7, 10}
    week = C.build_eh_concentrator_staged(T, time_base="week")
    assert not process_uc.is_year_base(week)
    assert [(h, len(s)) for h, s in process_uc._windows(week, 2027)] == [(0, 168)]


# ── the 2027 plan ──────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_2027_plans_plain_and_checks_four_weeks_binary(reset_backend):
    n, state, _ = _plan("2027", years=(2027,))
    assert bool(n.links.at["mill_1", "committable"]), "the units' commitment is put back after the plan"
    check = state["last_process_uc_check"]
    assert check["status"] == "ok", check["message"]
    rows = check["periods"]
    assert [r["period"] for r in rows] == [2027] * 4
    assert all(r["status"] == "ok" and r["pct"] is not None for r in rows)
    assert all("from hour" in r["window"] for r in rows)


@pytest.mark.live_solve
def test_mass_balance_per_period(reset_backend):
    n, _, _ = _plan("2027", years=(2027,))
    p0, p1, p2 = n.links_t.p0, n.links_t.p1, n.links_t.p2
    mined = n.generators_t.p["mine"]
    crushed = -p1["crusher"]
    milled = -p1[MILLS].sum(axis=1)
    drawn = p2[MILLS].sum(axis=1)
    conc = -p1[list(C.FLOTATION)].sum(axis=1)
    shipped = n.loads_t.p["shipments"]
    lost = n.generators_t.p["__lost_concentrate"]
    pile = n.stores_t.p[[s for s in n.stores.index if s.startswith("coarse_ore_stockpile")]].sum(axis=1)
    shed = n.stores_t.p["concentrate_shed"]
    scale = _by_period(mined).max()
    # Ore in = Σ crusher (its feed on bus2).
    assert _by_period(mined - p2["crusher"]).abs().max() < 1e-6 * scale
    assert p2["crusher"].to_numpy() == pytest.approx(1250.0 * p0["crusher"].to_numpy(), abs=1e-6)
    # Crusher = Σ mills + Δ stockpile (a store's p is its discharge).
    assert _by_period(crushed - drawn + pile).abs().max() < 1e-6 * scale
    assert milled.to_numpy() == pytest.approx(drawn.to_numpy(), abs=1e-6)
    # Concentrate = mass pull × mill throughput (flotation takes all the slurry).
    assert _by_period(conc - C.MASS_PULL * milled).abs().max() < 1e-6 * scale
    # Shipments = production + lost − Δ shed.
    assert _by_period(shipped - conc - lost - shed).abs().max() < 1e-6 * scale
    assert p0["mill_1"].to_numpy() == pytest.approx(C.GRIND_KWH_T / 1000.0 * (-p1["mill_1"]).to_numpy(), abs=1e-6)


@pytest.mark.live_solve
def test_the_shed_fills_for_21_days_and_drops_at_each_vessel(reset_backend):
    n, _, _ = _plan("2027", years=(2027,))
    e = n.stores_t.e["concentrate_shed"].loc[2027].to_numpy()
    loading = C.shipments_profile() > 0
    de = np.diff(e, prepend=e[-1])          # cyclic: the first hour follows the last
    assert (de[~loading] >= -1e-6).all(), "between vessels the shed only fills"
    for k in range(C.VESSELS):
        end = (k + 1) * C.SHIP_CYCLE_H
        before, after = e[end - C.SHIP_LOAD_H - 1], e[end - 1]
        assert before - after > 0.9 * C.vessel_t(2027) - C.SHIP_LOAD_H * C.conc_tph(2027) * 1.4
    assert e.max() > C.vessel_t(2027) * 0.9


@pytest.mark.live_solve
def test_in_the_curtailment_hours_import_is_at_most_the_envelope(reset_backend):
    n, _, _ = _plan("2027", years=(2027,))
    imp = n.links_t.p0["grid_import"].loc[2027].to_numpy()
    low = C.envelope_mw() < C.CONNECTION_MW[2027]
    assert low.sum() == 300
    assert (imp[low] <= C.ENVELOPE_LOW_MW + 1e-6).all()
    assert (imp <= C.CONNECTION_MW[2027] + 1e-6).all()


@pytest.mark.live_solve
def test_2027_without_pv_and_bess_loses_concentrate_and_a_bess_lowers_it(reset_backend):
    bare, _, _ = _bare()
    bess, _, _ = _plan("2027-bess", years=(2027,), edit=_bess_installed)
    lost_bare, lost_bess = _lost(bare)[2027], _lost(bess)[2027]
    assert lost_bare > 1.0
    assert lost_bess < lost_bare - 1.0
    # At the pack's price the plan builds none: a BESS cannot bridge a 150-h curtailment (ledgered).
    allowed, _, _ = _plan("2027-bess-allowed", years=(2027,), edit=_no_candidates({"pv"}))
    assert allowed.storage_units.at["bess_new_2027", "p_nom_opt"] == pytest.approx(0.0, abs=1e-3)


# ── restart, on the binary check's January week (it holds the first curtailment event) ──


def _bare():
    """2027 without PV and BESS: the 15 MW event cannot run both mill lines."""
    return _plan("2027-bare", years=(2027,), edit=_no_candidates({"pv", "bess"}))


def _january(n, edit=None):
    from services.solver_service import SolverConfig, _resolve_mip_kwargs

    first, sns = process_uc._windows(n, 2027)[0]
    m = process_uc.window_copy(n, 2027, first)
    if edit is not None:
        edit(m)
    cfg = SolverConfig(**T.solver_config(TID))
    # The check's own MIP settings (`solver_service._run_process_uc_check`).
    opts = _resolve_mip_kwargs(cfg.solver_name, cfg.mip_gap, cfg.mip_time_limit_s, {})
    status, cond, _ = process_uc._solve_window(m, sns, True, cfg, opts, {})
    assert status == "ok", cond
    return m, sns


def _runs(u, value) -> list[int]:
    out, k = [], 0
    for v in [*u, None]:
        if v == value:
            k += 1
        elif k:
            out.append(k)
            k = 0
    return out


@pytest.mark.live_solve
def test_in_the_binary_week_every_mill_stop_lasts_the_restart_time(reset_backend):
    n, _, _ = _bare()
    m, sns = _january(n)
    stops = []
    for mill in ("mill_1", "mill_2"):
        u = np.round(m.links_t.status[mill].loc[sns].to_numpy())
        p = m.links_t.p0[mill].loc[sns].to_numpy()
        assert set(np.unique(u)) <= {0.0, 1.0}
        assert (p >= C.MILL_P_MIN_PU * m.links.at[mill, "p_nom"] * u - 1e-6).all()
        inner = _runs(u, 0.0)
        # A stop cut by the window's end may be shorter; every other lasts min_down_time.
        if len(inner) and u[-1] == 0.0:
            inner = inner[:-1]
        stops += inner
    assert stops, "the 15 MW curtailment week should stop a mill"
    assert min(stops) >= C.MILL_MIN_DOWN_H


@pytest.mark.live_solve
def test_dropping_the_restart_time_never_raises_the_lost_tonnes(reset_backend):
    """The sign: with min_down_time 0 the loss is no higher, with the mills' headroom and without it (the lines
    sized to the feed). Up to the check's MIP gap, in tonnes at the lost-production value."""
    from services.solver_service import SolverConfig

    n, _, _ = _bare()
    gap = float(SolverConfig(**T.solver_config(TID)).mip_gap)

    def lost(*edits):
        def edit(m):
            for e in edits:
                e(m)
        m, sns = _january(n, edit)
        return float(m.generators_t.p["__lost_concentrate"].loc[sns].sum()), float(m.objective)

    def no_restart(m):
        m.links.loc[MILLS, "min_down_time"] = 0

    def no_headroom(m):
        m.links.loc[MILLS, "p_nom"] = C.FEED_TPH[2027] / 2 * C.GRIND_KWH_T / 1000.0

    for base, relaxed in ((lost(), lost(no_restart)), (lost(no_headroom), lost(no_headroom, no_restart))):
        tol = gap * max(base[1], relaxed[1]) / C.LOST_EUR_PER_T
        assert relaxed[0] <= base[0] + tol, (relaxed, base, tol)


# ── the whole template ─────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.live_solve
def test_the_three_period_year_solves_within_the_budget(reset_backend):
    n, state, seconds = _plan("full")
    assert seconds <= BUDGET_S, f"{seconds:.0f} s"
    check = state["last_process_uc_check"]
    assert [r["period"] for r in check["periods"]] == [y for y in YEARS for _ in range(4)]
    assert check["status"] == "ok", check["message"]
    shed = _by_period(n.stores_t.p["concentrate_shed"])
    scale = _by_period(n.loads_t.p["shipments"]).max()
    assert shed.abs().max() < 1e-6 * scale
