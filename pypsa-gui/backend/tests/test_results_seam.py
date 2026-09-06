"""
The seam is transparent, or this test says where it is not.

For every handler whose body was lifted into `services/results/`, call the
handler AND its `compute_*` function on the same solved golden network and
assert the payloads are JSON-identical. The handler still owns the network
lookup, the freshness gate and the `_state` reads; the compute function gets
exactly what the handler passes it. If those two ever disagree, the lift
changed behaviour, and the failing case names the endpoint.

Written before the compute functions existed, so it went red on
`ModuleNotFoundError` first.

Two things about how the comparison is made:

- `_result_df` is passed through from the router. The point of injecting it
  is that a test CAN pass a plain `getattr` lambda instead — one case below
  does, to prove the arithmetic runs with no router state at all — but the
  seam test proper compares like with like.
- Handlers are called directly as plain Python, the way `test_golden_economics`
  and `test_compare_cross_surface` already do. Unset `Query(...)` defaults then
  arrive as `fastapi.params.Query` objects rather than `None`; `wants_slice`
  handles that on both sides identically, which is why it moved with the body.
"""
from __future__ import annotations

import json

import pytest
from fastapi import Response

import routers.results as R
import routers.simulation as sim_router
from tests.golden import fixture as gf


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _canon(payload) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _assert_seam(handler_out, compute_out, *, label: str):
    if isinstance(handler_out, Response):
        assert handler_out.status_code == 204, label
        assert compute_out is None, (
            f"{label}: handler returned 204 but compute returned a payload"
        )
        return
    assert compute_out is not None, f"{label}: compute returned None but handler had a payload"
    assert _canon(handler_out) == _canon(compute_out), f"{label}: payloads differ across the seam"


def _cfg():
    return sim_router._state["solver_config"]


def test_cost_breakdown(golden):
    from services.results.cost_breakdown import compute_cost_breakdown

    _assert_seam(R.get_cost_breakdown(), compute_cost_breakdown(golden, _cfg()),
                 label="cost_breakdown")


def test_asset_economics(golden):
    from services.results.asset_economics import compute_asset_economics

    _assert_seam(
        R.get_asset_economics(),
        compute_asset_economics(golden, _cfg(), result_df=R._result_df),
        label="asset_economics",
    )


def test_emissions(golden):
    from services.results.emissions import compute_emissions

    _assert_seam(
        R.get_emissions(source="lopf"),
        compute_emissions(golden, "lopf", result_df=R._result_df),
        label="emissions",
    )


def test_lcoh(golden):
    from services.results.lcoh import compute_lcoh

    _assert_seam(
        R.get_lcoh(),
        compute_lcoh(golden, _cfg(), result_df=R._result_df),
        label="lcoh",
    )


def test_carrier_kpis(golden):
    from services.results.carrier_kpis import compute_carrier_kpis

    _assert_seam(
        R.get_carrier_kpis(),
        compute_carrier_kpis(golden, result_df=R._result_df),
        label="carrier_kpis",
    )


def test_prices(golden):
    from services.results.prices import compute_prices

    _assert_seam(
        R.get_prices(source="lopf"),
        compute_prices(golden, "lopf", None, None, result_df=R._result_df),
        label="prices",
    )
    _assert_seam(
        R.get_prices(source="lopf", from_=3, to_=9),
        compute_prices(golden, "lopf", 3, 9, result_df=R._result_df),
        label="prices[3:9]",
    )


def test_price_drivers(golden):
    from services.results.prices import compute_price_drivers

    _assert_seam(
        R.get_price_drivers(threshold=0.0, limit=50),
        compute_price_drivers(golden, 0.0, 50),
        label="price_drivers",
    )


def test_line_duals(golden):
    from services.results.line_duals import compute_line_duals

    _assert_seam(
        R.get_line_duals(),
        compute_line_duals(golden, result_df=R._result_df),
        label="line_duals",
    )


def test_curtailment(golden):
    from services.results.curtailment import compute_curtailment

    _assert_seam(R.get_curtailment(), compute_curtailment(golden, None, None),
                 label="curtailment")


def test_unit_commitment(golden):
    from services.results.unit_commitment import compute_unit_commitment

    _assert_seam(
        R.get_unit_commitment(),
        compute_unit_commitment(golden, None, None, result_df=R._result_df),
        label="unit_commitment",
    )


def test_statistics(golden):
    from services.results.statistics import compute_statistics

    _assert_seam(R.get_statistics(), compute_statistics(golden, _cfg()),
                 label="statistics")


def test_load_results(golden):
    from services.results.loads import compute_load_results

    _assert_seam(
        R.get_load_results(source="lopf"),
        compute_load_results(golden, _cfg(), "lopf", None, None, result_df=R._result_df),
        label="loads",
    )


def test_load_frame_helpers(golden):
    from services.results.load_frames import (
        corrected_marginal_prices,
        lp_scaled_load_frame,
    )

    a = R.corrected_marginal_prices(golden)
    b = corrected_marginal_prices(golden, result_df=R._result_df)
    assert a.equals(b), "corrected_marginal_prices differs across the seam"

    a = R.lp_scaled_load_frame(golden, _cfg(), "lopf")
    b = lp_scaled_load_frame(golden, _cfg(), "lopf", result_df=R._result_df)
    assert (a is None and b is None) or a.equals(b), "lp_scaled_load_frame differs across the seam"


def test_the_arithmetic_runs_with_no_router_state_at_all(golden):
    """
    The reason `result_df` is injected. A plain getattr over the live network
    stands in for the router's state-aware lookup; on the golden network the
    router has no LP-stage snapshot, so the two lookups resolve to the same
    frames and the payloads must agree.
    """
    from services.results.emissions import compute_emissions

    def live(n, accessor, attr, source="lopf"):
        acc = getattr(n, accessor, None)
        return getattr(acc, attr, None) if acc is not None else None

    assert _canon(compute_emissions(golden, "lopf", result_df=live)) == _canon(
        R.get_emissions(source="lopf")
    )
