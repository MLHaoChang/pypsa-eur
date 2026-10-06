# 17 — A goal with a round budget: bounded autonomous continuation

Status: needs-info (owner question Q17 in the spec: may the assistant start turns on its own?)
Type: task
Blocked by: 06 (done), 13 (the goal is log state), Q17 answered

Source: assessment design 4; DeepSeek Harness `docs/subsystems/goal.md`
(durable goal with phases active / paused / blocked / complete, a round cap,
each continuation round attributed; only a human resumes a paused or blocked
goal).

## What we have

Workflows (`harness/workflows/*.md`) carry a `done_when` per step and the
model advances with `advance_workflow`, but every turn starts from a user
message. Budgets (`harness/budget.py`: `MAX_TURNS_PER_SESSION`,
`MAX_OUTPUT_TOKENS_PER_SESSION`, the daily cap) bound a session, not a goal.

## What changes (if Q17 allows it)

1. Tools `set_goal(objective, max_rounds)`, `pause_goal`, `complete_goal`;
   `goal_state` frame and `/chat/history.goal`; a strip beside the workflow strip
   with Pause and Stop.
2. Continuation: after `turn_done`, if a goal is active, rounds remain, and
   the turn ended without a choice card, a confirmation card or an error,
   the backend enqueues a synthetic user message
   `Continue toward the goal (round n of N).` attributed to the goal. A
   confirmation card still blocks; a Guided write still needs its card; the
   session and daily budgets still apply and `blocked` is the goal's phase
   when they bite.
3. Every round is a `goal/round` event (issue 13) so the trajectory shows
   what the assistant did unattended.

## Open points for Q17

Whether unattended rounds are acceptable at all; the default `max_rounds`
(recommend 5) and the hard ceiling (recommend 20); whether a round may call
write-tier tools in Expert without a card (recommend no: a goal does not
change tiers).
