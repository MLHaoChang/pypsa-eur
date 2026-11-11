<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 40 — The shipped microgrid templates fail the island frequency screen

Status: needs-triage
Type: bug
Found by: issue 12's fit check (GK A I5)
Owner decisions: none yet

## What was seen

A campus run on a project made from each microgrid template, solved with the
template's own settings, fails the island frequency check
(`run/campus_freq_summary.json`):

| Template | Island hours | `failing_hours` | `bridge_failing_hours` | Failing events |
|---|---|---|---|---|
| `eh_microgrid` | 10 | 3 (hours 68, 141, 165) | none | E2 12 of 40, E3 3 of 10 |
| `eh_microgrid_staged` | 35 | 15 | at least 21 | E2 24 of 140, E3 9 of 35, E5 8 of 27 |

The staged template is sized with the island rows and still fails. Its
proposed cuts include infeasible ones ("steady island deviation 1.32 Hz
against 1 Hz").

## Why it matters

Issue 12's `mg.island.frequency` rule reads this check. It is a `warn` rule
because of this finding (GK A Q2): as a blocking rule no shipped microgrid run
would reach hand-off.

## To decide

Whether the templates' island sidecar (GFM settings, the 2 Hz/s transition
RoCoF, the 1 Hz steady deviation) or their sizing should change so the shipped
examples pass, and whether the rule then becomes blocking.

## Comments
