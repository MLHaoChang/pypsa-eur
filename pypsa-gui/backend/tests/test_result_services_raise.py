# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Prices, curtailment and cost breakdown raise on an internal exception.

A crash used to come back as None, the value for "no results", so the results
workbook listed a crashed view as "no results". The routes still answer 204.
"""
from __future__ import annotations

import pytest

import routers.results as results_router
import services.results.cost_breakdown as cost_breakdown
import services.results.curtailment as curtailment
import services.results.prices as prices
from services.solver_service import SolverConfig
from tests.golden import fixture as gf
from tests.test_results_workbook_views import _about, _build, _network

SERVICES = [
    pytest.param(cost_breakdown, "compute_cost_breakdown", "capex", "cost_breakdown",
                 lambda n: (n, SolverConfig()), id="cost_breakdown"),
    pytest.param(curtailment, "compute_curtailment", "curtailment", "curtailment",
                 lambda n: (n, None, None), id="curtailment"),
    pytest.param(prices, "compute_prices", "prices", "prices",
                 lambda n: (n, "lopf", None, None), id="prices"),
]


def _boom(*_a, **_k):
    raise RuntimeError("boom")


@pytest.mark.parametrize("module, name, view, _route, args", SERVICES)
def test_the_service_raises_on_an_internal_exception(module, name, view, _route, args, monkeypatch):
    n = _network()
    if name == "compute_cost_breakdown":
        monkeypatch.setattr(type(n), "statistics", property(_boom), raising=False)
    elif name == "compute_curtailment":
        monkeypatch.setattr(curtailment, "curtailment_frame", _boom)
    kwargs = {"result_df": _boom} if name == "compute_prices" else {}
    with pytest.raises(Exception):
        getattr(module, name)(*args(n), **kwargs)


@pytest.mark.parametrize("module, name, view, route, _args", SERVICES)
def test_the_route_still_answers_204_on_that_exception(
        module, name, view, route, _args, client, reset_backend, monkeypatch):
    gf.install_golden(gf.solve_golden_network())
    monkeypatch.setattr(results_router, name, _boom)
    assert client.get(f"/api/results/{route}").status_code == 204


@pytest.mark.parametrize("module, name, view, _route, _args", SERVICES)
def test_a_crashing_view_is_an_error_in_about_not_no_results(module, name, view, _route, _args, monkeypatch):
    n = _network()
    monkeypatch.setattr(module, name, _boom)
    status = str(_about(__import__("openpyxl").load_workbook(
        __import__("io").BytesIO(_build(n, [view]))))[view])
    assert status.startswith("error:"), status


def test_a_crashing_cost_breakdown_leaves_the_overview_with_a_note(monkeypatch):
    from services.results_workbook.overview import overview_figures
    n = _network()
    monkeypatch.setattr(cost_breakdown, "compute_cost_breakdown", _boom)
    figs = overview_figures(n, SolverConfig(), None, lambda n_, acc, attr, src="lopf": getattr(getattr(n_, acc), attr, None))
    assert any("cost breakdown failed" in note for note in figs.notes)
