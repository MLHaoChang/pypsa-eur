"""investment-case/1 (checked-results S8): NPV and IRR from the stored report.

Hand cash flows with a nonzero year 0 pin the engine's timing; a SAM oracle
report checks the rebuild against the engine's own headlines.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pytest

from services import workflow_tools
from services.finance import metrics
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_chat_tools_investment_case import _report
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "investment-case/1"
LCOE = "investment-lcoe/1"
# -100 then 60, 60: IRR 0.13066238629180748 (tests/test_finance_cashflow.py); at 10 %,
# -100 + 60/1.1 + 60/1.21 = 4.132231404958677.
EQUITY = [-100.0, 60.0, 60.0]
# -200, 50, 120, 80 at 7 %: -200 + 46.728971962616825 + 104.81264738404403 + 65.30381457829357.
PROJECT = [-200.0, 50.0, 120.0, 80.0]
PROJECT_NPV = -200 + 50 / 1.07 + 120 / 1.07**2 + 80 / 1.07**3


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", PROFILE + "," + LCOE)


@pytest.fixture
def project(baseline):
    ctx = baseline[0]
    assert ctx.network.optimize(solver_name="highs", include_objective_constant=False) == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(ctx.network, ctx.solver_state["solver_config"])
    yield ctx
    ctx.solver_state["investment_case_report"] = None
    ctx.solver_state["investment_case"] = None


def current_hash():
    from routers.results import _ic_current_assumptions

    key, why = _ic_current_assumptions()
    assert key is not None, why
    return key[0]


def store(ctx, mutate=None, fresh=True):
    def change(rep):
        if fresh:
            rep["assumptions_hash"] = current_hash()
        if mutate:
            mutate(rep["sections"]["project"]["payload"])

    rep = _report(mutate=change)
    ctx.solver_state["investment_case_report"] = rep
    return rep["sections"]["project"]["payload"]


def hand(payload, equity=EQUITY, project=PROJECT, coe=0.10, wacc=0.07):
    """Hand cash rows, with the headlines the engine computes on them."""
    payload["cash"]["equity_post_tax"], payload["cash"]["project_post_tax"] = equity, project
    payload["cost_of_equity"], payload["wacc_nominal"] = coe, wacc
    payload["equity_post_tax_npv"] = metrics.npv(coe, equity)
    payload["project_post_tax_npv"] = metrics.npv(wacc, project)
    payload["equity_post_tax_irr"] = metrics.irr(equity)[0]
    payload["project_post_tax_irr"] = metrics.irr(project)[0]


def published(ctx, expected, profile=PROFILE):
    """The profile's own values once they equal the hand ones (summation order differs by ulps)."""
    from harness import calculations, investment_calculations

    scope = {"version": 1, "profile": profile, "entity_id": "network"}
    raw = investment_calculations.source(ctx, "network", investment_calculations.current(ctx), profile)
    facts, _ = calculations.scoped_calculate(raw, scope)
    values = {f["metric"]: f["value"] for f in facts if f["metric"] in expected}
    assert values == pytest.approx(expected, rel=1e-12, abs=1e-12)
    return values


def ask(checked, ctx, values, profile=PROFILE):
    scope = {"version": 1, "profile": profile, "project_id": ctx.project_uuid,
             "entity_id": "network", "retain": False}
    return checked.run(NumericalProvider(values), message="Check the returns.", checked_scope=scope)


def answered(checked, frames):
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return {b["metric"]: b["value"] for b in response.json()["blocks"]}


def reasons(frames):
    return {s["metric"]: s["reason"] for s in approval(frames)["receipt"]["result_slots"]
            if s["disposition"] != "answered"}


def test_hand_cash_with_a_nonzero_first_year(project, checked):
    store(project, hand)
    values = published(project, {"equity_npv": 4.132231404958677, "project_npv": PROJECT_NPV,
                                 "equity_irr": 0.13066238629180748, "project_irr": metrics.irr(PROJECT)[0]})
    frames = ask(checked, project, values)
    assert answered(checked, frames) == pytest.approx(values, rel=1e-12)
    text = public_text(frames)
    assert "cost of equity 10.0000%" in text and "WACC 7.0000%" in text
    assert "(financial close) is undiscounted" in text
    assert "Money basis, as the report states it: nominal basis" in text
    assert "The LCOE is checked separately (investment-lcoe/1)" in text


def test_the_sam_report_matches_its_own_headlines(project, checked):
    payload = store(project)
    values = published(project, {
        "equity_npv": payload["equity_post_tax_npv"], "project_npv": payload["project_post_tax_npv"],
        "equity_irr": payload["equity_post_tax_irr"], "project_irr": payload["project_post_tax_irr"]})
    assert len(values) == 4
    assert answered(checked, ask(checked, project, values)) == pytest.approx(values, rel=1e-9)


def test_several_sign_changes_are_refused_where_the_engine_flags_them(project, checked):
    flows = [-100.0, 230.0, -132.0]  # roots at 10 % and 20 %
    assert "irr_multiple_sign_changes" in metrics.irr(flows)[1]
    store(project, lambda p: hand(p, equity=flows))
    values = published(project, {"equity_npv": metrics.npv(0.10, flows), "project_npv": PROJECT_NPV,
                                 "project_irr": metrics.irr(PROJECT)[0]})
    frames = ask(checked, project, values)
    assert reasons(frames) == {"equity_irr": "irr_not_unique"}


def test_an_unbracketed_root_is_refused(project, checked):
    flows = [-1.0, 1000.0]  # IRR 999 lies outside [-0.99, 10]
    store(project, lambda p: hand(p, equity=flows))
    values = published(project, {"equity_npv": metrics.npv(0.10, flows), "project_npv": PROJECT_NPV,
                                 "project_irr": metrics.irr(PROJECT)[0]})
    frames = ask(checked, project, values)
    assert reasons(frames) == {"equity_irr": "irr_not_bracketed"}


def test_a_headline_the_rate_does_not_reproduce_gives_no_npv(project, checked):
    def other_rate(payload):
        hand(payload)
        payload["cost_of_equity"] = 0.05  # not the rate the headline used
    store(project, other_rate)
    values = published(project, {"project_npv": PROJECT_NPV, "equity_irr": 0.13066238629180748,
                                 "project_irr": metrics.irr(PROJECT)[0]})
    frames = ask(checked, project, values)
    assert reasons(frames) == {"equity_npv": "headline_mismatch"}


def test_a_missing_rate_withholds_its_npv(project, checked):
    def no_rate(payload):
        hand(payload)
        payload["wacc_nominal"] = None
    store(project, no_rate)
    values = published(project, {"equity_npv": 4.132231404958677, "equity_irr": 0.13066238629180748,
                                 "project_irr": metrics.irr(PROJECT)[0]})
    frames = ask(checked, project, values)
    assert reasons(frames) == {"project_npv": "npv_rate_not_recorded"}


def test_a_report_stale_on_its_own_key_is_refused_with_a_fresh_dispatch(project, checked):
    # The solve is current (the dispatch fingerprint matches); the report's key is not.
    store(project, hand, fresh=False)
    frames = ask(checked, project, {})
    assert set(reasons(frames).values()) == {"investment_case_not_current"}


def test_no_report_is_refused(project, checked):
    assert set(reasons(ask(checked, project, {})).values()) == {"investment_case_not_current"}


def test_a_report_made_stale_by_a_finance_change_is_refused(project, checked):
    store(project, hand)
    project.solver_state["solver_config"].finance = {
        **(project.solver_state["solver_config"].finance or {}), "cost_of_equity": 0.2}
    assert set(reasons(ask(checked, project, {})).values()) == {"investment_case_not_current"}


def test_the_money_basis_is_the_reports_own_and_a_free_text_currency_is_no_unit(project, checked):
    def odd(payload):
        hand(payload)
        payload["currency"] = "<b>"
        payload["basis_statement"] = "nominal basis, costs in real 2027 EUR (2020 price data)"
    store(project, odd)
    values = published(project, {"equity_npv": 4.132231404958677, "project_npv": PROJECT_NPV,
                                 "equity_irr": 0.13066238629180748, "project_irr": metrics.irr(PROJECT)[0]})
    frames = ask(checked, project, values)
    text = public_text(frames)
    assert "costs in real 2027 EUR (2020 price data)" in text and "<b>" not in text
    answer = checked.client.get("/api/chat/answers/" + approval(frames)["answer_id"]).json()
    assert {b["unit"] for b in answer["blocks"]} == {"report currency", "fraction/year"}


# ── LCOE (investment-lcoe/1) ──────────────────────────────────────────────


def lcoe_hand(payload, energy=(0.0, 10.0, 10.0), value=(0.0, 100.0, 100.0), inflation=0.02, basis="nominal"):
    """Value 100 and 10 MWh in years 1 and 2 beside the equity cash -100, 60, 60 at 10 %."""
    hand(payload)
    payload["years"] = payload["years"][:3]
    payload["price_basis"] = basis
    payload["lcoe_inputs"] = {"energy_mwh": list(energy), "value": list(value), "inflation": inflation}
    r = 0.10
    num = sum(v / (1 + r) ** k for k, v in enumerate(value)) - sum(c / (1 + r) ** k for k, c in enumerate(EQUITY))
    den = sum(e / (1 + r) ** k for k, e in enumerate(energy))
    payload["lcoe_nominal_per_mwh"] = num / den if den > 0 else None
    rr = (1 + r) / (1 + inflation) - 1 if inflation is not None else None
    payload["lcoe_real_per_mwh"] = (payload["lcoe_nominal_per_mwh"] if basis == "real" else None
                                    if rr is None or den <= 0 else
                                    num / sum(e / (1 + rr) ** k for k, e in enumerate(energy)))


# (100/1.1 + 100/1.21 - 4.132231404958677) / (10/1.1 + 10/1.21) = 169.42148760330576 / 17.355371900826448
LCOE_NOMINAL = 169.42148760330576 / 17.355371900826448


def test_the_lcoe_from_the_retained_series(project, checked):
    store(project, lcoe_hand)
    assert LCOE_NOMINAL == pytest.approx(9.761904761904763)
    values = published(project, {"lcoe_nominal": LCOE_NOMINAL,
                                 "lcoe_real": 169.42148760330576 / (10 / 1.0784313725490196 + 10 / 1.0784313725490196**2)},
                       LCOE)
    frames = ask(checked, project, values, LCOE)
    assert answered(checked, frames) == values
    text = public_text(frames)
    assert "USD/MWh" in text and "incremental value" not in text


def test_the_sam_reports_lcoe_matches_its_headline(project, checked):
    payload = store(project)
    expected = {"lcoe_nominal": payload["lcoe_nominal_per_mwh"]}
    if payload["lcoe_real_per_mwh"] is not None:
        expected["lcoe_real"] = payload["lcoe_real_per_mwh"]
    values = published(project, expected, LCOE)
    assert values.keys() == expected.keys()


def test_zero_generation_is_refused_not_divided(project, checked):
    store(project, lambda p: lcoe_hand(p, energy=(0.0, 0.0, 0.0)))
    frames = ask(checked, project, {}, LCOE)
    assert reasons(frames) == {"lcoe_nominal": "no_discounted_generation", "lcoe_real": "no_discounted_generation"}


def test_a_report_stored_before_the_series_were_retained(project, checked):
    def old(payload):
        lcoe_hand(payload)
        del payload["lcoe_inputs"]
    store(project, old)
    assert set(reasons(ask(checked, project, {}, LCOE)).values()) == {"lcoe_inputs_not_retained"}


def test_no_inflation_withholds_only_the_real_lcoe(project, checked):
    store(project, lambda p: lcoe_hand(p, inflation=None))
    values = published(project, {"lcoe_nominal": LCOE_NOMINAL}, LCOE)
    frames = ask(checked, project, values, LCOE)
    assert reasons(frames) == {"lcoe_real": "inflation_not_recorded"}


def test_on_a_real_basis_the_real_lcoe_is_the_nominal_one(project, checked):
    store(project, lambda p: lcoe_hand(p, basis="real"))
    values = published(project, {"lcoe_nominal": LCOE_NOMINAL, "lcoe_real": LCOE_NOMINAL}, LCOE)
    assert values["lcoe_real"] == values["lcoe_nominal"]


def test_a_counterfactual_says_the_lcoe_is_on_incremental_value(project, checked):
    def incremental(payload):
        lcoe_hand(payload)
        payload["has_counterfactual"] = True
    store(project, incremental)
    values = published(project, {"lcoe_nominal": LCOE_NOMINAL}, LCOE)
    real = published(project, {"lcoe_real": 169.42148760330576 / (10 / 1.0784313725490196 + 10 / 1.0784313725490196**2)},
                     LCOE)
    frames = ask(checked, project, {**values, **real}, LCOE)
    assert "on the incremental value against the counterfactual" in public_text(frames)


def test_a_counterfactual_adds_no_lcoe_note_to_the_returns():
    from harness import investment_calculations

    raw = {"profile": PROFILE, "has_counterfactual": True, "index": ["2026"], "basis_statement": "nominal basis",
           "rates": {"cost_of_equity": 0.10, "wacc_nominal": 0.07}}
    text = " ".join(investment_calculations.notes(raw, str))
    assert "incremental value" not in text
    assert "The LCOE is checked separately (investment-lcoe/1)" in text


def test_an_unknown_retained_year_is_named_as_such(project, checked):
    def gap(payload):
        lcoe_hand(payload)
        payload["lcoe_inputs"]["energy_mwh"][1] = None
    store(project, gap)
    assert set(reasons(ask(checked, project, {}, LCOE)).values()) == {"lcoe_inputs_not_established"}
