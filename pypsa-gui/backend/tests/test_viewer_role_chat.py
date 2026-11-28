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
import re
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
from tests import test_viewer_role_gate as gate
from tests.test_workflow_provider_parity import _anthropic_stream, _compatible_stream

# The viewer-role gate suite's fixtures, shared as module attributes.
as_user = gate.as_user
baseline = gate.baseline
helpers_state = gate.helpers_state
world = gate.world

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
            assert restrictions.role_admits(snapshot, tier) == _admitted(
                snapshot, tier
            ), name
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
    assert (
        "This project is view only for you, so I can only look things up" in _VIEW_ONLY
    )


def _system_text(body, anthropic) -> str:
    if anthropic:
        system = body.get("system", "")
        if isinstance(system, list):
            return "".join(block.get("text", "") for block in system)
        return system
    first = body["messages"][0]
    return first["content"] if first.get("role") == "system" else ""


def _turn(world, as_user, who, wire, first_call=None):
    """One turn as `who`; returns every request body the provider got.

    With `first_call` (name, args) the model's first answer calls that tool,
    so the second body shows the offer rebuilt mid-turn.
    """
    anthropic = wire == "anthropic"
    base_url = "https://api.anthropic.com" if anthropic else "https://api.openai.com/v1"
    model = "claude-sonnet-5" if anthropic else "gpt-6-luna"
    member = as_user(world[who])
    assert member.post(f"/api/projects/{world['project']}/activate").status_code == 200
    http_module = httpx
    if anthropic:
        import anthropic as sdk

        module = next(
            cls.__module__.split(".")[0]
            for cls in sdk.DefaultHttpxClient.__mro__
            if cls.__module__.split(".")[0] in ("httpx", "httpx2")
        )
        http_module = import_module(module)
    profile = llm_config.LLMProfile(
        id="viewer",
        label="Viewer test",
        preset="custom",
        wire="anthropic" if anthropic else "openai",
        base_url=base_url,
        model=model,
        tools=True,
        vision=False,
        auth="bearer",
        fallback_model=None,
        max_output_tokens=256,
    )
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, profile.wire
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        name, args = first_call if (first_call and len(sent) == 1) else (None, {})
        stream = (
            _anthropic_stream(model, f"c{len(sent)}", name, args)
            if anthropic
            else _compatible_stream(f"c{len(sent)}", name, args)
        )
        return http_module.Response(
            200, headers={"content-type": "text/event-stream"}, content=stream
        )

    binding = PyPSAService.bind_request_context(world["ctx"])
    chat_tools.set_acting_user(world[who])
    try:
        with http_module.Client(transport=http_module.MockTransport(respond)) as http:
            if anthropic:
                provider = AnthropicProvider(
                    sdk.Anthropic(
                        api_key="synthetic-key",
                        base_url=base_url,
                        http_client=http,
                        max_retries=0,
                    )
                )
            else:
                provider = OpenAICompatProvider(
                    base_url,
                    api_key="synthetic-key",
                    http_client=http,
                    token_param="max_tokens",
                )
            frames = list(
                chat_service.run_turn(
                    session,
                    "add a generator, then show me the results",
                    provider=provider,
                )
            )
    finally:
        PyPSAService.reset_request_context(binding)
    assert frames[-1][0] == "turn_done", frames[-3:]
    return [
        (
            {
                t["name"] if anthropic else t["function"]["name"]
                for t in body.get("tools", [])
            },
            _system_text(body, anthropic),
        )
        for body in sent
    ]


def _writes(offered) -> set:
    return {n for n in offered if catalogue.safety_tier_for(n) != "read"}


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_a_viewer_is_offered_only_read_tools_and_told_why(world, as_user, wire):
    [(offered, system)] = _turn(world, as_user, "viewer", wire)
    assert offered, "a viewer still gets the read tools"
    assert _writes(offered) == set()
    assert "cancel_job" not in offered
    assert system.endswith("\n" + _VIEW_ONLY)


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_an_editor_keeps_the_write_tools_and_no_view_only_line(world, as_user, wire):
    [(offered, system)] = _turn(world, as_user, "editor", wire)
    assert _writes(offered)
    assert _VIEW_ONLY not in system


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_the_view_only_line_is_appended_after_the_editors_prompt(world, as_user, wire):
    [(_, editor)] = _turn(world, as_user, "editor", wire)
    [(_, viewer)] = _turn(world, as_user, "viewer", wire)
    # Each session gets its own short confirmation-token salt; nothing else differs.
    salt = re.compile(r"<verb>:[0-9a-f]{6}")
    assert (
        salt.sub("<verb>:", viewer) == salt.sub("<verb>:", editor) + "\n" + _VIEW_ONLY
    )


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "call",
    [
        ("find_capabilities", {"goal": "edit a generator"}),
        ("use_toolset", {"domain": "all"}),
    ],
)
def test_the_offer_rebuilt_mid_turn_stays_read_only_for_a_viewer(
    world, as_user, wire, call
):
    bodies = _turn(world, as_user, "viewer", wire, first_call=call)
    assert len(bodies) == 2, "the tool result went back to the model"
    for offered, _ in bodies:
        assert offered and _writes(offered) == set()


# ── LC-D22.viewer_role: the copilot's edit, undo and restore for a viewer ────


def test_a_viewers_copilot_edit_undo_and_restore_are_refused_and_change_nothing(
    world, as_user
):
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
        (
            "update_component",
            {
                "component_class": "Generator",
                "name": "G1",
                "updates": {"marginal_cost": 99},
            },
        ),
        ("undo_last", {}),
        (
            "restore_project_snapshot",
            {"name": project_name, "snapshot_id": checkpoint.id},
        ),
    ]
    try:
        for index, (name, args) in enumerate(calls):
            frames = list(
                dispatch_tool_call(
                    ChatSession(),
                    {"id": f"viewer-{index}", "name": name, "input": args},
                    [],
                )
            )
            errors = [d for e, d in frames if e == "tool_error"]
            assert errors and errors[0].get("error_kind") == KIND, (name, frames)
            assert not any(
                e in {"tool_pending_confirmation", "tool_running"} for e, _ in frames
            )
    finally:
        PyPSAService.reset_request_context(binding)
    assert lc43_gates.state(world["path"]) == before


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_the_view_only_line_comes_after_the_assurance_block(
    world, as_user, wire, monkeypatch
):
    from harness import assurance

    monkeypatch.setattr(assurance, "strict_configured", lambda: True)
    [(_, viewer)] = _turn(world, as_user, "viewer", wire)
    head, _, tail = viewer.rpartition("\n")
    assert tail == _VIEW_ONLY
    assert _VIEW_ONLY not in head


def test_the_chat_refusal_never_names_the_owner():
    """The refusal goes to the model provider: a snapshot that carried an owner email still gets the unnamed sentence."""
    from services.project_write_gate import MESSAGE

    with pytest.raises(HTTPException) as caught:
        restrictions.require_allowed({**_VIEW, "owner_email": "o@x.com"}, "write")
    assert caught.value.detail["message"] == MESSAGE
    assert "@" not in caught.value.detail["message"]
