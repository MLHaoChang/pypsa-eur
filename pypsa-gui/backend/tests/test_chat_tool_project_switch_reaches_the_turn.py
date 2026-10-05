"""
A tool that switches the active project must switch it for the REST of the turn.

Two layers of context copying stood between a tool's switch and the turn:

1. `chat_service._dispatch_real_tool_call` runs every tool as
   `contextvars.copy_context().run(...)` on an executor — it has to, because a
   pool worker inherits no contextvars and the tools read the acting user from
   one. `PyPSAService._publish_active` publishes through a ContextVar
   (`_request_ctx`), so the switch landed on that copy. The first fix,
   `adopt_active_from`, copied it back after the tool returned.

2. That fix was itself inside a copy. `routers/chat.py` streams the turn as a
   SYNC generator, and Starlette's `iterate_in_threadpool` drives every
   `next()` in a FRESH copy of the request task's context — the router's own
   comments say so twice. So the adopt was discarded at the next `yield`, and
   the rest of the turn was back on the old project. Its tests drove the
   generator in ONE context, the only case in which it works, so they passed.
   Measured with per-item copies, after `activate_project("B")` from A, the
   next step of the turn saw: A.

   An async handler (every import) adds a third copy, the `asyncio.run` task,
   whose publish the adopt could never see even in a single context.

Fixed with a turn CELL (`PyPSAService._turn_cell`): a one-element list bound
from the event-loop task, which every per-item copy and every copy below it
inherits by reference, so a write into it survives them all.

The tests below therefore drive the dispatcher through a real
`StreamingResponse` over a sync generator — the production shape — not through
`list(generator)`.

A switch made by ANY tool now reaches the turn (it used to be scoped to
`PROJECT_REBINDING_TOOLS`). A copy only the tool writes to cannot carry another
tab's switch, so following it is never wrong; whether the turn may CONTINUE on
the new identity is still the mid-turn guard's call, and it stops a turn whose
tool changed identity without being on the rebinding list.
"""
from __future__ import annotations

import contextvars

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService


@pytest.fixture(autouse=True)
def _no_confirmation_cards(monkeypatch):
    """The confirmation card is not under test, and an unanswered one for a
    destructive tool (`import_network_nc`) blocks for the full TTL and then
    never runs the tool at all."""
    monkeypatch.setattr(chat_service, "AUTO_APPROVE_TIERS",
                        frozenset({"write", "destructive"}))


def _ctx(name: str):
    ctx = PyPSAService.build_context()
    ctx.loaded_project = name
    return ctx


def _switching_tool(target):
    def _tool(**_kwargs):
        PyPSAService._publish_active(target)
        return {"activated": target.loaded_project}
    return _tool


def _turn_app(start_ctx, tool_name: str, *, bind_cell: bool) -> FastAPI:
    """The production shape: an async endpoint binds the request (as
    `deps.bind_active_project` does) and, like `routers/chat.chat_stream`, the
    turn cell; the body is a SYNC generator, so Starlette iterates it with
    `iterate_in_threadpool` — one fresh context copy per yielded item. The
    last item reports what the NEXT step of the turn sees."""
    app = FastAPI()

    @app.get("/turn")
    async def turn():
        PyPSAService.bind_request_context(start_ctx)
        if bind_cell:
            PyPSAService.bind_turn_cell()

        def _gen():
            session = chat_service.ChatSession()
            for _ev, _p in chat_service._dispatch_real_tool_call(
                session, {"id": "tu-1", "name": tool_name, "input": {}}, [],
            ):
                yield b"."
            yield (PyPSAService.get_active_context().loaded_project or "<unbound>").encode()

        return StreamingResponse(_gen(), media_type="text/plain")

    return app


def _next_step_sees(app: FastAPI) -> str:
    with TestClient(app) as c:
        return c.get("/turn").text.lstrip(".")


def test_a_switch_reaches_the_rest_of_a_streamed_turn(monkeypatch):
    a, b = _ctx("A"), _ctx("B")
    monkeypatch.setitem(chat_tools.DISPATCHERS, "activate_project", _switching_tool(b))

    seen = _next_step_sees(_turn_app(a, "activate_project", bind_cell=True))

    assert seen == "B", (
        "the tool switched to B and the next step of the streamed turn saw "
        f"{seen!r}: a ContextVar.set() inside the generator dies with the "
        "per-item copy iterate_in_threadpool made for it"
    )


def test_without_the_cell_the_switch_is_lost(monkeypatch):
    """The mechanism, pinned. If this ever starts passing with B, Starlette's
    iteration model changed and the cell may no longer be what carries it."""
    a, b = _ctx("A"), _ctx("B")
    monkeypatch.setitem(chat_tools.DISPATCHERS, "activate_project", _switching_tool(b))

    assert _next_step_sees(_turn_app(a, "activate_project", bind_cell=False)) == "A"


def test_a_publish_from_an_async_handler_reaches_the_turn(monkeypatch):
    """An import runs its route under `asyncio.run` — one copy below the tool's
    own — where `adopt_active_from` could not see it at all."""
    a, b = _ctx("A"), _ctx("B")

    def _async_switch(**_kwargs):
        async def _handler():
            PyPSAService._publish_active(b)
            return {"ok": True}
        return chat_tools._sync(_handler())

    monkeypatch.setitem(chat_tools.DISPATCHERS, "import_network_nc", _async_switch)

    assert _next_step_sees(_turn_app(a, "import_network_nc", bind_cell=True)) == "B"


def test_a_tool_that_does_not_switch_changes_nothing(monkeypatch):
    """The control: following must be a no-op when the tool left the binding
    alone, or every `activate_project` on the CURRENT project would churn it."""
    a = _ctx("A")
    monkeypatch.setitem(chat_tools.DISPATCHERS, "activate_project",
                        lambda **_k: {"activated": "A"})

    assert _next_step_sees(_turn_app(a, "activate_project", bind_cell=True)) == "A"


def test_the_chat_stream_route_binds_the_cell(client, monkeypatch):
    """Everything above depends on `routers/chat.py` binding the cell from
    its event-loop task; a refactor that drops the call would leave every
    test above green and production broken again."""
    calls: list[object] = []
    real = PyPSAService.bind_turn_cell

    def _spy():
        calls.append(PyPSAService.get_request_context())
        return real()

    monkeypatch.setattr(PyPSAService, "bind_turn_cell", staticmethod(_spy))
    chat_service._reset_sessions_for_tests()
    r = client.post(
        "/api/chat/stream",
        json={"session_id": "sess-cell-1", "script": [{"type": "session_done"}]},
    )
    assert r.status_code == 200
    assert calls, "chat_stream never bound the turn cell"
    assert calls[0] is not None, (
        "the cell was bound with no request context in scope — it would be a "
        "no-op, and the turn would follow nothing"
    )


# ── Without a cell (a direct `run_turn`, a test) the adopt still carries it ──


@pytest.fixture
def bound_to_a():
    a, b = _ctx("A"), _ctx("B")
    token = PyPSAService.bind_request_context(a)
    try:
        yield a, b
    finally:
        PyPSAService.reset_request_context(token)


def _dispatch(tool_name: str, args: dict):
    session = chat_service.ChatSession()
    collected: list[dict] = []
    frames = list(chat_service._dispatch_real_tool_call(
        session, {"id": "tu-1", "name": tool_name, "input": args}, collected,
    ))
    return frames, collected


def test_the_adopt_carries_a_switch_for_a_direct_caller(bound_to_a, monkeypatch):
    _a, b = bound_to_a
    monkeypatch.setitem(chat_tools.DISPATCHERS, "activate_project", _switching_tool(b))

    _dispatch("activate_project", {"project_id": "B"})

    assert PyPSAService.get_active_context().loaded_project == "B"


def test_the_adopt_follows_any_tool_not_only_the_rebinding_list(bound_to_a, monkeypatch):
    """It used to be scoped to `PROJECT_REBINDING_TOOLS`. The cell follows every
    tool in production, so the direct path must too, or the two disagree."""
    _a, b = bound_to_a
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", _switching_tool(b))

    _dispatch("list_components", {"component_class": "Bus"})

    assert PyPSAService.get_active_context().loaded_project == "B"


def test_no_cell_is_bound_outside_a_request():
    """Local mode has no request context; the process foreground is shared
    already, and a cell there would only shadow it."""
    def _probe():
        assert PyPSAService.bind_turn_cell() is None
        return PyPSAService._turn_cell.get()

    assert contextvars.copy_context().run(_probe) is None
