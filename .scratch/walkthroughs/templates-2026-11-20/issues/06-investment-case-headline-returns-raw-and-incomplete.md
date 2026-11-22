<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Investment case: raw number without unit, and NPV and post-tax returns 'not established' after a normal run

Status: needs-triage
Impact: medium
Templates: H2
Found by: template walk, 2026-11-20 (master 1f6905e3)
Shot: ../shots/eh-inv2.png

## What

Expert > Results > Commercial & finance > Investment case > Run the investment case on the H2 template (no changes). The result shows 'Of which avoided CO2 cost (undiscounted, whole horizon) 48,665,787.09' with no currency and two decimals, while 'Unlevered NPV at WACC', 'Equity NPV', 'Lifecycle-cost NPV', post-tax IRRs, payback and DSCR are 'not established' ('Tax: not established'). Only the pre-tax IRR (8.24 %, the same for unlevered and equity) is shown. The status bar then reads 'Obj: € 9 m'.

## Why it matters

A user who follows the template to the business case gets one return figure and no NPV, and a raw number. Nothing on the screen says which input to fill to establish the rest.

## Suggested fix

Format money with unit and thousands grouping; beside each 'not established' say which input is missing and link to it.

## Comments

- Seen again 2026-11-21 (template walk, master 6b421b06): Ammonia (Expert › Commercial & finance › Run the investment case, about 24 s upper bound): only the pre-tax IRR (22.18 %) is established; post-tax IRR, NPV at WACC, equity NPV, payback, DSCR, LCOE and LCOS are "not established", with "Tax: not established" and the "WACC gate: differs from the LP discount rate (… inflation differs)". IPP (about 3 s): IRR, NPV, payback and LCOE established (DSCR, LLCR, PLCR not), but "Unlevered NPV at WACC 28,841,527.66", "Equity NPV 3,055,564.64" and the Bill table (11,602,886.83) are still raw numbers without a unit.
