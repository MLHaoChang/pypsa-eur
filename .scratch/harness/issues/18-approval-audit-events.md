# 18 — Confirmation decisions as durable, fail-closed events

Status: ready-for-agent (done 2026-10-13: harness/approvals.py, fail-closed gate, revoked cards released at once)
Type: task
Blocked by: none

Source: assessment design 2; DeepSeek Harness `docs/subsystems/approval.md`
(`approval/asked` and `approval/decided` audit events, closed outcomes,
"callers fail closed on `unavailable`", the request carries the call id,
never the arguments).

## What we have

`harness/confirm.py` and `harness/session.py`: a `PendingConfirmation`
(token, tool, args, tier, TTL) in memory, consumed exactly once by
`/confirm`; the `confirmation_request` frame; the audit-log action prefix
(`session6`) in the system prompt. A decision survives only as the turn's
tool result; an expired or never-answered card leaves no record.

## What changes

1. `approval/asked {call_id, tool, tier, token6}` is appended when the card
   is issued; `approval/decided {call_id, outcome, by}` when `/confirm`
   answers, the TTL expires, or the session is dropped with a card open.
   Outcomes are the closed set `approved | rejected | expired | unavailable`;
   anything but `approved` denies. Arguments are not repeated: `tool/call`
   already holds them.
2. Auto-approved tiers (`AUTO_APPROVE_TIERS`) record `decided {by: policy}`
   with no `asked`, so the log says why no card appeared.
3. The replay tool (issue 13) shows decisions beside tool calls.

## Done when

The three outcomes and the policy path each have a red-first test; the
frame recording is unchanged; `/history` is unchanged.

## Comments

2026-10-12 (re-scoped against master after 143 commits; nothing of this issue exists there yet):
Confirmation cards are still in memory only (`harness/confirm.py`,
`PendingConfirmation` / `record_decision` / `wait_for_decision` in
`harness/session.py`). The card frame is `tool_pending_confirmation` (this
issue said `confirmation_request`). Changes:
- Write the records now to an owner store in the `assurance_records` style
  or a small append-only JSONL under `harness/durable.transaction`; fold them
  into issue 13's log when it lands.
- The closed outcomes gain `aborted` (`wait_for_decision` returns it) and
  `revoked` (`durable_conversation.rebind_scope` clears open cards and
  decisions without a record today).
- Link `approval/decided` to the effect `operation_id` (`harness/effects.py`
  reserves after confirmation) and the task `attempt_id` when present.
- For preview confirmations (`assistant_tools.preview_confirmation`) record
  the `preview_id`, not the diff.

2026-10-13 (done): `harness/approvals.py` appends `approval/asked
{call_id, tool, tier, token6}` and `approval/decided {call_id, tool, tier,
outcome, by}` to `<app data>/chat-approvals/<sha(owner)>/<sha(session)>.jsonl`
(0700/0600, fsynced under `durable.transaction`, files swept after 90 days).
Outcomes: `approved | rejected | expired | aborted | revoked | unavailable`;
`by`: `user | ttl | session | system | policy`. The gate
(`confirm._confirm_destructive_tool`) writes both; `AUTO_APPROVE_TIERS`
records `decided {by: policy}` with no `asked`. Fail closed (owner question
sent 2026-10-13, built with the recommended answer): an approval that cannot
be recorded does not run (`approval_unavailable`), and a card whose `asked`
cannot be written is never shown; a denial that cannot be recorded still
denies. `ChatSession.revoke_confirmations()` releases open cards as
`revoked` at once, and `durable_conversation.rebind_scope` now calls it
(before, a cleared card left its turn to time out as `expired`). Links:
`preview_id` for preview confirmations and the checked turn's
`operation_id`; never the arguments. Not linked: the task `attempt_id`,
which `assistant_tasks.begin_step` creates after the gate. Not changed: the
stub loop's own card (`harness/stub.py`), and the frames on the ordinary
paths. Tests: `tests/test_harness_approvals.py` (16 tests, one parametrized over approve and deny; red first). Replay of
decisions beside tool calls waits for issue 13.
