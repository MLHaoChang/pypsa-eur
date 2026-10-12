"""A7: actual HTTP adapters must share the same publication boundary."""

import json
from importlib import import_module

import httpx
import pytest

from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_service, llm_config
from tests.assurance_acceptance.support import UNSAFE, approval, public_text
from tests.test_workflow_provider_parity import (
    PROVIDERS,
    _anthropic_stream,
    _compatible_stream,
)


@pytest.mark.parametrize(
    "kind,model,base_url,auth", PROVIDERS, ids=[p[0] for p in PROVIDERS]
)
def test_unsafe_transport_text_is_withheld_on_every_supported_wire(
    baseline, kind, model, base_url, auth
):
    anthropic = kind == "anthropic"
    http_module = httpx
    if anthropic:
        import anthropic as sdk

        module = next(
            c.__module__.split(".")[0]
            for c in sdk.DefaultHttpxClient.__mro__
            if c.__module__.split(".")[0] in {"httpx", "httpx2"}
        )
        http_module = import_module(module)
    profile = llm_config.LLMProfile(
        id="assurance-wire",
        label="Acceptance",
        preset="custom",
        wire="anthropic" if anthropic else "openai",
        model=model,
        base_url=base_url,
        tools=True,
        vision=False,
        auth=auth,
        fallback_model=None,
        max_output_tokens=256,
    )
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, profile.wire

    calls = []

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model"] == model
        data = (
            _anthropic_stream(model, "none", None, {})
            if anthropic
            else _compatible_stream("none", None, {})
        )
        return http_module.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=data.replace(b"Finished.", UNSAFE.encode()),
        )

    with http_module.Client(transport=http_module.MockTransport(respond)) as http:
        provider = (
            AnthropicProvider(
                sdk.Anthropic(
                    api_key="synthetic-key",
                    base_url=base_url,
                    http_client=http,
                    max_retries=0,
                )
            )
            if anthropic
            else OpenAICompatProvider(
                base_url,
                api_key="synthetic-key" if auth == "bearer" else None,
                http_client=http,
                token_param="max_tokens",
            )
        )
        frames = list(
            chat_service.run_turn(
                session, "Analyze the selected project.", provider=provider
            )
        )
    assert calls, "No mocked provider transport was exercised"
    assert len(calls) <= 2, "Malformed output caused unbounded repair calls"
    assert not [data for event, data in frames if event in {"error", "session_done"}], (
        frames
    )
    approved = approval(frames)
    assert approved["verdict"] == "limited" and approved["receipt"]["limitations"]
    assert UNSAFE not in public_text(frames), (
        "This provider bypassed the shared assurance boundary"
    )
