# The repair time (MTTR) has no bound on the asset form or the workbook import

Status: needs-triage
Blocked by: 07 (#1855)

See `../spec.md`. Filed at GK B 70's request on #1855. GK B 70 confirmed there that the import matches the single edit, so the gap predates #1855.

- **Gap.** `mttr_hours` is `float | None` on every Create model in `pypsa-gui/backend/models/schemas.py`. The asset form and a workbook import both accept a negative or infinite repair time. The outage rate and basis are checked; MTTR is not.
- **Ask.** Bound `mttr_hours` in the schema, finite and ≥ 0 (blank stays unset), so the single edit and the import refuse the same values with the same message. This is a schema change, so it needs GK B's plan review.
- **Tests.** A negative and an infinite MTTR are each refused on the single edit, on the workbook preview and on the import, with the exact text, and nothing is written. A blank cell still imports as unset.
