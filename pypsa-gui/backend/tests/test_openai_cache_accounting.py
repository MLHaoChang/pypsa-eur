"""Provider caching metadata remains separate from total-token accounting."""
import httpx

from harness.providers.openai_compat import OpenAICompatProvider
from tests.test_openai_compatibility import request, sse
from tests.test_openai_responses import completed, text, tool_request


def test_chat_cached_input_is_reported_without_subtracting_total_input():
    provider = OpenAICompatProvider("https://api.openai.com/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse(
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 2000, "completion_tokens": 20,
                       "prompt_tokens_details": {"cached_tokens": 1536}}})))))
    assert list(provider.stream(request()))[-1].usage == {
        "input_tokens": 2000, "output_tokens": 20, "cache_read_tokens": 1536}


def test_responses_cached_input_is_reported_without_subtracting_total_input():
    event = completed([text("ok")])
    event["response"]["usage"] = {"input_tokens": 2000, "output_tokens": 20,
                                   "input_tokens_details": {"cached_tokens": 1536}}
    provider = OpenAICompatProvider("https://api.openai.com/v1", http_client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=sse(event)))))
    assert list(provider.stream(tool_request()))[-1].usage == {
        "input_tokens": 2000, "output_tokens": 20, "cache_read_tokens": 1536}
