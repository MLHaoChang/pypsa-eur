# CP-05b1 — Compare and export two verified case receipts

Status: done (#353, b51c9064)
Type: implementation
Baseline: master `e1adb0db95111ec9ef0f1ef8471938313a4e1cb6` (#352).

## Bounded delivery

Implement the comparison/export part of CT-18 using two individually prepared,
ordinarily approved single-network-case plans in the active authorized baseline
project. No new executor, shared two-case plan admission, stop/continue scheduler,
retry permission or migration of the CP-05a ledger/proof protocol is introduced.
Those advancement requirements of CT-17/CC-07 remain a later slice. This is not
completion of all CP-05 or whole-application/domain certification.

`compare_experiments(experiment_id, baseline_experiment_id)` is read-tier.
It accepts only distinct owned experiment IDs, never numerical values, child/job
IDs, compatibility overrides, windows or caller verdicts. Resolve both records
before exposing either. Re-read each existing native execution oracle. Missing,
failed, cancelled, partial, unresolved or unavailable cases retain their actual
state; no submission, reconciliation, activation, refresh write or newest-job
substitution occurs. A corrupt execution source remains unavailable without
repairing admission. Foreign/missing plan IDs refuse without source disclosure.

Compare only network single-case plans with validated frozen preflights and the
same exact prepared baseline input/configuration identity. Each completed case
must independently match its saved candidate, solver-recorded exact input,
native owned queue receipt, intact run evidence and recorded/job/saved objective.
Any referenced recorded baseline evidence is re-read and must still match its
frozen completed evidence digest; missing or corrupt baseline proof withholds
that case's numerical result.
Further compare full prepared snapshot/investment-period/weight identities,
solver configuration, native mode and objective units. No accounting/cost
interpretation beyond `recorded_solver_objective` is certified. Planned candidate
differences are disclosed; unrelated drift, mixed bases or missing proof yields
no delta. A historically consistent pair does not restore current baseline inputs.

Compute only finite `case - baseline_case` objective deltas in software, including
percentage with null for zero baseline. Keep individually verified outcomes
visible if a pair cannot be compared. A bounded canonical version-1 report binds
both plans/preflights/receipts/evidence and a SHA256 of the complete report.

`export_experiment_comparison(experiment_id, baseline_experiment_id,
comparison_sha256)` is write-tier through ordinary policy and the existing
edit-lock-aware authenticated agent-export/download path. Re-read sources and
require the inspected digest; changed evidence/status/cancellation refuses.
Export exactly that software-derived report as JSON, with no caller-supplied
metrics, no cached-report substitution or claim that an old export certifies
current sources. No strict checked-profile/allowlist expansion.

## Agreed verification seams

Continue the authorized harness controls, authenticated chat/SSE and download
seams used by CP-05a. Real isolated native scenario/queue/HiGHS fixtures provide
independent 200/300 EUR oracles and an unchanged baseline file; no private verdict
mock. Start with a RED public comparison of two completed independently approved
cases, then add partial/infeasible, zero denominator, source/identity/config/units
drift, foreign-owner/project, corrupt/missing ledgers and stale export/lock/strict
policy cases one vertical slice at a time. Fixture faults occur at native storage
or provider boundaries. Paid provider/media calls and real-use cases are excluded.

Require neighboring experiment/reconciliation/evidence/export tests, all six
semantic suites, tool contracts, manifest/inventory/matrix, local shared frontend
contracts and a clean-source expanded offline pilot. Then all current-head
workflows, actual backend shards/pilot/QA and fresh-master premerge before ordinary
expected-head squash merge. Document expected Validator/frontend/engine path
skips; never claim they certify fresh full suites. Independent domain review,
real-use calibration and live-provider/device verification remain pending.
