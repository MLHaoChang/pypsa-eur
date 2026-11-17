<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Metric semantics, deterministic calculations and raw validity

Status: ready-for-agent
Model: Opus
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

## Follow-up: checked plausibility findings

Named by GK B's amendment on #851 (2026-10-28): Codex K ships the plausibility rules native and read-only, and checked publication of its findings is deferred here. Owned by the Assurance track, gated by GK B.

**Checked plausibility findings: a bounded plausibility evidence/profile adapter.** Acceptance:
- only `get_results(result_kind="plausibility")` is admitted; no blanket `get_results` whitelist, and the general guard on ordinary results reads stays closed;
- a valid finding is published as a positive control;
- LC-T19 injected extra claims are withheld;
- the stored record is MAC'd like #850;
- lookup re-derives the findings from the recorded result.

Study-harness issue 05's LC-T19 line depends on this item.

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

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: `pypsa-gui/backend/harness/calculations.py` (extend beyond generator/network/storage profiles), result-service validity masks, hand-worked fixtures under `tests/`. Blockers: 01 and 02 are done. Reviewer: GK B (finance metrics such as NPV also GK B). Shares metric definitions with 09. Plan OK from GK B first.

### 2026-11-17 — S4: A4.6 metric parity and metamorphic fixtures (tests only)

Branch `claude/aa-10-s4-metric-parity`, plan GK B GO. `tests/assurance_acceptance/test_p4_metric_parity.py` checks five profiles on one hand network (3 snapshots, weights 1, 0.5, 2): checked answer = `/results/*` route = results-workbook cell = hand arithmetic, on value, unit, window and unavailable reasons; doubled weightings, reordered/renamed components and a window split; an injected +1 on each surface's service fails the parity check (no surface has a runtime cross-check, so no refusal is claimed). `tests/test_price_assurance.py` holds one hand case per bus-price/1 guard; `scripts/coord/mutants.py` on `calculate` (all lines, the checkout being shallow): 34 mutants, 34 killed after one added case. No production code changed.

| Case | Status | Note |
|------|--------|------|
| AA10-S4-1 bus-price/1 min_price, max_price | `_covered` | `/results/prices` series and the Prices sheet's Min/Max price cells |
| AA10-S4-2 bus-price/1 mean_price on the workbook | `_pending` (ticket 22) | the sheet's "Mean price" is the plain mean (40/3), the checked mean is weighted (65/3.5); only the unit is asserted |
| AA10-S4-3 bus-price/1 hours_at_voll | `_pending` | no route or workbook figure to compare; checked against hand arithmetic only |
| AA10-S4-4 carrier-energy/1 carrier_generator_energy | `_covered` | `/results/carrier_kpis` energy_mwh and the Dispatch sheet's "Generation — gas" |
| AA10-S4-5 carrier-energy/1 peak and StorageUnit energies | `_pending` | no route or workbook figure per carrier; hand and metamorphic checks only |
| AA10-S4-6 curtailment/1 curtailed and available energy, share | `_covered` | `/results/curtailment/by_cause`, `/results/carrier_kpis` curtailment_pct, the Curtailment sheet (no share cell) |
| AA10-S4-7 network-emissions/1 total_emissions, static efficiency | `_covered` | `/results/emissions` total_tCO2 and the Emissions sheet's "Total emissions"; no_co2_cap ↔ route cap inactive |
| AA10-S4-8 network-emissions/1 with a time-varying efficiency | `_pending` (ticket 23) | route and sheet divide by the static efficiency (37.5), the checked answer and the LP by the per-snapshot one (57.5) |
| AA10-S4-9 network-emissions/1 co2_cap_shadow_price on the workbook | `_pending` | the Emissions sheet has no shadow-price cell |
| AA10-S4-10 lost-load/1 energy, cost, shed hours, peak | `_covered` | `/results/lost_load` and the Lost load sheet (peak from the route series only) |

Filed from the plan review, not found by these tests: ticket 21 (adequacy total system cost reads 0 for a non-finite objective).
