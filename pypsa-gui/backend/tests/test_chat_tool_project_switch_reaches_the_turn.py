"""
A tool that switches the active project must switch it for the REST of the turn.

`chat_service._dispatch_real_tool_call` runs every tool as
`contextvars.copy_context().run(...)` on an executor — it has to, because a pool
worker inherits no contextvars and the tools read the acting user from one. But a
`ContextVar.set()` performed inside `Context.run()` mutates that copy and dies with
it. `PyPSAService._publish_active` publishes the active project through exactly such
a var (`_request_ctx`) whenever a request is in flight — i.e. always, in server mode.

So `activate_project("B")` succeeded, moved the session's DB pointer, and changed
nothing for the thread running the turn. Measured, before the fix:

    bound  : A
    inside : B   <- the tool believes it switched
    after  : A   <- what the rest of the turn sees
    next   : A   <- the next tool in the same turn

Consequences, both live in server mode and invisible in local mode (no session
cookie, so the process-global `_active` is used and nothing is lost — which is why
the suite never saw it):

  * every later tool in the turn ran against the OLD project — the documented flow
    in `PROJECT_REBINDING_TOOLS`' own comment, "activate_project -> update_component
    against the newly-activated scenario", edited the wrong project;
  * `run_turn`'s rebinding check re-reads the same stale view, so it emitted no
    `project_rebound` frame and never refreshed `turn_project_holder` — the frontend
    kept its old `currentProject`, and the next autosave's `expect=` 409'd: the
    2026-06-08 incident that frame exists to prevent.

The adopt is SCOPED to `PROJECT_REBINDING_TOOLS`. `import_*` and anything else
reaching `reset_network` also publishes, but publishes an UNBOUND context and is not
whitelisted; adopting it would make `_project_switched` fire and refuse the rest of
the turn — the second test pins that it is left alone.

The tools are faked under real names rather than driven for real: the defect is in
the dispatcher, and a real `activate_project` needs a database, two projects and a
resident registry entry that would test everything except this.
"""
from __future__ import annotations

import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService


@pytest.fixture
def bound_to_a():
    a = PyPSAService.build_context()
    a.loaded_project = "A"
    b = PyPSAService.build_context()
    b.loaded_project = "B"
    token = PyPSAService.bind_request_context(a)
    try:
        yield a, b
    finally:
        PyPSAService.reset_request_context(token)


def _dispatch(tool_name: str, args: dict):
    """One tool_use through the REAL dispatcher, as a chat turn runs it."""
    session = chat_service.ChatSession()
    collected: list[dict] = []
    frames = list(chat_service._dispatch_real_tool_call(
        session, {"id": "tu-1", "name": tool_name, "input": args}, collected,
    ))
    return frames, collected


def _switching_tool(target):
    def _tool(**_kwargs):
        PyPSAService._publish_active(target)
        return {"activated": target.loaded_project}
    return _tool


def test_a_rebinding_tool_switch_reaches_the_rest_of_the_turn(bound_to_a, monkeypatch):
    a, b = bound_to_a
    monkeypatch.setitem(chat_tools.DISPATCHERS, "activate_project", _switching_tool(b))

    assert PyPSAService.get_active_context().loaded_project == "A"
    _dispatch("activate_project", {"project_id": "B"})

    assert PyPSAService.get_active_context().loaded_project == "B", (
        "the tool switched to B inside its own context copy and the turn never "
        "saw it — every later tool in this turn would edit project A"
    )


def test_a_non_rebinding_tool_does_not_move_the_turn(bound_to_a, monkeypatch):
    """
    The scoping, pinned. A tool off the whitelist that publishes (as `import_*`
    does through `reset_network`) must NOT be adopted: doing so would make
    `_project_switched` fire and refuse every remaining tool in the turn with
    `project_switched_mid_turn`.
    """
    a, b = bound_to_a
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", _switching_tool(b))

    _dispatch("list_components", {"component_class": "Bus"})

    assert PyPSAService.get_active_context().loaded_project == "A"


def test_a_rebinding_tool_that_does_not_switch_changes_nothing(bound_to_a, monkeypatch):
    """The control: adopting must be a no-op when the tool left the binding
    alone, or every `activate_project` on the CURRENT project would churn it."""
    a, _b = bound_to_a
    monkeypatch.setitem(chat_tools.DISPATCHERS, "activate_project",
                        lambda **_k: {"activated": "A"})

    _dispatch("activate_project", {"project_id": "A"})

    assert PyPSAService.get_active_context() is a
