# 13 — The session event log: "model-visible means logged", replay and fork

Status: done (e1adb0db, no own PR)
Type: task
Blocked by: 08 (done), PR MLHaoChang/pypsa-eur#86 merged (done)

Source: `docs/superpowers/assessments/2026-10-06-deepseek-harness-adoption-assessment.md`
(design 1). The borrowed idea is DeepSeek Harness's append-only `SessionEvent`
log as the source of truth (their `docs/architecture.md`, "model-visible means
logged"; `docs/subsystems/persistence.md`, crash recovery and fork lineage).
Nothing of theirs is imported; this is a Python design inside `harness/`.

## What we have

`harness/history.py`: `chat.jsonl` holds one record per COMPLETED turn
(`append_turn`), a pending-turn WAL (`begin_pending_turn` / `read_pending_turn`
/ `clear_pending_turn`) covers a crash mid-turn, rotation at `ROTATE_BYTES`,
lineage on save / rename / snapshot, and the in-memory trim with the
`[Earlier conversation summary]` message. The per-turn addenda (ui-context,
Guided rules, workflow step; `harness/compose.py`) ride the user content and
are persisted with the turn, so they are already "logged", but as part of a
flattened message, not as their own events. There is no replay and no fork.

## What changes

1. `harness/eventlog.py`: an append-only `events.jsonl` per session with a
   header record (`format`, `session_id`, `project`, `parent_session`,
   `inherited_event_count`) and typed events, each with a monotonic `seq`:
   `turn/start`, `user/message`, `context/injected {kind: ui_context |
   guided | workflow_step, text}`, `assistant/attempt {blocks, usage,
   stop_reason}`, `tool/call {call_id, name, args}`, `tool/result {call_id,
   content, error_kind?, spill?}`, `approval/asked`, `approval/decided`
   (issue 18), `workflow/state`, `compaction/summary {shadowed: [seq..seq]}`,
   `turn/end {reason: completed | aborted | interrupted | error}`.
   Writes are batched per turn with one `fsync` at `turn/end` (the WAL's
   job today); redaction (`_redact_for_persist`) applies before the write.
2. `derive_messages(events) -> list[message]` is the ONE projection the
   loop sends to a provider. Red-first test: for every recorded session in
   `tests/fixtures`, `derive_messages` equals today's `session.messages`.
3. Crash recovery on open: a torn tail gets synthetic closers (`tool/result`
   with `error_kind: interrupted`, `turn/end {reason: interrupted}`) appended
   as an ordinary batch. This replaces the pending-turn WAL.
4. Fork: `fork(session, at_seq) -> new session` whose header names the parent
   and the inherited prefix; events before `at_seq` are shared, not copied.
5. Replay tool: `smoke/replay_session.py --events <file> --at <seq> --profile
   <id>` forks at `seq`, runs the next turn on the named profile, and prints
   the trajectory (tool names, frames, usage) beside the original. This is
   the provider-parity instrument the probe in issue 09 approximates with a
   scripted battery, and the evidence ADR-0002 asks for.
6. `chat.jsonl` stays as a PROJECTION for `GET /chat/history` and the
   lineage rules for one release (recommended answer to Q15), written from
   the same events at `turn/end`, so the panel and every existing test see
   no change. Removing it is a later decision.

## Done when

- The recorded frame sequence (`tests/test_chat_turn_frame_contract.py`) is unchanged.
- `/chat/history` is byte-identical for a recorded session; lineage tests unchanged.
- Red-first tests: torn-tail recovery; fork shares the prefix; `derive_messages`
  identity; a `context/injected` event exists for every addendum the loop added.
- The replay tool reproduces W2 of the parity battery from a recorded log on
  the stub wire and on the live Anthropic wire (runbook updated).
- The README's module table and "Splitting the loop" name `eventlog.py`;
  `harness/README.md` rule 8 gains "model-visible means logged".

## Pick-up notes

Start from `harness/history.py` and the loop's `_run_turn_body` (where the
turn record is assembled). Work red-first; keep `append_turn` callers until
the projection is proven. Gate: the full chat regression as in issue 08
(`tests/test_chat*.py test_llm*.py test_tool_*.py test_guided*.py
test_harness*.py test_report*.py` plus the files named in the issue 08
comments), then both parity runs. Model tiering per plan §4 rule 7.

## Comments

2026-10-12 (re-scoped against master after 143 commits; nothing of this issue exists there yet):
master has no event log, `derive_messages`, fork or replay tool; `history.py`
still writes `chat.jsonl` with the pending-turn WAL, and the turn record now
also carries `answer_id`, `receipt` and `owner_user_id`. Build on, do not
break:
- `rewind_session` cuts `session.messages` but leaves `chat.jsonl` alone, so
  the log needs a `rewind` (shadow) event, and `derive_messages` must equal
  the history the provider is actually sent, not raw `session.messages`.
- Checked answers: `append_history_message` replaces tool-result content in
  checked turns and messages carry a private `_approved_answer_id`
  (`harness/assurance.py` history seed). Add `checked_answer {answer_id,
  receipt}` and `checkpoint_revoked` events.
- `chat.jsonl` stays a projection with byte-identical records, `answer_id`
  and `receipt` included: `services/assurance_records.history_projection` and
  `harness/assurance_recovery.candidate` read its latest native record.
- A strict turn revokes its checkpoint before the WAL is written; keep that
  order (`.scratch/assistant-assurance/harness-contracts.md`).
- Persist with an existing pattern: the owner store
  (`assurance_records.save`: app-data, sha256 envelope, directory fsync) or
  the project-dir tmp + fsync + replace used by tasks and the durable thread.
- `tests/fixtures` holds no recorded sessions: record a corpus first, as the
  identity test's input.
- A fork must not inherit assurance checkpoints, approvals or task tickets.

2026-10-14 (part a, shadow mode): `harness/eventlog.py` writes
`<app data>/chat-events/<sha(owner)>/<sha(session)>.jsonl` beside today's path
(0700/0600, fsynced under `durable.transaction`, swept after 30 days): a
header (`format`, `session_id`, `owner_user_id`, `parent`,
`inherited_event_count`), then `turn/start {user, model}` (written at once,
the WAL's job), `user/message | assistant/attempt | tool/result {message}` from
`append_history_message` (after sanitising and a checked turn's
replacement), `context/injected {kind: ui_context | workflow_step |
checked_request, text}`, `history/trim {limit}`, `history/rewind {turns}`
(`history.cut_turns`, shared with `rewind_session`), `history/reset` (the
`/history` rehydration now calls `ChatSession.reset_history()`), and
`turn/end {reason, usage}` with one fsync for the turn's batch. Redacted
before the write; a failure is logged and never costs the turn.
`derive_messages(events)` equals `session.messages` (the seed of the next
provider request) for plain, tool, trimmed, rewound and rehydrated sessions.
`read_events(path, repair=True)` closes a torn tail (a `tool/result`
interrupted per unanswered call, `turn/end {interrupted}`) as an appended
batch. `fork(path, at_seq)` names the parent and the inherited prefix in its
header (shared, not copied) and carries only the owner and the history.
`harness/replay.py` + `smoke/replay_session.py --events --at --profile`
fork before a turn and run it again on a profile (its model, bound to it),
printing both trajectories. Not changed (part b): chat.jsonl, the pending-turn WAL and the
loop's provider input; `tool/call` is not a separate event (the tool_use
blocks are in `assistant/attempt`); `approval/*` stay in issue 18's log.
Tests: tests/test_harness_eventlog.py (16), tests/test_harness_replay.py (7),
red first. `interrupted` classified `inline` in tool-error-kinds.json (the
closer's literal; never on a live frame). W2 recorded on the stub (battery
5/5) and replayed from its log on the stub (identical) and on live
`anthropic-sonnet` (completed, the orient step taken on its own): see
`docs/superpowers/runbooks/harness-parity-probe.md`. The first replay found
the fork asking for a placeholder model with no profile; fixed and pinned.
Gate: the chat regression in three shards, 3670 passed, 5 skipped, 1 failed
(the manifest, now classified and passing). Part b (the switch-over) is a separate PR.

2026-10-14 (part b, the switch-over; owner: "the loop builds from
derive_messages(events), the WAL is retired, and chat.jsonl becomes a
projection of events"):
- The fold: `eventlog._apply(messages, event)` is the one step of
  `derive_messages`; `eventlog.apply(session, type, **fields)` records the
  event and folds the logged (redacted) event into `session.messages`.
  `append_history_message`, its trim, `rewind_session`, `reset_history` and
  `fork` go through it, so the deque IS `derive_messages(events)` by
  construction, and the loop's seed (`list(session.messages)`) is the log's
  projection. Consequence: earlier turns reach the model redacted, as
  chat.jsonl already stores them. A tripwire
  (`test_nothing_but_the_log_changes_a_sessions_history`) fails on any
  production `.messages` mutation outside `eventlog.py`. An event that cannot
  be redacted is not written, but still folded.
- Resume: `/history` rebuilds a session it mints (a restart, an eviction)
  from its log (`eventlog.resume`, torn tail closed first), tool calls and
  results included, and the session goes on in the same file; a live session
  is not rebuilt at all (it already is its projection). chat.jsonl rebuild
  stays only for a session recorded before the log existed. A rebuilt history
  holds only the session's own turns (the chat.jsonl rebuild replayed every
  turn in the window, co-members' included); the checked-answer scope is
  still recovered only when the newest answered turn in the shared transcript
  is the anchored answer (`test_newer_peer_message_cannot_become_the_owned_scope`).
- WAL retired: `begin/read/clear_pending_turn` and `chat.jsonl.pending` are
  gone. `turn/start` gains `project` and `ts`; `/history`'s `pending_turn`
  is `eventlog.interrupted_turn(caller, ctx)`: the caller's latest
  `turn/start` on this project with no `turn/end`, in a session not in this
  process, reported once (its log closed with `turn/end {interrupted}`).
  Unlike the WAL it is per session, not one slot per project, and only the
  caller's own turn is reported (CH-6).
- Projection: each chat.jsonl record (completed and interrupted) is a
  `turn/record {record}` event flushed before the loop appends the line
  (`eventlog.project_chat(events)` reads them back); `append_turn` stays at
  the loop's call site. The chat import route still writes chat.jsonl
  directly (no session, no log).
Gate: the chat regression in three shards, 4079 passed, 10 skipped, 5 failed:
two were that recovery rule (fixed as above), three were source-reading lock
tests that ran while history.py was being edited; the affected set rerun on
the final code: 293 passed, 7 skipped, 0 failed.
Tests: tests/test_harness_eventlog_truth.py (9, red first); the four WAL
tests in test_chat_pending_turn_wal.py moved there; the secret-scrubbing
precondition test's third sink is now the event log; the 13a sequence pin
gains `turn/record`. Parity: stub 5/5; live anthropic-sonnet 5/5 twice, and
once 5 prompts passing with one cross-prompt check failing (its message not
captured; W2 made three tool calls that run), no backend errors.
