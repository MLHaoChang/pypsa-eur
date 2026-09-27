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
    # The fee is an explicit LP term (review #1/#4), not the user's capital_cost.
    assert row.capital_cost == 0.0
    assert applied.facts["connection"]["fee_eur_per_mw_year"] == 50_000.0
    assert getattr(n, C.FEE_SPEC_ATTR) == {"link": "import", "fee_eur_per_mw_year": 50_000.0}
    applied.undo()
    assert not hasattr(n, C.FEE_SPEC_ATTR)


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


def test_a_dynamic_agreement_without_a_materialised_envelope_is_refused():
    # WP1.4b binds envelopes; one never resolved onto the network is refused.
    n = build_edge_15min()
    env = {"id": "env", "version": 1, "hash": "a" * 64, "source": "t"}
    with pytest.raises(C.CommercialBindingError, match="envelope"):
        C.apply_connection_agreement(
            n, _agreement(kind="non_firm_dynamic", envelope=env), poc_link="import")


def test_a_monthly_fee_is_twelve_times_the_annual_rate():
    n = build_edge_15min()
    monthly = TariffItem(id="m", kind="capacity", unit="per_kw_month",
                         periods=[TariffPeriod(name="all", rate=4.0)])
    applied = C.apply_connection_agreement(n, _agreement(fee=monthly), poc_link="import")
    assert applied.facts["connection"]["fee_eur_per_mw_year"] == 48_000.0


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


# ── Review round 1 (FAIL) — each finding pinned before its fix ─────────────


def _run(n, commercial, **cfg_kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, **cfg_kw)
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    return status, condition, cfg


def _gap_and_cb(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb


@pytest.mark.live_solve
def test_a_fee_on_a_fixed_non_firm_connection_is_reported_outside_the_lp_total():
    """#1: a fee on fixed capacity is a fixed charge — not in the LP, so not in
    the reconciled total; reported, flagged, billed by the billing pass."""
    n = build_edge_15min()
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    status, _, cfg = _run(n, _conn(_agreement(kind="non_firm_static", cap=30.0, fee=_fee(80.0))))
    assert status in ("ok", "optimal")
    gap, cb = _gap_and_cb(n, cfg)
    assert abs(gap) < 1e-6
    fixed = cb["commercial"]["network_capacity_fixed"]
    assert fixed["included_in_total"] is False and "fixed_charge_not_in_lp" in fixed["flags"]
    assert fixed["eur"] == pytest.approx(80_000.0 * 30.0 * 7 * 24 / 8760)


def _promoted_two_period():
    """Flat weather-year snapshots promoted to periods — the GUI's layout: the
    SAME timestamps repeat in every period."""
    base = build_edge_15min()
    n = base.copy()
    n.set_snapshots(base.snapshots[:96])
    n.snapshot_weightings.loc[:, :] = 0.25
    n.loads_t.p_set = base.loads_t.p_set.iloc[:96]
    n.generators_t.p_max_pu = base.generators_t.p_max_pu.iloc[:96]
    n.set_investment_periods([2030, 2035])
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    return n


def test_available_from_on_repeated_timestamps_blocks_by_period():
    """#2: timestamps are 2030 in both periods; 2035 must stay open."""
    n = _promoted_two_period()
    C.apply_connection_agreement(n, _agreement(available_from=date(2035, 1, 1)),
                                 poc_link="import")
    pmp = n.links_t.p_max_pu["import"]
    assert (pmp.loc[2030] == 0.0).all() and (pmp.loc[2035] > 0.0).all()
    assert int(n.links.at["import", "build_year"]) == 2035


def test_non_firm_static_respects_an_existing_availability_profile():
    """#3: PyPSA reads the time-varying column when one exists."""
    n = build_edge_15min()
    n.links_t.p_max_pu["import"] = 0.9
    applied = C.apply_connection_agreement(n, _agreement(kind="non_firm_static", cap=30.0),
                                           poc_link="import")
    assert np.allclose(n.links_t.p_max_pu["import"], 30.0 / 80.0)
    applied.undo()
    assert np.allclose(n.links_t.p_max_pu["import"], 0.9)


@pytest.mark.live_solve
def test_a_fee_reconciles_when_the_poc_link_carries_an_overnight_cost():
    """#4: the fee is an explicit LP term, not smuggled into capital_cost."""
    n = build_edge_15min()
    n.links.loc["import", ["overnight_cost", "lifetime"]] = [1000.0, 40.0]
    status, _, cfg = _run(n, _conn(_agreement(fee=_fee(80.0))))
    assert status in ("ok", "optimal")
    gap, cb = _gap_and_cb(n, cfg)
    assert abs(gap) < 1e-6, gap
    assert cb["commercial"]["network_capacity"] > 0


@pytest.mark.live_solve
@pytest.mark.parametrize("strategy", ["rolling"])
def test_a_fee_with_windowed_dispatch_is_refused(strategy):
    """#5/#6: myopic/rolling charge per window; refused in P1 (P6 scope). Since
    WP1.8 round 2 the preflight states the refusal, so the run stops at
    validation before any binding; the binding itself still refuses."""
    n = build_edge_15min()
    status, condition, _ = _run(n, _conn(_agreement(fee=_fee(80.0))), solve_strategy=strategy)
    assert (status, condition) == ("error", "validation_failed")
    with pytest.raises(C.CommercialBindingError, match=strategy):
        C.apply_commercial_for_solve(build_edge_15min(), _conn(_agreement(fee=_fee(80.0))),
                                     solve_strategy=strategy)


def test_a_fee_with_myopic_foresight_is_refused():
    from models.commercial import CommercialConfig

    n = _promoted_two_period()
    with pytest.raises(C.CommercialBindingError, match="myopic"):
        C.apply_commercial_for_solve(n, _conn(_agreement(fee=_fee(80.0))),
                                     solve_strategy="myopic", multi_period=True)


@pytest.mark.live_solve
def test_horizon_system_cost_agrees_with_the_breakdown_with_commercial_terms():
    """#5: the solve-queue/status-bar total must include the commercial rows."""
    from services.cost_totals import horizon_system_cost

    n = build_edge_15min()
    status, _, cfg = _run(n, {**_conn(_agreement(fee=_fee(80.0))),
                              "import_tariff": {
                                  "id": "t", "name": "t", "jurisdiction": "DE",
                                  "valid_from": "2030-01-01",
                                  "items": [{"id": "e", "kind": "energy", "unit": "per_kwh",
                                             "periods": [{"name": "all", "rate": 0.1}]}]}})
    assert status in ("ok", "optimal")
    gap, cb = _gap_and_cb(n, cfg)
    assert abs(gap) < 1e-6
    assert horizon_system_cost(n, cfg) == pytest.approx(cb["total"], rel=1e-9)


@pytest.mark.live_solve
def test_firm_without_a_fee_keeps_a_user_extendable_link_extendable():
    """#7: capped, not fixed, so the user's own capital cost still reconciles."""
    n = build_edge_15min()
    n.links.loc["import", ["p_nom_extendable", "capital_cost", "p_nom_max"]] = [True, 1000.0, 200.0]
    status, _, cfg = _run(n, _conn(_agreement(cap=60.0, fee=None)))
    assert status in ("ok", "optimal")
    assert n.links.at["import", "p_nom_opt"] <= 60.0 + 1e-6
    gap, _ = _gap_and_cb(n, cfg)
    assert abs(gap) < 1e-6, gap


@pytest.mark.live_solve
def test_breakdown_components_still_sum_to_the_totals():
    """#9: the commercial terms are a component, so Σ by_component == totals."""
    n = build_edge_15min()
    status, _, cfg = _run(n, _conn(_agreement(fee=_fee(80.0))))
    assert status in ("ok", "optimal")
    _, cb = _gap_and_cb(n, cfg)
    comps = cb["by_component"]
    assert sum(c["capex"] for c in comps) == pytest.approx(cb["capex"], rel=1e-9)
    assert sum(c["opex"] for c in comps) == pytest.approx(cb["opex"], rel=1e-9)
    assert any(c["component"] == "Commercial" for c in comps)


def test_available_from_is_read_on_the_site_clock_when_snapshots_are_utc():
    """#10: midnight in Berlin is 23:00 UTC the day before."""
    n = build_edge_15min()
    C.apply_connection_agreement(n, _agreement(available_from=date(2030, 1, 10)),
                                 poc_link="import", timezone="Europe/Berlin")
    pmp = n.links_t.p_max_pu["import"]
    assert pmp.loc[pd.Timestamp("2030-01-09 22:45")] == 0.0
    assert pmp.loc[pd.Timestamp("2030-01-09 23:00")] == 1.0


def test_a_fee_not_established_after_the_solve_is_flagged_not_dropped():
    """#10 ADR-0001: config names a fee, no committed coefficient → flag."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = build_edge_15min()
    terms = commercial_cost_terms(n, _conn(_agreement(fee=_fee(80.0))))
    assert "network_capacity_not_established" in terms["flags"]


# ── Review round 2 (FAIL) ──────────────────────────────────────────────────


def test_an_operational_solve_pins_the_designed_connection():
    """#1: adequacy sweeps (freeze_capacities) must not re-size the connection."""
    n = build_edge_15min()
    n.links.loc["import", "p_nom_opt"] = 35.5
    setattr(n, C.OPERATIONAL_ATTR, True)
    applied = C.apply_connection_agreement(n, _agreement(fee=_fee(500.0)), poc_link="import")
    row = n.links.loc["import"]
    assert bool(row.p_nom_extendable) and row.p_nom_min == pytest.approx(35.5)
    assert row.p_nom_max == pytest.approx(35.5 + 1e-6)
    assert not hasattr(n, C.FEE_SPEC_ATTR)  # no fee term in an operational solve
    applied.undo()
    assert not bool(n.links.at["import", "p_nom_extendable"])


def test_freeze_capacities_marks_the_network_operational():
    from services.adequacy.sweep import freeze_capacities

    n = build_edge_15min()
    undo = freeze_capacities(n)
    assert getattr(n, C.OPERATIONAL_ATTR, False) is True
    undo()
    assert not getattr(n, C.OPERATIONAL_ATTR, False)


def test_the_block_network_capacity_matches_the_weighted_total():
    """#2: the block carries the same years weighting as the capex it is in."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = build_edge_15min()
    n.meta[C.META_FEE] = {"link": "import", "fee_eur_per_mw_year": 1.0,
                          "eur_per_mw_by_period": {"_": 100.0}}
    n.links.loc["import", "p_nom_opt"] = 2.0
    terms = commercial_cost_terms(n, _conn(_agreement(fee=_fee(1.0))), years=lambda p: 5.0)
    assert terms["block"]["network_capacity"] == pytest.approx(200.0)  # flat: ×1


def test_fca_hours_are_chosen_among_open_snapshots_and_disclosed():
    """#3/#9: never spend curtailment where the connection is already closed;
    accumulate real weights; disclose target and achieved hours."""
    n = build_edge_15min()
    agr = ConnectionAgreement(kind="fca", import_cap_mw=40.0, curtailment_hours_per_year=876.0,
                              available_from=date(2030, 1, 10))
    entry = C.fca_stress_entry(n, agr, poc_link="import")
    series = np.asarray(entry["links_p_max_pu"]["import"])
    closed = np.asarray(n.snapshots < pd.Timestamp("2030-01-10"))
    zeroed_open = (series == 0.0) & ~closed
    assert zeroed_open.sum() > 0
    assert entry["target_hours"] == pytest.approx(876.0 * 168 / 8760)
    assert entry["achieved_hours"] <= entry["target_hours"] + 1e-9
    assert entry["achieved_hours"] == pytest.approx(0.25 * zeroed_open.sum())


def test_a_dst_gap_at_midnight_does_not_crash():
    """#8: America/Santiago skips 00:00 on 2030-09-08."""
    n = build_edge_15min()
    C.apply_connection_agreement(n, _agreement(available_from=date(2030, 9, 8)),
                                 poc_link="import", timezone="America/Santiago")


def test_the_fixed_fee_counts_only_open_time():
    """#7: a fixed fee accrues only while the connection is available."""
    n = build_edge_15min()
    applied = C.apply_connection_agreement(
        n, _agreement(kind="non_firm_static", cap=30.0, fee=_fee(80.0),
                      available_from=date(2030, 1, 10)), poc_link="import")
    open_h = float(n.snapshot_weightings.objective[n.snapshots >= pd.Timestamp("2030-01-10")].sum())
    assert applied.facts["connection"]["fixed_fee_eur"] == pytest.approx(
        80_000.0 * 30.0 * open_h / 8760)


# ── Phase 1 gate binding condition 2 ───────────────────────────────────────


@pytest.mark.live_solve
@pytest.mark.parametrize("changed", [
    lambda: _agreement(fee=_fee(120.0)),              # the fee rate
    lambda: _agreement(cap=50.0, fee=_fee(60.0)),     # the import cap
    lambda: _agreement(fee=_fee(60.0), available_from=date(2030, 1, 3)),
])
def test_a_changed_connection_agreement_after_the_solve_is_drift(changed):
    """The fee row is the SOLVED agreement's; an edit without a re-solve is
    disclosed like a demand or tier change (gate finding #1)."""
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    n = build_edge_15min()
    status, _, cfg = _run(n, _conn(_agreement(fee=_fee(60.0))))
    assert status in ("ok", "optimal")
    same = compute_cost_breakdown(n, cfg)["commercial"]["flags"]
    assert "config_changed_since_solve" not in same
    edited = SolverConfig(commercial=_conn(changed()))
    assert "config_changed_since_solve" in compute_cost_breakdown(n, edited)["commercial"]["flags"]
