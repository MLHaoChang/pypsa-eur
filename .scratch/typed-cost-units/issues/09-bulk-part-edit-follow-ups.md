# Bulk part edit follow-ups: owned costs, rate change, absent part column

Status: in-progress
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
  such a case (`invalid_sensitivity_attribute`) before any case exists. The assistant preview already refuses
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
