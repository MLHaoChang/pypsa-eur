<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-34 — Exports write a formula-looking asset name unescaped

Status: needs-triage
Type: bug

Found by LC-32 (D20, `LC-D20.injection`). With a generator named `=HYPERLINK(1)` in the network:

- `export_csv_bundle` writes `generators.csv` with the line `=HYPERLINK(1),B1,3.0,...` unescaped;
- `export_excel` stores the name as a formula; read back with `pandas.read_excel` the cell is empty.

A user-chosen name reaches files other people open in a spreadsheet. The owner has been asked
(via the coordinator) whether to fix the exports (prefix cells starting `=`, `+`, `-`, `@`), reject
such names, or leave it recorded.

Then add the covering test to `pypsa-gui/backend/tests/test_lc06_d20_delivery.py` and flip
`LC-D20.injection`.
