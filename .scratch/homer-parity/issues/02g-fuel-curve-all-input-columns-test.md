# 02g — Test a generator with every fuel-curve input column at once

Status: needs-triage

Follow-up from homer-parity 02b (#1506), from its mutant run.

## Problem

`services/fuel_curve.py` gates `apply` and `lp_refusals` on `any(c in gens.columns for c in INPUT_COLUMNS)` (lines 314 and 347 on #1506's head). The mutants `c in` → `c not in` survive on both lines: no test builds a network whose generators table carries every input column, so `not in` is still true for some missing column and the gate never closes when it should stay open.

## Wanted

- One test with a Generator that sets every column of `INPUT_COLUMNS` (the curve columns, the O&M per run hour and the lifetime in run hours), asserting `apply` stamps the derived costs and `lp_refusals` reads the row.
- Re-run `scripts/coord/mutants.py pypsa-gui/backend/services/fuel_curve.py` on the test; both survivors are killed.

## Acceptance

- The two mutants above are killed; no other test changes.
