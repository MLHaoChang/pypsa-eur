"""Opt-in evaluation transport: reserve each physical HTTP attempt before send."""
from contextlib import contextmanager
import json

import httpx

from harness.protocol import ProviderError
from harness.providers.openai_compat import OpenAICompatProvider


def reserve_request(meter, request):
    if request.url.scheme != "https" or request.url.host != "api.openai.com":
        raise ProviderError("invalid_request", "evaluation destination refused")
    try:
        body = json.loads(request.content)
        limits = [body[name] for name in ("max_tokens", "max_completion_tokens", "max_output_tokens") if name in body]
        if len(limits) != 1:
            raise ValueError("missing or ambiguous output cap")
        meter.reserve(len(request.content), limits[0])
    except (ValueError, TypeError, KeyError):
        raise ProviderError("invalid_request", "evaluation payload refused before send") from None


@contextmanager
def bounded_provider(key, meter, *, transport=None):
    with httpx.Client(timeout=httpx.Timeout(15, connect=10), follow_redirects=False,
                      transport=transport, event_hooks={"request": [lambda request: reserve_request(meter, request)]}) as client:
        yield OpenAICompatProvider("https://api.openai.com/v1", api_key=key,
                                   http_client=client, token_param="max_completion_tokens")
