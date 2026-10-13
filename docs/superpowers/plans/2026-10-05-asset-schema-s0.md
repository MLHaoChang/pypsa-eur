# Plan: asset parameter schema, S0 (core)

**Date:** 2026-10-05. **Owner:** coordinating session. **Parent:** `2026-10-05-one-investment-engine-two-faces.md`
§10 (adopted). **Source:** `docs/superpowers/assessments/2026-10-05-uniform-asset-parameterisation.md` §3.
**Lands:** its own PR, merged by the owner before U2's PR.

## Goal

One backend description of how an asset's investment is typed, one derivation of `capital_cost` for composite
assets, and one accessor for the upfront cost. Enough for U2's `compile.py` (corrected C1) and the IC session's S0b.
The expert UI (S1), the guided ledger view (S2) and template seeds (S3) build on it later.

## Scope

In:

1. `services/asset_schema/schema.py`: per component class, its investment **parts**
   (`power`, `energy`, `rating`, ...), each with basis, stored unit, display unit and the custom columns that hold
   it. Single-part classes map onto PyPSA's own columns (`overnight_cost`, `lifetime`, `fom_cost`). The battery
   (StorageUnit) is composite: `power` (per MW) and `energy` (per MWh, scaled by `max_hours`).
2. `services/asset_schema/derive.py`: `derive_capital_cost(parts, max_hours, rate)` gives the annual
   `capital_cost = Σ part × annuity(rate, part lifetime)` and `fom_cost = Σ fom_share × part`, and the retiring
   `lifetime` (the longest part). It uses `periodized_costs._annuity`, the GUI's one annuity.
3. `services/asset_schema/access.py`: `upfront_parts(n, cls, name)` and `upfront_per_unit(n, cls)` return the parts
   (composite), PyPSA's typed `overnight_cost` (single part), or one back-calculated part flagged
   `derived_from_capital_cost`, or `None` (ADR-0001).
4. Write path: `StorageUnitCreate` declares the custom part columns; `network_crud._create_component` /
   `_update_component` call `derive` when a StorageUnit carries parts, and **leave PyPSA `overnight_cost` empty**
   (PyPSA ignores `capital_cost` when it is set).
5. Solve path: `fill_periodized_cost_defaults` re-derives composite `capital_cost` with the effective discount rate
   (asset override or global) before its horizon scaling, and reverts it with everything else.
6. Readers: `periodized_costs.upfront_cost_series` returns the parts' sum for composite rows (no more
   single-lifetime back-calculation of a two-annuity cost); the capex budget (`solver/objective.py`) takes the
   upfront cost from `upfront_per_unit`, so an annualised value is never used as upfront.

Out (later steps): frontend cards and labels (S1), guided ledger view (S2), template and Energy Hub seeds (S3),
other composite assets (HVDC per km plus converters) and Store + Link batteries (S4), per-field provenance in
`n.meta` (with S2).

## Tests first

- `annuity` equality: `_annuity` equals PyPSA's `annuity` to 1e-12 across a grid of rates and lifetimes.
- `derive`: the assessment's battery (r 7 %, inverter 213.9 EUR/kW 10 y, storage 189.9 EUR/kWh 25 y, 4 h) gives
  about 95.7 EUR/kW/yr, and changing duration, either part or the rate re-derives it.
- Write path: creating and updating a StorageUnit with parts stores the parts, leaves `overnight_cost` NaN, and
  sets `capital_cost`, `fom_cost` and `lifetime`; a netCDF save and load keeps the parts.
- Solve: on a one-bus fixture the LP's battery cost coefficient equals the two-annuity sum (not the blended single
  annuity C1 v1.2 would have produced), and a changed global discount rate changes it without an edit.
- Readers: `upfront_cost_series` returns power part + energy part × `max_hours` for the battery; the capex budget
  binds on the upfront cost.
- Unchanged: the existing golden economics, FOM reconciliation, capex parity and overnight-derivation suites pass
  untouched, because single-part assets are not rerouted.

## Done when

Every test above passes, the existing suites show no new failures against master, and the PR is open with the
facade (`asset_schema.access.upfront_parts`, `upfront_per_unit`, `derive.derive_capital_cost`, the part column
names) documented for U2 and S0b.
