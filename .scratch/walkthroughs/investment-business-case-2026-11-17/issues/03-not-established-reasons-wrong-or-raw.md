<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — "Not established" names the wrong cause, or a raw code

Status: done (#1683: the UI names the first blocker in words; #1868: the workbook sheet names reasons in words and the report serves `pnl.kpi_row`; IB-B PR C, branch `claude/ib-b-terminal-life`: case B's cause, a typed capex line's life now feeds the remaining-life terminal rule instead of `terminal_part_unknown`)
Severity: high (the user cannot tell what to fix to get NPV and IRR)
Group: Investment and business case. Walk 2026-11-17, master 9491878d.
Breaks: `.scratch/business-case/spec.md` §7 and G9 (typed refusals name the line and the column); owner decisions RAW-NAMES-1, DECISION-GAPS-1. Not-established-never-zero itself holds (see README).

## Repro A (data centre)

1. Data-centre project from the template, solved, point of connection saved, participants from the "single owner" template, finance inputs with asset dates, investment case run, colocation preset applied with typed prices (issue 01 explains why typed).
2. Run the business case. Read the NPV, IRR tiles (or `report.kpis` and `report.pnl.row_reason`).

## Repro B (IPP)

1. "Renewables IPP with battery" from the template, solved, investment case run (completes).
2. Business case: apply "IPP hybrid"; set capacity price 40, GoO 250 000 MWh × 1, land 3000, Plant capex 1 × 130 000 000 (straight line, 25 y), insurance 0.005; set indexation 0.02 on the two revenue lines. Run.

## Actual

- A: every KPI reads `operating_not_established`. The real cause, in `row_reason`, is `degradation_missing:genset_1 … genset_4, genset_new, rooftop_pv` (six assets, each needs a rate typed in finance). With rates set to 0 the reason becomes `tax_not_established: capex_not_established, input_missing:tax_losses, tax_pack_missing`. With a tax pack and a loss rule set it stays `capex_not_established`, with no asset named, and `flags` carry `asset_lifetime_unknown:genset_1` and seven more.
- B: the tile says "Not established: the operating section is not established". The real cause is `terminal_value_missing` and `terminal_part_unknown:Plant capex:investment` (the typed capex line is not a part with a lifetime, so the terminal rule cannot value it). The IC on the same project has NPV and IRR, so Investment and Business case disagree on whether the project is established.
- The UI prints "Not established: the operating section is not established"; the workbook sheet "Business case" prints `operating_not_established`, `capacity_not_stated`.

## Expected

The reason shown is the cause the engine already holds (`row_reason`, the first blocker), in plain words with the item and the field to set ("Set a degradation rate for the gas generators", "Pick a tax rule", "Give the plant capex a lifetime for the terminal value"), and a link to it. Where the IC establishes a figure from the same inputs, the business case does too, or says which extra input it needs.

## Acceptance

- Both repros show the first blocker, in words, with the control that fixes it.
- A test that no KPI reason in the UI or workbook is a code string (`_not_established`, `_missing`, `:`).
