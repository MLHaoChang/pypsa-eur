# Template price series minted at create

Status: needs-triage
Type: task
Blocked by: —
Gated: told (Library write path; no LP change)
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §2.3 and §10 G3 first.

## What to build

- A template directory may carry `price_series/<name>.csv` (a timestamp and a value column) and
  `price_series/meta.json`. The meta gives each series its `unit`, `label`, `source: illustrative`,
  `vintage_year`, the zone and a note.
- `POST /from_template/<id>` mints each series into the creating org's Library with
  `series_store.put_series` (idempotent on content, as `export_series.put_flat_export_series`).
  It then writes the refs into the new project's `solver_config.commercial`:
  - `export_price_ref`;
  - each contract's `reference_price`, by a name map in `meta.json`.
- A failure refuses the create with a typed error. No half-made project is left.
- The sidecar list (`SIDECAR_FILES`, `_TEMPLATE_SIDECARS` in `routers/projects.py`) and
  `smoke/check_bundle.py` know the directory.

## Acceptance

- Creating twice in one org reuses the same series versions.
- The bound export price covers every snapshot, so `_export_price` doesn't refuse.
- The series shows `illustrative` in the Library and in section 6's status list.
