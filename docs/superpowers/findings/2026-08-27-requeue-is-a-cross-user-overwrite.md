# `requeue` overwrites another user's project, live, with no lock check

**Date:** 2026-08-27
**Found on:** `master` at `fcb8c4a8` (verified independently in source).
**Severity:** High — data integrity. **Affects:** multi-tenant / server only.
**Status: OPEN and UNOWNED.** `routers/solve_queue.py` is not this session's
file; the solve-queue session is standing by. Recorded here so it does not live
only in a socket conversation.

## The defect

`POST /api/simulation/queue/{job_id}/requeue` starts a solve that **saves a
project**, and checks no lock at any layer.

Three facts, each verified in source rather than inferred:

1. **The foreign-lock middleware compares the wrong project.** `main.py:764-765`
   does `active_ctx = PyPSAService.get_active_context()` /
   `binding_uuid = active_ctx.project_uuid` and looks the lock up on
   `binding_uuid` — the **caller's active session binding**, never the request's
   target.
2. **`requeue` resolves its project independently**, re-resolving from the job
   row's `old["project_key"]`. That has no relationship to the caller's binding.
3. **`requeue` performs no lock check.** `grep -n "get_lock\|holder_user_id"`
   over `routers/solve_queue.py` returns exactly two hits, both inside
   `enqueue_solve` (`lock = project_locks.get_lock(db, project.id)`;
   `if lock is not None and lock.holder_user_id != user.id`). The requeue path
   has no equivalent.

## The path — no setup required

1. User A holds the edit lock on project **P**.
2. User B's active project is **Q** — unlocked, ordinary state.
3. B calls `POST /api/simulation/queue/{job_id}/requeue` for a job whose
   `project_key` is **P**.
4. The middleware evaluates **Q**'s lock, finds it free, and passes the request.
5. `requeue` enqueues a solve that will **save P**.
6. A's work is overwritten by a solve B started.

## Why "the middleware accidentally guards this" is wrong

It was tempting — and this session said it first — to describe the over-broad
middleware gate as the only thing standing between `requeue` and an overwrite,
making the defect *latent*. That is not right, and the correction matters:

The gate only refuses when **B's own unrelated active project** is foreign-locked
— a condition with no bearing on the target. So the accident guards a case that
is nearly irrelevant and leaves the real one wide open. The defect is **live**,
not latent, and removing the (separately incorrect) gate would merely widen a
door that is already open.

## Consequence for sequencing — the natural order is the wrong one

Five queue routes (`pause`, `resume`, `cancel_queued`, `{id}/dismiss`,
`{id}/requeue`) are gated by the foreign-lock middleware and, by the gate's own
written rule beside its constants, none of them should be — they act on jobs or
the process-global dispatcher, never on the session's active project. That
exemption work and this defect look like one tidy-up. They are not:

* **The holder check is the fix, and it ships alone.** Port `enqueue_solve`'s
  `get_lock` → `holder_user_id != user.id` onto `requeue`. It depends on nothing
  else and closes the overwrite immediately.
* **The exemption is UX and can wait indefinitely** without anyone being at
  risk. Bundling them makes a data-integrity fix wait on a cosmetic one.
* Adding the allowlist entry **before** the holder check removes what little
  accidental cover exists — the "destructive route inheriting an exemption" case
  the allowlist's own comment warns about.

## The test that matters

A suite green on five "this route is exempt under a foreign lock" tests, while
the overwrite remains open, **reads as coverage**. The primary test is the one
that pins the new guard:

> `requeue` is refused when the caller is not the lock holder.

The five exemption tests pin a UX property. That one pins a data-integrity
property.

## Scope — stated precisely so nobody over- or under-reacts

* **Multi-tenant only.** `local_mode.is_local_mode()` short-circuits the gate
  and the lock semantics alike; the packaged desktop app is unaffected.
* **Not remotely exploitable by an unauthenticated party.** It needs an
  authenticated collaborator who can see the job.
* **But the trigger set is wider than it looks:** authorization on `requeue` is
  `_may_see`, deliberately weaker than `_may_abort`. More people can requeue a
  job than can abort it.
* **API-only today** — these routes have no UI and no chat tools, so reaching
  them takes a direct API call. That limits exposure now and will stop limiting
  it the moment a UI lands.
