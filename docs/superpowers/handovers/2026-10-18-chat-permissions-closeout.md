<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Chat permissions — bounded CP-06a–d closeout

Date: 2026-10-18, Europe/Berlin
Application baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Scope: implemented and merged permission visibility, bound confirmations,
personal restrictions and the in-page summary. Remaining CP-06 work is below.

Subsequent browser E2E review exposed two UI failures despite passing CI: pointer
interception of Check answers and narrow companion-review page overflow. The
implementation is merged; browser acceptance is incomplete. The owner's current
whole-application scope and remaining work are recorded in the
[copilot assessment](../../../.scratch/live-copilot/gap-assessment.md) and
[implementation amendment](../plans/2026-10-18-whole-application-live-copilot.md).

## Merged implementation

| Slice | Result | PR | Verified squash commit |
| --- | --- | --- | --- |
| CP-06a | Observed current access, blockers, confirmation rules and configured limits | [356](https://github.com/MLHaoChang/pypsa-eur/pull/356) | `e7777aa2e2c086789f1c802d387410a5280dc0cc` |
| CP-06b | Fixed ordinary-call arguments, current confirmation-rule checks, complete existing engineering preview | [357](https://github.com/MLHaoChang/pypsa-eur/pull/357) | `3f45c0e7d9ad66ee74e6bd918d655419152f3e8f` |
| CP-06c | Revisioned personal project rules, explicit saves and waiting/worker-pickup rechecks | [358](https://github.com/MLHaoChang/pypsa-eur/pull/358) | `8723578028bcb1952a36948dede20ebd09eb2b91` |
| CP-06d | In-page observed access using the mounted chat's actual checking selection and existing full review | [361](https://github.com/MLHaoChang/pypsa-eur/pull/361) | `c75ca5584b873418f10f1bca42f868255d445b7b` |

The [execution contract](../../../.scratch/chat-companion/issues/06d-companion-access-summary.md)
records final-source local and remote evidence. The
[implementation plan](../plans/2026-10-14-chat-companion-implementation.md) and
[acceptance matrix](../../../.scratch/chat-companion/test-plan.md) retain the wider scope.

## Confirmed final-source evidence

PR361 tested source is `bf08c89e011601f21bb37f349a9a013f1143711b`.
Its clean CI trial `ef8fa2564f626491f70bf9e05b8a539cabe1f95e` and squash commit
have the same tree `3b913eb7c13ab6528d8b21319a06d9d796fac3d7`.

- [Test 39076892578](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39076892578):
  all four actual backend shard test steps, actual checked-pilot and QA steps,
  aggregate, Unit and frontend Type-check/tests passed. Native artifact digests
  and disjoint JUnit records verified 15,940 backend passes / 320 known skips;
  all 300 required policy cases executed without skips. Frontend passed 5,595
  cases / 475 suites, including all 12 new cases. Offline pilot passed 133
  required cases; QA logged 33 passed drivers. These finite-suite counts are not
  population error estimates and are not added to overlapping local selections.
- Exact-source [CodeQL](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39076892569),
  [inventory](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39076908964),
  [control matrix](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39076910446)
  and [release-note guard](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39076892572)
  passed. Inventory: 255 tools / 15 tabs / 19 panels. Matrix: 170 controls / 14
  families, `full_coverage=not_established`.
- Fresh `scripts/coord/premerge.sh 361` passed against master `8723578028bcb1952a36948dede20ebd09eb2b91`:
  clean merge, no overlaps, all 19 checks successful or expected skipped. The
  frontend/documentation diff did not trigger trial semantic tests; all six
  semantic suites passed locally. The ordinary squash merge pinned the full
  expected head without an override or branch rewrite.
- Post-merge [Test](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39104307790),
  [CodeQL](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39104307787) and
  [image build](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39104307811)
  passed. Expected Validator skips and engine/integration no-change filters do
  not certify fresh tests for those paths.

The workspace evidence index is
`/workspace/scratch/github-actions/chat-companion-access-summary-evidence/continuation-state.json`;
it points to native metadata, artifact paths, filtered log proofs and the
independent merge verification. GitHub links above remain the portable evidence.
No automation was created for this slice.

## Preserved boundaries

Observed access and preview digests grant no authority. Ordinary backend
admission, ACLs, locks, checked-profile scope and action-specific validation
remain authoritative. Personal rules belong to the actor and saved project and
govern assistant dispatch while that project is active; they are not project
ACLs or protection for every API/cross-project target. Read-only tool
classification neither makes inference free nor verifies an arbitrary external
server. Saved rules may restrict authority, never grant it.

Changed revisions invalidate waiting actions and are rechecked at worker pickup;
saving does not cancel or roll back a handler that has already started. In-page
summaries describe the next-turn selection, hide refreshing/failed/mismatched
observations and do not certify a running answer or restore an old approval.
The current database stores personal rules' current state, not durable history.
The complete old/new/unit review covers the existing engineering adapter only.

## Remaining work and next handoff

1. **Current priority: generic whole-application copilot.** Follow the linked
   amendment for model/tool/parameter discovery, supported edits, verified
   ordered workflows and generic result adapters, then continuous speech. Fix
   the recorded browser UI failures as an early slice. Existing permission
   primitives are reused, not a certificate that every domain is covered.
2. **Durable personal policy history.** Specify an
   authenticated owner/project history view, explicit save provenance and
   revision/time records, retention and atomic save/history failure behavior.
   History must remain observational and never restore approval or broaden a
   rule. Agree its bounded contract and public API/UI tests before implementation.
3. **Wider effect previews.** Add one native/domain adapter at a time, with exact
   target/source/old/new/unit binding, denial/revocation and independent expected
   effects. A generic preview wrapper is not evidence of all-action correctness.
4. **Separate later packages.** External readiness/admin lifecycle, continuous
   media and durable/background responsibilities retain their CP-07–14 contracts.
   This closeout does not implement them or start another experiment case.
5. **Pending external evidence.** ADR-0002 live provider/device verification,
   independent domain review and real-use calibration remain pending by owner
   choice. No paid model/media calls, reviewer contact, manual deployment or
   production activation occurred in this package. There is no full-coverage,
   whole-domain approval or zero-hallucination claim.

Continue from fresh actual master in an isolated worktree, read current
AGENTS/steward/harness instructions and reassess any newer implementation.
Historical passing evidence never certifies an untested replacement source.
