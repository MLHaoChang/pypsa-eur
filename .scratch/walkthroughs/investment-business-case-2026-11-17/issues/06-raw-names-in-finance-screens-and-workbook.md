<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Raw and mangled names on the finance screens and in the workbook

Status: done (#1683: finance screens in words; #1868: the workbook "Business case" sheet and the "Quantity from" options in words)
Severity: low to medium (plain words, RAW-NAMES-1)
Group: Investment and business case. Walk 2026-11-17, master 9491878d. Extends `.scratch/ux-walkthrough-2026-11/issues/06-raw-tool-names-on-screens.md`, which covers the Expert header and chat cards; these are different strings.
Breaks: owner decision RAW-NAMES-1; workbook spec RW-KPI-1 (readable KPI rows).

## Where

| Screen | String | Say instead |
|---|---|---|
| Business case line table › Category (opex) | "Sga" | "Overheads (SG&A)" |
| Business case line table › Escalation | "Ppa", "Opex", "Fuel", "Tariff", "Export", "Capex", "Contract" | "PPA price", "Operating cost" … with the rate each reads |
| Business case › rows from the energy model | "Fom", "Energy wholesale", "Ppa settlement" | "Fixed running cost", "Wholesale market energy", "PPA settlement" |
| Business case › "Quantity from" | "Capacity in service: genset_1" | the asset's display name |
| Workbook sheet "Business case", KPI column | `ebitda_margin_gross`, `revenue_per_capacity_gross`, `project_npv`, reasons `operating_not_established`, `capacity_not_stated` | "EBITDA margin (gross)", "Project NPV", plain reasons |
| Workbook sheet names with the investment case selected | "Case dispatch_modes", "Case tax_equity", "Case CashflowLines" | "Investment case: dispatch modes", … |
| Workbook sheet "Economics" | "Revenue (all assets): not_established" | "not established" in words, as the app writes it |
| Expert › Results summary › LCOE tile | "Not available for this run — Checked figures are switched off for this installation." | what to do, or hide the tile |

## Also seen (layout)

Business case line table at 1360 px: the "Years" and "Basis" columns sit past the right edge and the quantity cell is cut off ("From the model (outputs not…"); the first column truncates names ("Capacity payment (BES"). Light and dark alike; contrast is fine in dark.

## Acceptance

- A guard (extend the expert-catalogue raw-name test) over the Business case view's labels and the workbook's KPI and sheet names.
- Line table scrolls or wraps without hiding the Basis column.
