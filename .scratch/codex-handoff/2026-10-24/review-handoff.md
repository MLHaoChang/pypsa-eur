<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Coordinator handoff: remaining outsourced tasks

Actual master: `aba252d2384f55116bbd15516a5b025951ba265e`.
Owner authorized proceeding with G-b/K; their explicit prerequisite (#694 verified merged) is still unmet. No repeated owner permission is needed. No merge, force-push, rebase, source replacement or official reviewer contact performed. PR descriptions have current evidence; no CI-invalidating push occurred.

## Current PRs and exact-head gates

### #694 — `101e8745ca44eb45b20a8ebb1a1a076cf5edb2b8`
- URL: https://github.com/MLHaoChang/pypsa-eur/pull/694
- Fresh premerge exit: 0; log: `evidence/proceed-premerge694.log`.
- Fresh GK OK required (old2afe approval stale); then coordinator merge unblocks G-b/K.
- Actual four backend test steps, both pilot/QA steps and aggregate requirement succeeded; see `evidence/proceed-steps694.json`. All current reported checks success/expected skips.

### #699 — `9418177a1f4e93c11948e6117e8731b9d05755c8`
- URL: https://github.com/MLHaoChang/pypsa-eur/pull/699
- Fresh premerge exit: 0; log: `evidence/proceed-premerge699.log`.
- GK OK current; coordinator merge.
- Actual four backend test steps, both pilot/QA steps and aggregate requirement succeeded; see `evidence/proceed-steps699.json`. All current reported checks success/expected skips.

### #700 — `1d5cc8d0ae88547b0e97514fdf998c73479033fc`
- URL: https://github.com/MLHaoChang/pypsa-eur/pull/700
- Fresh premerge exit: 1; log: `evidence/proceed-premerge700.log`.
- Premerge blocked by unresolved prior-head GK NOT OK; fresh GK and UX verdicts required.
- Actual four backend test steps, both pilot/QA steps and aggregate requirement succeeded; see `evidence/proceed-steps700.json`. All current reported checks success/expected skips.

### #704 — `d7dea035757ea8ba14c2861a0eb4f4f920a95f75`
- URL: https://github.com/MLHaoChang/pypsa-eur/pull/704
- Fresh premerge exit: 0; log: `evidence/proceed-premerge704.log`.
- Final GK implementation review required; approved plan alone is not implementation approval.
- Actual four backend test steps, both pilot/QA steps and aggregate requirement succeeded; see `evidence/proceed-steps704.json`. All current reported checks success/expected skips.

### #706 — `6afc428d84320ff0d034d84e63e6c6eae5972e5a`
- URL: https://github.com/MLHaoChang/pypsa-eur/pull/706
- Fresh premerge exit: 0; log: `evidence/proceed-premerge706.log`.
- Official UX OK required.
- Actual four backend test steps, both pilot/QA steps and aggregate requirement succeeded; see `evidence/proceed-steps706.json`. All current reported checks success/expected skips.

## Review/merge requests to relay through owner/coordinator

- #694: review the documented baseline-guard correction on full head101e8745ca44eb45b20a8ebb1a1a076cf5edb2b8, including frozen master1871073 baselines, exact dtype/index/NaN checks, current full remote CI and passing semantic/inventory gate. No engine change introduced by this correction. After fresh GK OK, coordinator reruns premerge and merges.
- #699: current GK OK and fresh premerge pass; coordinator can apply ordinary merge gate.
- #700: re-review corrected description/inventory delta; both earlier GK NOT OK and UX OK belong to7064038. Fresh technical premerge passes semantics/inventory, but existing official NOT OK remains blocking. Do not override.
- #704: final implementation review, including owner-authorized report-job admission guard. Full local result and controlled master failure reproduction disclosed in PR. No blanket local full-suite pass claimed.
- #706: UX review using already committed Guided/Expert screenshots linked in PR; all current CI and fresh premerge passed.

## L local-full-run diagnosis

- Original local full run:19926passed/369skipped/2failed; remote exact-head full shards all pass.
- Two failures are unchanged bulk-update count assertions at the native500-entry audit-history cap.
- Master at actualaba252d reproduces both original assertions after a controlled native logger seed proves500caller-visible entries. Ordinary full40-test file passes on both master and L.
- Five relevant source/test files have identical Git blobs between master and L. Details: `evidence/l-changelog-diagnosis.json`.
- Initial default-network probes stalled and were terminated incomplete; no pass claimed. An initial cap probe failed to establish visible tenant saturation and is excluded. Completed diagnostic uses explicit caller-visible cap and ordinary environment networking.
- Keep tests/logger unchanged; any shared fixture correction is a separate scoped task. A date-only local `.scratch/live-voice/capability-matrix.json` change in L is left untouched/uncommitted.

## After #694 is verified merged

1. Fetch actual master and verify merge metadata. Start G-b from that master on `outsource/profile-tuning-frontend`; approved test-first frontend plan and contract disclosures are in next-steps.md/fresh brief. Do not build on the unmerged G-a branch.
2. Open K draft plan PR from the new master on `outsource/results-plausibility`, body starts `Outsourced task K`; wait for official GK plan OK before code or tests. Proposed rules/tests and scope limits are in next-steps.md.
3. If I lands second relative to694, merge current master as its brief requires and regenerate inventory/control artifacts; never hand-merge JSON.

This session has no callable Claude session-messaging tool, so no coordinator/reviewer message was sent. Owner/coordinator must relay the concrete requests above through their existing review channel. This is an external coordination gate, not a missing authorization to implement G-b/K after their prerequisite.
