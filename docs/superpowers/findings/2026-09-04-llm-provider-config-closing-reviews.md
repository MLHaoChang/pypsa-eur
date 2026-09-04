# Closing reviews of `feature/llm-provider-config` — 2026-09-04

Both closing reviews named as UNRUN in
`docs/superpowers/handovers/2026-09-01-llm-provider-config-handover.md` have now
completed against `93ddbf79`: a whole-branch correctness review and an
adversarial security pass, run independently and in parallel.

**Verdict from both, independently: DO NOT MERGE.**

Neither tree was modified. 38 findings total. Nearly every one is a
*composition* failure — two correct halves with no test spanning the seam —
which is exactly what per-task review cannot see and what the 24 per-task
reviewers therefore missed.

## Verification status — read this before acting on any item

Findings are marked:

* **VERIFIED HERE** — I re-derived it myself at the definition, in this
  session, and the evidence is quoted. Treat as fact.
* **CONFIRMED (reviewer probe)** — the reviewer ran a probe and showed output.
  Credible, not independently re-run. Re-prove before writing the fix.
* **SUSPECTED** — reasoned from source only. Prove it first; it may be wrong.

Per the standing rule that a peer's figure becomes your claim the moment you
transcribe it: the counts and predicates in the VERIFIED items below were
recomputed, not copied.

---

## The two that change what the branch *is*

### C-1 (HIGH) — `tools: false` is advertised, never enforced. VERIFIED HERE.

`profile.tools` is read only on outbound paths: request build
(`services/chat_service.py:1812`), prompt trim (`:3178`), cache annotation
(`:3234`). The dispatch loop at `:3515` iterates `tool_uses` with **no
capability check**. An endpoint that returns `tool_use` blocks despite being
sent an empty tool list has them executed.

Blast radius, recomputed here via the real resolver `_safety_tier_for`:

```
total tools: 121
census: {'read': 56, 'write': 31, 'destructive': 31, 'execution': 3}
confirmation-gated: 34
UNGATED: 87
```

So a hostile endpoint drives **87 tools with no confirmation**, 31 of which
mutate the user's projects, and every tool result streams back to it.

Why this outranks a normal capability bug: it inverts the branch's premise.
Before this work the endpoint was always Anthropic, so "the model calls a tool
it was not offered" was hypothetical. The headline feature is *pointing the
assistant at an arbitrary endpoint*, which promotes the endpoint to an
attacker-controlled input. `_validate_base_url` also accepts plain `http`, so
MITM/DNS reaches it, not only social engineering.

**Fix direction:** refuse an unoffered `tool_use` server-side as a typed error
frame before dispatch. Test first, and mutation-test it: revert the guard and
confirm the test goes RED.

### C-2 (HIGH, SUSPECTED — and the single highest-value live call) — the OpenAI wire may be dead on arrival.

`services/llm_openai_compat.py:186-196` (stream) and `:365-372` (probe) send
**both** `max_tokens` and `max_completion_tokens`. The code comment claims this
is deliberate and safe. The branch's own preset help text, written from Task 2's
live-vendor research, says the opposite:

```
presets.json:32  "Current models require max_completion_tokens rather than max_tokens."
```

Current OpenAI models reject the **presence** of `max_tokens` with a 400
`unsupported_parameter`, whether or not `max_completion_tokens` accompanies it.
If that holds, every profile on the `openai` preset — all three suggested models
— fails on its first turn *and* on Test connection, surfacing as the bare
`invalid_request` banner of C-10.

The only test (`tests/test_llm_provider_seam.py:606`) asserts field presence
through a `MockTransport` and **cannot** see this. This is precisely the question
ADR-0002 exists to answer, and it is still open. **One `max_tokens=1` call to a
real OpenAI endpoint settles it.** Do not merge the OpenAI wire without it.

---

## Security pass — remaining findings

* **S-M1 (MEDIUM, reviewer probe)** — `_validate_preset_base_url_lock`
  (`services/llm_config.py:220`) gates on `entry["auth"] != "bearer"` while
  `derive_key_env` (`:110`) decides from `entry["key_env"]`. The two predicates
  disagree; the invariant survives only because no shipped preset has
  `auth != "bearer"` with a non-null `key_env`. Fail-open for the next preset
  added. Fix: gate on `key_env is not None`.
* **S-M2 (MEDIUM) — a third-party key is sent to Anthropic. VERIFIED HERE.**
  The anthropic branch builds `anthropic.Anthropic(api_key=key_value)` and
  **never reads `profile.base_url`** (`services/chat_service.py:1750`). Nothing
  validates `wire` against `preset`, so `preset="openai", wire="anthropic"`
  resolves `key_env=OPENAI_API_KEY` and ships that live key to
  `api.anthropic.com` every turn — with the ignored `base_url` meaning the
  operator cannot tell from Settings.
* **S-M3 (MEDIUM, reviewer probe)** — `probe_models()` echoes upstream-controlled
  strings into the connection-test response (`routers/chat.py:557,560`), against
  that route's own docstring. Combined with the accepted SSRF surface
  (`169.254.169.254`, `127.0.0.1`, RFC1918 all pass `_validate_base_url`) this
  makes Test connection a narrow read primitive, and the request carries
  `Authorization: Bearer <profile key>`.
* **S-M4 (MEDIUM) — the sibling regex that never got fixed. VERIFIED HERE.**

  ```
  llm_config._SLUG_RE          = ^[a-z0-9-]{1,48}$      matches "evil\n" -> True
  app_secrets._LLM_KEY_SLOT_RE = \A[A-Z0-9_]{1,64}\Z    matches "EVIL\n" -> False
  ```

  Task 3 fixed this `$`-before-newline hole in `app_secrets` and left the twin in
  `llm_config`. A profile id with a trailing newline persistently 500s the whole
  settings pane, recoverable only by a request the UI cannot construct.
* **S-L1** keys under 8 chars are never redacted (`redaction.py:46`) while
  `app_secrets.validate_value` enforces no minimum — two layers disagreeing about
  what a key is. **S-L2** malformed IPv6 `base_url` → uncaught `ValueError` → 500.
  **S-L3** `preset` is unvalidated free text (fails *closed*, but is persisted and
  rendered). **S-L4** the httpx log cap runs only under the desktop bootstrap.
  **S-L5** a test named `..._is_callable` no longer asserts callability — its
  assert migrated into a neighbouring function.

### Security: attacked and HELD (so you know what the gap list excludes)

`extra="forbid"` rejects a client-set `key_env` — the central
credential-exfiltration claim holds at the definition. Preset spoofing via case,
zero-width, fullwidth, path separators and trailing space all fail **closed**.
`set_active_profile`'s super-admin check is present and **mutation-tested**:
swapping in a check-less dispatcher makes its test FAIL, so it is not tautological.
No second instance of that route/tool-twin shape exists. `_AUTH_PUBLIC_PATHS` is
unwidened; the `/chat/health` revert holds. Redirects are not followed, so the
auth header is never re-sent. `ProviderError` messages leak no host or URL.

---

## Correctness pass — remaining findings

**HIGH**

* **C-3** `reconstruct_network_from_image` (`services/chat_tools.py:2723-2758`)
  is entirely profile-blind: it still builds an Anthropic client and hardcodes
  `DEFAULT_MODEL`. The spec's capability-enforcement requirement was skipped;
  only a redaction line landed. On an Ollama-only deployment it either demands an
  Anthropic key the operator deliberately lacks, or silently bills a
  `claude-sonnet-5` call and ships the user's image to Anthropic. Zero test
  coverage (`grep -c reconstruct tests/test_chat_profile_binding.py` → 0).
* **C-4 VERIFIED HERE** — an unknown or deleted `profile_id` silently runs the
  turn on a different provider, wire and model, with **no error frame**.
  `services/llm_config.py:441-454` falls through to `by_id[active_id]`, and its
  docstring documents that as intended — but `frontend/src/api/chat.ts:44` states
  the contract as *"the server resolves it and refuses an unconfigured id"*. A
  disproven claim written into source as fact. User picks `local-ollama`
  believing their data stays on localhost; the prompt goes to `api.moonshot.ai`.
  This is the ADR-0001 shape: unresolvable renders as success.
* **C-5** `startNewChat()` with no project open still eats the next project's
  history — `ChatPanel.tsx:1161`'s `if (!currentProject) return` sits above the
  `consumeSuppressHydrationOnce()` at `:1174`. The same bug `newChatSeq` was
  introduced to fix, reached through a different early return.
* **C-6 VERIFIED HERE** — `DELETE /settings/llm/profiles/{id}/key`
  (`routers/chat.py:491`) wipes the instance-wide provider key. Its sibling
  `DELETE /profiles/{id}` guards with
  `target.key_env.startswith("PYPSA_GUI_LLM_KEY__")` at `routers/chat.py:465`,
  with a docstring explaining that clearing a shared slot "would silently break
  every other profile still relying on it". The predicate was added to one route
  and not the other. Clearing one profile's key kills chat instance-wide.
* **C-7** (ADR-0001) "no key configured anywhere" renders as "Uses
  ANTHROPIC_API_KEY from the environment" — `AssistantModelSettings.tsx:102-109`
  never reads `key_present`. The one screen built to tell an admin why chat is
  broken asserts a credential source that does not exist.

**MEDIUM** — all four of C-8..C-11 are the same structure: two correct halves,
no test crossing the seam.

* **C-8** the A8 fallback is undone on the very next turn — `routers/chat.py:1035`
  sets `session.model` unconditionally where master did it only `if body.model:`.
  User is told "falling back to Sonnet", then re-sent to the rate-limited model.
* **C-9** omitting `profile_id` *causes* the re-override the spec says it
  prevents (`routers/chat.py:1022-1040`). The frontend send path is correct; the
  server breaks its own documented contract.
* **C-10** three provider error kinds (`invalid_request`, `upstream_error`,
  `sdk_not_installed`) have no `KIND_COPY` entry and render raw snake_case as the
  banner title (`ChatPanel.tsx:533`). `invalid_request` is the *expected*
  first-run failure of the new Add-model form. Also unrouted: `not_authorized`,
  `unknown_profile_id`, and `tool_running`.
* **C-11** the settings-outage state built at two layers is unreachable: the nav
  gate above it (`Sidebar.tsx:1287`, `hooks/useLLMSettings.ts:48`) is false on
  `isError`, hiding the door to the pane that was built to report the outage.
* **C-12** the `missing_api_key` banner names the *session's* profile while the
  key form branches on the *instance-wide* active profile — two questions, one
  banner. The Task-15 deep-link built for this never fires.
* **C-13** a per-profile secret is orphaned on disk with no enumeration that can
  ever collect it (flip a profile bearer→none, then delete it).
* **C-14** any profile write permanently erases profiles that failed validation
  at load — load-skip plus write-whole-file, each correct alone.
* **C-15** `redact_for_log` is strictly weaker than `redact_secrets_in_str`, and
  both are applied to the same exception three lines apart. Bounded today only
  because every shipped `key_env` is managed; `derive_key_env` has no
  `is_managed_key` check, so the first preset naming an unmanaged variable makes
  it live.
* **C-16** `_redact_for_persist` (`chat_service.py:372`) recurses dict *values*
  but not dict *keys*. Reachable via `POST /api/chat/import` — whose docstring
  claims the opposite — and organically via model-authored `tool_use.input` keys.
* **C-17** `_CREDENTIAL_QUERY_KEYS` is a 3-item **denylist**, fail-open for
  everything unlisted: `access_token`, `api-key` (Azure OpenAI's own parameter
  name, one character from the covered `api_key`), `subscription-key`, `sig` all
  accepted. A query-string credential is not an `app_secrets`-managed value, so
  no redaction path can ever scrub it.
* **C-18** the spec's subprocess-env hardening was never implemented.
  `routers/local_settings.py:164` spawns with no `env=`, and this branch is what
  made it matter: master had only `ANTHROPIC_API_KEY` in the environment, where
  now all four provider keys plus every `PYPSA_GUI_LLM_KEY__*` slot are injected
  and inherited by every spawn. Absent from the handover's task table.

**LOW** — C-19..C-29: the httpx log cap is desktop-only and its only test is an
`inspect.getsource` string match that would pass with the lines in dead code; a
legitimately empty `{"data": []}` is indistinguishable from a failed call, making
a fresh Ollama's first run report `model_not_found` with no list; unredacted
`{exc}` in `vision_invalid_json`; `_profile_from_dict` type-checks only
`tools`/`vision`, so `max_output_tokens='not-an-int'` reaches the provider
request; the stub reports `len(TOOLS)` where the real path reports what was sent;
`MANAGED_KEYS` has zero non-test consumers while its comment names callers that
do not exist; `chat_tools_schema.py:1698` still states the rationale commit
`2c13cbc7` **rejected**; `_require_super_admin`'s 403 detail talks about
Anthropic on every route; the super-admin gate is a first-statement call rather
than a `Depends`, so no test can sweep it and a new route added without it is
both unguarded and untested; and `_to_openai_messages` silently drops a whole
user message, text included, when content parts come back empty.

### Correctness: checked and CLEAN

`is_managed_key` as written (anchored both ends, no newline hole, the `9d9973f2`
fix holds). `MIN_SUBSTITUTION_LENGTH` off-by-one — exactly-8 *is* substituted.
Shell-exported keys are scrubbed. `llm_openai_compat` never logs the key, the
Authorization header, or the base_url. **The zero-config invariant holds** for a
session that never sets `model` — two built-ins synthesized, nothing written to
disk, routed through the untouched `_build_anthropic_client()`. Preset↔shared-key
exfiltration exhaustively traced and closed. `set_active_profile` authorization
verified at its definition with no case where the HTTP route refuses and the tool
succeeds. Both ADR-0001 three/four-state splits (chat dropdown, settings list)
are done right. Packaging is correct — `presets.json` bundled, `user.env` and
`llm-profiles.json` on `check_bundle.FORBIDDEN_FILES`.

---

## The process lesson, which is the reusable part

Findings C-8, C-9, C-12 and C-15 are one shape: **Task 7's A8 hoist is tested by
calling `chat_service` directly, and Task 7's router binding is tested by driving
`/stream`. Both green. Neither crosses.** A handful of two-turn-through-the-router
tests would have caught most of this list.

This is the standing rule restated with evidence: a green suite answers "did
anything I already asserted break", never "is this right". Twenty-four per-task
reviews and a full green gate did not see 38 findings, because per-task review is
structurally blind to the seams between tasks.
