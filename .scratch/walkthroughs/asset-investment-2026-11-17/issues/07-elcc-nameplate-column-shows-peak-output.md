<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The capacity-credit table labels wind's peak output as its nameplate

Status: ready-for-agent
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J2 Monte Carlo → ELCC (Expert)
Severity: low
Send to: `pages/results/McPanel.tsx` (ELCC picker and table)
Duplicate check: none.

## What happens

1. On Island Microgrid: Expert › Results › Reliability › Sequential Monte Carlo.
2. The picker lists `wind_turbines · vre · 4.85 MW`. Assets & sizing and the template both give 6 MW (`eh_templates.py:913`).
3. Tick pv_plant and wind_turbines, then Run study. The ELCC table reads "wind_turbines | vre | Nameplate 4.85 MW | Credit 1.21 MW (25.0%)".

`elcc.py:155-163` documents the figure as the peak must-take contribution over the horizon, not the installed capacity. The column header says "Nameplate".

## Hand check

1.21 / 6 = 20.2 % of nameplate. The table shows 25.0 %, which is 1.21 / 4.85, a share of peak output.

## Expected

The column reads "Peak output" (or shows "6 MW installed · 4.85 MW peak"). The credit share states its base, or uses nameplate as capacity-credit tables usually do.

## Breaks

review-ux.md: results traceable to inputs. RAW-NAMES-1 also covers the `vre` and `storage_unit` kind labels in the same table.

## Done when

The Island run shows the 6 MW installed figure next to the peak, and the share's base is stated.
