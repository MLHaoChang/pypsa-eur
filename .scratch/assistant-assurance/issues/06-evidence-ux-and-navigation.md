<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# P5: evidence navigation and honest process UI

Status: ready-for-agent; re-audited 2026-11-25 (owner: re-audit, then re-scope): A5.2 and A5.3 mostly done, A5.1/A5.4 partial, A5.5 has no real device or screen-reader test; what is left is under "Remaining scope"
Progress: Evidence card, CSRF-bound /ui-ack and the Load flow join shipped; per-family displayed-data proof, browser journeys for every mapped view, the real stage trail and accessibility journeys remain
Type: task
Blocked by: 04 (only for the A5.4 failure/retry trail; 03 and 05 are done); live-voice issues 03/06 for exact historical UI

Read the [spec](../spec.md), [test matrix](../test-plan.md),
[plan](../../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md)
and harness README. Design status/review is in the plan; readiness is subject to
the listed dependencies and reviewed contract, not implementation certification.

## Done (evidence)

Found in code and tests on master `504b9b3f` by reading them; not re-run in this audit. The "#119" in the old Status was truncated and no PR matches it (`git log --grep` finds nothing; history of these files starts at the re-home PR #1904), so no PR is named.

- **A5.2 ACK (backend)**: `pypsa-gui/backend/harness/navigation.py` issues an owner/project/input/turn/generation-bound action and `/chat/ui-ack` in `routers/chat.py` takes it with CSRF. Tests: `backend/tests/assurance_acceptance/test_p5_navigation.py` (`test_unissued_or_mismatched_ack_does_not_create_navigation_success`, `test_missing_csrf_is_rejected_before_ack_processing`); `test_results_source_navigation.py` (`test_source_ack_requires_exact_rendered_evidence_and_is_idempotent`, `test_boolean_alias_cannot_forge_a_typed_source_ack`, `test_mounted_target_with_stale_result_cannot_complete`); `test_loadflow_navigation.py` (`test_ack_with_changed_highlight_mismatches`, `test_window_ack_fails_after_a_resolve_changes_that_window`, `..._after_a_project_switch`, `..._after_a_newer_turn`); `test_sc07_site3d_ack.py` (`test_ack_with_another_target_is_refused`).
- **A5.2 displayed-data proof, load-flow only**: v2 ACKs record `verification: browser_displayed_data` (`navigation.py:318`, checked at `:395`). `test_navigation_join.py` covers the join (same solve and window, unconfirmed display opened only, other window/project/tab not joined, inputs changed after display named).
- **A5.2 browser side**: `frontend/src/utils/verifiedNavigation.ts:56` posts `/chat/ui-ack`; `verifiedNavigation.test.ts` and `ChatPanel.pastRunRace.test.tsx`.
- **A5.1 (partial)**: typed Load flow, results-source and Site 3D navigation, and the retained-view refusal with a missing section (`test_retained_projection_never_reads_live_results_and_missing_section_refuses`, `test_issue_refuses_typed_when_the_view_is_gone`).
- **A5.3**: `frontend/src/components/CheckedEvidenceCard.tsx` with `CheckedEvidenceCard.test.tsx` (cost basis, unavailable slots and retained scope, malformed metadata refused, clarification without a checked badge, required warning slots). Download is a same-origin `/api/chat/answers/<id>/export?format=zip` link (`:59`), hidden for clarifications. Unapproved artifact links are refused (ledger A5.3).
- **A5.4 (partial)**: `ChatPanel.assuranceAcceptance.test.tsx:220` ("P5: checking progress is visible without exposing planner reasoning") renders the `answer_progress` `checking_claims` stage with no reasoning text.
- **A5.5 (minimal)**: polite live regions exist (`ChatPanel.tsx:1072`, `:4127`, `role="status"` at `:1240`, `:3701`); no test drives them.

## Remaining scope

- **A5.1**: a real browser state journey for every mapped result tab/panel and window/run selection in Expert and Guided; unavailable historic view shows the exact source link. Popup/complex navigation is pending.
- **A5.2**: displayed-data revision proof for every result family other than load flow (they still ACK as `browser_mounted_view`, which is not numerical proof). Owner decision AA02-SCOPE-1 splits this with live-copilot LC-03 per-family adapters. Also: closed-tab and newer-user-navigation cases through the stream, not only the ACK API.
- **A5.3**: external-URL, XSS and injected-markdown cases on the card and artifact cards (only unapproved-artifact-link refusal is tested); assurance type/source/time/units/limitations shown on every card kind.
- **A5.4**: the full stage lifecycle (only actual transitions and tool outcomes), plus the failure and retry trail, and a test that no fake percentage or reviewer step is rendered.
- **A5.5**: keyboard, screen-reader and mobile operation; polite, deduplicated status updates; no focus or navigation takeover. Component and Chromium tests (the owner notes the app is desktop-first, so mobile is advisory).

## Test-first acceptance

Required case groups: **A5.1–A5.5** in the shared test matrix, cut to the remaining scope above. Start with a failing public-seam tracer, implement minimally, run green and relevant regressions. Use independent numerical fixtures and isolated authorized projects; preserve provider-neutral handlers, ordinary safety gates and cumulative budgets. Record every remaining case as executable/passed/red/pending with a reason. Opt-in skip or schema reachability is not behavior coverage. No paid APIs are needed.

## Completion and handoff

Done only when the required behavioral cases and dependencies pass, every
affected public channel uses the same approved contract, and regression/style/
schema/UI checks pass. Append commands, exact red/green counts, policy version,
remaining blockers and next runnable tracer to [handoff](../session-handoff.md).

## Comments

### Proposed reliability revision

P5 must visibly preserve required-slot limitations, per-metric validity and partial-scope warnings from tickets 09–10; a generic passed badge cannot conceal unavailable requested results.

See the [reliability contract](../reliability-contract.md). Original approval
does not cover these additions; review/freeze them before implementation.

### 2026-10-11 — shared harness implementation

The evidence card now exposes requested metrics, coverage/unavailable reasons, retained/current scope, review limitations and authenticated downloads. Supported full-page navigation issues an owner/project/input/turn/generation-bound action; a visible mounted page/tab triggers an authenticated browser ACK, while stale/superseded actions remain submitted. Popup/complex navigation, displayed cached-data revision proof and full browser/device/accessibility journeys remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.

Networkless study contexts have no network revision adapter: their view action
remains submitted without issuing an ACK rather than failing navigation or
fabricating a fingerprint. The new regression and all eight existing navigation
cases pass.


### User-facing checked pilot (2026-10-11)

The user selected an explicit chat opt-in. The control reads current server
capabilities, requests the existing strict publication contract and retains
checking through retry/edit/clarification/queue/Live paths. It cannot disable
server-required strict mode or silently downgrade after rollback. Unavailable
checked history has a conservative retry requirement without approval; async
rewinds reject changed generations, including project round trips.

Final source integrates master `baa3a95e`. The selected shared backend gate
passes 1,097 / seven skips; complete frontend passes 3,686 / 313 files. Native
Chromium with mocked API responses checks keyboard/SSE/digest/rollback at a
380-pixel panel width. Build, 229-tool inventory and touched-file style pass.
See [validation](../checked-opt-in-validation.json), [coverage ledger](../coverage-ledger.md)
and [activation/rollback](../checked-opt-in-runbook.md). No paid calls, full-backend
fresh run, independent domain approval or full-device/calibration release claim.
Global deployment remains disabled and this whole phase issue stays open.

### 2026-11-04 — displayed-data revision proof moved here from 02

Owner decision AA02-SCOPE-1 moves the "displayed-data revision proof" from [02](02-evidence-identity-and-access.md) to this issue (A5.2), with live-copilot LC-03 building the per-family adapters. Today the v2 `/ui-ack` `source_binding` re-reads the load-flow view and records `verification: browser_displayed_data`. Every other result family ACKs as `browser_mounted_view`, which is not numerical proof of what was shown.

## Audit (2026-11-25)

Re-audit done case by case; results are in "Done (evidence)" and "Remaining scope" above.
