# Single network case advancement

Contract: `experiment_execution` ledger version 1, bound to the version-2 plan
and the exact prepared-input contract. This delivers one network case only.
Multiple-case orchestration, cross-domain recipes and scientific/domain approval
remain separate scopes.

## Ordinary admission

1. Save the baseline, persist its typed plan and call `prepare_experiment`.
2. `advance_experiment(experiment_id)` arms or resumes this chat. It writes only
   owned metadata and returns one exact `next_call`; it executes nothing.
3. Submit that call as the ordinary offered `run_sensitivity_sweep` tool. Its
   existing execution confirmation, ACL, project locks, schema checks and queue
   apply. The adapter admits exactly one saved isolated scenario and never
   activates it. The baseline network and active binding remain unchanged.
4. Use the offered bounded `wait_for_job` call. A timeout means the wait ended;
   submission, running and failed jobs never become successful results.
5. `refresh_experiment` checkpoints authoritative job/evidence observations.
   Reading the plan also rechecks available sources without starting work.
6. To inspect or export the completed run, explicitly activate the returned case
   project through the ordinary project tool, then call `get_run_evidence` or
   `export_run_evidence` with the exact returned run ID. Native export/download
   guards apply. Activation detaches the chat's execution pointer; it does not
   delete its durable receipt. Return to the baseline and explicitly advance the
   same experiment to resume inspection.

There is no handler loop, project-rebinding task step, second queue, new provider
wire or new stream frame. Strict checked sessions retain their unmapped-tool
refusal. A solver receipt is not a new checked-answer profile or artifact approval.

## Bounds and no replay

The actor/project/plan/preflight digest and deterministic reserved case name bind
every ledger. The normal dispatch reserves an attempt durably **after** offering,
authorization and confirmation, **before** the actual handler. One submission
reservation is allowed, including across recreated chats. The reserved name
cannot be submitted through an unarmed session or a direct sweep call.
The plan's frozen ledger reference is saved before creating the ledger itself;
a missing or orphan checkpoint cannot silently recreate admission.

The saved and active baseline must both match the exact prepared input digest.
The baseline lock is held across scenario preparation/submission; job waits do
not hold it. Attempt caps cover the scoped submission and ordinary case waits;
wall-time caps prevent further advancement. They do not force-kill a detached
handler or an existing solver. Receipt inspection remains possible after a cap.
The two admitted handlers call no billed external vendor; model requests remain
subject to the existing harness/parent budgets, not a newly claimed per-experiment
model-cost meter. A cost cap never authorizes charges or expands the recipe.

`cancel_experiment` stops future scoped calls while retaining the same ledger.
It does not abort a queued/running job. Job abortion is a separate, ordinarily
authorized `solve_queue_abort` action; completed evidence can still be inspected.

Interrupted, failed or partial submissions never auto-retry. Known partial child
IDs are retained; an absent receipt leaves an uncertain reservation. A late
handler may record its actual receipt, but never causes automatic advancement.
Unknown/corrupt/missing checkpoints refuse rather than resetting or creating a
replacement. Re-preparing an armed plan cannot replace its frozen inputs.

## Truthful outcomes

The view distinguishes `case_effects_reserved`, `job_status`, `executed` and
`result_available`. Queued is not completed. An uncertain submission, interrupted
job or abort has unknown execution (`null`) when actual execution is unproven.
Completion requires the authorized child/job relationship, terminal job success,
the exact saved **and solver-recorded** input identity, fresh saved dispatch,
intact immutable run sections and matching recorded/job/saved objectives.

The numeric value and unit come from the matching recorded solver objective;
they are not an independently certified full engineering cost. Source loss,
input drift beneath the legacy JSON precision, missing exact solve identity or
altered evidence withholds values. Readable old plans keep their original IDs;
legacy/unsupported plans must obtain a valid current preflight before arming.

Independent review, real-use calibration, paid generation/media probes and
production activation remain pending/out of scope. The next experiment slice
extends to two cases and compatible comparison/partial outcomes, with explicit
reconciliation of uncertain effects before any retry; domain adapters follow.
