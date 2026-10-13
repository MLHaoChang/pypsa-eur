"""
WP3 — `services/reports/generator.py::generate_section` on the fake provider.

The provider-neutral contract (assessment §7, decision 9): one JSON object
per section as text, validated with pydantic, ONE repair turn, else a
`SectionFailure` that names the reason. No provider name in the module; the
request carries no tools and the stable system block.
"""
from __future__ import annotations

import json
import threading

from services.llm_fake import FakeProvider
from services.llm_provider import LLMEvent, ProviderError
from services.reports import prompts
from services.reports.generator import (
    SectionDraft,
    SectionFailure,
    extract_json_object,
    generate_section,
)

_SLICE = {"section_id": "fmea_top", "title": "Residual failure modes",
          "status": "ok", "payload": {"top": [{"rank": 1, "name": "g"}]},
          "required_disclosures": [], "table_ids": ["fmea_top"]}
_BASE = {"model": "test-model", "max_tokens": 1024,
         "system_blocks": prompts.system_blocks()}


def _turn(text: str, *, chunks: int = 1) -> dict:
    step = max(1, len(text) // chunks)
    pieces = [text[i:i + step] for i in range(0, len(text), step)] or [""]
    return {"events": [LLMEvent(type="text_delta", text=p) for p in pieces],
            "blocks": [{"type": "text", "text": text}]}


def _valid(section_id: str = "fmea_top") -> str:
    return json.dumps({"section_id": section_id,
                       "paragraphs": ["One residual mode dominates."],
                       "bullets": ["Generator g: rank 1."]})


def _run(provider, **kw):
    return generate_section(provider, base_request=_BASE, section_id="fmea_top",
                            title="Residual failure modes", slice=_SLICE,
                            language="en", **kw)


# ── extract_json_object ─────────────────────────────────────────────────────

def test_extract_json_object_finds_the_first_balanced_object():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('Sure! Here it is:\n```json\n{"a": {"b": [1, 2]}}\n```\nDone.') \
        == {"a": {"b": [1, 2]}}
    assert extract_json_object('text {"a": "}"} tail {"b": 2}') == {"a": "}"}
    assert extract_json_object("no json here") is None
    assert extract_json_object('{"a": 1') is None
    assert extract_json_object("") is None


# ── generate_section ────────────────────────────────────────────────────────

def test_valid_json_first_try_is_a_draft_with_no_repairs():
    provider = FakeProvider([_turn(_valid(), chunks=5)])
    out = _run(provider)
    assert isinstance(out, SectionDraft), out
    assert out.section_id == "fmea_top"
    assert out.paragraphs == ["One residual mode dominates."]
    assert out.bullets == ["Generator g: rank 1."]
    assert out.repairs == 0
    assert len(provider.requests) == 1


def test_prose_then_valid_json_on_repair_counts_one_repair():
    provider = FakeProvider([
        _turn("Here is the section. The top mode is the generator."),
        _turn("```json\n" + _valid() + "\n```"),
    ])
    out = _run(provider)
    assert isinstance(out, SectionDraft), out
    assert out.repairs == 1
    assert len(provider.requests) == 2
    repair = provider.requests[1]
    # The repair turn carries the model's own answer and one user message.
    roles = [m["role"] for m in repair.messages]
    assert roles == ["user", "assistant", "user"]
    last = repair.messages[-1]["content"]
    last_text = last if isinstance(last, str) else last[0]["text"]
    assert "Return only the JSON object" in last_text
    assert "previous answer failed" in last_text


def test_garbage_twice_is_a_failure_with_the_raw_head():
    provider = FakeProvider([_turn("nonsense " * 50), _turn("still nonsense")])
    out = _run(provider)
    assert isinstance(out, SectionFailure), out
    assert out.reason
    assert out.raw_head.startswith("still nonsense") or out.raw_head.startswith("nonsense")
    assert len(out.raw_head) <= 400
    assert out.repairs == 1


def test_valid_json_of_the_wrong_shape_is_repaired_then_failed():
    wrong = json.dumps({"section_id": "fmea_top", "paragraphs": []})
    provider = FakeProvider([_turn(wrong), _turn(wrong)])
    out = _run(provider)
    assert isinstance(out, SectionFailure)
    assert "paragraphs" in out.reason


def test_provider_error_is_a_failure_of_that_kind():
    provider = FakeProvider([ProviderError("rate_limited", "slow down")])
    out = _run(provider)
    assert isinstance(out, SectionFailure)
    assert out.reason == "rate_limited"


def test_stop_event_set_before_the_call_aborts_without_a_request():
    stop = threading.Event()
    stop.set()
    provider = FakeProvider([_turn(_valid())])
    out = _run(provider, stop_event=stop)
    assert isinstance(out, SectionFailure)
    assert out.reason == "aborted"
    assert provider.requests == []


def test_stop_event_set_mid_stream_aborts_between_events():
    stop = threading.Event()

    class Tripwire(FakeProvider):
        def stream(self, request):
            for i, ev in enumerate(super().stream(request)):
                if i == 1:
                    stop.set()
                yield ev

    provider = Tripwire([_turn(_valid(), chunks=6)])
    out = _run(provider, stop_event=stop)
    assert isinstance(out, SectionFailure) and out.reason == "aborted"


def test_request_carries_no_tools_and_the_stable_system_block():
    provider = FakeProvider([_turn(_valid())])
    _run(provider)
    req = provider.requests[0]
    assert req.tools == []
    assert req.tools_stable is True
    assert req.history_stable_anchor is None
    assert req.model == "test-model" and req.max_tokens == 1024
    assert req.system_blocks == [{"type": "text", "text": prompts.SYSTEM_BLOCK,
                                  "stable": True}]
    assert req.messages[0]["role"] == "user"
    body = req.messages[0]["content"]
    text = body if isinstance(body, str) else body[0]["text"]
    assert "<untrusted_data>" in text and '"top"' in text


def test_the_requested_section_id_wins_over_the_models():
    provider = FakeProvider([_turn(_valid("something_else"))])
    out = _run(provider)
    assert isinstance(out, SectionDraft) and out.section_id == "fmea_top"


def test_text_only_in_message_done_blocks_is_still_read():
    provider = FakeProvider([{"events": [], "blocks": [{"type": "text", "text": _valid()}]}])
    out = _run(provider)
    assert isinstance(out, SectionDraft)


def test_no_provider_name_in_the_generator_module():
    import pathlib

    from services.reports import generator
    src = pathlib.Path(generator.__file__).read_text(encoding="utf-8").lower()
    assert "anthropic" not in src and "openai" not in src
