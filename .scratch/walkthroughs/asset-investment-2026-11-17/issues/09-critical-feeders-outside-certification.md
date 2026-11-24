<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Must-stay-on feeders: simulate the cases issue 02 left in closed form or named

Status: needs-triage
Type: task (follow-up of issue 02, GK A conditions F6' and F8)
Journey steps: J2 Reliability goal → verdict; Expert › Reliability › Monte Carlo; planning loops
Send to: adequacy (`services/adequacy/critical_feeders.py`, `mc_zonal.py`, `link_supply.loop_scope`)

## What is left

Issue 02 adds a must-stay-on bus's loss of supply to the certified LOLE when every feeder to it is out. It does this in closed form, and only for a bus with no supply of its own that is fed straight from a supplied part of the hub (`model: composed`). Three cases stay outside the simulation. For the first two, the certification bounds or withholds instead (GK A F6'):

1. **A must-stay-on bus with units of its own** (`model: unsampled`). On the data-centre templates, `site_transformer` (unavailability 0.005, about 44 h/yr) is the only path from `dc_mv` to `it_bus`. While it is out, `it_bus` runs on `ups_battery` alone, and the gensets on `dc_mv` cannot reach it. The certification does not simulate those hours. It bounds them from above with `ups_battery` not counted (0.005 × 8,760 = 43.8 h/yr). Where that bound and the hub's interval together exceed the goal, the verdict is "Not decided" instead of a pass.
2. **Chained feeders**: a must-stay-on bus fed through another bus with no supply of its own (`model: unsampled`, `chained`). There is no closed-form bound, so a pass is withheld ("Not decided").
3. **The copper-plate views.** The coupling and margin loops and Expert's sequential Monte Carlo (`/results/mc`) read `link_supply.loop_scope`. They do not add the feeder hours, and they show about 0 h/yr on Island Microgrid where the certification shows about 88 h/yr. They now carry the note "this view does not sample the feeders of must-stay-on buses inside the hub (…); the reliability study's certification does".

## Proposed

- Model the bus behind its feeders as its own area in the zonal MC (`mc_zonal`): the feeders become a sampled tie, and the bus's own units and storage are its fleet. This covers cases 1 and 2 exactly, including the battery's state of charge across a repair.
- Have `loop_scope` users compose the same way, so that every reliability number on the page has one source (HOLISTIC-1).

## Done when

- The data-centre templates' certification simulates `it_bus` on its own units while `site_transformer` is out, and the F6' bound (and the "Not decided" it can cause) gives way to the simulated figure.
- A chained pocket is simulated, not withheld.
- Expert Monte Carlo and the loops report the same feeder hours as the certification on Island Microgrid.
- A test pins each.

## Comments

- 2026-11-23 status sweep: landed in #1729 (9713f895): feeder bound added: unread feeders rate as a bound and never pass, plain feeder remedy. Remains: simulating the it_bus pocket and chained pockets in the zonal Monte Carlo, and Monte Carlo/loops matching the certification feeder hours.
