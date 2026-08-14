# LLM provider seam — design

**Status:** approved in brainstorming, 2026-08-05 · **Sequence:** step (b) of (a)→(b)→(c)

Split the agent harness from the language model, so the harness is provider-
agnostic and Claude becomes one implementation among several — including local
models later.

## What is actually coupled, measured

`services/chat_service.py` is 3,054 lines. The Anthropic SDK appears in four
places:

| Site | What it does |
|---|---|
| `_build_anthropic_client` (~:1472) | constructs the client, returns typed `missing_api_key` / `sdk_not_installed` / `unauthorized` |
| `_map_sdk_exception` (~:1500) | maps SDK exception classes to error kinds |
| `client.messages.stream(...)` (:2118) | the request |
| the event loop (:2128+) | reads `event.type` — `text`, `thinking`, `content_block_start` |

Plus `services/chat_tools_schema.py`, whose own docstring says
*"Anthropic-format tool schemas"* — `{name, description, input_schema}`.

**The seam is narrower than it looks.** Everything else — session state, tool
dispatch, confirmation gating, SSE framing, audit prefixes, the 139-tool
registry — is already provider-neutral. This is an extraction, not a rewrite.

### The seam that is *not* narrow, and is out of scope

`services/chat_tools.py` contains **104 function-local imports of `routers.*`**
against 23 of services/db/pypsa. The tool layer reaches PyPSA *through the HTTP
route handlers*, which is why `_route()` exists — a shim faking FastAPI
dependency injection so the agent can call web handlers as functions.

All three production defects found in the 2026-08-04 effort came from that
inversion: `F1` (every project tool 401'd), `S9.1` (seven tools with unresolved
`Depends`, four mis-binding positionals), `F3` (`_route` omitting `session`).

This is the *domain* seam. It is real, it is where the bugs are, and it is
**deliberately excluded here**. Rewiring 139 tools while also cutting the model
seam would put both changes in one unreviewable diff, and the model seam does
not touch those tools at all. Its own spec, its own review.

## Architecture

Three layers, with the dependency arrow pointing one way only:

```
domain      services/pypsa_service, network ops, plain PyPSA
   ▲
harness     tool registry · dispatch · session · confirmation · SSE
   ▲
provider    AnthropicProvider │ OpenAICompatProvider │ FakeProvider
```

The harness depends on a `LLMProvider` protocol. No provider name, SDK import,
or wire-format detail appears above the provider layer.

### The provider protocol

```python
class LLMProvider(Protocol):
    name: str
    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]: ...
```

`LLMRequest` carries neutral data: system blocks, message history, the tool
list as `(name, description, json_schema)` triples, and the model id.
`LLMEvent` is a small closed vocabulary derived from what the harness already
yields downstream — `text_delta`, `thinking_delta`, `tool_use`, `usage`,
`stop`, `error`.

> **As built (2026-08-14):** the shipped vocabulary is `text_delta`,
> `thinking_delta`, `tool_use_start`, `ping`, `message_done`
> (`services/llm_provider.py:EVENT_TYPES`). `usage` + `stop` merged into the
> terminal `message_done` (blocks + usage dict); `error` became the
> `ProviderError` exception, which the harness maps — with a catch-all for
> anything a provider fails to map; `ping` surfaces every otherwise-unhandled
> upstream event so the harness's per-event abort check keeps its latency.
> Plan 2 should read this block, not the list above.

`chat_tools_schema.TOOLS` already stores the neutral triple; only the wrapper
differs per provider (Anthropic takes `input_schema`, OpenAI nests under
`function.parameters`). No tool definitions change.

### Capability handling — semantic annotation, provider translation

This is the decision that carries the most money.

Prompt caching is expressed today as Anthropic `cache_control: ephemeral`
markers at four sites: the system block (:2086), the last tool (:2094), and
two paths for the last message (:1423, :1432). The code documents the stakes —
cache_read at $0.30/MTOK against raw input at $3.00/MTOK.

A lowest-common-denominator interface has nowhere to put those markers. It
would drop them, nothing would fail, and input cost would rise roughly tenfold
with the bill as the only signal.

**The harness therefore annotates intent, never mechanism.** Blocks carry
`stable=True` — meaning "this prefix does not change between turns". The
harness never says "cache".

* `AnthropicProvider` translates `stable` → `cache_control: ephemeral`.
* `OpenAICompatProvider` ignores it, or maps it to a KV-cache prefix hint.
* `FakeProvider` records it, so tests can assert the markers survive.

Reasoning effort is handled the same way: the harness asks for a level, each
provider expresses or drops it.

The `stable` markers map one-to-one onto the four existing sites, so this is
closer to a rename than a redesign.

### Error taxonomy

The harness owns the neutral kinds already in use — `missing_api_key`,
`sdk_not_installed`, `unauthorized`, `rate_limited`, `upstream_error`,
`invalid_request`, `internal_error`. Each provider maps its own exceptions
into them. `_map_sdk_exception` moves into `AnthropicProvider` essentially
unchanged.

`invalid_request` (any 4xx that is not a 429) is the one kind whose
retryability is load-bearing rather than cosmetic: it is deliberately absent
from `_RETRYABLE_SDK_KINDS`, because a malformed request is deterministic and
retrying it is guaranteed waste. Any provider that maps a client-side
rejection onto a retryable kind reintroduces the thinking-block incident's
four-calls-before-the-user-sees-anything behaviour.

## The three implementations

**`AnthropicProvider`** — the existing code, relocated. Behaviour-preserving:
same error kinds, same cache markers, same event stream. The SSE frames the
frontend receives must be byte-identical, because `ChatPanel` parses them and
none of that is in scope.

**`FakeProvider`** — deterministic, scripted: "emit this text, then request
`ui_open_panel` with these arguments, then stop." No network, no API key.

This is the seam the existing `StreamRequest.script` stub *should* have used.
That stub fakes the output **frames**, so it bypasses the agent loop and
cannot exercise tool dispatch, confirmation gating, or cache markers. A
provider-level fake runs the real loop. The `script` field stays for now —
removing it is a separate cleanup with its own callers to check.

**`OpenAICompatProvider`** — Ollama, llama.cpp and LM Studio all speak the
OpenAI wire format, so one implementation covers essentially every local model
worth targeting.

Its purpose is not primarily to ship local-model support. **It is the only
thing that proves the interface is not secretly Anthropic-shaped.** A fake we
wrote ourselves cannot reveal that; it encodes the same assumptions. Two of
the differences it forces into the open early: tool-call framing differs
structurally, and there is no `cache_control` at all — which is precisely the
case the `stable` annotation exists to absorb.

### A known limitation, recorded rather than discovered later

Local models are substantially worse than Claude at selecting from 139 tools.
The seam will work; the *assistant* on a local model will likely need a
reduced tool set to be usable. "Runs on a local model" and "is good on a local
model" are different milestones, and only the first is in scope.

## Verification

* Backend pytest exits 0. The chat suites are the regression surface, and they
  pass today without an API key, so any new key requirement is a defect.
* `regress_chat_acting_identity.py` stays 24/24 — it exercises the tool path
  the harness still owns.
* **Behaviour-preservation:** SSE frames for a scripted turn are compared
  before and after the extraction. Equality is the assertion; a refactor that
  changes the wire format silently breaks a frontend that is not in scope.
* **The seam test:** the same harness-level test runs against `FakeProvider`
  and `OpenAICompatProvider` (against a local endpoint, skipped when absent)
  and asserts identical harness behaviour. A test that only ever runs against
  the fake proves nothing about the abstraction.
* **Cache markers survive:** `FakeProvider` records the request, and a test
  asserts `stable` reaches all four sites. Without this the tenfold cost
  regression is invisible.

## Out of scope

The domain seam (104 router imports). Tool-set reduction for local models.
Removing `StreamRequest.script`. Any UI change — the frontend must not be able
to tell this happened.
