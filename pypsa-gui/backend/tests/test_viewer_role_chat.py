# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Project viewer role, PR 2a: what the chat offers a viewer and tells the model.

A viewer is offered exactly the tools dispatch admits for them (one predicate,
`restrictions.role_admits`, GK B3 V5), on the Anthropic and the
OpenAI-compatible wire, and the system prompt carries the view-only fragment
(V6); an editor gets neither change.
"""

from __future__ import annotations

import json
from importlib import import_module

import httpx
import pytest
from fastapi import HTTPException

from harness import catalogue, restrictions
from harness.compose import _VIEW_ONLY
from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_service, chat_tools, llm_config
from services.pypsa_service import PyPSAService
from tests.test_viewer_role_gate import (  # noqa: F401  (fixtures)
    as_user,
    baseline,
    helpers_state,
    world,
)
from tests.test_workflow_provider_parity import _anthropic_stream, _compatible_stream

_VIEW = {"mode": "standard", "revision": "r", "project_role": "view"}
_EDIT = {**_VIEW, "project_role": "edit"}


def _admitted(snapshot, tier) -> bool:
    try:
        restrictions.require_allowed(snapshot, tier)
    except HTTPException:
        return False
    return True


def test_the_offer_rule_is_the_dispatch_rule_over_the_whole_catalogue():
    names = [t["name"] for t in catalogue.TOOLS]
    assert names
    for name in names:
        tier = catalogue.safety_tier_for(name)
        for snapshot in (_VIEW, _EDIT, None):
            assert restrictions.role_admits(snapshot, tier) == _admitted(snapshot, tier), name
    # An always-offered job control that stops work is a write: not for a viewer.
    assert catalogue.safety_tier_for("cancel_job") != "read"
    assert not restrictions.role_admits(_VIEW, catalogue.safety_tier_for("cancel_job"))


def test_view_only_reads_the_role_not_the_saved_rule():
    assert restrictions.view_only(_VIEW)
    assert restrictions.view_only({**_VIEW, "project_role": None})
    assert not restrictions.view_only(_EDIT)
    assert not restrictions.view_only({**_EDIT, "mode": "read_only"})
    assert not restrictions.view_only(None)


def test_the_view_only_fragment_names_no_tool():
    words = set(_VIEW_ONLY.replace(",", " ").replace(".", " ").split())
    assert not words & {t["name"] for t in catalogue.TOOLS}
    assert "This project is view only for you, so I can only look things up" in _VIEW_ONLY


def _turn(world, as_user, who, wire):
    """One text-only turn as `who`; returns the request body the provider got."""
    anthropic = wire == "anthropic"
    base_url = "https://api.anthropic.com" if anthropic else "https://api.openai.com/v1"
    model = "claude-sonnet-5" if anthropic else "gpt-6-luna"
    member = as_user(world[who])
    assert member.post(f"/api/projects/{world['project']}/activate").status_code == 200
    http_module = httpx
    if anthropic:
        import anthropic as sdk

        module = next(cls.__module__.split(".")[0] for cls in sdk.DefaultHttpxClient.__mro__
                      if cls.__module__.split(".")[0] in ("httpx", "httpx2"))
        http_module = import_module(module)
    profile = llm_config.LLMProfile(id="viewer", label="Viewer test", preset="custom",
        wire="anthropic" if anthropic else "openai", base_url=base_url, model=model,
        tools=True, vision=False, auth="bearer", fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, profile.wire
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        stream = (_anthropic_stream(model, "c0", None, {}) if anthropic
                  else _compatible_stream("c0", None, {}))
        return http_module.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    binding = PyPSAService.bind_request_context(world["ctx"])
    chat_tools.set_acting_user(world[who])
    try:
        with http_module.Client(transport=http_module.MockTransport(respond)) as http:
            if anthropic:
                provider = AnthropicProvider(sdk.Anthropic(api_key="synthetic-key", base_url=base_url,
                                                           http_client=http, max_retries=0))
            else:
                provider = OpenAICompatProvider(base_url, api_key="synthetic-key", http_client=http,
                                                token_param="max_tokens")
            frames = list(chat_service.run_turn(
                session, "add a generator, then show me the results", provider=provider))
    finally:
        PyPSAService.reset_request_context(binding)
    assert frames[-1][0] == "turn_done", frames[-3:]
    body = sent[0]
    offered = {t["name"] if anthropic else t["function"]["name"] for t in body.get("tools", [])}
    system = json.dumps(body.get("system", "")) + json.dumps(body.get("messages", [])[:1])
    return offered, system


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_a_viewer_is_offered_only_read_tools_and_told_why(world, as_user, wire):
    offered, system = _turn(world, as_user, "viewer", wire)
    assert offered, "a viewer still gets the read tools"
    assert {n for n in offered if catalogue.safety_tier_for(n) != "read"} == set()
    assert "cancel_job" not in offered
    assert "This project is view only for this user." in system


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_an_editor_keeps_the_write_tools_and_no_view_only_line(world, as_user, wire):
    offered, system = _turn(world, as_user, "editor", wire)
    assert {n for n in offered if catalogue.safety_tier_for(n) != "read"}
    assert "This project is view only for this user." not in system


# ── LC-D22.viewer_role: the copilot's edit, undo and restore for a viewer ────


def test_a_viewers_copilot_edit_undo_and_restore_are_refused_and_change_nothing(world, as_user):
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession
    from services.project_write_gate import KIND
    from tests import lc43_gates

    project_name = "Tool Baseline"
    # The project's own checkpoint, made by its owner before the viewer arrives.
    checkpoint = chat_tools.create_project_snapshot(project_name, "S0")
    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    binding = PyPSAService.bind_request_context(world["ctx"])
    chat_tools.set_acting_user(world["viewer"])
    before = lc43_gates.state(world["path"])
    calls = [
        ("update_component", {"component_class": "Generator", "name": "G1",
                              "updates": {"marginal_cost": 99}}),
        ("undo_last", {}),
        ("restore_project_snapshot", {"name": project_name, "snapshot_id": checkpoint.id}),
    ]
    try:
        for index, (name, args) in enumerate(calls):
            frames = list(dispatch_tool_call(
                ChatSession(), {"id": f"viewer-{index}", "name": name, "input": args}, []))
            errors = [d for e, d in frames if e == "tool_error"]
            assert errors and errors[0].get("error_kind") == KIND, (name, frames)
            assert not any(e in {"tool_pending_confirmation", "tool_running"} for e, _ in frames)
    finally:
        PyPSAService.reset_request_context(binding)
    assert lc43_gates.state(world["path"]) == before
