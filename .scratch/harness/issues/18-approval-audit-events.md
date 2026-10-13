# 18 — Confirmation decisions as durable, fail-closed events

Status: ready-for-agent (re-scoped 2026-10-12: no longer blocked by 13, see Comments)
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
