# Bounded experiment prerequisites

Contract: experiment record version 2; network-experiment-preflight/1.
This tranche validates planning prerequisites. It provides no case executor,
execution approval, paid-call authorization or scientific certification.

`start_experiment` persists the existing typed plan with normalized finite caps:

| Field | Default | Allowed |
| --- | --- | --- |
| max_cases | 8 | Integer 1–8, at least the number of cases |
| max_tool_attempts | 48 | Integer 1–96 |
| max_elapsed_seconds | 600 | Integer 1–3600 |
| max_cost_usd | 0 | Finite number 0–10; not authorization to incur charges |
| failure_policy | stop | stop or continue |

Unknown fields, nonfinite values and boolean-as-number caps refuse. Integral
numeric values normalize across transport serializers for equivalent retries.
Owner/project/request ID plus canonical plan content bind deduplication; a
changed plan cannot silently reuse a previous request. The project store retains
at most 100 records; exact retries still work at capacity. No record is evicted to
make room for a new request.

Bounded reads reject duplicate JSON fields, nonfinite checkpoints, unknown state,
unsafe identifiers, broken index references and missing/unindexed records. A
corrupt checkpoint does not become permission to create a replacement. Bounded
transactions and atomic record replacements protect metadata; interrupted writes
leave a visible reconciliation requirement. This does not enable a distributed
live project runtime.

## Preflight

`prepare_experiment(experiment_id)` is a write-tier metadata tool using ordinary
harness offering, authorization and confirmation. Guided mode requires its usual
write confirmation. Strict checked sessions retain the unmapped-tool refusal;
this tranche does not widen any checked-answer profile or effect allowlist.

The first adapter supports current static numeric/boolean network engineering
inputs admitted by the existing change-preview service. It verifies exact asset
and attribute existence, actual component schema/ranges, native units, lossless
schema coercion and the complete candidate's network validation on private copies.
String/enum plans and other domains can still be stored; unsupported preflight
returns a typed refusal and cannot become runnable.

The saved preflight binds actor/project, canonical plan, legacy input marker,
an exact typed input digest, expected candidate digests and warning counts.
The exact digest uses typed IEEE float encodings, missing markers, indices,
column/dtype identity and input-schema metadata; ordinary run/chart/cache
fingerprints remain unchanged. Small parameter edits therefore invalidate the
preflight even when the historical JSON-rounded marker stays the same.

An optional baseline resolves only through the authorized current-project run
store. It must be completed, have intact recorded artifacts and match the
current recorded input marker. A captured digest binds its returned evidence
metadata; source/evidence loss makes preflight stale. This is a recorded-marker
check, not reconstruction or proof of historical raw inputs: case inputs are
validated from the current project. A later executor must capture an isolated
baseline and recheck exact inputs, context, receipts and approvals before effects.

Every response retains `status=planned`, `executed=false` and
`execution_available=false`. `preflight.status=validated` means input-only
preflight checks passed; it does not mean cases ran or their outputs were checked.
Stored plans with uncertain operation/job state refuse rather than claiming no
effects happened. Current input changes or missing recorded evidence return
`preflight.status=stale` on reads.

## Legacy records and next scope

Version-1 plans remain readable by their existing IDs. Successful explicit
preflight upgrades a valid plan to version 2 without starting any operation.
Old ambiguous caps such as `max_cost` remain visible on legacy reads; preflight
refuses them instead of inventing a currency or silently dropping the limit.
Replan with the explicit contract when a legacy plan cannot be prepared.

The next scope is one isolated network case through ordinary project activation,
preview/apply, solver/job, terminal-outcome and evidence/export steps. Existing
durable tasks still forbid project-rebinding steps. Preparation never grants
those effects, and completed or uncertain operations must not replay.
Independent domain review and population calibration remain pending by the
owner's choice. No new paid generation or media probe is part of this tranche.
