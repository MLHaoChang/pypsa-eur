# pypsa-gui v6 Chatbot — Net-New Feature Research (ranked)

Synthesized 2026-06-08 from 4 independent read-only research agents (agentic/tool
capabilities, conversational UX, domain intelligence, reliability/cost/safety). Every
item is grounded in source (file:line in the per-dimension notes). Items already
**implemented this session** (retry/backoff, idle-session eviction, truncation marker,
model bump to opus-4-8 + mid-session switch, project-switch-mid-turn guard, 23 tool
fixes, ChatMarkdown) and items already in the two prior backlogs
(`CHATBOT_IMPROVEMENT_BACKLOG.md`, `CHATBOT_QA_BACKLOG.md`) are **excluded or merged**.

**Root finding (all four agents independently):** the system prompt is policy-only —
zero PyPSA domain guidance (no foresight modes, CF/curtailment/LCOE definitions, no
plausibility heuristics, no statistics-column quirks). The `/results/*` endpoints already
compute capacity factors, curtailment %, market value, CO2 shadow price, and LP duals;
the assistant pastes them as raw JSON. The biggest wins turn data the backend *already
produces* into expert interpretation, and cash in SSE payloads the frontend already
streams but discards.

---

## Master ranking (highest → lowest leverage)

| # | Feature | Dim | Value | Effort |
|---|---------|-----|-------|--------|
| 1 | **Domain-intelligence system-prompt module** — definitions, plausible ranges, foresight modes, statistics quirks, solver-error decoder seeded from CLAUDE.md | Domain | High | S–M |
| 2 | **Inline result rendering** — render `tool_result` payloads (already on the wire, discarded) as mini-tables/cards instead of a bare ✓ | UX | High | M |
| 3 | **Untrusted-content boundary** — wrap attachment + tool-result content in `<untrusted_data>` + system-prompt clause; prompt-injection defense for a destructive-tool agent | Safety | High | M |
| 4 | **Model sanity-check report (pre + post solve)** — flag out-of-range CF, ~100% curtailment, lost load, non-binding/implausible CO2 cap, islanded buses, zero/flat demand; reuse the `Issue` shape | Domain | High | M |
| 5 | **Click-to-navigate chat ↔ canvas** — a component name in a tool call selects it on the map / opens its properties (uses `args.name` already on every frame) | UX | High | M |
| 6 | **Composite synthesis tools** — `diagnose_results` ("why is my LCOE high?") + `solve_overview` (post-solve triage in one call); collapse the two most-repeated multi-call sequences | Agentic | High | S–M |
| 7 | **Guided what-if** — `clone → mutate → solve → compare` macro under one confirmation ("what if I double onshore wind cost?"); all primitives already exist | Domain | High | M–L |
| 8 | **Diff preview before destructive write** — confirmation card shows old→new field deltas (from React Query cache) + cascade impact, not raw JSON args | UX/Agentic | High | M |
| 9 | **Cross-session spend cap** — durable per-project/per-day token budget (the only current ceiling resets on every reload/eviction); data already in `chat.jsonl` usage | Cost | High | M |
| 10 | **`tool_running` status chip + render buffered `tool_progress`** — the frame is emitted and the progress is stored, but no UI case handles either; approved destructive tools jump silently to ✓ | UX | Med–High | S |
| 11 | **Explain-the-solver-error** — decode linopy/xarray/HiGHS failures into plain language + fix; CLAUDE.md already has the symptom→cause table | Domain | High | S–M |
| 12 | **Scenario-comparison narration / structured A-vs-B diff** — clean delta table (objective, capacity by carrier, emissions) + causal narration, independent of the Compare rail UI | Domain/Agentic | Med–High | M |
| 13 | **`undo_my_last_chat_action`** — session-scoped undo using the existing `agent:<session6>` audit tagging (closes a gap the tagging already sets up) | Agentic | Med | S |
| 14 | **Secrets/PII redaction before `chat.jsonl` persistence** — `_redact_for_log` is log-only; raw turns propagate into snapshot/copy bundles | Safety | Med | S–M |
| 15 | **Suggested-prompt chips / slash commands** — discoverability for a 70+ tool agent; empty state has no conversational starters | UX | Med | S–M |
| 16 | **Per-tool execution timeout** — non-solver tools run synchronously with no deadline; a hung read/write freezes the SSE thread | Reliability | Med | M |
| 17 | **Run-report markdown generation** — one-command shareable brief (objective, cost split, top carriers, emissions vs cap, sanity flags); export plumbing already exists | Domain | Med | S |
| 18 | **Configurable confirmation policy** — per-tier auto-approve for trusted single-user mode; cuts the confirm round-trip without weakening the multi-user default | Safety/UX | Med | S |
| 19 | **Single in-flight-turn concurrency guard** — two tabs on one `session_id` can interleave the `messages` deque → API-rejected sequence | Reliability | Med | S |
| 20 | **`/chat/metrics` observability** — turn latency, retry freq, error-kind distribution, token spend; everything's computed then discarded | Observability | Med | M |
| 21 | **Message edit / resend + regenerate-last** | UX | Med | M |
| 22 | **Pinned context strip** — always-visible bound-project + solved-state header | UX | Med | S |
| 23 | **Expose `aggregate_load_profile` as a read tool** — answers "total/peak demand?" in one call; handler exists, not surfaced | Agentic | Med | S |
| 24 | **Price-driver / marginal-unit + congestion-dual narration** | Domain | Med | M |
| 25 | **Suggest-next-modeling-step advisor** | Domain | Med | S–M |
| 26 | **Rate-limit `/stream`** (per-session/IP token bucket) | Safety | Med | S–M |
| 27 | **Conversation export/import** (portable JSON transcript) | UX | Low–Med | S |
| 28 | **Streaming "Continue" affordance** after Stop / tool-cap | UX | Low–Med | S |
| 29 | **`plan_then_execute` scaffolding** — explicit multi-step plan tracked across the 25-call ceiling | Agentic | Med | M |
| 30 | **Structured `Returns:` schema hints** on high-traffic read tools — cut field-name hallucination | Agentic | Low–Med | S |

---

## Recommended first slice (do these together)

The cheapest, highest-trust cluster that compounds:

- **#1 domain-prompt module** is the keystone — it's S–M and *unlocks* the narration value
  in #4, #7, #11, #12, #17, #24, #25 (those become prompt recipes once the model knows the
  domain). Ship it first.
- **#2 + #10** are pure UX dividend — the data is already streaming and thrown away.
- **#3 untrusted-content boundary** is the one genuine safety gap worth front-loading given
  the agent wields destructive + execution tools.

That four-item slice (#1, #2, #3, #10) is roughly S+M+M+S and turns the assistant from a
JSON-dumping CRUD driver into an interpreting, safer, legible analyst — before any of the
larger M–L capabilities (#5 click-to-navigate, #7 what-if) are tackled.
