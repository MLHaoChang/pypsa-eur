# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02: `set_energization_period`, chat's way to Guided section 3's
per-period connection drawer. It goes through `PUT /api/projects/{name}/flow/energization`
(the same handler, its validation, refusals and write rules); a viewer is refused
before dispatch. The route itself is pinned in `test_energization_route.py`."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness import catalogue
from services import chat_tools
from tests import test_viewer_role_gate as gate
from tests.test_energization_route import AS_BUILT, LINK, _create, _row

TOOL = "set_energization_period"

as_user = gate.as_user
baseline = gate.baseline
helpers_state = gate.helpers_state
world = gate.world


def _call(**args):
    """The dispatcher, in-process, in the project's own context: the context a
    chat turn binds for the open project (the HTTP request's is gone)."""
    from services.pypsa_service import PyPSAService

    ctx = next(c for c in list(PyPSAService._contexts.values())
               if c.loaded_project == args["name"])
    binding = PyPSAService.bind_request_context(ctx)
    try:
        return chat_tools.DISPATCHERS[TOOL](**args)
    finally:
        PyPSAService.reset_request_context(binding)


def test_the_tool_is_a_write_on_the_energization_route():
    assert catalogue.safety_tier_for(TOOL) == "write"
    assert catalogue.TOOL_ROUTES[TOOL] == [("PUT", "/api/projects/{name}/flow/energization")]
    # The handler takes its own undo step and edit-counter bump: the loop must
    # not add a second of either.
    assert not catalogue.tool_edits_network(TOOL)
    assert TOOL not in chat_tools.UNDO_CAPTURED_TOOLS


def test_chat_sets_one_period_as_the_drawer_does(client, tmp_projects_dir):
    from services import change_log_service

    name = _create(client, "eh_datacenter_staged", "staged02c")
    info = client.get("/api/network/undo/info").json()
    out = _call(name=name, link=LINK, period=2030, mw=30)
    assert out["message"] == f"The energized capacity of {LINK} for 2030 is now 30 MW (was 55 MW)."
    assert out["period"] == "2030" and out["note"] is None and "series" not in out
    after = client.get("/api/network/undo/info").json()
    assert after["depth"] == info["depth"] + 1  # one undo step
    assert after["network_revision"] > info["network_revision"]
    assert after["unsaved"] is True
    assert change_log_service.get_all()[0]["description"] == out["message"]
    assert _row(client, name)["energization"][0]["by_period"] == {**AS_BUILT, "2030": 30.0}


def test_chat_names_the_rating_it_does_not_raise(client, tmp_projects_dir):
    name = _create(client, "eh_datacenter_staged", "staged02c_rating")
    out = _call(name=name, link=LINK, period="2035", mw=500)
    assert out["note"] and "doesn't raise the link's capacity" in out["note"]
    assert out["message"].endswith(out["note"])


@pytest.mark.parametrize(
    ("args", "kind"),
    [
        ({"link": LINK, "period": 2030}, "energization_invalid_request"),  # no mw
        ({"link": "", "period": 2030, "mw": 10}, "energization_invalid_request"),
        ({"link": LINK, "period": 2030, "mw": True}, "energization_invalid_mw"),
        ({"link": LINK, "period": 2030, "mw": float("nan")}, "energization_invalid_mw"),
        ({"link": LINK, "period": 2030, "mw": 0}, "energization_invalid_mw"),
        ({"link": "no_such_link", "period": 2030, "mw": 10}, "energization_no_schedule"),
        ({"link": LINK, "period": 2099, "mw": 10}, "energization_unknown_period"),
    ],
)
def test_each_refusal_is_the_routes_typed_422_and_writes_nothing(
    client, tmp_projects_dir, args, kind
):
    name = _create(client, "eh_datacenter_staged", f"staged02c_{kind}")
    info = client.get("/api/network/undo/info").json()
    with pytest.raises(HTTPException) as exc:
        _call(name=name, **args)
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == kind
    assert client.get("/api/network/undo/info").json() == info
    assert _row(client, name)["energization"][0]["by_period"] == AS_BUILT


def test_a_viewer_is_refused_before_dispatch_and_nothing_changes(world, as_user):
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession
    from services.project_write_gate import KIND
    from services.pypsa_service import PyPSAService
    from tests import lc43_gates

    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    binding = PyPSAService.bind_request_context(world["ctx"])
    chat_tools.set_acting_user(world["viewer"])
    before = lc43_gates.state(world["path"])
    try:
        frames = list(dispatch_tool_call(
            ChatSession(),
            {"id": "viewer-energization", "name": TOOL,
             "input": {"name": "Tool Baseline", "link": LINK, "period": "all", "mw": 5}},
            [],
        ))
    finally:
        PyPSAService.reset_request_context(binding)
    errors = [d for e, d in frames if e == "tool_error"]
    assert errors and errors[0].get("error_kind") == KIND, frames
    assert not any(e in {"tool_pending_confirmation", "tool_running"} for e, _ in frames)
    assert lc43_gates.state(world["path"]) == before
