<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-23 — study copilot: goal → study brief → approved steps → Hand-off

Status: folded into the study harness (owner decision SH-FOLD-1, 2026-10-28): the core continues as `.scratch/study-harness/issues/01-run-skeleton-eh-site.md`; slice 3 (plan card UI) → `.scratch/study-harness/issues/08-run-monitor-and-envelope-card.md`; slice 4 (hand-off) → `.scratch/study-harness/issues/07-report-handoff-and-pilot.md`. Its cases SC-01–SC-16 stay binding there.
Previous status: design (phase 1), awaiting gatekeeper plan OK and UX placement OK
Progress: Slices 1 (#573) and 2 (#582) merged; slice 3 plan card UI and slice 4 hand-off not on master.
Type: task
Baseline: master `49791da` plus #542 (LC-05 slice 3, chat-panel continuation), which merges first
Owner decision: AI-567b (amends AI-567), `docs/agents/owner-decisions.md` (#559)
Research source: #567 `.scratch/high-value-ai-study/02-STUDY-COPILOT-SPEC.md`, `06-CONTRACTS-AND-JOURNEYS.md` (research only; its S01–S16 are adopted below as SC-01–SC-16)

## What the user sees

The user states a goal in the companion ("compare a battery and a bigger
connection for this campus through 2035, then hand me the report"). The
assistant drafts a **study brief** and shows it as a **plan review** Choice
card (`ask_user`, detail = the brief, one verdict). On "Approve plan" it calls
`start_task` with the brief; from then on the brief is the **plan card** in the
companion (the existing task card, `AssistantResultCard`), and it is the only
progress view. Each step runs as an ordinary offered tool call: a write or a
run still needs its own edit card or Confirmation card click, and the plan
stops at each card boundary for the user's "Continue" (owner decision COP-1). Each result is
read back natively before the next step. A failed step offers a correction
draft. The brief ends with its Hand-off step(s).

The card workspace **Next** row is unchanged: it shows the one next step per
card from the flow (card-workspaces §2.1). It never shows the brief's progress.
There is no second progress line, no new panel, no second Start button.

**Owner decision COP-1 (2026-10-21, via the coordinator).** Every write or
run step keeps its own card click; the plan also pauses at each card
boundary with "Continue to <card>" on the plan card; read steps run without
stopping; approving the brief grants nothing. Mechanism (slice 1): when the
last step of a phase completes and the next phase belongs to another card,
the task status becomes `checkpoint` and `admit` refuses the next phase's
first step (`task_checkpoint`) until `resume_task` clears it. The plan card's
"Continue to <card>" sends that ordinary resume request as a user message;
it is not an approval and grants no card.

## The brief is a `start_task` plan (no second system)

`start_task(title, steps, request_id, brief=None)` gains one optional argument.
The task file (version 2) stores it beside the steps; version-1 files stay
readable. Nothing else gets a store.

```
brief = {
  "objective": str ≤ 240 chars,             # the user's goal in their words
  "cards":     [card, ...] ≤ 6,             # capacity|grid|control|reliability|commercial|hand-off
  "cases":     [{"name": str ≤ 60, "experiment": "$step.experiment_id" | null}] ≤ 8,
  "phases":    [{"card": card, "steps": [step_id, ...]}]   # partitions the steps, in order
}
```

- **Steps stay `{id, tool, args}`** with the same validation (catalogue tool,
  bound signature, earlier-step references, 1800-char args). The brief only
  labels them: `phases` must partition the step ids in step order, each phase
  one declared card. A step outside every phase, or a phase out of order, is
  `invalid_brief`.
- **Step limit.** 1–12 stays for a plain plan. A plan with a brief admits
  1–24 steps and 32 000 chars of steps (args stay ≤ 1800 each). A longer goal
  is split into a second brief the user reviews again (SC-05); there is no
  hidden 25th step and no nesting.
- **Excluded tools stay excluded**: task controls (`_CONTROLS`), `ask_user`
  (Choice answers are user messages, they cannot be a durable step) and
  project-rebinding tools. "Allow run steps" means the run families below
  become *waiting* steps instead of start receipts that end `uncertain`:
  - `run_simulation`, `run_ac_pf_stage`: already wait on their `fg:` job (LC-12).
  - `run_sensitivity_sweep` and `solve_queue_enqueue` (queued jobs): new, the
    same `await_job` → `settle_job` path, followed by
    `continuation.follow_job` on the queue job's own record.
  - `wait_for_job` is not a plan step under a brief (its 25 s limit makes it a
    false failure); the waiting step replaces it.
- **Packet limit per step**, derived server-side, never model-supplied: the
  view's `steps[i].limit` is `{"args_chars": 1800, "result_chars": <catalogue
  result budget for the tool>, "wait": "job"|null}`. The plan card shows it on
  hover; the model sees it in `get_task`.
- **View budget.** `_view` stays ≤ 3800 chars: the brief is condensed to
  objective + cards + case names + the current phase's steps; other phases
  show `n of m done`. `step_details_truncated` as today.

## Cases → experiment tools

A brief's `cases` name what will be compared. They map onto the existing
experiment path, never a new one:

1. `start_experiment` (≤ 8 planned cases, `experiment_contract.MAX_CASES`),
2. `prepare_experiment` (preflight, zero writes),
3. `advance_experiment` → its `next_call` (`run_sensitivity_sweep`, a
   waiting step) → `refresh_experiment` (the native read-back; the step
   completes only when the case is `completed` with intact run evidence).

Execution admits **1 or 2 network cases per experiment**
(`experiments.py:286`, `experiment_execution.advance_experiment`). A brief
with more runnable cases, or a non-network domain, is refused at
`start_task` (`brief_cases_not_runnable`) with the native reason; the
assistant proposes the first two as one brief and the rest as a follow-up
brief. The LC-17 four-suggestion cap is a proposal cap, never execution
authority. Case steps' args reference `$exp.experiment_id` and
`$adv.next_call.arguments.<key>` (whole-value references, unchanged).

## Gates (the #567 HV-S / AI-558 / AI-567 rules)

| Rule | Where it is enforced |
|---|---|
| Never mark a step done from a caller-supplied result | `resolve_task_step(outcome="completed")` no longer stores the caller's `result`. It re-reads native evidence for the step's family: an edit step re-reads its receipt targets (`edit_cards` read-back), a job step its job record via `continuation`'s settle check, a read step re-runs nothing and refuses. No verifier → `task_resolution_unverifiable`, the step stays **Needs review**. `retry` stays explicit and confirmed. Applies to plain plans too. |
| Read each result back before the next step | Edits: `_edit_receipt.outcome == "verified"` (exists). Runs: job record + manifest + fingerprint (exists, LC-12). Cases: `refresh_experiment` step `completed` (new check in `finish_step`: `status != "completed"` is not completion). |
| No replay on resume | `resume_task` unchanged: running → `failed` (reads) / `uncertain` (effects); waiting → its job; nothing re-executes. Continuation from #542. |
| Failed step → correction draft, never auto-retry | A `failed`/`uncertain` step under a brief returns `next_step.correction`: the native error and the LC-19 triage tool calls to draft a fix (log, `diagnose_network`, `validate_network`). The fix is an ordinary edit card; the plan continues only after `resolve_task_step(retry)` with its confirmation. |
| Approvals only through edit cards / plan cards | The brief approval is the plan-review Choice card; each effect is its own card. Approving the brief grants nothing (SC-06/07). Card-boundary "Continue" is a user message, not an approval. |
| Server verdicts, no model maths | Step status, freshness and case verdicts come from native records only. The brief carries no numbers the model computed. |
| Freshness | Each completed step stores the project's input fingerprint after it (`workflow_tools._network_input_fingerprint`). On `get_task`/`resume_task`, a different current fingerprint that no plan step produced sets `brief_stale: "inputs_changed_outside_plan"` and the next effectful step needs a new brief review (SC-11). |
| Cancel | `cancel_task` unchanged: future steps stop, running jobs keep running and are reported as running (SC-13). |

## Resume

The brief lives in the task file, so `list_tasks`/`get_task`/`resume_task`
find it across sessions and providers, and the #542 continuation brings a
closed chat back at the next step. Old approvals are never restored: a step
that was pending re-offers its card.

## UI (one entry point)

- The plan card is the existing task card in the companion. It gains: the
  objective line, the card chips in scope, the case names, and the steps
  grouped by phase (card) with status (`done`, `next`, `waiting on job`,
  `needs review`, `stale`). Buttons stay Resume / Cancel / Review (the existing
  requests).
- A card chip in the plan card opens that card's workspace ("Open here"); it
  does not move the brief.
- The card workspace Next row and "Walk me through this card" (H1, W10
  session) are untouched. H1 may *mention* that a plan is running on the
  card, through the same plan card, never a second line (coordinate with
  session_01Fujxj8VNHA8JxTdXWmuPTX).

UX gate conditions for slice 3 (UX reviewer, 2026-10-21, on `3889eafe`):

1. Step status copy uses existing words: done → "Complete", next → "Next",
   waiting on job → "Waiting for the run", needs review → "To review",
   stale → the existing "Out of date" pill. Raw ids stay internal; no "job"
   or "stale" in copy.
2. Card chips are the shared card-chip component (W3a / W10), same label and
   behaviour (filter the rail/sidebar to the card, "Open here"); no new chip
   style.
3. The plan card's Next step carries its owning target ("Open here" to the
   field or card). A blocked or uncertain step (SC-09) says why and what the
   user can do, never a state word alone.

## Slices

1. **Harness** (gatekeeper): `brief` argument + validation + version-2 file,
   24-step limit under a brief, COP-1 card-boundary checkpoint, per-step limits, `resolve_task_step` native
   read-back, freshness. Tests SC-01, SC-05, SC-06, SC-07, SC-11, SC-12.
2. **Cases and waiting runs** (gatekeeper): queued-job waiting steps,
   `refresh_experiment` completion rule, correction draft, two-case brief.
   Tests SC-08, SC-09, SC-10, SC-13, SC-14.
3. **Plan card UI** (UX reviewer): brief rendering in `AssistantResultCard`,
   card chips → workspace, stale/needs-review states. Tests SC-03, SC-16,
   browser evidence.
4. **Hand-off close** (after LC-21): the closing phase's report/delivery
   steps, gaps listed as not established. Tests SC-15, SC-04.

## Cases (adopted from #567 S01–S16)

| Id | From | Case | Expected | Limit |
|---|---|---|---|---|
| SC-01 | S01 | Goal lacks units/horizon | Plan-review card asks; no `start_task` | brief ≤ 240-char objective |
| SC-03 | S03 | Plan card → card chip → Expert edit → back | One companion; Next row unchanged | — |
| SC-04 | S04 | Card outside scope but its input consumed | Shown as consumed prerequisite, not activated | ≤ 6 cards |
| SC-05 | S05 | 25 steps / nested `start_task` / `activate_project` / forward ref | `invalid_task*`/`invalid_brief`, nothing saved | 24 steps, 32 000 chars |
| SC-06 | S06 | Deny an edit step's card | Step stays pending, zero writes | 1800-char args |
| SC-07 | S07 | Lock/ACL changes after brief approval | Fresh refusal, zero effect | — |
| SC-08 | S08 | Edit then run | Run waits for `verified` receipt | result budget per tool |
| SC-09 | S09 | Sweep job lost (restart) | Step `uncertain`, dependents blocked, no requeue | — |
| SC-10 | S10 | Third runnable case / non-network domain | `brief_cases_not_runnable` | 2 runnable cases |
| SC-11 | S11 | Inputs edited outside the plan | `brief_stale`, next effect needs re-review | — |
| SC-12 | S12 | `resolve_task_step(completed)` with a made-up result | Native read-back decides; caller result ignored | 4000-char resolution |
| SC-13 | S13 | Cancel during a job | Future steps stop; job shown running | — |
| SC-14 | S14 | Infeasible solve | Correction draft, no auto-retry | LC-19 triage |
| SC-15 | S15 | Hand-off with an unsupported chapter | Gap listed, no universal report | LC-21 |
| SC-16 | S16 | Resume after permission revoked | `task_not_found`, no effects | — |

S02 (unsupported dynamics goal) is the native refusal already in
`find_capabilities`/catalogue support and stays a test of slice 1.
