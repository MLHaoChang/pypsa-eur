"""
LC-16 slice C. Scope 5: in a chat turn a multi-asset edit goes through the one
preview card (`harness/edit_routing.py`); the HTTP routes are unchanged.
Scope 6 (OJ-44): settings edits show old → new rows (`harness/edit_card.py`).
Plus the LC-D02 and LC-D09 batch cases.
"""
from __future__ import annotations

import json
import uuid

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import edit_card, edit_routing
from routers import simulation as sim_router
from routers.network_time_axis import update_investment_period_weightings
from services import chat_service, chat_tools, undo_service
from services import workflow_tools as wt
from services.pypsa_service import PyPSAService
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _text_block,
    _text_event,
    _tool_use_block,
    _tool_use_event,
)
from tests.test_project_changes_multi import dispatch, frame, mixed  # noqa: F401


@pytest.fixture
def grid(install_network):
    n = pypsa.Network()
    n.set_snapshots(range(2))
    for bus in ("B1", "B2"):
        n.add("Bus", bus, v_nom=110)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=12.1, r=0.1, s_nom=300, length=10)
    n.add("Line", "L2", bus0="B1", bus1="B2", x=12.1, r=0.1, s_nom=300, length=10)
    n.add("Generator", "G1", bus="B1", p_nom=50, carrier="solar")
    install_network(n, name=None)
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    yield session
    chat_tools.set_chat_session(None)


def _call(name, args):
    return {"type": "tool_use", "id": f"tu-{uuid.uuid4().hex[:8]}", "name": name, "input": args}


def _route(session, calls):
    """One model response: the loop's pre-scan, then each call through the dispatcher."""
    session.turn_redirects = edit_routing.redirects(calls, session.turn_single_edit_applied)
    frames, collector = [], []
    for tu in calls:
        for event, data in chat_service._dispatch_real_tool_call(session, tu, collector):
            frames.append((event, data))
            if event == "tool_pending_confirmation":
                session.record_decision(data["confirmation_token"], "approve")
    return frames, collector


def _refused(collector, tool_use_id):
    result = next(r for r in collector if r["tool_use_id"] == tool_use_id)
    assert result["is_error"] is True and "use_project_preview" in result["content"]
    return result["content"]


def _changes_in(content):
    text = content[content.index("changes=") + len("changes="):]
    return json.JSONDecoder().raw_decode(text)[0]


def _s_nom():
    lines = PyPSAService.get_network().lines
    return lines.at["L1", "s_nom"], lines.at["L2", "s_nom"]


# ── Scope 5: routing ──────────────────────────────────────────────────────

def test_bulk_numeric_in_chat_refuses_with_preview_form(grid):
    tu = _call("bulk_update_components", {"component_class": "Line", "names": ["L1", "L2"], "updates": {"s_nom": 500}})
    frames, collector = _route(grid, [tu])
    # Model-only: no frame of any kind reaches the user for the refused call.
    assert frames == []
    assert _changes_in(_refused(collector, tu["id"])) == [
        {"component_class": "Line", "names": ["L1", "L2"], "attribute": "s_nom", "value": 500}]
    assert _s_nom() == (300, 300)
    assert edit_routing.KIND == "use_project_preview"


def test_bulk_string_field_keeps_its_card(grid):
    tu = _call("bulk_update_components", {"component_class": "Line", "names": ["L1", "L2"], "updates": {"carrier": "AC2"}})
    frames, collector = _route(grid, [tu])
    assert frame(frames, "tool_pending_confirmation")["edit_card"]["complete"] is True
    assert not collector[-1].get("is_error")
    assert list(PyPSAService.get_network().lines.carrier) == ["AC2", "AC2"]


def test_a_field_is_eligible_per_class(grid):
    # p_nom is a Generator number field; a Bus has none, so the same key routes per class.
    assert edit_routing.preview_changes("update_component", {"component_class": "Generator", "name": "G1",
                                                             "attrs": {"p_nom": 60}})
    assert edit_routing.preview_changes("update_component", {"component_class": "Bus", "name": "B1",
                                                             "attrs": {"p_nom": 60}}) is None
    assert edit_routing.preview_changes("update_component", {"component_class": "Line", "name": "L1",
                                                             "attrs": {"s_nom": 1}, "new_name": "X"}) is None


def test_two_update_components_in_one_response_both_refuse(grid):
    a = _call("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 500}})
    b = _call("update_component", {"component_class": "Line", "name": "L2", "attrs": {"s_nom": 450}})
    frames, collector = _route(grid, [a, b])
    assert frames == []
    assert [r["tool_use_id"] for r in collector] == [a["id"], b["id"]]   # one result per tool_use
    expected = [{"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": 500},
                {"component_class": "Line", "names": ["L2"], "attribute": "s_nom", "value": 450}]
    assert _changes_in(_refused(collector, a["id"])) == expected == _changes_in(_refused(collector, b["id"]))
    assert _s_nom() == (300, 300)


def test_second_update_component_in_a_turn_refuses_and_names_only_itself(grid):
    a = _call("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 500}})
    frames, collector = _route(grid, [a])
    assert not collector[-1].get("is_error") and _s_nom() == (500, 300)
    assert grid.turn_single_edit_applied is True
    b = _call("update_component", {"component_class": "Line", "name": "L2", "attrs": {"s_nom": 450}})
    frames, collector = _route(grid, [b])
    assert frames == []
    # The applied L1 edit is never re-proposed (GK B C2).
    assert _changes_in(_refused(collector, b["id"])) == [
        {"component_class": "Line", "names": ["L2"], "attribute": "s_nom", "value": 450}]
    assert _s_nom() == (500, 300)


def test_a_denied_single_edit_does_not_count(grid):
    a = _call("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 500}})
    grid.turn_redirects = {}
    for event, data in chat_service._dispatch_real_tool_call(grid, a, []):
        if event == "tool_pending_confirmation":
            grid.record_decision(data["confirmation_token"], "deny")
    assert grid.turn_single_edit_applied is False


def test_a_run_resets_the_turn_count(grid):
    a = _call("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 500}})
    _route(grid, [a])
    assert grid.turn_single_edit_applied is True
    edit_routing.note_success(grid, "run_simulation", "execution_long_running", {})
    assert grid.turn_single_edit_applied is False
    edit_routing.note_success(grid, "update_component", "write", a["input"])
    edit_routing.note_success(grid, "apply_project_changes", "execution", {})
    assert grid.turn_single_edit_applied is False
    b = _call("update_component", {"component_class": "Line", "name": "L2", "attrs": {"s_nom": 450}})
    frames, collector = _route(grid, [b])
    assert frame(frames, "tool_pending_confirmation") and _s_nom() == (500, 450)


def test_a_string_single_edit_after_a_numeric_one_keeps_its_card(grid):
    _route(grid, [_call("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 500}})])
    frames, collector = _route(grid, [_call("update_component", {"component_class": "Line", "name": "L2",
                                                                 "attrs": {"carrier": "DC"}})])
    assert frame(frames, "tool_pending_confirmation") and not collector[-1].get("is_error")


def test_a_refusal_leaves_a_pending_confirmation_alone(grid):
    with grid._lock:
        grid.pending_confirmations["held"] = object()
    tu = _call("bulk_update_components", {"component_class": "Line", "names": ["L1", "L2"], "updates": {"s_nom": 9}})
    _route(grid, [tu])
    with grid._lock:
        assert "held" in grid.pending_confirmations
        del grid.pending_confirmations["held"]


def test_the_loop_refuses_both_calls_and_sends_one_result_each(grid):
    calls = [("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 500}}),
             ("update_component", {"component_class": "Line", "name": "L2", "attrs": {"s_nom": 450}})]
    t1 = ([_tool_use_event(f"tu-{i}", n, a) for i, (n, a) in enumerate(calls)],
          _FakeFinalMessage(content=[_tool_use_block(f"tu-{i}", n, a) for i, (n, a) in enumerate(calls)],
                            usage=_FakeUsage()))
    t2 = ([_text_event("done")], _FakeFinalMessage(content=[_text_block("done")], usage=_FakeUsage()))
    client = FakeAnthropicClient([t1, t2])
    events = list(chat_service.run_turn(grid, "set L1 500 and L2 450", client=client))
    assert not [d for e, d in events if isinstance(d, dict) and d.get("tool_use_id") in ("tu-0", "tu-1")]
    results = [b for m in client.calls[1]["messages"] if isinstance(m["content"], list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert [r["tool_use_id"] for r in results] == ["tu-0", "tu-1"]
    assert all(r["is_error"] and "use_project_preview" in r["content"] for r in results)
    assert _s_nom() == (300, 300)


def test_http_bulk_route_unchanged(client, install_network):
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110)
    n.add("Bus", "B2", v_nom=110)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, s_nom=300)
    n.add("Line", "L2", bus0="B1", bus1="B2", x=0.1, s_nom=300)
    install_network(n, name=None)
    response = client.patch("/api/network/_bulk", json={"component_class": "Line", "names": ["L1", "L2"],
                                                        "updates": {"s_nom": 500}})
    assert response.status_code == 200, response.text
    assert "use_project_preview" not in response.text


def test_eh_setup_tag_groups_keep_their_bulk_card():
    # suggest_eh_setup's grouped actions set tags (eh_role, eh_poc, eh_critical): no preview takes them.
    assert edit_routing.redirects([_call("bulk_update_components", {"component_class": "Bus", "names": ["B1", "B2"],
                                                                     "updates": {"eh_critical": True}})], True) == {}


# ── LC-D02 / LC-D09 batch cases ───────────────────────────────────────────

def test_one_bad_member_refuses_the_batch_and_stores_nothing(mixed):  # noqa: F811 — the fixture
    ctx, session, path = mixed
    before = PyPSAService.get_network().lines.s_nom.tolist(), PyPSAService.get_network().transformers.s_nom.tolist()
    with pytest.raises(HTTPException) as failure:
        chat_tools.preview_project_changes([
            {"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": 500},
            {"component_class": "Transformer", "names": ["T1"], "attribute": "s_nom", "value": 63},
            {"component_class": "Bus", "names": ["B9"], "attribute": "v_nom", "value": 20}])
    assert failure.value.detail["error_kind"] == "change_component_missing" and "B9" in failure.value.detail["message"]
    assert not list((path / "assistant_previews").glob("*.json")) if (path / "assistant_previews").exists() else True
    n = PyPSAService.get_network()
    assert (n.lines.s_nom.tolist(), n.transformers.s_nom.tolist()) == before


def test_storage_cost_batch_reads_back_exactly_and_marks_results_stale(mixed):  # noqa: F811 — the fixture
    ctx, session, _ = mixed
    n = PyPSAService.get_network()
    n.add("Store", "Tank", bus="B2", e_nom=100)
    n.meta["assistant_solve_input_fingerprint"] = wt._network_input_fingerprint(ctx)
    before = (n.storage_units.at["Battery", "standing_loss"], n.storage_units.at["Battery", "efficiency_store"],
              n.stores.at["Tank", "e_nom"], n.stores.at["Tank", "standing_loss"])
    preview = chat_tools.preview_project_changes([
        {"component_class": "StorageUnit", "names": ["Battery"], "attribute": "standing_loss", "value": 0.01},
        {"component_class": "StorageUnit", "names": ["Battery"], "attribute": "efficiency_store", "value": 0.95},
        {"component_class": "Store", "names": ["Tank"], "attribute": "e_nom", "value": 200},
        {"component_class": "Store", "names": ["Tank"], "attribute": "standing_loss", "value": 0.001}])
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]})
    assert len([d for e, d in frames if e == "tool_pending_confirmation"]) == 1
    receipt = frame(frames, "tool_result")["result"]["_edit_receipt"]
    assert receipt["outcome"] == "verified" and receipt["results_stale"] is True
    assert {(r["name"], r["field"]): r["readback"] for r in receipt["rows"] if r["role"] == "typed"} == {
        ("Battery", "standing_loss"): 0.01, ("Battery", "efficiency_store"): 0.95,
        ("Tank", "e_nom"): 200, ("Tank", "standing_loss"): 0.001}
    n = PyPSAService.get_network()
    assert (n.storage_units.at["Battery", "standing_loss"], n.storage_units.at["Battery", "efficiency_store"],
            n.stores.at["Tank", "e_nom"], n.stores.at["Tank", "standing_loss"]) == (0.01, 0.95, 200, 0.001)
    assert undo_service.depth() == 1
    chat_tools.undo_last()
    n = PyPSAService.get_network()
    assert (n.storage_units.at["Battery", "standing_loss"], n.storage_units.at["Battery", "efficiency_store"],
            n.stores.at["Tank", "e_nom"], n.stores.at["Tank", "standing_loss"]) == before


# ── Scope 6: settings cards (OJ-44) ───────────────────────────────────────

def _card(session, name, args):
    tu = _call(name, args)
    session.turn_redirects = {}
    frames = []
    for event, data in chat_service._dispatch_real_tool_call(session, tu, []):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
    return frames


def _typed(card):
    return [(r["field"], r["old"], r["new"], r["unit"]) for r in card["rows"] if r["role"] == "typed"]


def test_solver_card_rolling_rows_and_note(grid):
    frames = _card(grid, "update_solver_config", {"partial": {"solve_strategy": "rolling", "rolling_horizon": 24}})
    card = frame(frames, "tool_pending_confirmation")["edit_card"]
    assert card["kind"] == "settings_edit" and card["complete"] is True
    assert _typed(card) == [("solve_strategy", "full", "rolling", ""), ("rolling_horizon", 168, 24, "snapshots")]
    strategy, horizon = [r for r in card["rows"] if r["role"] == "typed"]
    assert strategy["label"] == "Solve strategy"
    assert strategy["words"] == {"old": "Full LP", "new": "Rolling horizon"}
    assert horizon["label"] == "Horizon" and horizon["old_applies"] is False
    note = next(r for r in card["rows"] if r["role"] == "note")
    assert note["note"] == edit_card.ROLLING_NOTE and note["component_class"] == "" and note["name"] == ""
    receipt = frame(frames, "tool_result")["result"]["_edit_receipt"]
    assert receipt["outcome"] == "verified"
    # The Expert receipt says what the card shows: a horizon that did not apply was "—", not 168.
    assert "rolling_horizon — → 24 snapshots" in card["says"] and receipt["says"] == card["says"]
    cfg = sim_router._state["solver_config"]
    assert (cfg.solve_strategy, cfg.rolling_horizon) == ("rolling", 24)


def test_economics_basis_card_shows_percent(grid):
    frames = _card(grid, "update_solver_config", {"partial": {"discount_rate": 0.05, "default_lifetime": 30}})
    rate, life = [r for r in frame(frames, "tool_pending_confirmation")["edit_card"]["rows"] if r["role"] == "typed"]
    assert (rate["label"], rate["old"], rate["new"]) == ("Discount rate", 0.07, 0.05)
    assert rate["display"]["unit"] == "%" and rate["display"]["new"] == pytest.approx(5.0)
    assert (life["label"], life["unit"], life["old"], life["new"]) == ("Default lifetime", "yr", 25.0, 30)
    assert sim_router._state["solver_config"].discount_rate == 0.05


def test_solver_card_refuses_schema_invalid_value_with_no_card(grid):
    frames = _card(grid, "update_solver_config", {"partial": {"target_lole_h": -1}})
    assert "tool_pending_confirmation" not in [e for e, _ in frames]
    assert frame(frames, "tool_error")["error_kind"] == "invalid_edit_value"
    assert sim_router._state["solver_config"].target_lole_h is None


def test_solver_goal_fields_get_labels(grid):
    frames = _card(grid, "update_solver_config", {"partial": {"target_lole_h": 3.0, "mc_draws": 200}})
    rows = [r for r in frame(frames, "tool_pending_confirmation")["edit_card"]["rows"] if r["role"] == "typed"]
    assert [(r["label"], r["unit"]) for r in rows] == [("Allowed shortfall", "h/yr"), ("Simulation runs per check", "")]


def test_solver_finance_key_refuses(grid):
    frames = _card(grid, "update_solver_config", {"partial": {"finance": {}}})
    assert frame(frames, "tool_error")["error_kind"] == "settings_own_route"
    assert "tool_running" not in [e for e, _ in frames]


def test_meta_card_old_new_readback(grid):
    PyPSAService.get_network().name = "Old"
    frames = _card(grid, "update_meta", {"name": "Harbour grid"})
    card = frame(frames, "tool_pending_confirmation")["edit_card"]
    assert card["complete"] is True and _typed(card) == [("name", "Old", "Harbour grid", "")]
    assert card["rows"][0]["label"] == "Network name"
    assert frame(frames, "tool_result")["result"]["_edit_receipt"]["outcome"] == "verified"
    assert PyPSAService.get_network().name == "Harbour grid"


@pytest.fixture
def periods(grid):
    n = PyPSAService.get_network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], [0, 1]]))
    n.investment_periods = [2030, 2040]
    return grid


def test_weightings_card_rows_sorted_by_period(periods):
    frames = _card(periods, "set_investment_period_weightings",
                   {"updates": {"2040": {"years": 10, "objective": 0.5}, "2030": {"years": 10}}})
    card = frame(frames, "tool_pending_confirmation")["edit_card"]
    assert [(r["label"], r["old"], r["new"], r["unit"]) for r in card["rows"]] == [
        ("2030 Years", 1.0, 10.0, "yr"), ("2040 Years", 1.0, 10.0, "yr"), ("2040 Objective", 1.0, 0.5, "")]
    assert frame(frames, "tool_result")["result"]["_edit_receipt"]["outcome"] == "verified"
    df = PyPSAService.get_network().investment_period_weightings
    assert (df.at[2030, "years"], df.at[2040, "objective"]) == (10.0, 0.5)


@pytest.mark.parametrize("updates", [{"2099": {"years": 5}}, {"2030": {"years": -1}}, {"2030": {"years": "x"}}])
def test_weightings_refuse_what_the_route_refuses(periods, updates):
    # The card runs the PATCH handler on a copy (GK B C8): the route and the card refuse the same values.
    before = PyPSAService.get_network().investment_period_weightings.copy()
    frames = _card(periods, "set_investment_period_weightings", {"updates": updates})
    assert "tool_pending_confirmation" not in [e for e, _ in frames]
    assert frame(frames, "tool_error")["error_kind"] == "edit_refused"
    with pytest.raises(HTTPException) as failure:
        update_investment_period_weightings({"updates": updates})
    assert failure.value.status_code == 400
    pd.testing.assert_frame_equal(PyPSAService.get_network().investment_period_weightings, before)
