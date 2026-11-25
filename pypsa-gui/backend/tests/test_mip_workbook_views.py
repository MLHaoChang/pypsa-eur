# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Assistant-assurance 17, slice 2b: the results workbook never writes a MIP's 0.0 bus
prices, or a figure priced at them, as a market result. Each run (the base, a held study
solve, every scenario) is judged by the limit of the solve that produced its own results (S3),
and an LP's workbook keeps its figures (S4)."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

import services.results_workbook as RW
from harness.solve_validity import DUALS_NOT_MARGINAL, MIP_PRICES_CAUSE, MIP_PRICES_TEXT
from services.results_workbook import scenarios as S
from services.results_workbook.overview import overview_figures
from services.solver_service import SolverConfig
from tests.test_mip_economics_readers import _rdf, _solved

MIP_RECORD = {"last_solve_settings": {"price_limit": DUALS_NOT_MARGINAL}}
LP_RECORD = {"last_solve_settings": {}}
VIEWS = ["capex", "prices", "economics"]


def _note(run: str) -> str:
    return f"{run}: marginal prices are not available. {MIP_PRICES_CAUSE}"


@pytest.fixture(scope="module")
def mip():
    return _solved(committable=True)


@pytest.fixture(scope="module")
def lp():
    return _solved(committable=False)


def _build(n, state, *, scenarios=None) -> dict:
    blob = RW.build_results_workbook(
        RW.WorkbookInputs(
            network=n,
            cfg=SolverConfig(),
            lost_load=None,
            result_df=_rdf,
            views=VIEWS,
            project="Base",
            scenarios=scenarios,
            solved_settings=lambda: None,
            solver_state=state,
        )
    )
    wb = load_workbook(io.BytesIO(blob))
    return {
        ws.title: [[c for c in row] for row in ws.iter_rows(values_only=True)]
        for ws in wb.worksheets
    }


def _text(rows) -> str:
    return "\n".join(str(c) for row in rows for c in row if c is not None)


def _line(figs, key):
    return next(line for line in figs.lines if line.key == key)


def _scenario(n, name, state):
    """A comparable scenario whose own saved state says how it was solved."""
    return S.Run(
        name=name,
        figs=overview_figures(
            n,
            SolverConfig(),
            None,
            _rdf,
            price_limit=S.run_price_limit(
                S.Run(name=name, network=n, solver_state=state)
            ),
        ),
        network=n,
        cfg=SolverConfig(),
        result_df=_rdf,
        solved_settings=lambda: None,
        solver_state=state,
    )


def test_run_price_limit_reads_the_run_s_own_solve(mip, lp):
    assert (
        S.run_price_limit(S.Run(name="m", network=mip, solver_state=MIP_RECORD))
        == DUALS_NOT_MARGINAL
    )
    # The record wins over today's judgement of the network: an LP network under a MIP record.
    assert (
        S.run_price_limit(S.Run(name="l", network=lp, solver_state=MIP_RECORD))
        == DUALS_NOT_MARGINAL
    )
    assert (
        S.run_price_limit(S.Run(name="l", network=lp, solver_state=LP_RECORD)) is None
    )


def test_the_overview_withholds_storage_charging_on_a_mip_with_the_reason(mip, lp):
    m = overview_figures(
        mip, SolverConfig(), None, _rdf, price_limit=DUALS_NOT_MARGINAL
    )
    line = _line(m, "opex_storage_charging")
    assert line.total is None and set(line.by_period.values()) <= {None}
    assert line.note == MIP_PRICES_TEXT
    assert _line(m, "opex").total is None  # a total over a withheld part (S1)
    # A price-independent line keeps its value and gets no reason.
    capex = _line(m, "capex_annualised")
    assert capex.total is not None and capex.note is None
    # The same network as an LP: the figures and their notes are as before.
    lp_figs = overview_figures(lp, SolverConfig(), None, _rdf)
    assert _line(lp_figs, "opex_storage_charging").total is not None
    assert _line(lp_figs, "opex_storage_charging").note is None


def test_a_mip_base_workbook_writes_no_price_and_names_the_reason(mip):
    wb = _build(mip, MIP_RECORD)
    prices = _text(wb["Prices"])
    assert _note("Base") in prices
    assert "Mean price" not in prices and "Price duration" not in prices
    for sheet in ("Capacity expansion", "Economics"):
        assert _note("Base") in _text(wb[sheet]), sheet
    assert MIP_PRICES_TEXT in _text(wb["Overview"])
    # The asset table's market columns read not_established (the workbook's empty cell), never 0.0.
    rows = wb["Economics"]
    header_at = next(i for i, r in enumerate(rows) if "revenue_eur" in r)
    header = rows[header_at]
    body = [r for r in rows[header_at + 1 :] if r and r[0] is not None][:4]
    for col in ("revenue_eur", "net_profit_eur", "avg_price_eur_per_mwh"):
        assert body and all(r[header.index(col)] == "not_established" for r in body), (
            col
        )


def test_an_lp_base_workbook_keeps_its_prices_and_has_no_reason(lp):
    wb = _build(lp, LP_RECORD)
    assert "Mean price" in _text(wb["Prices"])
    every = _text([r for rows in wb.values() for r in rows])
    assert MIP_PRICES_CAUSE not in every


def test_each_scenario_is_judged_by_its_own_solve(mip, lp):
    base_note, scen_note = _note("Base"), _note("Scenario")
    # An LP base with a MIP scenario: only the scenario's prices are withheld.
    wb = _build(
        lp,
        LP_RECORD,
        scenarios=S.Scenarios(runs=[_scenario(mip, "Scenario", MIP_RECORD)]),
    )
    prices = _text(wb["Prices"])
    assert "Mean price" in prices and scen_note in prices and base_note not in prices
    # A MIP base with an LP scenario: the base is withheld, the scenario keeps its prices.
    wb = _build(
        mip,
        MIP_RECORD,
        scenarios=S.Scenarios(runs=[_scenario(lp, "Scenario", LP_RECORD)]),
    )
    prices = _text(wb["Prices"])
    assert "Mean price" in prices and base_note in prices and scen_note not in prices
    for sheet in ("Economics", "Capacity expansion"):
        text = _text(wb[sheet])
        assert base_note in text and scen_note not in text, sheet


def test_a_held_study_solve_is_judged_with_its_own_config():
    """A Guided study's held solve (`study_solve.keep`) carries its solve settings but no
    solver_config; the run's own cfg judges it. A linearised process-UC solve with no MIP
    record is an LP and keeps its prices; the same network under binary UC is a MIP."""
    from tests.test_mip_price_views import _process_network

    n = _process_network()
    held = {"last_solve_settings": {}}
    lin = {"solver_name": "highs", "process_uc": "linearised"}
    binary = {"solver_name": "highs", "process_uc": "binary"}
    assert (
        S.run_price_limit(S.Run(name="b", network=n, cfg=lin, solver_state=held))
        is None
    )
    assert (
        S.run_price_limit(S.Run(name="b", network=n, cfg=binary, solver_state=held))
        == DUALS_NOT_MARGINAL
    )
    # A state that carries the solve's config is judged by it, not by the run's cfg.
    assert (
        S.run_price_limit(
            S.Run(
                name="b",
                network=n,
                cfg=lin,
                solver_state={**held, "solver_config": binary},
            )
        )
        == DUALS_NOT_MARGINAL
    )
    # The held solve's own record beats its config.
    assert (
        S.run_price_limit(S.Run(name="b", network=n, cfg=lin, solver_state=MIP_RECORD))
        == DUALS_NOT_MARGINAL
    )


def test_a_saved_scenario_s_figures_take_its_own_record(mip, monkeypatch):
    """`_fill`: the overview figures of a scenario read from its saved context pass that
    context's own limit (S3), not the base's."""
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        network=mip, solver_state={**MIP_RECORD, "solver_config": SolverConfig()}
    )
    run = S.Run(name="UC")
    monkeypatch.setattr("harness.sensitivity_comparison._values", lambda read: {})
    monkeypatch.setattr("services.finance.report.load_ic_report", lambda state: None)
    S._fill(
        run,
        {"ctx": ctx},
        result_df_for=lambda state: _rdf,
        ic_key=None,
        solved_settings_for=None,
        dispatch_ready=None,
    )
    assert _line(run.figs, "opex_storage_charging").note == MIP_PRICES_TEXT


def test_an_lp_total_that_is_not_established_gets_no_integer_solve_reason(
    lp, monkeypatch
):
    """Only a total withheld for a MIP names the MIP; an LP's missing total keeps its own note."""
    from services.results import economics_by_carrier as ebc

    real = ebc.compute_economics_by_carrier

    def one_missing(*a, **kw):
        out = real(*a, **kw)
        first = next(iter(out["by_carrier"].values()))
        first["storage_charge_cost_meur"] = {"total": None, "by_period": {}}
        return out

    monkeypatch.setattr(ebc, "compute_economics_by_carrier", one_missing)
    line = _line(
        overview_figures(lp, SolverConfig(), None, _rdf), "opex_storage_charging"
    )
    assert line.total is None and line.note is None


def test_the_all_assets_totals_are_not_established_when_one_asset_is_withheld(
    lp, monkeypatch
):
    """GK B2 H2 on #1994: a total over a withheld part is not established (S1), never the sum of
    the known parts; with every part known it is their sum."""
    from services.results import asset_economics as ae_mod
    from services.results_workbook.views_network import _sum_or_none

    assert _sum_or_none([1.0, None, 2.0]) is None
    assert _sum_or_none([1.0, float("nan")]) is None
    assert _sum_or_none([1.0, 2.0]) == 3.0

    real = ae_mod.compute_asset_economics

    def one_withheld(*a, **kw):
        out = real(*a, **kw)
        row = out["generators"][0]
        row["revenue_eur"] = row["net_profit_eur"] = None
        for b in row.get("by_period") or []:
            b["revenue_eur"] = b["net_profit_eur"] = None
        return out

    monkeypatch.setattr(ae_mod, "compute_asset_economics", one_withheld)
    rows = _build(lp, LP_RECORD)["Economics"]
    keys = {r[0]: r[3] for r in rows if r and r[2] == "horizon"}
    assert keys["Revenue (all assets)"] == "not_established"
    assert keys["Net profit (all assets)"] == "not_established"
