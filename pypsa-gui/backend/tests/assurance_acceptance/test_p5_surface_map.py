"""Surface map (AA-03 S5): every emitted event and chat route is mapped.

The surfaces are enumerated from the code itself, not from the map: every SSE
event name the harness emits (by AST), and every routers/chat.py route. A new
event or route without a row fails here, and so does a row whose surface no
longer exists. In checked mode an unmapped event is dropped (fail closed); an
ordinary turn keeps today's pass-through.
"""

from __future__ import annotations

import ast
import pathlib

from harness import assurance
from harness.assurance_coverage import (
    ADAPTERS,
    FRAME_PROJECTIONS,
    NO_ASSISTANT_TEXT,
    OUTPUT_CHANNELS,
    ROUTE_SURFACES,
)
from tests.assurance_acceptance.support import parse_sse, text_turn, tool_turn

BACKEND = pathlib.Path(__file__).resolve().parents[2]
# The modules that build (event, payload) frames for the chat stream.
FRAME_MODULES = (
    "harness/loop.py",
    "harness/confirm.py",
    "harness/guards.py",
    "harness/stub.py",
    "harness/budget.py",
)
# Yields of a frame held in a variable: relays of frames built above. Exact,
# so a new site has to be read and added here.
RELAY_YIELDS = {
    ("harness/loop.py", "run_turn", "(event, payload)"),
    ("harness/loop.py", "_run_turn_body", "budget_block"),
    ("harness/loop.py", "_run_turn_body", "_frame"),
    ("harness/guards.py", "observed", "frame"),
}
PROJECTIONS = {
    "approved_answer",
    "projected",
    "software_progress",
    "software_state",
    "dropped",
}


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _emitted_events():
    """("event", payload) tuples yielded, returned or listed by a frame module,
    plus literal `sse_frame("event", ...)` calls in the chat router."""
    names = set()
    for path in FRAME_MODULES:
        tree = ast.parse((BACKEND / path).read_text())
        parents = _parents(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Tuple)
                and len(node.elts) == 2
                and isinstance(node.elts[0], ast.Constant)
                and isinstance(node.elts[0].value, str)
                and isinstance(parents.get(node), (ast.Yield, ast.Return, ast.List))
            ):
                names.add(node.elts[0].value)
    tree = ast.parse((BACKEND / "routers/chat.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "sse_frame"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


def _relay_yields():
    sites = set()
    for path in FRAME_MODULES:
        tree = ast.parse((BACKEND / path).read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Yield)
                    and node.value is not None
                    and not (
                        isinstance(node.value, ast.Tuple)
                        and node.value.elts
                        and isinstance(node.value.elts[0], ast.Constant)
                    )
                ):
                    sites.add((path, fn.name, ast.unparse(node.value)))
    return sites


def _chat_routes():
    tree = ast.parse((BACKEND / "routers/chat.py").read_text())
    routes = set()
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in fn.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and isinstance(deco.func.value, ast.Name)
                and deco.func.value.id == "router"
            ):
                routes.add(f"{deco.func.attr.upper()} {deco.args[0].value}")
    return routes


# ── events ───────────────────────────────────────────────────────────────


def test_every_emitted_event_is_mapped():
    emitted = _emitted_events()
    assert len(emitted) >= 21
    assert sorted(emitted - set(FRAME_PROJECTIONS)) == []


def test_every_mapped_event_is_still_emitted():
    assert sorted(set(FRAME_PROJECTIONS) - _emitted_events()) == []


def test_every_projection_is_a_known_kind():
    assert set(FRAME_PROJECTIONS.values()) <= PROJECTIONS


def test_relay_yield_sites_are_exactly_the_known_ones():
    assert _relay_yields() == RELAY_YIELDS


def test_project_frame_drops_an_unmapped_event():
    turn = object.__new__(assurance.Turn)
    assert turn.project_frame("invented_event", {"text": "unchecked"}) is None
    assert turn.project_frame("thinking", {"delta": "private"}) is None
    assert turn.project_frame("turn_done", {"x": 1}) == {"x": 1}


def test_an_unmapped_event_never_reaches_a_checked_client(
    baseline, harness, monkeypatch
):
    from harness import loop
    from tests.assurance_acceptance.support import observed_draft

    real = loop._run_turn_body

    def body(*args, **kwargs):
        yield "invented_event", {"text": "unchecked 999 MW"}
        yield from real(*args, **kwargs)

    monkeypatch.setattr(loop, "_run_turn_body", body)
    draft = observed_draft()
    frames = harness.run(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(draft),
            text_turn(draft),
        ]
    )
    assert "invented_event" not in {event for event, _ in frames}
    assert "unchecked 999 MW" not in str(frames)


def test_an_ordinary_turn_keeps_the_pass_through(client, monkeypatch):
    from harness import loop
    from harness.providers.fake import FakeProvider
    from services import chat_service

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    real = loop._run_turn_body

    def body(*args, **kwargs):
        yield "invented_event", {"text": "ordinary"}
        yield from real(*args, **kwargs)

    monkeypatch.setattr(loop, "_run_turn_body", body)
    provider = FakeProvider([text_turn("Hello.")])
    monkeypatch.setattr(
        chat_service,
        "_provider_for_profile",
        lambda profile, client=None: (provider, None),
    )
    response = client.post(
        "/api/chat/stream", json={"session_id": "ordinary-s5", "message": "Hi"}
    )
    frames = parse_sse(response.text)
    assert ("invented_event", {"text": "ordinary"}) in frames


# ── routes ───────────────────────────────────────────────────────────────


def test_every_chat_route_is_mapped():
    routes = _chat_routes()
    assert len(routes) >= 52
    assert sorted(routes - set(ROUTE_SURFACES)) == []


def test_every_mapped_route_still_exists():
    assert sorted(set(ROUTE_SURFACES) - _chat_routes()) == []


def test_every_route_names_a_declared_channel():
    for route, channel in ROUTE_SURFACES.items():
        assert channel == NO_ASSISTANT_TEXT or channel in OUTPUT_CHANNELS, route


def test_every_output_channel_names_a_declared_adapter():
    assert set(OUTPUT_CHANNELS.values()) <= set(ADAPTERS)


def test_the_inventory_check_refuses_a_route_on_an_unknown_channel(tmp_path):
    from tests.test_assistant_inventory import COVERAGE, PANELS, TABS, isolated_reader

    edit = (COVERAGE, '"POST /stream": "stream_tokens"', '"POST /stream": "raw_tokens"')
    result = isolated_reader(tmp_path, TABS, PANELS, edits=[edit])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "POST /stream=raw_tokens" in result.stdout
