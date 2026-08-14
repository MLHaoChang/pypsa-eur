"""
OpenAI-compatible chat-completions provider over raw httpx.

Covers Ollama, LM Studio, vLLM, and the OpenAI/Moonshot/DashScope clouds —
they all speak this wire format. Exists in THIS plan primarily to prove the
seam is not secretly Anthropic-shaped (seam spec): tool-call framing differs
structurally, and there is no cache_control at all — `stable` is silently
dropped, which is exactly what the annotation design absorbs.

httpx is imported function-locally: it is a dev dependency until plan 2 adds
it to gui-requirements.txt, and this module must import cleanly in the
frozen app regardless.

History translation notes (request.messages arrive Anthropic-block-shaped):
  * assistant tool_use blocks   → assistant message with tool_calls[]
  * user tool_result blocks     → one {"role": "tool"} message each
  * thinking / redacted_thinking→ dropped (no wire equivalent; never replayed)
  * image / document blocks     → dropped here; plan 2's capability gate
                                  refuses them before any provider call
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from services import redaction
from services.llm_provider import LLMEvent, LLMRequest, ProviderError

_REASONING_KEYS = ("reasoning_content", "reasoning")  # passive passthrough


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": t["name"],
                          "description": t.get("description", ""),
                          "parameters": t.get("input_schema", {})}}
            for t in tools]


def _flatten_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _to_openai_messages(request: LLMRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{
        "role": "system",
        "content": "\n\n".join(b.get("text", "")
                               for b in request.system_blocks),
    }]
    for msg in request.messages:
        role, content = msg.get("role"), msg.get("content")
        if role == "assistant" and isinstance(content, list):
            tool_calls = [
                {"id": b.get("id", ""), "type": "function",
                 "function": {"name": b.get("name", ""),
                              "arguments": json.dumps(b.get("input", {}))}}
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant",
                                     "content": _flatten_text(content) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        elif role == "user" and isinstance(content, list):
            results = [b for b in content if isinstance(b, dict)
                       and b.get("type") == "tool_result"]
            for b in results:
                out.append({"role": "tool",
                            "tool_call_id": b.get("tool_use_id", ""),
                            "content": _flatten_text(b.get("content", ""))
                            or json.dumps(b.get("content", ""))})
            plain = _flatten_text(content)
            if plain and not results:
                out.append({"role": "user", "content": plain})
        else:
            out.append({"role": role or "user",
                        "content": _flatten_text(content)})
    return out


class OpenAICompatProvider:
    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str | None = None,
                 http_client: Any | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._http = http_client  # tests inject MockTransport clients

    def _client(self):
        import httpx  # function-local: dev-only dep until plan 2 ships it
        return self._http or httpx.Client(timeout=httpx.Timeout(120.0,
                                                                connect=10.0))

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        import httpx
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": _to_openai_messages(request),
        }
        if request.tools:
            payload["tools"] = _to_openai_tools(request.tools)
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"

        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {}
        client = self._client()
        try:
            with client.stream("POST", f"{self._base}/chat/completions",
                               json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    raise ProviderError(
                        _kind_for_status(resp.status_code),
                        f"HTTP {resp.status_code} from chat/completions")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        yield LLMEvent(type="ping")
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        yield LLMEvent(type="ping")
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        u = chunk["usage"]
                        usage = {
                            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
                            "output_tokens": int(
                                u.get("completion_tokens", 0) or 0),
                        }
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        for rk in _REASONING_KEYS:
                            if delta.get(rk):
                                yield LLMEvent(type="thinking_delta",
                                               text=str(delta[rk]))
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            yield LLMEvent(type="text_delta",
                                           text=delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            slot = calls.setdefault(
                                idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                                yield LLMEvent(
                                    type="tool_use_start",
                                    tool_use_id=tc["id"],
                                    tool_name=(tc.get("function") or {}
                                               ).get("name", ""))
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
        except ProviderError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError(
                "unreachable",
                redaction.redact_secrets_in_str(str(exc))) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "upstream_error",
                redaction.redact_secrets_in_str(str(exc))) from exc
        finally:
            if self._http is None:
                client.close()

        blocks: list[dict[str, Any]] = []
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})
        for idx in sorted(calls):
            slot = calls[idx]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except ValueError:
                args = {"_raw_arguments": slot["args"]}
            blocks.append({"type": "tool_use", "id": slot["id"],
                           "name": slot["name"], "input": args})
        yield LLMEvent(type="message_done", blocks=blocks, usage=usage)


def _kind_for_status(status: int) -> str:
    if status == 401:
        return "unauthorized"
    if status == 429:
        return "rate_limited"
    if 400 <= status < 500:
        # whole-range on purpose — same rationale as map_sdk_exception:
        # a deterministic 4xx retried is guaranteed waste.
        return "invalid_request"
    return "upstream_error"
