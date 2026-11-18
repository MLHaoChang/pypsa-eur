<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05: Island results say "Not yet available" with no step to take; the campus-file prerequisite has no link

Status: ready-for-agent
Impact: medium
Templates: Island Microgrid
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d). Shot: `../shots/05-not-yet-available-above-results.png`

## Repro

1. Island Microgrid, Expert. Results › Control: "Not yet available: It can't be run from here yet. Run the island study in Grid study ›".
2. Follow the link. The panel opens on "Study settings: Draft the campus file in the grid connection first; the study runs on it." This is plain text, with no link and no button.
3. Run the cost-of-islanding study anyway. It runs.
4. Back in Results › Control › Island & ride-through, the top line still reads "Not yet available: campus run with island tables, island steady state and short circuit at the island hours, island frequency check at the island hours". The island study results are below it.

## Actual

- The user cannot tell what "the campus file" is, where "the grid connection" step is, or which of the four missing checks the next action would unlock.
- The "Not yet available" line sits above real results, so the tab reads as empty at first glance.
- Solver settings › General › Control lists "Island, ride-through & load smoothing: Not yet available · It can't be run from here yet", yet this template ships island settings and the island study runs.

## Expected

- The prerequisite is a "›" link (through `withReturn`) to the editor that drafts the campus file, or a button that drafts it.
- The results line says what has run and what has not, for example "Ran: cost of island capability. Not run yet: steady state, short circuit and frequency at the island hours (needs the campus file › Draft it)".

## Breaks

- `review-ux.md`: "the user never has to jump away and back"; "Disabled reasons are visible text".
- WALK-E2E-1 (no dead ends).
