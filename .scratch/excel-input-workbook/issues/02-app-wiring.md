# S2 app wiring (catalogue adapter, template and export routes, /import/excel)

Status: ready-for-agent
Blocked by: 01

See `../spec.md`.

## Comments

GK B plan GO (2026-11-17), with conditions:

- F1. Imports run the same write-path checks as an edit: `rederive_imported_parts` (TCU 11), plus the catalogue range validators once expert-catalogue 01a lands (#738). A stale cost part is re-derived and listed.
- F2. Routes:
  - export/workbook on a named project checks the ACL, and a cross-org request gets the same response as a nonexistent project;
  - the template reads only the active project;
  - an import into the active project is a project_write row for viewer-role;
  - the parity, golden-coverage, ACL and lock-policy tests run at the trial merge.
- F3. HOLISTIC-1:
  - Say whether chat can export or import the workbook, and why.
  - Hand-off and Guided both reach it in S3.
  - The PR body and the About sheet list what the workbook does not carry (layout, scene, reports, ledger).
