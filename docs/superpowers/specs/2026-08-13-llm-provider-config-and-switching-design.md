# LLM provider configuration and model switching — design

**Status:** approved in brainstorming + grilling 2026-08-13, then adversarially
verified same day (4 agents, findings folded in — see §Verification findings) ·
**Builds on:** [2026-08-05-llm-provider-seam-design.md](2026-08-05-llm-provider-seam-design.md)
(step (b), approved, **not yet implemented**)

Let the assistant run on cloud models beyond Anthropic (OpenAI, Moonshot/Kimi,
Qwen) and on local deployments (Ollama, LM Studio, self-hosted
OpenAI-compatible servers), switchable by the user in a simple fashion, with
the app — and the assistant itself — able to guide the switch.

## Relationship to the provider seam

The seam spec is settled input, not re-litigated here. It extracts the
`LLMProvider` protocol, `AnthropicProvider` / `OpenAICompatProvider` /
`FakeProvider`, the `stable=True` cache annotation, and the neutral error
taxonomy — and it explicitly excludes every UI change. This spec adds the
layer it excluded: who decides *which* provider runs, how that choice is
stored, and how the user is guided through changing it.

**Implementation order is seam first, then this** — two plans. The seam's
behaviour-preservation gate (byte-identical SSE frames for a scripted turn)
must land and pass before any UI depends on the new layer.

Two corrections to the seam spec, measured 2026-08-13: the tool registry holds
**117** entries (`TOOLS` and `DISPATCHERS` both), not 139. And its inventory of
"four SDK sites" misses a fifth: `reconstruct_network_from_image`
(`services/chat_tools.py:2314-2348`) builds its own Anthropic client and
hardcodes `DEFAULT_MODEL` — a second, profile-blind provider call site this
spec brings under the profile store (§Capability enforcement).

## Architecture — a fourth layer below provider

```
domain      pypsa_service, network ops
   ▲
harness     tool registry · dispatch · session · confirmation · SSE
   ▲
provider    AnthropicProvider │ OpenAICompatProvider │ FakeProvider
   ▲
config      profile store · preset catalogue · widened secret allowlist
```

Config answers one question — which provider, which model, which credentials,
which capabilities — and hands the provider layer a fully-resolved answer. A
provider is **constructed from a resolved profile**; it never reads a file or
an env var itself.

## The profile — the unit the user switches between

```jsonc
{
  "id": "ollama-local",                    // slug [a-z0-9-]
  "label": "Ollama (local)",               // what the dropdown shows
  "preset": "ollama",                      // catalogue id, or "custom"
  "wire": "openai",                        // adapter: "openai" | "anthropic"
  "base_url": "http://localhost:11434/v1",
  "model": "qwen3:8b",                     // always free text
  "capabilities": { "tools": true, "vision": false },
  "fallback_model": null,                  // optional; Anthropic presets ship one
  "max_output_tokens": null                // optional; default = global 8192
}
```

Note what is **not** in the body: `key_env`. The secret slot name is **derived
server-side** — a preset's declared env name (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `MOONSHOT_API_KEY`, `DASHSCOPE_API_KEY`), or
`PYPSA_GUI_LLM_KEY__<SLUG>` (slug upper-cased, `-`→`_`) for custom profiles.
A client-settable `key_env` would be a one-form exfiltration: a custom profile
pointing `base_url` at an attacker host while naming `ANTHROPIC_API_KEY` as
its slot ships the Anthropic key to that host in an `Authorization` header.
Derivation closes it. Env names are always upper-case: on Windows
`os.environ` upper-cases keys, so a mixed-case name would silently break the
`_SHELL_NAMES` precedence check (verified: `app_secrets.py:169,222`).

Two profiles may share a slot (the built-in Anthropic pair does); that is a
supported property. `base_url` is validated to **reject userinfo**
(`https://user:pass@host`) and credential-shaped query params — otherwise a
secret enters the profiles file through the back door. Presets declare
`auth: "bearer" | "none"`; Ollama/LM Studio are `none`, so no-key profiles are
complete, and the "no usable key" banner never fires for them.

Profiles live in `<app-data>/llm-profiles.json` beside `local-settings.json`,
plus one `active_profile_id`. Written with the same `os.open(..., 0o600)` +
re-chmod path `app_secrets._write_managed` uses. No secret ever enters that
file. The store **re-reads per call, never `lru_cache`** — every secrets test
isolates by repointing `PYPSAGUI_APP_DATA_DIR` per test and depends on
call-time reads (verified: `tests/test_app_secrets.py:45`,
`test_local_settings_store.py:40-46`).

Ownership is **instance-wide, desktop-first**: one set of profiles per
install, editable by a super-admin — which the desktop app's single seeded
user is. Server deployments keep today's shape: the admin configures, members
use (members may still pick among configured profiles per session). No
per-user profiles.

### Zero-config back-compat

With no `llm-profiles.json`, `llm_config` synthesizes the built-ins: two
Anthropic profiles (`anthropic-sonnet` → `DEFAULT_MODEL`, `anthropic-opus` →
`OPUS_MODEL`) sharing the `ANTHROPIC_API_KEY` slot, active =
`anthropic-sonnet`. Nothing is written to disk at startup. An install that
only ever set `ANTHROPIC_API_KEY` sees no prompt, no migration, no behaviour
change. A corrupt or unreadable profiles file logs a warning and falls back to
the built-ins — the `read_settings` never-raise rule.

The built-in model literals live in `llm_config.py`, **not** in
`chat_service.py` / `chat_tools.py`:
`test_chat_models.py:41` statically rejects model literals in those two
modules (verified).

## The preset catalogue — data, not code

`presets.json` ships in the bundle — an **explicit `datas` entry in
`pypsa-gui.spec`** (the spec file is a documented allowlist, never a directory
sweep; verified `pypsa-gui.spec:64-75`). Each entry: id, label, wire, base
URL, auth style + derived key-slot name, default capabilities, suggested model
ids, and the help text the settings pane renders.

**v1 roster:** Anthropic, OpenAI, Moonshot (Kimi), Qwen (DashScope), Ollama,
LM Studio, and **Custom OpenAI-compatible** (the always-present escape hatch;
its help text covers vLLM and other self-hosted servers).

No base URL, key format, or model id is asserted in this spec. **Every
catalogue entry is verified against the vendor's live documentation during
implementation** — a research step in the plan. Model id fields stay free
text everywhere so a newer model works even when the catalogue lags.

## Secrets — one store, allowlist widened to a rule

`user.env` remains the single secret store (the 2026-08-05
api-key-store-collision finding is why there will not be a second one).
`app_secrets.MANAGED_KEYS`, today the tuple `("ANTHROPIC_API_KEY",)`, becomes
a rule `is_managed_key(name)`: the four known provider names plus the
`PYPSA_GUI_LLM_KEY__<SLUG>` prefix (upper-case, length-bounded). The
behavioural allowlist tests (`test_app_secrets.py:116-143` — `SECRET_KEY`,
`PYPSAGUI_APP_DATA_DIR`, `DATABASE_URL` never applied or kept) survive a rule
unchanged (verified: no test pins the tuple as a literal).

**Both halves of the boundary need rewrites the tuple made implicit:**

* `_read_managed` (`app_secrets.py:112`) filters by membership — becomes
  `is_managed_key`. Mechanical.
* `_write_managed` (`app_secrets.py:119`) **rebuilds the file by iterating
  the tuple**. A rule cannot be enumerated: kept as-is, every custom key slot
  silently vanishes on the next save. It becomes
  `for name in sorted(values) if is_managed_key(name) and values[name]` — the
  write-time allowlist survives as a filter — plus a new test: *saving key A
  does not erase key B*.
* `DELETE /profiles/{id}` also clears the profile's namespaced slot —
  rule-based membership has no enumeration that could ever garbage-collect an
  orphan otherwise.
* `MAX_VALUE_LENGTH` rises to 2000 (JWT-style bearers on the custom escape
  hatch exceed 500). `status()`'s response shape is unchanged — its
  exact-dict test (`test_app_secrets.py:99-105`) is pinned.

Shell > `user.env` > `backend/.env` precedence, `_SHELL_NAMES` masking, 0600
`O_CREAT` writes, and last-four hints all carry over. `OPENAI_API_KEY`
exported in the shell wins, and `status()` says so.

### Redaction — widened, and given an enumerator

`_redact_for_log` / `_redact_secrets_in_str` today match `sk-ant-*` plus
generic `key=val` / bearer shapes. Widening needs a value source that does not
exist yet: `app_secrets` has no bulk reader of *live* values (`_read_managed`
reads the file only — a shell-exported `OPENAI_API_KEY` is never in it). New:

```python
def live_secret_values() -> frozenset[str]:
    # every managed name's live value, env + file, snapshotted once
    return frozenset(
        v for k, v in os.environ.items() if is_managed_key(k) and v
    ) | frozenset(_read_managed().values())
```

Rules: the value set is **snapshotted once per top-level redaction call**
(`_redact_for_persist` recurses over every block of every turn — per-string
file reads would be hundreds of disk hits); value substitution applies only to
values **≥ 8 characters** (Ollama users set `OPENAI_API_KEY=ollama`;
substituting that rewrites the word everywhere in transcripts — the
`_MIN_HINT_LENGTH = 8` precedent); shape patterns cover the rest.

**Three leak sites the widening must also cover** (verified):

1. `chat_service.py:2805` — tool_error `content: str(detail or exc)[:1000]`
   persists upstream exception text unredacted. Wrapped in
   `_redact_secrets_in_str`.
2. `chat_tools.py:2376` — the vision sub-call formats the raw exception into
   a model-visible string. Same wrap.
3. **httpx's own INFO logging.** The packaged app installs a root
   `RotatingFileHandler` at INFO (`desktop/bootstrap.py:87-96`, verified);
   httpx logs every request URL at INFO, and `str(URL)` preserves userinfo.
   Bootstrap sets `logging.getLogger("httpx")` and `"httpcore"` to WARNING;
   base_url validation (above) independently rejects userinfo.

Subprocess hardening, one line: spawn sites that inherit the environment
(`routers/local_settings.py:148` file-manager open; `desktop/gui.py`
relaunch) pass `env={k: v for k, v in os.environ.items() if not
is_managed_key(k)}`. Solver subprocesses are excluded — they may need
license env vars, and the threat model (`app_secrets.py` header) already
accepts same-user reads.

## Backend

### Modules (flat `services/*.py`, matching convention)

| Module | Role |
|---|---|
| `services/llm_config.py` | profile store: load/validate `llm-profiles.json`, resolve active profile, synthesize built-ins, derive key slots, load `presets.json`; owns the built-in model literals |
| `services/app_secrets.py` | widened as above |
| `services/llm_provider.py`, `llm_anthropic.py`, `llm_openai_compat.py`, `llm_fake.py` | owned by the seam plan; this spec's addition is profile-based construction |

### Wire dependency

`OpenAICompatProvider` speaks the chat-completions SSE format over raw
`httpx`. **httpx is a dev dependency only** (`backend/requirements.txt:29`);
it is absent from `gui-requirements.txt`, the frozen-build manifest, and
`test_packaging_requirements.py:180-210` fails the suite on any unguarded
third-party import missing from that file (verified). **Adding a pinned
`httpx` to `gui-requirements.txt` is the plan's first packaging task** — as a
guarded import it would pass tests while every OpenAI-compatible provider is
silently dead in the shipped app, the worst failure shape available.

No `openai` SDK: no second retry layer fighting the harness's backoff loop,
and the provider must own its error mapping anyway. Consequences:
`sdk_not_installed` cannot occur on this path; connection failures surface as
`unreachable` (§Error taxonomy). Streaming is always requested; usage
accounting is best-effort via `stream_options.include_usage` — a stream with
no usage data logs once per session and counts zero toward caps rather than
guessing.

### Routes

On the **chat router** (the admin router 404s in local mode), gated by
`_require_super_admin` unless noted:

| Route | Does |
|---|---|
| `GET /chat/settings/llm` | profiles + active id + per-profile key status (hint only) + preset catalogue + base URLs |
| `PUT /chat/settings/llm/profiles/{id}` | create/update a profile — never carries a secret, never carries `key_env` |
| `DELETE /chat/settings/llm/profiles/{id}` | delete; clears the namespaced key slot; active falls back to built-ins |
| `PUT /chat/settings/llm/profiles/{id}/key` | set/clear the derived slot in `user.env` |
| `POST /chat/settings/llm/active` | set `active_profile_id` |
| `POST /chat/settings/llm/profiles/{id}/test` | connection test (below) |
| `GET /chat/profiles` | **member-level** (authenticated, not super-admin): `[{id, label, wire}]` + active id — feeds every user's dropdown |

`GET /chat/health` is **unauthenticated** (`routers/chat.py:97`, no
dependency — verified) and therefore gains **nothing enumerable**: it keeps
`anthropic_api_key_present` (verbatim today's semantics — literal
`ANTHROPIC_API_KEY` env presence; 11 assert sites pin it) and `default_model`
(the active profile's resolved model id), and adds only
`active_profile: {id, label, wire}` and `chat_ready: bool` (active profile
resolves + its auth requirement is satisfied). Profile inventory, base URLs
and key hints stay on the gated routes.

**Three legacy key surfaces stay, all delegating to the same slot:**

| Legacy surface | Fate |
|---|---|
| `GET/PUT/DELETE /chat/settings/api-key` | delegates to the `ANTHROPIC_API_KEY` slot; pinned backend tests unaffected (verified: they touch route + health only) |
| `PUT /api/local-settings/anthropic-key` + `probe_api_key()` | stays Anthropic-labelled — it edits the Anthropic slot; the pane's key section becomes the built-in profile's key field and deep-links to the new section (one control, not two disagreeing panes) |
| `ApiKeySetup` inline banner field | kept for the built-in Anthropic profile (§Frontend) |

### Turn path — the binding must be built, not extended

Verified reality: **no session↔model binding exists.** `get_or_create_session`
ignores `model=` on reuse (`chat_service.py:720-723`); the router overwrites
`session.model` per request (`routers/chat.py:403-404`); `GET /history` mints
sessions from the raw on-disk `model` string and replays Anthropic-shaped
blocks into them (`routers/chat.py:217-236`); `POST /chat/import` validates
key presence only. The spec therefore *builds*:

* `ChatSession` gains `profile_id` + `bound_wire`, set at creation and by
  history rehydration. `ChatSession.model` **stays** (the resolved model id —
  it is pinned by the A8 tests and stamped into turn records).
* The router resolves the requested profile **before** the
  `has_explicit_script` branch (the stub path must not bypass binding) and
  passes it into the generator. Wire mismatch → typed
  `profile_switch_requires_new_chat` **SSE frame from inside the generator,
  never a 4xx** — the client discards non-2xx response bodies
  (`api/chat.ts:72-75`), so an HTTP error would surface as a bare
  "connection lost" with none of the promised guidance.
* Same-wire rebinds are free **semantically but cache-cold**: Anthropic
  prompt caches are model-scoped, so a switch re-writes ~12k tokens of
  breakpoints at the 1.25× write premium. The dropdown applies switches
  between turns only (select disabled while streaming).
* Legacy `model` field: the two built-in strings resolve to the built-in
  profiles; an **unknown** model string resolves to the active profile with a
  logged warning — not a refusal. (Free-text passthrough is a documented
  contract today — `test_chat_models.py:28-38`'s docstring; the smoke
  driver's `--model <anything>` flag depends on it.)
* `session_init` **keeps** `model` (pinned: `test_chat_sse.py:91`,
  `e2e_chat_service.sh:217-241`) and adds `profile_id` + `label`; its
  `tool_count` reports the count actually sent this turn, not
  `len(TOOLS)`.

### Durable records — `chat.jsonl` gains `profile_id`, keeps `model`

Four consumers key on `model` (verified): the import validator's required
tuple (`routers/chat.py:296`), history rehydration (`:219`), the e2e pin
(`test_chat_e2e.py:1663`), and `ChatTurn.model` in the frontend. So: turn
records keep `model` (resolved id) and **add** `profile_id`. The import
required-tuple is unchanged; the export envelope version stays
`pypsa-gui-chat-export/1` (additive field). Read side: a record's
`profile_id` resolves through `llm_config` when still configured; else the
`model` string resolves to a built-in; else the active profile. When the
resolved wire differs from the blocks' provenance, non-portable blocks
(thinking, image/document) are dropped from rehydration rather than replayed
into a provider that 400s on them.

### A8 fallback — generalised with its bound made explicit

Verified: `model_fallback_used` is declared **inside** the agentic loop
(`chat_service.py:2269`), so it resets every tool iteration — the once-per-
turn bound today rides the permanent `session.model` mutation at `:2367`,
which the pinned test (`test_chat_e2e.py:2495-2499`) asserts. Generalisation:
per-profile `fallback_model` (same wire, same endpoint), the used-flag
**hoisted to turn scope**, and the fallback rebinds the session for the rest
of the session (matching today's persistence) while emitting `model_fallback`
— which gains its first frontend consumer (§Frontend; today the frame is
silently dropped by the `default`-less switch). The two A8 tests update in
place; `ChatSession(model=...)` construction keeps working.

### Capability enforcement — honest degradation

* `tools: false` → no `tools=` (the cache-breakpoint budget is safe — the
  last-tool marker is already guarded by `if tools_with_cache:`, verified
  `:2251-2258`) — and the system prompt drops its tool-chaining half. That
  half is **not separable today**: `_DOMAIN_GUIDE`, `_SOLVER_ERROR_DECODER`,
  `_PRICE_CONGESTION_GUIDE`, `_NEXT_STEP_RUBRIC` each mix domain facts with
  tool-chaining imperatives, and `test_chat_e2e.py:1747-1798` pins the
  assembled prompt. The plan splits each constant into a domain-facts half
  and a tool-chaining half (both module-level, preserving byte-stability),
  updates the prompt pins, and assembles per capability. The panel shows a
  persistent "answers only, cannot act" notice. Note: `set_active_profile`
  is itself a tool — **switching out of a tool-less profile is UI-only**, and
  the notice says so.
* `vision` is enforced on the **outbound message array** (any
  `image`/`document` block), not on `attachment_file_ids` — attachments from
  earlier turns are replayed by the session history
  (`chat_service.py:2210-2223`, verified), so a request-field check misses
  every replay. Violation → `capability_unsupported` frame before any
  provider call. On the `openai` wire, `vision: true` covers **images only**
  (translated to the chat-completions image format by the adapter); PDF
  `document` blocks are Anthropic-native and additionally require
  `wire: anthropic` — the attach UI says so per file type.
* `reconstruct_network_from_image` (`chat_tools.py:2314-2348`) constructs
  from the **active resolved profile** instead of
  `_build_anthropic_client()` + hardcoded `DEFAULT_MODEL`, and is refused
  with `capability_unsupported` when the active profile lacks vision.
* Tool-capable but unverified models get the full 117-tool set and may choose
  badly; that is accepted and labelled, not silently curated.

Reasoning output on the OpenAI path is passive passthrough: a recognised
reasoning field maps to the existing `thinking` SSE event, never replayed.
The panel gains a `thinking` renderer — **none exists today** (verified:
no `thinking` case in `handleFrame`; the frame is currently dropped), so
"the panel renders it" is new work, scoped as a minimal muted/collapsible
block.

### The switching tool

`set_active_profile(profile_id)` joins the registry (117 → 118; parity test
updates). Its `Safety:` marker uses an existing tier from `DESTRUCTIVE_TIERS`
(the plan pins which one), so the standard confirmation card fires; card and
result both state "takes effect when you start a new chat". It validates the
id against configured profiles and writes `active_profile_id`. It switches
among **already-configured** profiles only — creation and key entry stay
UI-only. Read-side awareness (active + configured profile labels + the
switching procedure) joins the system-prompt context. (The
presence-and-deixis spec (step (c)) is also unimplemented — this does not
depend on it; it is a system-prompt addition of its own.)

## Error taxonomy — additions

| Kind | Meaning | Retryable |
|---|---|---|
| `unreachable` | connect refused / DNS / connect timeout (`httpx.ConnectError` family) | no |
| `capability_unsupported` | request needs a capability the active profile lacks | no |
| `profile_switch_requires_new_chat` | cross-wire profile named mid-session | no |

`missing_api_key` broadens to "the active profile's key slot is empty"; for
the built-in Anthropic profiles this is byte-identical to today, and for
`auth: none` presets it is never emitted. `model_not_found` appears in the
connection-test verdict; on the turn path providers map it to
`invalid_request` (deterministic, terminal — the retryability rule stays
load-bearing).

**Response hygiene:** verdicts and error frames use fixed strings per kind
plus at most host:port — never the full URL, never upstream exception text.
`routers/local_settings.py:56-64` documents this as an invariant stronger
than scrubbing ("no formatting step for a key to survive"), pinned by
`test_local_settings_api.py:366-395`; the new surfaces inherit it.

## Frontend

Five touch points (ErrorBanner is load-bearing, not incidental):

* **`api/chat.ts`** — `ChatModel` union → `string` (all four sites:
  `ChatStreamRequest.model`, `ChatHealth.default_model`, `ChatTurn.model`,
  `chatStore`); `ChatStreamRequest` gains `profile_id`; `ChatTurn` gains
  `profile_id?`; new thin `api/llmSettings.ts` for the settings routes +
  `GET /chat/profiles`.
* **`chatStore.ts` / `ChatPanel.tsx`** — `model` → `profileId: string | null`,
  **default `null` = "server's active profile"**; `profile_id` is omitted
  from the request when null, so the server's binding (and any A8 fallback)
  is not silently re-overridden every turn — today `model` is sent
  unconditionally (`ChatPanel.tsx:1620`), which would defeat both the
  binding and `set_active_profile`. The dropdown lists profiles from a new
  `useChatHealth`/`useChatProfiles` query (**no health consumer exists
  today** — `getChatHealth` has zero callers, verified), invalidated after
  key writes, profile writes, `set_active_profile` results, and
  `session_init`. Select: disabled while streaming; `max-w` + truncate (the
  dock is 380px and a native select sizes to its widest option); a disabled
  placeholder option until the query resolves (a controlled select whose
  value matches no option silently shows the first). New store action
  **`startNewChat()`** — nulls `sessionId`, clears messages/pending/usage/
  error, sets a one-shot hydration-suppression flag (the mount effect at
  `ChatPanel.tsx:888-952` otherwise re-binds `h.last_session_id`). **No such
  flow exists today** — Clear keeps `sessionId` (verified `:1710-1717`).
  New frame consumers: `model_fallback` (append a system line, update
  `profileId`), `thinking` (minimal renderer).
* **`ErrorBanner` (in `ChatPanel.tsx`)** — the three hardcoded kind lists
  (`:465-495` title map, `:496-508` fall-through, `:1471-1489` tool_error
  allowlist) collapse into one `KIND_COPY` map and gain the three new kinds
  with their fix copy + action buttons ("Open settings" deep-link, "Start
  new chat" → `startNewChat()`).
* **`AssistantModelSettings.tsx`** (new) — hosted in the settings slide
  panel but **gated on `/chat/settings/llm` reachability, not on
  `useLocalSettingsAvailable()`**: the local-settings pane renders `null` on
  every non-desktop deployment (verified `pages/LocalSettings.tsx:36`), which
  would make the server-super-admin story a blank panel. The Settings nav
  rows (`Sidebar.tsx:1295`, `CommandPalette.tsx:434-441`) show when either
  surface is reachable; the pinned "renders nothing on web" test updates.
  Deep-linking uses the house request/clear pattern
  (`requestSettingsSection` / `clearSettingsSectionRequest`, same shape as
  `requestResultsTab`) — `setSlidePanel` carries no sub-target and the panel
  subtree remounts on panel switch, so an anchor must survive a remount.
  Contents: profile list with active radio, add-from-preset, custom form
  (label, base URL, model id + suggestions, capability toggles), per-profile
  key field for `auth: bearer` profiles, Test connection with typed verdict.
* **`ApiKeySetup.tsx`** — generalises; trigger stays the error frame
  (broadened `missing_api_key`), body names the active profile, keeps the
  inline key field for the built-in Anthropic profile, deep-links otherwise.
  Its test file's **full-factory module mock** (`ApiKeySetup.test.tsx:25-29`,
  no `importOriginal`) breaks on any new import — the mock surface updates in
  the same commit, as do the Anthropic-specific copy sites
  (`ApiKeySetup.tsx:89-128`, `pages/LocalSettings.tsx:62,102`,
  `api/localSettings.ts` placeholder).

### Connection test

The verdict is a real `max_tokens=1`, non-streaming completion through the
profile. Typed verdict: `ok` (+ latency), `unreachable`, `unauthorized`,
`model_not_found`, `invalid_request` — fixed strings, host:port at most.
Separately and best-effort, `GET /models` populates model-id suggestions;
its failure is ignored.

## Guidance — UI-led, assistant-aware

The switcher must be self-explanatory with the assistant broken. Error copy
is the guidance:

| Kind | Rendered as |
|---|---|
| `unreachable` + localhost | "Is Ollama running? Start it, then Test connection." + Open settings |
| `unreachable`, remote | "Could not reach `<host:port>`." + Open settings |
| `unauthorized` | names which key slot is rejected, links to its field |
| `model_not_found` (test) | "The endpoint doesn't know `<model>` — pick from its model list." |
| `capability_unsupported` | names the capability and the active profile |
| `profile_switch_requires_new_chat` | "Start new chat" button → `startNewChat()` |

On top, the assistant knows the procedure and can act: "switch me to Kimi" →
`set_active_profile` card → approve → new chat. Useful, never load-bearing.

## Verification

Beyond the seam spec's own gates (which land first):

1. **`pixi run gui-tests` exits 0 with no key and no profiles file** — the
   canonical gate (plain pytest in the default env fails the 7 webview tests
   by design). Zero-config synthesis is the regression surface.
2. Store tests: corrupt/absent/non-object profiles file never blocks launch;
   `is_managed_key` rejects `SECRET_KEY`/`PYPSAGUI_APP_DATA_DIR`/
   `DATABASE_URL`, accepts the upper-case prefix rule; **saving key A does
   not erase key B**; deleting a profile clears its slot; shell-wins
   precedence holds for `OPENAI_API_KEY`; base_url userinfo rejected. An
   **autouse `llm_config` reset fixture** — `conftest.py:65` pins one
   session-scoped app-data dir, so without a reset the first profile write
   leaks into every later test.
3. Route tests: no response ever echoes a key or a full URL; super-admin
   gate on every settings route; member gate on `GET /chat/profiles`;
   `/chat/health` payload contains nothing enumerable; test-endpoint
   verdicts via `httpx.MockTransport`.
4. Turn tests: `tools: false` carries no `tools=` + trimmed prompt +
   corrected `tool_count`; vision enforcement fires on **replayed** blocks,
   not just fresh attachments; cross-wire frame emitted from the generator
   (not a 4xx); history rehydration binds a profile and drops non-portable
   blocks; legacy `model` strings resolve (built-ins by name, unknown →
   active + warning); A8 fallback fires once per turn and emits a consumed
   frame; `set_active_profile` dispatch + confirmation; the stub path
   respects the binding.
5. Redaction: planted `PYPSA_GUI_LLM_KEY__X` and shell-only `OPENAI_API_KEY`
   values come out redacted from logs **and** from `chat.jsonl` (including
   the tool_error path); a 6-char value is not substituted; httpx logger
   capped at WARNING in the packaged bootstrap.
6. Replaced tests, named: `test_chat_models.py:22,28`
   (`ALLOWED_MODELS` pins) → profile-store validation tests;
   `test_no_module_hardcodes_a_model_literal` still passes because built-in
   literals live in `llm_config.py`; prompt pins at
   `test_chat_e2e.py:1747-1798` updated for the split constants;
   `LocalSettings.test.tsx` web-null pin updated; `ApiKeySetup.test.tsx`
   mock factory extended.
7. Live smoke against a local Ollama, skipped when absent.
8. Catalogue verification against live vendor docs during implementation.
9. Frontend vitest: dropdown loading/selected states, cross-wire notice +
   `startNewChat()`, KIND_COPY rendering for the three new kinds, settings
   section gating on web vs desktop, banner deep-link.
10. Packaging: pinned `httpx` in `gui-requirements.txt` (unguarded import);
    `presets.json` in the `.spec` datas; **`user.env` and
    `llm-profiles.json` added to `check_bundle.py` FORBIDDEN_FILES**
    (verified absent today — the file that actually holds the live key is
    not on the forbidden list).

## Risks

| Risk | Mitigation |
|---|---|
| httpx ships guarded → OpenAI providers silently dead in packaged app | unguarded import + packaging test + live smoke |
| Secret leak via new key shapes | enumerator + three named sites + httpx logger cap + bundle forbidden-list |
| Local models choose badly among 117 tools | accepted, labelled; curation deferred |
| Preset data goes stale | data-only updates; free-text model ids; connection test names the failure |
| Cross-provider history replay 400s | wire binding + rehydration drop rule |
| Scope: touched surfaces grew ~40% in verification | workstreams + estimates below; sequencing seam-first unchanged |

## Workstreams (estimates are assumptions, ±50%)

| # | Workstream | Est. |
|---|---|---|
| 0 | Provider seam (its own plan, precondition) | 2–4 d |
| 1 | `llm_config` + `app_secrets` widening + redaction enumerator + packaging | 2–3 d |
| 2 | Routes + connection test + health/profiles split + legacy delegates | 1–2 d |
| 3 | Turn path: binding, rehydration, A8, capabilities, prompt split, vision sub-call | 2–4 d |
| 4 | Frontend: store/dropdown/startNewChat/ErrorBanner/settings section/banner | 2–4 d |
| 5 | Presets research + live smoke + doc updates (`CHATBOT.md`, stale comments) | 1 d |

## Concurrency (checked 2026-08-13)

Main worktree (`pypsa-eur`, `feature/local-app-impl`) dirty in
`compare.py` / `economics.py` / compare tests — no overlap with any file this
spec touches. A build-coordination session is rebuilding/installing the app
from the main worktree; non-interference acknowledged both ways (this work
stays out of `dist-app` and `/Applications`). Newest mtime among target files
was 5 days old at check time. `master` moved to trunk at `3e58e424` during
this design session (fast-forward, verified green by that session).

## Open items

* httpx's INFO-logs-full-URL behaviour was verified from library knowledge,
  not a live repro — the bootstrap logger-cap test in verification item 5
  falsifies it either way.
* Per-vendor image formats on the `openai` wire (data-URL vs `image_url`
  variants) — resolved during the preset research step.
* Which `DESTRUCTIVE_TIERS` tier `set_active_profile` uses — plan decision.
* Whether `GET /chat/profiles` needs org-scoping on multi-tenant servers
  beyond member-auth — deferred until multi-tenancy itself defines it.

## Out of scope

Per-user profiles and keys. Tool-set curation for weak models.
Reasoning-effort knobs. Removing `StreamRequest.script`. The domain seam (104
router imports). Any SSE frame-format change beyond the three added kinds +
additive `session_init`/`model_fallback` fields. Solver subprocess env
changes.

## Decision ledger (grilled 2026-08-13)

| Decision | Choice |
|---|---|
| Relation to seam spec | extend, don't supersede; seam lands first |
| Ownership | instance-wide, desktop-first, super-admin on servers |
| Weak models | capability declaration + honest degradation |
| Config UI | preset catalogue + custom escape hatch |
| Guidance | UI-led, assistant-aware |
| Secrets | one store, allowlist widened to a rule |
| Wire dep | raw httpx, no openai SDK (httpx added to the shipped manifest) |
| Vision-less attachments | disable attach UI + typed server error (enforced on outbound blocks) |
| Switch UX | profiles in dropdown; cross-wire = new chat |
| Config home | settings-pane section + chat banner deep-link (section gated on llm-settings reachability, not local mode) |
| Connection test | 1-token live completion + opportunistic model list |
| Reasoning | passive passthrough, display-only (renderer is new work) |
| Agent power | full `set_active_profile` tool, confirmation-gated (user chose beyond recommendation); switches configured profiles only |
| Presets v1 | Anthropic, OpenAI, Moonshot, Qwen, Ollama, LM Studio + Custom |

## Verification findings (2026-08-13, four adversarial agents)

Every claim below was re-verified against the code before inclusion; the
corrections are folded into the body above. Headline items: no session↔model
binding existed to extend; `GET /history`/`POST /import` were an unguarded
back door around wire enforcement; no new-chat flow existed; vision
enforcement on the request field missed history replay; a fifth profile-blind
Anthropic call site (`reconstruct_network_from_image`); httpx absent from the
shipped manifest; `_write_managed`'s tuple iteration would delete custom
slots; client-settable `key_env` was an exfiltration primitive; mixed-case
env names break Windows precedence; `/chat/health` is unauthenticated;
`user.env` missing from the bundle forbidden-list; `thinking` and
`model_fallback` frames had no frontend consumers; the settings pane renders
`null` on web; no section-anchor machinery existed.
