<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Metric semantics, deterministic calculations and raw validity

Status: ready-for-agent; audit 2026-11-19: losses validity (#1644) and solve validity (#1680) merged; S4/S5/S6a/S7 open in #1704, #1705, #1712, #1707; left besides: NPV and other-domain metrics, plausibility adapter
Model: Opus
Progress: harness/calculations.py with generator, network and storage profiles shipped; other domains, NPV and validity checks pending
Type: task
Blocked by: none (reliability contract review done, GK B OK @d36109ae, AA-A04-REVIEW-1; 01 and 02 done)

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

### 2026-11-17: GK B plan GO, slices S1–S7 (builder session_01G7qCujEGst2EoC67pAmmXd)

GK B (64) gave GO on the 09+10 plan at master 0ab07339. Slices, one PR each: S1 losses raw validity (10, A1.7); S2 solve-status/MIP-gap guard; S3 objective-to-cost bridge profile and an objective-vs-cost clarification; S4 cross-surface parity and metamorphic fixtures; S5 per-metric mapping ledger, with S5b oracle batches; S6 bounded sections above 20 slots; S7 definition hash in evidence and the validation-cache key. #1113's GK B OK @d36109ae is the section-1 contract review (AA-A04-REVIEW-1); independent_domain_approval stays false. Checked plausibility findings wait for Codex K (#851).

Conditions:
- G1: existing Losses fields byte-identical; `validity` is additive. Loads and branches are both classified; real 0, NaN, ±inf and absent are kept apart, pinned by exact equality.
- G2: name the persisted status/gap source. A missing status is limited, never optimal. A pure LP has no gap; MIP-ness comes from the network. One tolerance constant, with a boundary test. Report the flip count before building.
- G3: a public bridge wrapper; objective-scale reversal tested under auto and user scale ≠ 1; a residual tolerance constant with a boundary test; a penalty that can't be identified is `unavailable`, never 0; clarification choices pinned by exact list equality.
- G4: the wrong service aggregate is injected by monkeypatch, and the refusal kind is asserted exactly.
- G5: exact set equality between profile metrics and ledger rows, so both missing and orphan rows fail; a pending row has `oracle: null` and is never counted as verified.
- G6: deterministic server-owned sections, each slot in exactly one; a contract change is re-reviewed under AA-A04-REVIEW-1.
- G7: new kinds go in `tool-error-kinds.d/`; inventory, route-parity and control-matrix checks run at the trial merge; calculations.py registration only after asking the coordinator.
- G8: one targeted mutant per guard, with its killing test listed in the PR body.
- G9: Journey fit line per PR; UX leads S3/S6.
- S7 ruling: the sealed chart keeps verifying as drawn. The definition hash goes in the evidence record and the cache key; one test shows a fresh answer misses the cache after a definition change, another that the sealed chart reports the old hash.

### 2026-11-17: GK B S2 ruling (gap-aware limits)

Option (a); no owner question, since only deltas and prices on MIP runs flip. G2a: the effective gap is the one solver_service passed for that solve and solver (committable → configured mip_gap under that solver's option name). Any other path (modular-only, an unmapped solver, a missing field) reads `solve_gap_not_recorded`, never a solver default. G2b: MIP-ness comes from the network (committable Generator/Link; extendable with *_nom_mod > 0), tested per component; a non-extendable unit with a mod value is not integer. G2c: the band is |Δ| ≤ gap_a·|obj_a| + gap_b·|obj_b| (an LP counts 0) on the user-unit objective, and it limits only total-cost/objective deltas and least-cost wording. Boundary tests sit at the band and one ULP above; SOLVE_GAP_TOLERANCE = 1e-6. G2d: fixed-commitment duals are answered as limited ("marginal given the fixed commitment"); absent duals stay unavailable; a small committable test shows which applies. G2e: the gap disclosure is additive and facts stay byte-identical. G2f: `unsupported_stochastic_run` is detected from the network's scenarios.

### 2026-11-17: S1b, the harness reader for losses validity (named slice, GK B #1644 ask 3)

S1 (#1644) adds `services/results/losses.py:_validity_rows` (per entity, aligned to `n.snapshots`: finite, zero, missing, ±inf, absent, misaligned). The `/results/losses` response carries only a bounded per-component summary, so per-entity rows stay off the chat `get_results` path until a reader exists. Nothing in the harness reads it yet.

S1b adds that reader: a checked losses profile (`branch-losses/1`) built on `_validity_rows`. A1.7 acceptance for S1b:
- a branch or load whose state is `incomplete`, `absent` or `misaligned` never yields a loss or demand fact; its slot is unavailable with the state as the reason;
- a real zero is answered as 0 MWh, a missing value never is;
- a short or zero-row frame never certifies;
- `loss_pct_of_demand` needs every load complete; the denominator guard is the existing near-zero rule;
- an independent hand-worked LP-with-losses oracle and the S5 ledger rows.
