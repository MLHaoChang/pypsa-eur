<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12: Island site warns "subsea_tie has no outage data" while section 3 marks outage data done

Status: ready-for-agent
Impact: low
Templates: Island Microgrid
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Island Microgrid, Guided › 3 Connection & grid. The card shows "✓ Outage data: 7 units" and "Connection point: Subsea tie: normally open; the study runs the site as an island".
2. Open 5 Assets & sizing.

## Actual

- Section 5 opens with a warning: "1 unit has no outage data (subsea_tie). (Assets & sizing › Forced-outage rate)", with "Open here ›" and "Open in Expert". The section stays "to check".
- The tie is normally open and the site runs as an island, so the tie's outage rate does not enter the study. Section 3 already counts outage data as complete.
- Results › Summary › Key capacities lists "grid 100 MW" (mainland_supply) on the island, which reads as if the island imports.

## Expected

- A normally-open tie on an island site is excluded from the outage-data check, or the check says why it matters.
- Sections 3 and 5 agree.
- Key capacities omit, or label, a supply the island cannot use ("mainland, tie open").

## Breaks

WALK-E2E-1 (plain words, numbers consistent across sections).
