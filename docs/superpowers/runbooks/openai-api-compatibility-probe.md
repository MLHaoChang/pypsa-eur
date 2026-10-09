# OpenAI API compatibility check — 2026-10-09

The chatbot uses the Chat Completions API through
`backend/harness/providers/openai_compat.py`. Anthropic continues to use its
existing provider; tool declarations, handlers, confirmation rules, and the
provider-neutral turn loop are unchanged.

## Fixes

- Keep the connection-test HTTP client open until its one token-parameter retry
  finishes. Previously an owned client was closed before the retry.
- Treat HTTP 401 and 403 as authentication/authorization failures, without retry.
- Reject EOF without a completion marker. Accept either `[DONE]` or a
  `finish_reason`, including compatible servers that omit `[DONE]`.
- Refuse tool calls stopped by `length` or `content_filter`, malformed JSON
  arguments, and arguments whose decoded value is not an object. No completed
  assistant message is emitted, so these calls cannot reach the tool handlers.
- Add current OpenAI API IDs to the preset suggestions and explain the labels.
- Set `reasoning_effort="none"` for `gpt-6-luna` function tools at the official
  OpenAI Chat Completions endpoint. A live HTTP 400 identified this requirement;
  other models and compatible servers retain their existing parameters.

## Model ID evidence

Checked against the vendors' current generated SDK types on 2026-10-09:

- [OpenAI ChatModel](https://github.com/openai/openai-python/blob/main/src/openai/types/shared/chat_model.py)
- [Anthropic Model](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/model.py)

All suggested OpenAI and Anthropic IDs in `backend/presets.json` occur in these
sources. Astra maps to `gpt-6-astra`; Sol can mean `gpt-6.1-sol` or `gpt-6-sol`.
Use the exact ID, not the bare label. The existing `gpt-5.6-*` suggestions and
Claude defaults (`claude-sonnet-5`, `claude-opus-5`) are also listed.

SDK inclusion establishes a recognized API ID, not access for a particular
account. The Settings connection test fetches `/models` to display accessible
IDs; its completion verdict remains the authoritative connection check.

Model switching within a provider is supported. Switching between OpenAI and
Anthropic requires a new chat under the existing history compatibility contract;
a refusal leaves the original session and its history intact.

## Automated checks

From `pypsa-gui/backend`, using a Python environment with the backend/test
requirements and the repository's PyPSA/pandas/linopy versions:

```sh
python -m pytest tests/test_chat_*.py tests/test_llm_*.py \
  tests/test_openai_compatibility.py tests/test_harness_layout.py \
  tests/test_stub_openai_endpoint.py
```

The focused `test_openai_compatibility.py` includes fragmented arguments through
real `list_components` execution, tool-result replay and streamed continuation,
invalid arguments, truncation, EOF, authentication failures, owned-client retry,
and Sol/Astra switching followed by a fresh Anthropic chat.

From `pypsa-gui/frontend`:

```sh
npm test -- src/api/chat.test.ts src/api/llmSettings.test.ts \
  src/store/chatStore.test.ts src/store/chatStore.sendRequest.test.ts \
  src/utils/chatUi.test.ts src/components/ChatPanel \
  src/components/AssistantModelSettings.test.tsx \
  src/components/ApiKeySetup.test.tsx src/components/ChatLaunchGreeting \
  src/pages/ProjectsHomePage.chatBridge.test.tsx
npm run build
```

### Results in the current environment

| Check | Result |
| --- | --- |
| Focused OpenAI regressions | 19 offline checks passed; live probe passed separately |
| Broad backend chat/provider/harness suite | 1,906 passed; 6 skipped; 1 failed; 23 setup errors initially |
| Gridspine and OpenAI recheck after dependency setup | 47 passed; 1 live probe skipped; all 23 setup errors resolved |
| Frontend chat/settings tests | 630 passed across 30 files |
| Frontend TypeScript and production build | Passed; existing Vite chunk warnings |
| Diff whitespace check | Passed |
| Live OpenAI model discovery after publication | Passed: HTTP 200; 127 model IDs listed |
| Targeted backend provider/profile/model/layout recheck | 289 passed; 4 gated live/local probes skipped |
| Final smoke-test controls and harness-layout recheck | 42 passed; 1 live probe skipped |
| Live OpenAI tool execution/continuation probe | Passed on `gpt-6-luna`: 2 completion requests, 888 input tokens, 29 output tokens |

The remaining failure is
`test_chat_uploads.py::TestUploadsEndpoints::test_post_docx_zip_upgrade`.
Its synthetic ZIP fixture is classified as `application/octet-stream` by this
environment's libmagic, so the unchanged upload route rejects it. The test fails
in isolation too; the MIME classification was independently reproduced without
running the chatbot. No upload code or tool handler was changed to suppress it.

The backend environment uses the repository's pinned PyPSA 1.1.2, pandas 2.3.3,
linopy 0.8.0, pandapower 3.1.2, and lightsim2grid 0.10.1, plus the backend
requirements and test dependencies. It is a pip test environment, not a full
recreation of the pixi lock. Existing live/local-endpoint probes were skipped
where their opt-in settings were absent; skips are not live coverage.

## Minimal live OpenAI tool probe

Allow `api.openai.com` through the environment's supported network configuration
and supply a key through its credential configuration. Managed environments
reserve the `OPENAI_` prefix for secret targets: use
`PYPSA_GUI_OPENAI_API_KEY`, restricted to `api.openai.com`, and map it to the
application's expected variable for this command. Never place a literal key
in this file or a command argument. Then, from `pypsa-gui/backend`:

```sh
OPENAI_API_KEY="${OPENAI_API_KEY:-${PYPSA_GUI_OPENAI_API_KEY}}" \
  PYPSA_GUI_TEST_LIVE_OPENAI_TOOLS=1 \
  python -m pytest tests/test_openai_compatibility.py \
  -k live_openai_tool_execution_and_continuation -rs
```

The default API model is `gpt-6-luna`; override it with
`PYPSA_GUI_TEST_LIVE_OPENAI_MODEL` if your account exposes another model. The
probe creates a throwaway network with one bus and a saved OpenAI profile. It
uses the production profile/key/provider resolution, offers only the read-only
`list_components` tool, executes it, returns its result to the model, and checks
that the streamed continuation names the bus. It uses a short smoke-test system
prompt, forces the first function call, and disables further calls for the
continuation. It allows at most two completion requests with retries disabled
and caps output at 128 tokens per request. An offline test verifies these
controls without spending API credit. This probe verifies profile/provider
resolution, request translation, streaming, real tool execution, and result
continuation; it does not evaluate the full application prompt or tool catalogue
against a live model. This test spends API credit and skips by default.

### Live verification after publication — 2026-10-09

The published environment reports the `PYPSA_GUI_OPENAI_API_KEY` binding ready
and the network policy enforced. The key was mapped to `OPENAI_API_KEY` only
for the test command; no credential value was printed or written into this
repository.

Authenticated `GET /v1/models` succeeded with HTTP 200 and listed 127 models.
The returned IDs include `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`,
`gpt-6-luna`, `gpt-5.6-sol`, and `gpt-4.1-mini`. This confirms that Astra/Sol
are actual API IDs exposed by this account, in addition to their SDK listing.

The initial quota blocker (`credit_balance_exhausted` / `insufficient_quota`)
cleared. A subsequent HTTP 400 explained that Luna's default reasoning mode
does not support function tools on Chat Completions and explicitly prescribed
`reasoning_effort="none"`. The adapter now supplies that parameter only for
Luna tools on the official OpenAI endpoint. The associated regression checks
also verify that other models, tool-free requests, and compatible servers keep
their existing parameters.

The final gated live probe passed: 1 passed, 19 deselected. It made exactly
two completion requests and reported 888 input tokens and 29 output tokens.
It streamed a function call, parsed the arguments, executed the actual
`list_components` handler, replayed its result, streamed an answer containing
`OpenAI_probe_bus`, and received usage reporting and `turn_done`. The earlier
two-call attempt stopped when the model requested a further tool call; the
deterministic smoke controls now keep this test within its request budget.

No further live calls were made after the successful run. The unrelated DOCX
fixture/libmagic failure described above remains. No Anthropic live call was
made. The previous failed environment was not accessed or modified.
