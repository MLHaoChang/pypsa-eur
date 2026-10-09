"""Responses transport for official models requiring it for reasoning tools."""
from copy import deepcopy
import json
from urllib.parse import urlsplit

from harness.protocol import LLMEvent, ProviderError


REASONING_TOOL_MODELS = frozenset({"gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra"})


def uses_responses(base, request):
    return (urlsplit(base).hostname == "api.openai.com"
            and request.model in REASONING_TOOL_MODELS
            and bool(request.tools))


def payload(request, messages, state):
    """Translate replayable history; reuse server reasoning within a tool loop."""
    prefix = state.get("messages", [])
    continuation = (state.get("id") and state.get("model") == request.model
                    and state.get("system") == request.system_blocks
                    and request.messages[:len(prefix)] == prefix)
    if continuation:
        # The completed response (including private reasoning) is already on
        # the server. Replay only newly appended tool results/user content.
        messages = messages_from_tail(request.messages[len(prefix):])
    inputs = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            inputs.append({"type": "function_call_output",
                           "call_id": message["tool_call_id"], "output": message["content"]})
            continue
        content = message.get("content")
        if content:
            if not isinstance(content, str):
                content = [({"type": "input_image", "image_url": b["image_url"]["url"]}
                            if b.get("type") == "image_url" else
                            {"type": "input_text", "text": b["text"]})
                           for b in content]
            inputs.append({"role": role, "content": content})
        for call in message.get("tool_calls", []):
            inputs.append({"type": "function_call", "call_id": call["id"],
                           "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    body = {"model": request.model, "max_output_tokens": request.max_tokens,
            "stream": True, "input": inputs, "reasoning": {"effort": "low"}}
    if continuation:
        body["previous_response_id"] = state["id"]
    if request.tools:
        body["tools"] = [{"type": "function", "name": t["name"],
                          "description": t.get("description", ""),
                          "parameters": t["input_schema"], "strict": False} for t in request.tools]
    return body


def messages_from_tail(messages):
    from harness.protocol import LLMRequest
    from harness.providers.openai_compat import _to_openai_messages
    return _to_openai_messages(LLMRequest("", 0, [], [], False, messages, None))


def stream_response(response, request, state):
    """Translate streamed Responses events into the existing neutral seam."""
    starts = set()
    completed = False
    for line in response.iter_lines():
        if not line.startswith("data:"):
            yield LLMEvent(type="ping")
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            yield LLMEvent(type="ping")
            continue
        kind = event.get("type")
        if kind == "response.output_text.delta":
            yield LLMEvent(type="text_delta", text=event.get("delta", ""))
        elif kind == "response.reasoning_summary_text.delta":
            yield LLMEvent(type="thinking_delta", text=event.get("delta", ""))
        elif kind == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "function_call" and item.get("call_id") not in starts:
                starts.add(item["call_id"])
                yield LLMEvent(type="tool_use_start", tool_use_id=item["call_id"], tool_name=item["name"])
        elif kind in ("response.failed", "response.incomplete", "error"):
            state.clear()
            raise ProviderError("invalid_request" if kind == "response.incomplete" else "upstream_error",
                                f"Responses stream ended with {kind}")
        elif kind == "response.completed":
            result = event["response"]
            blocks = []
            for item in result.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            blocks.append({"type": "text", "text": part["text"]})
                elif item.get("type") == "function_call":
                    try:
                        args = json.loads(item.get("arguments", "{}"))
                    except ValueError as exc:
                        raise ProviderError("invalid_request", "tool-call arguments are not valid JSON") from exc
                    if not isinstance(args, dict):
                        raise ProviderError("invalid_request", "tool-call arguments must be a JSON object")
                    call_id = item["call_id"]
                    if call_id not in starts:
                        yield LLMEvent(type="tool_use_start", tool_use_id=call_id, tool_name=item["name"])
                        starts.add(call_id)
                    blocks.append({"type": "tool_use", "id": call_id, "name": item["name"], "input": args})
            usage = result.get("usage") or {}
            neutral_usage = {"input_tokens": usage.get("input_tokens", 0),
                             "output_tokens": usage.get("output_tokens", 0)} if usage else {}
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            if cached:
                neutral_usage["cache_read_tokens"] = cached
            state.update(id=result.get("id"), model=request.model,
                         system=deepcopy(request.system_blocks),
                         messages=deepcopy(request.messages) + [{"role": "assistant", "content": deepcopy(blocks)}])
            completed = True
            yield LLMEvent(type="message_done", blocks=blocks, usage=neutral_usage)
            break
        else:
            yield LLMEvent(type="ping")
    if not completed:
        state.clear()
        raise ProviderError("upstream_error", "Responses stream ended before completion")
