# LLM provider configuration and switching — implementation handover

**Branch:** `feature/llm-provider-config` (36 commits ahead of local `master`)
**Head at handover:** `cf3d3102`
**Status:** all 16 planned tasks implemented and individually reviewed.
**NOT done:** the ADR-0002 live probes, and the 38 findings the two closing
reviews returned (both say DO NOT MERGE — see
[the findings doc](../findings/2026-09-04-llm-provider-config-closing-reviews.md)). See
[What is genuinely not finished](#what-is-genuinely-not-finished) — read that
section before deciding this is ready to merge, because a green suite here
does **not** mean what it usually means.

---

## What this delivers

The assistant could only run on Anthropic. It can now run on OpenAI, Moonshot
(Kimi), Qwen (DashScope), Ollama, LM Studio, or any OpenAI-compatible
endpoint, selected through **profiles**.

A *profile* is the unit a user switches between: provider wire + model +
credential slot + declared capabilities. A super-admin configures them; every
member can pick among them in the chat panel.

**Zero-config is preserved and is the plan's central safety net.** With no
`llm-profiles.json` and only `ANTHROPIC_API_KEY` set, behaviour is
byte-identical to before: two built-in Anthropic profiles are synthesized in
memory, nothing is written to disk, and no user ever meets the profile concept.
This was verified by construction — the full backend gate (3100 passed) ran in
an environment with no key and no profiles file.

### Source map

| Layer | Files |
|---|---|
| Config store | `backend/services/llm_config.py`, `backend/presets.json` |
| Secrets | `backend/services/app_secrets.py` (allowlist is now a *rule*), `backend/services/redaction.py` |
| Providers | `backend/services/llm_anthropic.py`, `llm_openai_compat.py`, `llm_provider.py`, `llm_fake.py` |
| Turn path | `backend/services/chat_service.py` (session↔profile binding, capability enforcement, A8 fallback) |
| Routes | `backend/routers/chat.py` (`/chat/settings/llm/*`, `/chat/profiles`) |
| Chat tool | `backend/services/chat_tools.py` + `chat_tools_schema.py` (`set_active_profile`) |
| Frontend | `frontend/src/api/llmSettings.ts`, `components/AssistantModelSettings.tsx`, `components/ChatPanel.tsx`, `store/chatStore.ts`, `hooks/useLLMSettings.ts` |

Authoritative design: `docs/superpowers/specs/2026-08-13-llm-provider-config-and-switching-design.md`
Plan with per-task detail: `docs/superpowers/plans/2026-08-14-llm-provider-config-and-switching.md`

---

## ⚠️ MERGE PRECONDITION — this branch must not be split

**Any cherry-pick or partial revert that lands the provider wiring without the
redaction widening ships a live third-party API key into logs and `chat.jsonl`
unscrubbed — and every test still passes.**

Why: `master`'s `redact_secrets_in_str` is pattern-only (`sk-ant-*`,
`key=value`, `bearer …`). That is safe there for one accidental reason —
`llm_openai_compat` has no production caller on master, so no third-party key
ever reaches a log. This branch removes **both halves** of that safety at once:
it gives the provider a caller (Tasks 6–7) *and* adds per-profile key slots
whose values match none of those patterns (Tasks 1–3). The sole compensating
control is Task 4's value-substitution widening of `redaction.py`.

So the hazard is not the merge — it is a **split**. Inspection cannot catch it:
"the redaction commit is in the diff" is equally true of an arrangement that
dropped the caller and one that dropped the redactor.

**This is enforced, not remembered:** `backend/tests/test_no_split_merge_precondition.py`
plants a key value matching none of master's patterns, drives a real turn that
logs and persists it, and asserts it is absent from both sinks. A second test
disables the substitution and asserts the value **does** leak — so the first
test passes because of the control, not because there was nothing to redact.

---

## What is genuinely not finished

### 1. ADR-0002 live probes — STILL UNMET. The plan cannot honestly be called done.

> **Update 2026-09-04.** The Anthropic probe was RUN, not skipped, and it
> **failed**: the machine's stored `ANTHROPIC_API_KEY` is revoked. Anthropic
> returns `401 authentication_error: API key is invalid` for that key with
> **no PyPSA code in the path** (plain `curl`), and the stored value is clean
> — 108 chars, `sk-ant-` prefix, no whitespace, no quotes — so this is a dead
> credential, not a mangled one. Verified identically under `-e default` and
> `-e test`.
>
> What that DID establish: the probe harness works end to end, and the live
> failure path is correct — a real upstream 401 maps to a terminal
> `unauthorized` with the clean message `ANTHROPIC_API_KEY rejected by
> Anthropic API` and no key material in the log line.
>
> What it did NOT establish, and this is the part that still blocks the ADR:
> **no token has ever been streamed from a live model through this branch.**
> The success path of the anthropic wire remains UNPROBED. Supply a valid key
> and re-run before calling the branch done.
>
> One thing the probe surfaced and CLEARED, recorded so nobody re-opens it:
> the terminal-error frame sequence is
> `['session_init', 'error', 'session_done']`, which ends `session_done`
> rather than the `turn_done` the pinned invariant names as a turn's last
> frame. That is fine — `ChatPanel.tsx:1934` (`session_done`) and
> `ChatPanel.tsx:1938` (`error`) both call `closeStream()`, so the composer
> unlocks on a failed turn. The adjacent worry, that `voiceTurnRef` is
> cleared only under `turn_done` and would make the NEXT turn speak aloud
> after a failed voice turn, is also unfounded: `ChatPanel.tsx:1972`
> re-assigns it from `dictatedRef` on every send.

`docs/adr/0002-chat-changes-need-a-live-api-probe.md` states that no test in
`backend/tests/` constructs a real client, so a green suite does **not** verify
a chat change, and *"its absence is a defect in the change, not in the suite."*

Both probes are written and drive the full production path (profile store →
`_provider_for_profile` → `run_turn`). The anthropic probe now RUNS and fails
on a revoked key (see the update above); the **openai probe still skips**,
because no local endpoint is installed here. Their skip reasons say the
requirement is UNMET rather than "not configured" — a skip that reads as a
pass is the failure mode the ADR exists to prevent.

To close it:

```bash
# Anthropic wire — one short turn, negligible credit.
# `-e test` is the canonical env (the one `pixi run gui-tests` resolves to).
PYPSA_GUI_TEST_LIVE_ANTHROPIC=1 ANTHROPIC_API_KEY=sk-ant-… \
  pixi run -e test python -m pytest \
  pypsa-gui/backend/tests/test_llm_provider_seam.py -k live_probe_anthropic -v

# OpenAI wire — save an openai-wire profile first, then:
PYPSA_GUI_TEST_LIVE_OPENAI_PROFILE=<profile id> \
  pixi run -e test python -m pytest \
  pypsa-gui/backend/tests/test_llm_provider_seam.py -k live_probe_openai -v

# Fuller end-to-end against a running backend:
pixi run -e test python pypsa-gui/backend/smoke/run_chat_smoke.py --profile <id>
```

Note this debt predates the branch: the already-merged provider **seam** also
shipped without a live probe. These probes close both.

### 2. The two closing reviews — NOW COMPLETE. Both say DO NOT MERGE.

> **Update 2026-09-04.** Both reviews have now run against `93ddbf79`,
> independently and in parallel, and both return **DO NOT MERGE**. 38 findings:
> **[2026-09-04-llm-provider-config-closing-reviews.md](../findings/2026-09-04-llm-provider-config-closing-reviews.md)**
> — read that before touching this branch.
>
> The two that change what the branch *is*:
>
> 1. **`tools: false` is advertised but never enforced.** `profile.tools` is
>    read only on outbound paths; the dispatch loop iterates `tool_uses`
>    ungated, so an endpoint returning tool_use blocks it was never offered has
>    them executed. Of 121 tools, 34 are confirmation-gated and **87 are not**,
>    31 of those mutating. This inverts the branch's own premise: the headline
>    feature is pointing the assistant at an arbitrary endpoint, which promotes
>    that endpoint to attacker-controlled input.
> 2. **The OpenAI wire may be dead on arrival.** The request sends both
>    `max_tokens` and `max_completion_tokens`; the branch's own `presets.json`
>    help text says current models reject the former. The only test asserts
>    field presence through a `MockTransport` and cannot see it. One live call
>    settles it — this is exactly what ADR-0002 exists for.
>
> Nearly every finding is a *composition* failure: two correct halves with no
> test spanning the seam. Twenty-four per-task reviews and a green gate saw
> none of them.

The original entry, kept for the record — the final whole-branch review and the
adversarial security pass were dispatched and **killed by an API session limit**,
along with four sub-agents. Neither produced a verdict at the time. What they
were scoped to cover:

- Whole-branch review over `master..HEAD` (36 commits) — cross-task coherence,
  dead code, surviving false-fact comments, and triage of the deferred items in
  `.superpowers/sdd/2026-08-14-llm-provider-config-and-switching/progress.md`.
- Adversarial security pass — scoped by the fact that an earlier review cleared
  the seam on the grounds that `llm_openai_compat` had *no production caller*.
  **This branch is exactly what makes that verdict expire.**

Partial security verification was done by hand in the meantime — see below.

---

## Security posture: verified vs unverified

**Verified by direct execution (controller, 2026-09-01):**

| Control | Result |
|---|---|
| Cross-host redirect carrying the auth header | **Closed.** `httpx.Client` defaults `follow_redirects=False`; our construction does not override it. |
| Client-settable credential slot | **Closed.** `ProfileIn` uses `extra="forbid"` and has no `key_env`; the slot is derived server-side. |
| Hand-edited `llm-profiles.json` smuggling a shared key onto an attacker host | **Closed.** The preset↔`base_url` coupling is validated on **load**, not only on save — the attempt is rejected and falls back to built-ins. |
| Prompt-injected model configuring an exfiltration route | **Closed.** `set_active_profile`'s entire input surface is `['profile_id']`, which must match a configured profile; `chat_tools` calls neither `save_profiles` nor `set_secret`. |
| Privilege escalation via the chat tool | **Fixed during the plan** (`2c13cbc7`). It reached the same instance-wide store as the super-admin HTTP route with no role check; a member could flip the provider for every org by approving their own confirmation card. Confirmation-gating is not a role boundary. |

**Known and accepted, stated rather than hidden:**

- **SSRF reach.** `base_url` validation rejects userinfo, credential-shaped
  query params, and non-http(s) schemes — but **accepts** `169.254.169.254`,
  `127.0.0.1`, and RFC1918 addresses (measured). A super-admin can therefore
  aim the provider at internal hosts and use the connection-test verdict and
  latency as an oracle. The actor is already privileged, but this is a genuine
  new outbound primitive driven by stored config. Decide whether an allowlist
  or egress policy is wanted.
- `trust_env` is left at httpx's default `True`, so `HTTP(S)_PROXY` is honoured.
  Environment variables are trusted by policy and corporate-proxy support is
  desirable — noted, not a finding.

**Not yet attacked (needs the killed security pass):** preset spoofing via
unicode/case tricks on preset ids and slugs; whether one task's guarantee is
undone by another's code.

---

## Verification status

| Gate | Result at `cf3d3102` |
|---|---|
| `pixi run gui-tests` (canonical backend) | 3100 passed, 21 skipped |
| frontend `npm test` | 151 files / 1481 tests |
| `npx tsc --noEmit` | clean |
| Packaging coverage | **claimed** — `npm run build` was run first; a stale `dist/` passes against the wrong bytes with an identical count |

Run gates through pixi. Bare `npm`/`npx` exits **127** (node is pixi-provided),
and bare `pytest` runs the wrong env where 7 pywebview tests fail by design.

```bash
pixi run gui-tests                                              # backend, repo root
cd pypsa-gui/frontend
pixi run --manifest-path ../../pixi.toml npm run build          # BEFORE gating
pixi run --manifest-path ../../pixi.toml npm test
pixi run --manifest-path ../../pixi.toml npx tsc --noEmit
```

---

## Task-by-task record

Every task was implemented, independently reviewed, and fixed where review
found defects. Reviews caught **real** problems repeatedly — several of them in
work that had passing tests.

| # | Task | Commits | Review outcome |
|---|---|---|---|
| 1 | `llm_config` profile store | `3c3c19d6`→`28642b8d` | 1 fix round — credential-query check was **case-bypassable** (`?API_KEY=` accepted) |
| 2 | Preset catalogue + packaging | `c5fe3b8b`, `a1604692` | Approved. Implementer caught a real bug in the plan's own PyInstaller `datas` example |
| 3 | `app_secrets` allowlist → rule | `f766ccd0`→`9d9973f2` | 1 fix round — `$` anchor matched **before a trailing newline** |
| 4 | Redaction widening | `4d3e3bd3`→`44a4339b` | 1 fix round — regex ran before substitution, **fragmenting a secret and leaking the remainder** |
| 5 | Model constants → `llm_config` | `1e231492` | Approved first pass |
| 6 | Profile-built providers | `1a2c5ad8`→`ced04971` | 2 fix rounds — `base_url=None` crash; synthetic tool-id **collided with upstream's own id shape** (a defect in my plan text) |
| 7 | Session↔profile binding | `7f55646e`→`32a0949a` | 2 fix rounds — `GET /history` **reverted a live session mid-turn**; then a residual TOCTOU across two lock acquisitions |
| 8 | Capability enforcement + prompt split | `867509f4`→`8ac14686` | 2 fix rounds — silent image drop; and my own contradictory constraint forced a needless change to every user's prompt |
| 9 | Settings + profiles routes | `97aa4118` | **False premise in my brief** caught before merge — I asserted `/chat/health` was unauthenticated; it is not, and "fixing" it opened anonymous recon |
| 10 | `set_active_profile` tool | `a4080245`→`2c13cbc7` | **Privilege escalation** found in code I wrote inline; also closed a latent fail-open in `_safety_tier_for`'s docs |
| 11 | Backend close-out | `7a5a087e` | DashScope preset URL was **wrong** — re-verified against live vendor docs and corrected |
| 12 | Frontend API layer | `a6f34417` | Re-anchoring found a **fifth** `ChatModel` site the plan missed |
| 13 | Dropdown, `startNewChat`, frames | `270f48ea`→`b60a7d9d` | 1 fix round — hydration-suppression flag **silently dropped a project's history**; fail-open wire check |
| 14 | Unified `KIND_COPY` | `b1d0156c`→`10eeae18` | 1 fix round — a **disproven backend claim** had been written into a source comment as verified fact |
| 15 | Settings pane + deep-link | `eae5d89a`→`f745b483` | 2 fix rounds — an outage rendered identically to "not for you" (ADR-0001), fixed at **two layers** |
| 16 | Gates + precondition proof | `cf3d3102` | All gates green; no-split precondition proven with discrimination evidence |

---

## How to continue

1. **Read the ledger.** `.superpowers/sdd/2026-08-14-llm-provider-config-and-switching/progress.md`
   is the full decision record — every finding, adjudication, deferred minor,
   and correction. It is git-ignored, so it exists only on the machine that ran
   the plan; copy it out if you need it elsewhere.
2. **Re-run the two killed reviews** (above). Do not merge on the strength of
   green gates alone — this plan's reviews found defects in green code more
   than a dozen times.
3. **Close ADR-0002** with at least the Anthropic probe.
4. **Do not split the branch.** See the merge precondition.

### Carried follow-ups (recorded, out of this plan's scope)

- **`TOOL_ERROR_BANNER_KINDS` drift, one direction unguarded.** The subset test
  catches a routed kind losing its copy, but not a kind that *should* route and
  doesn't — which is the direction that actually bit us. Closing it needs a
  shared backend↔frontend manifest of kinds that can genuinely surface as
  `tool_error`, populated by a backend test that empirically triggers each.
- **`useLocalSettings` still collapses outage and unauthorized** into one state.
  Deliberately not widened into: it is pre-existing, its header defends the
  choice, and `useLLMSettings` documents the divergence as intentional.
- Two open findings on `master`, unrelated to this branch and recorded there:
  `docs/superpowers/findings/2026-08-27-lock-holder-email-reaches-the-model.md`
  and `2026-08-27-requeue-is-a-cross-user-overwrite.md`.
