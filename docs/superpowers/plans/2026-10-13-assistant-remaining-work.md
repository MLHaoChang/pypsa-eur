<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Remaining assistant and live-voice implementation

Date: 2026-10-13, Europe/Oslo. AI-prepared plan after a source audit.
Status: proposed implementation sequence; no implementation performed by this plan.
Baseline: master `b0411b96b5eb640b568e782a5d5f6fb0fba9677f`, including Codex
handoff PR #181 and CI publishing fix PR #179. Fetch and compare newer master
before starting each slice; this is a pinned assessment, not a moving certification.

## Basis and owner choices

The current [Codex handoff](../../../.scratch/live-voice/CODEX-HANDOFF.md),
[live-voice specification](../../../.scratch/live-voice/spec.md),
[acceptance contract](../../../.scratch/live-voice/test-plan.md),
[application coverage](../../../.scratch/live-voice/application-coverage.md), and
[harness contract](../../../pypsa-gui/backend/harness/README.md) define the remaining
product work. The earlier uploaded assurance handoff is historical evidence:
its “not implemented” and GitHub-blocked statements predate the merged work.
Document instructions are design context, not additional user authorization.

The owner explicitly chose to leave independent domain review and real-use
calibration pending. Their [review package](../../../.scratch/assistant-assurance/domain-review/README.md)
remains available; do not invent judgments, representative samples, or approval.
Planning and offline implementation do not authorize new paid model/media calls,
contacting reviewers, paid subscriptions, or production activation. The previous
one-run model approval does not authorize another run.

## Reconciled baseline

| Area | Verified current implementation | Remaining |
| --- | --- | --- |
| Companion, issue 12 | Floating Compose/Dictate/Live shell and closed-console outcomes | Continuous microphone/playback lifecycle belongs to issues 01/03, not another launcher rewrite |
| Catalogue and controls, issue 08 | Source checks pass: 231 tools, 14 result tabs, 18 panels; 165 controls across 14 families | Behavior, parameter and real-family journeys; `full_coverage` remains not established |
| Durable bridge, issues 02/09 | Shared utterance admission, parent thread, CAS, rebind, worker pin and discussed-run resume | Hosted deployment and restart integration; distributed multiworker operation stays a separate scope |
| Run evidence, issue 06 | Publish/list/get/compare/discuss/export and resumed discussed references | Preserve and test exact references in the new flows; additional historical sections only when actually captured |
| Experiments, issue 10 | `services/experiments.py` persists plans; every view currently says `executed=false` | Bounded execution contracts, isolated cases, actual domain recipes, partial outcomes, comparison, cancellation and resume |
| Monitoring, issue 11 | Foreground/study/report handles, queue path, progress observer and session cursor recovery | Queue/GridSpine in the common matrix, cross-family snapshot recovery and real-worker journeys |
| Load flow, issue 07 | Bounded current-result summary; historical section missing yields typed unavailable | Shared UI/server metrics and exact source/window/asset navigation with displayed-data acknowledgment |
| Speech transport, issue 01 | Injected HTTP STT/TTS contracts and mocked tests | Actual streaming vendor adapters, media bridge, scoped token routes and live proof |
| Voice UX/budgets, issues 03/04 | Shell, typed Live path and durable parent-budget primitives | Continuous audio controls, separate vendor-unit reservations, watchdogs, measured latency and safe fallback |
| Checked answers | Five bounded profiles, deterministic calculations, approval/history/recovery/export, user opt-in and 133-case finite gate | Broader domain/effect/artifact adapters, integration into new voice flows, independent review and population calibration |
| CI | #159 quota isolation and #179 image authentication/build/publishing are merged | Validation must identify source and executed steps; a path-filtered green job is not a fresh backend run |

At the final audit snapshot, `b0411b96`'s
[Test](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38424607571),
[CodeQL](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38424607580) and
[image](https://github.com/MLHaoChang/pypsa-eur/actions/runs/38424607569) workflows
passed. Unit tests executed; backend/frontend/GridSpine/integration suites were
path-filtered for the documentation change. Earlier successful full-backend
evidence belongs to #159's tested source, not this newer master. No full-suite
rerun was needed to write this plan. Inventory and control-matrix checks were
executed locally on the pinned baseline.

The issue ledger uses `claimed` for several partially delivered slices. Do not
interpret that as all acceptance cases passing. Older comments and top-of-file
spec counts also need reconciliation against the current source inventory.

## Delivery order

Each row is a reviewable scope. Split the domain and artifact rows into individual
adapter PRs rather than combining them into one change. Start with PR 1, then PR 2.

| Slice | Scope | Dependencies | Primary issues |
| --- | --- | --- | --- |
| PR 1 | Freeze and enforce experiment execution prerequisites | Existing persistence/task contracts | 10, assurance 09/11 |
| PR 2 | Advance one isolated network case through ordinary harness calls | PR 1 | 10, 02, 06, 09 |
| PR 3 | Complete cross-family monitoring/recovery | Existing job adapters; integrate PR 2 | 11, 09 |
| PR 4 | Share load-flow metrics and implement exact result navigation | Existing summary/evidence; PR 3 for completion refresh | 07, 06, assurance 06/10 |
| PR 5 | Complete media-free companion UX and lifecycle scaffolding | Existing launcher/bridge; PRs 3–4 | 03 |
| PR 6 | Add durable media budget and fallback contracts with mocks | Existing parent budgets; PR 5 lifecycle | 04, 09 |
| PR 7 | Implement hosted streaming/media adapters with mocks | PRs 5–6 | 01, 02, 03 |
| PR 8 series | Add commercial, reliability and GridSpine case recipes and typed parameters | PRs 1–3; authoritative domain services | 10, 08 |
| PR 9 series | Expand checked effects, result profiles and artifact admission | Each relevant domain contract/recipe | Assurance 04/05/09/10/11 |
| PR 10 series | Prove all 14 family journeys and prolonged conversation | Relevant prior slices | 05, 08, 09 |
| External gates | Real media/device/deployment proof and production readiness | PRs 5–7/10 plus actual setup and separately authorized probes | 01/03/04/05/09 |
| Deferred review | Independent engineering review and real-use calibration | Actual reviewers and authorized representative data | Assurance release gates |

Media-independent work can progress while external gates remain pending. Pending
review does not prevent writing deterministic guardrails or fixtures; it prevents
claiming independent review or calibrated production reliability.

## PR 1 — execution prerequisites

Targets: [experiments.py](../../../pypsa-gui/backend/services/experiments.py),
[assistant_tasks.py](../../../pypsa-gui/backend/services/assistant_tasks.py),
the shared harness dispatch/admission seam, and
[existing persistence tests](../../../pypsa-gui/backend/tests/test_experiments_persist.py).

1. Define a versioned execution ledger and migrate existing version-1 records
   without starting any operation. Preserve the eight-case/twelve-change bounds
   unless a separately justified contract change is needed.
2. Replace unvalidated `limits` dictionaries with finite, typed maximum-case,
   tool-attempt, time and cost/resource caps and explicit stop conditions.
   Define unavailable usage and partial-failure policy before execution.
3. Validate each target against the actual domain schema, including enum values,
   units and ranges. A syntactically valid dotted path is not sufficient.
4. Resolve baseline IDs through authorized run/project services and freeze exact
   input/run references. Never reconstruct unavailable historical inputs or copy
   another user's baseline based only on a supplied string.
5. Bind request deduplication to owner, project and the canonical plan payload.
   Currently a repeated request ID can return an existing different plan.
   Reject changed-payload reuse, corrupt index/record state and uncertain history.
6. Reuse established storage/transaction and dispatch reservations. Keep deployment
   on one authoritative application worker; process-local locks are not a claim
   of distributed correctness.
7. Specify proposed advancement/reconciliation controls and their safety tiers.
   These are new interfaces until implemented and added to catalogue, dispatcher,
   route, toolset, schema and inventory together. They return the next bounded
   ordinary call or verified state; they do not invoke an unrestricted agent loop.

Acceptance: malformed/unbounded limits, invalid domain paths/units, cross-owner
baselines, conflicting request IDs and corrupt records fail before any effect.
Identical retries reuse the same plan; migration leaves old plans unexecuted.
Freeze `planned`, submitted/running, terminal, partial and uncertain semantics.
An uncertain attempt must never be described as “no operation occurred.”

## PR 2 — first real network case

1. Use an independently calculated tiny network fixture with an immutable
   baseline and one approved case. Extend to two cases and a deliberate failure.
2. Prepare isolated scenarios via the existing project/scenario interfaces.
   Project activation/rebinding happens explicitly through ordinary tools:
   `start_task` currently forbids project-rebinding steps, so do not put
   `activate_project` into its stored step list or remove that protection.
3. Within the bound case project, reuse normal preview/apply, solve/queue,
   wait/evidence and export calls. A persisted plan or task is not confirmation;
   preserve exact arguments, ACL, locks, preview fingerprints and budgets at every
   dispatch. Checked sessions retain the current strict tool allowlist.
4. Reserve and checkpoint an operation before the actual handler. Job submission
   is not completion. Confirm authoritative terminal status and available output
   before marking a case complete or advancing comparison/export.
5. Derive execution summaries from ledger facts; remove the unconditional
   `executed=false` note only where the new contract can prove actual state.
6. Resume completed steps without replay. Reconcile interrupted writes/late
   workers before offering retry. Cancel future case advancement independently
   from an actual job-cancellation request; preserve partial outcomes.
7. Compare only compatible run/source/window/basis records. Export exactly the
   selected evidence and disclose failures and unavailable fields.

Acceptance: real baseline hash unchanged; case input/output equals the elementary
oracle; denied/stale approval performs no mutation; start/timeout/failure never
looks completed; disconnect/restart/duplicate requests do not duplicate effects;
partial failure and cancellation retain truthful evidence. Cover E-I1/E-I2 with
the public authenticated harness path, not service-only happy-path assertions.

## PR 3 — monitoring and restart recovery

Extend [job_adapters.py](../../../pypsa-gui/backend/harness/job_adapters.py), progress
observer/session state and client recovery without another tool catalogue.

- Expand the existing D-C1 matrix to foreground, adequacy/study, report,
  solve-queue and GridSpine jobs. Preserve each family's actual cancellation API.
- Existing D-I2 tests prove session cursor replay; add authoritative snapshots
  and gap/duplicate/reorder recovery across all families and a recreated episode.
  Jobs lost on restart remain unavailable/uncertain until reconciled.
- Include owner/project/run/input-result revision/generation in updates. Refresh
  only the matching result cache/view; never override newer user navigation.
- Add a real-worker multi-family fixture alongside the current state-flip fakes.
  Prove an ordinary read finishes while slow work remains running, without
  model polling, duplicated jobs or conflicting writes.

Acceptance: D-U1/D-I1/D-I2/D-C1 across every supported family; wrong-owner handles
and stale cursors fail safely; stopping a wait/observer does not abort its job.

## PR 4 — load-flow metric and view agreement

Targets: [loadflow_summary.py](../../../pypsa-gui/backend/services/loadflow_summary.py),
[LoadFlow.tsx](../../../pypsa-gui/frontend/src/pages/results/LoadFlow.tsx),
result APIs and [navigation.py](../../../pypsa-gui/backend/harness/navigation.py).

1. Make the UI and assistant consume a versioned shared metric definition/result
   rather than independently calculating loading. Preserve both terminals,
   optimized/configured rating, time-varying limits and weighted windows.
2. Keep active-power proxy and AC apparent-power assessment distinct. Preserve
   missing ratings/flows, nonconvergence and stale/failed solve refusal.
3. Carry exact project/run/source/window/asset/result revision into typed
   navigation. Open the load-flow result tab, select source/window and highlight
   the correct asset; offer an honest route when the tab is unavailable.
4. ACK only after the intended view and matching data revision are displayed.
   Existing mounted-view/input-fingerprint ACKs alone do not prove displayed
   result identity. Unknown study revisions stay submitted, not falsely completed.
5. Historical support requires an actually preserved load-flow section. Add its
   raw/provenance capture before admitting historical claims; otherwise retain
   `historical_loadflow_unavailable` without substituting current results.

Acceptance: one numerical oracle across UI/API/chat; source/window switches and
hidden tabs; newer navigation wins; project/run drift and stale ACKs rejected.
A load-flow summary is not automatically a sixth checked-answer profile. Any
new checked profile needs its own raw-validity, required-slot and publication
contract under PR 9.

## PR 5 — companion conversation scaffolding

Extend existing CompanionLauncher/CompanionOutcome, ChatPanel and chat state;
keep issue 12's completed launcher behavior.

- Add mock-driven listening/thinking/speaking/paused/failed lifecycle, input/output
  captions, mute/end, barge-in, progress/evidence/download/confirmation cards.
- Live stays on the current canvas; opening the dock preserves the same session.
  Distinguish stop playback, end media, abort a chat turn, stop monitoring and
  cancel a job. Each calls its own authority rather than implicitly replaying work.
- Reject late media callbacks after end, project rebind or provider change.
  Close tracks, playback, sockets and subscriptions on every exit.
- Preserve explicit Check answers on retries, corrections, queued utterances and
  fallback. In checked sessions, speech uses only the exact committed approved
  answer; captions, raw tokens and a second paraphrasing model cannot bypass it.

Acceptance: Chromium/mock media tests, keyboard/reduced-motion/focus behavior,
permission/autoplay failure and resource cleanup. Mock lifecycle success remains
separate from real microphone/speaker acceptance.

## PRs 6–7 — budgets first, then hosted media

PR 6 adds provider-neutral, durable reservations for media duration, STT units,
generated TTS and reasoning tokens/cost, including startup minimums. Persist
parent caps across episodes, reconnect/profile changes and compaction. Unknown or
late usage retains a conservative reservation; count final usage once. Watchdogs
close idle/abandoned sessions; typed fallback keeps task/evidence state and does
not restore expired approval tokens or replay a write. Verify with mocks first.

PR 7 extends the existing provider-isolated STT/TTS contracts with streaming
events and actual LiveKit/vendor adapters. Partials are captions; only deduplicated
final utterances enter shared admission. Keep SDK/wire details in providers and
version any stream/event changes explicitly. Use short-lived actor/thread/project/
generation-scoped credentials, CSRF/ownership/rate/SDP limits and pinned-worker
routing. Do not expose main credentials or raw handler access to the browser.

Acceptance: F-U1/F-I1/F-I2 and G-U1/G-I1 mocked cases including cancellation,
401/403/429, invalid language/numbers, interrupted generation, unknown usage,
reconnect and stale callbacks. Real vendor/media gates remain pending until
external setup and separately authorized capped probes are available.

Recheck vendor API/model lifecycle and pricing before selecting production
profiles; the handoff's historical compatibility/pricing findings are not current
proof. Current managed readiness reports only the OpenAI credential. Historical
Moonshot success does not establish present Moonshot, STT/TTS or LiveKit access.

## PR 8 series — domain recipes and complete parameter behavior

After the first network case and shared recovery contract, implement one bounded
commercial/settings, reliability/study and GridSpine recipe per PR. Cover actual
service schemas and job/evidence adapters. A domain name accepted by the current
plan serializer does not demonstrate executable domain support.

Add list/nested/unit/file/topology variants only where actual application schemas
support them, with exact previews and ordinary approvals. Validate uploaded file
IDs through ownership/mapping services; never derive handler paths from arbitrary
dotted input. Preserve baseline isolation, seeds, dependencies, caps, stop policy,
partial failure, same-basis comparison and provenance export in each recipe.

Acceptance: E-U1/E-C1 real small fixtures for each recipe, plus malformed units,
changed previews, mixed source/basis, interrupted effects and false completion.
Unsupported required domains remain explicit and block a full experiment claim.

## PR 9 series — remaining answer-assurance adapters

Follow the [current assurance status](../../../.scratch/assistant-assurance/implementation-status.md)
and [reliability contract](../../../.scratch/assistant-assurance/reliability-contract.md).
Retain the five released bounded profiles and their historical/recovery safeguards.

1. Add exact effect guards and authoritative read-back/receipts for each newly
   admitted experiment/domain mutation or queued job. Approval is not execution;
   an HTTP success is not a verified postcondition. Unknown strict tools refuse.
2. Add result profiles in bounded groups: load-flow loading first after PR 4,
   then actual adequacy/FMEA/GridSpine sections. Each requires frozen scope,
   raw missing/validity masks, solver fidelity, units, complete required slots,
   independently calculated oracle and adverse public-publication tests.
3. Broader Stores/Links/multi-period economics, charging purchases, revenue,
   overnight/FOM and NPV require separate accounting definitions and dependencies.
   Do not relabel current direct costs as total or lifetime economics.
4. Extend chart/report/import/general-delivery admission: captured exact dataset,
   transformation/render specification, labels/axes/units and artifact-byte hashes
   committed together, bounded storage, quarantine and authorized reads. Match
   the selected source/entity/window; finding a number elsewhere is not proof.
5. Preserve approved-only history, summaries, exports and checked speech across
   new adapters. Instrument repair/review and publication errors without raw
   private reasoning or unapproved artifacts becoming public evidence.

Acceptance: per-adapter public-seam golden and adverse cases join the finite
gate; corrupt/omitted/wrong-source values never publish; complete valid requests
do not disappear behind a refusal. Retain meaningful fulfillment/error
denominators and separate mechanical checks from scientific/domain approval.
Independent review and population calibration remain deferred owner inputs.

## PR 10 series — family journeys and release evidence

Populate the existing 14-family matrix with real fixtures for projects/scenarios,
network/assets, time series/multi-period, solver/simulation, results,
reliability/health, GridSpine/campus, commercial/library/investment, experiments,
import/export/delivery, reports, audit/undo, harness/provider workflows, and
UI/preferences/monitoring. Assign exact fixture IDs and executed results.

For each eligible tool/control/parameter/view, test schema, authorization,
eligibility, offered-tool dispatch and intended state/content. Preserve official
OpenAI's 128-tool bound while proving discovery followed by offered execution;
exercise supported provider seams with deterministic adapters before separately
authorized live checks. Tool inventory agreement is not behavioral coverage.

Add prolonged multi-project/provider conversations with run/asset/window
references, restart/compaction/rebind, cumulative budgets, concurrent monitoring,
corrections, partial failures and delivered-file contents. Expand existing
B-C1/run-evidence tests rather than replacing their already delivered behavior.

Full coverage stays false until all required journeys, control parity and standing
blockers have reviewable evidence. Clear blockers explicitly; never infer closure
from a catalog count, fluent answer, mocked transcript or skipped test.

## External validation and activation

Required inputs are actual LiveKit/STT/TTS setup, a hosted authoritative app/audio
worker and representative browsers/devices. Obtain a concrete, separately approved
probe budget before any new paid run. The handoff does not supply those inputs.

Prove continuous speech, English/German/Mandarin and code switching as supported,
technical names and number/unit corrections, noise/echo/pauses, barge-in,
permissions/autoplay failures, complete shutdown and typed fallback. Measure
cost/resource units and useful grounded-answer latency with exact sample/device/
network/model identifiers. Spec targets (feedback <=300 ms, playback-stop p95
<=250 ms, grounded answer p95 <=2 s cached / <=3 s reference uncached) are gates
to measure, not existing performance claims. Disclose benchmark limitations.

Verify hosted single-worker pinning, credential rotation, restart reconciliation,
long-job resume and current context without replay. Distributed multiworker
execution is a separate future design, not a prerequisite silently implemented
inside these PRs. Production activation needs the applicable successful journeys
and media gates; checked-domain certification additionally needs its pending
independent review. Keep capability/assurance limitations visible during pilots.

## Verification and handoff for every implementation PR

- Write meaningful failure cases for changed behavior, then implement through the
  existing harness. Run focused backend/provider/auth/evidence/task tests and
  touched frontend/browser checks. Add real tiny-domain fixtures where needed.
- Regenerate/review inventory and control mappings after relevant schema/UI
  changes. Run layout, frame, alias, catalogue/dispatcher/route and toolset parity
  checks; do not introduce a vendor-specific second planner.
- Run the existing 133-case checked-answer gate when its path or shared harness
  changes. Add required cases for new profiles/adapters before widening admission.
- Complete required PR CI before merge. Record commit/manifest, commands, counts,
  skipped cases and path-filtered jobs. Use a complete relevant backend/frontend
  run for broad harness/UI changes; do not repeat unaffected suites just for a
  planning/documentation edit.
- Append source-pinned results to the appropriate issue, coverage record and
  session handoff; refresh the Codex table only for evidenced closures. A later
  master merge or cancelled run does not inherit an earlier full-suite pass.

The immediate next implementation is PR 1's bounded experiment prerequisites,
followed by PR 2's first isolated network case. Media setup and external review
are tracked dependencies, not reasons to stop this independent software work.
