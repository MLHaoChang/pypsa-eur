"""Seam tests for the provider extraction (2026-08-13 plan)."""
from __future__ import annotations


def test_redaction_module_scrubs_key_shapes(monkeypatch):
    from services import redaction
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123xyz")
    out = redaction.redact_for_log("boom: sk-ant-abc123xyz in trace")
    assert "sk-ant-abc123xyz" not in out
    assert "[REDACTED-API-KEY]" in out
    out2 = redaction.redact_secrets_in_str("password=hunter2 bearer tok123")
    assert "hunter2" not in out2 and "tok123" not in out2


def test_chat_service_redaction_aliases_still_exist():
    from services import chat_service, redaction
    assert chat_service._redact_for_log is redaction.redact_for_log
    assert chat_service._redact_secrets_in_str is redaction.redact_secrets_in_str


def test_llm_event_vocabulary_and_provider_error():
    from services.llm_provider import (
        ERROR_KINDS, EVENT_TYPES, LLMEvent, LLMRequest, ProviderError,
    )
    assert "unreachable" in ERROR_KINDS and "rate_limited" in ERROR_KINDS
    ev = LLMEvent(type="text_delta", text="hi")
    assert ev.blocks == [] and ev.usage == {}
    assert "message_done" in EVENT_TYPES
    err = ProviderError("rate_limited", "429 from upstream")
    assert err.kind == "rate_limited" and "429" in err.message
    req = LLMRequest(
        model="m", max_tokens=10,
        system_blocks=[{"type": "text", "text": "s", "stable": True}],
        tools=[], tools_stable=True, messages=[], history_stable_anchor=None,
    )
    assert req.system_blocks[0]["stable"] is True
