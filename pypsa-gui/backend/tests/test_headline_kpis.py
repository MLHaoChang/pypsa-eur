"""The headline figures at the top of Results (ui-modern 03 S1)."""
import pandas as pd
import pytest

from routers import results as results_router
from services.results import headline_kpis as hk
from tests.test_result_profiles_assurance import solve, windy  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

URL = "/api/results/headline_kpis"


def cards(client):
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["kind"], {c["id"]: c for c in body["cards"]}, [c["id"] for c in body["cards"]]


def as_template(monkeypatch, template_id):
    monkeypatch.setattr(results_router, "_workbook_template", lambda: {"id": template_id})


def test_nothing_solved_answers_204(baseline, client):
    assert client.get(URL).status_code == 204


def test_a_plain_network_reads_cost_and_the_checked_curtailed_share(windy, client):
    solve(windy)
    kind, by_id, order = cards(client)
    assert (kind, tuple(order)) == ("network", hk.ORDER["network"])
    cost = client.get("/api/results/cost_breakdown").json()
    # The strip and the Capacity expansion tab read one payload.
    assert by_id["system_cost"]["value"] == pytest.approx(cost["total"])
    assert by_id["system_cost"]["unit"] == "currency/yr"
    assert by_id["new_build_capital"]["value"] == pytest.approx(cost["capex_expansion"])
    # 33 of 50 MWh available curtailed (the `windy` fixture).
    share = by_id["curtailed_share"]
    assert share["value"] == pytest.approx(66) and share["unit"] == "%" and share["state"] == "ready"
    assert share["formula_id"] == "curtailment-share-of-available/1"


def test_a_figure_the_run_did_not_produce_is_a_state_with_its_sentence(windy, client):
    solve(windy)
    _, by_id, _ = cards(client)
    # No lines: nothing to load. A computed zero keeps its 0.
    line = by_id["peak_line_loading"]
    assert (line["value"], line["state"], line["reason"]) == (None, "not_applicable", hk.REASONS["unknown_branch"][1])
    assert by_id["new_build_capital"]["value"] == 0 and by_id["new_build_capital"]["state"] == "zero"


def test_peak_line_loading_names_its_line(windy, client):
    n = windy.network
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
    windy.network.loads_t.p_set = pd.DataFrame({"L1": [6, 7]}, index=windy.network.snapshots)
    _, by_id, _ = cards(client)
    share = by_id["curtailed_share"]
    assert (share["value"], share["state"]) == (None, "stale")
    assert share["reason"] == "The inputs changed since this solve."


def test_an_energy_hub_leads_with_lcoe_and_says_when_the_case_has_not_run(windy, client, monkeypatch):
    solve(windy)
    as_template(monkeypatch, "eh_datacenter")
    kind, by_id, order = cards(client)
    assert (kind, tuple(order)) == ("energy_hub", hk.ORDER["energy_hub"])
    assert (by_id["lcoe"]["value"], by_id["lcoe"]["state"]) == (None, "not_run")
    assert by_id["lcoe"]["reason"] == "The investment case has not run on this solve."
    # No value of lost load in the solve: unserved energy was not measured, not zero.
    unserved = by_id["unserved_energy"]
    assert (unserved["value"], unserved["state"]) == (None, "not_modelled")


def test_a_microgrid_leads_with_unserved_energy_and_keeps_a_real_zero(windy, client, monkeypatch):
    solve(windy, voll=1000)
    as_template(monkeypatch, "eh_microgrid_staged")
    kind, by_id, order = cards(client)
    assert (kind, order[0]) == ("microgrid", "unserved_energy")
    unserved = by_id["unserved_energy"]
    assert (unserved["value"], unserved["unit"], unserved["state"]) == (0, "MWh", "zero")


@pytest.mark.parametrize("template, kind", [
    (None, "network"), ({"id": "eh_microgrid"}, "microgrid"), ({"id": "eh_datacenter_staged"}, "energy_hub"),
    ({"id": "eh_site", "site_type": "hospital"}, "energy_hub"),
])
def test_project_kind(template, kind):
    assert hk.project_kind(template) == kind


def test_card_sets_are_short_and_reasons_carry_no_engine_identifier():
    assert all(len(ids) <= 5 and len(set(ids)) == len(ids) for ids in hk.ORDER.values())
    sentences = [r for _, r in hk.REASONS.values()] + [hk._UNAVAILABLE[1], hk.NOT_MODELLED_LOST_LOAD, hk.NO_COST]
    for sentence in sentences:
        assert "_" not in sentence and "/" not in sentence and sentence.endswith(".")
    assert {s for s, _ in hk.REASONS.values()} <= {"stale", "not_applicable", "not_run", "unavailable"}
