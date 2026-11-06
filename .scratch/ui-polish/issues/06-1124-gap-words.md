<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# #1124: `GAP_WORDS` covers one gap only

Status: needs-triage
Type: wording
Found by: UX (9) review items (#1124); owner list DOCS-1.

## Problem

Only `import_tariff` has a word; any other gap falls back to the generic text. Extend the map to every gap the backend can name, with a parity test against the backend gap list (review-ux: card labels reuse the owning panel's word map with a parity test). Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/components/runMonitor/RunMonitor.tsx` line ~108 (`GAP_WORDS` = `{ import_tariff: 'Import tariff' }`), `gapWords` (~110) and the row text (~143).
