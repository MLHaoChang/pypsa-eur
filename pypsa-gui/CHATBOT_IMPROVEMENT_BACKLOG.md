# pypsa-gui v6 Chatbot — Improvement Backlog

Synthesized and de-duplicated from 6 independent examiner passes (session lifecycle, tool/safety model, Anthropic-SDK/cost, persistence/lineage, frontend UX, test coverage). Every item below was confirmed against source — the named gap is genuinely absent or only partially present. The chatbot is a solid, well-tested feature; these are the highest-leverage additions, not happy-path bugs.

**Overall: functional with notable gaps.** Strengths: 100 tools across 5 safety tiers with single-use confirmation tokens, prompt caching on system + tools, per-project chat.jsonl with locked rotation + lineage, abort-on-disconnect SSE, ~3500 LOC backend test suite. The risks concentrate in C2 multi-resident reliability, session lifecycle hygiene, Anthropic-path cost/correctness, UX visibility, and frontend/parallel-test coverage.

> Skeptic notes: items the examiners flagged that are ALREADY handled (and therefore dropped/down-ranked): prompt caching of system prompt + tool catalog is implemented (chat_service.py:1130-1142); abort_event is cleared at turn start so /abort is one-shot (1067); confirmation tokens are single-use with monotonic TTL under lock; chat.jsonl rotation is atomic under chat_state.lock; list-result truncation >200 DOES emit a structural `{_truncated,total,sample}` marker (so only the dict char-cut path is silent — see P1 item). Per-session output-token hard cap exists (200k). Solver bridge already snapshots ctx at entry (F10). These were merged or excluded accordingly.

---

## P0 — Safety / correctness / silent-corruption

### 1. Snapshot active project at turn start; validate before dispatch + persistence
- **Category:** reliability / correctness · **Value:** high · **Effort:** M
- **Why:** `run_turn` only calls `get_active_context()` at persistence time, after the entire streaming + tool loop. Under C2 multi-resident, a user switching projects mid-turn causes tools to execute against the NEW network and the turn record to append to the WRONG project's chat.jsonl — silent cross-project corruption. The solver bridge already snapshots ctx at entry (F10); the turn loop does not.
- **First step:** At `run_turn` entry capture `turn_project = PyPSAService.get_active_context().loaded_project`. Re-fetch and compare before each handler dispatch and before `append_turn`; on mismatch emit `error_kind='project_switched_mid_turn'` and abort (or pin all work to the original ctx).
- **Evidence:** `chat_service.py:1218-1228` (active context fetched AFTER loop); entry at `1062-1110` takes no snapshot; contrast F10 solver-bridge snapshot ~`624`.

---

## P1 — High-value reliability / cost / UX

### 2. Implement session eviction (idle TTL + LRU cap)
- **Category:** reliability · **Value:** high · **Effort:** M
- **Why:** `_SESSIONS` is process-lifetime with no eviction; `drop_session()` is defined but has zero callers (grep-confirmed). A long-running backend accumulates abandoned `ChatSession` objects (400-msg deque + usage + pending tokens) indefinitely — an unbounded memory leak with no operator visibility.
- **First step:** Add `last_activity` to `ChatSession`; in `get_or_create_session`, opportunistically scan `_SESSIONS` under `_SESSIONS_LOCK` and drop entries idle > TTL (default 24h, env-configurable) plus an LRU cap (~1000).
- **Evidence:** `chat_service.py:364-396`.

### 3. Inject live network context into the system prompt
- **Category:** prompt-engineering / cost · **Value:** high · **Effort:** S
- **Why:** `_build_system_prompt` is fully static — never names the active project, bus/line/snapshot counts, or solved state. The model must call `get_meta` or the user must re-state context every turn, wasting tokens/round-trips and reducing tool relevance.
- **First step:** In `run_turn`, fetch meta (reuse `get_meta` body) and interpolate one line: `Working with <name>: <buses> buses, <lines> lines, <snapshots> snapshots, solved=<bool>`.
- **Evidence:** `chat_service.py:989-1009`.

### 4. Surface truncation to the model — explicit marker in tool-result content
- **Category:** reliability / correctness · **Value:** high · **Effort:** S
- **Why:** `_result_to_anthropic_content` hard-cuts dict/list JSON at 4000 chars with NO marker, so the agent can believe it saw the full result and decide on partial data. (The list path >200 emits a structural marker; the char-cut path is silent.)
- **First step:** When the serialized string exceeds the cap, append ` …[RESULT TRUNCATED: showed N of M chars — request a narrower query]` before returning.
- **Evidence:** `chat_service.py:1490-1502` (silent cut); `1462-1487` (list-only signal).

### 5. Transient-error retry + rate-limit model fallback in run_turn
- **Category:** reliability · **Value:** high · **Effort:** M
- **Why:** Any SDK exception (incl. 429 / 503) maps to one error frame + immediate `session_done` — no retry, no backoff, no fallback to the cheaper model. A single transient blip aborts the turn. `ALLOWED_MODELS` is defined but no fallback is wired.
- **First step:** Wrap `client.messages.stream(...)` in a bounded retry (3 attempts, exp backoff) for transient kinds; on persistent `rate_limited`, emit a frame offering/auto-applying a Sonnet downgrade.
- **Evidence:** `chat_service.py:1174-1178`; `ALLOWED_MODELS` `91-93`.

### 6. Render tool_progress — expandable per-tool execution panel
- **Category:** ux · **Value:** high · **Effort:** M
- **Why:** `tool_progress` frames (solver `[PHASE]`/`[VALIDATION]`/`TRACEBACK`, import progress) are stored in `chatStore.toolProgress` and appended on receipt, but NO component renders them. Long solves are opaque — users see only `→tool` / `✓tool` tags.
- **First step:** Add a collapsible `<details>` panel under each tool message subscribing to `toolProgress[tool_use_id]`, rendering kind/line pairs in a scrollable monospace pane.
- **Evidence:** `chatStore.ts:85-86,169-177` (stored); `ChatPanel.tsx:460` (appended); no render site.

### 7. Lock-to-bottom autoscroll
- **Category:** ux · **Value:** medium · **Effort:** S
- **Why:** The scroll effect unconditionally `scrollIntoView` on `messages.length`, so scrolling up to re-read mid-stream yanks the view back down on every token batch.
- **First step:** Only auto-scroll when the container is already near the bottom (`scrollHeight - scrollTop ≈ clientHeight`); otherwise show a 'scroll to latest' badge.
- **Evidence:** `ChatPanel.tsx:417-420`.

### 8. Copy-to-clipboard on code blocks
- **Category:** ux · **Value:** high · **Effort:** S
- **Why:** `ChatMarkdown` renders fenced code (Python/YAML/CLI) with no copy affordance in a code-heavy domain.
- **First step:** Wrap the code block in a relative div + absolute top-right button (lucide `Copy`) calling `navigator.clipboard.writeText(children)` with a transient 'Copied' state.
- **Evidence:** `ChatMarkdown.tsx:32-38`.

### 9. Serialize history reads under chat_state.lock (rotation race)
- **Category:** reliability · **Value:** medium · **Effort:** S
- **Why:** `GET /history` reads chat.jsonl via `read_text()` WITHOUT the lock while `append_turn` rotates (`chat.jsonl`→`.1`) under it — a read racing rotation can return a partial/empty file. Non-hot path, cheap to fix.
- **First step:** Wrap the read in `chat_history` with `with ctx.chat_state.lock:`.
- **Evidence:** `chat.py:~99-121`; `chat_service.py:469-484`.

### 10. Per-worker PROJECTS_DIR fixture for parallel pytest
- **Category:** testing · **Value:** high · **Effort:** S
- **Why:** conftest documents that `tmp_projects_dir` is deliberately NOT autouse because concurrent collection cross-contaminates the shared `backend/projects` dir → flaky chat-tool failures. Blocks `pytest -n auto` / CI parallelism.
- **First step:** Add a session-scoped fixture keyed on xdist `worker_id` pointing `PROJECTS_DIR` at a per-worker tmp dir; make the redirect autouse when `worker_id` is set.
- **Evidence:** `conftest.py:87-104`.

### 11. Smart history trimming with turn summarization
- **Category:** reliability / cost · **Value:** high · **Effort:** M
- **Why:** `session.messages = deque(maxlen=400)` with no trim strategy silently drops the oldest messages when full — breaking context continuity and risking dropping a `tool_use` without its `tool_result` (which the SDK rejects).
- **First step:** Near capacity, summarize the oldest N turns into a single 'Summary of earlier conversation' message, preserving tool_use/tool_result pairing.
- **Evidence:** `chat_service.py:187-188`.

---

## P2 — Polish / nice-to-have

### 12. Surface cache + input-token savings in the cost meter
- **Category:** ux / cost-visibility · **Value:** medium · **Effort:** S
- **Why:** usage tracks `cache_read`/`cache_create` + input tokens, but the meter derives EUR from output only and never shows cache hits — users get no feedback that session-reuse caching works, so can't optimize behavior.
- **First step:** Include input + cache_read in the EUR formula and add a 'cache: Nk read' line.
- **Evidence:** `ChatPanel.tsx:~105-113`; `chatStore.ts:44-49`.

### 13. Frontend chat test harness (ChatPanel + chat API client)
- **Category:** testing · **Value:** high · **Effort:** L
- **Why:** ChatPanel (669 LOC), ChatMarkdown, chat.ts have zero tests — SSE consumer, confirmation state machine, abort UX, markdown rendering all uncovered, blocking safe UI refactors.
- **First step:** Stand up Vitest; add `ChatPanel.test.tsx` with a fake SSE emitter covering subscribe/unsubscribe, frame parsing, confirmation token extraction, code/table markdown.
- **Evidence:** no `.test`/`.spec` under `frontend/src/components` or `/api`.

### 14. Empty-state onboarding for the chat panel
- **Category:** ux · **Value:** medium · **Effort:** S
- **Why:** With no messages the panel is blank — no hint of capabilities in a complex PyPSA domain, raising the activation barrier.
- **First step:** Render an EmptyState when `messages.length===0` listing capabilities and the tool count from `session_init`.
- **Evidence:** `ChatPanel.tsx:~604-623`.

### 15. Network topology / connectivity diagnostic tool
- **Category:** feature · **Value:** high · **Effort:** M
- **Why:** `validate_network` runs preflight checks but there is no graph-level diagnostic (isolated buses, islands, orphan generators) — common modelling debug needs the agent must currently infer from raw `list_components`.
- **First step:** Add a `topology_analyzer` service (connected components, orphan assets, island count) and a read-tier `diagnose_network` tool.
- **Evidence:** `chat_tools.py:~717-719`.

### 16. Result pagination / cursor for large reads
- **Category:** ux · **Value:** high · **Effort:** M
- **Why:** `list_components` / `list_all_timeseries` truncate to a 200-sample with no way for the agent to fetch the next page or learn the true total beyond the marker. On big networks the agent is blind to the tail.
- **First step:** Add optional `offset`/`limit` params returning `{items, total_count, offset}`; update schema + dispatcher.
- **Evidence:** `chat_tools.py:119-128, 243-245`.

### 17. Batch create / delete component tools
- **Category:** feature · **Value:** high · **Effort:** M
- **Why:** `bulk_update_components` exists for attribute updates only; create/delete are singleton — deleting a substation (bus + N lines + transformers) is N separate locked, audited, undo'd calls.
- **First step:** Implement `batch_create_components(class, specs[])` and `batch_delete_components(class, names[])` under one lock + audit entry; register in DISPATCHERS as write/destructive tiers.
- **Evidence:** `chat_tools.py:369-399, 460-479, 491-495`.

### 18. Apply cache_control to message history on long sessions
- **Category:** performance-cost · **Value:** medium · **Effort:** M
- **Why:** Only system + tools carry `cache_control`; the full message history is retransmitted at raw input price every turn. On turn 10+ this is the dominant input cost.
- **First step:** Mark an older assistant block (e.g. every 5th, when >10 messages) with an ephemeral `cache_control` breakpoint before passing `messages` to the stream.
- **Evidence:** `chat_service.py:1143-1149`.

### 19. Pre-validate destructive tool args before issuing the confirmation card
- **Category:** ux · **Value:** medium · **Effort:** S
- **Why:** The confirmation token is issued before handler dispatch, so a destructive op with bad args (e.g. delete of a nonexistent bus) makes the user confirm an action that then 404s.
- **First step:** Add an optional `pre_dispatch_validate(args)` per DISPATCHER entry; run it before `issue_confirmation` for destructive tiers and fail fast without a card round-trip.
- **Evidence:** `chat_service.py:1325-1335` (confirmation before dispatch ~`1378`).

### 20. WAL-style pending-turn write + corruption signalling on reload
- **Category:** reliability · **Value:** high (combined) · **Effort:** M
- **Why:** Two coupled durability gaps: (a) `append_turn` runs only after the turn completes, so a crash mid-response loses the turn entirely; (b) `chat_history` silently skips unparseable JSON lines, so a truncated file replays as '9 of 10' turns with no indication of loss.
- **First step:** Write `chat.jsonl.pending` synchronously before streaming and merge it on reload with an 'incomplete' flag; in `chat_history`, count skipped lines and return `history_gap=true` + count for a frontend banner.
- **Evidence:** `chat_service.py:1217-1228`; `chat.py:114-117`.

### 21. ARIA roles + live region for accessibility
- **Category:** ux · **Value:** medium · **Effort:** M
- **Why:** Confirmation card lacks `role='alertdialog'`, error banner lacks `role='alert'`, messages are unlabeled divs, and there is no `aria-live` region to announce streamed messages — keyboard/screen-reader use is impractical.
- **First step:** Add the roles above, `aria-label`s on Approve/Deny/Stop buttons, and wrap the message list end in `aria-live='polite'`.
- **Evidence:** `ChatPanel.tsx` ConfirmationCard `196-244`, ErrorBanner `264-292`, messages `605`.

### 22. Concurrent-turn + project-switch test scenarios; expired-token auto-dismiss
- **Category:** testing / ux · **Value:** medium · **Effort:** M
- **Why:** No test covers two tabs driving the same `session_id` (concurrent `session.messages` appends), nor a project switch mid-turn (item 1's guard). Separately, when a confirmation token expires the frontend keeps showing the card; clicking Approve then 404s with no auto-dismiss.
- **First step:** Add `test_concurrent_turns_same_session` and `test_project_switch_mid_turn` in `test_chat_e2e.py`; on the frontend, when the countdown reaches zero auto-dismiss the card and show 'confirmation expired'.
- **Evidence:** no concurrent/project-switch tests (grep); `ChatPanel.tsx:129-154` (countdown, no auto-dismiss); `chat_service.py:334-344` (passive expiry).

---

### Minor observations (no item, worth a glance)
- ~~Model id in code is `claude-opus-4-7` and `claude-sonnet-4-6` — chat Opus may be one rev behind.~~ **DONE (2026-06-07):** Opus bumped to `claude-opus-4-8` (latest) across backend `OPUS_MODEL`, frontend `ChatModel`/pricing/picker; Sonnet 4.6 already latest. Also fixed: the model picker now takes effect mid-session (was ignored after turn 1).
- `MAX_OUTPUT_TOKENS_PER_SESSION` (200k) has a hard cap but no soft warning — consider an 80%-threshold warning frame (low value).
- Switching model mid-conversation (Sonnet↔Opus) applies silently to the next turn with no warning dialog (`ChatPanel.tsx:573-581`).
