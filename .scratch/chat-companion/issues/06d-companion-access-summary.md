# CP-06d — access summary in in-page chat

Status: done (#361, c75ca558)
Execution: implemented and squash merged in [PR #361](https://github.com/MLHaoChang/pypsa-eur/pull/361)
Merge: `c75ca5584b873418f10f1bca42f868255d445b7b`
Tested source: `bf08c89e011601f21bb37f349a9a013f1143711b`
Baseline: master `8723578028bcb1952a36948dede20ebd09eb2b91` (#358)

Show a concise, server-observed access summary while the user opens “Chat here”.
Use the existing authenticated access snapshot and the exact next-turn checked
selection from the mounted ChatPanel; do not copy that selection into a second
store or infer it from a past answer. Show current project/mode, personal rule,
checking selection and blockers. Explain that actions are rechecked and that an
already running turn retains its admitted selection. No summary grants access,
approves an action or claims the next answer has already been checked.

An explicit keyboard-accessible disclosure opens the existing full access and
restriction controls in the same surface. Keep ordinary Confirmation/Choice
cards and sending independent. Use the same scoped query, fresh refresh/focus/
reconnect behavior and save invalidation as docked chat. Hide old observations
during a fetch, failed refresh, project/chat/session/mode/checking change or
mismatched response. Stop observation and remove the summary when in-page chat
closes or the dock opens. Do not remount ChatPanel or interrupt its stream.

Authorized seams: rendered React UI with API transport/time mocked, plus the
existing ordinary chat send transport to verify the summary and request share
the actual checked selection. Add vertical RED/GREEN cases for automatic scoped
observation, scope/refetch failures, explicit review/save, selection changes and
surface lifecycle. No new backend routes, models, tools, frames or authority.

Durable policy history, wider effect adapters, external readiness, continuous
media, background work and domain adapters stay separate. Live provider/device
verification, independent domain review and real-use calibration remain pending;
no paid calls, deployment or full-coverage claim. Source-pinned CI and the fresh
master premerge gate remain required for the owner's ordinary squash merge.

## Local implementation evidence

- The summary uses a React portal from the continuously mounted ChatPanel into
  the in-page companion. Its transient checked selection remains in that panel,
  and opening/closing either surface does not replace the chat or its stream.
- Public rendered-UI RED cases observed the missing summary and the incorrect
  unavailable-checks label before their implementations. The final 59-case
  access/checked-selection selection passes, including all 12 new cases,
  keyboard review, explicit revisioned saves, failed/in-flight refreshes, late
  project responses, mismatched mode/session/checking, and actual pilot/strict
  in-page send transport. Requested-but-unavailable checks are not labelled off.
- A broader surrounding frontend selection passes 842 cases across 38 suites.
  The final 59-case selection covers subsequent test/copy additions; the counts
  overlap and must not be added. Forced TypeScript and production build pass
  (existing large-chunk warning); these are simulated browser tests, not live
  provider/device evidence.
- Existing backend policy/API regressions pass 300 cases with one existing
  empty-KNOWN_GAPS parameter skip, including all six steward semantic suites and
  authenticated access/restriction tests. No backend behavior changed.
- Exact-source [Test 39076892578](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39076892578)
  passed: all four actual backend test steps, the pilot/QA steps and aggregate,
  frontend Type-check/tests and Unit executed successfully. Digest-verified
  native disjoint JUnit records contain 15,940 passes and 320 known skips; all
  300 selected policy cases executed without skips. Frontend passed 5,595 cases
  across 475 suites, including all 12 new cases. The offline pilot passed all
  133 required cases; QA logs confirmed all 33 drivers. Counts overlap with
  local selections and are not added. Validator validation/report and documented
  engine/integration no-change skips do not certify those paths afresh.
- Exact-source CodeQL, inventory, control matrix and release-note checks passed.
  Fresh `scripts/coord/premerge.sh 361` exited 0 against baseline master with a
  clean merge, no overlaps and all 19 checks successful or expected skipped.
  This frontend/documentation slice did not trigger trial semantic tests; the
  six semantic suites passed in the local policy selection. The ordinary merge
  pinned the full tested head; its squash tree equals the CI trial/source tree.
- Post-merge [Test 39104307790](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39104307790),
  [CodeQL 39104307787](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39104307787)
  and [image build 39104307811](https://github.com/MLHaoChang/pypsa-eur/actions/runs/39104307811)
  completed successfully. The bounded implementation is closed; live/device,
  independent domain review and real-use calibration remain pending. See the
  [package handoff](../../../docs/superpowers/handovers/2026-10-18-chat-permissions-closeout.md).
