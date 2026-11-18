<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09: Load drawer shows "Demand 0 MW" and "Sign -1" next to a 34.5 MW profile

Status: ready-for-agent
Impact: low
Templates: Data Center Energy Hub (any load with a time series)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d). Shot: `../shots/09-load-drawer-demand-0.png`

## Repro

1. Data Center Energy Hub, Guided › 4 Loads & demand. The card reads "it_load · Peak load: 34.49 MW".
2. Press "Edit ›" on it_load.

## Actual

Under LOAD PROPERTIES the drawer shows "Demand: 0 MW", "Sign: -1" and "Profile: Loaded · 168 pts". The 0 MW is the static `p_set`, which the profile overrides. "Sign -1" is PyPSA's `sign` attribute.

## Expected

When a profile is loaded, Demand shows its peak and mean, for example "34.5 MW peak · 32.2 MW mean (from the profile)", matching the section card. "Sign" is hidden in Guided, or shown in words in Expert ("consumes power").

## Breaks

- RAW-NAMES-1.
- WALK-E2E-1 (right numbers): the card and the drawer disagree.
