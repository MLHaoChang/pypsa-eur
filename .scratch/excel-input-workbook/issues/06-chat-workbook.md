# Chat export and import of the Excel input workbook

Status: done
Blocked by: 03

See `../spec.md`. Filed by S2 (issue 02, F3): S2 gives chat no workbook tool.

- **Gap.** Chat cannot download the template or a project's workbook, and cannot import the workbook as a new project. Its `import_excel` reaches the new reader through `/api/io/import/excel` but refuses the input workbook (422 `import_as_new_project`), and `export_excel` stays in PyPSA's own layout.
- **Why held.** IMPORT-FLOW-1: an import shows its preview first and never overwrites. Chat needs a preview+confirm card for it.
- **Ask.** A preview+confirm card over these routes, landed with S3's Exports so the UI and chat change together (HOLISTIC-1):
  - `GET /api/workbook/template`
  - `GET /api/projects/{name}/workbook`
  - `POST /api/workbook/preview`
  - `POST /api/workbook/import`
- **Route parity.** S2 declares nothing in `smoke/assistant_route_parity_declarations.d/`: `--check` refuses a declaration for a route no GUI binding reaches. When S3 binds the routes, it declares the two GETs `binary-transfer` and the preview and import `held-by-spec` with this file as the issue, until this issue builds the card.
- **Then** remove the `excel_input_workbook` "unbuilt" entry S2 added (`services/not_via_chat.d/claude-excel-input-workbook-s2.json`) and any `held-by-spec` rows naming this file.
