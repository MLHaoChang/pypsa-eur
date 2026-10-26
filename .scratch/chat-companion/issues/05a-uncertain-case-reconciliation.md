# CP-05a — Explicit uncertain-case reconciliation

Status: done (#352, e1adb0db)
Type: implementation
Baseline: master `319985014d7cd5b74fbc07bb1ad4975aaffcd6f7` (#351).

## Scope and authority

Extend the existing single-network-case adapter, not its execution recipe or
checked-result allowlist. `reconcile_experiment(experiment_id, receipt_sha256)`
is a destructive-tier metadata control through ordinary offered-tool, ACL,
confirmation and strict-checked policy. The caller provides no outcome or IDs.
The digest binds the current ledger and native submission checkpoint. A changed
receipt requires a fresh inspection and ordinary confirmation.

The original submission reservation is permanent. Reconciliation never executes,
requeues, aborts, deletes, activates, renews budgets or resets a case. Even a
verified pre-effect failure requires a separately prepared plan and fresh
ordinary approvals for any new attempt. Two-case comparison is a later scope.

## Proof

- New native submissions hold a per-experiment cross-process lease from before
  reservation until after the handler and receipt recording stop. Reconciliation
  must acquire that lease without waiting; absence of a session/Future, process
  PID checks, elapsed time and Activity history are not quiescence proof.
- A separate bounded version-1 submission checkpoint binds owner, project,
  plan/preflight, submission attempt and monotonic native boundaries. Record the
  first-effect boundary before scenario creation, then the returned child,
  saved candidate, queue boundary and returned job. No model arguments/results
  can manufacture these checkpoints. Failed journal writes stop before the next
  effect. Missing/corrupt/inconsistent proof never resets admission.
- The lease proves the cooperating submission handler stopped; it does not
  imply that a queued solver stopped. Native queued/running jobs remain such.
  The runtime still supports one application worker; no distributed executor
  or ability to kill a detached thread is introduced.
- A positive handler-finished checkpoint still before the first effect, with
  no child/job receipt, may settle this submission as not started. A crash or
  missing close checkpoint stays unresolved even when the lease is free.
  This is scoped to case creation and
  queue submission, not a claim that the project or chat had no other effects.
- Recover checkpointed child/job IDs only after current ACL, parent/name,
  sensitivity descriptor and exact saved-input read-back match the frozen plan.
  A separate native child origin record binds the exact original job and
  attempt; a later same-input job cannot substitute for the missing return.
  Queue identity and terminal evidence still use the existing native checks;
  completed values require the existing matching saved/job/run oracle.
- A proven child without a queue boundary is partial work, not success. A queue
  boundary without an exact job receipt remains unresolved; never guess a job
  by project name or adopt an unrelated job. Missing/changed/revoked sources
  stay unresolved, without numerical values or retry permission.
- Legacy submissions without the new fenced checkpoint remain readable by the
  existing source observers but cannot acquire fabricated no-effect proof.
  Resolution is bounded historical metadata; every current result is reread.

## Verification seams

Use the agreed ordinary harness controls and authenticated chat/SSE approval
path, real project/queue storage, a real small HiGHS fixture and durable receipts.
Fault injection is at the native handler/storage boundary; never fake a verdict.
First RED: an interrupted submission before effects cannot be explicitly settled
through the public control. Then cover returned-receipt loss, partial child,
still-live/late worker, cross-process lease release/restart, stale confirmation,
duplicate resolution, original-call replay refusal, caps/cancel preservation,
cross-owner/project access, corrupt/missing/legacy proof and strict refusal.

Run neighboring experiment/workflow/effect/transaction tests, all six semantic
suites, manifest/inventory/matrix and expanded offline pilot. Require all exact
head workflows and actual backend shards/pilot/QA steps; run the shared frontend
error-contract checks locally and document frontend path-filter skips. Then a fresh
current-master premerge and ordinary expected-head squash merge.

No paid model/media calls, reviewer contact, engine changes, real-use case
execution, deployment or production activation. Independent domain review,
real-use calibration and live-provider/device verification remain pending.
