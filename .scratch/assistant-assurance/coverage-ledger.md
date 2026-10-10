<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Answer-assurance acceptance coverage ledger

Status: first reviewed executable RED tranche; assurance implementation absent.
Reliability additions are proposed and pending review. Their requirement groups
A0.4, A1.7, A2.8–A2.10, A3.5–A3.6, A4.6, A6.6 and A7.7 have no new executable
coverage or recorded passes. This session restored documentation only; the
archived test files remain in the transfer bundle and have not been rerun.
Source baseline: local origin/master `d43809ae` (dictation PR 106 already merged).
Reviewed transferred specification commits: `55ce946b`, `34dbb3f3`; publication
was blocked by GitHub HTTP 401 in that session. Current access is recorded in the
[handoff](session-handoff.md). No paid APIs called. Default skips are not coverage.

The independent reviewer approved design publication and this first acceptance
tranche after assertion-strength, public-contract and canonical-hash corrections.
That verdict is not runtime, full phase, scientific correctness or release approval.
The comprehensive [matrix](test-plan.md) remains the requirement source.

## Executable tranche by phase

Backend files live in `pypsa-gui/backend/tests/assurance_acceptance/`. Frontend
file: `pypsa-gui/frontend/src/components/ChatPanel.assuranceAcceptance.test.tsx`.
All model responses are scripted or HTTP-mocked; authenticated API, turn loop,
real tool dispatch, isolated project handlers, chart rendering and downloads run.
Frontend tests mount the real ChatPanel/store and inject stream callbacks.

| Phase | Backend cases | Initial result | Executable behavior |
| --- | ---: | --- | --- |
| P0 contract | 9 | 9 expected failures | Invalid admission/version/mode; strict session metadata; required public routes |
| P1 evidence | 5 | 3 expected failures, 2 controls pass | Real input fact/evidence scope and source/unit; cross-org access; anonymous access |
| P2 publication | 13 | 13 expected failures | Fragmented first/intermediate/final prose and thinking; value/sign/unit/entity mismatch; contradictory narrative; human rendering; bounded malformed-draft fallback; approved answer/receipt retrieval |
| P3 outcomes | 3 | 3 expected failures | Read receipts; fabricated completion; navigation submitted before ACK |
| P4 artifacts | 4 | 3 expected failures, 1 control passes | Exact known chart data/units and canonical hashes; delivered bytes; delivery chain; legacy sidecar fault injection; unsolved output refusal |
| P5 navigation | 5 | 4 expected failures, 1 control passes | Unknown/mismatched action/turn/target/generation refusal; CSRF |
| P6 history | 4 | 4 expected failures | Exact nonempty approved text and receipt in replay/export; repeated fetch never regenerates |
| P7 providers | 4 | 4 expected failures | Actual Anthropic/OpenAI/compatible/local HTTP adapter requests require the same limited nonempty approved fallback; bounded repair |
| UI P2/P6 | 4 | 3 expected failures, 1 availability control passes | No unapproved text/thinking/TTS; usable limited voice answer |
| UI P3 | 6 | 6 expected failures | Submitted/running/partial/failed/uncertain/cancel_requested must not show completed checkmark |
| UI P4/P5 | 2 | 2 expected failures | No unapproved artifact link; actual software checking stage visible |

Total: **47 backend cases: 43 expected failures, 4 existing controls pass**.
**12 frontend cases: 11 expected failures, 1 availability control passes**.
Missing new endpoints/frames, ignored strict options, unchecked publication and
unbound metadata explain the failures. There are no collection/setup errors.
Controls do not prove the new gate: anonymous refusals and CSRF happen in existing
middleware; unsolved chart refusal already exists; the voice availability control
can pass because current UI ignores the new approval frame.

Chart hash checks use the documented canonical encoding and an independent known
G1/p_nom = 20 MW dataset. The legacy-storage corruption case deliberately modifies
the current sidecar and observes `build_delivery`; that path is NOT a future public
contract. Replace its corruption fixture when storage changes. It currently proves
metadata tampering is accepted, while existing byte checks remain separate controls.
An aggregated TestClient response proves frame ordering and fetched identity,
not commit-before-first-byte under a crash/concurrent-revocation race.

## Passing regression checks

Relevant backend selection: **321 passed, 48 skipped** (47 opt-in acceptance plus
one deliberate golden-record generation helper), no failures/errors. Full default
frontend: **3,529 passed, 12 skipped** (the proposed acceptance suite), no failures.
Production TypeScript/Vite build passed. Ruff lint/format passed, documentation
links/whitespace passed, and the inventory matched 220 tools/14 tabs/17 panels.
Commands and limitations are in the [results assessment](../../docs/superpowers/assessments/2026-10-10-assistant-answer-assurance-tests.md).
These controls preserve existing behavior; skipped proposed tests remain unverified.

## Comprehensive requirement mapping and remaining work

A partial row means useful executable cases exist, with explicitly named pending
requirements. No row is a complete phase/release gate. References below are file
stems from the directories above; “UI” means the ChatPanel acceptance file.

| Matrix group | Existing executable coverage | Still required |
| --- | --- | --- |
| A0.1 | Partial: p0 admission; p2 malformed draft | Duplicate IDs, exact size/count boundaries, extra trust fields, complete block schemas |
| A0.2 | Existing inventory CLI matches 220 tools/14 tabs/17 panels | Adapter/family/output-channel behavior mapping; unknown-adapter refusal and stale inventory negative fixture |
| A0.3 | None | Explicit legacy/unvalidated state; disabled/shadow/pilot semantics and old clients |
| A1.1 | Partial: p1 project/entity/unit, cross-org; p2 entity mismatch | Run/section/metric/window identity, forged pointers and traversal, same-org ACL denial |
| A1.2 | Partial: p1 anonymous; p5 missing CSRF | Post-collection/review/cache revocation, disabled identity, publish/read races, invalid CSRF and cross-owner ACK |
| A1.3 | None | Real immutable historical run fixture, failed/newer run choice, current input invalidation |
| A1.4 | Partial: p4 legacy provenance corruption | Evidence retention/loss/corruption/restart; unsaved inputs, source/policy/cache versions |
| A1.5 | None | Capture-before-truncation/omission, complete aggregates vs page extrema |
| A1.6 | Existing unsolved chart refusal control only | Real partial/infeasible/nonconverged/missing/low-fidelity adapters and null semantics |
| A2.1 | Partial: p2 unsafe fragments; p6 replay/export; UI text/thinking/TTS/link | Caption/tool-summary scope, summary/compaction, cold reload and actual speech transport |
| A2.2 | Partial: p1/p2 L1/p_set input fact | All result units/run/window/metric attribution, same numeric value in different sources |
| A2.3 | Partial: p2 negative and MW/MWh mismatch | Locale, kW/MW conversion, percentages/denominators, rounding tolerance |
| A2.4 | None | Freeze additive computed-block schema, independent derived fixtures and zero denominator |
| A2.5 | Partial: p2 valid ledger plus contradictory prose | Caption/omitted claims/full-table/causal assertions and approved interpretation semantics |
| A2.6 | Partial: p2 malformed draft and max-one repair | Truncation, provider/checker errors, exhausted budgets, unavailable strict mode and timeouts |
| A2.7 | Partial: p7 real no-tool HTTP adapters; p2 retrieval call count | Tool prohibition during repair/review, fragmented tool IDs/pairing, stale callbacks/abort |
| A3.1 | Partial: p3 read/forged action/submitted; UI six noncompleted outcomes | Real queue receipts and successful-return failed/partial postconditions |
| A3.2 | Existing provider/confirmation/task regression controls only | Assurance-specific denial/expiry/locks/checkpoint/ambiguous timeout and no replay |
| A3.3 | UI cancel_requested display | Real selected-project/run cancellation and terminal job identity |
| A3.4 | None | Real write/undo/import/export partial effects and typed postcondition comparisons |
| A4.1 | Partial: p4 known static input chart | Time series, historical/current solved-result scope and all chart types |
| A4.2 | Partial: p4 legacy sidecar corruption and byte hashes; UI gated link | Actual manifest/data/render/bytes tampering, axes/captions, mixed runs and expired/unapproved downloads |
| A4.3 | Existing finite-data regression controls only | Assurance aggregation/window/filter/clipping/missing cases and render races |
| A4.4 | Existing report/numeric-audit regression controls only | Assurance-gated report narration, regeneration and disclosure-preserving exports |
| A4.5 | Partial: p4 delivery chart chain | Import assertions/mapping, mixed uploads, binary/table complete chain |
| A5.1 | Existing navigation schema inventory controls only | Real browser state journeys for every mapped view in Expert/Guided and exact historical sources |
| A5.2 | Partial: p3 submitted; p5 unexpected/mismatched ACK | Issue real expected action and matching success ACK; stale issued actions, owner/project/run/revision, closed tab/new navigation |
| A5.3 | UI unapproved artifact link refusal only | Evidence-card source/time/unit/limitation/download UX, external links/XSS/injected markdown |
| A5.4 | UI actual checking stage; p2 no thinking | Complete real stage lifecycle and retry/failure trail; no fake steps/percentages |
| A5.5 | None | Keyboard, screen-reader, mobile, status deduplication and focus behavior |
| A6.1 | None | Freeze read-only reviewer request/response seam; pass/reject/malformed/disagreement/timeout |
| A6.2 | None | Reviewer least privilege, hostile annotations, redaction and shared budget limits |
| A6.3 | Partial: p6 replay/export IDs/text; UI TTS | Summary/cold reload, private provider-pairing trace, retention and compaction |
| A6.4 | Partial: repeated owned-answer fetch no model call | Real restart/resume/provider switch with approved refs and ACL recheck; no effect replay |
| A6.5 | Frame ordering/record fetch only | Commit/emit fault injection, interrupted SSE GET recovery, dedup, incomplete writes and migration |
| A7.1 | Partial: p7 four HTTP-mocked wires; existing workflow provider parity controls | New Responses adapter case; fragmented tool-calling assurance and provider-switch parity |
| A7.2 | None for proposed assurance | Real service journey per family and create/configure/solve/compare/chart/export/reload |
| A7.3 | None | Prolonged mixed-domain/history/error/job/navigation conversations |
| A7.4 | None | Cold/warm/slow/reviewer-down/load measurements, queue/storage limits and cache behavior |
| A7.5 | None | Expert-adjudicated qualitative benchmark, false accepts/rejects, unresolved cases |
| A7.6 | None | Full rollout/rollback/old-client gates after every required mapped family passes |

All family rows in test-plan.md remain pending assurance-specific real fixtures.
Passing existing harness regressions preserves the baseline; it does not convert
those families into checked-answer coverage. Shared source inventory is discovery
coverage, not complete tool/UI or engineering validation.

## Implementation continuation

Before implementation, review/freeze the [reliability contract](reliability-contract.md)
and map its added case groups to fixtures. Original independent review and RED
counts apply only to the transferred tranche. New request-slot, numerical-validity
and mandatory-guard requirements cannot inherit those approvals/counts.

1. Once GitHub authentication works, publish reviewed specification to master.
   If these test files already exist locally, keep them isolated and then publish
   their own PR on the resulting master; avoid duplicate docs under squash merge.
2. Implement one vertical tracer first: reject unsupported strict negotiation;
   then buffer unsafe fragmented provider prose and publish an owned limited
   software fallback. Make its focused RED case GREEN and run relevant regressions.
3. Add positive evidence-backed L1 input publication and authorization; then
   progressively implement P1–P7 tickets and their pending fixtures above.
4. Keep acceptance opt-in until implemented; never make tests pass by weakening
   assertions, silently bypassing strict mode, xfail, or treating skips as passes.
5. Do not claim user confidence guarantees or release readiness until all required
   phase/family/race/domain/browser/calibration gates have evidence.
