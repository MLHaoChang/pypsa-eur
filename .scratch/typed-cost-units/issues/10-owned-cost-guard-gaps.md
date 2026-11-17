# Owned-cost guard gaps: Excel import and writers that set the frame directly

Status: done (#1555)
Type: bug
Found in: TCU 09 (#1545) hand-off, N5.
Owner area: app backend (GK B reviews).
Parent: `09-bulk-part-edit-follow-ups.md`.

## What

The single edit and the bulk edit refuse a write of a cost the investment parts own
(`network_crud.check_parts_own_costs`) and a priced part without a lifetime (`_check_investment_parts`). Writers that
set the component frame directly skip both:

- N5: the Excel import (`routers/io.py` `import_excel`, legacy sheets via `n.add` and native workbooks via
  `import_from_excel`) writes parts and `capital_cost`/`fom_cost`/`lifetime` raw. A stale cost stays until staging
  re-derives it; a priced part without a lifetime imports and fails later.
- Library reference apply (`routers/network_library_reference.py`): audit whether it bypasses the guard.
- Pack pricing (`services/library/defaults_pack/pricing.py` `price_from_pack`): the non-battery branch writes
  `overnight_cost`, `lifetime`, `fom_cost` and `capital_cost` with `df.at` and never checks for priced parts.
- Site packs (`services/study/packs.py`) and compile (`services/study/compile.py`): audit.

## Acceptance

- Every writer that can reach a row with priced parts runs the same validators as the single edit, or is shown not to
  reach one; a test per writer.
- GK B OK on the final head.

## Comments
- 2026-11-17, GK B (60) plan GO with conditions W1–W5. Ruling (a): on import the parts win silently, as on create.
  The netCDF and CSV-bundle imports go in scope with the same helper. Project open and restore stay out.
- 2026-11-17, built (#1555):
  - Imports (legacy and native Excel, netCDF, CSV bundle): each one calls `network_crud.rederive_imported_parts` on
    the candidate before `_replace_with`.
    - A priced part without a lifetime, or a non-finite part or scale, refuses the whole import (422
      `invalid_edit_value`, naming the row). The open network is unchanged.
    - Priced rows are re-derived at the rate in force: the asset's own, else the project's.
    - The change log names the re-derived rows.
  - `price_from_pack`: the plain branch raises `PartsOwnCosts` before any write when the row prices parts. No caller
    reaches it; a test pins that every caller prices a storage unit only as a battery.
  - Audit, where no change was needed:
    - Library reference apply writes through the single edit, and only to Generator or Link. A route test pins its
      refusal of a Storage or Line target.
    - `study/packs.py` builds a fresh network and prices the battery through `apply_parts`.
    - `study/compile.py` writes no cost column.
    - MATPOWER adds lines with no cost or part columns.
    - The templates' existing-asset `lifetime`/`overnight_cost` loops never touch a priced row.
    - The full grep audit is in the #1555 body.
  - Follow-up candidates:
    - The project-bundle upload (`routers/projects.py` `import_bundle`) is a restore, so it is unguarded. Staging
      re-derives its costs.
    - The experiment preflight copy (`assistant_tools._candidate`) writes `capital_cost` raw on a copy.
