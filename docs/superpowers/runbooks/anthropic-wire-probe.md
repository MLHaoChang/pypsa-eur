# Running the ADR-0002 anthropic-wire live probe

ADR-0002 says a green suite does not verify a chat change, because no test in
`backend/tests/` constructs a real client. This is the probe that closes it for
the **anthropic** wire — the one the zero-config default actually uses, so it
is the more load-bearing of the two.

It **spends real API credit**, which is why it is opt-in. One turn is a few
output tokens; see "What a run costs" for the part that is not few.

Run first on 2026-09-04 (failed — revoked key) and again on 2026-09-09
(**passed**). See "What this does and does not establish".

## Running it

```bash
PYPSA_GUI_TEST_LIVE_ANTHROPIC=1 ANTHROPIC_API_KEY=sk-ant-… \
  pixi run -e test python -m pytest \
  pypsa-gui/backend/tests/test_llm_provider_seam.py -k live_probe_anthropic -v
```

`-e test` is the canonical environment — the one `pixi run gui-tests` resolves
to. Both variables are required: the skip is gated on the pair, so a key alone
does not enable the probe and `=1` alone does not either.

Nothing else needs setting up. Unlike the openai probe there is no profile to
create: `anthropic-sonnet` is a built-in, synthesized in code whether or not
`llm-profiles.json` exists, and its `key_env` derives to `ANTHROPIC_API_KEY`.

`ANTHROPIC_BASE_URL`, if set in the environment, is honoured — the provider
constructs `anthropic.Anthropic()` with no arguments, so the SDK reads it.
Check it before trusting a result: a probe that passed against a proxy or a
gateway has probed that, not the vendor.

## What a run costs

Measured on the 2026-09-09 run, from the `turn_done` frame's own usage (this
was the second call of the session, so the prefix was already warm — a cold
first call moves the same tokens into `cache_create`):

```
input_tokens 89 | output_tokens 4 | cache_create 2278 | cache_read 22358
```

The test's docstring calls this "cheap by design", which is true of the
*completion* and misleading about the *request*. A turn carries the full
assistant system prompt and all 121 tool schemas — roughly 24.6k tokens of
prefix — because the probe deliberately drives the production path rather than
a trimmed one. That is the point of the probe, so the number is a fact to know
rather than a defect to fix; it is priced as cache traffic, not fresh input.

## What this does and does not establish

The 2026-09-09 run passed. Through the production path — profile store →
`_provider_for_profile` → `run_turn`, no injected client:

```
profile   : anthropic-sonnet | wire: anthropic | model: claude-sonnet-5
            | key_env: ANTHROPIC_API_KEY
provider  : AnthropicProvider  (built with no error)
HTTP      : POST https://api.anthropic.com/v1/messages 200
frames    : session_init -> token -> turn_done
token     : {'delta': 'ok'}
```

It establishes:

* the anthropic wire's **success path** — a token has now been streamed from a
  live vendor model through this branch. Until this run, only the *failure*
  path had ever been exercised live (a 401 on the revoked key, 2026-09-04);
* the pinned invariant holds on a live call: a turn ends on `turn_done`;
* profile resolution, key-slot derivation and provider construction all work
  against the real SDK, not just against `FakeProvider` and `MockTransport`;
* usage is reported (`reported: True`) on a real response, not only a
  synthesized one.

It does **not** establish:

* anything about the other wire. The openai wire has its own probe and its own
  runbook (`local-openai-wire-probe.md`), passed against Ollama;
* anything about tool *execution*. The probe asks for one word and says "No
  tools", so the 121 schemas are sent and never exercised. A live tool round
  trip is still unprobed;
* anything about vision, streaming backpressure, or long turns.

## The one thing that makes a pass meaningless

A skipped probe reads as a pass in a summary line. That is the failure mode
ADR-0002 exists to prevent, and it is why the skip reason says UNPROBED rather
than "not configured". When reporting, quote the result — `1 passed`, with the
frame sequence — never "the suite is green".
