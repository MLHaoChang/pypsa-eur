"""
One section's prose through the provider seam (WP3).

`generate_section` speaks ONLY the neutral vocabulary of
`services.llm_provider` (`LLMRequest`, `LLMEvent`, `LLMProvider.stream`,
`ProviderError`). No provider name, no SDK, no wire detail appears here —
the same rule the chat harness holds to, and the reason the tests run on
`services.llm_fake.FakeProvider`.

The contract is the one the assessment pins for EVERY profile (§7,
decision 9): the model returns one JSON object as text; it is extracted
(first balanced object, fences tolerated), validated with pydantic
(`SectionDraft`), repaired ONCE with a short user message that carries the
validation error, and otherwise reported as a `SectionFailure` with the
reason and the head of the raw answer. A strict-schema tool on a wire that
has one would be an optional fast path on top; nothing depends on it, and
this module does not offer one yet.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness.protocol import LLMRequest, ProviderError
from services.reports import prompts

MAX_PARAGRAPH_CHARS = 1200
MAX_BULLETS = 8
RAW_HEAD_CHARS = 400

REPAIR_MESSAGE = ("Return only the JSON object; the previous answer failed: "
                  "{error}")


class SectionDraft(BaseModel):
    """What the model must return for one section, validated."""

    model_config = ConfigDict(extra="ignore")

    section_id: str
    paragraphs: list[str] = Field(min_length=1)
    bullets: list[str] = Field(default_factory=list, max_length=MAX_BULLETS)
    # How many repair turns it took (0 or 1); set by `generate_section`.
    repairs: int = 0

    @staticmethod
    def _clean(items: list[Any]) -> list[str]:
        return [str(x).strip() for x in items if x is not None and str(x).strip()]

    def model_post_init(self, __context: Any) -> None:
        self.paragraphs = self._clean(self.paragraphs)
        self.bullets = self._clean(self.bullets)
        if not self.paragraphs:
            raise ValueError("paragraphs must hold at least one non-empty paragraph")
        too_long = [i for i, p in enumerate(self.paragraphs)
                    if len(p) > MAX_PARAGRAPH_CHARS]
        if too_long:
            raise ValueError(
                f"paragraph {too_long[0]} is longer than {MAX_PARAGRAPH_CHARS} "
                "characters; split it")


@dataclass
class SectionFailure:
    """
    Why no draft was produced: a neutral error kind, `aborted`, or the
    validation error after the one repair turn.
    """

    reason: str
    raw_head: str = ""
    repairs: int = 0


# ── JSON extraction ─────────────────────────────────────────────────────────


def extract_json_object(text: str) -> dict | None:
    """
    The first balanced `{…}` in `text` that parses as a JSON object, or None.

    Tolerates prose before and after and a ``` fence around the object. A
    brace inside a JSON string does not count; an unbalanced object is None.
    """
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def _validate_json[T: BaseModel](text: str, model_cls: type[T]) -> tuple[T | None, str]:
    """`(instance, "")` or `(None, error)` for one raw answer."""
    obj = extract_json_object(text)
    if obj is None:
        return None, "no JSON object found in the answer"
    try:
        return model_cls.model_validate(obj), ""
    except (ValidationError, ValueError) as exc:
        return None, _short_error(exc)


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:3]:
            loc = ".".join(str(x) for x in err.get("loc", ())) or "object"
            parts.append(f"{loc}: {err.get('msg', 'invalid')}")
        return "; ".join(parts)
    return str(exc).splitlines()[0][:200]


# ── the turn ────────────────────────────────────────────────────────────────


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text or "(empty)"}]}


def _stream_text(provider: Any, request: LLMRequest,
                 stop_event: threading.Event | None) -> str | None:
    """
    Every `text_delta` of one turn joined; the `message_done` text blocks
    when no delta arrived. None when `stop_event` was set between events.
    """
    deltas: list[str] = []
    done_text: list[str] = []
    for event in provider.stream(request):
        if stop_event is not None and stop_event.is_set():
            return None
        if event.type == "text_delta":
            deltas.append(event.text)
        elif event.type == "message_done":
            for block in event.blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    done_text.append(str(block.get("text", "")))
    return "".join(deltas) if deltas else "".join(done_text)


def generate_section(provider: Any, *, base_request: Mapping[str, Any],
                     section_id: str, title: str, slice: dict[str, Any],
                     language: str, instruction: str | None = None,
                     stop_event: threading.Event | None = None,
                     ) -> SectionDraft | SectionFailure:
    """
    One section's draft from `provider`, or the reason there is none.

    `base_request` carries `model`, `max_tokens` and (optionally)
    `system_blocks` — the pieces the caller resolved from the profile. The
    request goes out with no tools (`tools=[]`, marked stable so a wire
    with a cache has nothing to invalidate) and no history anchor: every
    section is its own single-turn conversation.
    """
    result = generate_json(
        provider, base_request=base_request,
        user_message=prompts.section_user_message(
            section_id, title, slice, language=language, instruction=instruction),
        model_cls=SectionDraft, stop_event=stop_event)
    if isinstance(result, SectionFailure):
        return result
    draft, repairs = result
    # The job knows which section it asked for; a mangled id is not a reason
    # to burn the repair turn.
    draft.section_id = section_id
    draft.repairs = repairs
    return draft


def generate_json[T: BaseModel](provider: Any, *, base_request: Mapping[str, Any],
                                user_message: str, model_cls: type[T],
                                stop_event: threading.Event | None = None,
                                ) -> tuple[T, int] | SectionFailure:
    """
    One JSON object of shape `model_cls` from `provider`, as
    `(instance, repairs)`, or the `SectionFailure` saying why there is none.

    The JSON-in-text contract `generate_section` is built on, for any
    pydantic shape: the answer's first balanced object is validated, repaired
    ONCE with the validation error, else failed with the raw head. The
    request carries the stable system block (from `base_request` or the
    section guide), no tools and no history anchor.
    """
    if stop_event is not None and stop_event.is_set():
        return SectionFailure("aborted")

    system_blocks = list(base_request.get("system_blocks") or prompts.system_blocks())
    messages: list[dict[str, Any]] = [_user(user_message)]

    def request() -> LLMRequest:
        return LLMRequest(
            model=str(base_request["model"]),
            max_tokens=int(base_request["max_tokens"]),
            system_blocks=system_blocks,
            tools=[],
            tools_stable=True,
            messages=messages,
            history_stable_anchor=None,
        )

    raw = ""
    error = ""
    for attempt in range(2):
        if stop_event is not None and stop_event.is_set():
            return SectionFailure("aborted", raw[:RAW_HEAD_CHARS], repairs=attempt)
        try:
            text = _stream_text(provider, request(), stop_event)
        except ProviderError as exc:
            return SectionFailure(exc.kind, raw[:RAW_HEAD_CHARS], repairs=attempt)
        if text is None:
            return SectionFailure("aborted", raw[:RAW_HEAD_CHARS], repairs=attempt)
        raw = text
        instance, error = _validate_json(raw, model_cls)
        if instance is not None:
            return instance, attempt
        if attempt == 0:
            messages.append(_assistant(raw))
            messages.append(_user(REPAIR_MESSAGE.format(error=error)))
    return SectionFailure(error or "invalid answer", raw[:RAW_HEAD_CHARS], repairs=1)
