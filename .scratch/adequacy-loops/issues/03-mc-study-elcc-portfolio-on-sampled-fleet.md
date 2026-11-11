<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — The MC study, ELCC and the portfolio sample the Link-aware fleet

Status: needs-triage
Type: task
Gated: yes (GK A)
Model tier: opus
Raised by: [01](01-loops-sample-link-outages.md) (GK A (20), the 4th site: option (a))

## What

01 moved the coupling and margin loops and the EH study's fixed-plan snapshot onto `link_supply.sampled_fleet`. The MC study endpoint (`mc_loop_runner.start_mc`) still samples `snapshot_inputs`' generator-only fleet, because ELCC (`elcc._resolve`, `vre_assets`) and `portfolio_population(n, inputs)` key their units to the network's generators by name.

Two gaps follow:

- On every EH template with a trusted grid-import Link, the MC study still nets the grid-side generator as must-take on a copper plate. That is the double count 01 fixed in the loops, so its LOLE is optimistic there.
- On the H2 hubs it refuses ("nothing to sample") while the loops reach a verdict.

## Acceptance

- `start_mc` samples `sampled_fleet` (the hub-side split, the import Link, firm backup under AL-1), and its refusal is `nothing_to_sample` on that fleet.
- ELCC and the portfolio work on the hub-side fleet: candidates resolve on the hub side, and a `link:<lk>` unit is either priced explicitly or refused by name.
- Before/after per EH template in the release note, as 01's AL-8 did.
