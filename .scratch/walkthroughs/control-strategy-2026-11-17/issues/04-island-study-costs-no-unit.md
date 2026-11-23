<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04: Island study cost tables give no unit, and limits show constraint names

Status: done

Done in #1673 (20728b0b).
Impact: medium
Templates: Island Microgrid (any project that runs Grid study › Island)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d). Shot: `../shots/04-island-cost-no-unit.png`

## Repro

1. Island Microgrid, Expert. Run LOPF: objective 3,473,806.
2. Grid study › Island › "Run cost-of-islanding study" (default scenario: seamless).
3. Results › Control › Island & ride-through › "Island study".

## Actual

- "Cost of island capability": Baseline (no island rows), Objective 3,473,806, Cost 0; Seamless, Objective 3,657,931, Cost 184,125. There is no currency and no "per year".
- "Island limits that were reached": rows "Island-n1-down-bess new" (7 h, 83,338) and "Island-n1-down-island battery" (9 h, 96,349). These are LP constraint names, and again there is no unit.
- The two limit costs sum to 179,687, not 184,125. Nothing says that a limit's cost is its shadow value, so the costs are not shares of the total.

The numbers are right. The baseline equals the LOPF objective (3,473,806). The cost of island capability is 3,657,931 − 3,473,806 = 184,125.

## Expected

- "€ per year" on every money column, as Results › Summary does (€3.47 M).
- The limit in words, for example "Island N-1: loss of bess_new, downward reserve".
- One line saying that the limit costs are marginal values and do not add up to the total.

## Breaks

- RAW-NAMES-1.
- `review-ux.md` ("A chart header never gives two units", and units on figures).
- WALK-E2E-1: results traceable to inputs.

The raw field keys in the same tab's inputs table (`droop_pct, i_max_pu, k_gfm_min, H_s, tau_f_s`, "island sidecar (island_config.json)") add to template-e2e issue 19 (`.scratch/template-e2e/issues/19-expert-raw-names.md` on `claude/template-e2e-expert`). They are not re-filed here.
