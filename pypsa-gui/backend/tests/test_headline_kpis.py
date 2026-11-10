"""The headline figures at the top of Results (ui-modern 03 S1)."""
# ruff: noqa: F811 -- imported shared fixtures.

from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from routers import results as results_router
from services.results import headline_kpis as hk
from tests.test_investment_assurance import lcoe_hand, project, store  # noqa: F401
from tests.test_result_profiles_assurance import solve, windy  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

URL = "/api/results/headline_kpis"


def cards(client):
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    return (
        body["kind"],
        {c["id"]: c for c in body["cards"]},
        [c["id"] for c in body["cards"]],
    )


def as_template(monkeypatch, template_id):
    monkeypatch.setattr(
        results_router, "_workbook_template", lambda: {"id": template_id}
    )


def test_nothing_solved_answers_204(baseline, client):
    assert client.get(URL).status_code == 204


def test_a_plain_network_reads_cost_and_the_checked_curtailed_share(windy, client):
    solve(windy)
    kind, by_id, order = cards(client)
    assert (kind, tuple(order)) == ("network", hk.ORDER["network"])
    cost = client.get("/api/results/cost_breakdown").json()
    # The strip and the Capacity expansion tab read one payload.
    assert by_id["system_cost"]["value"] == pytest.approx(cost["total"])
    # Opex is over the modelled snapshots (two hours here), so no "/yr"; the
    # annualised new-build cost of a single-period run is a year's.
    assert by_id["system_cost"]["unit"] == "currency"
    assert by_id["new_build_capital"]["unit"] == "currency/yr"
    assert by_id["new_build_capital"]["value"] == pytest.approx(cost["capex_expansion"])
    # 33 of 50 MWh available curtailed (the `windy` fixture).
    share = by_id["curtailed_share"]
    assert (
        share["value"] == pytest.approx(66)
        and share["unit"] == "%"
        and share["state"] == "ready"
    )
    assert share["formula_id"] == "curtailment-share-of-available/1"


def test_a_figure_the_run_did_not_produce_is_a_state_with_its_sentence(windy, client):
    solve(windy)
    _, by_id, _ = cards(client)
    # No lines: nothing to load. A computed zero keeps its 0.
    line = by_id["peak_line_loading"]
    assert (line["value"], line["state"], line["reason"]) == (
        None,
        "not_applicable",
        hk.REASONS["unknown_branch"][1],
    )
    assert (
        by_id["new_build_capital"]["value"] == 0
        and by_id["new_build_capital"]["state"] == "zero"
    )


def test_peak_line_loading_names_its_line(windy, client):
    n = windy.network
    # A lightly loaded spur listed first: the name is the peak's, not the first branch's.
    n.add("Bus", "B3", v_nom=110)
    n.add("Line", "A spur", bus0="B1", bus1="B3", x=0.1, r=0.01, s_nom=10)
    n.add("Load", "L3", bus="B3", p_set=0.5)
    n.add("Bus", "B2", v_nom=110)
    n.add("Line", "North tie", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=10)
    n.add("Load", "L2", bus="B2", p_set=2)
    solve(windy)
    _, by_id, _ = cards(client)
    line = by_id["peak_line_loading"]
    assert line["value"] == pytest.approx(20) and line["unit"] == "%"
    assert line["detail"] == "North tie" and line["formula_id"]


def test_inputs_changed_since_the_solve_reads_out_of_date(windy, client):
    solve(windy)
    windy.network.loads_t.p_set = pd.DataFrame(
        {"L1": [6, 7]}, index=windy.network.snapshots
    )
    _, by_id, _ = cards(client)
    share = by_id["curtailed_share"]
    assert (share["value"], share["state"]) == (None, "stale")
    assert share["reason"] == "The inputs changed since this solve."


LOAD_FLOW = "A load flow replaced the results of this solve."
NOT_OPTIMAL = "The solve did not finish with an optimal result."


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"ac_pf_results": {"converged": True}}, ("unavailable", LOAD_FLOW)),
        ({"lopf_results": {"kept": True}}, ("unavailable", LOAD_FLOW)),
        ({"condition": "suboptimal"}, ("unavailable", NOT_OPTIMAL)),
        ({"status": "failed"}, ("unavailable", NOT_OPTIMAL)),
    ],
)
def test_an_unverified_solve_says_which_check_failed(
    windy, client, monkeypatch, change, expected
):
    solve(windy)
    for key, value in change.items():
        monkeypatch.setitem(windy.solver_state, key, value)
    # The inputs did not change, so the card must not say they did.
    assert (
        hk._refused(windy, "curtailed_share", "unverified_solve")["reason"]
        == (expected[1])
    )
    assert (
        hk._refused(windy, "curtailed_share", "unverified_solve")["state"]
        == (expected[0])
    )


def test_a_load_flow_over_the_solve_is_not_called_changed_inputs(
    windy, client, monkeypatch
):
    solve(windy)
    monkeypatch.setitem(windy.solver_state, "ac_pf_results", {"converged": True})
    share = hk._checked(windy, "curtailed_share")
    assert (share["value"], share["state"], share["reason"]) == (
        None,
        *hk.LOAD_FLOW_REPLACED,
    )


CASE = {
    "status": "completed",
    "present": True,
    "stale": False,
    "assumptions_hash": "a",
    "current_hash": "a",
}


@pytest.mark.parametrize(
    "state, expected",
    [
        # Literal states: only a changed solve is "stale" (it dims the strip).
        (None, ("not_run", "The investment case has not run on this solve.")),
        (
            {**CASE, "present": False},
            ("not_run", "The investment case has not run on this solve."),
        ),
        (
            {**CASE, "status": "running"},
            ("running", "The investment case is still running."),
        ),
        (
            {**CASE, "stale": True},
            ("unavailable", "The investment case is out of date: run it again."),
        ),
        (
            {**CASE, "current_hash": "b"},
            ("unavailable", "The investment case is out of date: run it again."),
        ),
        # No key to compare against: its age is unknown, so it is not called old.
        (
            {**CASE, "stale": True, "current_hash": None},
            ("unavailable", "The investment case cannot be compared with its inputs."),
        ),
        (
            {**CASE, "stale": True, "assumptions_hash": None},
            ("unavailable", "The investment case cannot be compared with its inputs."),
        ),
        # Current, but the stored report is not this one.
        (CASE, ("unavailable", "The investment case could not be read.")),
    ],
)
def test_an_lcoe_card_says_why_the_case_is_not_current(
    windy, monkeypatch, state, expected
):
    solve(windy)
    monkeypatch.setattr(hk.calculations.investment, "current", lambda ctx: state)
    lcoe = hk._checked(windy, "lcoe")
    assert (lcoe["value"], lcoe["state"], lcoe["reason"]) == (None, *expected)


@pytest.mark.parametrize(
    "error",
    [HTTPException(409, "busy"), ValueError("v"), KeyError("k"), TypeError("t")],
)
def test_a_case_that_cannot_be_read_says_so(windy, monkeypatch, error):
    def failing(ctx):
        raise error

    monkeypatch.setattr(hk.calculations.investment, "current", failing)
    assert hk._case_not_current(windy) == (
        "unavailable",
        "The investment case could not be read.",
    )


def test_a_solve_that_recorded_no_inputs_is_not_called_changed(windy, client):
    solve(windy)
    for key in hk.SOLVE_FINGERPRINTS:
        assert windy.network.meta.pop(key)
    _, by_id, _ = cards(client)
    share = by_id["curtailed_share"]
    # Nothing was compared, so neither "stale" nor "the inputs changed".
    assert (share["value"], share["state"], share["reason"]) == (
        None,
        "unavailable",
        "This solve did not record its inputs, so this figure is not checked.",
    )


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"status": "failed"}, ("unavailable", NOT_OPTIMAL)),
        ({"ac_pf_results": {"converged": True}}, ("unavailable", LOAD_FLOW)),
    ],
)
def test_no_fingerprints_does_not_hide_a_failed_or_replaced_solve(
    windy, monkeypatch, change, expected
):
    solve(windy)
    for key in hk.SOLVE_FINGERPRINTS:
        del windy.network.meta[key]
    for key, value in change.items():
        monkeypatch.setitem(windy.solver_state, key, value)
    assert hk._unverified(windy) == expected


def test_a_branch_without_a_loading_is_passed_over_for_the_peak_name(monkeypatch):
    raw = {
        "branches": {"a": {"name": "No flows"}, "b": {"name": "North tie"}},
        "energy_weight": [1.0],
    }
    branch = hk.calculations.branch
    monkeypatch.setattr(branch, "_window", lambda raw, scope: (0, 0))
    monkeypatch.setattr(
        branch,
        "_loading",
        lambda item, weights, a, b: (
            None if item["name"] == "No flows" else {"peak_branch_utilisation": 20.0}
        ),
    )
    assert hk._peak_branch(raw, {}, 20.0) == "North tie"


@pytest.mark.parametrize("missing", hk.SOLVE_FINGERPRINTS)
def test_one_missing_fingerprint_is_also_not_compared(windy, missing):
    solve(windy)
    del windy.network.meta[missing]
    assert hk._unverified(windy) == hk.NOT_COMPARED


def test_a_peak_line_name_that_cannot_be_read_costs_one_card(
    windy, client, monkeypatch
):
    n = windy.network
    n.add("Bus", "B2", v_nom=110)
    n.add("Line", "North tie", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=10)
    n.add("Load", "L2", bus="B2", p_set=2)
    solve(windy)

    def odd(raw, scope, peak):
        raise KeyError("branches")

    monkeypatch.setattr(hk, "_peak_branch", odd)
    _, by_id, _ = cards(client)
    line = by_id["peak_line_loading"]
    assert (line["value"], line["state"]) == (None, "unavailable")
    assert by_id["system_cost"]["state"] == "ready"


def test_a_ready_lcoe_card_carries_the_checked_figure(project, client, monkeypatch):
    store(project, lcoe_hand)
    as_template(monkeypatch, "eh_datacenter")
    _, by_id, order = cards(client)
    lcoe = by_id["lcoe"]
    assert order[0] == "lcoe" and lcoe["state"] == "ready" and lcoe["reason"] is None
    # tests/test_investment_assurance.py::test_the_lcoe_from_the_retained_series.
    assert lcoe["value"] == pytest.approx(
        169.42148760330576 / (10 / 1.0784313725490196 + 10 / 1.0784313725490196**2)
    )
    assert lcoe["unit"] == "USD/MWh"
    assert lcoe["formula_id"] == "investment-lcoe-real/1"


def test_a_busy_network_costs_one_card_not_the_strip(windy, client, monkeypatch):
    from contextlib import contextmanager

    import harness.durable

    solve(windy)

    @contextmanager
    def busy():
        raise HTTPException(409, {"error_kind": "assurance_context_changed"})
        yield

    monkeypatch.setattr(harness.durable, "network_guard", busy)
    _, by_id, _ = cards(client)
    share = by_id["curtailed_share"]
    assert (share["value"], share["state"], share["reason"]) == (None, *hk.BUSY)
    assert by_id["system_cost"]["state"] == "ready"


@pytest.mark.parametrize("error", [KeyError("p"), TypeError("odd frame")])
def test_an_odd_source_costs_one_card_not_the_strip(
    windy, client, monkeypatch, error, caplog
):
    solve(windy)

    def odd(raw, scope):
        raise error

    monkeypatch.setattr(hk.calculations, "scoped_calculate", odd)
    _, by_id, _ = cards(client)
    share = by_id["curtailed_share"]
    assert (share["value"], share["state"], share["reason"]) == (
        None,
        *hk._UNAVAILABLE,
    )
    assert by_id["system_cost"]["state"] == "ready"
    # A programming error is logged, not only turned into a sentence.
    logged = [r for r in caplog.records if "curtailed_share" in r.getMessage()]
    assert logged and logged[0].exc_info[1] is error


def test_a_failing_cost_breakdown_leaves_the_checked_cards(windy, client, monkeypatch):
    solve(windy)

    def broken(n, config):
        raise RuntimeError("statistics failed")

    monkeypatch.setattr(results_router, "compute_cost_breakdown", broken)
    _, by_id, _ = cards(client)
    for card_id in ("system_cost", "new_build_capital"):
        assert (by_id[card_id]["value"], by_id[card_id]["reason"]) == (None, hk.NO_COST)
    assert by_id["curtailed_share"]["state"] == "ready"


def test_the_strip_gate_is_one_switch(windy, client, monkeypatch):
    solve(windy)
    asked = []
    monkeypatch.setattr(hk, "_shown", lambda profile: asked.append(profile))
    _, by_id, _ = cards(client)
    share = by_id["curtailed_share"]
    assert (share["value"], share["state"], share["formula_id"]) == (
        None,
        "unavailable",
        None,
    )
    assert asked == [hk.CHECKED[c][0] for c in ("curtailed_share", "peak_line_loading")]
    assert by_id["system_cost"]["state"] == "ready"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_figure_is_never_shown(value):
    card = hk._card("system_cost", value, "currency")
    assert (card["value"], card["state"], card["reason"]) == (None, *hk._UNAVAILABLE)


@pytest.mark.parametrize(
    "periods, new_build_unit", [([], "currency/yr"), ([2030, 2040], "currency")]
)
def test_cost_units_follow_the_horizon(monkeypatch, periods, new_build_unit):
    monkeypatch.setattr(hk, "_checked", lambda ctx, card_id: hk._card(card_id))
    ctx = SimpleNamespace(network=SimpleNamespace(investment_periods=periods))
    by_id = {
        c["id"]: c
        for c in hk.headline_kpis(
            ctx,
            template={"id": "eh_microgrid"},
            cost={"total": 9.0, "capex_expansion": 4.0},
            lost_load=None,
        )["cards"]
    }
    # The system cost never reads per year; see `headline_kpis`.
    assert by_id["system_cost"]["unit"] == "currency"
    assert by_id["new_build_capital"]["unit"] == new_build_unit


def test_an_energy_hub_leads_with_lcoe_and_says_when_the_case_has_not_run(
    windy, client, monkeypatch
):
    solve(windy)
    as_template(monkeypatch, "eh_datacenter")
    kind, by_id, order = cards(client)
    assert (kind, tuple(order)) == ("energy_hub", hk.ORDER["energy_hub"])
    assert (by_id["lcoe"]["value"], by_id["lcoe"]["state"]) == (None, "not_run")
    assert by_id["lcoe"]["reason"] == "The investment case has not run on this solve."
    # No value of lost load in the solve: unserved energy was not measured, not zero.
    unserved = by_id["unserved_energy"]
    assert (unserved["value"], unserved["state"]) == (None, "not_modelled")


def test_a_microgrid_leads_with_unserved_energy_and_keeps_a_real_zero(
    windy, client, monkeypatch
):
    solve(windy, voll=1000)
    as_template(monkeypatch, "eh_microgrid_staged")
    kind, by_id, order = cards(client)
    assert (kind, order[0]) == ("microgrid", "unserved_energy")
    unserved = by_id["unserved_energy"]
    assert (unserved["value"], unserved["unit"], unserved["state"]) == (
        0,
        "MWh",
        "zero",
    )


def test_a_missing_payload_is_a_state_never_a_zero(windy):
    solve(windy)
    template = {"id": "eh_microgrid"}
    by_id = {
        c["id"]: c
        for c in hk.headline_kpis(windy, template=template, cost=None, lost_load=None)[
            "cards"
        ]
    }
    for card_id in ("system_cost", "new_build_capital"):
        assert (
            by_id[card_id]["value"],
            by_id[card_id]["state"],
            by_id[card_id]["reason"],
        ) == (
            None,
            "unavailable",
            hk.NO_COST,
        )
    assert (by_id["unserved_energy"]["value"], by_id["unserved_energy"]["state"]) == (
        None,
        "not_modelled",
    )
    # One missing field leaves the other card its figure.
    partial = {"total": 5.0, "capex_expansion": None}
    by_id = {
        c["id"]: c
        for c in hk.headline_kpis(
            windy, template=template, cost=partial, lost_load=None
        )["cards"]
    }
    assert (by_id["system_cost"]["value"], by_id["system_cost"]["state"]) == (
        5.0,
        "ready",
    )
    assert by_id["new_build_capital"]["state"] == "unavailable"


@pytest.mark.parametrize(
    "template, kind",
    [
        (None, "network"),
        ({"id": "eh_microgrid"}, "microgrid"),
        ({"id": "eh_datacenter_staged"}, "energy_hub"),
        ({"id": "eh_site", "site_type": "hospital"}, "energy_hub"),
    ],
)
def test_project_kind(template, kind):
    assert hk.project_kind(template) == kind


def test_card_sets_are_short_and_reasons_carry_no_engine_identifier():
    assert all(len(ids) <= 5 and len(set(ids)) == len(ids) for ids in hk.ORDER.values())
    split = [
        hk._UNAVAILABLE,
        hk.BUSY,
        hk.INPUTS_CHANGED,
        hk.LOAD_FLOW_REPLACED,
        hk.NOT_OPTIMAL,
        hk.NOT_COMPARED,
        hk.CASE_NOT_RUN,
        hk.CASE_RUNNING,
        hk.CASE_STALE,
        hk.CASE_UNREAD,
        hk.CASE_NO_KEY,
    ]
    sentences = [r for _, r in [*hk.REASONS.values(), *split]] + [
        hk.NOT_MODELLED_LOST_LOAD,
        hk.NO_COST,
    ]
    for sentence in sentences:
        assert "_" not in sentence and "/" not in sentence and sentence.endswith(".")
    assert {s for s, _ in [*hk.REASONS.values(), *split]} <= {
        "stale",
        "not_applicable",
        "not_run",
        "running",
        "unavailable",
    }


# --- S3: the delta against the base case, and the trend over periods ---------

from harness import sensitivity_comparison as sc  # noqa: E402
from services.results import headline_delta as hd  # noqa: E402
from tests.test_sensitivity_comparison import (  # noqa: E402, F401
    edit_saved,
    set_price,
    sweep,
    swept,
)
from tests.test_sensitivity_comparison import solve as solve_saved  # noqa: E402


def strip(client, name=None):
    """The strip's answer, after opening the saved project `name` if given."""
    if name is not None:
        opened = client.get(f"/api/projects/{name}")
        assert opened.status_code == 200, opened.text
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    return body, {c["id"]: c for c in body["cards"]}


def no_deltas(by_id):
    return all(card["delta"] is None for card in by_id.values())


def test_a_case_reads_each_cards_change_against_its_base(swept, client):
    """G1 serves 5 MW for 2 h: 100 at the base's price of 10, 200 at 20."""
    base_id, _cases = swept
    body, by_id = strip(client, "Gas 20")
    assert body["comparison"] == {
        "role": "case",
        "base_name": "Tool Baseline",
        "base_project_id": base_id,
        "comparable": True,
        "reason": None,
    }
    assert by_id["system_cost"]["value"] == pytest.approx(200)
    assert by_id["system_cost"]["delta"] == {
        "value": pytest.approx(100),
        "pct": pytest.approx(100),
        "note": None,
    }
    # Nothing is built in either: a zero base has a delta and no percentage.
    assert by_id["new_build_capital"]["delta"] == {
        "value": 0,
        "pct": None,
        "note": "zero base: no percentage",
    }


def test_a_cheaper_case_reads_a_negative_change(swept, client):
    _body, by_id = strip(client, "Gas 5")
    assert by_id["system_cost"]["delta"] == {
        "value": pytest.approx(-50),
        "pct": pytest.approx(-50),
        "note": None,
    }


def test_the_base_says_it_is_the_base_and_carries_no_delta(swept, client):
    base_id, _cases = swept
    body, by_id = strip(client)
    assert body["comparison"] == {
        "role": "base",
        "base_name": "Tool Baseline",
        "base_project_id": base_id,
        "comparable": True,
        "reason": None,
    }
    assert no_deltas(by_id)


def test_a_project_with_no_cases_has_no_comparison(baseline, client):
    _ctx, base_id, _root = baseline
    solve_saved(base_id)
    body, by_id = strip(client)
    assert body["comparison"] is None
    assert no_deltas(by_id)
    assert all(card["trend"] is None for card in by_id.values())


def test_a_case_whose_base_changed_says_why_and_carries_no_delta(swept, client):
    base_id, _cases = swept
    edit_saved(base_id, set_price(11))
    solve_saved(base_id)
    body, by_id = strip(client, "Gas 20")
    assert body["comparison"]["role"] == "case"
    assert body["comparison"]["base_name"] == "Tool Baseline"
    assert body["comparison"]["comparable"] is False
    assert body["comparison"]["reason"] == (
        "Not compared with the base case: the base's inputs or finance "
        "settings changed since this case was branched."
    )
    assert no_deltas(by_id)
    assert by_id["system_cost"]["value"] == pytest.approx(200)


def test_a_base_out_of_date_is_named_first(swept, client):
    base_id, _cases = swept
    edit_saved(base_id, set_price(11))
    body, by_id = strip(client, "Gas 20")
    assert body["comparison"]["reason"] == (
        "Not compared with the base case: the base has no current results to "
        "compare with; the base's inputs or finance settings changed since "
        "this case was branched."
    )
    assert no_deltas(by_id)


def test_a_base_the_user_cannot_open_is_not_named(swept, client, monkeypatch):
    base_id, _cases = swept
    resolve = sc._resolve

    def closed(db, user, ref):
        if ref == base_id:
            raise HTTPException(404, {"error_kind": "sensitivity_project_not_found"})
        return resolve(db, user, ref)

    monkeypatch.setattr(sc, "_resolve", closed)
    body, by_id = strip(client, "Gas 20")
    assert body["comparison"] == {
        "role": "case",
        "base_name": None,
        "base_project_id": None,
        "comparable": False,
        "reason": hd.NO_BASE_ACCESS,
    }
    assert no_deltas(by_id)


@pytest.mark.parametrize("error", [KeyError("x"), OSError("x"), HTTPException(409)])
def test_a_base_that_cannot_be_read_costs_the_deltas_only(
    swept, client, monkeypatch, error
):
    def broken(project):
        raise error

    strip(client, "Gas 20")
    monkeypatch.setattr(sc, "_read", broken)
    body, by_id = strip(client)
    assert body["comparison"]["comparable"] is False
    assert body["comparison"]["reason"] == hd.BASE_UNREADABLE
    assert body["comparison"]["base_name"] == "Tool Baseline"
    assert no_deltas(by_id)
    assert by_id["system_cost"]["value"] == pytest.approx(200)


def test_base_figures_that_fail_cost_the_deltas_only(swept, client, monkeypatch):
    strip(client, "Gas 20")
    build, calls = results_router._headline_cards, []

    def second_fails():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("base")
        return build()

    monkeypatch.setattr(results_router, "_headline_cards", second_fails)
    body, by_id = strip(client)
    assert body["comparison"]["comparable"] is False
    assert body["comparison"]["reason"] == hd.BASE_UNREADABLE
    assert no_deltas(by_id)
    # The base's context is unbound again: the next read is the case's.
    monkeypatch.setattr(results_router, "_headline_cards", build)
    assert strip(client)[1]["system_cost"]["value"] == pytest.approx(200)


def test_the_comparison_leaves_the_acting_user_as_it_found_it(swept):
    from services import chat_tools

    before = chat_tools.acting_user_id()
    assert results_router._headline_comparison([], None) is None
    assert chat_tools.acting_user_id() == before


def test_a_scratch_network_has_no_comparison():
    assert hd.base_case(SimpleNamespace(project_uuid=None)) == (None, None)


def card(card_id, value, unit="currency"):
    return {"id": card_id, "value": value, "unit": unit, "delta": None}


def test_a_delta_needs_both_values_in_one_unit():
    cards = [
        card("a", 90.0),
        card("b", None),
        card("c", 1.0),
        card("d", 1.0, "currency/yr"),
        card("e", 1.0),
        card("f", -30.0),
    ]
    base = [
        card("a", 120.0),
        card("b", 1.0),
        card("c", None),
        card("d", 1.0, "currency"),
        card("f", -20.0),
    ]
    hd.add_deltas(cards, base)
    deltas = {c["id"]: c["delta"] for c in cards}
    assert deltas["a"] == {"value": -30.0, "pct": -25.0, "note": None}
    # Over the base's size: a more negative figure is a negative change.
    assert deltas["f"] == {"value": -10.0, "pct": -50.0, "note": None}
    assert [deltas[k] for k in "bcde"] == [None] * 4


def periods(*totals):
    return {
        "total": sum(totals),
        "capex_expansion": 1.0,
        "by_period": [
            {"period": 2030 + 10 * i, "total": t} for i, t in enumerate(totals)
        ],
    }


def cost_cards(cost, investment_periods=(2030, 2040)):
    ctx = SimpleNamespace(
        network=SimpleNamespace(investment_periods=investment_periods)
    )
    payload = hk.headline_kpis(ctx, template=None, cost=cost, lost_load=None)
    return {c["id"]: c for c in payload["cards"] if c["id"] in hk.ORDER["network"][:2]}


def test_system_cost_carries_its_total_per_investment_period(monkeypatch):
    monkeypatch.setattr(hk, "_checked", lambda ctx, cid: hk._card(cid))
    by_id = cost_cards(periods(41.0, 37.5, 33.0))
    assert by_id["system_cost"]["trend"] == [
        {"period": 2030, "value": 41.0},
        {"period": 2040, "value": 37.5},
        {"period": 2050, "value": 33.0},
    ]
    assert by_id["new_build_capital"]["trend"] is None


@pytest.mark.parametrize(
    "cost",
    [periods(41.0), periods(), periods(41.0, float("nan")), {"total": 1.0}],
)
def test_no_trend_without_two_finite_periods(monkeypatch, cost):
    monkeypatch.setattr(hk, "_checked", lambda ctx, cid: hk._card(cid))
    assert cost_cards(cost)["system_cost"]["trend"] is None
