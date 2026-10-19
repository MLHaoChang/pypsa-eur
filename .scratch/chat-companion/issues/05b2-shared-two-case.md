# CP-05b2 — Shared two-case network admission

Status: ready-for-agent
Type: implementation
Baseline: master `b51c90645647dd1d2f0ed2f242bf00f7072b4b42` (#353).

## Contract

Extend the existing advance/refresh/cancel controls to exactly two prepared
network cases, in their frozen declared order. Controls execute nothing: each
case is still one exact ordinary `run_sensitivity_sweep`, separately confirmed,
followed by ordinary bounded waits. No unattended loop or activation is added.
The original saved/active baseline must match before each submission.

Use a new version-2 execution ledger containing two independently validated
version-1 case receipts, bound to the parent plan/preflight and case identities.
Existing version-1 single-case ledgers/proofs remain unchanged. Internal case
projections select one frozen candidate; they are never persisted as new plans.
The parent reference is saved first. Missing/orphan/corrupt parent or case data
refuses without replacement. Each case reserves at most one submission forever.
All cases share one started_at, cancellation and combined submission/wait attempt
cap. Caps never reset on refresh, new chat, reconciliation or case transition.
The parent lock serializes reservations; the native submission lease serializes
handlers across cases and survives session loss. Case proofs/native job origins
retain exact case-specific candidate/preflight identities. No paid calls occur.

After a verified completion the next case may be offered. A failed/aborted/
interrupted native job remains visible: `stop` blocks later cases; `continue`
permits the next ordinary admission. Uncertain, partial or unavailable effects
block both policies until explicit fenced reconciliation establishes either a
matching terminal job or positively verified no effects. Partial effects or
unresolved queue admission always block; reconciliation never authorizes retry.
An already reserved case is never repeated, including a no-effect settlement.
Cancellation/caps stop future scoped admissions, not existing native jobs or
receipt inspection. No automatic abort, repair or budget renewal is added.

Parent views expose ordered individual receipts and truthful aggregate state,
combined attempts, elapsed budget and failure policy. Partial/failed outcomes do
not become successful results; each completed case still passes the existing
saved/job/immutable-run numerical oracle. Strict checked profiles stay unchanged.

Extend existing comparison/export with optional explicit `case_id` and
`baseline_case_id` selectors, required for two-case plans. Two distinct cases in
the same plan may be compared; duplicate/unknown selections refuse. Resolve both
owned parent scopes before details. Reuse all existing compatibility, digest and
download rules. No caller metrics, job IDs or compatibility override is accepted.

## Agreed seams and delivery gates

Continue the previously owner-authorized ordinary harness controls, public
authenticated chat/SSE/confirmation/download seams and native filesystem/worker
fault boundaries. Use real isolated Scenario/queue/HiGHS fixtures: 200/300 EUR
objectives, deliberate native infeasibility, unchanged baseline bytes and binding.
Work in vertical RED/GREEN slices. Cover failure policies, shared attempt/time
caps, recreated chats, stale calls, concurrent/reserved/no-replay, denial/worker
starvation, cancellation, source loss, uncertain/partial submission and explicit
case reconciliation, owner/lock and strict refusal, exact comparison/export.

Require neighboring experiment/reconciliation/comparison suites, all six semantic
suites, catalogue contracts and manifest/inventory/matrix, shared frontend
contracts and clean-source offline pilot. Require every current-head workflow,
actual four backend shards, pilot/QA steps and fresh-master premerge before
ordinary expected-head squash merge. Paid/live-provider/device probes, real-use
execution/calibration, external domain review and production activation remain
pending/excluded. No reviewer contact. Broader domain adapters/background work
are separate scopes.

## Implementation and local evidence

The bounded implementation uses one parent execution file and internal immutable
case projections, not child experiment plans or a new queue. Separate proof files
share the parent native lease. Original single-case names/storage remain byte
compatible; two-case scenario names add the frozen ordinal to avoid truncation
collisions. Explicit selectors extend the existing comparison/export tools.

Local native verification passed 235 cases, including all 32 new two-case cases
with zero skips; 112 earlier native experiment regressions also passed (counts
partly overlap and must not be added). Authenticated HTTP confirms each case
separately, denial/starvation reserves nothing for the second case, and native
HiGHS outcomes are 200/300 EUR or deliberate infeasibility. All six semantic
suites and adjacent source contracts passed together: 326 passed / one existing
skip. Shared frontend manifest/profile contracts: 255 passed. Changed-module Ruff,
inventory (255 tools / 15 tabs / 19 panels), matrix (170 controls / 14 families,
full_coverage not_established) and diff checks pass. Clean-source offline pilot,
remote exact-head CI and fresh-master premerge remain required before merge.
Live-provider/device, domain review and real-use calibration remain pending.

Source review additionally reproduced a terminal-before-wait race: the first
native job completed before its tracked wait was dispatched, and the parent
incorrectly selected the second case and refused that wait. A deterministic
public native-job regression failed with experiment_effect_already_reserved.
The correction preserves shared budget/cancellation and exact retained job /
receipt / timeout checks, allowing that already tracked bounded wait without
advancing any case. Revised native two-case suite: all 33 passed, zero skips.
The earlier clean-source 133-case offline gate passed on the initial commit;
it must be repeated on this correction before the PR is opened.
