# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""ui-modern 06: a failed expansion CAPEX compute is unavailable, never 0.00.

The view raises part-way through the component loop, after the Generator
bucket was written, so a partial figure would survive if the handler only
nulled the total.
"""

from __future__ import annotations

import contextlib

import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from routers.results import get_cost_breakdown
from services.results import cost_breakdown as CB
from services.results_workbook.cells import NOT_ESTABLISHED
from services.results_workbook.views import HORIZON
from services.solver_service import SolverConfig
from tests.test_results_workbook_views import _build, _key, _wb


def _expanding_network() -> pypsa.Network:
    """One gas generator built from zero, plus an extendable store."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "A")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="A", p_set=50.0)
    n.add(
        "Generator",
        "G",
        bus="A",
        carrier="gas",
        p_nom=0.0,
        p_nom_extendable=True,
        capital_cost=1_000.0,
        marginal_cost=10.0,
    )
    n.add("Store", "S", bus="A", e_nom_extendable=True, capital_cost=5.0)
    n.optimize(solver_name="highs")
    return n


class _FailsAt:
    """The real view, except the attribute read that starts `name`'s turn."""

    def __init__(self, view, name):
        self._view, self._name = view, name

    def __getattr__(self, attr):
        if attr == self._name:
            raise RuntimeError(f"{attr} could not be read")
        return getattr(self._view, attr)


def _fail_mid_loop(monkeypatch, at="storage_units"):
    real = CB.periodized_cost_view

    @contextlib.contextmanager
    def view(n, cfg, *args, **kwargs):
        with real(n, cfg, *args, **kwargs) as v:
            # The lifetime resolve opens the view `for_back_calculation`;
            # only the expansion compute fails.
            yield v if kwargs.get("for_back_calculation") else _FailsAt(v, at)

    monkeypatch.setattr(CB, "periodized_cost_view", view)


@pytest.fixture()
def network(install_network):
    n = _expanding_network()
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()
    return n


def test_the_control_writes_the_generator_bucket_before_storage(network):
    """Without the fault the Generator bucket holds a real figure, so the
    faulted run below raises after a bucket was written."""
    payload = get_cost_breakdown()
    by = {row["component"]: row for row in payload["by_component"]}
    assert by["Generator"]["capex_expansion"] > 0
    assert payload["capex_expansion"] == pytest.approx(
        by["Generator"]["capex_expansion"]
    )


def test_a_mid_loop_failure_leaves_no_figure_and_no_partial_bucket(
    network, monkeypatch
):
    _fail_mid_loop(monkeypatch)
    payload = get_cost_breakdown()
    assert payload["capex_expansion"] is None
    assert payload["storage_capex_expansion"] is None
    rows = {row["component"]: row for row in payload["by_component"]}
    assert "Generator" in rows
    assert all(row["capex_expansion"] is None for row in rows.values())
    # The figures that do not depend on the expansion compute stay.
    assert payload["capex"] > 0
    assert isinstance(payload["total"], float)


def test_the_workbook_writes_not_established_not_a_zero(network, monkeypatch):
    _fail_mid_loop(monkeypatch)
    wb = _wb(_build(network, views=["capex"]))
    ws = next(ws for ws in wb.worksheets if ws.title.startswith("Capacity expansion"))
    assert _key(ws, "Capex of new build", HORIZON) == NOT_ESTABLISHED


def _table_cell(ws, table: str, row_label: str, column: str):
    """The cell under `column` on `row_label`'s row of the table titled `table`."""
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    start = next(
        i for i, r in enumerate(rows) if r and str(r[0] or "").startswith(table)
    )
    header = next(r for r in rows[start + 1 :] if column in r)
    col = header.index(column)
    for r in rows[rows.index(header) + 1 :]:
        if row_label in r:
            return r[col]
    raise AssertionError(f"no {row_label!r} row in {table!r}")


def test_the_workbook_keeps_the_per_component_column_as_not_established(
    network, monkeypatch
):
    """GK B W2: an all-None column is kept, cell by cell, not dropped."""
    _fail_mid_loop(monkeypatch)
    wb = _wb(_build(network, views=["capex"]))
    ws = next(ws for ws in wb.worksheets if ws.title.startswith("Capacity expansion"))
    cell = _table_cell(ws, "Cost by component", "Generator", "capex_expansion")
    assert cell == NOT_ESTABLISHED
