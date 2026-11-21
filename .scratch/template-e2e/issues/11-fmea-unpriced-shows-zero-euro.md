<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — FMEA worksheet shows €0 for a failure mode it could not price

Status: done (remainder; see end of file)
Owner: template-e2e 08 slice-2 builder (assigned by coordinator session_01G7qCuj8rD9ab9ReE2VaxLB; #1645 introduced the null). Lands in slice 2.
Size: S
Gated: UX (UX 2 session_01G7qCukikR93QrY7d2YFJ1c)
Likely files: pypsa-gui/frontend/src/…/fmea.ts (l.110–111 `Number(r.severity_eur ?? 0)`, sort at l.142); FmeaTab.tsx (l.335, l.374 currency cells); ResultsCard.tsx l.172 already handles null
Journey fit: results (FMEA worksheet) → decide.

## Problem

Since #1645, `/fmea_modes` with no VOLL returns `severity_eur` and `criticality_eur_per_year` as None (GK B plan Q1a), together with the row "Value of lost load: not set". fmea.ts turns None into 0, so the worksheet prints €0 next to the unpriced warning and ranks unpriced modes as the least critical.

## Acceptance (UX 2's terms on #1645)

1. An unpriced mode's € cells read a plain "Not set". The reason is visible text on the table, for example one muted line: "Not priced: set the Value of Lost Load (VOLL) in Solver settings >" with withReturn, the label as in the Solver settings panel. Never a bare "—", and never a reason shown only in a tooltip (#1266). This holds in Guided and Expert alike.
2. Unpriced modes sort after the priced ones, and the column header or that line says so. They are never mixed in as "least critical". Any total or chart over the modes leaves them out and says so.
3. A vitest covers null severity and null criticality, plus one shot of the table in the unpriced state, dark and light.
4. It lands with or before slice 2 of template-e2e 08, not after. If it hasn't started when slice 2's plan is ready, tell UX 2.

## Remainder (checked on master a68541e6b)

Verified: `mergeWorksheet` keeps null for computed rows, FmeaTab shows "Not set" and sorts unpriced last. Fixed here: manual rows no longer turn a missing € into 0 (`fmea.ts`, vitest added).

Follow-up, not done (open #1729 and #1757 edit it): `pages/results/EhReferenceDesignPanel.tsx` still has `severity_eur` cells (l.575 CSV `?? ''`, the table cells near l.1896) — check they read "Not set" and not €0 once those PRs merge.
