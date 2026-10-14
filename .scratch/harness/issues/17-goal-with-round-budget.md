# 17 — A goal with a round budget: bounded autonomous continuation

Status: ready-for-agent (done 2026-10-14: goal on the session, rounds through admission, goal strip; Q17a/Q17b asked, built on the recommendations)
Type: task
Blocked by: 06 (done)

Source: assessment design 4; DeepSeek Harness `docs/subsystems/goal.md`
(durable goal with phases active / paused / blocked / complete, a round cap,
each continuation round attributed; only a human resumes a paused or blocked
goal).

## What we have

Workflows (`harness/workflows/*.md`) carry a `done_when` per step and the
model advances with `advance_workflow`, but every turn starts from a user
message. Budgets (`harness/budget.py`: `MAX_TURNS_PER_SESSION`,
`MAX_OUTPUT_TOKENS_PER_SESSION`, the daily cap) bound a session, not a goal.

## What changes

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

## Decided (Q17)

Unattended rounds are allowed; `max_rounds` defaults to 5 with a hard
ceiling of 20; a round never changes tiers (a write in Expert cards as it
does today, a write in Guided cards as Guided requires).

## Comments

2026-10-12 (re-scoped against master after 143 commits; nothing of this issue exists there yet):
master's durable parent thread (`services/durable_conversation.py`) already
has an unused `goals` field and `reserve_budget` / `settle_budget`, and
`run_turn` already drains a queued utterance into a follow-up turn through
`services/utterance_admission.TurnCoordinator`. So:
- Store the goal on the durable thread, not as event-log state; this drops
  the dependency on issue 13 (a `goal/round` event is added when 13 lands).
- Drive each round through utterance admission (the turn lease and
  `generation`), never a separate enqueue, or rounds race queued
  corrections. A thread rebind bumps `generation` and must stop the goal.
- `blocked` means: a budget hit, a task step in `needs_review`, an
  `uncertain` effect (`harness/effects.py`), or a generation change.
- Refuse goal rounds in strict or pilot-checked turns (prose is withheld).
- Frames are closed and recent work avoided new ones: carry goal state in the
  tool result, or add `goal_state` to `harness/events.py` deliberately.

2026-10-14 (done, one PR): `harness/goals.py`; tools `set_goal(objective,
max_rounds)` (default 5, 1–20, `invalid_goal`; a second goal is
`goal_already_set` until the first is complete), `pause_goal(reason)`,
`complete_goal(summary)`, all read tier, control tools. The goal is on the
ChatSession beside the workflow, not the durable thread: a thread is bound
only for Live voice, so a typed chat would have had none (asked as Q17a,
recommendation taken meanwhile). After a turn, `run_turn` asks
`goals.after_turn` and runs the next round on the same stream as
`Continue toward the goal (round n of N).` with a fresh `utterance_id` at
the goal's admission generation; a queued correction is drained first.
Stops: Choice card (waits, the answer's turn resumes rounds); declined,
lapsed, revoked or unrecordable card → paused `card_declined`; abort →
paused `aborted`; disconnect → paused `disconnected`; budget refusal →
blocked `budget`; error / not completed → blocked `error` (set before the
`error` frame, which closes the panel's stream); `task_step_uncertain` or the
session's task in `needs_review` → blocked `needs_review`; admission
generation moved (a rebind) → blocked `generation_changed`; checked turn →
blocked `checked_turn`; rounds used up → paused `rounds_used_up`. A
`session_done` is held while the goal is active so `goal_state` reaches the
panel first. Only a person resumes (`POST /api/chat/{id}/goal`
pause/resume/stop; resume re-arms at the current generation with fresh
rounds and the panel sends the turn; stop clears it and aborts a running
round). Per-turn goal block after the workflow step (`context/injected
kind=goal`); event log `goal/set`, `goal/round`, `goal/phase`; `/history.goal`;
`goal_state` frame added to `harness/events.py`. Panel: goal strip (Pause /
Resume / Stop, why it stopped), a "Goal: round n of N" marker per round.
set_goal asks no card (read tier like start_workflow; Q17b, recommendation
taken meanwhile). Tests: tests/test_harness_goals.py (34, red first),
ChatPanel.goalStrip.test.tsx (4), ChatPanel.goalFrame.test.tsx (2); the tool
inventory regenerated (+3 tools). Live: anthropic-sonnet over HTTP set a
3-round goal, ran rounds 1 and 2 on its own and completed it in round 2
(27.7 s, no backend errors; event log goal/set, goal/round ×2, goal/phase
complete). Gate: the chat regression in three shards, 3918 passed, 12
skipped, 2 failed (the inventory, regenerated and passing); frontend 4985 of
4987 with BottomPanel's paste timing failing only under the concurrent
backend load (passes alone and on a rerun).
