# LLM provider configuration and model switching — design

**Status:** approved in brainstorming + grilling, 2026-08-13 ·
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

One correction to the seam spec's numbers, measured 2026-08-13: the tool
registry holds **117** entries (`TOOLS` and `DISPATCHERS` both), not 139.

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
an env var itself. That is what keeps `FakeProvider` and the seam test free of
config fixtures.

## The profile — the unit the user switches between

```jsonc
{
  "id": "ollama-local",                    // slug [a-z0-9-]; also names the secret
  "label": "Ollama (local)",               // what the dropdown shows
  "preset": "ollama",                      // catalogue id, or "custom"
  "wire": "openai",                        // adapter: "openai" | "anthropic"
  "base_url": "http://localhost:11434/v1",
  "model": "qwen3:8b",                     // always free text
  "capabilities": { "tools": true, "vision": false },
  "key_env": "PYPSA_GUI_LLM_KEY__ollama_local",
  "fallback_model": null,                  // optional; Anthropic presets ship one
  "max_output_tokens": null                // optional; default = global 8192
}
```

Profiles live in `<app-data>/llm-profiles.json` beside `local-settings.json`,
plus one `active_profile_id`. **No secret ever enters that file** — it holds
only `key_env`, the name of the slot in `user.env`. Two profiles may share a
`key_env` (the built-in Anthropic pair does); that is a supported property.

Ownership is **instance-wide, desktop-first**: one set of profiles per
install, editable by a super-admin — which the desktop app's single seeded
user is. Server deployments keep today's shape: the admin configures, members
use. No per-user profiles.

### Zero-config back-compat

With no `llm-profiles.json`, `llm_config` synthesizes the built-ins: two
Anthropic profiles (`anthropic-sonnet` → `DEFAULT_MODEL`, `anthropic-opus` →
`OPUS_MODEL`) sharing `key_env: ANTHROPIC_API_KEY`, active =
`anthropic-sonnet`. An install that only ever set `ANTHROPIC_API_KEY` sees no
prompt, no migration, no behaviour change. The chat suites pass today without
an API key; any new key or file requirement is a defect.

A corrupt or unreadable profiles file logs a warning and falls back to the
built-ins — the `read_settings` never-raise rule; an app-data problem must
never be why the app will not start.

## The preset catalogue — data, not code

`presets.json` ships in the bundle: id, label, wire, base URL, auth style /
key env name, default capabilities, suggested model ids, and the help text the
settings pane renders. Adding a provider later is a data edit.

**v1 roster:** Anthropic, OpenAI, Moonshot (Kimi), Qwen (DashScope), Ollama,
LM Studio, and **Custom OpenAI-compatible** (the always-present escape hatch;
its help text covers vLLM and other self-hosted servers). No separate vLLM
preset.

No base URL, key format, or model id is asserted in this spec. **Every
catalogue entry is verified against the vendor's live documentation during
implementation** — a research step in the plan. A preset with a stale URL
fails with a confusing error, which is the exact opposite of this feature's
point. Model id fields stay free text everywhere so a newer model works even
when the catalogue lags.

## Secrets — one store, allowlist widened to a rule

`user.env` remains the single secret store
(the 2026-08-05 api-key-store-collision finding is why there will not be a
second one). `app_secrets.MANAGED_KEYS`, today the tuple
`("ANTHROPIC_API_KEY",)`, becomes a rule `is_managed_key(name)`:

* the known provider names: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `MOONSHOT_API_KEY`, `DASHSCOPE_API_KEY`;
* plus the namespaced prefix `PYPSA_GUI_LLM_KEY__<slug>` for custom profiles,
  slug validated `[a-z0-9-]` (after `-`→`_` normalisation for the env name)
  and length-bounded.

The security boundary the tuple enforced is preserved: `SECRET_KEY` and
`PYPSAGUI_APP_DATA_DIR` remain unwritable through this module. Everything
else about `app_secrets` carries over verbatim — 0600 `O_CREAT` writes,
shell > `user.env` > `backend/.env` precedence, `_SHELL_NAMES` masking,
last-four-characters hints. `OPENAI_API_KEY` exported in the launching shell
behaves exactly as ops expect: it wins, and `status()` says so.

**Redaction widens with it.** `_redact_for_log` /
`_redact_secrets_in_str` today match `sk-ant-*` only; an OpenAI or Moonshot
key logged verbatim is a new leak the moment a second provider exists. Both
helpers iterate every managed secret *value* currently known to `app_secrets`,
plus generic shapes (`sk-*`, bearer-token headers).

## Backend

### Modules (flat `services/*.py`, matching convention)

| Module | Role |
|---|---|
| `services/llm_config.py` | profile store: load/validate `llm-profiles.json`, resolve active profile, synthesize built-ins, load `presets.json` |
| `services/app_secrets.py` | widened as above |
| `services/llm_provider.py`, `llm_anthropic.py`, `llm_openai_compat.py`, `llm_fake.py` | owned by the seam plan; this spec's one addition is profile-based construction |

### Wire dependency

`OpenAICompatProvider` speaks the chat-completions SSE format over **raw
`httpx`** (already a dependency ≥0.27). No `openai` SDK: no new frozen-bundle
surface, no second retry layer fighting the harness's existing
backoff loop, and the provider must own its error mapping anyway.
Consequences: `sdk_not_installed` cannot occur on this path, and connection
failures surface as a new neutral kind (below). Streaming is always requested
(`stream: true`); usage accounting is best-effort via
`stream_options.include_usage` where supported — a stream with no usage data
logs once per session and counts zero toward caps rather than guessing.

### Routes

On the **chat router** (the admin router 404s in local mode — same reasoning
as the existing key routes), all gated by `_require_super_admin`:

| Route | Does |
|---|---|
| `GET /chat/settings/llm` | profiles + active id + per-profile key status (hint only) + preset catalogue |
| `PUT /chat/settings/llm/profiles/{id}` | create/update a profile — never carries a secret |
| `DELETE /chat/settings/llm/profiles/{id}` | delete; deleting the active profile falls back to built-ins |
| `PUT /chat/settings/llm/profiles/{id}/key` | set/clear that profile's key slot in `user.env` |
| `POST /chat/settings/llm/active` | set `active_profile_id` |
| `POST /chat/settings/llm/profiles/{id}/test` | connection test (below) |

No response body ever contains a key — hints only, the `app_secrets.status()`
pattern.

`GET /chat/health` grows `active_profile` (id, label, wire, model,
capabilities, key_present) and `profiles[]` (id, label, model, wire,
key_present). `anthropic_api_key_present` and `default_model` remain as
computed aliases so existing consumers keep working. The legacy
`GET/PUT/DELETE /chat/settings/api-key` routes stay, delegating to the
`ANTHROPIC_API_KEY` slot — `ApiKeySetup`'s pinned tests keep passing until
the UI swap lands in the same feature.

### Test connection

The verdict is a real **`max_tokens=1`, non-streaming completion** through the
profile — the only probe that exercises base URL, auth, and model id together,
so its failure is the user's failure. Typed verdict: `ok` (+ latency),
`unreachable`, `unauthorized`, `model_not_found`, `invalid_request` — each
rendered with fix copy. Separately and best-effort, `GET /models` (or the
Anthropic equivalent) populates model-id suggestions in the form; its failure
is ignored (some servers don't expose it). Cost on paid APIs: a fraction of a
cent.

### Turn path

`ChatStreamRequest` gains `profile_id`. The legacy `model` field maps
`claude-sonnet-5` / `claude-opus-5` onto the built-in profiles, so an old
client works unchanged; `ALLOWED_MODELS` and the closed `ChatModel` union are
retired — **the profile store is the validator**, and a request can only name
a configured profile, never a raw endpoint.

The session **binds its resolved profile at creation**. A stream request
naming a profile whose `wire` differs from the session's bound wire gets a
typed `profile_switch_requires_new_chat` error frame — the server enforces
what the UI promises. Same-wire rebinds are free, exactly like today's
Sonnet↔Opus switching.

The A8 hardcoded Opus→Sonnet rate-limit fallback generalises to the
per-profile `fallback_model` (same wire, same endpoint, one attempt). Absent —
the default for everything but the Anthropic presets — means no fallback.
`max_output_tokens` overrides the global 8192 per profile.

### Capability enforcement — honest degradation

Profiles declare `tools` and `vision`. Enforcement is server-side, with UI
mirrors (frontend section):

* `tools: false` → the request carries **no** `tools=` at all, and the system
  prompt drops its tool-chaining half (the domain guide's instructions to
  chain `get_results` calls are noise to a model that cannot call them). The
  panel shows a persistent "answers only, cannot act" notice.
* `vision: false` + `attachment_file_ids` present → typed
  `capability_unsupported` error frame **before any provider call**. Excel /
  Word / CSV are unaffected — they flow through the `read_excel_sheet` tool,
  not content blocks.
* Tool-capable but unverified models get the full 117-tool set and may choose
  badly; that is accepted and labelled, not silently curated. Tool-set
  reduction is out of scope (as in the seam spec: "runs on a local model" and
  "is good on a local model" are different milestones).

Reasoning output on the OpenAI path is **passive passthrough**: a recognised
reasoning field in a delta (`reasoning_content` et al.) maps to the existing
`thinking` SSE event so the panel renders it, and is **never replayed** into
later requests. No request-side knob, no capability flag consulted.

### The switching tool

`set_active_profile(profile_id)` joins the registry (117 → 118; the
`len(TOOLS) == len(DISPATCHERS)` parity test updates). Its `Safety:` marker
uses an existing tier from `DESTRUCTIVE_TIERS` (the plan pins which one), so
the standard confirmation card fires with no new tier machinery; its card and
its result
both state **"takes effect when you start a new chat"**. It validates the id
against configured profiles and writes `active_profile_id`. Boundary: it
switches among **already-configured** profiles only — profile creation and
key entry stay UI-only, so no key material ever transits the chat channel.

Read-side awareness rides the existing deixis context: active profile label +
configured profile labels + a two-line switching procedure. A few dozen
tokens; "which model am I talking to?" gets a true answer instead of a
hallucinated one.

## Error taxonomy — additions

The seam spec's neutral kinds stand. This spec adds:

| Kind | Meaning | Retryable |
|---|---|---|
| `unreachable` | connection refused / DNS / timeout at connect (`httpx.ConnectError` family) | no |
| `capability_unsupported` | request needs a capability the active profile lacks | no |
| `profile_switch_requires_new_chat` | cross-wire profile named mid-session | no |

`model_not_found` appears in the connection-test verdict; on the turn path a
provider maps it to `invalid_request` (deterministic, terminal — the
retryability rule the seam spec calls load-bearing is untouched).

## Frontend

Four touch points:

* **`api/chat.ts`** — `ChatModel` union → `string`; `ChatStreamRequest` gains
  `profile_id`; `ChatHealth` gains the profile fields. New thin
  `api/llmSettings.ts` client for the six routes.
* **`chatStore.ts` / `ChatPanel.tsx`** — `model` → `profileId`; the dropdown
  lists **profile labels** fed from `/chat/health` (built-ins render as
  "Sonnet 5" / "Opus 5", preserving today's UX verbatim). Picking a same-wire
  profile applies silently, like today. Picking a cross-wire profile shows an
  inline notice — "Switching to *Ollama (local)* starts a new chat" — wired
  through the existing new-chat flow.
* **`AssistantModelSettings.tsx`** (new, in the settings slide panel): profile
  list with active radio, add-from-preset picker, custom form (label, base
  URL, free-text model id with suggestions from the test endpoint, capability
  toggles), per-profile key field reusing `ApiKeySetup`'s masking / hint /
  overridden-by-environment patterns, and the Test connection button with its
  typed verdict line.
* **`ApiKeySetup.tsx`** generalises: trigger becomes "the active profile has
  no usable key / no profile resolves", body names the active profile and
  deep-links to the settings section (`setSlidePanel('settings')` + section
  anchor). For the built-in Anthropic profile it keeps the inline key field —
  first-run UX unchanged.

## Guidance — UI-led, assistant-aware

The switcher must be self-explanatory with the assistant *broken*, because a
misconfigured provider is exactly when the assistant cannot help. So the UI
carries the guidance, and **error copy is the guidance**:

| Kind | Rendered as |
|---|---|
| `unreachable` + localhost base URL | "Is Ollama running? Start it, then Test connection." + Open settings link |
| `unreachable`, remote | "Could not reach `<base_url>`." + Open settings link |
| `unauthorized` | names which key slot is rejected, links to its field |
| `model_not_found` (test verdict) | "The endpoint doesn't know `<model>` — pick from its model list." |
| `capability_unsupported` | names the capability and the active profile |
| `profile_switch_requires_new_chat` | offers a "Start new chat" button |

The banner, the settings pane, and the test verdicts share this vocabulary —
one story wherever the user meets it.

On top of that, the assistant knows the procedure (deixis context) and can
act: "switch me to Kimi" → `set_active_profile` confirmation card → approve →
new chat. Useful, never load-bearing.

## Verification

Beyond the seam spec's own gates (which land first):

1. **`pixi run gui-tests` exits 0 with no key and no profiles file** — the
   canonical gate (plain pytest in the default env fails the 7 webview tests
   by design), and zero-config synthesis is the regression surface.
2. Store tests: corrupt / absent / non-object `llm-profiles.json` never
   blocks launch; `is_managed_key` rejects `SECRET_KEY` and
   `PYPSAGUI_APP_DATA_DIR`, accepts the prefix rule; shell-wins precedence
   holds for `OPENAI_API_KEY`.
3. Route tests: no response ever echoes a key; super-admin gate on every new
   route; test-endpoint verdicts driven through `httpx.MockTransport`.
4. Turn tests: `tools: false` request carries no `tools=` and the trimmed
   system prompt; vision rejection frame; cross-wire frame; legacy `model`
   field resolves to the built-in profiles.
5. Redaction: a planted `PYPSA_GUI_LLM_KEY__x` value in a log line comes out
   redacted.
6. Live smoke against a local Ollama, skipped when absent (the seam spec's
   pattern for `OpenAICompatProvider`).
7. Catalogue verification: every preset's base URL / auth style / suggested
   models checked against live vendor docs during implementation, recorded in
   the plan.
8. Frontend vitest: dropdown from health payload, cross-wire notice, settings
   section, banner deep-link.
9. Packaging: `presets.json` present in the frozen bundle;
   `check_bundle.py` still asserts `backend/.env` absent.

## Out of scope

Per-user profiles and per-user keys. Tool-set curation for weak models.
Reasoning-effort knobs. Removing `StreamRequest.script`. The domain seam (104
router imports). Any change to confirmation gating, the M7 pre-scan, token
caps beyond the per-profile `max_output_tokens`, or the SSE frame format
(beyond the three added error kinds).

## Decision ledger (grilled 2026-08-13)

| Decision | Choice |
|---|---|
| Relation to seam spec | extend, don't supersede; seam lands first |
| Ownership | instance-wide, desktop-first, super-admin on servers |
| Weak models | capability declaration + honest degradation |
| Config UI | preset catalogue + custom escape hatch |
| Guidance | UI-led, assistant-aware |
| Secrets | one store, allowlist widened to a rule |
| Wire dep | raw httpx, no openai SDK |
| Vision-less attachments | disable attach UI + typed server error |
| Switch UX | profiles in dropdown; cross-wire = new chat |
| Config home | settings-pane section + chat banner deep-link |
| Connection test | 1-token live completion + opportunistic model list |
| Reasoning | passive passthrough, display-only |
| Agent power | full `set_active_profile` tool, confirmation-gated (user chose beyond recommendation); switches configured profiles only |
| Presets v1 | Anthropic, OpenAI, Moonshot, Qwen, Ollama, LM Studio + Custom |
