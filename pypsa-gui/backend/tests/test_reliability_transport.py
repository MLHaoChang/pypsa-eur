"""Checked cost publication through real SDK transports, including Responses."""

# ruff: noqa: F811 -- shared pytest fixtures.
import json
import re
from importlib import import_module

import httpx
import pytest

from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_service, llm_config
from tests.assurance_acceptance.support import approval, public_text
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_harness_reliability import VALUES, scope
from tests.test_openai_compatibility import sse
from tests.test_openai_responses import completed, text
from tests.test_workflow_provider_parity import (
    PROVIDERS,
    _anthropic_stream,
    _compatible_stream,
)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


@pytest.mark.parametrize(
    "kind,model,base_url,auth",
    [*PROVIDERS, ("responses", "gpt-6.1-sol", "https://api.openai.com/v1", "bearer")],
)
@pytest.mark.parametrize("fabricate", [False, True])
def test_cost_scope_on_every_transport(solved, kind, model, base_url, auth, fabricate):
    anthropic = kind == "anthropic"
    http_module = httpx
    if anthropic:
        import anthropic as sdk

        http_module = import_module(
            next(
                c.__module__.split(".")[0]
                for c in sdk.DefaultHttpxClient.__mro__
                if c.__module__.split(".")[0] in {"httpx", "httpx2"}
            )
        )
    profile = llm_config.LLMProfile(
        id="reliability-transport",
        label="Reliability test",
        preset="custom",
        wire="anthropic" if anthropic else "openai",
        model=model,
        base_url=base_url,
        tools=True,
        vision=False,
        auth=auth,
        fallback_model=None,
        max_output_tokens=1024,
    )
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, profile.wire
    sent = []

    def respond(request):
        body = json.loads(request.content)
        sent.append(body)
        ledgers = [
            json.loads(m)
            for value in strings(body)
            for m in re.findall(
                r'<untrusted_data>(\{"schema_version".*?\})</untrusted_data>', value
            )
        ]
        assert len(ledgers) == 1
        ledger = ledgers[0]
        for block in ledger["blocks"]:
            block["value"] = (
                999
                if fabricate and block["metric"] == "total_cost"
                else VALUES[block["metric"]]
            )
        draft = json.dumps(ledger)
        if kind == "responses":
            assert request.url.path == "/v1/responses"
            data = sse(completed([text(draft)]), done=False)
        else:
            data = (
                _anthropic_stream(model, "none", None, {})
                if anthropic
                else _compatible_stream("none", None, {})
            )
            # Replace one string through JSON encoding, never splice unescaped
            # identifiers/draft JSON into an SDK wire payload.
            data = data.replace(
                json.dumps("Finished.").encode(), json.dumps(draft).encode()
            )
        return http_module.Response(
            200, headers={"content-type": "text/event-stream"}, content=data
        )

    with http_module.Client(transport=http_module.MockTransport(respond)) as http:
        provider = (
            AnthropicProvider(
                sdk.Anthropic(
                    api_key="synthetic",
                    base_url=base_url,
                    http_client=http,
                    max_retries=0,
                )
            )
            if anthropic
            else OpenAICompatProvider(
                base_url,
                api_key="synthetic" if auth == "bearer" else None,
                http_client=http,
                token_param="max_tokens",
            )
        )
        frames = list(
            chat_service.run_turn(
                session,
                "Check the declared key results.",
                provider=provider,
                strict_assurance=True,
                checked_scope=scope(solved),
            )
        )
    assert len(sent) == 1
    approved = approval(frames)
    assert approved["verdict"] == ("limited" if fabricate else "passed")
    assert "999 EUR" not in public_text(frames)
    if not fabricate:
        assert "230 EUR" in public_text(frames)
        assert approved["receipt"]["request_complete"]
