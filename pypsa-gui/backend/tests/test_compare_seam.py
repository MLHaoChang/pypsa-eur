"""
The compare engine's seam is transparent, or this test says where it is not.

For each of the nine summaries, call the router-level name (which resolves
solver state the way the engine used to, inline) and the service-level name
(which takes that state as keyword arguments) on the same solved golden
network, and assert identical pydantic payloads.

Written before `services/compare/` existed; red on `ModuleNotFoundError`.
"""
from __future__ import annotations

import json

import pytest

import routers.compare as CMP
import routers.results as R
import routers.simulation as sim_router
from tests.golden import fixture as gf


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _cfg():
    return sim_router._state["solver_config"]


def _canon(model) -> str:
    return json.dumps(model.model_dump(), sort_keys=True, default=str)


P = list(gf.GOLDEN_PERIODS)


def test_periodized_lookup(golden):
    from services.compare.support import _periodized_lookup

    assert CMP._periodized_lookup(golden) == _periodized_lookup(golden, cfg=_cfg())


def test_capacity(golden):
    from services.compare.capacity import _compute_capacity_summary

    assert _canon(CMP._compute_capacity_summary(golden, P, True, True)) == _canon(
        _compute_capacity_summary(golden, P, True, True, cfg=_cfg())
    )


def test_dispatch(golden):
    from services.compare.dispatch import _compute_dispatch_summary

    assert _canon(CMP._compute_dispatch_summary(golden, P, True, True)) == _canon(
        _compute_dispatch_summary(golden, P, True, True, cfg=_cfg())
    )


@pytest.mark.parametrize("name,module", [
    ("_compute_loading_summary", "services.compare.loading"),
    ("_compute_prices_summary", "services.compare.prices"),
    ("_compute_emissions_summary", "services.compare.emissions"),
    ("_compute_curtailment_summary", "services.compare.curtailment"),
    ("_compute_storage_cycling_summary", "services.compare.storage_cycling"),
])
def test_pure_summaries(golden, name, module):
    import importlib

    svc = getattr(importlib.import_module(module), name)
    assert _canon(getattr(CMP, name)(golden, P, True, True)) == _canon(svc(golden, P, True, True))


@pytest.mark.parametrize("prices_from_state", [True, False])
def test_economics(golden, prices_from_state):
    from services.compare.economics import _compute_economics_summary

    a = CMP._compute_economics_summary(golden, P, True, True, prices_from_state=prices_from_state)
    b = _compute_economics_summary(
        golden, P, True, True, prices_from_state=prices_from_state,
        cfg=_cfg(), result_df=R._result_df,
    )
    assert _canon(a) == _canon(b)


def test_lost_load(golden, tmp_path):
    from services.compare.lost_load import compute_lost_load_summary

    # No results_state.pkl under tmp_path -> the router reads "no capture".
    a = CMP._compute_lost_load_summary(tmp_path, golden, P, True, True)
    b = compute_lost_load_summary(None, golden, P, True, True)
    assert _canon(a) == _canon(b)


def test_the_engine_runs_with_no_router_state_at_all(golden):
    """
    `cfg` built by hand, prices read straight off the network, no `_state`,
    no `_result_df`. On the golden network the live state holds exactly this
    config and no LP-stage snapshot, so the numbers must match the router's.
    """
    from services.compare.economics import _compute_economics_summary
    from services.solver_service import SolverConfig

    cfg = SolverConfig(
        discount_rate=gf.GOLDEN_DISCOUNT_RATE,
        multi_investment_periods=True,
        investment_periods=P,
    )
    b = _compute_economics_summary(golden, P, True, True, prices_from_state=False, cfg=cfg)
    a = CMP._compute_economics_summary(golden, P, True, True, prices_from_state=False)
    assert _canon(a) == _canon(b)
