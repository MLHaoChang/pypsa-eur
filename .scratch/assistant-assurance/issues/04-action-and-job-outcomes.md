<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P3: verified action outcomes and real progress

Status: ready-for-agent
Progress: S1 (blocking foreground-job outcomes, harness/outcomes.py) merged ee141c66; S1b (chat tool-line marks) in review; S2–S5 planned; update_component static inputs done (effects.py)
Type: task
Blocked by: 01, 02; live-voice issue 11 for nonblocking jobs

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Map handler results to submitted/running/completed/partial/denied/cancel_requested/cancelled/failed/uncertain. Capture requested versus actual postconditions; unknown effects require reconciliation. Remove generic completion checkmarks for queued/failed payloads. Publish only actual task/job/check transitions and retain ordinary confirmations; no model polls.

## Test-first acceptance

Required case groups: **A3.1–A3.4** in the shared test matrix. Start with a failing
public-seam tracer, implement minimally, run green and relevant regressions.
Use independent numerical fixtures and isolated authorized projects; preserve
provider-neutral handlers, ordinary safety gates and cumulative budgets.
Record every required case as executable/passed/red/pending with a reason.
Opt-in skip or schema reachability is not behavior coverage. No paid APIs are
needed to start these deterministic slices.

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### Proposed reliability revision

P3 also requires A3.5–A3.6: required guards separate from courtesy checks, exact confirmed scope, real effect read-back and ambiguous-operation reconciliation. Coordinate ticket 11; no completion from model-supplied result JSON alone.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

The shared effect adapter now reserves exact confirmed static-input update contracts before execution, compares actual complete input read-back against a private candidate, checkpoints completion and refuses live/uncertain replay. Read-only reconciliation can settle verified after/before states without executing again. This is only `checked-input-update/1`; queue jobs, other mutations, partial-output maps and distributed project runtime remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.

### 2026-11-05 — audit against master ad12e8c79 and the slice plan (GK B (24))

Audit: 35 existing P3/effect tests pass. Tool adapters: write_unvalidated 100,
destructive_unvalidated 50, job_unvalidated 26, checked_input_effect 1
(update_component), navigation_ack 1. In a checked turn every other tool reads
"uncertain"; the backend emitted no running/partial/failed/denied/
cancel_requested/cancelled outcome before S1.

| Case | State at audit | Owner slice |
|------|----------------|-------------|
| A3.1 submitted/running not completed | navigation passed; UI passed for 6 statuses; backend job mapping red | S1 |
| A3.1 successful return over failed/partial not success | durable-task layer passed; tool outcome red | S1 (jobs), S2 (writes) |
| A3.1 unavailable effects uncertain | passed for update_component; red elsewhere | S1, S2 |
| A3.2 denied/expired, lock refusal, checkpoint failure | red (ordinary refusals only) | S4 |
| A3.2 timeout after possible effect, no replay | passed for update_component | S5 (jobs) |
| A3.3 cancel_requested not cancelled; own project/run; unrelated/newer job | red on the backend (UI display only) | S1 |
| A3.4 requested vs actual | passed for update_component; bulk/create/delete/undo/import/export red | S2, S3 |
| A3.5 guards, schema, paths, altered args, stale revision, courtesy, no-progress | guard_revision/args_changed passed for update_component; rest red | S4 (needs owner freeze) |
| A3.6 wrong read-back, extra edits, late worker, model completion | passed (update_component; p3 forged action) | — |
| A3.6 partial writes; duplicate/conflicting job ops | red | S2, S5 (needs owner freeze) |
| A3.6 mismatched artifacts | pending: issue 05 (P4 manifests) | 05 |
| Nonblocking job cases | pending: live-voice issue 11 | live-voice 11 |

GK B (24) verdicts:
- Q1 (a): use representative real handlers per effect class. Every tool left
  write/destructive/job_unvalidated gets a named `_pending` row with its ticket;
  a class reads "covered" only for what its tests check (#918/#920).
- Q2 (a): classify the outcome of every tool_result in ordinary and checked
  turns. The visible change (checkmarks gone in ordinary turns) is UI and needs
  the UX reviewer's OK on the PR that changes the display; tell the UX
  reviewer where it sits before building it.
- Q3 (a): build the A3.5 guards in S4 and record progress in issue 11.
- Q4: the A3.5–A3.6 freeze is the owner's (the original approval was the
  owner's); GK B finds the wording sound and testable, plus reliability-contract
  §4. Owner answer AA04-FREEZE-1 (2026-11-05, ~14:4x CET): "Freeze as worded
  (Recommended)"; A3.5–A3.6 are frozen as worded in test-plan.md rows A3.5/A3.6
  and reliability-contract.md §4. S4/S5 build after S1–S3, each with a plan first.
- S1 GO WITH CHANGES, conditions J1–J8: inputs are the independent read only;
  fail closed per input; identity (id, project, run id, started_at) per
  mismatch; abort cancel_requested→cancelled; failed/partial for run_simulation
  and wait_for_job; update_component outcomes unchanged; pure classifier with a
  table test and a killed mutant per branch; shared files untouched.
- S2–S5: send GK B a short plan before each.

### 2026-11-05 — S1 build (branch `claude/aa04-s1-job-outcomes`)

`harness/outcomes.py`: `note_submission` records the call's own identity when
the loop mints an `fg:` handle (job id, project, context, worker thread, run id
and the run marker's started_at). `read_live` reads the live solver record and
the run's evidence marker; any exception is a read error. `classify` is pure.
The loop adds `_outcome` to run_simulation, run_ac_pf_stage, wait_for_job and
abort_simulation results in ordinary turns. Checked turns cannot run these
tools (CHECKED_TOOLS), so their projection is unchanged. `_outcome`, not
`_assurance`, because `_assurance` on an ordinary result hides task cards and
download links in the frontend. The display stays as on master: the checkmark
change is a separate UI slice (S1b) with the UX reviewer.

AC PF "completed" with fewer converged snapshots than total reads partial;
missing or invalid counts read uncertain. An unsaved scratch network has no run
marker, so its solve never reads completed (no_run_record). wait_for_job on
`ad:`, `rpt:` and queue jobs reads uncertain, reason adapter_pending.

GK B (24) notes for later slice plans (GK B (25) now holds this review):
- S2: a revision that moved during read-back gives uncertain; a requested
  value equal to the old value counts as "changed as requested", not a short
  write (test it).
- S4: the required-guard deny happens before any reservation or checkpoint
  write (test nothing is written); the bounded stop counts identical failing
  calls per turn and per run and refuses with its own kind from a
  tool-error-kinds.d fragment; PRE_DISPATCH_VALIDATORS untouched.
- S5: the conflict check compares normalized args (S4's confirmation
  fingerprint), not raw JSON.

UX (9) GO for S1b (chat tool-line marks), rules:
1. Words from `components/activityWords.ts` ACTIVITY_STATUS_WORDS, lowercased:
   add submitted → "queued", cancel_requested → "stopping", uncertain →
   "outcome unknown" to that map; a test fails when an outcome has no word.
2. "…" only for in-flight (submitted, running, cancel_requested); ✓ only for
   completed; failed "✕" in text-warn; cancelled/partial/uncertain "–" muted;
   glyphs aria-hidden.
3. A stale "(running)" must not stay: update the line from the job's live
   state (preferred) or word it as of that moment ("was running"); say which.
4. Shots of every mark state in Guided and Expert (component-level harness OK, #1053).

### 2026-11-05 — S1b build (branch `claude/aa04-s1b-tool-line-marks`)

`components/toolOutcome.ts` (pure): `_outcome.outcome` → glyph, word, tone
and in-flight flag; an unknown string reads as uncertain. Words come from
ACTIVITY_STATUS_WORDS (submitted → Queued, cancel_requested → Stopping,
uncertain → Outcome unknown, denied → Denied added). ChatPanel writes the
transcript line `<glyph> <tool> (<word>)` for a result with `_outcome` and
renders the glyph aria-hidden in Guided and Expert; the checked-turn
`_assurance` line and every tool without `_outcome` are unchanged.

UX rule 3, both parts: an in-flight line takes the newest later `_outcome`
of the same job id in the transcript (wait_for_job, abort_simulation); with
no later read it reads "running as of 14:02" (Guided: "Running: start a solve,
as of 14:02"). The global /simulation/status is not used: it carries no job
identity. A reloaded transcript has no tool rows (history replays tool uses
as `[tool: name]`), so no stale line comes back. Shots:
`frontend/scripts/tool-outcome-shots.mjs`.

GK B (25) plan conditions U1–U5 (no GK gate on the frontend-only PR) and UX (9)
GO, applied: a job tool's returned result with a missing, malformed or
non-version-1 `_outcome` reads "outcome unknown: the result could not be
read", and the handler's `completed`/`status` are never read (U1); a cut
result's `_outcome` is read strictly from the start of its preview, else
unknown (U2); supersede only forward, same non-null job id, same project
(U3); `adapter_pending` and `no_submission` read unknown with their own words
(U4); the outcome table names all nine (U5). UX: the time gets a date
("4 Nov 14:02") when it is from an earlier day; Guided keeps the glyph.

UX (9) NOT OK @1d2a57e6 fixed: the reason follows a colon (Expert) or sits in
brackets (Guided), with no em dash; both modes say "as of 14:02"; shots in
`.scratch/assistant-assurance/shots/aa04-s1b-tool-outcome-{guided,expert}.png`.
