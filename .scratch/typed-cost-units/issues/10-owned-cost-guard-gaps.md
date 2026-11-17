# Owned-cost guard gaps: Excel import and writers that set the frame directly

Status: ready-for-agent
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
