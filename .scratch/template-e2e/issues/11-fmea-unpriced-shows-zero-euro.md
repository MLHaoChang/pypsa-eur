<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — FMEA worksheet shows €0 for a failure mode it could not price

Status: ready-for-agent
Owner: results-components builder (guided-results, session_01G7qCukjZ3bmHs3PUZEJDS1; #1611 territory)
Size: S
Gated: UX (UX 2 session_01G7qCukikR93QrY7d2YFJ1c)
Likely files: pypsa-gui/frontend/src/…/fmea.ts (l.110–111 `Number(r.severity_eur ?? 0)`, sort at l.142); FmeaTab.tsx (l.335, l.374 currency cells); ResultsCard.tsx l.172 already handles null
Journey fit: results (FMEA worksheet) → decide.

## Problem

Since #1645, `/fmea_modes` with no VOLL returns `severity_eur` and `criticality_eur_per_year` as None (GK B plan Q1a), together with the row "Value of lost load: not set". fmea.ts turns None into 0, so the worksheet prints €0 next to the unpriced warning and ranks unpriced modes as the least critical.

## Acceptance

- An unpriced mode's € cells read "not set" (the words the Assumptions row uses), not €0.
- Unpriced modes sort after the priced ones.
- A vitest pins both (None → "not set"; sort order).
