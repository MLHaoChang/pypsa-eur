<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Reliability contracts in the shared harness

Current continuation: `feat/harness-durable-conversation-scope`, based on master
`bd761721` (PR 118, including PR 120 and earlier assurance additions). The user's instruction authorizes implementing these additions.
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

## Still pending

Whole phase tickets stay open: storage/Links/multi-period economics and NPV;
adequacy, FMEA, GridSpine and other domain adapters; arbitrary natural-language
completeness/material clarification outside the bounded grammar and general
conversational/task constraint recovery;
more operation/job mappings and a distributed project runtime; displayed-data
revision proof and complete browser/device/accessibility journeys; checked
chart/report/import/general-export adapters; independent domain review, measured
calibration/load budgets and release enablement. Unmapped strict tools fail
closed. This implementation does not guarantee hallucination-free arbitrary
answers or all-application correctness.

## Current recovery validation

The recovery continuation includes 53 native history/control cases and nine
solver task-outcome cases. It integrates parent resume/rebind revocation and
requires a terminal solver outcome before a sequential task advances. Ordinary
foreground solves remain detached; fully asynchronous task-step recovery remains
open. The initial full backend run exposed an inherited detached-solver
regression, which was corrected without relaxing numerical assertions.

The integrated recovery gate passes 474 cases with one recording skip; the
provider/task/native-file/progress gate passes 58 cases. Complete frontend
validation passes 3,623 tests in 308 files. Build, upstream, inventory, control
matrix and documentation checks pass. Final complete backend and post-correction
QA validation are running; their final counts remain pending in the linked ledger.

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
