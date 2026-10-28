<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P1: authorized evidence identity and validity

Status: in progress — list_components page evidence (A1.1, current-input part of A1.3, A1.5 page capture) in #806; A1.2 revocation cases executable on claude/assurance-02-revocation; rest of P1 open
Progress: Authorized retained evidence, ACL rechecks shipped; status says whole phase open, displayed-data revision proof pending
Type: task
Blocked by: 01; live-voice issue 06 for retained-run guarantees

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Scope

Capture full typed tool data before truncation, scope it to actor/project/run/entity/metric/window and persist through the existing run/artifact design. Implement bounded authorized evidence resolution, ACL revocation/revision rechecks, historical as-of labels, complete-source aggregates and mode/coverage/solver validity. Do not infer integrity or convergence from a hash alone.

## Test-first acceptance

Required case groups: **A1.1–A1.6** in the shared test matrix. Start with a failing
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

### First implementation continuation

Current-input capture, owned persistence, exact component/metric/unit matching and
fresh source/ACL checks are implemented for a static-input pilot. This ticket is
not complete: retained runs, coverage masks, all-domain adapters and recovery/race
fixtures remain required. See [implementation status](../implementation-status.md).

### Proposed reliability revision

P1 also requires A1.7: preserve raw validity/missing masks before sanitization and apply numerical validity/coverage rules. Coordinate ticket 10; matching a sanitized service response cannot certify calculation correctness.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

Scoped raw evidence is now immutable through the store API, owner/project authorized and recalculated on every public lookup. Explicit retained IDs preserve a bounded historical run after foreground edits; current-source records still invalidate. Per-owner bounded transactions enforce quotas/atomic files across independent POSIX processes. The project/network runtime is still single-worker, Windows locking and complete browser/backend restart journeys are not certified, and retention is 24 hours. Unresolved effect reservations are not pruned or reused.

See [implementation status](../implementation-status.md) and [contracts](../harness-contracts.md). Whole ticket remains open.

### 2026-10-28 — list_components page evidence

`list_components` is the second checked read (`static_input_page_evidence`). A page's evidence holds the exact static inputs of the rows it showed, by entity name (RFC 6901 paths), bound to owner/org/project and a digest of the class's rows and input metrics taken before the handler; a changed digest at capture or lookup, or another project, makes it unavailable. Out-of-range pages are refused before the handler. Answers name the rows ("rows a..b of N, not the whole class"). Retained runs, coverage masks, aggregates, results families and other read families remain open.

### 2026-10-28 — A1.2 revocation before publish and read

A1.2 is now executable in `tests/assurance_acceptance/test_p1_revocation.py`, covering two revocations: org membership removed (project ACL) and user disabled. If access is revoked after a `get_component` or `list_components` capture and before publish, nothing is published: no approval, no token, no answer record, and the stream ends with `assurance_publication_failed`. A tool called after revocation returns `tool_error` with no values. No log record carries the value or the draft. Each route is read successfully before revocation. After it, the answer, export and evidence routes give exactly the status and body of an unknown id (404 `Record not found` for membership, 401 for disabled), and history projects `unavailable` without the text. One product fix: `records.load` used to answer a revoked project with 404 `Project not found`, which told a revoked record apart from a missing one. Still pending: CSRF on a stateful ACK (no assurance ACK route before P3) and a reviewer cache hit (the reviewer is withheld, so no cache exists).

#### Follow-ups

- **REVOKE-TOCTOU.** The project ACL is not held under `network_guard`. A revocation that lands between commit's last `records.authorize` (`harness/assurance.py`, inside the `network_guard` block) and `records.save` can still commit the answer; the next read refuses it. Closing the window needs the ACL check and the save inside one DB transaction, or a re-check after save that deletes the record.
- **REVOKE-KIND.** Revocation surfaces as the generic `assurance_publication_failed` ("could not be saved"), and `harness/loop.py` logs it as an ERROR traceback. A typed kind (for example `assurance_access_revoked`) and a warning-level log need a ChatPanel change and UX review.
