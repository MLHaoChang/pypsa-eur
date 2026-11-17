# A part lifetime that overflows the annuity escapes as an OverflowError

Status: needs-triage
Type: bug
Found in: TCU 11 (#1628), GK B (64) review.
Owner area: app backend (GK B reviews).
Parent: `11-bundle-import-and-experiment-copy-cost-guard.md`.

## What

The per-row derive computes `(1 + r) ** L` for a priced part's lifetime `L`. A lifetime large enough to overflow it
(for example 1e6 years at an ordinary rate) raises a raw `OverflowError` from the writer instead of a typed
refusal. TCU 11's rate rule bounds the rate against a 200-year reference lifetime, not the part's own lifetime.

## Acceptance

- Every owned-cost writer (single edit, bulk, network and bundle import, experiment copy) refuses such a part with
  the typed `invalid_edit_value` refusal naming the row and the part, before anything is written.
- Tested on at least the single-edit and bundle-import paths.
