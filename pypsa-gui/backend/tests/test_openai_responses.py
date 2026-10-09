"""Reasoning-model transport regressions; no network or credentials needed."""
import json
from dataclasses import replace

import httpx
import pytest

from harness.catalogue import TOOLS
from harness.protocol import ProviderError
from harness.providers.openai_compat import OpenAICompatProvider
from tests.test_openai_compatibility import request, sse


def tool_request(model="gpt-6.1-sol"):
    req = request()
    req.model = model
    req.tools = [t for t in TOOLS if t["name"] == "get_component"]
    return req


def completed(output, response_id="resp_1"):
    return {"type": "response.completed", "response": {"id": response_id,
        "output": output, "usage": {"input_tokens": 100, "output_tokens": 20}}}


def call(args='{"component_class":"Bus","name":"B1"}'):
    return {"type": "function_call", "call_id": "call_1", "name": "get_component", "arguments": args}


def text(value):
    return {"type": "message", "content": [{"type": "output_text", "text": value}]}


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra"])
def test_official_reasoning_tools_use_responses(model):
    sent = []
    def handle(req):
        sent.append(json.loads(req.content))
        assert req.url.path == "/v1/responses"
        return httpx.Response(200, content=sse(completed([call()]), done=False))
    provider = OpenAICompatProvider("https://api.openai.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    events = list(provider.stream(tool_request(model)))
    assert sent[0]["max_output_tokens"] == 256
    assert "max_tokens" not in sent[0] and "max_completion_tokens" not in sent[0]
    assert sent[0]["reasoning"] == {"effort": "low"}
    assert sent[0]["tools"][0]["name"] == "get_component"
    assert sent[0]["tools"][0]["strict"] is False
    assert events[-1].blocks[0]["input"]["name"] == "B1"
    assert events[-1].usage == {"input_tokens": 100, "output_tokens": 20}


@pytest.mark.parametrize("model,base", [
    ("gpt-6-luna", "https://api.openai.com/v1"),
    ("gpt-4.1-mini", "https://api.openai.com/v1"),
    ("gpt-6.1-sol", "http://localhost:11434/v1"),
])
def test_other_profiles_keep_chat_completions(model, base):
    sent = []
    def handle(req):
        sent.append(req)
        assert req.url.path == "/v1/chat/completions"
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}))
    provider = OpenAICompatProvider(base, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    assert list(provider.stream(tool_request(model)))[-1].blocks == [{"type": "text", "text": "ok"}]
    assert len(sent) == 1


def test_real_handler_and_reasoning_continuation(install_network):
    import pypsa
    from services import chat_service
    net = pypsa.Network()
    net.add("Bus", "B1", v_nom=110)
    install_network(net)
    sent = []
    def handle(req):
        body = json.loads(req.content)
        sent.append(body)
        assert req.url.path == "/v1/responses"
        if len(sent) == 1:
            return httpx.Response(200, content=sse(
                {"type": "response.output_item.added", "item": call("")},
                {"type": "response.function_call_arguments.delta", "delta": '{"component_'},
                completed([{"type": "reasoning", "id": "private_reasoning"}, call()]), done=False))
        assert body["previous_response_id"] == "resp_1"
        assert len(body["input"]) == 1
        assert body["input"][0]["type"] == "function_call_output"
        assert body["input"][0]["call_id"] == "call_1"
        assert "110" in body["input"][0]["output"]
        return httpx.Response(200, content=sse(
            {"type": "response.output_text.delta", "delta": "110 kV"},
            completed([text("110 kV")], "resp_2"), done=False))
    provider = OpenAICompatProvider("https://api.openai.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    frames = list(chat_service.run_turn(chat_service.ChatSession(model="gpt-6.1-sol"),
                                       "Get B1 voltage", provider=provider))
    assert len(sent) == 2
    assert not any(e in ("error", "tool_error") for e, _ in frames)
    assert any(e == "tool_result" for e, _ in frames)
    assert "110 kV" == "".join(d["delta"] for e, d in frames if e == "token")
    assert frames[-1][0] == "turn_done"


@pytest.mark.parametrize("args", ['{"name":', '[]', 'null', '42'])
def test_malformed_response_arguments_never_dispatch(args):
    provider = OpenAICompatProvider("https://api.openai.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda req:
            httpx.Response(200, content=sse(completed([call(args)]), done=False)))))
    events = []
    with pytest.raises(ProviderError) as exc:
        for event in provider.stream(tool_request()):
            events.append(event)
    assert exc.value.kind == "invalid_request"
    assert all(e.type != "message_done" for e in events)


@pytest.mark.parametrize("event,expected", [
    ({"type": "response.incomplete"}, "invalid_request"),
    ({"type": "response.failed"}, "upstream_error"),
    ({"type": "error"}, "upstream_error"),
    ({"type": "response.created"}, "upstream_error"),
])
def test_incomplete_failed_or_disconnected_responses(event, expected):
    provider = OpenAICompatProvider("https://api.openai.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda req:
            httpx.Response(200, content=sse(event, done=False)))))
    with pytest.raises(ProviderError) as exc:
        list(provider.stream(tool_request()))
    assert exc.value.kind == expected


@pytest.mark.parametrize("status,expected", [(401, "unauthorized"), (403, "unauthorized"), (400, "invalid_request")])
def test_responses_http_errors_are_not_adaptively_retried(status, expected):
    sent = []
    def handle(req):
        sent.append(req)
        return httpx.Response(status, json={"error": {"param": "max_tokens", "code": "unsupported_parameter"}})
    provider = OpenAICompatProvider("https://api.openai.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(ProviderError) as exc:
        list(provider.stream(tool_request()))
    assert exc.value.kind == expected and len(sent) == 1


def test_model_switch_discards_previous_response_pointer():
    sent = []
    def handle(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, content=sse(completed([text("ok")]), done=False))
    provider = OpenAICompatProvider("https://api.openai.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    req = tool_request()
    list(provider.stream(req))
    req.messages.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]})
    req.messages.append({"role": "user", "content": "next"})
    list(provider.stream(replace(req, model="gpt-6-astra")))
    assert "previous_response_id" not in sent[1]
    assert len(sent[1]["input"]) == 3
