"""The `ic.counterfactual` gate fact (study harness issue 19): read from the
stored investment case through `GET /results/investment_case`, never by
re-running it."""
from __future__ import annotations

import pytest

from services import ic_counterfactual as F

BUYS = ["counterfactual_basis:site_today", "counterfactual_buys:h2_offtake",
        "co2_price_period:2030:2030:120"]
UNSERVED = ["counterfactual_basis:site_today",
            "counterfactual_baseline_unserved:h2_offtake",
            "counterfactual_not_established:owner_conversion_load"]
@pytest.fixture
def ic(client, install_network, session_ctx, monkeypatch):
    """Run the investment case through its real route and runner (the case
    builder stubbed with the SAM s2 case and ``flags``), then read the fact
    through `GET /results/investment_case` as the harness does."""
    import dataclasses

    from tests.fixtures.investment_case.sam import sam_case as S
    from tests.test_investment_case_routes import (
        STUDY_URL, _fake_build, _poll, _put_fin, _s2_inputs, _solved)

    import contextvars

    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    def on(fn):
        # The harness's binding (`services.run_gates._on`): the run project's
        # context, whatever the calling request has bound.
        ctx = session_ctx(client)

        def call():
            PyPSAService._turn_cell.set(None)
            PyPSAService.bind_request_context(ctx)
            return fn()
        return contextvars.copy_context().run(call)

    def read():
        return F.read(session_ctx(client), on)

    def run(flags=(), refuse=None):
        _fake_build(monkeypatch, refuse=refuse, case_fn=lambda: dataclasses.replace(
            S.to_finance_case("s2"), flags=tuple(flags)))
        assert client.post(STUDY_URL, json={}).status_code == 200
        _poll(client)
        return read()

    _solved(install_network)
    assert _put_fin(client, _s2_inputs()).status_code == 200
    # Stamp the solve as the app's own solve does, so its results are current.
    ctx = session_ctx(client)
    ctx.solver_state.update(status="completed", condition="optimal")
    on(lambda: workflow_tools._record_solve_inputs(ctx.network,
                                                  ctx.solver_state.get("solver_config")))
    assert workflow_tools.solve_freshness(ctx)["results_current"] is True
    return run, read


def test_a_done_case_on_the_site_today_basis_is_established(ic):
    run, _read = ic
    assert run(BUYS) == {"state": "established", "basis": "site_today", "reasons": []}


def test_a_done_case_without_the_basis_flag_is_established_on_the_analytic_basis(ic):
    run, _read = ic
    assert run() == {"state": "established", "basis": "analytic", "reasons": []}


def test_a_done_case_with_a_not_established_flag_says_why(ic):
    run, _read = ic
    assert run(UNSERVED) == {"state": "not_established", "basis": "site_today",
                             "reasons": ["counterfactual_not_established:owner_conversion_load"]}


def test_a_refused_case_is_not_established_with_its_code(ic):
    run, _read = ic
    assert run(refuse="counterfactual_not_established:lossy_poc") \
        == {"state": "not_established", "basis": None,
            "reasons": ["counterfactual_not_established:lossy_poc"]}


def test_no_case_is_not_run(ic):
    _run, read = ic
    assert read() == {"state": "not_run", "basis": None, "reasons": ["no_case"]}


def test_a_case_whose_network_was_edited_is_not_run(ic, client, session_ctx):
    run, read = ic
    assert run(BUYS)["state"] == "established"
    n = session_ctx(client).network
    n.generators.loc["gas", "marginal_cost"] += 5.0       # an input edit after the solve
    assert read() == {"state": "not_run", "basis": None,
                      "reasons": ["case_stale", "results_not_current"]}
    n.generators.loc["gas", "marginal_cost"] -= 5.0
    assert read()["state"] == "established"
    n.generators_t.p.iloc[0, 0] += 1.0                    # the dispatch the case read
    assert read() == {"state": "not_run", "basis": None,
                      "reasons": ["case_stale", "changed:dispatch"]}


@pytest.mark.parametrize("status, reason", [("running", "case_running"),
                                            ("failed", "case_failed")])
def test_a_running_or_failed_case_is_not_run(status, reason):
    assert F.classify({"status": status, "report": {"present": True, "stale": False}}) \
        == {"state": "not_run", "basis": None, "reasons": [reason]}


def test_the_text_quotes_the_cases_own_sentences():
    from services.finance.baseline_text import flag_text
    from services.finance.export_xlsx import BASELINE_CAVEATS

    caveats = dict(BASELINE_CAVEATS)
    assert F.counterfactual_text(BUYS) == [caveats["counterfactual_buys"],
                                           caveats["co2_price_period"]]
    assert F.counterfactual_text(UNSERVED) \
        == [flag_text("counterfactual_baseline_unserved:h2_offtake")]
    # Quoted, never restated: no price in the words.
    assert not any("EUR" in t or "2.0" in t for t in F.counterfactual_text(BUYS))
