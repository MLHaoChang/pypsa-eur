# Independent domain review package

Status: **pending independent reviewer judgments and observed provider decisions**.
The editing assistant prepared these files; no human or independent expert
approval is claimed. No test-authored ballots are included.

- [packet.json](packet.json): ten blind qualitative interpretations across all
  five supported profiles, without expected labels or model predictions.
- [reviewer-a.json](reviewer-a.json) and [reviewer-b.json](reviewer-b.json): unresolved
  templates, with independence attestations false until completed truthfully.
- [numerical-oracles.json](numerical-oracles.json): eight declared-input scenarios,
  forty profile expectations. These are independent arithmetic fixtures, not
  reviewed engineering opinions or production sampling.
- [calibration.json](calibration.json): pending report with null rates and all ten
  labels/observations unresolved/missing.

Use the [calibration runbook](../calibration-runbook.md) to supply judgments and
measurements. Reviewers must check physical meaning and accounting assumptions,
not simply agree with the implementation's output. In particular:

1. Check generator energy weights separately from objective expenditure weights,
   changing marginal prices, fixed installed capacity and the explicit annual
   allocation. Determine whether selected profile names and public disclaimers
   match these accounting boundaries.
2. Check the two-generator network aggregate, 4 MW G1 supply cap, G2 dispatch,
   capacity charges and unsupported other-component/nonlinear costs.
3. Check StorageUnit grid-side charge/discharge, distinct duration weights,
   efficiencies, complete chronological predecessor states, capacity bounds and
   noncyclic operation. Stored energy must not become an adequacy certificate.
4. Check storage discharge expenditure, end-of-snapshot holding expenditure,
   declared fixed-charge allocation, and exclusion of charging electricity
   purchases, revenues and lifetime investment/discounting.
5. Check zero-valued results versus unavailable sources; inspect adversarial
   results and exact source/profile/units/publication behavior in the expanded
   validation record. Identify gaps or ambiguous accounting claims explicitly.

Implementation sources to examine are
[calculations.py](../../../pypsa-gui/backend/harness/calculations.py),
[storage_calculations.py](../../../pypsa-gui/backend/harness/storage_calculations.py),
[storage_costs.py](../../../pypsa-gui/backend/harness/storage_costs.py),
[assurance.py](../../../pypsa-gui/backend/harness/assurance.py), and
[the independent scenario oracle](../../../pypsa-gui/backend/harness/reliability_cases.py).
Record reviewer identity, expertise, evaluated commit/manifest, accepted scope,
findings and required corrections in a separate signed review. The offline tool
cannot authenticate or replace that signed engineering review.
