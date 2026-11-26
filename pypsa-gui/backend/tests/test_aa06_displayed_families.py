"""Assistant-assurance 06, A5.2 (S2): displayed-data proof for Emissions and
Grid risk to production.

Each tab renders `/api/results/displayed/<tab>`: the payload its old route
serves (byte-equal in the canonical encoding, so the two can never show
different numbers) plus a binding the ACK re-reads. Oracles are hand-computed
from the fixture networks, never from the adapter.
"""

# ruff: noqa: F811 -- imported pytest fixtures
import copy
import hashlib

import pandas as pd
import pypsa
import pytest

from harness import navigation
from harness.navigation import OrdinaryTurn
from harness.session import ChatSession
from routers.results_displayed import canonical
from services import assurance_records, chat_tools
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

# ── Emissions fixture: one gas unit, hand oracle ────────────────────────────
# Load 10 MW for 3 h, gas only: 30 MWh out. Intensity 0.2 t/MWh primary,
# efficiency 0.4: 30 / 0.4 * 0.2 = 15 t CO2.
EMISSIONS_T = 15.0


def _emissions_network(load=10.0):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Bus", "B1", v_nom=110)
    n.add(
        "Generator",
        "G_gas",
        bus="B1",
        carrier="gas",
        p_nom=50,
        efficiency=0.4,
        marginal_cost=30,
    )
    n.add("Load", "L1", bus="B1", p_set=load)
    assert n.optimize(solver_name="highs", include_objective_constant=False)[0] == "ok"
    return n


@pytest.fixture
def solved(baseline):
    ctx, project_id, root = baseline
    ctx.network = _emissions_network()
    ctx.solver_state.update(status="completed", condition="optimal")
    return ctx


def _turn(ctx, generation=3):
    owner = assurance_records.actor_id()
    turn = OrdinaryTurn(ChatSession(owner_user_id=owner), ctx, generation)
    navigation.begin_turn(turn, generation)
    return turn


def _issue(ctx, tab, generation=3):
    turn = _turn(ctx, generation)
    return turn, navigation.issue(
        turn, {"kind": "navigate", "panel_id": "results", "results_tab": tab}
    )


def _ack_body(ack):
    return {
        k: ack[k]
        for k in (
            "version",
            "action_id",
            "turn_id",
            "binding_generation",
            "target",
            "source_binding",
        )
    }


def _outcome(client, action_id):
    return client.get(f"/api/chat/navigation/{action_id}").json()["outcome"]


# ── Canonical encoding ──────────────────────────────────────────────────────


def test_canonical_maps_non_finite_to_null_and_sorts():
    assert (
        canonical({"b": [float("inf"), 1.5], "a": float("nan"), "c": -float("inf")})
        == '{"a":null,"b":[null,1.5],"c":null}'
    )


# ── One source of truth: the bound payload is the old route's payload ───────


def test_emissions_view_is_the_old_route_byte_equal_and_the_hand_oracle(client, solved):
    old = client.get("/api/results/emissions")
    new = client.get("/api/results/displayed/emissions")
    assert old.status_code == new.status_code == 200, (old.text[:200], new.text[:200])
    body = new.json()
    assert canonical(body["data"]) == canonical(old.json())
    assert body["data"]["total_tCO2"] == pytest.approx(EMISSIONS_T)
    binding = body["binding"]
    assert set(binding) == {
        "version",
        "project_id",
        "project_name",
        "scope",
        "view",
        "input_sha256",
        "data_sha256",
    }
    assert binding["view"] == "emissions" and binding["scope"] == "current"
    assert binding["project_id"] == str(solved.project_uuid)
    assert (
        binding["data_sha256"]
        == hashlib.sha256(canonical(old.json()).encode()).hexdigest()
    )


def test_grid_risk_view_is_the_old_route_byte_equal(client, solved):
    old = client.get("/api/results/grid_risk_production")
    new = client.get("/api/results/displayed/grid_risk")
    assert old.status_code == new.status_code == 200, (old.text[:200], new.text[:200])
    assert canonical(new.json()["data"]) == canonical(old.json())
    # No process unit in this network: the tab says so, and that is what is bound.
    assert new.json()["data"]["omitted"] == "no_process_unit"


def test_an_unsolved_project_answers_204_like_the_old_route(client, baseline):
    assert client.get("/api/results/emissions").status_code == 204
    assert client.get("/api/results/displayed/emissions").status_code == 204


# ── ACK: completes on the exact binding, 409 on every change ────────────────


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
def test_ack_of_the_rendered_binding_is_displayed_data(client, solved, tab):
    _, ack = _issue(solved, tab)
    served = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    assert ack["version"] == 2 and ack["source_binding"] == served
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


def _resolve(ctx):
    # A re-solve with the same inputs but different figures (as a different
    # solver path would give): only the result tables change.
    ctx.network.generators_t.p.loc[:, "G_gas"] += 1.0


def _input_edit(ctx):
    ctx.network.loads.loc["L1", "p_set"] = 12.0


@pytest.mark.parametrize("change", [_resolve, _input_edit])
def test_emissions_ack_after_a_resolve_or_an_input_edit_is_refused(
    client, solved, change
):
    _, ack = _issue(solved, "emissions")
    change(solved)
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert _outcome(client, ack["action_id"]) == "submitted"


def test_emissions_ack_after_a_project_switch_is_refused(client, solved):
    _, ack = _issue(solved, "emissions")
    original = solved.project_uuid
    solved.project_uuid = "00000000-0000-4000-8000-000000000001"
    try:
        assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 409
    finally:
        solved.project_uuid = original
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize(
    "field,value",
    [
        ("data_sha256", "0" * 64),
        ("view", "grid_risk"),
        ("input_sha256", "0" * 64),
    ],
)
def test_emissions_ack_with_another_binding_is_refused(client, solved, field, value):
    _, ack = _issue(solved, "emissions")
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"][field] = value
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    assert _outcome(client, ack["action_id"]) == "submitted"


def test_an_emissions_binding_cannot_complete_a_grid_risk_action(client, solved):
    _, emissions = _issue(solved, "emissions")
    _, grid = _issue(solved, "grid_risk")
    body = {**_ack_body(grid), "source_binding": emissions["source_binding"]}
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409


def test_an_unsolved_tab_issues_no_displayed_data_action(baseline):
    _, ack = _issue(baseline[0], "emissions")
    assert ack is None


# ── "showing" only when the tab shows exactly what the turn read ────────────


def test_showing_after_the_turn_read_the_same_emissions(client, solved):
    turn, ack = _issue(solved, "emissions")
    turn.note_read("get_results", chat_tools.get_results(result_kind="emissions"))
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 200
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data",
        "say": "showing",
    }


def test_a_read_before_a_resolve_is_opened_not_showing(client, solved):
    turn = _turn(solved)
    turn.note_read("get_results", chat_tools.get_results(result_kind="emissions"))
    # Another results read is not an emissions read and leaves it in place.
    turn.note_read("get_results", chat_tools.get_results(result_kind="generators"))
    assert set(turn.reads) == {"emissions"}
    _resolve(solved)
    ack = navigation.issue(
        turn, {"kind": "navigate", "panel_id": "results", "results_tab": "emissions"}
    )
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 200
    out = navigation.display(turn, ack, wait=0)
    assert out["verification"] == "browser_displayed_data" and out["say"] == "opened"
    assert out["differs_from_read"]["read"] != out["differs_from_read"]["shown"]


def test_grid_risk_view_binds_the_hand_oracle_connection_line(client, baseline):
    """The grid-to-process chain of test_grid_risk_production: the grid is cut
    for one of six hours, demand 4 t/h, no inventory, so 4 t are lost; per year
    4 x 8760 / 6 = 5840 t, at 100 EUR/t."""
    from tests.test_grid_risk_production import _network

    ctx = baseline[0]
    ctx.network = _network()
    ctx.solver_state.update(status="completed", condition="optimal")
    body = client.get("/api/results/displayed/grid_risk").json()
    assert body["data"]["available"] is True
    line = next(ln for ln in body["data"]["lines"] if ln["key"] == "connection")
    assert line["by_period"]["_"]["t_per_year"] == pytest.approx(5840.0)
    assert line["by_period"]["_"]["eur_per_year"] == pytest.approx(584000.0)
    old = client.get("/api/results/grid_risk_production").json()
    assert canonical(body["data"]) == canonical(old)
    _, ack = _issue(ctx, "grid_risk")
    assert ack["source_binding"] == body["binding"]
    assert (
        client.post("/api/chat/ui-ack", json=_ack_body(ack)).json()["verification"]
        == "browser_displayed_data"
    )
