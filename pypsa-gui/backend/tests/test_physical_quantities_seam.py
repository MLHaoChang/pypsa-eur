"""
Edge Investment Case — physical-quantity seam (Phase 0, WP0.3).

Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md decision 11
Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.3

One accessor returns the physical quantities (capacity, build year,
lifetime, annualised capital cost, FOM, dispatched energy, PoC flows) that
BOTH the Economics tab and the finance layer consume, so the two can never
disagree. The agreement test below compares it to `compute_asset_economics`
on the golden network, asset by asset.

Written red first: `services.results.physical_quantities` did not exist.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

import routers.results as R
import routers.simulation as sim_router
from tests.golden import fixture as gf

pytestmark = pytest.mark.live_solve


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _cfg():
    return sim_router._state["solver_config"]


def _plain_result_df(n, accessor, attr, source="lopf"):
    acc = getattr(n, accessor, None)
    return None if acc is None else getattr(acc, attr, None)


def _close(a, b, rel=1e-9, abs_=1e-6):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_)


def test_returns_frames_for_every_cost_bearing_class(golden):
    from services.results.physical_quantities import physical_quantities

    pq = physical_quantities(golden, _cfg(), result_df=_plain_result_df)
    assert set(pq["components"]) >= {"generators", "storage_units", "stores", "links"}
    gens = pq["components"]["generators"]
    assert isinstance(gens, pd.DataFrame)
    for col in ("bus", "carrier", "p_nom_opt", "build_year", "lifetime",
                "capital_cost_annualised", "overnight_cost", "overnight_cost_available",
                "fom_cost_per_unit", "energy_mwh", "fixed_cost_eur", "fom_cost_eur_annual"):
        assert col in gens.columns, col
    assert pq["is_multi_period"] is True
    assert list(pq["periods"]) == list(gf.GOLDEN_PERIODS)
    assert pq["total_years_factor"] == pytest.approx(float(sum(gf.GOLDEN_YEARS)))
    assert pq["capital_costs_available"] is True
    # Guards the result_df test below from going vacuous if the fixture ever
    # stops dispatching.
    assert gens["energy_mwh"].sum() > 0


def test_agrees_with_asset_economics_generators(golden):
    from services.results.asset_economics import compute_asset_economics
    from services.results.physical_quantities import physical_quantities

    econ = compute_asset_economics(golden, _cfg(), result_df=R._result_df)
    pq = physical_quantities(golden, _cfg(), result_df=R._result_df)
    gens = pq["components"]["generators"]
    assert len(econ["generators"]) == len(gens) > 0
    for row in econ["generators"]:
        g = row["name"]
        assert g in gens.index
        assert _close(row["p_nom_opt_mw"], gens.at[g, "p_nom_opt"]), g
        assert _close(row["energy_mwh"], gens.at[g, "energy_mwh"]), g
        assert _close(row["fixed_cost_eur"], gens.at[g, "fixed_cost_eur"]), g
        # FOM is part of fixed cost on both surfaces since the FOM
        # reconciliation (P2 WP2.0); `fom_cost_eur` is its share, on the same
        # per-horizon, active-years basis.
        assert _close(row["fom_cost_eur"], gens.at[g, "fom_cost_eur"]), g


def test_agrees_with_asset_economics_storage_and_links(golden):
    from services.results.asset_economics import compute_asset_economics
    from services.results.physical_quantities import physical_quantities

    econ = compute_asset_economics(golden, _cfg(), result_df=R._result_df)
    pq = physical_quantities(golden, _cfg(), result_df=R._result_df)
    su = pq["components"]["storage_units"]
    assert len(econ["storage_units"]) == len(su) > 0
    for row in econ["storage_units"]:
        s = row["name"]
        assert _close(row["p_nom_opt_mw"], su.at[s, "p_nom_opt"]), s
        assert _close(row["discharge_mwh"], su.at[s, "discharge_mwh"]), s
        assert _close(row["charge_mwh"], su.at[s, "charge_mwh"]), s
        assert _close(row["fixed_cost_eur"], su.at[s, "fixed_cost_eur"]), s
    links = pq["components"]["links"]
    assert len(econ["links"]) == len(links) > 0
    for row in econ["links"]:
        ln = row["name"]
        assert _close(row["p_nom_opt_mw"], links.at[ln, "p_nom_opt"]), ln
        assert _close(row["energy_mwh"], links.at[ln, "output_mwh"]), ln
        assert _close(row["input_energy_mwh"], links.at[ln, "input_mwh"]), ln
        assert _close(row["fixed_cost_eur"], links.at[ln, "fixed_cost_eur"]), ln


def test_fixed_cost_is_the_lp_rate_times_capacity_times_active_years(golden):
    """P2 WP2.0 (the merged FOM rule): (annuitised investment + FOM per horizon)
    × capacity × the years the asset is active — what the LP charges."""
    from services.period_utils import active_period_years
    from services.results.physical_quantities import physical_quantities
    from services.solver.periodized_costs import fom_per_horizon

    pq = physical_quantities(golden, _cfg(), result_df=_plain_result_df)
    gens = pq["components"]["generators"]
    fom = fom_per_horizon(golden, golden.generators["fom_cost"]).rename(str)
    active = active_period_years(golden, "Generator")
    years = (active.sum(axis=1).rename(str) if active is not None
             else pd.Series(pq["total_years_factor"], index=gens.index))
    expect = (gens["capital_cost_annualised"] + fom.reindex(gens.index)) * gens["p_nom_opt"] \
        * years.reindex(gens.index)
    pd.testing.assert_series_equal(gens["fixed_cost_eur"], expect.rename("fixed_cost_eur"),
                                   check_exact=False, rtol=1e-9)


def test_energy_by_period_sums_to_horizon_total(golden):
    from services.results.physical_quantities import physical_quantities

    pq = physical_quantities(golden, _cfg(), result_df=_plain_result_df)
    by_p = pq["energy_by_period"]["generators"]
    assert list(by_p.columns) == list(gf.GOLDEN_PERIODS)
    total = pq["components"]["generators"]["energy_mwh"]
    pd.testing.assert_series_equal(by_p.sum(axis=1).rename("energy_mwh"), total,
                                   check_exact=False, rtol=1e-12)


def test_poc_is_none_when_no_grid_import_link(golden):
    from services.results.physical_quantities import physical_quantities

    pq = physical_quantities(golden, _cfg(), result_df=_plain_result_df)
    assert pq["poc"] is None


def test_poc_flows_when_a_link_is_tagged_grid_import(golden):
    from services.results.physical_quantities import physical_quantities

    golden.links["eh_role"] = ""
    ln = golden.links.index[0]
    golden.links.at[ln, "eh_role"] = "grid_import"
    pq = physical_quantities(golden, _cfg(), result_df=_plain_result_df)
    assert pq["poc"] is not None
    assert pq["poc"]["links"] == [ln]
    imp = pq["poc"]["import_mw"]
    assert isinstance(imp, pd.Series) and len(imp) == len(golden.snapshots)
    assert (imp >= -1e-9).all()
    assert pq["poc"]["import_mwh"] == pytest.approx(
        float((imp * pq["weights"]["energy"]).sum()))


def test_reads_only_through_result_df(golden):
    """The accessor must get dispatch through the injected callable, never from
    router state: passing a callable that returns None for dispatch yields
    zero energy, not a crash and not the live network's values."""
    from services.results.physical_quantities import physical_quantities

    def no_dispatch(n, accessor, attr, source="lopf"):
        return None

    pq = physical_quantities(golden, _cfg(), result_df=no_dispatch)
    assert (pq["components"]["generators"]["energy_mwh"] == 0.0).all()


def test_module_imports_no_router():
    import inspect

    from services.results import physical_quantities as m

    src = inspect.getsource(m)
    assert "from routers" not in src and "import routers" not in src


# ── branches the golden network cannot reach (review WP0.3 #5) ───────────────
#
# Golden has no Store, one single-port Link, always a p1 frame, always
# multi-period. This inline FLAT network covers the rest: a Store that cycles,
# a 3-port CHP Link, two PoC links (one exporting), snapshot weights of 3.


def _edge_network():
    import pypsa

    from services.solver_service import SolverConfig, with_periodized_cost_defaults

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    # objective ≠ generators ON PURPOSE (gate assessor P0 #2): cost sums must
    # use `objective`, energy sums `generators`; equal columns let a swap pass.
    n.snapshot_weightings["objective"] = 3.0
    n.snapshot_weightings["stores"] = 3.0
    n.snapshot_weightings["generators"] = 2.0
    for b in ("gas", "elec", "heat", "grid"):
        n.add("Bus", b)
    n.add("Generator", "gas_supply", bus="gas", p_nom=500.0, marginal_cost=30.0)
    n.add("Generator", "grid_supply", bus="grid", p_nom=500.0, marginal_cost=80.0)
    n.add("Load", "grid_sink_price", bus="grid", p_set=0.0)
    n.add("Link", "chp", bus0="gas", bus1="elec", bus2="heat", efficiency=0.4,
          efficiency2=0.45, p_nom_extendable=True, capital_cost=100.0, fom_cost=5.0)
    n.add("Store", "tank", bus="heat", e_nom_extendable=True, e_cyclic=True,
          capital_cost=2.0)
    n.add("Link", "poc_a", bus0="grid", bus1="elec", p_nom=60.0, p_min_pu=-1.0,
          eh_role="grid_import", marginal_cost=0.1)
    n.add("Link", "poc_b", bus0="grid", bus1="elec", p_nom=60.0, p_min_pu=-1.0,
          eh_role="import", marginal_cost=0.2)
    elec = [40, 40, 40, 40, 90, 90, 40, 40]
    heat = [5, 5, 5, 5, 5, 60, 60, 5]
    n.add("Load", "elec_load", bus="elec", p_set=elec)
    n.add("Load", "heat_load", bus="heat", p_set=heat)
    cfg = SolverConfig(discount_rate=0.07)
    with with_periodized_cost_defaults(n, cfg):
        n.optimize(solver_name="highs")
    n.model.solver_model = None  # allow n.copy() in tests
    return n, cfg


@pytest.fixture(scope="module")
def edge():
    return _edge_network()


def test_edge_store_and_multiport_link_agree_with_asset_economics(edge, reset_backend):
    from services.results.asset_economics import compute_asset_economics
    from services.results.physical_quantities import physical_quantities

    n, cfg = edge
    econ = compute_asset_economics(n, cfg, result_df=_plain_result_df)
    pq = physical_quantities(n, cfg, result_df=_plain_result_df)
    assert pq["is_multi_period"] is False and pq["total_years_factor"] == 1.0
    stores = pq["components"]["stores"]
    assert len(econ["stores"]) == len(stores) == 1
    row = econ["stores"][0]
    assert stores.at["tank", "discharge_mwh"] > 0, "fixture must cycle the store"
    assert _close(row["p_nom_opt_mw"] if "p_nom_opt_mw" in row else row["e_nom_opt_mwh"],
                  stores.at["tank", "p_nom_opt"])
    assert _close(row["discharge_mwh"], stores.at["tank", "discharge_mwh"])
    assert _close(row["charge_mwh"], stores.at["tank", "charge_mwh"])
    assert _close(row["fixed_cost_eur"], stores.at["tank", "fixed_cost_eur"])
    links = pq["components"]["links"]
    chp = next(r for r in econ["links"] if r["name"] == "chp")
    assert _close(chp["energy_mwh"], links.at["chp", "output_mwh"])
    assert _close(chp["input_energy_mwh"], links.at["chp", "input_mwh"])
    # Three-port output counts heat as well as electricity.
    assert links.at["chp", "output_mwh"] > 0.4 * links.at["chp", "input_mwh"] + 1e-6


def test_edge_flat_energy_by_period_is_a_single_column(edge, reset_backend):
    from services.results.physical_quantities import physical_quantities

    n, cfg = edge
    pq = physical_quantities(n, cfg, result_df=_plain_result_df)
    by_p = pq["energy_by_period"]["generators"]
    assert list(by_p.columns) == [None]
    pd.testing.assert_series_equal(by_p[None].rename("energy_mwh"),
                                   pq["components"]["generators"]["energy_mwh"],
                                   check_exact=False, rtol=1e-12)


def test_edge_two_poc_links_are_gross_and_net(edge, reset_backend):
    from services.results.physical_quantities import physical_quantities

    n, cfg = edge
    pq = physical_quantities(n, cfg, result_df=_plain_result_df)
    poc = pq["poc"]
    assert poc["links"] == ["poc_a", "poc_b"]   # both role spellings count
    pd.testing.assert_series_equal(poc["net_mw"], poc["import_mw"] - poc["export_mw"])
    w = pq["weights"]["energy"]
    assert poc["import_mwh"] == pytest.approx(float((poc["import_mw"] * w).sum()))
    assert (w == 2.0).all()                       # energy basis = generators


def test_missing_p1_column_falls_back_to_p0_times_efficiency_per_link(edge, reset_backend):
    """Review WP0.3 #1: a p1 frame that lacks one Link's column must not zero
    that Link's output (asset_economics falls back per link)."""
    from services.results.physical_quantities import physical_quantities

    n, cfg = edge

    def drop_chp_from_p1(net, accessor, attr, source="lopf"):
        df = getattr(getattr(net, accessor), attr, None)
        if accessor == "links_t" and attr == "p1" and df is not None:
            return df.drop(columns=["chp"])
        return df

    pq = physical_quantities(n, cfg, result_df=drop_chp_from_p1)
    links = pq["components"]["links"]
    p0 = n.links_t.p0["chp"]
    p2 = n.links_t.p2["chp"]
    w = pq["weights"]["energy"]
    expect = float(((p0 * 0.4) - p2).mul(w).sum())
    assert links.at["chp", "output_mwh"] == pytest.approx(expect, rel=1e-9)


def test_nan_snapshot_weight_counts_as_one(edge, reset_backend):
    from services.results.physical_quantities import physical_quantities

    n, cfg = edge
    m = n.copy()
    m.snapshot_weightings.iloc[0, :] = float("nan")
    pq = physical_quantities(m, cfg, result_df=_plain_result_df)
    assert pq["weights"]["energy"].iloc[0] == 1.0
    assert not pq["weights"]["energy"].isna().any()



def test_energy_follows_generators_weighting_and_cost_follows_objective(edge, reset_backend):
    from services.results.physical_quantities import physical_quantities

    n, cfg = edge
    pq = physical_quantities(n, cfg, result_df=_plain_result_df)
    assert (pq["weights"]["energy"] == 2.0).all()
    assert (pq["weights"]["cost"] == 3.0).all()
    gens = pq["components"]["generators"]
    for g in gens.index:
        expect = float(n.generators_t.p[g].sum() * 2.0)
        assert gens.at[g, "energy_mwh"] == pytest.approx(expect, rel=1e-12), g
    # Fixed cost owes nothing to snapshot weights (flat network: × 1 year).
    fixed = gens["capital_cost_annualised"] * gens["p_nom_opt"]
    pd.testing.assert_series_equal(gens["fixed_cost_eur"], fixed.rename("fixed_cost_eur"))
