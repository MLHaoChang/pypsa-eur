# LLM Provider Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the Anthropic SDK coupling out of `chat_service.py` behind a
provider-neutral `LLMProvider` seam, prove the seam with a Fake and an
OpenAI-compatible implementation, and change zero observable behaviour.

**Architecture:** Three new provider modules (`llm_provider.py` types,
`llm_anthropic.py`, `llm_fake.py`, `llm_openai_compat.py`) plus one moved
redaction module. The harness (`_run_turn_body`) stops reading SDK event
objects and instead consumes a closed `LLMEvent` vocabulary; cache intent is
annotated as `stable`, translated to `cache_control` only inside
`AnthropicProvider`. The existing `client=` test-injection kwarg and every
`chat_service.<symbol>` monkeypatch surface keep working via aliases.

**Tech Stack:** Python 3.12, FastAPI backend, `anthropic` SDK (guarded
import), `httpx` (dev env), pytest via pixi.

**Spec:** `docs/superpowers/specs/2026-08-05-llm-provider-seam-design.md`,
as corrected by `docs/superpowers/specs/2026-08-13-llm-provider-config-and-switching-design.md`
(§Relationship to the provider seam): the registry is **117** tools, and
`reconstruct_network_from_image` is a fifth SDK site — **out of scope here**
(plan 2 reprofiles it); this plan must simply not break it.

## Global Constraints

- **Gate:** `pixi run gui-tests` from the repo root. NEVER bare
  `pixi run python -m pytest` for the full suite — in the default env the 7
  pywebview tests fail by design and that reads as a broken seam.
- **Single test:** `pixi run -e test python -m pytest pypsa-gui/backend/tests/<file>.py -v`
  from the repo root (conftest inserts the backend dir on sys.path).
- **Behaviour preservation is the acceptance bar:** the chat suites
  (`test_chat_e2e.py`, `test_chat_sse.py`, `test_chat_thinking_blocks.py`,
  `test_chat_track_a_e2e.py`, `test_chat_message_trim.py`,
  `test_chat_api_key_settings.py`) pass **unmodified**. Any edit to an
  existing chat test in this plan is a defect, not a fix.
- **No new key requirement:** the suites pass today without
  `ANTHROPIC_API_KEY`; they must after.
- **No frontend change of any kind.** SSE frame names, payload keys and
  ordering are byte-stable.
- `anthropic` must NOT be imported at module import time in any module —
  `chat_service` is importable without the SDK installed and stays so.
- **Preserved injection surfaces** (tests pin them):
  `run_turn(..., client=<fake>)`; `chat_service._build_anthropic_client`;
  `chat_service._map_sdk_exception`; `chat_service._serialise_for_anthropic`;
  `chat_service._with_history_cache_breakpoint`;
  `chat_service._redact_for_log` / `_redact_secrets_in_str`. All remain
  reachable at those names (aliases are fine).
- Error kinds are the existing closed set plus **`unreachable`** (new,
  non-retryable, used by the OpenAI-compat path only in this plan).
- Every task: TDD with RED/GREEN evidence in the report (per user CLAUDE.md).
  Commits small, message style `refactor(chat): …` / `feat(chat): …` /
  `test(chat): …`.
- **Concurrency:** before Task 1, run `git status --short` in
  `/Users/orange/Desktop/Code Test/pypsa-eur-assistant` — expect clean; this
  plan touches `backend/services/` only. The sibling `pypsa-eur` worktree's
  compare/economics work does not overlap.

## File Structure

| File | Responsibility |
|---|---|
| Create `backend/services/redaction.py` | secret-scrubbing helpers, moved verbatim from `chat_service` (providers need them; a provider importing `chat_service` would be an import cycle) |
| Create `backend/services/llm_provider.py` | neutral vocabulary: `LLMRequest`, `LLMEvent`, `ProviderError`, `ERROR_KINDS`, `LLMProvider` protocol. Zero deps beyond stdlib |
| Create `backend/services/llm_anthropic.py` | everything SDK-shaped: client build, exception→kind map, block serialisation, `stable`→`cache_control` translation, the stream loop |
| Create `backend/services/llm_fake.py` | scripted provider; records requests so tests can assert `stable` markers survive |
| Create `backend/services/llm_openai_compat.py` | chat-completions SSE over httpx; proves the seam is not Anthropic-shaped |
| Modify `backend/services/chat_service.py` | `_run_turn_body` consumes `LLMEvent`s; moved symbols become aliases |
| Create `backend/tests/test_llm_provider_seam.py` | provider unit tests + the cross-provider seam test |

---

### Task 1: Move redaction into `services/redaction.py`

**Files:**
- Create: `pypsa-gui/backend/services/redaction.py`
- Modify: `pypsa-gui/backend/services/chat_service.py` (regex constants ~`:320-353`, `_redact_for_log` `:1468-1484`)
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py` (new file, first test)

**Interfaces:**
- Consumes: nothing (stdlib + `os`, `re` only).
- Produces: `redaction.redact_secrets_in_str(text: str) -> str`,
  `redaction.redact_for_log(value: Any) -> str` — used by Tasks 3, 6, and by
  `chat_service` via aliases.

- [ ] **Step 1: Write the failing test**

```python
# pypsa-gui/backend/tests/test_llm_provider_seam.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.redaction'`

- [ ] **Step 3: Create the module by moving code verbatim**

Create `services/redaction.py` with this skeleton, then MOVE (cut, do not
copy) the following symbols from `chat_service.py` into it, bodies verbatim,
renamed only by dropping the leading underscore: the three pattern constants
`_SECRET_KV_RE`, `_BEARER_RE`, `_SK_ANT_RE` (defined just above
`_redact_secrets_in_str`, ~`:320-346`), `_redact_secrets_in_str` (`:347-353`)
and `_redact_for_log` (`:1468-1484`).

```python
"""
Secret scrubbing shared by chat_service and the LLM provider modules.

Moved out of chat_service so providers can import it without a cycle
(providers must never import chat_service). Bodies are verbatim moves;
chat_service re-exports under the old underscore names because tests and
callers pin those.
"""
from __future__ import annotations

import os
import re
from typing import Any

# <the three regex constants, moved verbatim, names unchanged>

def redact_secrets_in_str(text: str) -> str:
    ...  # body of chat_service._redact_secrets_in_str, verbatim

def redact_for_log(value: Any) -> str:
    ...  # body of chat_service._redact_for_log, verbatim (drop its inner
         # `import os` / `import re` — module-level imports cover them)
```

In `chat_service.py`, at the two vacated sites, leave:

```python
from services.redaction import (  # moved 2026-08-13 (provider seam, Task 1)
    redact_for_log as _redact_for_log,
    redact_secrets_in_str as _redact_secrets_in_str,
)
```

(one import near the old regex site; delete the duplicate definitions.)

- [ ] **Step 4: Run the new test, then the chat suites**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v`
Expected: PASS (2 tests)

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_chat_e2e.py pypsa-gui/backend/tests/test_chat_sse.py -q`
Expected: PASS, zero failures (aliases preserve every call site)

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/redaction.py pypsa-gui/backend/services/chat_service.py pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "refactor(chat): move secret redaction into services/redaction.py"
```

---

### Task 2: Neutral vocabulary — `services/llm_provider.py`

**Files:**
- Create: `pypsa-gui/backend/services/llm_provider.py`
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py`

**Interfaces:**
- Consumes: nothing.
- Produces (exact, later tasks depend on these):
  `LLMRequest(model, max_tokens, system_blocks, tools, tools_stable, messages, history_stable_anchor)`;
  `LLMEvent(type, text, tool_use_id, tool_name, blocks, usage)` with `type ∈
  {"text_delta","thinking_delta","tool_use_start","ping","message_done"}`;
  `ProviderError(kind, message)` with `.kind`/`.message`;
  `ERROR_KINDS` frozenset; `LLMProvider` Protocol with `name: str` and
  `stream(request) -> Iterator[LLMEvent]`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py::test_llm_event_vocabulary_and_provider_error -v`
Expected: FAIL — `No module named 'services.llm_provider'`

- [ ] **Step 3: Write the module**

```python
"""
Provider-neutral vocabulary for the LLM seam (spec 2026-08-05, plan 2026-08-13).

The harness speaks ONLY these types. No provider name, SDK import, or
wire-format detail may appear above the provider layer. Cache intent is the
`stable` annotation on blocks / `tools_stable` / `history_stable_anchor`;
"cache_control" is an Anthropic word and lives in llm_anthropic only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

# Neutral error kinds. Harness-owned; each provider maps its own exceptions
# into exactly these. `invalid_request` is deliberately non-retryable (see
# chat_service._RETRYABLE_SDK_KINDS) — a deterministic 4xx retried is pure
# waste (the thinking-block incident). `unreachable` is connect-level failure
# (refused / DNS / connect timeout): meaningful for local endpoints, never
# retryable within a turn.
ERROR_KINDS: frozenset[str] = frozenset([
    "missing_api_key", "sdk_not_installed", "unauthorized", "rate_limited",
    "upstream_error", "invalid_request", "internal_error", "unreachable",
])

# Closed event vocabulary. `ping` exists so providers can surface EVERY
# upstream stream event for abort-latency purposes (the harness checks
# session.abort_event per event) without the harness knowing event shapes.
EVENT_TYPES: frozenset[str] = frozenset([
    "text_delta", "thinking_delta", "tool_use_start", "ping", "message_done",
])


@dataclass
class LLMRequest:
    model: str
    max_tokens: int
    # [{"type": "text", "text": str, "stable": bool}] — `stable` marks a
    # prefix that does not change between turns of one session.
    system_blocks: list[dict[str, Any]]
    # Neutral tool triples {name, description, input_schema} — exactly
    # chat_tools_schema.TOOLS entries, unwrapped.
    tools: list[dict[str, Any]]
    tools_stable: bool
    # Message history in the harness's persisted block format (Anthropic
    # block dicts — the documented neutral history format; non-Anthropic
    # providers translate on the way out and must not mutate these).
    messages: list[dict[str, Any]]
    # Index of the last completed message before this turn, or None on a
    # session's first turn. The stable-history marker.
    history_stable_anchor: int | None


@dataclass
class LLMEvent:
    type: str
    text: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    # message_done only: serialised content blocks (plain dicts, replayable).
    blocks: list[dict[str, Any]] = field(default_factory=list)
    # message_done only: {"input_tokens", "output_tokens",
    # "cache_read_tokens", "cache_create_tokens"} — absent keys read as 0.
    usage: dict[str, int] = field(default_factory=dict)


class ProviderError(Exception):
    """A provider-side failure, already mapped to a neutral kind."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "internal_error"
        self.message = message


class LLMProvider(Protocol):
    name: str

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/llm_provider.py pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "feat(chat): neutral LLM provider vocabulary (seam types)"
```

---

### Task 3: `FakeProvider` — scripted, recording

**Files:**
- Create: `pypsa-gui/backend/services/llm_fake.py`
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py`

**Interfaces:**
- Consumes: Task 2 types.
- Produces: `FakeProvider(turns: list[dict | ProviderError])`. Each dict turn:
  `{"events": list[LLMEvent], "blocks": list[dict], "usage": dict}`.
  `.requests: list[LLMRequest]` — deep-copied record of every `stream()`
  call, in order. Task 7's cache-marker test reads it.

- [ ] **Step 1: Write the failing test**

```python
def test_fake_provider_scripts_and_records():
    import copy
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent, LLMRequest, ProviderError

    turns = [
        {"events": [LLMEvent(type="text_delta", text="he"),
                    LLMEvent(type="text_delta", text="llo")],
         "blocks": [{"type": "text", "text": "hello"}],
         "usage": {"input_tokens": 3, "output_tokens": 2}},
        ProviderError("rate_limited", "scripted 429"),
    ]
    p = FakeProvider(turns)
    req = LLMRequest(model="m", max_tokens=8,
                     system_blocks=[{"type": "text", "text": "s", "stable": True}],
                     tools=[{"name": "t", "description": "d", "input_schema": {}}],
                     tools_stable=True, messages=[], history_stable_anchor=None)
    got = list(p.stream(req))
    assert [e.type for e in got] == ["text_delta", "text_delta", "message_done"]
    assert got[-1].blocks == [{"type": "text", "text": "hello"}]
    assert got[-1].usage["input_tokens"] == 3
    # request recorded by value, not reference
    assert p.requests[0].system_blocks[0]["stable"] is True
    req.system_blocks[0]["stable"] = False
    assert p.requests[0].system_blocks[0]["stable"] is True

    import pytest
    with pytest.raises(ProviderError) as ei:
        list(p.stream(req))
    assert ei.value.kind == "rate_limited"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py::test_fake_provider_scripts_and_records -v`
Expected: FAIL — `No module named 'services.llm_fake'`

- [ ] **Step 3: Write the module**

```python
"""
Deterministic scripted provider. No network, no key.

This is the provider-level fake the seam spec calls for: it runs the REAL
agent loop (unlike StreamRequest.script, which fakes output frames and
bypasses dispatch/confirmation). It also records every LLMRequest so tests
can assert the `stable` annotations actually arrive — the guard against the
silent tenfold cache-cost regression.
"""
from __future__ import annotations

import copy
from typing import Any, Iterator

from services.llm_provider import LLMEvent, LLMRequest, ProviderError


class FakeProvider:
    name = "fake"

    def __init__(self, turns: list[dict[str, Any] | ProviderError]) -> None:
        self._turns = list(turns)
        self.requests: list[LLMRequest] = []

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        self.requests.append(copy.deepcopy(request))
        if not self._turns:
            raise AssertionError("FakeProvider: script exhausted")
        turn = self._turns.pop(0)
        if isinstance(turn, ProviderError):
            raise turn
        yield from turn.get("events", [])
        yield LLMEvent(
            type="message_done",
            blocks=copy.deepcopy(turn.get("blocks", [])),
            usage=dict(turn.get("usage", {})),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/llm_fake.py pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "feat(chat): FakeProvider — scripted seam-level fake that records requests"
```

---

### Task 4: `AnthropicProvider` — the SDK code, relocated

**Files:**
- Create: `pypsa-gui/backend/services/llm_anthropic.py`
- Modify: `pypsa-gui/backend/services/chat_service.py`
  (`_build_anthropic_client` `:1487-1513`, `_map_sdk_exception` `:1516-1555`,
  `_serialise_for_anthropic` `:1784-1840`, `_with_history_cache_breakpoint`
  `:1401-1465` — all become verbatim moves + aliases)
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py`

**Interfaces:**
- Consumes: Task 2 types; `redaction.redact_for_log` (Task 1).
- Produces: `build_client() -> tuple[Any | None, str | None]` (body of old
  `_build_anthropic_client`, verbatim); `map_sdk_exception(exc) -> tuple[str, str]`;
  `serialise_block(block) -> dict`; `with_history_cache_breakpoint(messages, anchor) -> list`;
  `AnthropicProvider(client)` with `.name == "anthropic"` and
  `.stream(request)` per the protocol. Task 5 wraps injected fake SDK
  clients in `AnthropicProvider(client)`.

- [ ] **Step 1: Write the failing test**

The fake SDK client below mirrors the shape the e2e suite already fakes
(`messages.stream(**kwargs)` context manager, events with `.type`, and
`get_final_message()`); recording `calls` proves kwargs stay byte-identical.

```python
class _SeamFakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def __iter__(self):
        return iter(self._events)
    def get_final_message(self):
        return self._final


class _SeamFakeClient:
    def __init__(self, events, final):
        self.calls = []
        outer = self
        class _M:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                return _SeamFakeStream(events, final)
        self.messages = _M()


class _Ev:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Final:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage


def _seam_request(messages=None, anchor=None):
    from services.llm_provider import LLMRequest
    return LLMRequest(
        model="claude-sonnet-5", max_tokens=64,
        system_blocks=[{"type": "text", "text": "SYS", "stable": True}],
        tools=[{"name": "a", "description": "d", "input_schema": {"type": "object"}},
               {"name": "b", "description": "d", "input_schema": {"type": "object"}}],
        tools_stable=True,
        messages=messages if messages is not None else [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": [{"type": "text", "text": "prior"}]},
        ],
        history_stable_anchor=anchor,
    )


def test_anthropic_provider_translates_events_and_cache_markers():
    from services.llm_anthropic import AnthropicProvider

    block = _Ev("content_block_start",
                content_block=_Ev("tool_use", id="tu1", name="a"))
    fake = _SeamFakeClient(
        events=[_Ev("text", text="hi"), _Ev("thinking", thinking="hm"),
                block, _Ev("content_block_delta")],
        final=_Final(content=[{"type": "text", "text": "hi"}],
                     usage=_Ev("u", input_tokens=7, output_tokens=3,
                               cache_read_input_tokens=5,
                               cache_creation_input_tokens=1)),
    )
    events = list(AnthropicProvider(fake).stream(_seam_request(anchor=1)))
    kinds = [e.type for e in events]
    # unhandled SDK events surface as ping (abort latency preserved)
    assert kinds == ["text_delta", "thinking_delta", "tool_use_start",
                     "ping", "message_done"]
    assert events[0].text == "hi"
    assert events[2].tool_use_id == "tu1" and events[2].tool_name == "a"
    done = events[-1]
    assert done.blocks == [{"type": "text", "text": "hi"}]
    assert done.usage == {"input_tokens": 7, "output_tokens": 3,
                          "cache_read_tokens": 5, "cache_create_tokens": 1}

    kw = fake.calls[0]
    # stable → cache_control at the three sites, and NOWHERE in the request
    # does the word "stable" survive (the SDK would 400 on it).
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "stable" not in kw["system"][-1]
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kw["tools"][0]
    marked = kw["messages"][1]["content"][-1]
    assert marked["cache_control"] == {"type": "ephemeral"}
    assert kw["model"] == "claude-sonnet-5" and kw["max_tokens"] == 64


def test_anthropic_provider_maps_exceptions_to_provider_error():
    import pytest
    from services.llm_anthropic import AnthropicProvider
    from services.llm_provider import ProviderError

    class _Boom:
        def __init__(self):
            class _M:
                def stream(self, **kwargs):
                    raise RuntimeError("kaput sk-ant-secret123")
            self.messages = _M()

    with pytest.raises(ProviderError) as ei:
        list(AnthropicProvider(_Boom()).stream(_seam_request()))
    # no real `anthropic` classes matched → internal_error, message redacted
    assert ei.value.kind == "internal_error"
    assert "sk-ant-secret123" not in ei.value.message


def test_chat_service_seam_aliases_point_at_llm_anthropic():
    from services import chat_service, llm_anthropic
    assert chat_service._build_anthropic_client is llm_anthropic.build_client
    assert chat_service._map_sdk_exception is llm_anthropic.map_sdk_exception
    assert chat_service._serialise_for_anthropic is llm_anthropic.serialise_block
    assert (chat_service._with_history_cache_breakpoint
            is llm_anthropic.with_history_cache_breakpoint)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v -k anthropic`
Expected: FAIL — `No module named 'services.llm_anthropic'`

- [ ] **Step 3: Write the module (moves + the stream translation)**

Move the four symbols named in **Files** out of `chat_service.py` verbatim
(docstrings included — they carry incident history), renaming:
`_build_anthropic_client`→`build_client`, `_map_sdk_exception`→`map_sdk_exception`
(its `_redact_for_log` calls become `redaction.redact_for_log`),
`_serialise_for_anthropic`→`serialise_block`,
`_with_history_cache_breakpoint`→`with_history_cache_breakpoint`. Then add:

```python
"""
Everything Anthropic-SDK-shaped, behind the LLMProvider seam.

Moved verbatim from chat_service (2026-08-13 seam plan): build_client,
map_sdk_exception, serialise_block, with_history_cache_breakpoint — their
docstrings carry the incident history and stay with them. New here:
AnthropicProvider, which translates the neutral LLMRequest (stable
annotations) into the SDK call (cache_control) and SDK stream events into
the closed LLMEvent vocabulary. The `anthropic` package is imported lazily,
never at module import time.
"""
from __future__ import annotations

from typing import Any, Iterator

from services import redaction
from services.llm_provider import LLMEvent, LLMRequest, ProviderError

# ... the four moved functions here ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: Any) -> None:
        # An already-constructed SDK client (or a test fake with the same
        # .messages.stream(**kwargs) shape). Construction stays OUTSIDE the
        # provider so chat_service._build_anthropic_client remains the one
        # monkeypatch/injection surface the test suite pins.
        self._client = client

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        # stable → cache_control, at the three request sites. The `stable`
        # key must not reach the SDK (unknown fields 400).
        system = []
        for blk in request.system_blocks:
            out = {k: v for k, v in blk.items() if k != "stable"}
            if blk.get("stable"):
                out["cache_control"] = {"type": "ephemeral"}
            system.append(out)
        tools = list(request.tools)
        if tools and request.tools_stable:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        messages = with_history_cache_breakpoint(
            request.messages, request.history_stable_anchor
        )
        try:
            with self._client.messages.stream(
                model=request.model,
                max_tokens=request.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", None)
                    if etype == "text":
                        yield LLMEvent(type="text_delta",
                                       text=getattr(event, "text", "") or "")
                    elif etype == "thinking":
                        yield LLMEvent(type="thinking_delta",
                                       text=getattr(event, "thinking", "") or "")
                    elif etype == "content_block_start":
                        blk = getattr(event, "content_block", None)
                        if getattr(blk, "type", None) == "tool_use":
                            yield LLMEvent(
                                type="tool_use_start",
                                tool_use_id=getattr(blk, "id", "") or "",
                                tool_name=getattr(blk, "name", "") or "",
                            )
                        else:
                            yield LLMEvent(type="ping")
                    else:
                        # Surface every other SDK event as ping so the
                        # harness's per-event abort check keeps its latency.
                        yield LLMEvent(type="ping")
                final = stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — mapped to neutral kind
            kind, msg = map_sdk_exception(exc)
            raise ProviderError(kind, msg) from exc

        usage = getattr(final, "usage", None)
        usage_out = {}
        if usage is not None:
            usage_out = {
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "cache_read_tokens": int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_create_tokens": int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0),
            }
        yield LLMEvent(
            type="message_done",
            blocks=[serialise_block(b)
                    for b in (getattr(final, "content", []) or [])],
            usage=usage_out,
        )
```

In `chat_service.py`, replace the four moved definitions with aliases at the
same locations:

```python
from services.llm_anthropic import (  # moved 2026-08-13 (provider seam)
    build_client as _build_anthropic_client,
    map_sdk_exception as _map_sdk_exception,
    serialise_block as _serialise_for_anthropic,
    with_history_cache_breakpoint as _with_history_cache_breakpoint,
)
```

- [ ] **Step 4: Run seam tests + the suites that pin the moved symbols**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py pypsa-gui/backend/tests/test_chat_thinking_blocks.py pypsa-gui/backend/tests/test_chat_message_trim.py -v`
Expected: PASS — the thinking-block and trim suites exercise
`_serialise_for_anthropic` / `_with_history_cache_breakpoint` through the
aliases.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/llm_anthropic.py pypsa-gui/backend/services/chat_service.py pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "refactor(chat): AnthropicProvider — SDK code relocated behind the seam"
```

---

### Task 5: Rewire `_run_turn_body` onto the seam

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py`
  (`run_turn` signature `:1912`; `_run_turn_body` — the request-build site
  `:2239-2258`, the stream/retry loop `:2260-2391`, the usage/final block
  `:2393-2434`; module docstring `:11`)
- Test: existing chat suites (unmodified) + one new seam test

**Interfaces:**
- Consumes: `AnthropicProvider`, `FakeProvider`, Task 2 types.
- Produces: `run_turn(session, message, *, client=None, provider=None, …)` —
  new optional `provider` kwarg; when given it wins over `client`. Task 7
  runs the harness through it.

- [ ] **Step 1: Write the failing test (the provider injection seam)**

```python
def test_run_turn_accepts_a_provider_and_emits_identical_frames():
    from services import chat_service
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    session = chat_service.ChatSession(model="claude-sonnet-5")
    fake = FakeProvider([
        {"events": [LLMEvent(type="text_delta", text="he"),
                    LLMEvent(type="text_delta", text="llo")],
         "blocks": [{"type": "text", "text": "hello"}],
         "usage": {"input_tokens": 5, "output_tokens": 2}},
    ])
    events = list(chat_service.run_turn(session, "hi", provider=fake))
    names = [n for n, _ in events]
    assert names[0] == "session_init"
    assert names.count("token") == 2
    assert ("turn_done" in names) and (names[-1] == "session_done")
    # the request carried the stable annotations (cache-cost guard)
    req = fake.requests[0]
    assert req.system_blocks[-1]["stable"] is True
    assert req.tools_stable is True
    assert req.max_tokens == chat_service.MAX_OUTPUT_TOKENS_PER_TURN
    assert len(req.tools) == 117
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py::test_run_turn_accepts_a_provider_and_emits_identical_frames -v`
Expected: FAIL — `run_turn() got an unexpected keyword argument 'provider'`

- [ ] **Step 3: Rewire**

3a. `run_turn` / `_run_turn_body` signatures gain `provider: Any | None = None`
(threaded through like `client`). Provider resolution, replacing the current
`client, err = _build_anthropic_client()` block (`:2075-2085`):

```python
    if provider is None:
        if client is None:
            client, err = _build_anthropic_client()
            if client is None:
                # unchanged: typed frame + disabled panel, never a crash
                ...existing error emit, verbatim...
        provider = llm_anthropic.AnthropicProvider(client)
```

(`client=` injected fakes keep working — they get wrapped; their `.calls`
recordings are byte-identical because Task 4's provider passes the same
kwargs.)

3b. Request build (replacing `:2246-2258`): system/tools lose their inline
`cache_control` and gain `stable` annotations; the anchor moves into the
request:

```python
        system_blocks = [{"type": "text", "text": system_prompt, "stable": True}]
        request = llm_provider.LLMRequest(
            model=session.model,
            max_tokens=MAX_OUTPUT_TOKENS_PER_TURN,
            system_blocks=system_blocks,
            tools=tools,
            tools_stable=True,
            messages=messages,
            history_stable_anchor=history_cache_anchor,
        )
```

Keep the cache-economics comment block (`:2239-2245`) — reworded one line:
the translation now happens in `llm_anthropic`.

3c. The attempt loop (`:2274-2327`) becomes event-vocabulary-driven. The
retry/A8/terminal `except` block (`:2328-2391`) is UNCHANGED except its first
two lines:

```python
            final_blocks: list[dict[str, Any]] = []
            final_usage: dict[str, int] = {}
            try:
                request.model = session.model  # A8 fallback re-read per attempt
                for ev in provider.stream(request):
                    if session.abort_event.is_set():
                        yield "session_done", {"reason": "aborted"}
                        return
                    if ev.type == "text_delta":
                        emitted_this_attempt = True
                        yield "token", {"delta": ev.text}
                    elif ev.type == "thinking_delta":
                        emitted_this_attempt = True
                        yield "thinking", {"delta": ev.text}
                    elif ev.type == "tool_use_start":
                        emitted_this_attempt = True
                        yield "tool_preparing", {
                            "tool_use_id": ev.tool_use_id,
                            "tool_name": ev.tool_name,
                        }
                    elif ev.type == "message_done":
                        final_blocks = ev.blocks
                        final_usage = ev.usage
                    # "ping": abort-check only, no frame
                break  # stream completed — leave the retry loop
            except llm_provider.ProviderError as exc:
                error_kind, msg = exc.kind, exc.message
                ...rest of the except block verbatim (retry, A8, terminal)...
```

3d. Usage + final-message block (`:2393-2416`): replace the
`getattr(final_message, ...)` reads with the dicts:

```python
        if final_usage:
            session.accrue_usage(
                input_tokens=final_usage.get("input_tokens", 0),
                output_tokens=final_usage.get("output_tokens", 0),
                cache_read_tokens=final_usage.get("cache_read_tokens", 0),
                cache_create_tokens=final_usage.get("cache_create_tokens", 0),
            )
            _metric_add_tokens(final_usage.get("input_tokens", 0),
                               final_usage.get("output_tokens", 0))

        assistant_blocks = final_blocks  # already serialised by the provider
```

Everything after (`_sanitise_history_message` onward) is untouched. Update
the module docstring line `:11` ("Anthropic SDK is STILL…") to name the seam.

- [ ] **Step 4: Behaviour-preservation gate — the whole point of this plan**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_chat_e2e.py pypsa-gui/backend/tests/test_chat_sse.py pypsa-gui/backend/tests/test_chat_thinking_blocks.py pypsa-gui/backend/tests/test_chat_track_a_e2e.py pypsa-gui/backend/tests/test_chat_message_trim.py pypsa-gui/backend/tests/test_llm_provider_seam.py -q`
Expected: PASS with **zero modified chat tests**. A failure here means the
seam changed behaviour — fix the seam, never the test.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/chat_service.py pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "refactor(chat): drive run_turn through the LLMProvider seam"
```

---

### Task 6: `OpenAICompatProvider` over httpx

**Files:**
- Create: `pypsa-gui/backend/services/llm_openai_compat.py`
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py`

**Interfaces:**
- Consumes: Task 2 types; `redaction.redact_secrets_in_str`; `httpx`
  (dev-env dependency; shipping it in `gui-requirements.txt` is plan 2's
  first packaging task — this provider is dev/test-only until then, and its
  `httpx` import is function-local so the module imports cleanly in the
  frozen app).
- Produces: `OpenAICompatProvider(base_url, api_key=None, http_client=None)`
  with `.name == "openai-compat"`, `.stream(request)` per protocol.
  `http_client` accepts an `httpx.Client` (tests pass
  `httpx.Client(transport=MockTransport(...))`).

- [ ] **Step 1: Write the failing tests**

```python
def _sse_bytes(*chunks):
    out = b""
    for c in chunks:
        out += b"data: " + c + b"\n\n"
    return out + b"data: [DONE]\n\n"


def test_openai_compat_streams_text_tools_reasoning_and_usage():
    import httpx, json
    from services.llm_openai_compat import OpenAICompatProvider

    body = _sse_bytes(
        b'{"choices":[{"delta":{"reasoning_content":"hmm"}}]}',
        b'{"choices":[{"delta":{"content":"he"}}]}',
        b'{"choices":[{"delta":{"content":"llo"}}]}',
        b'{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        b'"function":{"name":"list_projects","arguments":"{\\"li"}}]}}]}',
        b'{"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"mit\\":2}"}}]}}]}',
        b'{"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":4}}',
    )
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(req.content)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})

    provider = OpenAICompatProvider(
        "http://localhost:11434/v1", api_key="k",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    req = _seam_request()
    events = list(provider.stream(req))
    kinds = [e.type for e in events]
    assert kinds[0] == "thinking_delta" and events[0].text == "hmm"
    assert kinds.count("text_delta") == 2
    assert kinds[-1] == "message_done"
    done = events[-1]
    assert {"type": "text", "text": "hello"} in done.blocks
    tool = [b for b in done.blocks if b["type"] == "tool_use"][0]
    assert tool["id"] == "c1" and tool["name"] == "list_projects"
    assert tool["input"] == {"limit": 2}
    assert done.usage == {"input_tokens": 11, "output_tokens": 4}
    # request translation
    sent = captured["json"]
    assert captured["auth"] == "Bearer k"
    assert sent["stream"] is True
    assert sent["messages"][0]["role"] == "system"
    assert sent["tools"][0]["function"]["name"] == "a"
    assert "cache_control" not in json.dumps(sent)  # stable dropped, silently


def test_openai_compat_maps_connect_and_status_errors():
    import httpx, pytest
    from services.llm_openai_compat import OpenAICompatProvider
    from services.llm_provider import ProviderError

    def refuse(req):
        raise httpx.ConnectError("refused")

    p = OpenAICompatProvider(
        "http://localhost:11434/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(ProviderError) as ei:
        list(p.stream(_seam_request()))
    assert ei.value.kind == "unreachable"

    for status, kind in [(401, "unauthorized"), (429, "rate_limited"),
                         (404, "invalid_request"), (500, "upstream_error")]:
        pp = OpenAICompatProvider(
            "http://h/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(
                lambda r, s=status: httpx.Response(s, json={"error": {}}))))
        with pytest.raises(ProviderError) as ei:
            list(pp.stream(_seam_request()))
        assert ei.value.kind == kind, status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v -k openai`
Expected: FAIL — `No module named 'services.llm_openai_compat'`

- [ ] **Step 3: Write the module**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v`
Expected: PASS (all seam tests so far)

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/llm_openai_compat.py pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "feat(chat): OpenAICompatProvider — chat-completions SSE over httpx"
```

---

### Task 7: The cross-provider seam test

**Files:**
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py`

**Interfaces:**
- Consumes: `run_turn(..., provider=)` (Task 5), both providers, MockTransport.
- Produces: the seam spec's core assertion — one harness scenario, two
  providers, identical harness behaviour.

- [ ] **Step 1: Write the failing test**

```python
def _openai_provider_scripted_hello():
    import httpx
    from services.llm_openai_compat import OpenAICompatProvider
    body = _sse_bytes(
        b'{"choices":[{"delta":{"content":"he"}}]}',
        b'{"choices":[{"delta":{"content":"llo"}}]}',
        b'{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}',
    )
    return OpenAICompatProvider(
        "http://fake/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=body,
                headers={"content-type": "text/event-stream"}))))


def _fake_provider_scripted_hello():
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent
    return FakeProvider([
        {"events": [LLMEvent(type="text_delta", text="he"),
                    LLMEvent(type="text_delta", text="llo")],
         "blocks": [{"type": "text", "text": "hello"}],
         "usage": {"input_tokens": 5, "output_tokens": 2}},
    ])


def test_seam_same_harness_behaviour_across_providers():
    """The seam spec's core assertion: a test that only runs against the
    fake proves nothing about the abstraction."""
    from services import chat_service

    def frames(provider):
        session = chat_service.ChatSession(model="claude-sonnet-5")
        out = list(chat_service.run_turn(session, "hi", provider=provider))
        # session_id differs per run; compare names + token deltas only
        return ([n for n, _ in out],
                [p["delta"] for n, p in out if n == "token"])

    fake_names, fake_tokens = frames(_fake_provider_scripted_hello())
    oai_names, oai_tokens = frames(_openai_provider_scripted_hello())
    assert fake_names == oai_names
    assert fake_tokens == oai_tokens == ["he", "llo"]


def test_stable_markers_reach_all_sites_via_fake():
    """Cache-cost guard: without this, the tenfold input-cost regression is
    invisible (seam spec, capability section)."""
    from services import chat_service
    from services.llm_provider import LLMEvent
    from services.llm_fake import FakeProvider

    session = chat_service.ChatSession(model="claude-sonnet-5")
    fake = FakeProvider([
        {"events": [], "blocks": [{"type": "text", "text": "a"}],
         "usage": {}},
        {"events": [], "blocks": [{"type": "text", "text": "b"}],
         "usage": {}},
    ])
    list(chat_service.run_turn(session, "one", provider=fake))
    list(chat_service.run_turn(session, "two", provider=fake))
    first, second = fake.requests[0], fake.requests[1]
    assert first.system_blocks[-1]["stable"] and first.tools_stable
    assert first.history_stable_anchor is None      # no history yet
    assert second.history_stable_anchor is not None  # anchored to turn 1
    assert 0 <= second.history_stable_anchor < len(second.messages)
```

- [ ] **Step 2: Run to verify current state**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v -k "seam_same or stable_markers"`
Expected: FAIL until Tasks 5–6 are complete; PASS immediately after (this
task may reorder naturally — if it passes first run, that is the GREEN
evidence, note it in the report).

- [ ] **Step 3: Optional live check (skipped when absent)**

Append:

```python
import os
import pytest


@pytest.mark.skipif(
    not os.environ.get("PYPSA_GUI_TEST_OLLAMA_URL"),
    reason="no local OpenAI-compatible endpoint configured")
def test_seam_against_live_local_endpoint():
    from services import chat_service
    from services.llm_openai_compat import OpenAICompatProvider
    provider = OpenAICompatProvider(
        os.environ["PYPSA_GUI_TEST_OLLAMA_URL"],
        api_key=os.environ.get("PYPSA_GUI_TEST_OLLAMA_KEY"))
    session = chat_service.ChatSession(
        model=os.environ.get("PYPSA_GUI_TEST_OLLAMA_MODEL", "qwen3:8b"))
    names = [n for n, _ in chat_service.run_turn(
        session, "Reply with the single word: ok", provider=provider)]
    assert names[0] == "session_init" and names[-1] == "session_done"
    assert "token" in names
```

- [ ] **Step 4: Run the file, confirm the skip is visible**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -v`
Expected: PASS, with `test_seam_against_live_local_endpoint SKIPPED`

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/tests/test_llm_provider_seam.py
git commit -m "test(chat): cross-provider seam test + stable-marker cache guard"
```

---

### Task 8: Full gate + smoke

**Files:** none new.

- [ ] **Step 1: Run the canonical gate**

Run: `pixi run gui-tests` (repo root)
Expected: exit 0. NOT plain pytest — in the default env the 7 pywebview
tests fail by design and would read as seam breakage.

- [ ] **Step 2: Run the identity regression the seam spec names**

Run: `pixi run -e test python pypsa-gui/backend/smoke/regress_chat_acting_identity.py`
Expected: 24/24 (per seam spec §Verification). If the script requires a
live backend or key and cannot run headless, record that verbatim in the
report instead of skipping silently.

- [ ] **Step 3: Verify no accidental frontend or schema drift**

Run: `git diff --stat master...HEAD -- 'pypsa-gui/frontend' 'pypsa-gui/backend/services/chat_tools_schema.py'`
Expected: empty output (this plan never touches either).

- [ ] **Step 4: Commit any straggling fixes; final state clean**

```bash
git status --short   # expect: clean
```

---

## Self-Review (done at planning time)

- **Spec coverage:** protocol + three implementations (Tasks 2–6), `stable`
  annotation with per-site guard (Tasks 4, 5, 7), error taxonomy incl. the
  non-retryable `invalid_request` rule preserved verbatim (Task 5 keeps the
  except-block), behaviour preservation (Task 5 Step 4, Task 8), seam test
  across providers incl. live-skip (Task 7). Out of scope confirmed out:
  tool-set reduction, `StreamRequest.script` removal, UI.
- **Known deltas from the seam spec, deliberate:** line numbers updated to
  the current file (spec cited a 3,054-line file; it is 3,247 today);
  `unreachable` added to the kinds now rather than in plan 2 (the OpenAI
  provider needs it to exist); `_serialise_for_anthropic` moves in Task 4
  (the spec's "event loop" site) with an alias, because
  `test_chat_thinking_blocks.py` pins it by name.
- **Type consistency:** `LLMEvent.type` strings in Tasks 3–7 all ∈
  `EVENT_TYPES` (Task 2); usage dict keys identical at producer (Tasks 4, 6)
  and consumer (Task 5); `FakeProvider.requests` field name consistent
  (Tasks 3, 5, 7).
