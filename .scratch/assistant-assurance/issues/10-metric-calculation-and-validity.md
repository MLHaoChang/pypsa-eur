<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Metric semantics, deterministic calculations and raw validity

Status: needs-triage
Progress: harness/calculations.py with generator, network and storage profiles shipped; other domains, NPV and validity checks pending
Type: task
Blocked by: reliability contract review; 01 for computed schema; 02 for raw evidence

## Scope

Implement [reliability contract](../reliability-contract.md) sections 2–3 across
P0/P1/P2/P4/P7. Add versioned metric/formula definitions over existing services,
bounded computed dependencies and server rendering. Preserve raw missing/validity
masks before legacy sanitization. Validate convergence/coverage and domain-specific
physical/accounting checks; maintain input assumptions and uncertainty labels.
Do not confuse source agreement with independent calculation validation.

## Test-first acceptance

Required groups: **A1.7, A2.9, A2.10, A4.6, A7.7** in [test matrix](../test-plan.md).
Use hand-worked raw inputs and independent arithmetic: snapshot-weighted energy,
multi-period duplicate indices, objective/cost bridge, signed deltas/denominators,
complete-population predicates and non-converged/missing versus zero. Inject
plausible erroneous service results and check invariants/metric identity. Test
same semantics in answers, charts/tables and reports. Never derive expected values
by calling the service under test or copying its response.

## Completion and handoff

Done only with reviewed per-metric validity/formula/tolerance/fixture mappings,
required green public-seam cases and relevant result/harness regressions. Domain
rules must be independently reviewed for each released metric. Record pending
metrics explicitly in [ledger](../coverage-ledger.md) and [handoff](../session-handoff.md).

## Comments

Initial source targets: `services/results/losses.py` zero-filling and denominator
handling; `routers/results.py` cost/objective definitions; report value-only audit.
This is a planning ticket, not a claim those application paths have been fixed.

Implementation continuation (2026-10-10): The generator-dispatch/1 candidate now independently calculates energy, marginal expenditure and peak output from raw per-metric masks, capacity bounds and matching optimal-LP provenance. Full cost accounting, the proposed 230 EUR same-basis fixture, cross-domain/report parity and independent domain review remain pending. See [implementation status](../implementation-status.md). This is editing-agent prototype coverage, not inherited independent approval or whole-ticket completion.

### 2026-10-11 — shared harness implementation

The shared calculation registry/DAG supports generator dispatch/cost and bounded linear network cost, raw masks/solve-input provenance, nodal power balance and passive-branch limits, plus signed retained-baseline deltas. Independent real-LP oracles cover 17 MWh, 170 EUR marginal cost, 7 MW peak, explicit 60 EUR allocation/230 EUR total and 260 EUR comparison. Annual capital charges and EUR price year are declared assumptions, not verified conversion, NPV, objective parity or independent scientific certification. Storage/Links/multi-period and other engineering domains remain pending.

See [implementation status](../implementation-status.md), [contracts](../harness-contracts.md) and [coverage ledger](../coverage-ledger.md). The whole ticket remains open; no independent review verdict is inherited by this extension.

### 2026-10-11 — bounded StorageUnit dispatch continuation

The shared harness adds `storage-unit-dispatch/1` with six checked metrics,
whole-message selection, exact windows and predecessor-state validity. Raw split
power, storage durations, bounds and chronological efficiency/loss balance are
validated before publication. Unsupported storage costs remain unavailable;
cyclic units, nonzero inflow/spill, Stores and investment periods are refused.
The independent LP oracle gives 10/4/−6 MWh grid-side energy and states 8/4/0 MWh.
Evidence/replay/export/recovery use existing owned contracts. The whole issue
remains open; broader storage economics and independent domain approval remain
pending. See [implementation status](../implementation-status.md) and
[coverage ledger](../coverage-ledger.md).


### Bounded StorageUnit economics continuation (2026-10-11)

The user authorized the next implementation scope. `storage-unit-cost/1`
adds direct objective-weighted discharge/holding expenditure and declared
installed-power fixed charges, with an independent 51-EUR LP oracle. Missing or
unsupported dependencies cannot become complete totals; unavailable costs no
longer borrow physical trajectory counts as cost-data coverage. Shared retained
comparison, restart clarification, publication and export preserve exact facts.

The shared gate passes 1,029 / six skips; the final overlapping 40 cost cases
pass after the metadata correction. Fresh frontend passes 3,641 / 310 files;
build, inventory and style/documentation gates pass. Exact source qualifications
and selections are in [validation](../storage-economics-validation.json) and
[coverage ledger](../coverage-ledger.md). No paid model calls or independent
domain approval. This whole issue stays open for broader storage economics,
other domain adapters, arbitrary-language completeness and calibration/release.
