# 13 — The session event log: "model-visible means logged", replay and fork

Status: needs-info (owner question Q15 in the spec: it changes the history format that decision Q7 kept)
Type: task
Blocked by: 08 (done), PR MLHaoChang/pypsa-eur#86 merged, Q15 answered

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
