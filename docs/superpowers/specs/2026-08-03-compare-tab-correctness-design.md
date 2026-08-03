# Compare: establishing that all ten result tabs calculate correctly

**Date:** 2026-08-03
**Status:** design approved, plan pending

## Why

`routers/compare.py` is ~2,700 lines computing ten result tabs, and almost
none of it is covered by a test that runs in a gate.

`tests/golden/coverage.py::SURFACES` lists ten surfaces, of which exactly two
are Compare tabs — `compare_economics` and `compare_capacity` — and both are
checked for CAPEX only. The remaining eight tabs (overview, dispatch, line
loading, prices, emissions, curtailment, lost load, storage cycling) have no
pytest-collected numeric coverage at all. Two QA scripts do exercise them
(`qa_phase4_compare.py`, `qa_results_summary_compare.py`) but `pytest.ini`'s
`python_files = test_*.py` excludes both from every gate, so nothing fails
when a number moves.

The gap is not theoretical. Three wrong-number defects have surfaced in
adjacent code within the last three days:

- `_safe_capital_cost` omitted the `nyears` factor — 365× on a unit-weighted
  fixture (findings doc, 2026-08-01, §4).
- Asset Detail `capex_annual` read the raw `capital_cost` column — 100% low
  for an `overnight_cost`-priced asset (§7).
- Asset Detail subtracted an annual CAPEX rate from horizon-summed revenue —
  net profit high by `(periods − 1) × annual CAPEX`, and a one-day window
  reported a 34.9 MEUR loss (fixed 2026-08-03, commit 922eb4d0).

All three are the same family: a quantity on one time basis combined with a
quantity on another. Compare computes on both bases throughout, so it is the
obvious next place to look.

## What "correct" means here

Three check families. Every tab gets 1 and 2; two tabs need 3.

### 1. Cross-surface agreement

Activate project A, read the live Results endpoint, read Compare's A-side,
require equality. This is the failure mode the user actually meets — two
screens showing different numbers for one network.

Structural note: Compare computes from each project's **last-saved netcdf**,
while Results computes from the **active in-memory network**. Any cross-check
must activate the project first, and a divergence may therefore be a
save/load fidelity problem rather than an arithmetic one. The tests must
distinguish those two causes rather than reporting "they disagree".

### 2. Structural invariants

No oracle needed, and the cheapest real evidence available.

- **A/A identity.** Comparing a project against itself must yield exactly
  zero deltas on every tab. Any non-zero value is a defect with no
  interpretation required. This covers all ten tabs for the cost of one test.

  **Where this check lives is not where it first appears to.**
  `GET /compare/{name}/results-summary` returns ONE project's summary;
  `CompareView.tsx` fetches A and B independently and diffs them client-side
  (`qA`/`qB` React Query pairs, one per tab). So no delta exists anywhere in
  the backend, and A/A identity is a FRONTEND property: feed the diffing code
  two identical payloads and every delta must be zero, every percentage
  change must be zero or suppressed, and no sign may be inverted. The backend
  gets the weaker sibling — determinism, i.e. the same project summarised
  twice yields an identical payload.
- **Per-period consistency.** For EXTENSIVE quantities, per-period values sum
  to the horizon total.
- **Internal identities.** LCOE × energy = capex + opex; per-carrier rows sum
  to the reported total; curtailed ≤ available; binding hours ≤ horizon hours.

**Extensive/intensive classification is part of the deliverable, not an
implementation detail.** An additivity sweep run against Asset Detail on
2026-08-03 flagged `co2_intensity`, `load_factor`, `energy_capacity` and
`bus_capacity_by_carrier` as non-additive — all four are rates or stocks and
were false positives. Storage cycling is the same case and is explicit about
it: `_compute_storage_cycling_summary` reports "All" as the AVERAGE of
per-period cycles, because cycles is intrinsically a per-year rate. A blanket
sum-check would manufacture a defect there. Each tab's metrics are classified
in the plan, and the classification is asserted, so a future metric added
without a classification fails rather than silently escaping the check.

### 3. Independent oracle

Only where 1 and 2 cannot discriminate:

- **No counterpart exists** — overview, storage cycling.
- **Compare and Results share a helper.** Agreement then proves nothing. This
  is not hypothetical: Asset Detail's `capex_annual` passed a cross-surface
  test for weeks because the test compared it against `asset_economics
  fixed_cost_eur / 15`, i.e. per-year against per-year, so the two
  `fixed_cost_eur` fields were never compared to each other.

## Harness

**Permanent tests** run against the golden multi-period fixture
(`tests/golden/fixture.py`): two periods with unequal weightings (5 and 10
years), solved with HiGHS. Where a tab needs an asset the fixture lacks to be
exercised non-trivially, the fixture is extended. A zero-valued asset passes
a broken formula and a correct one alike — the fixture's `gas` sits at 0 MW
optimal capacity and is useless for anything multiplied by capacity, which the
2026-08-01 findings doc records as a near-miss (a test targeting it would have
XPASSed under `strict=True` and failed the build for the wrong reason).

**Two levels, because the endpoint is not directly callable in a test.**
`get_results_summary` takes `AuthorizedProject = ProjectAccessDep` and reads
`project.directory / "network.nc"`, so it needs a real project — DB row plus
org-scoped storage — not just an installed network. Accordingly:

- **Numeric tests call the `_compute_*_summary` functions directly** on the
  golden network. Fast, no HTTP, no project on disk. This is the same choice
  the 2026-08-01 plan made for `_compute_economics_summary`, and for the same
  reason.
- **A small number of endpoint tests** go through `client` + the `api_project`
  fixture (which creates the DB row and org-scoped storage) to prove the
  wiring: that each tab's field is actually populated on the payload, and that
  the values arriving over HTTP are the ones the compute functions produced.
  Without these, a tab could compute correctly and never reach the response —
  the payload's own docstring says later phases "fill in additional optional
  fields", so an unpopulated optional field is a live failure mode.

**Spot-check pass** against the real `3_nodes_system` (3 periods × 8760, real
data, live app). Evidence, not a regression guard — it is user data and cannot
be committed as a fixture.

**Cleared during exploration**, recorded so they are not re-raised later as
findings:

- `prices_from_state` is correctly passed `False` for Compare bundles
  (`routers/compare.py:2700`), so a loaded comparison reads the loaded
  network's own duals and never the live network's cached snapshot.
- Storage cycling's unweighted `objective` basis is deliberate and documented
  in its own docstring, not an oversight.

## Per-tab matrix

Every row additionally gets backend determinism (same project twice → identical
payload) and, on the frontend side, the A/A identity check described above.

| Tab | Compute fn | Cross-surface oracle | Key invariants |
|---|---|---|---|
| Overview | inline in `get_results_summary` | — (oracle) | counts vs the netcdf; capacity agrees with the Capacity tab |
| Capacity | `_compute_capacity_summary` | `/statistics`, `/generators`, `/api/simulation/asset_costs` | additivity; Σ per-carrier = total |
| Dispatch | `_compute_dispatch_summary` | `/generators`, `/carrier_kpis` | additivity; energy on the `generators` basis |
| Line loading | `_compute_loading_summary` | `/lines`, `/line_duals` | binding hours ≤ horizon hours |
| Prices | `_compute_prices_summary` | `/prices`, `/price_drivers` | duration curve monotone; percentiles inside [min, max] |
| Emissions | `_compute_emissions_summary` | `/emissions`, `/carrier_kpis` | Σ per-carrier = total; intensity is INTENSIVE |
| Economics | `_compute_economics_summary` | `/asset_economics`, `/economics_by_carrier`, `/cost_breakdown` | LCOE × energy = capex + opex; additivity |
| Curtailment | `_compute_curtailment_summary` | `/curtailment` | curtailed ≤ available; rate is INTENSIVE |
| Lost load | `_compute_lost_load_summary` | `/lost_load` | VOLL cost = energy × VOLL; additivity |
| Storage cycling | `_compute_storage_cycling_summary` | — (oracle) | "All" = AVERAGE of periods, never the sum |

## Suspects carried into the plan

Hypotheses from a code read. Each is verified or dismissed with a measurement
before any fix is written; none is treated as a defect on the strength of
reading alone.

**S1 — Capacity and Economics disagree on CAPEX by construction.**
`_compute_total_annuitised_capex` walks Generator, StorageUnit and Store.
`_compute_economics_summary` walks those plus Link. So the Capacity tab omits
link CAPEX that the Economics tab counts; on `3_nodes_system` that is
Electrolyzer 1 (≈18.1 M€/yr) and P2H 2 (≈0.64 M€/yr). The omission is
deliberate and commented — "passive branches with no extension don't
contribute to the LP objective" — which holds for a fixed line and does not
hold for an extendable link, and both links in that project are extendable.
Resolution may be to include extendable links, or to keep the omission and
state it in the UI; that is a product call and is escalated, not decided here.

**S2 — Line loading and Prices take their hours basis from the cost column.**
Both call `_build_snapshot_weights(n)`, which defaults to `objective`, and
then use the result for binding-hour counts and the price duration curve's
hours axis. The helper's own docstring assigns hours/energy quantities to
`generators`. The two columns are equal on an ordinary hourly year, so this
is invisible there and diverges on representative-week runs — a real
configuration in this GUI (`sample_representative_weeks`) whose weights,
per CLAUDE.md, reset to 1.0 when a project is promoted to multi-period.
Requires a fixture in which the two columns genuinely differ; without one the
test passes vacuously.

**S3 — Economics documents capex as €/yr while LCOE divides horizon energy.**
`_compute_economics_summary`'s docstring says `capex (€/yr)` and
`LCOE = (Σ capex + Σ opex) / Σ dispatch_MWh`. The `_safe_capital_cost` path
was corrected in the 2026-08-01 plan's Task 9, but that task verified CAPEX,
not the quotient, so the time basis of LCOE itself is unverified. Identical in
shape to commit 922eb4d0.

## Frontend scope

Larger than it first looks, because **the comparison itself is computed here.**
The backend never subtracts B from A; `CompareView.tsx` does, per tab, from two
independently fetched payloads. Every difference the user reads is frontend
arithmetic on backend inputs, so a backend-only examination would verify the
inputs to the feature and none of its output.

In scope:

- **A/A identity** — identical payloads in, zero deltas out, for all ten tabs
- delta and percentage-change computation, including sign convention
  consistency across tabs (does "green" mean A>B on every tab?)
- divide-by-zero and null guards when a baseline is 0 or absent, and the
  distinction between "0" and "not reported" (a tab whose optional payload
  field is absent must not read as a 100% reduction)
- unit conversions and labels against the units the backend declares
  (`fixed_cost_eur` shipped as `unit="EUR/a"` while holding a horizon total
  until 2026-08-03; the label was the bug)
- period selection: the per-period view must read the same period's value from
  both A and B, and must not fall back to a horizon total for one side

Rendering, layout and interaction are out of scope.

## Fix protocol

Per confirmed defect, in this order: failing test first, root cause
established, fix, full suite (`pixi run gui-tests`), its own commit. Defects
are not batched into one commit — the 2026-08-01 plan's per-defect commits are
the precedent, and they are what made today's history readable.

Escalate rather than decide, when "correct" is a product question rather than
an arithmetic one. S1 is the known example.

## Execution order

1. A/A identity across all ten tabs (frontend), plus backend determinism.
   Cheapest, needs no oracle, and any failure is unambiguous.
2. Invariants per tab, with the extensive/intensive classification asserted.
3. Cross-surface per tab, working down the matrix.
4. Oracle for overview and storage cycling.
5. Remaining frontend derived values — signs, guards, units, period selection.
6. Endpoint wiring tests via `client` + `api_project`.
7. Real-project spot check.

Steps 1–4 are the bulk. If the pass has to be cut short, 1 and 2 alone
establish more than exists today, since they need no counterpart endpoint and
cover all ten tabs.

## Deliverables

- Findings document in `docs/superpowers/findings/`, one section per tab,
  every claim marked MEASURED or INFERRED, and agreements recorded explicitly
  so "no complaint" is never mistaken for "not checked".
- Permanent pytest coverage for all ten tabs, added to
  `tests/golden/coverage.py::SURFACES` so a future eleventh tab cannot be
  added without the guard noticing.
- The fixes.

## Out of scope

- Rewriting `routers/compare.py` for size or structure. It is 2,700 lines and
  that is worth addressing, but not while also changing its numbers.
- The `compare_economics.per_asset_lcoh` Link-only coverage claim, deferred
  by the 2026-08-01 plan as a documentation inaccuracy rather than a wrong
  number. Unchanged here.
- Promoting `qa_phase4_compare.py` / `qa_results_summary_compare.py` into the
  gate. New tests are written against the golden fixture; what to do with the
  QA scripts afterwards is a separate decision.
