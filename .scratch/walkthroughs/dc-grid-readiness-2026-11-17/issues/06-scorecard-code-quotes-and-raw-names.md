<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06: Scorecard rows show Python quotes, raw asset names and an em dash

Status: done
Type: task
Impact: low (wording; every DC project shows it in both modes)
Templates: eh_datacenter, eh_datacenter_staged (Guided and Expert scorecard)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: RAW-NAMES-1; review-ux.md "No Python-repr quotes", "A colon, not an em dash, joins clauses". Extends #1618 issue 06 and the template-e2e raw-names issues (#1613 issue 05, #1654 issue 19) to the scorecard. Those are filed generally and don't name these strings.

## Repro

Create Data Center Energy Hub with defaults. Open Guided › 3 Connection & grid › Site › Grid readiness (or Expert › Readiness and run › Issues).

## Actual

- Ramp, oscillation and rapid-voltage-change rows: "After UPS 'ups_battery': 93.6 MW/min · Fail" (`GridReadinessScorecard.tsx:119`, "After battery 'b'" too).
- Reactive power range: "Not sized yet: bess_new — run the plan to see the reactive range it adds." (`grid_readiness.py:1415`).
- Island ride-through: "Limit: bridge 600 s on bess_new" (`grid_readiness.py:1546`).

## Expected

Asset names in curly quotes or by their display label ("After the UPS battery “UPS battery”", "Not sized yet: “New battery”: run the plan to see …"), and a colon in place of the em dash. The same rows in the report follow suit.
