"""Cached input is reported beside uncached input on both OpenAI wires.

Owner decision 2026-10-12 (chat harness issue 20): `input_tokens` means
input NOT served from cache on every wire, as on the Anthropic wire, so the
panel meter and the daily cap never count a cached token twice. The vendor
prompt count includes the cached part; the adapter subtracts it.
"""
import httpx

from harness.providers.openai_compat import OpenAICompatProvider
from tests.test_openai_compatibility import request, sse
from tests.test_openai_responses import completed, text, tool_request


def test_chat_cached_input_is_reported_beside_uncached_input():
    provider = OpenAICompatProvider("https://api.openai.com/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 2000, "completion_tokens": 20,
                       "prompt_tokens_details": {"cached_tokens": 1536}}})))))
    assert list(provider.stream(request()))[-1].usage == {
        "input_tokens": 464, "output_tokens": 20,
        "cache_read_tokens": 1536, "cache_create_tokens": 0}


def test_responses_cached_input_is_reported_beside_uncached_input():
    event = completed([text("ok")])
    event["response"]["usage"] = {"input_tokens": 2000, "output_tokens": 20,
                                   "input_tokens_details": {"cached_tokens": 1536}}
    provider = OpenAICompatProvider("https://api.openai.com/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse(event)))))
    assert list(provider.stream(tool_request()))[-1].usage == {
        "input_tokens": 464, "output_tokens": 20,
        "cache_read_tokens": 1536, "cache_create_tokens": 0}
