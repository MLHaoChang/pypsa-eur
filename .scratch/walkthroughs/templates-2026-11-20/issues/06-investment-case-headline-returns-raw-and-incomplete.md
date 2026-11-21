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
