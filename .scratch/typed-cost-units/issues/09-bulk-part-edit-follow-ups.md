# Bulk part edit follow-ups: owned costs, rate change, absent part column

Status: done (#1545)
Type: bug
Found in: GK B (59) review of #1533 (TCU 08), non-blocking notes.
Owner area: app backend (GK B reviews).
Parent: `08-bulk-part-edit-skips-rederive-and-fingerprint.md`.

## What

- N1: `services/assistant_tools.py` `_preview_candidate` said the bulk handler skips the priced-part check. Since
  #1533 it runs it; the comment now says why the pre-check stays (a typed refusal before sandbox work).
- N2: on a row with a priced part, a PUT or bulk write of `capital_cost`, `fom_cost` or `overnight_cost` (and
  `lifetime` when every part has its own lifetime: StorageUnit, not Line) reported "updated" and was then re-derived
  away. It is now a 422 naming the parts (`network_crud.check_parts_own_costs`, modelled on `fuel_curve.guard`):
  a blank or the stored value is not an edit; clearing the parts in the same write is allowed. The sweep refuses
  such a case (`invalid_sensitivity_cases`, a registered kind) before any case exists. The assistant preview already refuses
  these through `edit_card._check_value` (`derived_field`).
- N3: after a project discount-rate change, any write to a priced row re-derives it at the new rate (bulk on any
  column, and PUT as before). Kept: the costs really are stale, the rate change already moved the fingerprint
  (the config is hashed), and the staging re-derives with the rate in force, so the LP is identical. Pinned.
- N4: a bulk write to a part column absent from the frame was a 400 while PUT created it. Bulk now creates the
  class's own part columns (after every check, so a refusal adds none); any other unknown column stays a 400.
- N5 (note only): the legacy Excel sheet import (`routers/io.py:434-456`) writes parts raw; the staging re-derive
  and the composite-parts fingerprint backstop cover it. No change.

## Acceptance

- Tests in `pypsa-gui/backend/tests/test_bulk_part_rederive.py` (TCU 09 block) for N2, N3, N4 and the sweep.
- GK B OK on the final head.

## Comments

- 2026-11-14, GK B (59) plan GO with conditions:
  - N1 GO. N3 kept (GK B ruling); the staged LP capital_cost is bit-identical with or without the bulk.
  - P1 list every caller that submits an owned column for a composite row; the form stops sending owned columns
    (vitest) rather than echoing them; no tolerance in the guard.
  - P2 tests, templates and shipped cases that edit capital_cost or lifetime on a priced StorageUnit or Line still
    pass, or are changed on purpose and named.
  - P3 ownership from the row after the write; a write that adds the first priced part and a capital_cost is
    refused (intended: the typed cost would be re-derived away in the same write).
  - P4 reuse registered refusal kinds; P5 the sweep leaves no child directory and no DB row.
  - N4a the column is created after the validate-all phase, under the lock; a 422 leaves columns and dtypes.
    N4b a new all-NaN part column leaves the fingerprint.
  - Mutants: guard removed, before-ownership, Line lifetime owned, N4 widened, N4 before validation, isclose.
- 2026-11-16, built: the StorageUnit and Line forms omit their derived costs (`assetForm.omitDerived`); the sweep
  refuses with the registered `invalid_sensitivity_cases`. The test helper `_fingerprint` now hashes the test's
  own network: outside a request the active context is a different network, so the TCU 08 fingerprint asserts
  compared unrelated networks (they hold on the real one).
- 2026-11-16, GK B (59) GK OK @cf7e7b3f on #1545 (P1–P5, N3, N4, mutants). Non-blocking note fixed after:
  the overnight_cost refusal now names it in plain words (no field spec on a composite class).
