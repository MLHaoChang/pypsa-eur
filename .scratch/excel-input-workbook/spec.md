<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Excel input workbook

Owner decisions: IMPORTS-1, IMPORT-FLOW-1, PROJ-SELF-1, HOLISTIC-1, EXPERT-GUIDED-1, RAW-NAMES-1, PR-SIZE-1.

A downloadable template with plain-named sheets and columns: nodes, lines, transformers, generators,
storage, stores, loads, links, carriers, time series, and horizon and finance basics. Headers carry units,
cells carry validation lists, and an About sheet explains the workbook. The user fills or edits the workbook
in Excel and imports it as a new project (IMPORT-FLOW-1: preview first, never an overwrite). Exporting any
project to the same workbook round-trips: export, then import, gives the same network and sidecars.

Raw tool names appear only in a hidden mapping row (RAW-NAMES-1). The reader trusts nothing in the file,
the hidden rows included.

Each slice is one PR:

- `issues/01-reader-writer.md`: S1, the pure reader and writer in `gridspine/ingest/` (GK B)
- `issues/02-app-wiring.md`: S2, the routes, the catalogue adapter and /import/excel (GK B)
- `issues/03-ui.md`: S3, the template download and the Exports choice (UX)
