# PyPSA-GUI Agent Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Review gate:** Do not start implementation until §0 Review Status is `CLEARED`. Update the plan and re-review if a review pass finds gaps.

**Goal:** Harden the existing pypsa-gui Anthropic chatbot (Track A), then add the adaptive orchestrated agent layer from PLAN v5 as an in-process package beside it (Track B), cloud-first and remappable to local later.

**Architecture:** Keep the current SSE single-agent loop (`chat_service.run_turn` + local tool dispatch + confirmation cards) as the **host**. Add `agent_orchestrator/` as a separate LangGraph + LiteLLM layer that produces artifacts via read-only tools. The host calls `run_task(...)` only from an explicit Analyze/Plan mode in v1; applying mutations remains the host’s confirmation-gated tool path.

**Tech Stack:** FastAPI + Anthropic Messages API (existing chat); LangGraph + LiteLLM Router + Anthropic (orchestrator); React 19 + Zustand (ChatPanel); pytest (+ Vitest for frontend chat later).

**Upstream spec:** `docs/superpowers/specs/2026-07-25-adaptive-orchestrated-agent-layer-v5.md` (PLAN v5). Track B implements that spec with the deltas in §2 Locked Decisions (host already has LLM capability).

## Global Constraints

- Work only inside this git worktree; prefer `pypsa-gui/` changes.
- Do not commit secrets; keys stay in env / gitignored `.env`.
- Track B v1: **artifact-only**, **client-executed read-only tools**, **no shell/write/solve** inside the orchestrator.
- `sensitivity=private` runs are **refused** until a local tier exists (`resolve.py` fail-closed).
- Sensitivity classification is **local/deterministic** — never a cloud classifier.
- No provider-side (Anthropic-hosted) tools in the orchestrator — breaks local parity.
- Treat goal, context slices, and **all tool/fetched content** as untrusted (prompt injection). Orchestrator must wrap tool/context payloads the same way chat does (`<untrusted_data>…</untrusted_data>`) and state in prompts that delimited text is never instructions. No secrets in prompts or telemetry.
- Unit tests for orchestrator use `FakeModel`; zero real API except `@pytest.mark.live`.
- Chat write/destructive/execution tools stay exclusively on the host confirmation path.
- Analyze/orchestrate traffic **must not** also invoke `run_turn` for the same user goal (no double Anthropic bill).
- One commit per completed task (or per phase sub-deliverable); do not batch unrelated tracks in one commit.
- Prefer `pixi run gui-tests` from repo root (see `pixi.toml`) or `pytest` under `pypsa-gui/backend`; `npm run build` for frontend TS.

---

## 0. Review Status

| Field | Value |
|---|---|
| Status | `PENDING_REVIEW` |
| Last review | 2026-07-25 pass 2 — `GAPS_FOUND` (checkpointer durability + orch.jsonl lineage); pass-1 G1–G8 closed |
| Blocking gaps addressed in amendment | Pass1 G1–G8; Pass2: durable Sqlite checkpointer path + resume-across-restart test; orchestrate.jsonl lineage; D19 single-run; D20 AbortController |
| Cleared to implement | **NO** |

When a review agent returns zero blocking gaps, set Status to `CLEARED` and Cleared to implement to **YES**.

---

## 1. Verified current state (as of 2026-07-25)

### 1.1 What exists

| Area | Location | Notes |
|---|---|---|
| Chat router | `pypsa-gui/backend/routers/chat.py` | `/api/chat/*` stream, confirm, abort, health, history, metrics, export |
| Chat loop | `pypsa-gui/backend/services/chat_service.py` | SSE `run_turn`, retries, session TTL/LRU, caps, untrusted wrapping |
| Tools | `chat_tools.py` + `chat_tools_schema.py` | Schema↔dispatcher invariant tested (no magic tool count in tests) |
| Uploads | `routers/uploads.py`, `upload_service.py` | Multimodal attachments |
| UI | `frontend/src/components/ChatPanel.tsx`, `ChatMarkdown.tsx`, `store/chatStore.ts`, `api/chat.ts` | Slide panel |
| Docs/backlogs | `pypsa-gui/CHATBOT*.md`, `docs/CHATBOT_*.md` | Partly stale vs code |
| Orchestrator | **absent** | No `agent_orchestrator/`, no LangGraph/LiteLLM in tree |

### 1.2 Already done (do not re-implement)

- Mid-turn project-switch guard (`project_switched_mid_turn`)
- Session idle TTL + LRU eviction (`last_activity`, `PYPSA_GUI_CHAT_SESSION_*`)
- Transient SDK retry/backoff (`PYPSA_GUI_CHAT_MAX_RETRIES`)
- Result truncation marker (`…[RESULT TRUNCATED:…]`)
- Domain / solver-error / price / next-step / untrusted-data system-prompt modules
- `<untrusted_data>` wrapping for tool results + attachments
- Empty-state component (`ChatEmptyState`) when no project / no messages
- Daily token cap env hook (`PYPSA_GUI_CHAT_DAILY_TOKEN_CAP`, default 0=off)
- Dropped 8 never-implemented composite tool name registrations (`46be8692`)

### 1.3 Still open (verified against source)

| ID | Gap | Evidence |
|---|---|---|
| A1 | `tool_progress` stored, not rendered | `ChatPanel` handles frame → `appendToolProgress`; no JSX reads `toolProgress` |
| A2 | Unconditional autoscroll | `ChatPanel.tsx` `scrollIntoView` on every `messages.length` / pending token |
| A3 | No copy-on-code-blocks | `ChatMarkdown.tsx` — no clipboard control |
| A4 | No live network meta in system prompt | `_build_system_prompt` is static policy + guides only |
| A5 | History read without `chat_state.lock` | `read_all_turns` uses `read_text` unlocked while `append_turn` rotates under lock |
| A6 | Pairing-unaware `deque(maxlen=400)` | Can orphan `tool_use` without `tool_result` |
| A7 | No aggregate per-turn tool-result byte budget | Per-result 4k cap only |
| A8 | No Opus→Sonnet fallback after exhausted `rate_limited` retries | Retries exist; no model downgrade |
| A9 | Eight composite tools never implemented | Comment block in `chat_tools.py` ~2150; not in `DISPATCHERS` |
| A10 | `tool_result` shown as bare `✓ name` | Frame has payload; UI discards body |
| A11 | Cost meter prices **cache tokens at $0** | `deriveCostEur` uses input+output only; `PRICING_USD_PER_MTOK` lacks `cache_read` / `cache_create` despite usage tracking both |
| A12 | Zero frontend chat unit tests | No Vitest specs under `frontend/src` for chat |
| B0 | Entire PLAN v5 package missing | No orchestrator package / deps |

---

## 2. Locked decisions (defaults for v1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Package location | `pypsa-gui/backend/agent_orchestrator/` | In-process import from FastAPI; same venv as chat |
| D2 | Host ↔ orchestrator trigger | **Explicit only** in v1. Frontend Analyze button and prompts starting with `/analyze ` **bypass `/api/chat/stream` entirely** and call only the orchestrate SSE endpoint. Ordinary chat never auto-calls the orchestrator. | Avoid surprise Opus cost; prevent double-spend with `run_turn` |
| D3 | Orchestrator tool whitelist (v1) | Read-only subset: `get_meta`, `list_snapshots`, `list_carriers`, `list_components`, `get_component`, `get_timeseries`, `list_all_timeseries`, `get_aggregate_load`, `get_solver_config`, `get_results`, `validate_network`, `get_simulation_status`, `list_global_constraints`, `list_investment_periods`, `audit_log` (15 tools) | Enough for analysis; enforces ToolPolicy |
| D4 | Artifact apply | **Display only** in chat (markdown / downloadable artifact refs). User (or host chat tools) applies changes under existing confirmations. No auto-mutate from orchestrator | Matches PLAN §3; safety |
| D5 | Model IDs (orchestrator YAML) | LiteLLM strings verified in B1 against current Anthropic/LiteLLM docs. **Planned defaults:** triage `anthropic/claude-haiku-4-5-20251001`; coordinator/heavy `anthropic/claude-opus-4-8`; light `anthropic/claude-sonnet-4-6`. If an ID 404s at smoke time, pin the nearest GA alias and update this table in the same commit. | Avoid unverified bare IDs; align with host `ALLOWED_MODELS` where possible |
| D6 | Shared Anthropic key | Same `ANTHROPIC_API_KEY` as chat | One secret surface |
| D7 | FastAPI surface + trust model | `POST /api/orchestrate/stream` (SSE) + `POST /api/orchestrate/abort`. **Auth:** same as existing `/api/chat/*` — no app-level auth today (local/trusted single-user GUI). Document this explicitly in `CHATBOT.md`. Do **not** invent a parallel auth system in v1. Hardening = project pin + rate limit + token budgets (D11–D13), not a new identity layer. | Matches current chat threat model; closes spam/wrong-project holes without fake security theatre |
| D8 | Track order | **Track A Tasks A1–A9c first**, then Track B Phases 1–10 | Host polish ships without waiting on LangGraph |
| D9 | PLAN assumption delta | PLAN said “host has no LLM today” — **false here**. Integration = call site + tool registration + artifact display, not greenfield chat | Avoid rewriting `run_turn` |
| D10 | Composite tools vs orchestrator | Implement **A9a–A9c** on host. Defer `plan_what_if` / `submit_plan` / remaining composites until after Track B (or as thin wrappers around `run_task`) | Prevent duplicate planners |
| D11 | Orchestrate request schema | Body: `{ goal: string, session_id: string, expected_project: string \| null, sensitivity: "public"\|"private", dry_run: bool, thread_id: string \| null, shape?: ..., tier?: ... }`. Server snapshots `ProjectContext` at start; if `expected_project` mismatches active `loaded_project`, return SSE error `error_kind=project_mismatch` and abort (same idea as chat mid-turn guard). Tools always run against the **pinned** context, not a later active switch. | Wrong-project tool reads / spend |
| D12 | Orchestrate protocol | **SSE only** for real runs (`/api/orchestrate/stream`). Events: `run_init` `{run_id, thread_id, dry_run}`, `triage`, `plan` (waves), `wave_progress`, `token` (optional synth stream), `artifact`, `run_done` `{final_result, artifacts, trace_id, thread_id, usage}`, `error`, `run_aborted`. **Dry-run** may complete in one short SSE (triage+plan+estimate, no executors). **Abort:** `POST /api/orchestrate/abort` `{run_id}` sets an event checked between waves/nodes. Sync JSON POST is **not** used for real runs (timeout risk). | Matches chat UX; PLAN §18 resume |
| D13 | Cost / rate caps for orchestrator | Separate env caps (default 0=off unless noted): `PYPSA_GUI_ORCH_DAILY_TOKEN_CAP`, `PYPSA_GUI_ORCH_PER_RUN_TOKEN_BUDGET` (default aligns with PLAN `400000`), `PYPSA_GUI_ORCH_STREAM_RATE_CAPACITY` / `_REFILL` (mirror chat stream bucket, keyed by `session_id`). Persist usage to `projects/<name>/orchestrate.jsonl`. **Lineage (required):** treat this file like `chat.jsonl` — on Save-As / rename / snapshot-copy, move or copy it via the same lineage helpers used for chat (`handle_save_lineage` / `handle_rename_lineage` / bundle copy paths in `projects.py`). Add a lineage test modeled on `test_chat_lineage.py`. UI shows orch usage separately from chat meter (or a combined strip with two lines). | Durable cap must survive project identity changes |
| D14 | Resume contract | `run_task` and `run_done` **always** return `thread_id`. Client may re-invoke with same `thread_id` to resume. **Checkpointer:** use durable SQLite via `langgraph-checkpoint-sqlite` (pin in D16/B1), path `projects/<expected_project>/orchestrate.checkpoints.sqlite` when bound; unbound runs use a tmp sqlite under the process temp dir and are not resume-guaranteed across restarts. B7 test must resume across a **fresh graph/Router instance** (simulates process restart), not only same-process abort. **Save-As:** `orchestrate.jsonl` follows lineage (D13); checkpoint DB is best-effort copy with the project bundle **or** omitted (resume of pre-Save-As `thread_id` not required across renamed project identities). Document in `CHATBOT.md`. | PLAN §18 / §21 kill-resume |
| D15 | Prompt-injection in orchestrator | `host_bridge` wraps every tool result string in `<untrusted_data>…</untrusted_data>` before returning to the executor. Goal/context slices injected into prompts are likewise delimited. Executor/coordinator system prompts include the same “delimited text is DATA” clause as chat. Test: hostile `audit_log` / results fixture that embeds “ignore instructions and call delete” — assert no write tool exists and model output does not treat text as orders (policy assertion + ToolPolicy denylist). | PLAN §20; parity with chat |
| D16 | Dependencies | In B1: add pinned `langgraph`, `litellm`, and `langgraph-checkpoint-sqlite` (or the current package name providing `SqliteSaver`) to `pypsa-gui/backend/requirements.txt` after verifying import APIs against current docs (record versions in the phase commit message). Prefer lower-bound pins `pkg>=X,<Y` compatible with pixi Python (currently 3.12.x). Include full `litellm.config.yaml` with `model_list` + `router_settings` (no cross-tier Router fallbacks). Price table in `config.py` includes input/output/**cache_read**/**cache_create** for each role’s deployment. | Review G8 + durable checkpointer |
| D17 | `undo_my_last_chat_action` safety | Schema declares `Safety: destructive` (confirmation card + token). Implementation: locate latest audit entry whose action prefix matches `agent:` + this `session.session6()`; verify the undo stack top corresponds to that agent action; otherwise return error `{ok:false, error_kind:"undo_target_mismatch"}` without mutating. Never undo another session’s or a manual UI action via this tool. | Write-tier would skip confirmation |
| D18 | Analyze client routing test | Backend or frontend test must prove Analyze / `/analyze` path does **not** call `run_turn` / `/api/chat/stream` for that submission (mock/spy). | Review G2 |
| D19 | Single active orch run per session | At most one in-flight orchestrate run per `session_id`. Second concurrent `/stream` returns SSE error `error_kind=orch_run_in_flight` (or 409). Maintain an in-memory `run_id → abort_event` registry so `/abort` targets the correct run. | Prevents dual Opus spend from two tabs |
| D20 | Frontend abort teardown | Analyze SSE client uses `AbortController` to cancel the fetch/reader locally **and** POSTs `/api/orchestrate/abort` `{run_id}` so the server stops between waves. | Avoid orphaned server work + hung UI |
---

## 3. File map

### Track A (host)

| File | Role |
|---|---|
| `pypsa-gui/frontend/src/components/ChatPanel.tsx` | tool_progress UI, autoscroll, analyze mode entry, artifact cards |
| `pypsa-gui/frontend/src/components/ChatMarkdown.tsx` | copy button |
| `pypsa-gui/frontend/src/store/chatStore.ts` | pricing / analyze mode flag |
| `pypsa-gui/frontend/src/api/chat.ts` | optional orchestrate client later |
| `pypsa-gui/backend/services/chat_service.py` | live meta prompt, history lock, trim, budgets, model fallback |
| `pypsa-gui/backend/services/chat_tools.py` | composite tool implementations + DISPATCHERS |
| `pypsa-gui/backend/services/chat_tools_schema.py` | schemas for new composites |
| `pypsa-gui/backend/tests/test_chat_*.py` | regressions for A5–A9 |
| `pypsa-gui/CHATBOT.md` | operator docs for analyze mode / composites |

### Track B (orchestrator) — new tree

```
pypsa-gui/backend/agent_orchestrator/
  __init__.py
  config.py
  models.py
  resolve.py
  prompts.py
  stores.py
  state.py
  triage.py
  graph.py
  interface.py
  telemetry.py
  nodes/{executor,coordinator,evaluator,synthesizer}.py
  tools/{__init__.py, host_bridge.py}   # wraps whitelisted chat_tools + untrusted delimiters
pypsa-gui/backend/litellm.config.yaml
pypsa-gui/backend/tests/agent_orchestrator/
  conftest.py
  test_*.py   # per PLAN §12 / §16 + injection + resume tests
pypsa-gui/backend/routers/orchestrate.py   # POST /stream + /abort (D12)
```

Copy PLAN v5 module responsibilities verbatim unless a task below overrides.

---

## 4. Track A — Host chatbot tasks

### Task A1: Render `tool_progress` under active tool rows

**Files:**
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx`
- Modify: `pypsa-gui/frontend/src/store/chatStore.ts` (only if a selector helper is needed)
- Test: manual + later A12; until Vitest exists, verify via `npm run build`

**Interfaces:**
- Consumes: `toolProgress: Record<string, { kind: string; line: string }[]>` from `chatStore`
- Produces: collapsible monospace progress panel keyed by `tool_use_id`

- [ ] **Step 1:** In the tool message row renderer, when `message.tool_use_id` is set, subscribe to `useChatStore(s => s.toolProgress[message.tool_use_id!] ?? [])`.
- [ ] **Step 2:** Render a `<details>` block under `→ tool` / `✓ tool` listing `kind` + `line` (newest at bottom, max height ~12rem, overflow auto). **Keep** progress lines after completion (collapsed by default under `✓`) so users can re-open solver phase logs; do not clear on `tool_result` (optional: cap stored lines per id at 500).
- [ ] **Step 3:** On session reset / `clearChat`, clear the whole `toolProgress` map (already likely); do not wipe mid-turn on success.
- [ ] **Step 4:** `cd pypsa-gui/frontend && npm run build` — expect success.
- [ ] **Step 5:** Commit `fix(chat): render tool_progress during long-running tools`

---

### Task A2: Lock-to-bottom autoscroll

**Files:**
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx`

- [ ] **Step 1:** Track `stickToBottom` state; on scroll, set true only when `scrollHeight - scrollTop - clientHeight < 80`.
- [ ] **Step 2:** Replace unconditional `scrollIntoView` effect: only scroll when `stickToBottom` is true.
- [ ] **Step 3:** When `!stickToBottom` and new tokens/tools arrive, show a small “↓ Latest” control that scrolls and re-sticks.
- [ ] **Step 4:** `npm run build` — expect success.
- [ ] **Step 5:** Commit `fix(chat): lock-to-bottom autoscroll with jump-to-latest`

---

### Task A3: Copy button on fenced code blocks

**Files:**
- Modify: `pypsa-gui/frontend/src/components/ChatMarkdown.tsx`

- [ ] **Step 1:** Wrap `pre`/`code` renderer in a relative container with a top-right button using existing lucide `Copy` / `Check` icons if already in the app; otherwise use a text button “Copy”.
- [ ] **Step 2:** On click, `navigator.clipboard.writeText(codeText)`; show “Copied” for 1.5s.
- [ ] **Step 3:** `npm run build`.
- [ ] **Step 4:** Commit `feat(chat): copy button on markdown code blocks`

---

### Task A4: Inject live network context into system prompt

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (`_build_system_prompt`, `run_turn`)
- Test: `pypsa-gui/backend/tests/test_chat_live_meta_prompt.py` (new)

**Interfaces:**
- Consumes: `PyPSAService.get_active_context()` / existing `get_meta()` body
- Produces: system prompt suffix like `Working with <project>: <buses> buses, <lines> lines, <snapshots> snapshots, solved=<bool>.`

- [ ] **Step 1:** Write failing test that mocks a bound context with known meta and asserts `_build_system_prompt` / turn system text contains project name + counts.
- [ ] **Step 2:** Run test — expect FAIL.
- [ ] **Step 3:** At turn start (after `turn_ctx` snapshot), fetch compact meta; pass into `_build_system_prompt(session, live_meta=...)`. On unbound/error, omit line (do not fail the turn).
- [ ] **Step 4:** Run test — expect PASS; run `pixi run gui-tests` filtered to chat prompt tests.
- [ ] **Step 5:** Commit `feat(chat): inject live network meta into system prompt`

---

### Task A5: Lock `read_all_turns` under `chat_state.lock`

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (`read_all_turns`)
- Test: extend `pypsa-gui/backend/tests/test_chat_rotation_race.py` or add sibling

- [ ] **Step 1:** Write/extend test that documents concurrent rotate+read must not raise / return empty spuriously (existing rotation race tests are the model).
- [ ] **Step 2:** Inside `read_all_turns`, hold `ctx.chat_state.lock` for the **entire** critical section: resolve persist path, decide rotated vs current sources, and read/parse both files. Unbound/`path is None` may return `[]` without taking the lock only if that decision does not touch files.
- [ ] **Step 3:** Run rotation-race + history tests — PASS.
- [ ] **Step 4:** Commit `fix(chat): read chat.jsonl under chat_state.lock`

---

### Task A6: Pairing-aware session message trim

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (`ChatSession.messages`)
- Test: `pypsa-gui/backend/tests/test_chat_message_trim.py` (new)

- [ ] **Step 1:** Failing test: fill deque past capacity with `user` / `assistant[+tool_use]` / `user(tool_result)` groups; assert no orphan `tool_use` without matching `tool_result`.
- [ ] **Step 2:** Replace silent `deque(maxlen=400)` eviction with explicit trim that drops complete turn groups from the front (prefer summarizing oldest N user turns into one `user` summary message when crossing 360 messages).
- [ ] **Step 3:** Tests PASS.
- [ ] **Step 4:** Commit `fix(chat): pairing-aware history trim`

---

### Task A7: Per-turn aggregate tool-result budget

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (`run_turn` tool-result path)
- Test: `pypsa-gui/backend/tests/test_chat_tool_result_budget.py` (new)

**Constant:** `MAX_TOOL_RESULT_CHARS_PER_TURN = 40_000`

- [ ] **Step 1:** Failing test: stub many large tool results; after budget, further results become `{_omitted: true, length: N, reason: "per_turn_tool_result_budget"}` and content tells the model to narrow queries.
- [ ] **Step 2:** Implement running counter in `run_turn`; apply after per-result truncation.
- [ ] **Step 3:** Tests PASS.
- [ ] **Step 4:** Commit `fix(chat): cap aggregate tool-result size per turn`

---

### Task A8: Opus → Sonnet fallback on persistent rate limit

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py`
- Test: `pypsa-gui/backend/tests/test_chat_model_fallback.py` (new)

- [ ] **Step 1:** Failing test: mock stream raising `rate_limited` until retries exhausted on Opus; assert one more attempt on `DEFAULT_MODEL` and SSE annotates fallback.
- [ ] **Step 2:** After retry loop exhausts on `rate_limited`, if `session.model == OPUS_MODEL`, set model to `DEFAULT_MODEL`, emit informational frame/event, retry stream once.
- [ ] **Step 3:** Tests PASS.
- [ ] **Step 4:** Commit `fix(chat): fall back Opus to Sonnet after rate-limit exhaustion`

---

### Task A9a–A9c: Host composite tools (first three)

**Files:**
- Modify: `pypsa-gui/backend/services/chat_tools.py`
- Modify: `pypsa-gui/backend/services/chat_tools_schema.py`
- Test: `pypsa-gui/backend/tests/test_chat_tools_dispatch.py` (+ new focused tests)
- Invariant: `test_chat_tools_schema_match.py` / `test_tool_schema_signature_consistency.py` must stay green

**A9a `diagnose_results` (read tier):** compose `get_results` kinds (objective, capacity_factor, curtailment, load_shedding/stats as available) + `validate_network` into one structured report dict with `issues: list[{severity,code,message}]`.

**A9b `solve_overview` (read tier):** post-solve triage — status, objective, top carriers by capacity/energy, emissions vs cap if present, pointer to run `diagnose_results`.

**A9c `undo_my_last_chat_action` (destructive tier — D17):** schema must include `Safety: destructive` so the host confirmation card gates it. Implementation: find latest audit entry whose action prefix matches this session’s `agent:…:session6()`; verify undo stack top is that agent action; else return `{ok:false, error_kind:"undo_target_mismatch"}` with no mutation. Tests: match / mismatch / no-entry.

- [ ] Implement A9a with tests + schema + dispatcher; commit `feat(chat): diagnose_results composite tool`
- [ ] Implement A9b similarly; commit `feat(chat): solve_overview composite tool`
- [ ] Implement A9c per D17; commit `feat(chat): undo_my_last_chat_action destructive tool`
- [ ] Update `CHATBOT.md` tool/capability notes

**Deferred composites (not in Track A):** `sanity_check_results`, `compare_scenarios`, `generate_run_report`, `submit_plan`, `plan_what_if` — either Track B wrappers or a later Track A slice after orchestrator exists.

---

### Task A10: Inline tool_result preview (optional polish after A1)

**Files:** `ChatPanel.tsx`, possibly `chat.ts` frame typing

- [ ] If `tool_result` SSE payload includes a small summary/preview field, render a compact table/pre under `✓`; if payload is name-only today, extend backend frame to include truncated JSON (≤500 chars) first.
- [ ] Commit `feat(chat): inline tool_result preview`

---

### Task A11: Price cache tokens in the cost meter

**Files:** `frontend/src/store/chatStore.ts`, `ChatPanel.tsx`

**Fact check:** `deriveCostEur` already prices **input + output**. Gap is cache only.

- [ ] Extend `PRICING_USD_PER_MTOK` entries with `cache_read` and `cache_create` (Anthropic published rates for the pinned models; bump `PRICING_VERSION`).
- [ ] Update `deriveCostEur` to include both cache dimensions; show `cache: Nk read / Mk create` when nonzero.
- [ ] Add a small unit test or pure-function test for `deriveCostEur` if Vitest is available; otherwise a backend-independent TS assert in a tiny `chatStore.pricing.test.ts` once A12 lands — until then, manual numeric check in PR description.
- [ ] Commit `feat(chat): price cache_read and cache_create in cost meter`

---

### Task A12: Frontend chat Vitest harness (larger; can follow Track B Phase 1)

**Files:** add Vitest config if missing; `ChatPanel.test.tsx`, `chat.test.ts`

- [ ] Stand up Vitest; fake SSE emitter covering subscribe, confirmation token, tool_progress render, autoscroll stickiness.
- [ ] Commit `test(chat): vitest harness for ChatPanel SSE UX`

---

## 5. Track B — Orchestrator (PLAN v5 phases, host-adapted)

Follow `docs/superpowers/specs/2026-07-25-adaptive-orchestrated-agent-layer-v5.md` §§4–21 with these **host adaptations**:

1. Package lives under `pypsa-gui/backend/agent_orchestrator/`.
2. `tools/host_bridge.py` imports whitelist from §2 D3, applies `ToolPolicy(read_only=True)`, and wraps results per D15.
3. Host entry points are **only** `POST /api/orchestrate/stream` and `/abort` (D7/D12). Chat Analyze mode (B-I1) uses that SSE client and **never** `run_turn` for the same submission (D2/D18).
4. Do **not** replace `run_turn` tool loop.
5. Model ids and dependency pins per D5/D16 (verified in B1).
6. Cost/rate/project binding per D11–D13.
7. Resume/`thread_id` per D14.

### Phase gate rule

Each PLAN phase (1–10) = one task group below. Stop for review after Phases 3, 5, 7, and 9. Commit per phase.

### Task B1 — Skeleton + model + resolve (PLAN Phase 1)

**Files:** create package modules `config.py`, `models.py`, `resolve.py`, `prompts.py`; `litellm.config.yaml` (full `model_list` + `router_settings`); document env keys in `CHATBOT.md` / `.env.example` if present; pin deps in `requirements.txt`; `tests/agent_orchestrator/test_resolve.py`, `test_smoke_live.py` (marked live)

- [ ] Verify LangGraph + LiteLLM + sqlite checkpointer APIs and Anthropic model id strings against current docs; write pinned `langgraph` / `litellm` / `langgraph-checkpoint-sqlite` constraints into `requirements.txt` (D16).
- [ ] Ship complete `litellm.config.yaml` using LiteLLM `anthropic/...` model strings (D5); **no** cross-tier Router `fallbacks`. Note intentional light-tier divergence from PLAN’s `claude-sonnet-5` → host `claude-sonnet-4-6`.
- [ ] `config.py` price table includes input/output/cache_read/cache_create.
- [ ] Router built from YAML (not bare `completion()` for routed calls).
- [ ] `resolve_role(tier, sensitivity)` fail-closed for private; escalation logic in `resolve.py` only.
- [ ] Live smoke optional: `@pytest.mark.live`
- [ ] Commit `feat(orchestrator): phase1 skeleton models resolve prompts`

### Task B2 — Stores + state (PLAN Phase 2)

- [ ] `stores.py`, `state.py` with `Annotated[..., operator.add]` reducers
- [ ] Tests: reducer merge, store round-trip, resolve
- [ ] Commit `feat(orchestrator): phase2 stores and graph state`

### Task B3 — Unified executor (PLAN Phase 3)

- [ ] `nodes/executor.py`: context budget, ToolPolicy, retries, `NEEDS_DECOMPOSITION`
- [ ] Host bridge registers D3 whitelist only; wraps every tool result in `<untrusted_data>` (D15); executor system prompt includes untrusted-data clause
- [ ] Test: hostile fixture in tool output attempting to order a write/delete — assert ToolPolicy rejects any non-whitelist name and bridge never registers write tools
- [ ] Tests: success / retry→failed / decomposition signal
- [ ] Commit `feat(orchestrator): phase3 unified executor`

### Task B4 — Triage (PLAN Phase 4)

- [ ] Local sensitivity + shape/tier; override; heuristic fast-path; `emit_triage`
- [ ] Tests: override skips LLM; private local; low-confidence safe default
- [ ] Commit `feat(orchestrator): phase4 triage`

### Task B5 — Atomic path (PLAN Phase 5)

- [ ] Graph: triage atomic → wrap_goal_as_task → one executor call
- [ ] Tests: atomic-light + atomic-heavy; `test_executor_unified`
- [ ] Commit `feat(orchestrator): phase5 atomic path`

### Task B6 — Coordinator waves (PLAN Phase 6)

- [ ] `emit_plan` → waves; 1×1 short-circuit; caps `MAX_WAVES` / `MAX_SUBTASKS_PER_WAVE`
- [ ] Commit `feat(orchestrator): phase6 coordinator waves`

### Task B7 — Wave execution + replan (PLAN Phase 7)

- [ ] Semaphore, evaluator after final wave, tier-raise re-issue (public only)
- [ ] Wire durable Sqlite checkpointer at `projects/<project>/orchestrate.checkpoints.sqlite` (D14)
- [ ] Tests: `test_sequential_waves`, `test_decompose_loop`, **plus** FakeModel resume across a **new** graph/Router instance with the same `thread_id` (simulates kill/restart)
- [ ] Commit `feat(orchestrator): phase7 wave loop and replan`

### Task B8 — Synthesizer (PLAN Phase 8)

- [ ] Merge summaries; tolerate gaps
- [ ] Commit `feat(orchestrator): phase8 synthesizer`

### Task B9 — Interface + HTTP SSE + DRY_RUN (PLAN Phase 9)

**Files:** `interface.py`, `routers/orchestrate.py`, mount in `main.py`, persistence for orch usage (D13), `CHATBOT.md`

**`run_task` return shape (required keys):**
`{ final_result, artifacts, trace_id, thread_id, usage, triage_meta?, dry_run_plan? }`

- [ ] Implement `run_task(...)` with private refusal; DRY_RUN skips executors but may run triage+plan
- [ ] Implement `POST /api/orchestrate/stream` + `/abort` per D11–D12; pin `ProjectContext`; enforce rate + daily + per-run budgets (D13); enforce single-active-run per session (D19) via run registry
- [ ] Persist orch usage to `orchestrate.jsonl`; hook Save-As / rename / snapshot-copy lineage like `chat.jsonl` (D13); add lineage test
- [ ] Document trust model (no app auth; local GUI) + resume-by-`thread_id` + checkpointer path in `CHATBOT.md`
- [ ] Tests: project_mismatch; private refusal; dry_run has no executor calls; abort between waves; orch_run_in_flight; Analyze path does not call `run_turn` (D18 — may live with B-I1)
- [ ] Commit `feat(orchestrator): phase9 interface SSE and budgets`

### Task B-I1 — Chat Analyze mode UX (merge with or immediately after B9)

**Files:** `ChatPanel.tsx`, `api/orchestrate.ts` (new), `chatStore.ts`

- [ ] Header control “Analyze” + Dry-run checkbox; `/analyze ` prefix in the composer also routes here
- [ ] Client **only** opens orchestrate SSE — never `createChatStream` / `run_turn` for that submit (D2/D18)
- [ ] Render triage/plan/estimate frames; on `run_done`, append assistant artifact markdown; show orch usage line
- [ ] Abort: `AbortController` cancels the SSE fetch **and** POSTs `/api/orchestrate/abort` `{run_id}` (D20); support resume by sending last `thread_id`
- [ ] Disabled when chat health reports missing API key
- [ ] Commit `feat(chat): Analyze mode via orchestrate SSE`

### Task B10 — Guardrails + telemetry + seam test (PLAN Phase 10)

- [ ] Budgets/timeouts/concurrency; telemetry incl. triage decisions + wave index; `test_seam_remap`
- [ ] Acceptance checklist from PLAN §21 exercised with FakeModel (+ optional one live smoke), including resume and private refusal
- [ ] Commit `feat(orchestrator): phase10 telemetry and seam remap guarantee`

---

## 6. Explicitly out of scope (this plan)

- Local Ollama/Qwen hardware bring-up (Mode 2) — only YAML comments + remap test
- Side-effecting orchestrator workers / sandbox
- Replacing Anthropic chat with LangGraph for ordinary chat turns
- Cursor Cloud Agent / Cursor SDK integration
- Re-implementing already-done backlog items in §1.2
- Full guided what-if macro and click-to-navigate canvas (follow-up plan)
- Frontend design-system overhaul

---

## 7. Testing strategy

| Layer | Command / practice |
|---|---|
| Host backend | `cd pypsa-gui/backend && pixi run gui-tests` (or pytest path for chat tests) |
| Orchestrator units | `pytest pypsa-gui/backend/tests/agent_orchestrator -q` (no network) |
| Live smoke | `pytest -m live` only with key + explicit intent |
| Frontend | `npm run build`; Vitest after A12 |
| Invariant | schema↔dispatcher count/signature tests must remain green after every tool change |

---

## 8. Acceptance

### Track A done when

- A1–A8 merged and tested
- A9a–A9c registered, schema-matched, documented; A9c is destructive + confirmation-gated
- A11 prices cache_read and cache_create (input/output already priced)
- No regression in existing `test_chat_*` suite
- ChatPanel shows solver progress; scrolling does not yank mid-read

### Track B done when

- PLAN §21 acceptance holds under FakeModel
- `test_executor_unified`, `test_sequential_waves`, `test_seam_remap`, resume/`thread_id`, and injection/ToolPolicy tests pass
- Private runs refused; DRY_RUN returns plan/estimate without executors
- Analyze mode uses orchestrate SSE only (no `run_turn` for that submit); returns artifact without mutating the network
- Project mismatch / rate limit / single-active-run / orch daily+per-run budgets enforced
- `orchestrate.jsonl` (+ checkpoint sqlite) follow project lineage on Save-As/rename/copy
- Light/heavy roles remappable via YAML only
- `run_done` always includes `thread_id`; resume works across fresh graph instance (durable checkpointer)

---

## 9. Execution order (checklist)

1. [ ] Review loop clears §0 → `CLEARED`
2. [ ] A1 → A2 → A3 (frontend quick wins)
3. [ ] A4 → A5 → A6 → A7 → A8 (backend reliability)
4. [ ] A9a → A9b → A9c
5. [ ] A10 → A11 (polish; A12 may slip after B1)
6. [ ] B1 → B2 → B3 *(review)* → B4 → B5 *(review)* → B6 → B7 *(review)* → B8 → B9/B-I1 *(review)* → B10
7. [ ] Final acceptance pass + update `CHATBOT.md`

---

## 10. Self-review (author)

| Spec / concern | Covered by |
|---|---|
| PLAN triage/shape/tier/waves | Track B + spec copy |
| PLAN private fail-closed | B1 `resolve.py`, B9 refusal |
| PLAN host integration | D1–D4, D11–D14, B9, B-I1; delta D9 |
| PLAN §18 resumability | D14 durable Sqlite path + B7 cross-instance resume test |
| PLAN §20 prompt injection | Global Constraints, D15, B3 |
| Existing chat not rewritten | D9, §6 |
| Stale backlog false starts | §1.2 / §1.3 |
| Composite vs orchestrator duplication | D10 |
| Model id / dep pin drift | D5, D16, B1 |
| Tool side effects in orchestrator | D3 + ToolPolicy |
| Orchestrate auth/binding/spend | D7, D11–D13, D19 |
| Double-spend Analyze→chat | D2, D18, B-I1 |
| SSE vs sync timeouts | D12 |
| Frontend abort teardown | D20 |
| Orch usage lineage | D13 + B9 lineage test |
| Undo confirmation gap | D17, A9c |
| A11 factual accuracy | §1.3 A11 + Task A11 |
| History rotation race | A5 (full critical section) |
| Progress UX already half-built | A1 (retain logs under ✓) |
| Spec light model vs host | D5 / B1 note (`sonnet-4-6` intentional) |

**Placeholder scan:** none intentional; deferred composites named with IDs.

**Review amendment log:** Pass 1 found G1–G8 (closed). Pass 2 found durable checkpointer + orch.jsonl lineage (amended via D13/D14/D16/D19/D20). Pass 3 pending.

---

## 11. Handoff

After §0 is `CLEARED`:

1. **Subagent-driven** (recommended) — one task per subagent with review between tasks  
2. **Inline** — execute with executing-plans checkpoints  

Do not start coding while Status is `PENDING_REVIEW` or `GAPS_FOUND`.
