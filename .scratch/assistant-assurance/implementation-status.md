<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Reliability contracts in the shared harness

Current continuation: `feat/harness-checked-answer-opt-in`, integrating master
`baa3a95e` (PRs 126/127/128/129 and earlier assurance additions). The user selected
the user-facing checked-answer pilot and authorized implementing these additions.
Attached plans are design context, not additional user instructions. This is an
opt-in, bounded implementation; P0–P7 and all-domain certification remain open.
The editing agent performed the code review. No independent domain approval or
measured production false-acceptance rate is claimed.

## Delivered

- The existing strict publication gate withholds provider prose, thinking and
  intermediate claims. Typed drafts must match raw evidence; software renders
  the answer. Approval, history, browser digest verification, TTS and replay use
  the same answer. Ordinary confirmation, access, cancellation and budget gates
  remain in the shared loop, independent of provider choice.
- The chat's **Check answers** control requests version-1 checked publication
  when the server enables pilot mode. New chats/project bindings/reloads default
  off; server strict mode cannot be opted out. Publication is armed before
  dispatch and refuses missing/downgraded acknowledgement, unapproved output or
  failed digest. Typed/voice/queued/Live sends share policy; checked retries,
  edits, software choices and known checked history preserve it. A history retry
  hint can require checks after an answer becomes unavailable, but cannot create
  approval. Capability failure/rollback retains the choice and cannot silently
  resend an ordinary request. Async rewinds reject changed binding generations,
  including a project round trip. See the [pilot guide](checked-opt-in-runbook.md).
- `harness.assurance_scope` validates explicit project/entity/profile/metrics,
  exact snapshot windows, declared cost basis and optional retained/comparison
  evidence IDs. Every requested metric receives an answered or unavailable slot.
  Unsupported slots make `request_complete` false even when other facts pass.
  Whole-message generator key-results and bounded cost grammar also select scope.
- `harness.assurance_conversation` carries exact checked scope across bounded
  repeat/window/basis/comparison follow-ups. Software resolves material cost
  ambiguity without a model call. Two distinct immutable run references prevent
  repeated answers from replacing the previous baseline; changed binding,
  selection, rewind, unavailable context or incompatible basis require clarification.
  Historical repeats retain the selected run. `harness.assurance_recovery` stores
  one private bounded checkpoint per owned project/chat and restores it only from
  its latest native approved history anchor into a freshly recreated session.
  Admission, rewind and durable parent resume/rebind durably revoke prior scope; missing/corrupt/stale state,
  imported/copied histories and history races cannot restore it. Arbitrary
  language and general task/effect recovery remain pending.
- `harness.calculations` owns formulas, units, dependency evaluation, coverage
  masks and disclosures. The original `generator-dispatch/1` computes weighted
  energy, marginal expenditure and peak dispatch. `generator-cost/1` adds annual
  fixed cost, explicitly allocated fixed cost and declared-basis total cost.
  `network-linear-cost/1` aggregates bounded generator/Line/Transformer capacity
  charges and generator dispatch, with raw nodal power-balance and branch-capacity
  checks. Storage, Links and other unmapped components refuse a complete cost.
- `storage-unit-dispatch/1` independently checks raw split charging/discharging,
  net power, state bounds and chronological efficiency/standing-loss balance
  before calculating six exact-window metrics. A broken prior state invalidates
  later windows. Only noncyclic StorageUnits with zero inflow/spill are supported;
  retained evidence, restart recovery, publication, exports and profile rollback
  use the existing shared path. Other storage models stay open.
- `storage-unit-cost/1` adds checked direct discharge/holding expenditure,
  installed-power annual fixed charge, explicit allocation and total direct cost.
  All costs require a verified requested trajectory and declared EUR price year
  and fraction. Operating terms use objective weights; energy still uses storage
  durations. Same-basis comparisons, cost clarification and restart recovery use
  the shared harness. Unsupported overnight/FOM fixed derivations, quadratic
  operation, modular/inactive units and charging-purchase/revenue/NPV quantities
  cannot become a complete total. Deployment stays opt-in.
- Comparison slots compute candidate-minus-baseline cost and percentage change
  against an immutable retained run on the same entity/window/basis. Zero baseline
  percentage denominators and incompatible bases are unavailable.
- Raw retained evidence is immutable, atomically persisted, recalculated on
  replay and owner/project authorized. Explicit retained IDs support historical
  replay after foreground model edits and process restart. Current-run records
  still recheck active source provenance and become unavailable after edits.
- `harness.effects` admits only an explicitly enabled, exact static-input
  `update_component` contract. It reserves a durable operation ID after ordinary
  confirmation and before dirty flags/undo/handler execution. Server read-back
  compares the entire input fingerprint against an independently prepared
  candidate. A handler's success JSON cannot establish completion. Completion
  refers to static settings; time-series values can override them. Completed
  retries return the checkpoint; live or uncertain operations never auto-repeat.
  Read-only reconciliation refuses live workers and records verified postconditions.
- `harness.navigation` issues short-lived owner/project/input/turn/generation
  bound actions. The browser ACKs only after the expected visible page/tab mounts.
  Stale, superseded, mismatched and unissued ACKs are refused. Submission remains
  submission until that independent browser acknowledgement arrives. Networkless
  studies navigate without inventing a network-revision ACK.
- `harness.reviewer` optionally checks at most two qualitative interpretations
  using one bounded, read-only provider call with no tools/history/evidence fetch.
  Deterministic validation runs first; a numeric-prose guard rejects digits,
  common English number words and units in favor of typed computed blocks.
  Reviewed interpretation is still a model judgment, not deterministic proof. Missing usage, malformed verdicts, tools, timeout or
  review failure withhold interpretations while preserving checked facts.
- `harness.publication` exports authenticated JSON or ZIP from the same approved
  answer and revalidated evidence. ZIP contains answer JSON, facts CSV and byte
  hashes/coverage in a manifest. Text cells resist spreadsheet formula execution.
  Existing PNG data/render/byte integrity remains separate.
- `harness.rollout` exposes capabilities/contracts, profile rollback, a separate
  effects switch and shadow observations. Shadow records bounded verdict/count/
  validation-latency metadata without storing drafts or changing normal text.
- The evidence card shows scope, current/retained identity, required slots,
  limitations and review state, with authenticated evidence/export links. The
  UI never treats a missing slot or submitted navigation as verified completion.

## Numerical meaning and boundaries

The independent real-LP fixture supplies 5 MW for 2 weighted hours and 7 MW for
1 hour: **17 MWh, 170 EUR marginal expenditure, 7 MW peak**. A 20 MW installed
capacity at 3 EUR/MW/year gives **60 EUR/year fixed charge**; an explicit annual
fraction of 1 produces **230 EUR declared-basis total**. A 260 EUR retained
baseline gives a **−30 EUR / approximately −11.54%** change. Different objective
weights alter expenditure without altering generator-weighted energy.

The independent storage LP fixture charges 5 MW for 2 storage-duration hours,
then discharges 2 MW for each of two hours. Charging/discharging efficiencies
0.8/0.5 give states **8, 4, 0 MWh** and grid-side totals **10 MWh charged,
4 MWh discharged, −6 MWh net discharged**. Generator/objective weights differ
intentionally. Hand-worked standing-loss and simultaneous-operation fixtures
check the recurrence and separate power columns. These are local storage
trajectory checks, not whole-network feasibility or independent certification.

These accounting profiles assume the provided `capital_cost` is an annual charge
and the declared EUR price year is consistent. They do not perform FX/price-year
conversion, calculate NPV or reproduce the solver objective. A matching solve
fingerprint and bounded physical checks are source/proxy checks, not independent
solver verification or a certificate of every engineering constraint.

Limits: 1–512 ordered unique datetime snapshots; no investment-period axis;
network scope at most 32 generators, 64 passive branches and 64 buses/loads;
20 requested metrics; 128 KiB per record; 512 records per owner; 24-hour public
retention. Owner-store transactions are serialized across processes. The active
project/network runtime still supports **one application worker**, not a distributed
project state. Unresolved operation reservations are not pruned or reused; after
public retention expires they require operator recovery outside this API.

Mounted-view ACKs prove visible page/tab mounting and server-side current inputs.
They do not independently prove every displayed cached result has the same data
revision. Model review uses the configured provider/model and is not independent
expert review. Known review usage is charged to shared counters; bounded calls
and concurrency do not create a durable billing reservation across restarts or
unknown late usage.

## Activation

See [versioned contracts and examples](harness-contracts.md). Deployment mode
still defaults to `disabled`. `strict` gates ordinary turns; `pilot` accepts an
explicit `assurance: {"version": 1, "mode": "strict"}`. `shadow` observes ordinary
answers without approving them. Profile rollback and the separately disabled
checked-write switch can narrow admission. Review defaults to disabled; requested
interpretations are withheld unless review is explicitly required and passes.
No production deployment setting is changed by this PR.

The browser exposes pilot opt-in through the existing per-request API; it does
not grant profile availability or enable writes/review. See the [activation and
rollback guide](checked-opt-in-runbook.md) for backend restart and allowlist steps.

## Still pending

Whole phase tickets stay open: broader storage/Stores/Links/multi-period economics
(including charging purchases, revenues and overnight/FOM derivations) and NPV;
adequacy, FMEA, GridSpine and other domain adapters; arbitrary natural-language
completeness/material clarification outside the bounded grammar and general
conversational/task constraint recovery;
more operation/job mappings and a distributed project runtime; displayed-data
revision proof and complete browser/device/accessibility journeys; checked
chart/report/import/general-export adapters; independent domain review, measured
calibration/load budgets and release enablement. Unmapped strict tools fail
closed. This implementation does not guarantee hallucination-free arbitrary
answers or all-application correctness.

## Checked pilot validation

At `38404196`, integrated master `baa3a95e` plus the pilot passes the selected
71-entry backend/provider/auth/history/recovery/run/experiment gate:
1,097 passed / seven skips. Fresh complete frontend passes 3,686 in 313 files;
38 new pilot/API cases include a genuine red/green round-trip rewind regression.
Native Chromium verifies keyboard opt-in, real fetch/SSE/digest publication,
withheld private text and rollback/draft/explicit-opt-out behavior using mocked
API responses. Backend numerical/auth cases separately exercise real handlers
and the independent 51-EUR LP oracle. Build, 229-tool inventory, two-file Ruff,
documentation/diff checks pass. The editing agent reviewed code and the
380-pixel layout; no independent domain approval is claimed.

Exact selections, source hashes, commands and qualifications are in
[checked-opt-in-validation.json](checked-opt-in-validation.json) and
[coverage ledger](coverage-ledger.md). Initial 1,045/7 backend and 3,678/312
frontend results are earlier overlapping checkpoints. No fresh complete backend,
paid model or remote CI result is claimed. Only docs/validation change after
the final gates. Activation is documented but production remains disabled.

## Historical StorageUnit economics validation

The shared backend/provider/auth/history/recovery/run-evidence gate passes
1,029 cases / six skips at `50499693`. The final 40 cost cases pass at `360b8cea`
after strengthening an assertion and correcting unavailable-cost coverage
metadata. Those 40 cases overlap the wider gate; the only subsequent Python
changes are the covered metadata/assertion changes. No fresh complete backend
run at the final head is claimed. Independent literal LP values, missing and
unsupported dependencies, fabrication, exact windows, signed prices, retained
comparisons/exports and restart clarification exercise public contracts.

The fresh complete frontend passes 3,641 cases in 310 files. Production build,
224-tool inventory, eight-file Ruff and documentation/diff checks pass. No paid
model calls or remote CI success. Exact selections and source qualifications are
in the [durable validation record](storage-economics-validation.json) and
[coverage ledger](coverage-ledger.md). QA/upstream and earlier complete backend
results below remain historical. Independent review, browser/device journeys,
calibration and release enablement remain open.

## Historical StorageUnit dispatch validation

The 40 storage cases use independent LP and hand-worked values, with current and
retained evidence, exports, restart recovery and model-fabrication refusals.
A generator named `network` retains its generator scope and header; a genuine
red public-stream case protects the type boundary. Contract draft inputs also
capture typed prices before deferred rendering; exact saved-price assertions
remain in the UI tests.

The complete backend baseline passes 11,515 cases / 273 skips. The final
54-file gate passes 912 / six skips; four added cases account for all 11,792
final collected nodes (11,519 passed / 273 skipped), without a fresh full run at
the last head. The fresh complete frontend passes 3,637 tests in 310 files.
All 31 QA drivers are accounted for after one startup-timeout rerun; 36 upstream
units, the final build, inventory, eight-file Ruff and documentation checks pass.
The [coverage ledger](coverage-ledger.md) records sources, prior failures and
exact qualifications. No paid model calls or remote CI success is claimed.

## Historical recovery validation

The recovery continuation includes 55 native history/control cases and nine
solver task-outcome cases. It integrates parent resume/rebind revocation and
requires a terminal solver outcome before a sequential task advances. Ordinary
foreground solves remain detached; fully asynchronous task-step recovery remains
open. The initial full backend run exposed an inherited detached-solver
regression, which was corrected without relaxing numerical assertions.

The final continuation integrates master `1970db27` / PR 122. Final collection
contains 11,751 backend cases in 551 files. The completed baseline partitions,
corrected remainder, added cases and latest affected-path gates account for that
collection: 11,478 passed and 273 skipped. This is multi-revision coverage, not a
fresh full run at the final head. The linked ledger records sources and exclusions.
The latest recovery/parent/task/progress/workflow/new-domain gate passes 869 cases
with six skips. A queued-correction scope regression fails before correction;
the post-fix admission/recovery/scope/task/frame gate passes 107 cases / one skip.
Two new cases prove safe repeats and refusal of added constraints without a second
model call or retention of stale authority. The handler-signature gate passes 176
cases; new tool-contract cases pass 32 / four paid-live skips.

All 31 post-correction QA drivers and 36 upstream unit tests previously passed.
The latest production build, inventory (224 tools), control matrix and
documentation checks pass. Across 15 touched Python files Ruff adds no findings
to master's 46 existing findings.
The first updated-master frontend run had one existing large-table test timeout;
the unchanged full rerun without concurrent backend jobs passes all 3,627 tests
in 310 files, exit 0, without unhandled errors. Earlier 3,623-test frontend
results are historical. After PR 122's subsequent merge, all 47 affected frontend
cases in six files and the production build pass; no fresh full frontend run at
the last head is claimed.

## Historical verification

Providers are fake or real adapters with HTTP mocks; HiGHS solves the independent
small numerical fixtures. No paid model calls. The current results and exact
commands are in [coverage ledger](coverage-ledger.md) and [handoff](session-handoff.md).
Historical PR 120 verification: its fresh complete backend run had **11,143
passed / 269 skipped / zero failures** across all **11,412 collected cases**.
Two disjoint whole-file subprocess selections both exit 0; exact counts, warnings,
commands and reproduction are in the linked ledger. The fresh complete frontend
has **3,610 passed / 307 files**, exit 0 and no uncaught errors. All **31 QA
drivers** and **36 upstream unit tests** pass; production build, documentation,
inventory and diff checks pass. The focused harness/transport/history/budget/frame
gate has **274 passed / one deliberate recording skip**; the final scope cases
have **30 passed**. These focused selections overlap the complete suites.

The earlier PR 119 run-plus-correction result remains recorded historically in the
ledger; those counts remain historical; the recovery continuation requires fresh validation. Skips are not claimed as
coverage. GitHub Actions dispatch returns HTTP 422 because Actions is disabled for
this repository; these are local locked-environment results, not remote CI checks.
