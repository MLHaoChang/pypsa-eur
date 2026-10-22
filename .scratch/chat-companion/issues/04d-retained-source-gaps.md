# CP-04d — Retained checked-answer source gaps

Status: done (#327, 622149df)
Type: implementation
Baseline: master `147426641` (includes CP-04c #319)

## Plan and contract

1. Observe the native `answer_approved` seam after its publication projector and
   durable answer/history validation. Read the server's committed answer, never
   provider prose, provisional drafts, arbitrary tool errors or browser hints.
2. Retain one idempotent observation per published answer with non-answered
   requested result slots. Preserve exact answer/turn/session/project identities,
   bounded request descriptors and only missing metric/disposition/reason fields.
   Keep no result values, evidence bodies, answer text, user replies, paths,
   approval credentials or execution authority. Fully answered requests and
   non-scoped refusals are outside this adapter. Reviewer/navigation limitations
   without missing result slots are not source gaps.
3. Extend the existing private decision store with a closed `source_gap` record
   variant. Existing question/confirmation records retain their exact schemas.
   Shared 256-record/1-MB/30-day retention, owner/project isolation, atomic writes,
   transaction locks, corruption refusal and unavailable/truncation warnings
   remain in force. Failure to retain an observation cannot block publication.
4. Project gaps into Needs you as `needs_review`, with last native fact
   `source_gap_recorded`. This records a historical publication gap, not current
   evidence availability. Reads, ordinary follow-ups and later successful answers
   never silently resolve or replace that gap. No implicit matching across
   entities/windows/baselines. Exact resolution/linkage is a later scope.
5. Use existing authenticated revision/digest-bound inspection GET, with fresh
   project ACL on each read, no session/project activation and no mutations.
   Render missing metrics, reasons, requested profile/entity/window/cost basis
   and retained/baseline source IDs as plain React text. Clearly distinguish the
   originating turn from a tool call. A stored request is not evidence authority;
   no solve/retry/approval/answer or historical activation controls are added.

## Validation

Exercise authenticated native stream with FakeProvider (no paid calls), exact
receipt-to-observation identity, process/session loss, owner/project ACL and
Save-As isolation, stable/idempotent reads, clarification history, journal failure,
corruption refusal and request descriptor privacy. Cover fully answered and
partially unavailable real HiGHS requests and rejected/unpublished drafts.
Frontend checks cover safe gap text/details, read-only controls, stale inspection
and project-change cancellation using the existing inspector lifecycle. Reuse
Activity/history regressions, semantic suites, bounded offline pilot and QA,
frontend type/build/tests, inventory, matrix, full current-head remote CI and
fresh-master premerge before ordinary squash merge.

## Boundaries

This adds inspection only. No gap resolution API, durable uncertain-effect
reconciliation, two-case comparison runner, effective-permission changes,
historical-session switching, model calls, deployment or full-coverage claim.
Independent domain review and real-use calibration remain pending by owner
choice. Confirmed validation and PR identity are recorded in the handoff.
