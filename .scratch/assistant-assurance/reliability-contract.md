<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Additional harness reliability requirements

Status: proposed revision after source audit; unimplemented; independent review
pending. Read with the [spec](spec.md), [protocol baseline](protocol-v1.md),
[test matrix](test-plan.md) and [assessment](../../docs/superpowers/assessments/2026-10-10-assistant-assurance-reliability.md).
This document specifies required behavior. New observable fields/block shapes
must pass P0 contract review before acceptance fixtures or implementation use them;
the previously frozen v1 examples are historical baseline contracts.

## 1. Check the requested task as well as each individual fact

A true statement about the wrong run, period or quantity is an incorrect answer.
At turn admission, build a server-owned request scope from the authenticated
project/binding, the user's current message, explicit prior selections and typed
UI selections. Record requested operations, required result slots, selected runs,
entities/window/period, constraints and authorized effects. A model's proposed
intent remains untrusted: resolve identifiers through services, reject unsupported
operations and retain the original user request for audit. Do not treat client UI
context as authority for project ownership or permissions.

For a supported request, each required slot ends as answered with evidence,
unavailable with a reason, or needs clarification. The answer checker enforces
those dispositions and the requested filters. Do not silently substitute a
nearby metric, another entity, an old run, a default period or a different study
to fill a slot. Ambiguity that changes the calculation or effect requires a
targeted choice; an explicit application default may resolve a read when stated
in the answer. Low-confidence free-text parsing is never an authorization grant.
Unknown requests return a scoped clarification or supported limitation.

Each domain has a versioned key-results profile listing required metrics,
definitions, sources, units, validity rules and disclosures. A generic "give me
the key results" uses the selected domain/profile, states its project/run/period,
and includes every required slot or its unavailable disposition. Extra available
metrics cannot hide a failed required result. The profile covers both useful
results and warnings; ranking only successful rows is not a complete summary.
Do not require every catalogue metric in every answer. A focused request uses a
focused profile. More than the 20-block answer limit requires bounded sections
or an approved table artifact with a scoped summary, never silent omission or
an increased unbounded draft budget.

`passed` still means mechanical checks passed. Required omissions make the
whole answer `limited`; slot-level checks stay visible. A user clarification
has no checked result badge. No model or parser can certify that arbitrary
natural language has been understood perfectly.

## 2. Metric definitions and deterministic calculations

Create a shared versioned metric registry over existing result services. An
entry names domain/metric ID, definition/version, source field and raw inputs,
dimensions/unit, sign convention, aggregation, index join, time/period/currency
basis, precision and validity prerequisites. Presentation names cannot alias
semantically different metrics. Formula and renderer versions enter evidence,
answer receipts and validation-cache keys.

Define a bounded computed-block schema in P0/P2: registered formula ID/version,
named operand references to exact evidence fields or earlier computed blocks,
explicit requested scope and an allowed display unit/precision. A model-provided
result is a consistency assertion only; software recomputes and renders it.
Dependencies form an acyclic graph of at most 20 fact/computed blocks per answer;
reject duplicate/forward/cyclic/unresolved refs, incompatible scopes, non-finite
values and formulas outside the registry. No arbitrary expressions, Python,
SQL, browser scripts or evaluation of uploaded formulas. Bounded aggregates
operate on the captured complete dataset in software, not model-visible pages.

Domain semantics must be explicit:

- Energy is power times the registered snapshot duration/energy weights;
  objective weights and investment-period objective weights are distinct from
  physical-energy/year weights. Align period plus timestamp and entity IDs;
  reject duplicate/missing indices and implicit joins across unmatched cases.
- Cost distinguishes solver objective, objective constant/penalties, total
  system cost, annual/horizon totals, discounted values and currency/price year.
  Do not rename `status.objective` to total cost. Reconcile the documented
  objective-to-cost bridge; an unexplained residual is a limitation.
- Capacity distinguishes configured, installed/optimized and available/derated
  values; demand distinguishes requested, served and unserved; price distinguishes
  a solver dual from a tariff. Units alone cannot resolve those meanings.
- A delta names baseline and candidate; percentage change names its denominator;
  percentage-point change is a separate metric. Zero or near-zero denominators
  yield a typed undefined result under a metric-specific rule, never a guessed
  zero/infinity. Rounding happens after validation and aggregation.
- Rates, shares, maxima and top-k rankings require the declared full population,
  exclusions and tie policy. "All passed", "none failed", "safe" and "optimal"
  are factual predicates requiring complete relevant checks, not free prose.
- Stochastic study results carry seed/sample count, method, uncertainty and
  scope. A confidence interval is not certainty of future performance; preserve
  the service's assumptions and statistical validity conditions.

Freeze each released metric's formula and tolerance in a reviewed worked fixture.
Compare raw values with a metric-owned tolerance; displayed decimal precision
cannot enlarge the acceptance tolerance. Report precision/rounding separately.
Input literals are recorded assumptions. A standalone calculation over confirmed
user values is labelled computed from supplied inputs, never a solved result.

### Worked key-results acceptance tracer

For a single selected period, independently specify dispatch [5, 7] MW and
energy-duration weights [2, 1] h for G1. Full valid energy is
`5 × 2 + 7 × 1 = 17 MWh`; configured `p_nom = 20 MW` remains a capacity input.
With a constant 10 EUR/MWh marginal cost and the explicitly same objective
weights, operating cost is 170 EUR on that horizon. A documented 60 EUR fixed
cost allocation yields 230 EUR total on the same basis; the domain fixture must
independently configure/reconcile which terms the solver objective contains.
The model cannot relabel the 170 EUR operating cost/objective as 230 EUR total.

If the second interval is non-converged, the full-horizon energy is unavailable.
An explicitly permitted valid-subset summary is 10 MWh over the first interval,
with the second excluded and its coverage warning. Returning 10 MWh as the full
horizon or returning 0 for the missing interval fails. If a comparison baseline
on the identical basis is 260 EUR, candidate-minus-baseline is -30 EUR and
percentage change is about -11.54%, with 260 EUR as the denominator. These values
are test oracles specified independently of the service or model response.

## 3. Matching a service response does not establish calculation correctness

Adapters must capture the original typed values and validity/missing masks before
serialization or legacy UI cleanup. Preserve reasons such as non-converged,
not evaluated, absent source, inactive/not applicable and actual measured zero.
Where raw source/masks are unavailable, block the affected metric or mark it
unavailable; the sanitized response cannot earn a checked calculation label.

Use per-domain validation rules: solver termination and feasibility/convergence,
requested-versus-evaluated coverage, finite values, documented physical/accounting
invariants and bounded residuals. Checks run against the exact selected mode,
run and window. A queued job finishing is not proof of numerical validity;
an optimization returning a feasible incumbent with a gap is not a proven optimum.
Invalid intervals may yield a labelled partial result only with exact excluded
indices/counts and an explicit denominator. Do not extrapolate it to the horizon.

The fixture oracle must be independent of the implementation under test. Calling
the same service twice, copying its output to expected values, or hashing that
output checks consistency only. Use small hand-worked networks, explicit raw
inputs, independent arithmetic and reviewed solver/domain expectations. Include
metamorphic checks where valid: unit conversion preserves the quantity, entity
permutation preserves totals, disjoint windows reconcile, identical cases have
zero deltas and admissible cost bridges reconcile within documented tolerances.
Inject a plausible wrong value/status/unit into an otherwise successful response;
demonstrate that raw-source/invariant checks prevent its publication.

This establishes tested calculation behavior and evidence identity. It does not
prove the physical model, external inputs or every possible engineering result.
Interpretation reviewers cannot replace these numerical checks.

## 4. Bound actions before execution and verify effects afterward

For every effectful tool, including exports with legacy read tiers, map a
required precondition and postcondition adapter. Admission validates full tool
arguments against the offered schema, actual domain types/ranges/units, request
scope, project/run/revision, eligibility and authorized effect bounds. Existing
confirmations remain authoritative; the confirmation binds the exact normalized
arguments or preview fingerprint, not a changed payload. Uploaded documents,
annotations, result text and reviewer output cannot extend the user's authority.

The current courtesy `PRE_DISPATCH_VALIDATORS` stay advisory. Add a distinct
required guard path: missing/raising mandatory guards block the effect, with a
typed software error and no handler call. Authorization, confirmation, locks and
domain constraints remain monotonic; one approval cannot override another denial.
Revalidate after waits and under the existing commit lock. Detect no-progress
loops and repeated failed/conflicting calls; stop at the shared attempt/time/
resource budget and request a scoped correction. Valid authorized changes may
advance a binding through a server-recorded transition; stale callbacks may not.

After execution, compare requested fields/effects against actual saved or
foreground state as appropriate. Bind the receipt to request/tool ID, exact
arguments, before/after revisions, committed paths, retained artifacts/job IDs
and verification result. A return value claiming success is insufficient.
Writes beyond the authorized changed-path set, partial bulk changes and failed
read-back produce partial/uncertain outcomes; never say complete. Do not issue
automatic compensating edits/undo without the ordinary authorization gates.

Reserve an operation ID before effects using existing task/job idempotency
mechanisms; extend those mechanisms to untasked effects rather than create a
second task store. Same operation ID with different arguments is a conflict.
User-requested repeats are new operations. An expired timeout worker can still
finish: quarantine dependent claims and reconcile real state before any retry.
Resolve/recovery tools require independent effect evidence; model-supplied
completion text or result JSON alone cannot certify completion.

## 5. Apply the gate to every surface and measure its failures

The capability matrix maps every tool/operation plus required UI controls to a
guard, evidence/metric adapter, validity checker, postcondition or explicit
read-only designation, key-results profile, renderer and executable fixtures.
Each row names its schema/version, owner and release status. Unsupported rows
fail closed in strict assistant publication and remain declared limitations.
A real journey per family is a minimum supplement to these rows. It does not
certify every metric or operation in that family. CI fails on new or changed
tools, parameters, metrics or output surfaces without updated mappings/tests.

Public surfaces include streaming text, table cells, tool cards, chart titles,
captions, evidence cards, report previews/PDFs, download names/metadata, job
notifications, workflow/task completion, history/compaction, exports and speech.
Each consumer checks an owned approved record with matching generation/content;
HTTP success, completion events, stale caches and legacy renderers grant no bypass.
Default reference text/courtesy/progress is software authored; templated factual
status still needs its exact receipt. A policy failure cannot be overridden by
provider fallback, toolset switching, a reviewer or a performance target.

Record request-slot fulfillment, wrong-source/unit/value rejections, missing-mask
failures, invalid-result leakage, unauthorized effect attempts, unverified
completion claims, false acceptance/rejection, abstentions and fallback rate.
Report denominator, supported family/model/policy versions and fixture source.
Evaluate usefulness alongside rejection so an always-unavailable bot cannot pass
release: every supported valid golden request must return its required results.
CI requires zero unchecked publication/effects and zero false accepts on mandatory
deterministic adversarial fixtures; this is a finite-suite gate, not a population
error guarantee. Qualitative/live error estimates require independent labels,
sample counts, uncertainty and explicit pending targets before quality claims.
Confirmed leakage or invalid completion pauses the affected adapter's checked
publication, preserves authorized ordinary app access, and adds a regression
fixture before reenablement. Diagnostic storage stays bounded and redacted.

## Required implementation order

P0 freezes request/metric/computed/guard contracts and maps the complete inventory.
P1 preserves raw validity and supplies one trustworthy source. P2 implements a
request-scoped key-results tracer, deterministic arithmetic and approved rendering.
P3 binds effects to guards/postconditions. P4–P6 propagate the same contracts to
artifacts, views, reports, notifications, history and voice. P7 requires complete
supported-row evidence and calibrated usefulness/error reporting. See tickets
[09](issues/09-request-scope-and-key-results.md),
[10](issues/10-metric-calculation-and-validity.md) and
[11](issues/11-required-guards-and-effect-verification.md).
