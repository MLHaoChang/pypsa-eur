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


@pytest.mark.parametrize(
    "tab,change",
    [("emissions", _resolve), ("emissions", _input_edit), ("grid_risk", _input_edit)],
)
def test_ack_after_a_resolve_or_an_input_edit_is_refused(client, solved, tab, change):
    _, ack = _issue(solved, tab)
    change(solved)
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
def test_ack_after_a_project_switch_is_refused(client, solved, tab):
    _, ack = _issue(solved, tab)
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
    # (A generators series has curtailment's shape, so it is noted as a
    # curtailment read whose hash never matches the curtailment shown: S3a.)
    turn.note_read("get_results", chat_tools.get_results(result_kind="generators"))
    assert set(turn.reads) == {"emissions", "curtailment"}
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


# ── GK B NOT OK @3631166f: tests for the surviving mutants ──────────────────


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
def test_the_binding_input_hash_is_the_plain_network_fingerprint(client, solved, tab):
    from services import workflow_tools

    served = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    assert served["input_sha256"] == workflow_tools._network_input_fingerprint(solved)
    _input_edit(solved)
    again = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    assert again["input_sha256"] == workflow_tools._network_input_fingerprint(solved)
    assert again["input_sha256"] != served["input_sha256"]


def test_a_non_finite_value_is_null_in_the_data_its_hash_and_the_ack(
    client, solved, monkeypatch
):
    from routers import results

    payload = {"total_t": float("nan"), "peak_t": float("inf"), "rows": [1.0]}
    monkeypatch.setattr(results, "get_emissions", lambda *_a, **_k: payload)
    body = client.get("/api/results/displayed/emissions").json()
    assert body["data"] == {"total_t": None, "peak_t": None, "rows": [1.0]}
    expected = hashlib.sha256(
        b'{"peak_t":null,"rows":[1.0],"total_t":null}'
    ).hexdigest()
    assert canonical(payload) == '{"peak_t":null,"rows":[1.0],"total_t":null}'
    assert body["binding"]["data_sha256"] == expected
    _, ack = _issue(solved, "emissions")
    assert ack["source_binding"] == body["binding"]
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
def test_a_held_network_lock_answers_409(client, solved, tab):
    import threading

    from services.pypsa_service import PyPSAService

    lock = PyPSAService.get_lock()
    held, release = threading.Event(), threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(10)

    worker = threading.Thread(target=hold)
    worker.start()
    assert held.wait(5)
    try:
        response = client.get(f"/api/results/displayed/{tab}")
    finally:
        release.set()
        worker.join()
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "assurance_context_changed"


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
def test_a_reader_without_access_to_the_open_project_gets_404(_auth_db, solved, tab):
    from fastapi.testclient import TestClient

    import main
    from tests.conftest import attach_session
    from tests.test_chat_session_ownership import _other_org_user

    uid, session_local = _other_org_user(_auth_db)
    with TestClient(main.app) as c:
        outsider = attach_session(c, session_local, uid)
        response = outsider.get(f"/api/results/displayed/{tab}")
    assert response.status_code == 404
    assert response.json()["detail"]["error_kind"] == "not_authorized"


@pytest.mark.parametrize(
    "field,value", [("run_id", "run-2"), ("window", {"from": 0, "to": 2})]
)
def test_a_binding_naming_a_run_or_window_these_views_lack_is_refused(
    client, solved, field, value
):
    """Emissions and Grid risk have no run or window selector: a binding that
    names one (another run or window) is not this view's binding shape, so the
    ACK refuses it as malformed (422) and the action stays submitted."""
    _, ack = _issue(solved, "emissions")
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"][field] = value
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422
    assert _outcome(client, ack["action_id"]) == "submitted"


# ── mutants.py survivors (S2 run at 3631166f) ───────────────────────────────


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
def test_the_binding_names_the_open_project(client, solved, tab):
    binding = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    assert binding["project_id"] == str(solved.project_uuid)
    assert binding["project_name"] == str(solved.loaded_project)
    assert binding["project_name"] != ""


def test_a_binding_without_an_open_project_names_none_as_empty(solved, monkeypatch):
    from routers import results, results_displayed

    monkeypatch.setattr(solved, "project_uuid", None)
    monkeypatch.setattr(solved, "loaded_project", None)
    binding = results_displayed._bound("emissions", results.get_emissions("lopf"))[
        "binding"
    ]
    assert binding["project_id"] == "" and binding["project_name"] == ""


@pytest.mark.parametrize("tab", ["emissions", "grid_risk"])
@pytest.mark.parametrize("result", [None, "text", 3, []])
def test_a_non_payload_read_has_no_ref(tab, result):
    from harness.displayed_adapters import ADAPTERS

    assert ADAPTERS[tab].read_ref(result) is None


def test_the_grid_risk_read_of_the_shown_payload_is_its_hash(client, baseline):
    """The grid-risk chat read is "showing" only for the available payload the
    tab renders: its ref is the binding's hash; an unavailable or partial
    payload has no ref."""
    from harness.displayed_adapters import ADAPTERS
    from tests.test_grid_risk_production import _network

    ctx = baseline[0]
    ctx.network = _network()
    ctx.solver_state.update(status="completed", condition="optimal")
    shown = client.get("/api/results/displayed/grid_risk").json()["binding"]
    read = client.get("/api/results/grid_risk_production").json()
    assert read["available"] is True
    assert ADAPTERS["grid_risk"].read_ref(read) == {"data_sha256": shown["data_sha256"]}
    for partial in (
        {**read, "available": False},
        {**read, "available": "yes"},
        {k: v for k, v in read.items() if k != "omitted"},
        {k: v for k, v in read.items() if k != "project_id"},
    ):
        assert ADAPTERS["grid_risk"].read_ref(partial) is None


def test_the_served_data_keeps_the_routes_key_order_and_only_the_hash_sorts(
    client, solved, monkeypatch
):
    """A tab may draw a dict's keys in order (carrier stacks): the view serves the
    route's order; the canonical sorted form is only what is hashed."""
    from routers import results

    payload = {"z_first": {"solar": 1.0, "grid": 2.0}, "a_last": [1]}
    monkeypatch.setattr(results, "get_emissions", lambda *_a, **_k: payload)
    response = client.get("/api/results/displayed/emissions")
    data = response.json()["data"]
    assert list(data) == ["z_first", "a_last"]
    assert list(data["z_first"]) == ["solar", "grid"]
    assert (
        response.json()["binding"]["data_sha256"]
        == hashlib.sha256(canonical(payload).encode()).hexdigest()
    )
