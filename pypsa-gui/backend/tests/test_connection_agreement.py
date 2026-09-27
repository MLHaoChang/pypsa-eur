"""
Connection agreements — firm, non-firm static, `available_from`
(Edge Investment Case P1 WP1.4a).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.4a
Spec §5 table rows "Connection capacity fee", "Non-firm static cap", "Grid
arriving in year N".

`apply_connection_agreement` mutates the PoC Link for ONE solve and hands back
an undo that restores every attribute it touched (the `apply_archetype_pack_
detailed().undo()` discipline). The capacity fee is annual (€/kW/yr); the LP
sees it scaled to the horizon, and `cost_breakdown["commercial"]
["network_capacity"]` reports the same figure from persisted data, so the
objective gap stays 0.
"""
from __future__ import annotations

import queue
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.commercial import ConnectionAgreement, TariffItem, TariffPeriod
from services.commercial import connection as C
from tests.fixtures.investment_case.edge_15min import build_edge_15min


def _fee(rate_per_kw_year: float) -> TariffItem:
    return TariffItem(id="cap_fee", kind="capacity", unit="per_kw_year",
                      periods=[TariffPeriod(name="all", rate=rate_per_kw_year)])


def _agreement(kind="firm", cap=60.0, fee=None, available_from=date(2030, 1, 1), **kw):
    return ConnectionAgreement(kind=kind, import_cap_mw=cap, available_from=available_from,
                               capacity_fee=fee, **kw)


def _snapshot_links(n):
    cols = ["p_nom", "p_nom_extendable", "p_nom_min", "p_nom_max", "capital_cost",
            "p_max_pu", "build_year"]
    return (n.links[cols].copy(),
            n.links_t.p_max_pu.copy() if hasattr(n.links_t, "p_max_pu") else None)


# ── apply / undo (no solve) ────────────────────────────────────────────────


def test_firm_with_a_fee_makes_the_poc_link_extendable_up_to_the_cap():
    n = build_edge_15min()
    applied = C.apply_connection_agreement(n, _agreement(fee=_fee(50.0)), poc_link="import")
    row = n.links.loc["import"]
    assert bool(row.p_nom_extendable) and row.p_nom_max == 60.0 and row.p_nom_min == 0.0
    # €50/kW/yr = €50,000/MW/yr, scaled to a 7-day horizon.
    assert row.capital_cost == pytest.approx(50_000.0 * 7 * 24 / 8760)
    assert applied.facts["fee_eur_per_mw_year"] == 50_000.0
    assert applied.facts["horizon_years"] == pytest.approx(7 * 24 / 8760)


def test_firm_without_a_fee_fixes_the_connection_at_the_cap():
    n = build_edge_15min()
    C.apply_connection_agreement(n, _agreement(fee=None), poc_link="import")
    row = n.links.loc["import"]
    assert not bool(row.p_nom_extendable) and row.p_nom == 60.0


def test_non_firm_static_caps_flow_with_p_max_pu():
    n = build_edge_15min()  # physical p_nom 80
    C.apply_connection_agreement(n, _agreement(kind="non_firm_static", cap=30.0),
                                 poc_link="import")
    assert n.links.at["import", "p_nom"] == 80.0
    assert n.links.at["import", "p_max_pu"] == pytest.approx(30.0 / 80.0)


def test_available_from_zeroes_flow_before_the_date():
    n = build_edge_15min()  # 2030-01-07 … 2030-01-13
    C.apply_connection_agreement(n, _agreement(available_from=date(2030, 1, 10)),
                                 poc_link="import")
    pmp = n.links_t.p_max_pu["import"]
    before = n.snapshots < pd.Timestamp("2030-01-10")
    assert (pmp[before] == 0.0).all() and (pmp[~before] == 1.0).all()


def test_export_cap_sets_the_export_link():
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    C.apply_connection_agreement(n, _agreement(export_cap_mw=20.0), poc_link="import",
                                 export_link="export")
    assert n.links.at["export", "p_nom"] == 20.0


@pytest.mark.parametrize("agreement", [
    _agreement(fee=_fee(50.0), available_from=date(2030, 1, 10)),
    _agreement(kind="non_firm_static", cap=30.0),
    _agreement(fee=None, export_cap_mw=20.0),
])
def test_undo_restores_every_mutated_attribute(agreement):
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    n.links_t.p_max_pu["import"] = np.linspace(0.5, 1.0, len(n.snapshots))
    before_static, before_t = _snapshot_links(n)
    applied = C.apply_connection_agreement(n, agreement, poc_link="import",
                                           export_link="export")
    applied.undo()
    after_static, after_t = _snapshot_links(n)
    pd.testing.assert_frame_equal(before_static, after_static)
    pd.testing.assert_frame_equal(before_t, after_t)


def test_undo_drops_a_column_it_created():
    n = build_edge_15min()
    assert "import" not in n.links_t.p_max_pu.columns
    applied = C.apply_connection_agreement(n, _agreement(available_from=date(2030, 1, 10)),
                                           poc_link="import")
    applied.undo()
    assert "import" not in n.links_t.p_max_pu.columns


def test_a_fee_in_an_unsupported_unit_is_refused_before_any_mutation():
    n = build_edge_15min()
    before_static, _ = _snapshot_links(n)
    bad = TariffItem(id="kva", kind="capacity", unit="per_kva_year",
                     periods=[TariffPeriod(name="all", rate=10.0)])
    with pytest.raises(C.CommercialBindingError, match="per_kva_year"):
        C.apply_connection_agreement(n, _agreement(fee=bad), poc_link="import")
    pd.testing.assert_frame_equal(before_static, _snapshot_links(n)[0])


def test_non_firm_dynamic_and_fca_are_left_to_wp1_4b():
    n = build_edge_15min()
    env = {"id": "env", "version": 1, "hash": "a" * 64, "source": "t"}
    with pytest.raises(C.CommercialBindingError, match="WP1.4b"):
        C.apply_connection_agreement(
            n, _agreement(kind="non_firm_dynamic", envelope=env), poc_link="import")


def test_a_monthly_fee_is_twelve_times_the_annual_rate():
    n = build_edge_15min()
    monthly = TariffItem(id="m", kind="capacity", unit="per_kw_month",
                         periods=[TariffPeriod(name="all", rate=4.0)])
    applied = C.apply_connection_agreement(n, _agreement(fee=monthly), poc_link="import")
    assert applied.facts["fee_eur_per_mw_year"] == 48_000.0


# ── multi-period `available_from` ──────────────────────────────────────────


def _two_period():
    from services.snapshot_index import _build_period_multiindex

    base = build_edge_15min()
    idx = pd.date_range("2030-01-07", periods=96, freq="15min")
    n = base.copy()
    mi = _build_period_multiindex([2030, 2035], [idx, idx + pd.DateOffset(years=5)])
    n.set_snapshots(mi)
    n.investment_periods = [2030, 2035]
    n.snapshot_weightings.loc[:, :] = 0.25
    load = base.loads_t.p_set["site_load"].iloc[:96].to_numpy()
    n.loads_t.p_set = pd.DataFrame({"site_load": np.tile(load, 2)}, index=mi)
    pv = base.generators_t.p_max_pu["pv"].iloc[:96].to_numpy()
    n.generators_t.p_max_pu = pd.DataFrame({"pv": np.tile(pv, 2)}, index=mi)
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    return n


def test_available_from_in_a_later_period_sets_the_build_year():
    n = _two_period()
    C.apply_connection_agreement(n, _agreement(available_from=date(2035, 1, 1)),
                                 poc_link="import")
    assert int(n.links.at["import", "build_year"]) == 2035
    pmp = n.links_t.p_max_pu["import"]
    assert (pmp.loc[2030] == 0.0).all() and (pmp.loc[2035] == 1.0).all()


# ── through run_simulation (live solve) ────────────────────────────────────


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    sink: dict = {}
    status, condition = run_simulation(
        SolverConfig(commercial=commercial), n, PyPSAService.get_lock(),
        threading.Event(), queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal"), (status, condition)
    return sink


def _conn(agreement):
    return {"poc_link": "import", "connection": agreement.model_dump(mode="json")}


@pytest.mark.live_solve
def test_a_high_fee_sizes_the_connection_strictly_below_the_cap():
    n = build_edge_15min()
    cheap = build_edge_15min()
    _solve(cheap, _conn(_agreement(fee=_fee(0.001))))
    _solve(n, _conn(_agreement(fee=_fee(500.0))))
    sized, unconstrained = n.links.at["import", "p_nom_opt"], cheap.links.at["import", "p_nom_opt"]
    assert sized < 60.0 - 1e-3
    assert sized < unconstrained - 1e-3  # the fee made the BESS shave the peak


@pytest.mark.live_solve
def test_the_solve_leaves_the_users_link_as_it_was():
    n = build_edge_15min()
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    before_static, before_t = _snapshot_links(n)
    _solve(n, _conn(_agreement(fee=_fee(50.0), available_from=date(2030, 1, 9))))
    after_static, after_t = _snapshot_links(n)
    pd.testing.assert_frame_equal(before_static, after_static)
    # …while the solve itself honoured the date.
    assert n.links_t.p0["import"][n.snapshots < pd.Timestamp("2030-01-09")].abs().max() < 1e-6
    assert n.links_t.p0["import"][n.snapshots >= pd.Timestamp("2030-01-09")].max() > 1.0


@pytest.mark.live_solve
def test_non_firm_static_flow_never_exceeds_the_cap():
    n = build_edge_15min()
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    _solve(n, _conn(_agreement(kind="non_firm_static", cap=30.0)))
    assert n.links_t.p0["import"].max() <= 30.0 + 1e-6


@pytest.mark.live_solve
def test_objective_gap_is_zero_with_the_fee_and_the_row_is_reported():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from services.solver_service import SolverConfig

    n = build_edge_15min()
    commercial = _conn(_agreement(fee=_fee(80.0)))
    _solve(n, commercial)
    cb = compute_cost_breakdown(n, SolverConfig(commercial=commercial))
    dec = compute_objective_decomposition(n, cb)
    assert abs(dec["gap_pct"]) < 1e-6, dec
    expected = 80_000.0 * (7 * 24 / 8760) * n.links.at["import", "p_nom_opt"]
    assert cb["commercial"]["network_capacity"] == pytest.approx(expected, rel=1e-9)


@pytest.mark.live_solve
def test_multi_period_available_from_blocks_the_early_period_and_reconciles():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from services.solver_service import SolverConfig

    n = _two_period()
    # One simulated day per period: a small fee so importing beats the backup.
    commercial = _conn(_agreement(fee=_fee(1.0), available_from=date(2035, 1, 1)))
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, multi_investment_periods=True,
                       investment_periods=[2030, 2035])
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, condition)
    p0 = n.links_t.p0["import"]
    assert p0.loc[2030].abs().max() < 1e-6 and p0.loc[2035].max() > 1.0
    cb = compute_cost_breakdown(n, cfg)
    dec = compute_objective_decomposition(n, cb)
    assert abs(dec["gap_pct"]) < 1e-6, dec
