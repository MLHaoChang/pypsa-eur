<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Certified 0.0 h/yr leaves out the outages of the hospital's only feeder

Status: ready-for-agent
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J2 Reliability goal → verdict
Severity: high
Send to: adequacy (EH study certification, `services/adequacy/eh_stages.py` hub_fleet_scope)
Duplicate check: none in #1618 or the adequacy-loops issues. Adequacy loops 01 (#1341) samples import Links only.

## What happens

1. Create "Island Microgrid" and run the Guided study (9 › Run study).
2. Section 10 reads "Certified: no shortfall expected, under the 3 h/yr goal", with Expected shortfall 0.0 h/yr.
3. The same page lists "Biggest risks: Hospital feeder · about €657 k per year". The engineering detail's FMEA table ranks `hospital_feeder` first: Occ./yr 10.95, ΔEUE 13,140 MWh (`shots/island-detail.small.png`).

The template gives the feeder an outage rate of 0.01 with an 8 h repair time (`project_templates/eh_templates.py:950`). The feeder is the only path to the must-stay-on hospital bus (`eh_critical`, 1.5 MW mean load).

## Hand check

- Unavailability: 0.01 × 8,760 h = 87.6 h/yr (10.95 outages × 8 h).
- The hospital loses supply in each of those hours, whatever the island's generation does.
- So the hospital's expected loss-of-load is about 88 h/yr, against a 3 h/yr goal.
- Its expected unserved energy is about 1.5 MW × 87.6 h ≈ 131 MWh/yr, which at the template's €5,000/MWh is the €657 k the FMEA shows.

The certification's Monte Carlo samples units and import Links only. `hub_fleet_scope` treats every bus inside the hub boundary ("MC hub boundary hospital, island") as one copper plate, so an outage of an internal Link to a critical bus is never drawn. The caveats line ("ONE weather realisation … INDEPENDENT … demand response EXCLUDED") doesn't say so.

## Expected

The verdict must not certify a critical bus that the FMEA on the same page shows losing supply about 88 h/yr. Either of these would do:

- Sample the outages of internal Links that feed an `eh_critical` bus.
- Withhold "Certified" and say in words that the feeder is outside the sample, naming it.

## Breaks

Adequacy spec: the verdict answers "is the must-stay-on load adequate". review-ux.md: results are traceable to inputs. The headline and the risk list on one page contradict each other without saying why.

## Done when

The Island Microgrid run either reports the hospital's LOLE with the feeder sampled, close to the 87.6 h/yr hand figure and so a miss, or shows no "Certified" and names the unsampled feeder. A test pins one of the two.

## Comments

- **F6' (GK A (31), 2026-11-19), a must-stay-on bus with units of its own** (the data-centre `it_bus` behind `site_transformer`). Its loss of supply while its feeders are out is bounded from above by the same closed-form figure as a bus without units, because its own units can only lower that loss.
  - If the bound, together with the rest of the hub, stays within the target, the bus does not block "pass". Its row shows the bound, labelled as an upper bound with the bus's own units not counted.
  - Otherwise the certification reads "inconclusive", never "fail". It names the bus and the bound, and says what clears it: a second feeder, a lower outage rate on the feeder, or the two-area treatment in issue 09.
  - A cap never certifies a pass on a risk that was not evaluated.
