"""
Cross-surface checks: does the Compare tab agree with the live `/results/*`
endpoints that compute the same quantities from the active network?

`install_golden(n)` makes the golden network the live singleton so the
`routers.results` / `routers.simulation` functions imported here read the
SAME network `compare_support.summarise()` reads. Every test below reads the
target function/endpoint's REAL response shape (verified by hand against a
running golden network before the assertion was written — see the
task-5-6-report.md for the raw probe output) rather than guessing key names,
and carries a `compared >= 1` (or equivalent) guard so a silent shape drift
fails loudly instead of iterating zero times and passing vacuously.
"""
from __future__ import annotations

import pytest

from tests import compare_local_networks as cln
from tests import compare_support as cs
from tests.golden import fixture as gf


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


# ── Task 5: Capacity tab — CAPEX vs. periodized_capital_costs ──────────────

def test_capacity_capex_agrees_with_periodized_capital_costs(golden):
    """
    Σ per-carrier capex_meur must equal Σ over assets of
    periodized_capital_costs × p_nom_opt × horizon years — the resolution
    asset_economics, cost_breakdown and asset_costs all share.
    """
    from services.solver_service import periodized_capital_costs
    from routers.simulation import _state

    cap = cs.summarise(golden)["capacity"]
    tab_total_eur = sum(c.total for c in cap.capex_meur_by_carrier.values()) * 1e6

    pcc = periodized_capital_costs(golden, _state.get("solver_config"))
    horizon_years = float(sum(gf.GOLDEN_YEARS))
    expected = 0.0
    # EVERY cost-bearing class, passive branches included, and every link
    # rather than only the extendable ones.
    #
    # Both of those used to be otherwise, and this list had drifted from the
    # code twice over. The link entry still said `extendable_only=True` long
    # after `_compute_total_annuitised_capex` stopped restricting the link
    # walk; it kept passing only because every link in the golden fixture
    # happens to be extendable, so the two policies coincide here and the
    # stale expectation was never exercised.
    #
    # Lines and transformers are new, per the 2026-08-13 ruling that a passive
    # branch's capital cost is part of what the system costs. Omitting them
    # here was worth 7500 MEUR on this very fixture — see
    # tests/test_capex_line_inclusion_parity.py for the measurement.
    for attr, nom, ext_only in (("generators", "p_nom", False),
                                ("storage_units", "p_nom", False),
                                ("stores", "e_nom", False),
                                ("links", "p_nom", False),
                                ("lines", "s_nom", False),
                                ("transformers", "s_nom", False)):
        df = getattr(golden, attr)
        for name in df.index:
            if ext_only and not bool(df.at[name, f"{nom}_extendable"]):
                continue
            cc = pcc.get(attr, {}).get(name, {}).get("capital_cost", 0.0)
            opt = float(df.at[name, f"{nom}_opt"] if f"{nom}_opt" in df.columns
                        else df.at[name, nom])
            expected += cc * opt * horizon_years

    assert tab_total_eur == pytest.approx(expected, rel=1e-6)


# ── Task 6: Dispatch tab vs. /results/carrier_kpis ──────────────────────────

def test_dispatch_agrees_with_carrier_kpis(golden):
    """
    `GET /results/carrier_kpis` returns `{"rows": [...]}` — NOT `{"carriers":
    [...]}`. Each row is `{"component", "carrier", "capacity_mw",
    "energy_mwh", "capacity_factor_pct", "curtailment_mwh", "curtailment_pct",
    "market_value_eur_per_mwh", "revenue_eur"}` (verified by calling
    `get_carrier_kpis()` directly against the solved golden network — see
    task-5-6-report.md for the raw dump). There is no `energy_gwh` key: energy
    is `energy_mwh`, in MWh, so it must be divided by 1000 before comparing to
    the Compare tab's `dispatch_gwh_by_carrier` (GWh). Rows are per
    (component, carrier) — e.g. a Link's H2 energy and a Generator's gas
    energy are separate rows — so this only compares the component classes
    the dispatch tab itself aggregates (Generator, StorageUnit; see
    `_compute_dispatch_summary` in routers/compare.py, which walks
    `n.generators_t.p` and the clipped-non-negative half of
    `n.storage_units_t.p`, and does NOT include Links or Stores).
    """
    import routers.results as R

    disp = cs.summarise(golden)["dispatch"]
    kpis = R.get_carrier_kpis()
    # Shape check first — a silently-renamed top-level key would otherwise
    # make the loop below iterate zero times and pass vacuously.
    assert "rows" in kpis and kpis["rows"], (
        "carrier_kpis returned nothing for a solved network")
    compared = 0
    for row in kpis["rows"]:
        if row.get("component") not in ("Generator", "StorageUnit"):
            continue
        carrier = str(row.get("carrier", "")).lower()
        energy_mwh = row.get("energy_mwh")
        if carrier not in disp.dispatch_gwh_by_carrier or energy_mwh is None:
            continue
        want_gwh = energy_mwh / 1000.0
        assert disp.dispatch_gwh_by_carrier[carrier].total == pytest.approx(want_gwh, rel=1e-6)
        compared += 1
    assert compared >= 1, "no carrier compared — key names have drifted"


# ── Task 8: Prices tab vs. /results/prices ──────────────────────────────────

def test_prices_mean_agrees_with_results_prices(golden):
    """
    `GET /results/prices` returns `{"index", "columns", "data", "data_adjusted",
    "periods", "source", "negative_hours", "fallback_per_snapshot", "note"}`
    (verified by calling `get_prices()` directly against the solved golden
    network). On the golden fixture: `index` is 48 ISO timestamps, `columns`
    is the 4 bus names (`elec_a`, `elec_b`, `h2`, `iso_bus`), `data` and
    `data_adjusted` are both 48x4 matrices of EUR/MWh (in this fixture they
    happen to be numerically identical cell-for-cell — no subsidised
    renewable ever sets the dual — but `data_adjusted` is the semantically
    correct one to compare against: it's the merit-order-corrected series,
    matching what `_compute_prices_summary` reads via
    `corrected_marginal_prices`, per that function's own docstring). There is
    no top-level "mean"/"prices" key, so the tab's `mean_price.total` is
    recomputed here directly from the raw matrix as the snapshot-weighted
    mean, using the SAME weighting basis `_compute_prices_summary` uses:
    `_build_snapshot_weights(n)` with no explicit column defaults to
    `period_utils.snapshot_weights(n, "objective")`.

    Verified numerically before this assertion was written: weighted mean of
    `data_adjusted` == `prices.mean_price.total` exactly (ratio 1.0, 0
    differing cells) on the golden fixture — see task-5b-7-8-report.md.
    """
    import numpy as np

    import routers.results as R
    from services import period_utils

    prices = cs.summarise(golden)["prices"]
    resp = R.get_prices()
    # Shape check first — a silently-renamed/-reshaped payload would otherwise
    # make the weighted-mean computation below silently compare against an
    # empty or malformed matrix and pass vacuously.
    assert "data_adjusted" in resp and resp["data_adjusted"], (
        "get_prices returned nothing for a solved network")
    data_adj = np.asarray(resp["data_adjusted"], dtype=float)
    assert data_adj.shape == (len(golden.snapshots), len(golden.buses)), (
        f"unexpected /results/prices shape {data_adj.shape} for "
        f"{len(golden.snapshots)} snapshots x {len(golden.buses)} buses — "
        "key names or payload shape have drifted")

    w = period_utils.snapshot_weights(golden, "objective")
    w_full = np.broadcast_to(np.asarray(w.values).reshape(-1, 1), data_adj.shape)
    compared = int(data_adj.size)
    assert compared >= 1, "no price cells returned — shape has drifted"
    want = float((data_adj * w_full).sum() / w_full.sum())
    assert prices.mean_price.total == pytest.approx(want, rel=1e-6)


# ── Task 9: Emissions tab vs. /results/emissions ────────────────────────────

def test_emissions_agree_with_the_results_emissions_endpoint(golden):
    """
    `GET /results/emissions` (`R.get_emissions()`) returns a dict with keys
    `{'total_tCO2', 'by_carrier', 'by_generator', 'cap', 'caps',
    'is_multi_period', 'by_period'}` — verified by calling `get_emissions()`
    directly against the solved golden network. NONE of the brief's guessed
    total-key candidates (`total_kt`, `total_co2_kt`, `total`) exist in the
    real response; the actual key is `total_tCO2`. It is also in a DIFFERENT
    UNIT than the Compare tab's `total_kt` — tonnes, not kilotonnes (1 kt =
    1000 t) — so a bare key rename would not have been enough.

    Measured on the golden fixture: live `total_tCO2` = 8537.142857142859 t;
    Compare `total_kt.total` = 8.53714285714286 kt;
    8537.142857142859 / 1000 == 8.53714285714286 (ratio 1.0 exactly).
    """
    import routers.results as R

    em = cs.summarise(golden)["emissions"]
    live = R.get_emissions()
    # Shape check first — a silently-renamed/-reshaped payload would
    # otherwise let the comparison below fail with a confusing KeyError
    # instead of a clear "the endpoint shape moved" message.
    assert live, "emissions endpoint returned nothing"
    assert "total_tCO2" in live, f"no total_tCO2 key in {sorted(live)}"
    assert em.total_kt.total == pytest.approx(live["total_tCO2"] / 1000.0, rel=1e-6)


# ── Task 10: Economics tab vs. /results/asset_economics (suspect S3) ────────

def test_compare_capex_agrees_with_asset_economics_per_carrier(golden):
    """
    `GET /results/asset_economics` (`R.get_asset_economics()`) returns a dict
    with keys `{'currency', 'is_multi_period', 'periods', 'generators',
    'storage_units', 'stores', 'links'}` — verified by calling
    `get_asset_economics()` directly against the solved golden network.
    Unlike Task 6's `carrier_kpis` guess, the brief's bucket names here
    (`generators`/`storage_units`/`stores`/`links`) and field name
    (`fixed_cost_eur`) are CORRECT; each row also carries `carrier`.
    `fixed_cost_eur` is already horizon-total (the 922eb4d0 fix:
    `fixed_cost = fixed_cost_annual * total_years_factor` — see
    `get_asset_economics`'s own docstring), matching Compare's `capex_meur`
    (also horizon-total, per the S3 identity test above).

    Measured: gas/solar/diesel/h2 all agree exactly (ratio 1.0 — e.g. gas
    376323.6185743867 == 376323.6185743867). The lone `storage_units` row
    (`bess`) carries `carrier=''` (StorageUnit has no carrier set in this
    fixture) so it never matches a Compare carrier bucket and is correctly
    excluded rather than silently miscounted.
    """
    import routers.results as R

    econ = cs.summarise(golden)["economics"]
    live = R.get_asset_economics()
    by_carrier = {}
    for bucket in ("generators", "storage_units", "stores", "links"):
        for row in live.get(bucket, []) or []:
            c = str(row.get("carrier", "") or "").lower()
            by_carrier[c] = by_carrier.get(c, 0.0) + float(row.get("fixed_cost_eur") or 0.0)
    compared = 0
    for carrier, e in econ.by_carrier.items():
        if carrier not in by_carrier:
            continue
        assert e.capex_meur.total * 1e6 == pytest.approx(by_carrier[carrier], rel=1e-6), carrier
        compared += 1
    assert compared >= 1, "no carrier compared — asset_economics keys have drifted"


# ── Task 11: Curtailment tab vs. /results/curtailment ───────────────────────
#
# The golden fixture never curtails at all (see test_compare_invariants.py's
# KNOWN_VACUOUS_TABS["curtailment"] and compare_local_networks.py's module
# docstring), so this reads live data from the SAME purpose-built local
# network `test_compare_invariants.py`'s Task 11 identity tests use, rather
# than `golden`.

def test_curtailment_agrees_with_results_curtailment():
    """
    `GET /results/curtailment` (`R.get_curtailment()`) returns `{index,
    columns, data}` — a raw PER-SNAPSHOT MW time series per curtailable
    generator (`(p_max - p).clip(lower=0)`, renewable-carrier-filtered), NOT
    a pre-aggregated GWh total. Verified by calling `get_curtailment()`
    directly against `compare_local_networks.solve_curtailment_network()`
    (there is no `"gwh"`/`"total"` key at all — the brief's guess of a
    pre-aggregated payload does not hold here any more than Task 6's
    `carrier_kpis` guess held for `energy_gwh`).

    Recomputed here as the SAME weighted-sum-to-GWh
    `_compute_curtailment_summary` performs (`_build_snapshot_weights(n,
    "generators")`, then ÷1000 for GWh) and compared against the Compare
    tab's `total_gwh.total`.

    Measured on the local curtailment network: live `data` = `[[80.0],
    [40.0], [80.0], [40.0]]` under `columns=['solar']`, summing to 240.0 MWh;
    Compare's `total_gwh.total` = 0.24 GWh; 240.0 / 1000 == 0.24 (ratio 1.0
    exactly, 0 differing cells).
    """
    import numpy as np

    import routers.results as R
    from services import period_utils

    n = cln.solve_curtailment_network()
    cln.install_network(n)

    cur = cs.summarise(n)["curtailment"]
    resp = R.get_curtailment()
    # Shape check first — a silently-renamed/-reshaped payload would
    # otherwise make the weighted-sum computation below silently compare
    # against an empty or malformed matrix and pass vacuously.
    assert "data" in resp and resp["data"], (
        "curtailment endpoint returned nothing for a solved, curtailing network")
    assert resp.get("columns"), f"no curtailable columns in response: {sorted(resp)}"

    data = np.asarray(resp["data"], dtype=float)
    assert data.shape == (len(n.snapshots), len(resp["columns"])), (
        f"unexpected /results/curtailment shape {data.shape} for "
        f"{len(n.snapshots)} snapshots x {len(resp['columns'])} curtailable "
        "generators — key names or payload shape have drifted")

    w = period_utils.snapshot_weights(n, "generators")
    w_full = np.broadcast_to(np.asarray(w.values).reshape(-1, 1), data.shape)
    compared = int(data.size)
    assert compared >= 1, "no curtailment cells returned — shape has drifted"
    want_gwh = float((data * w_full).sum()) / 1000.0
    assert cur.total_gwh.total == pytest.approx(want_gwh, rel=1e-6)


# ── Task 14: Capacity vs. Economics total CAPEX (suspect S1) ────────────────

def test_capacity_and_economics_agree_on_total_capex(golden):
    """
    Two tabs of one comparison must not report different CAPEX for one
    network. This FAILED until 2026-08-04: `_compute_total_annuitised_capex`
    walked only Generator/StorageUnit/Store while `_compute_economics_summary`
    walked those PLUS Link, so the golden fixture's extendable `electrolyzer`
    (~28.57 MW built, horizon CAPEX EUR166,249.77) was counted by one tab and
    not the other.

    MEASURED before the fix: Capacity 25.154535 M€ vs Economics 25.320785 M€ —
    a 0.166250 M€ gap equal to the electrolyzer's CAPEX
    (0.16624977136776928 M€) to every printed digit. On a real three-period
    project the same defect measured a 56.192453 M€ gap, exactly that
    project's two links' horizon CAPEX.

    Resolved by the product owner on 2026-08-04: EXTENDABLE links are now
    included in the Capacity tab, passive branches remain excluded. See
    `_compute_total_annuitised_capex`'s trailing comment and findings §S1.

    KNOWN RESIDUAL, deliberately not closed: `_compute_economics_summary`
    walks EVERY link with a positive capital_cost, not only extendable ones,
    so a NON-extendable link carrying a capital_cost would still appear in
    Economics and not here. No such asset exists on this fixture, which is
    why this test passes — it does not prove the general case.
    """
    s = cs.summarise(golden)
    cap_total = sum(c.total for c in s["capacity"].capex_meur_by_carrier.values())
    econ_total = sum(e.capex_meur.total for e in s["economics"].by_carrier.values())
    assert cap_total == pytest.approx(econ_total, rel=1e-6), (
        f"Capacity tab {cap_total} M€ vs Economics tab {econ_total} M€")


# ── Dispatch tab OPEX vs. Results cost_breakdown ────────────────────────────
def test_dispatch_opex_agrees_with_cost_breakdown(golden):
    """
    The Dispatch tab's headline OPEX must equal Results' cost_breakdown OPEX
    and the sum of Economics' gen_cost split — all three are the LP's
    variable cost, Σ marginal_cost × dispatch × weight.

    MEASURED before the fix, golden fixture: Dispatch said 2.494286 MEUR
    while Economics and Results both said 2.597143. The 0.102857 gap is the
    electrolyzer Link's VOM (|p0| 10 285.714 MWh × 10 €/MWh) — the dispatch
    summary's opex loop covered generators ONLY, so any Link or StorageUnit
    marginal cost was silently absent from one of the three surfaces.

    Same defect shape as the 7500 MEUR capex gap fixed at 75786a49: a
    component class present in one walk and missing from another, invisible
    because no cross-surface test compared the two totals.
    """
    import routers.simulation as sim_router
    from routers.results import get_cost_breakdown
    from services.solver_service import SolverConfig

    sim_router._state["solver_config"] = SolverConfig()

    s = cs.summarise(golden)
    dispatch_opex = s["dispatch"].opex_meur.total
    econ_gen_cost = sum(e.gen_cost_meur.total for e in s["economics"].by_carrier.values())

    payload = get_cost_breakdown()
    results_opex = float(payload["opex"]) / 1e6

    assert econ_gen_cost == pytest.approx(results_opex, rel=1e-6), (
        f"Economics gen_cost {econ_gen_cost} vs Results {results_opex} — "
        f"these two already agreed before this test existed; a failure here "
        f"is a NEW regression, not the known dispatch gap"
    )
    assert dispatch_opex == pytest.approx(results_opex, rel=1e-6), (
        f"Dispatch tab says {dispatch_opex} MEUR, Results cost_breakdown "
        f"says {results_opex} — the same LP cannot have two variable costs"
    )


def test_dispatch_opex_counts_storage_discharge_vom_like_economics():
    """
    The golden fixture's storage unit carries marginal_cost=0, so the
    dispatch-vs-cost_breakdown test above exercises the LINK half of the
    OPEX fix and not the storage half — the same no-fixture-reaches-the-
    branch trap that let both capex parity suites pass while the views
    disagreed by 7500 MEUR. This network gives BOTH a link and a storage
    unit a non-zero marginal_cost, then requires the Dispatch headline to
    equal Economics' gen_cost sum.

    Convention pinned: storage VOM applies to the clipped DISCHARGE half
    only (the charge side is a bus-price transfer, not an LP cost), links
    on raw signed p0 — both mirroring `_walk_dispatch_side`.
    """
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "h2", carrier="h2")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    n.add("Carrier", "h2")
    n.add(
        "Generator", "gas",
        bus="elec", carrier="gas",
        p_nom=200.0, marginal_cost=50.0,
    )
    # The peak exceeds the gas plant's 200 MW, so the battery MUST charge in
    # the low hours and discharge into the peak — its VOM term is genuinely
    # non-zero. (A peak within gas capacity defeats the fixture: the LP just
    # runs gas and the battery sits idle, which the guard below catches.)
    n.add("Load", "L", bus="elec", p_set=[60.0, 60.0, 60.0, 60.0, 250.0, 250.0])
    n.add(
        "StorageUnit", "bess",
        bus="elec", carrier="battery",
        p_nom=80.0, max_hours=4.0, marginal_cost=7.0,
        efficiency_store=1.0, efficiency_dispatch=1.0,
    )
    n.add(
        "Link", "electrolyzer",
        bus0="elec", bus1="h2", carrier="h2",
        p_nom=30.0, efficiency=0.7, marginal_cost=12.0,
    )
    n.add("Load", "HL", bus="h2", p_set=10.0)
    n.optimize(solver_name="highs")

    s = cs.summarise(n)
    dispatch_opex = s["dispatch"].opex_meur.total
    econ_gen_cost = sum(e.gen_cost_meur.total for e in s["economics"].by_carrier.values())

    # Guard against the vacuous case: if neither the battery nor the
    # electrolyzer actually ran, this network proves nothing.
    assert float(n.storage_units_t.p["bess"].clip(lower=0).sum()) > 1e-6, (
        "fixture defeated — the battery never discharged"
    )
    assert float(n.links_t.p0["electrolyzer"].abs().sum()) > 1e-6, (
        "fixture defeated — the electrolyzer never ran"
    )

    assert dispatch_opex == pytest.approx(econ_gen_cost, rel=1e-6), (
        f"Dispatch {dispatch_opex} MEUR vs Economics gen_cost {econ_gen_cost} "
        f"— storage discharge VOM or link VOM is missing from one walk"
    )


# ── Economics tab revenue vs. /results/asset_economics ──────────────────────
def test_economics_revenue_agrees_with_asset_economics(golden):
    """
    Per-carrier revenue on the Economics tab must equal the same carrier's
    revenue summed over /results/asset_economics rows.

    MEASURED in the 2026-08-14 sweep before the fix: carrier h2 (the
    electrolyzer Link) showed economics=0.0 against asset_econ=0.2136903 —
    `_walk_dispatch_side`'s revenue block read `row.get("bus")`, links carry
    `bus0`/`bus1` and no `bus` column, so every pure-link carrier's revenue
    was a fabricated 0.00 while the Results side computed the real figure.
    A zero that means "unimplemented branch" rendering identically to a
    measured zero is ADR-0001's exact prohibition, wearing the Economics
    table's Revenue column.

    Class mapping mirrors what each surface actually reports: generators and
    links carry `revenue_eur` (net of input cost for links, matching this
    codebase's LCOH accounting), storage_units carry `discharge_revenue_eur`
    (the gross discharge side — Compare books the charge side separately in
    `storage_charge_cost_eur`). Stores are deliberately absent: neither
    surface computes store revenue today.
    """
    import routers.results as R
    import routers.simulation as sim_router
    from services.solver_service import SolverConfig

    sim_router._state["solver_config"] = SolverConfig()

    econ = {
        k: e.revenue_meur.total
        for k, e in cs.summarise(golden)["economics"].by_carrier.items()
    }

    ae = R.get_asset_economics()
    per_car: dict[str, float] = {}
    for cls, key in (
        ("generators", "revenue_eur"),
        ("links", "revenue_eur"),
        ("storage_units", "discharge_revenue_eur"),
    ):
        for row in ae.get(cls) or []:
            val = row.get(key)
            if val is None:
                continue
            car = str(row.get("carrier", "")).lower()
            per_car[car] = per_car.get(car, 0.0) + float(val) / 1e6

    # Guards: the fixture must exercise the branch that diverged — a carrier
    # whose ONLY revenue-bearing asset is a Link.
    assert "h2" in per_car and per_car["h2"] > 0, (
        "fixture defeated — asset_economics no longer reports link revenue "
        "for h2, so this test cannot see the divergent branch"
    )
    compared = 0
    for car, av in per_car.items():
        if car not in econ:
            continue
        assert econ[car] == pytest.approx(av, rel=1e-6), (
            f"carrier {car!r}: Economics tab says {econ[car]} MEUR, "
            f"asset_economics sums to {av} — the same assets cannot earn "
            f"two different revenues depending on which tab is open"
        )
        compared += 1
    assert compared >= 3, f"only {compared} carriers compared — key drift?"


# ── Storage cycles: the two Compare-internal implementations ────────────────
@pytest.mark.parametrize("solve_fixture", [
    cln.solve_storage_cycling_flat_network,
    cln.solve_storage_cycling_multi_network,
])
def test_dispatch_cycles_agree_with_the_cycling_block(solve_fixture):
    """
    Compare computes equivalent cycles TWICE: `_compute_dispatch_summary`'s
    `storage_cycles_by_carrier` and `_compute_storage_cycling_summary`'s
    `cycles_by_carrier`. Both render in the same view. They have already
    diverged once — the dispatch walk summed throughput across the horizon
    against a single period's energy cap, reporting n_periods× the per-year
    rate until it was aligned to mean-per-year (see the comment block in the
    dispatch walk) — and nothing but this test holds them together now.

    The 2026-08-14 sweep measured them agreeing (2.0 == 2.0 on both
    fixtures); this pins that agreement rather than fixing anything.
    """
    n = solve_fixture()
    s = cs.summarise(n)
    d = s["dispatch"].storage_cycles_by_carrier
    c = s["storage_cycling"].cycles_by_carrier

    shared = set(d) & set(c)
    assert shared, (
        f"no carrier computed by both implementations — dispatch={sorted(d)} "
        f"cycling={sorted(c)}; the fixture no longer exercises the overlap"
    )
    for car in shared:
        assert d[car].total == pytest.approx(c[car].total, rel=1e-9), (
            f"carrier {car!r}: dispatch walk says {d[car].total} cycles, "
            f"cycling block says {c[car].total} — the same fleet cannot "
            f"cycle at two different rates in one view"
        )
