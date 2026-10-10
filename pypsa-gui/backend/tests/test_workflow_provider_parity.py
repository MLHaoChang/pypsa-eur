"""Shared workflow tools through real adapters and mocked HTTP streams.

These are transport/dispatch checks, not live model capability claims. No
credentials or paid calls are needed; project handlers and HiGHS are real.
"""
import json
from importlib import import_module

import httpx
import pytest

from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_service, chat_tools, llm_config
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.test_workflow_tools import baseline, case, helpers_state  # noqa: F401


PROVIDERS = [
    ("anthropic", "claude-sonnet-5", "https://api.anthropic.com", "bearer"),
    ("openai", "gpt-6-luna", "https://api.openai.com/v1", "bearer"),
    ("compatible", "Qwen/Qwen3-32B", "https://other-model.example/v1", "bearer"),
    ("local", "qwen3:8b", "http://localhost:11434/v1", "none"),
]


def _anthropic_stream(model, call_id, name, args):
    events = [{"type": "message_start", "message": {
        "id": "msg_parity", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }}]
    if name:
        encoded = json.dumps(args)
        middle = len(encoded) // 2
        events += [
            {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "id": call_id, "name": name, "input": {}}},
            *[{"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": part}}
              for part in (encoded[:middle], encoded[middle:])],
        ]
    else:
        events += [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Finished."}},
        ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use" if name else "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


def _compatible_stream(call_id, name, args):
    if name:
        encoded = json.dumps(args)
        middle = len(encoded) // 2
        chunks = [
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": call_id,
                "type": "function", "function": {"name": name, "arguments": encoded[:middle]}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
                "function": {"arguments": encoded[middle:]}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        ]
    else:
        chunks = [{"choices": [{"index": 0, "delta": {"content": "Finished."}, "finish_reason": "stop"}]}]
    chunks.append({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    return ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()


def _last_result(body, anthropic):
    if anthropic:
        result = body["messages"][-1]["content"][0]
        assert result["type"] == "tool_result"
        return result["tool_use_id"], result["content"], result.get("is_error", False)
    result = body["messages"][-1]
    assert result["role"] == "tool"
    return result["tool_call_id"], result["content"], "confirmation_denied" in result["content"]


@pytest.mark.parametrize("kind,model,base_url,auth", PROVIDERS, ids=[p[0] for p in PROVIDERS])
@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_shared_tools_execute_and_continue_on_each_transport(baseline, kind, model, base_url, auth, decision):
    ctx, _, path = baseline
    original = (path / "network.nc").read_bytes()
    anthropic = kind == "anthropic"
    http_module = httpx
    if anthropic:
        import anthropic as sdk
        # SDK releases use either httpx or httpx2; its public client base
        # identifies the matching transport library without a network call.
        module = next(cls.__module__.split(".")[0] for cls in sdk.DefaultHttpxClient.__mro__
                      if cls.__module__.split(".")[0] in ("httpx", "httpx2"))
        http_module = import_module(module)
    wire = "anthropic" if anthropic else "openai"
    profile = llm_config.LLMProfile(id="parity", label="Parity test", preset="custom",
        wire=wire, base_url=base_url, model=model, tools=True, vision=False,
        auth=auth, fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, wire
    sent, echoed, job_ids = [], [], []
    sequence = [
        ("project_readiness", {"project_id": "Tool Baseline"}),
        ("use_toolset", {"domain": "simulation"}),
        ("run_sensitivity_sweep", {"baseline_project_id": "Tool Baseline", "cases": [case("Parity Sensitivity")]}),
        ("wait_for_job", None),
        ("use_toolset", {"domain": "results"}),
        ("get_study_evidence", {"project_id": "Parity Sensitivity", "section": "simulation"}),
    ]

    def respond(request):
        assert str(request.url) == base_url + ("/v1/messages" if anthropic else "/chat/completions")
        assert bool(request.headers.get("authorization")) == (auth == "bearer" and not anthropic)
        assert bool(request.headers.get("x-api-key")) == anthropic
        body = json.loads(request.content)
        assert body["model"] == model
        index = len(sent)
        sent.append(body)
        previous_error = False
        if index:
            call_id, content, previous_error = _last_result(body, anthropic)
            assert call_id == f"call_{index - 1}"
            echoed.append(call_id)
            if index == 3 and not previous_error:
                # The scripted agent consumes the actual returned job ID,
                # rather than reading queue internals to manufacture a call.
                result, _ = json.JSONDecoder().raw_decode(content[content.index("{"):])
                job_ids.append(result["cases"][0]["job"]["id"])
        name, args = (None, {}) if previous_error or index >= len(sequence) else sequence[index]
        if name == "wait_for_job":
            args = {"job_id": job_ids[-1], "timeout_seconds": 25}
        if name:
            offered = {t["name"] if anthropic else t["function"]["name"] for t in body["tools"]}
            assert name in offered
            if index == 0:
                assert {"project_readiness", "wait_for_job", "get_study_evidence", "use_toolset", "run_sensitivity_sweep"} <= offered
            if index == 2:
                assert "get_solver_config" in offered and "create_component" not in offered
            if index == 5:
                assert "run_sensitivity_sweep" not in offered
        stream = _anthropic_stream(model, f"call_{index}", name, args) if anthropic else _compatible_stream(f"call_{index}", name, args)
        return http_module.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    with http_module.Client(transport=http_module.MockTransport(respond)) as http:
        if anthropic:
            provider = AnthropicProvider(sdk.Anthropic(api_key="synthetic-key", base_url=base_url, http_client=http, max_retries=0))
        else:
            provider = OpenAICompatProvider(base_url, api_key="synthetic-key" if auth == "bearer" else None,
                                             http_client=http, token_param="max_tokens")
        frames = []
        for event, data in chat_service.run_turn(session,
                "project_readiness use_toolset run_sensitivity_sweep wait_for_job get_study_evidence", provider=provider):
            frames.append((event, data))
            if event == "tool_pending_confirmation":
                session.record_decision(data["confirmation_token"], decision)

    assert frames[-1][0] == "turn_done", frames
    assert len([event for event, _ in frames if event == "tool_pending_confirmation"]) == 1
    assert not [data for event, data in frames if event == "error"]
    assert PyPSAService.get_active_context() is ctx and not ctx.results_unsaved
    assert (path / "network.nc").read_bytes() == original
    errors = [data for event, data in frames if event == "tool_error"]
    if decision == "deny":
        assert len(sent) == 4 and len(echoed) == 3
        assert len(errors) == 1 and errors[0]["error_kind"] == "confirmation_denied"
        assert solve_queue.list_jobs() == []
    else:
        assert not errors and len(sent) == 7 and len(echoed) == 6
        results = {data["tool_name"]: data["result"] for event, data in frames if event == "tool_result"}
        assert results["project_readiness"]["ready"]
        assert results["wait_for_job"]["completed"]
        assert results["get_study_evidence"]["available"] and results["get_study_evidence"]["objective"] == 200
        assert len(solve_queue.list_jobs()) == 1


def test_agents_keep_independent_catalogue_views(baseline):
    from harness.providers import wiring
    from harness.toolsets import filter_tools
    first, second = chat_service.ChatSession(), chat_service.ChatSession()
    profile = llm_config.LLMProfile(id="local-agent", label="Local agent", preset="custom",
        wire="openai", base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=False, auth="none", fallback_model=None, max_output_tokens=256)
    eligible = wiring._tools_payload()
    chat_tools.set_chat_session(first)
    chat_tools.use_toolset("simulation")
    chat_tools.set_chat_session(second)
    assert second.toolset == "all"
    chat_tools.use_toolset("results")
    assert wiring._tools_payload_for_profile(profile) == filter_tools(eligible, "results")
    chat_tools.set_chat_session(first)
    assert first.toolset == "simulation"
    assert wiring._tools_payload_for_profile(profile) == filter_tools(eligible, "simulation")
