# Running the ADR-0002 openai-wire live probe against a local endpoint

ADR-0002 says a green suite does not verify a chat change, because no test in
`backend/tests/` constructs a real client. The openai-wire probe closes that
for the OpenAI-compatible wire **without an API key and without spending
credit**, by pointing it at a local server.

Run it on any machine that can reach a model. It was first run this way on
2026-09-04 and passed — see "What this does and does not establish".

## Quickest path (Ollama)

```bash
ollama serve &                 # defaults to http://localhost:11434
ollama pull qwen3:0.6b         # any small model is fine

pixi run -e test env \
  PYPSA_GUI_TEST_LIVE_OPENAI_PROFILE=local-ollama \
  PYPSA_GUI_TEST_LIVE_OPENAI_MODEL=qwen3:0.6b \
  NO_PROXY=127.0.0.1,localhost \
  python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py \
  -k live_probe_openai -v
```

`_MODEL` is what makes this work unattended: the probe creates the profile
itself, in the test session's own app-data dir.

**Why that matters — the probe's original instructions could not work.**
They say "save an openai-wire profile, then set
`PYPSA_GUI_TEST_LIVE_OPENAI_PROFILE=<its profile id>`". But `conftest.py`
pins `PYPSAGUI_APP_DATA_DIR` to a fresh `mkdtemp` at import time, so a profile
saved beforehand is invisible to the test process. Following the instructions
literally always failed on a missing profile. (Before C-4 it was worse: the
old `resolve_profile` fell through to the ACTIVE profile, so this probe
quietly ran on the built-in **Anthropic** profile and reported a result for a
wire it had never touched.)

Optional overrides: `_BASE_URL` (default: the preset's own endpoint),
`_PRESET` (default `ollama`), `_AUTH` (default `none`).

## Other endpoints

| Endpoint | Extra env |
|---|---|
| LM Studio | `_BASE_URL=http://localhost:1234/v1` `_PRESET=lmstudio` |
| Moonshot / DashScope | `_PRESET=moonshot` (or `dashscope`), `_AUTH=bearer`, and the preset's key in its slot |
| OpenAI | `_PRESET=openai`, `_AUTH=bearer`, `OPENAI_API_KEY=…` |

Only the **OpenAI** row settles C-2's remaining question — whether OpenAI
rejects the *presence* of `max_tokens`. Every other row probes the wire
itself: transport, SSE framing, tool-call assembly, usage, profile store and
provider construction.

## What this does and does not establish

Run on 2026-09-04 against Ollama 0.32.15 the probe passed, with frames
`session_init → token ×3 → turn_done` through `OpenAICompatProvider` over a
real socket. That is the first time any token has been streamed from a live
model through this branch.

It establishes:

* the openai wire is **not** dead on arrival — a real turn completes;
* a turn really does end on `turn_done`, the pinned invariant, on a live call;
* the C-2 payload is correct on the wire: exactly one completion-length
  parameter, captured and inspected mid-flight;
* real failures are handled correctly — a context-overflow 400 from the server
  surfaced as a terminal `invalid_request` with clean
  `session_init → error → session_done` frames and no key material in the log.

It does **not** establish:

* anything about OpenAI's own parameter validation. That remains documentary,
  and C-2's `max_completion_tokens` branch is still unprobed against the
  vendor;
* anything about the **anthropic** wire, which is still UNPROBED — its stored
  key is revoked. That probe needs a valid `ANTHROPIC_API_KEY` and is the one
  the zero-config default actually uses.

A local endpoint that speaks the OpenAI protocol is a real server, but it is
not the vendor. Do not record this as closing ADR-0002 outright.
