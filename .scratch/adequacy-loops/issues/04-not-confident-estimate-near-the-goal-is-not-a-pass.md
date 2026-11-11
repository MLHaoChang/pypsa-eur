<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — A not-confident LOLE estimate within one sampled event of the goal is not a pass

Status: needs-triage
Type: task
Gated: yes (GK A)
Model tier: opus
Raised by: [01](01-loops-sample-link-outages.md) (AL-8; owner decision 10 Nov: "Ship + follow-up rule")

## What

01 made the coupling loop sample `eh_h2_hub`'s grid-import Link. On the shipped template the loop now reads "met" at 1.46 h/yr against a 2 h/yr goal, and the `eh_h2_hub@1` LOLE rule passes where it blocked before. That estimate is two sampled shortfall events in 500 draws. One event is worth 0.73 h/yr there, so a third event gives 2.19 h/yr and a miss. The same seed and draw count give that third event when the project lacks the template's tariff, because the tariff moves the electrolyser's dispatch. The loop record already says `confident: false`, and no gate reads it.

A pass that one more sampled event would turn into a miss should not read as a pass.

## Acceptance

- A rule, stated once and used by every LOLE gate (the run profiles' `eh.adequacy.lole`, the coupling and margin loops' "met", the MC study's verdict against a goal): an estimate that is not confident and lies within one sampled event of the goal is "not confident", never a pass. The plan says what "one sampled event" is on a multi-period horizon and with non-unit weightings (the loop's `resolution_floor_h` is the starting point).
- The verdict names what would settle it (the draw count that resolves the goal).
- Before/after per EH template and per run profile, as 01's AL-8 did. `eh_h2_hub` is expected to move from pass to not confident; any other flip goes to the owner before the OK.
- Own plan to GK A before building.

## Out of scope

Changing the default draw count, or the sampling itself.
