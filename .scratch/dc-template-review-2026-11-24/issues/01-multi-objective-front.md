<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 A real front of reliability vs capex vs opex vs CO2 for the data-centre templates

Status: in progress
Blocked by: none (02 adds the CO2 axis cap)
Source: DC template review 2026-11-24 (`../README.md`). Stage 3.
Severity: blocker (the owner's core ask)
Model: opus. Review: GK A (engine) + UX (results view).

## Problem

The only trade-off curve is the "cost vs availability" frontier: `services/adequacy/frontier.py` sweeps one epsilon-constraint, the ENS cap, over a fixed ladder of 8 targets (`DEFAULT_TARGETS_PERMYRIAD`, at most `MAX_FRONTIER_POINTS` = 12) and records one total cost per point. It has:

- no CO2 axis (no `co2` in `frontier.py` or the EH study's frontier stage);
- no capex/opex split per point, so "affordability" is one number;
- no Guided surface (`FrontierPanel` is mounted only in Expert `pages/results/AdequacyTab.tsx:123`);
- `frontier_default` unset for the DC packs (`services/adequacy/eh_study.py:179`), so the EH study never runs it on its own.

**Reproduced on master `06859826`** (API, project made from `eh_datacenter_staged`): `POST /api/results/frontier` ran 8 targets in 72 s. Every point came back `binding: "voll"`, `achieved_ens_mwh` 0, and the same `total_system_cost_eur`; `knee` null and `warning` null. With VOLL at 5000 EUR/MWh and no outages inside the LP, serving every MWh is already optimal, so the "front" is one point, and nothing tells the user that the targets never bound.

The intent comparison (`services/compare/intent_table.py`) and architecture comparison (`POST /campus-electrical/{name}/dc-architecture/compare`) give one solved point per preset, not a front.

## User-visible impact

A user cannot see which designs are non-dominated across reliability, cost and emissions, nor pick a design on that front. Every trade-off is either one axis or a handful of presets.

## Acceptance criteria

- [ ] A front study over two constraint axes (ENS cap × CO2 cap, see 02), each point a full re-optimisation, records per point: ENS/EENS, LOLE where certified, annualised capex, opex, total CO2 (t/yr, see 03), and the built capacities.
- [ ] Reliability is measured where it can vary: points carry the sampled-outage (MC) ENS/LOLE of each built fleet, not only the deterministic LP's ENS, which is 0 on the template.
- [ ] When no target binds (every point equal), the study says so in words and suggests the lever (lower VOLL, add outage sampling), instead of drawing a flat line.
- [ ] Dominated points are marked, not dropped; non-convexity is warned as `frontier.py` already does.
- [ ] A budget refuses grids larger than the solve budget before any solve, with the count named.
- [ ] Results show the front as a chart (cost vs reliability, coloured or faceted by CO2) with a table; a point can be applied as the design (one click, revision-checked).
- [ ] Guided section 10 shows the front with the chosen point; Expert shows the same data (Guided/Expert parity test).
- [ ] Engine test: on a 3-bus toy network the front is monotone along each axis and a hand-solved point matches.

## Likely files

`services/adequacy/frontier.py`, `frontier_loop_runner.py`, `eh_study.py` (frontier stage), `routers/results.py` (`/results/frontier`), `frontend/src/pages/results/FrontierPanel.tsx`, a Guided results tile under `pages/hubDesign/`.

## Comments
