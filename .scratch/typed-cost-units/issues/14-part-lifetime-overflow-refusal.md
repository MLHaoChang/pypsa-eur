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

Also an asset's own `discount_rate` the annuity cannot take (-2 divides by zero, 1e308 overflows).

## Already covered (GK B 64 ask, #1628)

`network_crud._imported_parts_problem` catches `ArithmeticError` from the per-row derive, so every caller of
`rederive_imported_parts` refuses with the typed `invalid_edit_value` naming the row ("<Class> '<name>': its
investment parts cannot be annuitised at rate <r> over <L> years"), before anything is written: the project
bundle import, the native network import (`routers/io.py`) and the harness's import outcome. Tested on the bundle
import. Still open: the single edit and create (`_check_investment_parts`, which catches only `ValueError`), the
bulk edit, the assistant's edit and experiment copy (`assistant_tools`, through `_check_investment_parts`), and
the re-derives that follow a length change (`rederive_scaled_parts`).

## Acceptance

- Every owned-cost writer (single edit, bulk, network and bundle import, experiment copy) refuses such a part with
  the typed `invalid_edit_value` refusal naming the row and the part, before anything is written.
- Tested on at least the single-edit and bundle-import paths.
