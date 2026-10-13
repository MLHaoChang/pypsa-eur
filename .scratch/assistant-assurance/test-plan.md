<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant assurance acceptance matrix

Status: comprehensive test requirements plus a reviewed first executable RED
tranche. GitHub authentication blocked its original transfer; current observed
access is recorded in the [handoff](session-handoff.md). See the
[coverage ledger](coverage-ledger.md) for exact test mappings and pending work.
No passed implementation gates are claimed.
The [reliability revision](reliability-contract.md) adds requirement groups below;
they are pending schema review/executable fixtures, without new recorded passes.

Tests use public contracts and known-good literals/worked independent fixtures.
Mock model transports for adversarial drafts and reviewer failures. Exercise real
authorized handlers, isolated projects/jobs and artifact bytes where possible.
Paid/live behavior and real-device quality are separate, explicitly reported gates.

## Phase matrix

| Case group | Required tests and independent assertions | Observable public seam |
| --- | --- | --- |
| A0.1 | Valid/malformed/unknown versions; duplicate claims; oversized blocks; unexpected fields; no forged trust flags | Draft/answer JSON schema, stream negotiation |
| A0.2 | Every actual eligible tool/family/result tab/panel/output channel mapped; unknown adapter cannot get checked label; intentionally stale inventory rejected | Inventory CLI and ordinary catalogue/tool offering |
| A0.3 | Legacy clients/history/answers explicitly unvalidated; feature disabled/shadow never advertises checking | Stream/history and ChatPanel |
| A0.4 | Server-owned request/metric/profile/guard contracts; spoofed intent/ownership refused; changed inventory metric/parameter/surface without mapping fails CI | Reviewed schemas, admission and inventory |
| A1.1 | Evidence IDs bound to correct actor/org/project/run/section/asset/metric/window; forged refs, traversal, unauthorized cross-project/source blocked | Authenticated evidence API and stream |
| A1.2 | ACL revoked after collection/review/cache hit before publish/read; disabled user; CSRF missing/invalid on stateful ACK; no leaked values | Stream, evidence/artifact/ACK routes |
| A1.3 | Current input change invalidates current-source claims; selected historic completed run remains as-of valid; newer failed/running does not replace it | Run listing/evidence and approved answer |
| A1.4 | Hash corruption, lost source, duplicate completion, unsaved foreground inputs, cache/policy revision, retention expiry and restart | Run/evidence/artifact APIs |
| A1.5 | Full results captured before 4,000-char truncation/40,000-char budget omission; correct complete aggregates; page extrema not global | Tools -> approved facts/evidence |
| A1.6 | Not converged/infeasible/partial/missing source/low-fidelity outcomes and configured assumptions are disclosed; null not zero | Domain adapters and answer |
| A1.7 | Raw validity captured before sanitization; actual zero vs non-converged/missing/not-applicable; bad residual/feasibility and incomplete coverage block or limit the exact metric | Raw source -> adapter -> answer |
| A2.1 | Invented result in fragmented first/intermediate/final prose/thinking never reaches text, captions, public history or TTS; raw tool cards are scoped | Stream/ChatPanel/history/voice |
| A2.2 | Valid facts render exact entity/metric/run/unit/value/window; same value in another entity or metric does not verify | Answer blocks and UI |
| A2.3 | Negative/sign/locale decimals, separators, MW/MWh and kW/MW conversion, percentages/denominators and rounding tolerances | Draft -> approved facts |
| A2.4 | Known deterministic derived values (e.g. 8 minus 5 = 3, percentage basis explicit); wrong formula/zero denominator/arbitrary expressions blocked | Computed claim blocks |
| A2.5 | Valid claim ledger plus contradictory prose/caption, omitted claims, invented entities, unsupported whole-table/causal statements | Structured draft -> published answer |
| A2.6 | Bad JSON, provider error before/after fragment, max-token truncation, budget limit, checker exception, single repair limit and honest fallback | Stream and approved answer |
| A2.7 | No new tool execution during repair/review; exact IDs/pairing/continuation; delayed stale turn/project callback and abort never publish | Actual provider HTTP adapter -> tool seam |
| A2.8 | Key-results profile fulfills required requested slots/warnings; valid wrong metric/run/window refused; ambiguous cost/capacity clarified; unsupported/missing slots explicitly limited; 20-block limit preserves scope | Admission -> rendered answer/receipt |
| A2.9 | Hand-worked weighted energy/multi-period joins, objective-to-cost bridge, baseline direction, percent vs percentage points, zero/near-zero denominators; rounding cannot enlarge tolerance; formula DAG malformed/cyclic/unknown refs blocked | Registered computed blocks and result services |
| A2.10 | Complete-population all/none/top-k predicates, ties/exclusions, feasible incumbent vs optimum, stochastic coverage/uncertainty; malicious labels/source notes cannot establish validity or reviewer approval | Predicates/interpretation -> all public projections |
| A3.1 | Submitted/running not completed; successful return with failed/partial status not success; unavailable effects uncertain | Tool receipt, stream/card/status |
| A3.2 | Confirmation denied/expired, lock refusal, checkpoint failure and timeout after possible effect; no automatic replay | Tool execution and durable task |
| A3.3 | Cancel requested not cancelled; job terminal state belongs to selected project/run; unrelated/newer job cannot satisfy completion | Job adapter -> outcome claim |
| A3.4 | Mutating postconditions compare requested and actual saved/unsaved values; undo/import/export effects and partial writes | Real tool handlers and action receipts |
| A3.5 | Missing/raising mandatory guard, invalid full schema, unauthorized changed paths, altered confirmed arguments and stale revision after wait block before effects; courtesy checks cannot override denial; bounded no-progress stop | Ordinary dispatch/confirmation/commit |
| A3.6 | Successful return but wrong read-back values, extra edits, partial writes or mismatched artifacts cannot say complete; duplicate operation/conflicting args and late worker reconcile without replay; model-supplied completion alone refused | Task/job/state receipt and UI |
| A4.1 | Exact chart source data/indices/entities/units match independent fixture; input labelled configured; historic/current scope accurate | Chart tool -> manifest/dataset/download |
| A4.2 | Swapped/corrupt dataset/render spec/sidecar/image; mislabeled axes/units/caption; mixed runs; unapproved or expired artifacts not released | Artifact provenance/download |
| A4.3 | Aggregation/window/period/filters, clipping, NaN/inf/missing and partial dataset are accurately represented; change during render handled | Chart/table artifact and claim |
| A4.4 | Report narratives preserve required disclosures, checked claims and exact code-rendered tables; hallucinated interpretation withheld; regeneration remains scoped | Report generation/view/export |
| A4.5 | Imports are input assertions until validated; format/mapping/units not guessed; binary/table delivery bytes plus dataset/render hashes verified | Import inspection and delivery manifest |
| A4.6 | Same metric/formula/source/masks/required warnings in text/table/chart/PDF/download metadata; plausible incorrect service aggregate fails independent raw/invariant checks; unit/permutation/window reconciliation metamorphic fixtures | Real result service -> artifact -> approved answer |
| A5.1 | Every actual result tab/panel and mapped asset/window/run selection works in Expert/Guided; unavailable historic view uses exact source link | Typed navigation -> browser state |
| A5.2 | ACK exact action/turn/target/project/run/revision/generation; absent/forged/stale ACK, closed tab, newer user navigation cannot say opened | CSRF ACK API and stream |
| A5.3 | Evidence cards explain assurance type, source/time/units/limitations; direct open/data-download; no arbitrary external URL/XSS/injected markdown | ChatPanel/evidence/artifact cards |
| A5.4 | Only actual stage transitions/tool outcomes visible; no fake percentages/reviewer steps/chain-of-thought; failure and retry states understandable | Stream -> rendered process trail |
| A5.5 | Keyboard/screen-reader/mobile operation; status updates polite/deduplicated; no unexpected focus/navigation takeover | Component and Chromium tests |
| A6.1 | Reviewer accepts/rejects/disagrees/malformed/timeout; zero reviewer tools; deterministic failure cannot be overridden | Mocked reviewer HTTP through neutral seam |
| A6.2 | Reviewer evidence least privilege, fenced hostile annotations, no keys/private paths; bounded input/output/calls; same cumulative budget | Reviewer request and metrics |
| A6.3 | Public reload/summaries/export/TTS use only approved text+receipt; private traces retain tool pairing, expire safely, never certify drafts | History, provider continuation, voice |
| A6.4 | Restart/resume/compaction/profile switch preserve exact constraints/ref IDs/approval metadata; ACL/cache/binding rechecked; no action replay | Durable thread/public history/tool loop |
| A6.5 | Interrupted publication, deduplicated answer IDs, incomplete writes/reconnect, source retention and policy migration | Stream, approved-answer API/history |
| A6.6 | Request constraints/profile/formula versions preserved across resume/provider switch; toolset/fallback/report/job/task notifications cannot bypass checks; cached older formula cannot certify changed calculation | History/notifications/cache/shared consumers |
| A7.1 | Cross-provider parity: Anthropic, official OpenAI, compatible remote/local transport; fragmented args; identical guard decisions | Real SDK HTTP mocks -> shared harness |
| A7.2 | At least one REAL service journey per required family; workflow create/configure/solve/compare/interpret/chart/export/reload | Authorized full app/API/browser |
| A7.3 | Prolonged mixed-domain conversation, injected wrong answers/errors, historical run follow-ups, pending jobs and navigation | Multi-turn integration |
| A7.4 | Cold/warm/slow/reviewer-down/load; acknowledgement vs validation vs answer latency; max queues/storage/budgets; cache invalidation | Timed isolated integration/telemetry |
| A7.5 | Domain expert independent golden judgments for qualitative review; false acceptance/rejection and unresolved cases published | Calibrated reviewer benchmark |
| A7.6 | Shadow/pilot/strict/rollback and old-client behavior; no unvalidated factual publication labelled trusted; all mapped families green | Release configuration and browser |
| A7.7 | Per-required-operation/metric mappings and independent fixtures; zero mandatory deterministic false accepts/leaks/effects; supported valid requests fulfill required slots; abstention/usefulness/error denominators and versioned metrics reported | Complete release matrix/benchmark/report |

## Family completion matrix

The existing [application coverage contract](../live-voice/application-coverage.md)
is authoritative. Each row below needs an adapter/source/fixture/case mapping,
not just one mock answer or a discoverable schema. Static counts do not certify
runtime behavior. Parameters and interfaces without tools remain explicit gaps.

| Family | Required evidence and journey |
| --- | --- |
| Projects/scenarios/templates | Correct IDs/ACL/binding; create/edit/save/branch/open receipts and actual state |
| Network/topology/assets | Typed units/ranges/asset attribution; preview/apply/read-back/undo; static vs dynamic inputs |
| Time series/horizon/multi-period | Actual timestamp/index/period/weights/timezone, missing/mapping, retained input provenance |
| Solver/simulation | Mode/convergence/validity, queue lifecycle, configured versus actual inputs and current/as-of scope |
| All 14 result tabs and asset details | Independent golden metrics per tab, consistent source/run/window/units, no stale/missing-as-zero |
| Reliability/health/adequacy/FMEA | Assumptions, seeds/confidence/coverage/fidelity, failure cases and bounded recommendation |
| GridSpine/campus electrical | Stage/run/config/connection identity, ranked scope, refined comparison and handoff artifacts |
| Economics/investment/Library | Period/currency/price basis/denominator/discounting, software-computed metrics and scenarios |
| Experiments/sensitivity | Baseline isolation, compatible cases and partial failure, exact deltas and postconditions |
| Import/export/delivery | Declared file/input origin, mapping errors, byte/data/render hashes and authorized downloads |
| Reports/templates/edit round trip | Checked text/tables/captions, disclosure retention, exact evidence after regeneration |
| Audit/undo/checkpoints | Real effect/audit/revision receipts, ambiguous-effect review and no replay |
| Harness/workflows/providers | Every tool discoverable/eligible, safety unchanged, cross-provider continuation and budgets |
| UI/preferences/monitoring | Exact target/context ACK, intended settings state, independent progress and stale-generation refusal |

## Executable suite policy and release evidence

New numerical oracles must not call the service under test to obtain expected
values. Use independent raw inputs/arithmetic, reviewed domain expectations and
plausible incorrect-result fault injection. Positive companion cases prevent
an always-unavailable implementation from satisfying negative-only tests.

After reviewed plan publication, create phase-divided opt-in acceptance modules
against current public seams with deterministic fixtures. Execute with the opt-in
enabled and record the expected red failures, unavailable endpoints and any
already-green regression controls. Default opt-in skips are NO coverage. Cases
requiring new public interfaces must state the pending schema/dependency and be
marked requirements until meaningful executable fixtures exist; do not pretend
to test future scientific services with hardcoded self-confirming mocks.

The first test PR need not implement every future real-domain fixture; its
coverage ledger must explicitly separate executable tests, specification-only
requirements, unimplemented behavior and paid/device gates. Map every case above
to a test or a remaining blocker. Later implementation follows vertical TDD;
preserve behavior expectations while evolving a reviewed public API contract.

Required implementation validation: focused red/green unit/component seams,
backend shared-harness/auth/tool/history regressions, complete frontend suite,
TypeScript/Vite build, schema/inventory/style checks, provider transport parity,
Chromium multi-step/evidence/chart/download/navigation tests. Live model probes
are bounded supplements with cached fixtures/spend ledger, not deterministic
release tests. Human qualitative/device gates stay explicit.

Publish test commands, baseline commit, mode/case count, red/green/skip reasons,
JUnit/log paths and provenance in the handoff after each slice. Proposed latency
limits in the spec require environment/model-specific measured results. Full
release is blocked by any unmapped family or unresolved required negative case.

## Bounded StorageUnit dispatch acceptance

`test_storage_assurance.py` extends A0.4/A1.7/A2.8–A2.10/A6.6/A7.7 with an
independent HiGHS LP (10/4/−6 MWh; states 8/4/0), differing storage/generator/
objective weights, exact windows and raw chronological validity. It rejects
missing prior states, impossible balance/net power/bounds, invalid durations,
unsupported cyclic/inflow/spill sources and fabricated model quantities/units/
formulas. Retained JSON/ZIP, current-source invalidation and native restart
recovery exercise the existing public seam. UI cases verify digest-approved
storage text/voice and unavailable-cost completeness. Broader storage economics,
all-domain adapters and independent review remain pending.

## Bounded StorageUnit economics acceptance

`test_storage_cost_assurance.py` uses independent literals from a real LP:
20 EUR discharge, 16 EUR holding and 15 EUR allocated fixed cost give 51 EUR
direct cost; an exact middle window gives 39 EUR. Distinct storage/generator/
objective weights, static/dynamic prices, signed costs, solved extendable power,
simultaneous operation, missing dependencies, unsupported cost models and
overflow exercise the numerical boundary. Zero-baseline and same-basis retained
comparisons, exports/replay, current-source invalidation, typed cost choices,
previous distinct runs and native restart recovery exercise public shared paths.
Fabricated numbers/units/formulas/omitted slots/narrative stay withheld. UI
digest approval, voice and incomplete unsupported-NPV slots use the same receipt.
The adapter is opt-in; independent review and broader economics remain pending.

## User-facing checked pilot acceptance

`ChatPanel.checkedOptIn.test.tsx`, `api/reliability.test.ts` and
`test_checked_answer_pilot.py` exercise explicit admission, ordinary companion
behavior, required strict mode, malformed/unavailable capabilities, rollback,
pre-ack suppression, digest/voice publication, absent/downgraded/duplicate
acknowledgement and conservative retry/edit/clarification/queue/Live policy.
History unavailable hints cannot grant approved badges. A round-trip project
change during an asynchronous rewind cannot clear/resend another conversation.

`scripts/smoke-checked-answers.mjs` uses native Chromium, the real chat UI and
fetch/SSE/digest path with mocked HTTP responses. It checks keyboard opt-in,
approved publication, suppression, HTTP rollback and choice/draft preservation,
then explicit ordinary opt-out. Real backend numeric/auth paths are separate
pytest gates; no paid model call or whole-device/domain certificate is implied.
