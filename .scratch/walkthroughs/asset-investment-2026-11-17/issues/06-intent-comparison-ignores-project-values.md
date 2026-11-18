<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The intent comparison asks again for values the project already holds

Status: ready-for-agent
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J1 Design intent → sizing loop (Expert)
Severity: medium
Send to: `pages/IntentComparisonSection.tsx`
Duplicate check: none.

## What happens

On the Data Center Energy Hub project (as in issue 05), the Intent comparison inputs have three problems.

Five fields open empty although the project already holds their values:

- Grid ramp limit: empty. Grid connection › Connection limits shows "Connection ramp limit: 4 MW/min (assumed)".
- Load swing, Oscillation amplitude, Oscillation period: empty. The same panel states "Steepest load change 23.4 MW/s" and "7.2 MW peak-to-peak … at 0.5 Hz".
- Pst limit: empty, with "No grid code is set". Grid connection › Grid code and PCC shows "EU RfG (2016/631) and DCC (2016/1388)" as the selected code.

Critical load is prefilled as `34.4937816188` MW, ten decimals.

The "Campus link" select also lists `site_transformer` next to `grid_import` with no hint which is the point of connection. Grid connection names `grid_import` as the PoC.

## Expected

- Each field prefills from the project value it duplicates, marked with its source (assumed, grid code) and editable.
- "No grid code is set" appears only when Grid code and PCC has none saved. If the select only shows its first option without saving it, the select must say "not set".
- Numbers are rounded to the field's precision (34.49 MW).
- The link select preselects the PoC.

## Breaks

review-ux.md: one entry point per value, units and precision. HOLISTIC-1: one source per number across the tool.

## Done when

Opening the section on the template shows the ramp, envelope, Pst limit and PoC link from the project, and the critical load rounded. A component test covers the prefill.
