<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-12 (VS-4) — finish "edit, relaunch, walk me through" across long jobs

Status: ready-for-agent inside LC-05 scope (owner decision Q1); closes LC-05 with OJ-07/08/10
Type: task
Baseline: master `d006f4beaa302b97432c65757762ae180222731a`
Dependencies: LC-03 and LC-04 closed; existing `start_task`, `wait_for_job`,
completion observer; refines [LC-05](05-ordered-workflows.md)
Contract: LC-C12 in the [specification](../spec.md)

## Problem

`wait_for_job` waits at most 25 s (`harness/catalogue.py:1844-1856`). The
completion observer records terminal jobs for the Activity panel only
(`harness/completion_observer.py`, `services/assistant_activity.py:112-116`).
So "update and relaunch, then show me" ends at "the solve is running" and the
user has to ask again. Also, a parameter-only edit does not mark results
stale for most reads (`services/dispatch_status.py:103-131`).

## Scope

1. **Plan.** The model states the chain as one `start_task` plan (edit steps,
   run, walk). Reuse `start_task`; do not build a second graph engine.
2. **Gate.** The run step is admitted only after native read-back of every
   edit and its own approval. A refused or uncertain edit stops the plan
   (LC-C6 unchanged).
3. **Continue on completion.** When a job started by a task step in this
   conversation reaches a terminal state, the harness posts one continuation
   turn into that conversation ("job <id> finished: <status>") and the task
   resumes at the next step. Only for that owner, project and task; at most
   once per job; never after the user cancelled or moved to another project;
   the continuation turn has no approval authority of its own. A closed tab
   gets it on reopen through the durable parent refs.
4. **Stale label.** After a chat edit, result reads compare the solve-input
   fingerprint (`services/workflow_tools.py:423`) and report stale results as
   stale, as the load-flow summary already does.
5. **Failure and cancel.** A failed job is explained from its native log
   (`get_simulation_log_history`) with the solver error decoder; cancel is a
   separate approved action with its receipt.

## Done when

OJ-07, OJ-08 and OJ-10 pass in Tier A, including a fixture solve that takes
longer than 25 s, a restart during the job (no second run, one continuation)
and a cancelled job (no walk). LC-T22.

## Rules folded in from #567 HV-S (study copilot)

Source: draft PR #567 `.scratch/high-value-ai-study/` (commit e8d53410), research only; owner decision AI-567 (2026-10-21). Adopt the listed cases into this ticket's own numbering; each must name its token/packet limit.
- Never mark a step done from a caller-supplied result alone (`resolve_task_step`); read the result back natively before the next step.
- No replay on resume; an interrupted step resumes or is reported, never re-run silently.
- A failed step yields a correction draft, never an automatic retry.
- Superseded by AI-567b: the cross-card study brief is built by the study copilot (ticket 23, #569) on these plans; no separate progress line, the plan card is its one entry point and the card workspace Next row stays the per-card next step.
- Cases: #567 S-series (see 05-IMPLEMENTATION-AND-TESTS.md).
