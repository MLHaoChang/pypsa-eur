"""LC-06 S11, LC-D23: one journey on the Claude wire (owner decision LC32-OPEN-1).

The seam is ``run_turn`` with the real ``AnthropicProvider`` over a mocked HTTP
stream, so what the model is offered, what it asks for and what the tools do are
read from the request bodies and the network, not from the dispatcher's own
view. Parity with the OpenAI wire is out of scope here (``test_workflow_provider_parity.py``
keeps dispatch parity on four transports).

Journey on the baseline project (G1 20 MW), scripted as the model would speak:
1. ``use_toolset("results")``: the next request offers fewer tools, ``update_component``
   is not among them, and every one is also in the full offer;
2. the model asks for ``update_component`` anyway: refused as ``tool_not_offered``,
   the model is told so (``is_error``), and G1 is still 20 MW when the next request
   goes out. A capability that was not offered is never granted;
3. ``use_toolset("all")``: the full offer is back, identical to the first;
4. ``update_component`` G1 p_nom 35 raises a confirmation card for a write; approved,
   G1 is 35 MW and the model is sent the edit receipt (20 -> 35) as untrusted data;
   denied, G1 stays 20 MW and the model is told ``confirmation_denied``.
"""
# ruff: noqa: F811 -- shared public harness fixtures
import json
from importlib import import_module

import anthropic as sdk
import pytest

from harness.providers.anthropic import AnthropicProvider
from services import chat_service, llm_config
from services.pypsa_service import PyPSAService
from tests.test_workflow_provider_parity import _anthropic_stream
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

MODEL, BASE_URL = "claude-sonnet-5", "https://api.anthropic.com"
EDIT = {"component_class": "Generator", "name": "G1", "attrs": {"p_nom": 35}}
SCRIPT = [("use_toolset", {"domain": "results"}), ("update_component", EDIT),
          ("use_toolset", {"domain": "all"}), ("update_component", EDIT)]


def p_nom():
    return float(PyPSAService.get_network().generators.at["G1", "p_nom"])


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_the_claude_wire_journey_grants_only_what_was_offered(baseline, decision):
    http_module = import_module(next(cls.__module__.split(".")[0] for cls in sdk.DefaultHttpxClient.__mro__
                                     if cls.__module__.split(".")[0] in ("httpx", "httpx2")))
    profile = llm_config.LLMProfile(id="d23", label="D23", preset="custom", wire="anthropic",
        base_url=BASE_URL, model=MODEL, tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=MODEL)
    session.profile_id, session.bound_wire = profile.id, "anthropic"
    offers, results, g1_at_request = [], [], []

    def respond(request):
        body = json.loads(request.content)
        index = len(offers)
        offers.append({t["name"] for t in body["tools"]})
        g1_at_request.append(p_nom())
        if index:
            results.append(body["messages"][-1]["content"][0])
        name, args = SCRIPT[index] if index < len(SCRIPT) else (None, {})
        return http_module.Response(200, headers={"content-type": "text/event-stream"},
                                    content=_anthropic_stream(MODEL, f"call_{index}", name, args))

    with http_module.Client(transport=http_module.MockTransport(respond)) as http:
        provider = AnthropicProvider(sdk.Anthropic(api_key="synthetic-key", base_url=BASE_URL,
                                                   http_client=http, max_retries=0))
        frames = []
        for event, data in chat_service.run_turn(session, "go", provider=provider):
            frames.append((event, data))
            if event == "tool_pending_confirmation":
                session.record_decision(data["confirmation_token"], decision)

    assert frames[-1][0] == "turn_done" and len(offers) == 5
    full, narrowed = offers[0], offers[1]
    assert "update_component" in full and "use_toolset" in full
    assert narrowed < full and "update_component" not in narrowed and offers[2] == narrowed
    assert offers[3] == full == offers[4]

    refused = [d for e, d in frames if e == "tool_error" and d["tool_use_id"] == "call_1"]
    assert [d["error_kind"] for d in refused] == ["tool_not_offered"]
    assert results[1]["is_error"] is True and results[1]["content"] == "tool_not_offered"
    assert g1_at_request[:4] == [20.0] * 4

    cards = [d for e, d in frames if e == "tool_pending_confirmation"]
    assert [c["tool_name"] for c in cards] == ["update_component"]
    if decision == "approve":
        assert p_nom() == 35.0 and g1_at_request[4] == 35.0
        receipt = json.loads(results[3]["content"].split("<untrusted_data>")[1].split("</untrusted_data>")[0])
        row = receipt["_edit_receipt"]["rows"][0]
        assert (row["old"], row["new"], row["readback"]) == (20.0, 35.0, 35.0)
    else:
        assert p_nom() == 20.0 and g1_at_request[4] == 20.0
        assert results[3]["is_error"] is True and results[3]["content"] == "confirmation_denied"
