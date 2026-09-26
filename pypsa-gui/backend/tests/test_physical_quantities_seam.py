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
        # TODO(FOM): asset_economics reports FOM as an annual informational
        # figure only and excludes it from fixed cost; the objective includes
        # it (see notes/2026-09-26-edge-client-feature-benchmark.md §1.6, fix
        # delegated). Until that lands, the seam exposes both and we compare
        # the annual FOM figure explicitly.
        assert _close(row["fom_cost_eur"], gens.at[g, "fom_cost_eur_annual"]), g


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


def test_fixed_cost_is_capital_cost_times_capacity_times_years(golden):
    from services.results.physical_quantities import physical_quantities

    pq = physical_quantities(golden, _cfg(), result_df=_plain_result_df)
    gens = pq["components"]["generators"]
    expect = gens["capital_cost_annualised"] * gens["p_nom_opt"] * pq["total_years_factor"]
    pd.testing.assert_series_equal(gens["fixed_cost_eur"], expect.rename("fixed_cost_eur"),
                                   check_exact=False, rtol=1e-12)


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
