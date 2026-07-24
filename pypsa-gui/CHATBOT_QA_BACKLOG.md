# AI Chat Service — Improvement Backlog (v6 QA follow-up)

**Gate:** Functional QA PASSED. All four verification dimensions are overall `pass` (Tool-use = pass, Session/SSE = pass, Persistence/lineage = pass, SDK-integration = pass-with-concerns). No dimension failed. The full chat pytest suite is green and the 23 prior tool defects + the P0 project-switch guard are confirmed done.

**Scope rule:** Everything below is VERIFIED-OPEN against the source (file:line cited). Items already implemented this session (P0 project-switch guard, the 23 tool fixes, model bump + mid-session switch, ChatMarkdown, the AST import regression test) are deliberately excluded.

---

## P1 — High-value reliability / cost / capability / UX

### 1. Retry-with-backoff on transient SDK errors (429 / 5xx)
- **Category:** reliability · **Value:** high · **Effort:** M
- **Why:** `run_turn`'s single try/except maps any SDK exception to an error frame + `session_done` and returns immediately. A transient 429 or 529/500 kills the whole turn with no retry; the user re-sends and loses streamed progress.
- **First step:** Wrap the `messages.stream` call in a bounded retry loop keyed on `_map_sdk_exception`'s `error_kind`; retry only `{rate_limited, upstream_error}` with capped exponential backoff (honor `Retry-After`); fall through to the existing error frame after N attempts.
- **Evidence:** `chat_service.py:1195-1199` — `except Exception` → map → yield error + `session_done` + `return`, no retry.

### 2. Evict idle in-memory sessions (wire `drop_session` + TTL/LRU sweep)
- **Category:** reliability · **Value:** high · **Effort:** M
- **Why:** `_SESSIONS` is process-lifetime with no eviction; `drop_session` is defined but has **zero callers**. Each `ChatSession` holds a 400-message deque + usage + pending confirmations. Long-running backends leak memory with every new session id.
- **First step:** Stamp `last_activity` on each session (updated in `run_turn`); sweep idle sessions past a TTL via `drop_session` under `_SESSIONS_LOCK`. `chat.jsonl` + GET `/history` already back replay, so eviction is safe.
- **Evidence:** `chat_service.py:397` `def drop_session` — only occurrence in backend is the definition; `_SESSIONS` registry has no eviction path.

### 3. Explicit dict/list result-truncation marker
- **Category:** reliability · **Value:** high · **Effort:** S
- **Why:** `_result_to_anthropic_content` does `json.dumps(result)[:4000]` — a silent hard cut, often producing invalid JSON. The model believes it saw the full result and draws wrong conclusions on large tables/stats. The list path already emits a `{_truncated,total,sample}` struct; the dict/string path does not.
- **First step:** When the serialized string exceeds the cap, append ` …[RESULT TRUNCATED: showed 4000 of N chars — call a narrower query for the rest]`.
- **Evidence:** `chat_service.py:1569` — `return json.dumps(result, default=str)[:4000]` (and `[:4000]` again at 1571), no marker.

### 4. GET `/history` must read `chat.jsonl` under `ctx.chat_state.lock`
- **Category:** reliability · **Value:** medium · **Effort:** S
- **Why:** `append_turn` rotates (rename) + writes under `ctx.chat_state.lock`, but `/history` reads via `read_text()` **without** the lock. A concurrent rotation can expose a transient missing/empty file; the `JSONDecodeError` skip handles partial lines but not a whole-file miss mid-rename.
- **First step:** Wrap the `read_text()` loop in the history handler in `with ctx.chat_state.lock:`. Reads are short → negligible contention.
- **Evidence:** `chat.py:109` — `src.read_text(...)` with no lock; rotation+write hold the lock at `chat_service.py:473-487`.

### 5. Inject live network context into the system prompt
- **Category:** capability · **Value:** high · **Effort:** M
- **Why:** `_build_system_prompt` is fully static — never names the project, component counts, or solved state. The model must spend a `get_meta` tool call to orient itself every session, wasting tokens/latency and hurting first-response relevance.
- **First step:** At `run_turn` start (after capturing `turn_ctx`), build a compact context paragraph (project name, snapshot count, component counts, solved/unsolved) and append it to the per-turn system prompt; re-derive per turn so it stays fresh.
- **Evidence:** `chat_service.py:1000-1013` — returns a constant policy string; no project/network fields interpolated.

### 6. Render `tool_progress` frames during long solves
- **Category:** ux · **Value:** high · **Effort:** M
- **Why:** The backend streams `[PHASE]/[VALIDATION]/TRACEBACK` via the solver bridge and `chatStore` stores them in `toolProgress` keyed by `tool_use_id`, but no component renders them. During a multi-minute solve the user sees a static `→tool` tag and the assistant feels hung.
- **First step:** In `ChatPanel`, read `toolProgress[toolUseId]` for the active solver tool and render the streamed phase lines (collapsible) under the tool tag; clear on completion.
- **Evidence:** `chatStore.ts:85-86,192-195` store/append `toolProgress`; `ChatPanel.tsx:566` references it only in the reset path — no JSX render site.

### 7. Per-turn cumulative tool-result size budget
- **Category:** cost · **Value:** medium · **Effort:** M
- **Why:** Each result is capped at 4000 chars, but with `MAX_TOOL_CALLS_PER_TURN=25` a turn can push ~100KB of results with no aggregate cap — crowding out the system prompt / tool catalog / reasoning budget and inflating cost.
- **First step:** Track a running sum of serialized result lengths in `run_turn`; past a threshold (~40KB) replace further full results with a `{_omitted, length}` stub and tell the model the budget is exhausted.
- **Evidence:** `chat_service.py:105` `MAX_TOOL_CALLS_PER_TURN=25`; `chat_service.py:1569` per-result cap — no aggregate per-turn cap in `run_turn`.

---

## P2 — Polish / hardening

### 8. Model fallback (Opus → Sonnet) on persistent rate-limit
- **Category:** reliability · **Value:** medium · **Effort:** M
- **Why:** `ALLOWED_MODELS` has both tiers but no downgrade logic when Opus is persistently rate-limited. Paired with #1, gives graceful degradation instead of a failed turn.
- **First step:** After #1's retry loop exhausts on `rate_limited`, if `session.model` is the Opus tier, retry the turn once on Sonnet and annotate the fallback.
- **Evidence:** `chat_service.py:91-97` defines both tiers; `chat_service.py:1195-1199` has no fallback branch.

### 9. fsync `chat.jsonl` after append
- **Category:** reliability · **Value:** low · **Effort:** S
- **Why:** `append_turn` relies on context-manager close to flush; no fsync. A crash before OS writeback loses the just-completed turn — visible on screen, gone from `/history` after restart.
- **First step:** After `f.write`, call `f.flush()` then `os.fsync(f.fileno())` (still under the lock), guarded in try/except for platforms that reject it.
- **Evidence:** `chat_service.py:485-487` — `f.write(...)` with no flush/fsync.

### 10. Pending-turn WAL for mid-response crash recovery
- **Category:** reliability · **Value:** low · **Effort:** M
- **Why:** `append_turn` runs only after a turn completes; a crash mid-stream loses the entire turn including the user's message.
- **First step:** Write a minimal `{user, session_id, model, started_at}` to `chat.jsonl.pending` at `run_turn` start; clear on success; surface orphaned pending records as interrupted turns in `/history`.
- **Evidence:** `chat_service.py:1238-1255` — `append_turn` only on the completed-turn path; no pending/WAL record.

### 11. Pairing-aware trim when the message deque overflows
- **Category:** reliability · **Value:** medium · **Effort:** M
- **Why:** `session.messages = deque(maxlen=400)` drops oldest entries positionally. A drop between a `tool_use` block and its matching `tool_result` orphans the pair and Anthropic rejects the next request.
- **First step:** Replace positional eviction with a trim that drops complete user+assistant(+tool_result) turn groups (or summarizes) from the front.
- **Evidence:** `chat_service.py:191-192` — `deque(maxlen=400)`; eviction is positional, not pairing-aware.

### 12. Reconcile `run_simulation` safety-tier doc drift
- **Category:** observability · **Value:** low · **Effort:** S
- **Why:** Schema says `Safety: execution`; the `run_turn` comment says `execution_long_running`. Both are in `DESTRUCTIVE_TIERS` so confirmation still fires — no functional bug, but the inconsistency will mislead a future maintainer.
- **First step:** Pick one canonical tier label and make schema description + `run_turn` comment agree.
- **Evidence:** `chat_tools_schema.py:551` (`Safety: execution`) vs `chat_service.py:~1272` comment (`execution_long_running`); both in `DESTRUCTIVE_TIERS` at `chat_service.py:88`.

### 13. Frontend tests for ChatPanel SSE consumer, confirmation state machine, markdown
- **Category:** testing · **Value:** medium · **Effort:** L
- **Why:** `ChatPanel.tsx` (~669 LOC), `ChatMarkdown`, and `api/chat.ts` have zero coverage. The SSE parser, confirmation UX, abort flow, and markdown rendering are unguarded — any streaming refactor can silently break chat with no signal.
- **First step:** Stand up vitest + Testing Library; first test the `api/chat.ts` SSE frame parser against canned `event:`/`data:` chunks (including a frame split across two reads), then the confirmation card transitions.
- **Evidence:** No test files for ChatPanel/ChatMarkdown/api/chat.ts; chat tests are backend pytest only.

### 14. Poll `request.is_disconnected()` to abort on client disconnect
- **Category:** cost · **Value:** low · **Effort:** M
- **Why:** Turn abort relies on the explicit `/abort` + frontend `controller.abort()`. A closed tab or dropped network without `/abort` leaves the server streaming from Anthropic and burning output tokens until the turn ends naturally.
- **First step:** In the SSE generator, periodically `await request.is_disconnected()` between frames and set `session.abort_event` when true.
- **Evidence:** `chat.py:167` — comment defers `is_disconnected()` polling; no actual poll exists.

---

**Top two to do first:** #1 (retry/backoff — turns every transient 429 into a hard turn failure) and #2 (session eviction — a real process-lifetime memory leak). #3 (truncation marker) is the cheapest high-value win.