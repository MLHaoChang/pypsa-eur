"""
Guided mode: every change goes through the confirmation card (G3; P25 gate
B1, adopted from the reviewer's probe
`qa25/probes/test_probe_write_no_card.py`).

In Expert, `write`-tier tools dispatch inline, as they always have
(`test_chat_e2e.py::test_cold_path_activate_renders_as_success` pins it). In
Guided — `ui_context.ui_mode == 'guided'`, the same allow-listed key the ui
block reads — `write` joins the destructive / execution tiers at the existing
card: `tool_pending_confirmation`, then nothing changes until the user
approves; on a denial nothing changes at all.

Several Guided writes in ONE response (decided at the P25 gate): each gets
its own card, one after the other — the dispatch loop is sequential and
blocks on each card, so the UI still shows one card at a time. They are not
refused by the M7 one-destructive-per-response pre-scan, whose tier set is
unchanged; a denial skips only that call.
"""
from __future__ import annotations

import threading
import time

import pypsa
import pytest

from routers import simulation as sim_router
from services import chat_service
from harness import session as harness_session
from services import chat_tools
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

GUIDED = {"ui_mode": "guided", "panel": "hubDesign", "guided_step": "site"}


def _bus_flag(col: str) -> bool:
    b = PyPSAService.get_network().buses
    return bool(b.at["B1", col]) if col in b.columns else False


def _voll() -> float:
    return float(sim_router._state["solver_config"].voll)


class _StressSpy:
    """put_stress_scenarios needs a saved project; the question here is only
    WHETHER it runs, so the dispatcher is replaced by a recorder."""
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, name: str, scenarios: list) -> dict:
        self.calls.append({"name": name, "scenarios": scenarios})
        return {"scenarios": scenarios}


# tool, args, read the state, the state it has once applied
CASES = [
    ("update_component",
     {"component_class": "Bus", "name": "B1", "attrs": {"eh_poc": True}},
     lambda spy: _bus_flag("eh_poc"), True),
    ("bulk_update_components",
     {"component_class": "Bus", "names": ["B1", "B2"], "updates": {"eh_critical": True}},
     lambda spy: _bus_flag("eh_critical"), True),
    ("update_solver_config", {"partial": {"voll": 4321.0}},
     lambda spy: _voll(), 4321.0),
    ("put_stress_scenarios", {"name": "P", "scenarios": []},
     lambda spy: len(spy.calls), 1),
]


@pytest.fixture
def world(tmp_projects_dir, install_network, monkeypatch):
    monkeypatch.setattr(harness_session, "CONFIRMATION_TTL_SECONDS", 5.0)
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    install_network(n, name=None)
    spy = _StressSpy()
    monkeypatch.setitem(chat_tools.DISPATCHERS, "put_stress_scenarios", spy)
    return spy


def _client(calls: list[tuple[str, dict]]) -> FakeAnthropicClient:
    t1 = ([_tool_use_event(f"tu-{i}", name, args) for i, (name, args) in enumerate(calls)],
          _FakeFinalMessage(content=[_tool_use_block(f"tu-{i}", name, args)
                                     for i, (name, args) in enumerate(calls)],
                            usage=_FakeUsage()))
    t2 = ([_text_event("done")], _FakeFinalMessage(content=[_text_block("done")],
                                                   usage=_FakeUsage()))
    return FakeAnthropicClient([t1, t2])


def _run(session, client, ui_context, decisions, *, observe=None):
    """Drive run_turn in a thread; answer each card with the next decision.
    `observe()` is called while each card is pending (the tool is blocked)."""
    streamed: list[tuple[str, dict]] = []
    seen_while_pending: list = []
    done = threading.Event()

    def go():
        for ev in chat_service.run_turn(session, "do it", client=client,
                                        ui_context=ui_context):
            streamed.append(ev)
        done.set()

    t = threading.Thread(target=go)
    t.start()
    for decision in decisions:
        deadline = time.monotonic() + 5.0
        token = None
        while time.monotonic() < deadline and token is None:
            with session._lock:
                if session.pending_confirmations:
                    token = next(iter(session.pending_confirmations))
            if token is None:
                time.sleep(0.02)
        assert token, "no confirmation card appeared"
        if observe:
            seen_while_pending.append(observe())
        session.record_decision(token, decision)
    assert done.wait(10.0), "turn did not finish"
    t.join()
    return streamed, seen_while_pending


def _names(events):
    return [e for e, _ in events]


@pytest.mark.parametrize("tool,args,read,applied", CASES, ids=[c[0] for c in CASES])
def test_guided_write_waits_for_the_card_then_applies(world, tool, args, read, applied):
    assert chat_service._safety_tier_for(tool) == "write"
    before = read(world)
    assert before != applied
    session = chat_service.ChatSession()
    events, pending_states = _run(session, _client([(tool, args)]), GUIDED, ["approve"],
                                  observe=lambda: read(world))
    names = _names(events)
    assert "tool_pending_confirmation" in names
    card = next(p for e, p in events if e == "tool_pending_confirmation")
    assert card["tool_name"] == tool and card["safety_tier"] == "write"
    # nothing changed while the card was waiting
    assert pending_states == [before]
    assert names.index("tool_pending_confirmation") < names.index("tool_running")
    assert "tool_result" in names
    assert read(world) == applied


@pytest.mark.parametrize("tool,args,read,applied", CASES, ids=[c[0] for c in CASES])
def test_guided_write_denied_changes_nothing(world, tool, args, read, applied):
    before = read(world)
    session = chat_service.ChatSession()
    events, _ = _run(session, _client([(tool, args)]), GUIDED, ["deny"])
    names = _names(events)
    assert "tool_pending_confirmation" in names
    assert "tool_running" not in names and "tool_result" not in names
    err = next(p for e, p in events if e == "tool_error")
    assert err["error_kind"] == "confirmation_denied"
    assert read(world) == before


@pytest.mark.parametrize("ctx", [None, {"panel": "hubDesign"},
                                 {"ui_mode": "expert", "panel": "hubDesign"},
                                 {"ui_mode": "GUIDED"}])
@pytest.mark.parametrize("tool,args,read,applied", CASES, ids=[c[0] for c in CASES])
def test_expert_write_still_applies_directly(world, tool, args, read, applied, ctx):
    session = chat_service.ChatSession()
    events = list(chat_service.run_turn(session, "do it", client=_client([(tool, args)]),
                                        ui_context=ctx))
    names = _names(events)
    assert "tool_pending_confirmation" not in names
    assert names == ["session_init", "tool_request", "tool_running", "tool_result",
                     "token", "turn_done"]
    assert read(world) == applied


def test_guided_read_tools_are_not_carded(world):
    session = chat_service.ChatSession()
    events = list(chat_service.run_turn(
        session, "look", client=_client([("get_solver_config", {})]), ui_context=GUIDED))
    assert "tool_pending_confirmation" not in _names(events)
    assert "tool_result" in _names(events)


def test_several_guided_writes_in_one_response_each_get_a_card_in_turn(world):
    calls = [
        ("update_component", {"component_class": "Bus", "name": "B1",
                              "attrs": {"eh_poc": True}}),
        ("update_component", {"component_class": "Bus", "name": "B2",
                              "attrs": {"eh_critical": True}}),
        ("update_solver_config", {"partial": {"voll": 4321.0}}),
    ]
    voll0 = _voll()
    session = chat_service.ChatSession()
    events, _ = _run(session, _client(calls), GUIDED, ["approve", "deny", "approve"])
    names = _names(events)
    # never refused as a parallel batch, never run silently
    assert not any(p.get("error_kind") == "parallel_destructive_not_allowed"
                   for e, p in events if e == "tool_error")
    cards = [p["tool_use_id"] for e, p in events if e == "tool_pending_confirmation"]
    assert cards == ["tu-0", "tu-1", "tu-2"]
    # one card at a time: each tool's outcome precedes the next card
    order = [(e, p.get("tool_use_id")) for e, p in events
             if e in ("tool_pending_confirmation", "tool_result", "tool_error")]
    assert order == [("tool_pending_confirmation", "tu-0"), ("tool_result", "tu-0"),
                     ("tool_pending_confirmation", "tu-1"), ("tool_error", "tu-1"),
                     ("tool_pending_confirmation", "tu-2"), ("tool_result", "tu-2")]
    buses = PyPSAService.get_network().buses
    assert bool(buses.at["B1", "eh_poc"]) is True
    assert "eh_critical" not in buses.columns or bool(buses.at["B2", "eh_critical"]) is False
    assert _voll() == 4321.0 != voll0
    assert "tool_running" in names


def test_m7_still_refuses_two_destructives_in_guided(world):
    calls = [("delete_component", {"component_class": "Bus", "name": "B1"}),
             ("delete_component", {"component_class": "Bus", "name": "B2"})]
    session = chat_service.ChatSession()
    events = list(chat_service.run_turn(session, "x", client=_client(calls),
                                        ui_context=GUIDED))
    kinds = [p.get("error_kind") for e, p in events if e == "tool_error"]
    assert kinds == ["parallel_destructive_not_allowed"] * 2
    assert "tool_pending_confirmation" not in _names(events)
    assert set(PyPSAService.get_network().buses.index) == {"B1", "B2"}
