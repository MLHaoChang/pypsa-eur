<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 20 — DC form and site decisions are not editable in Expert after creation

Status: needs-info
Impact: medium
Templates: eh_datacenter(+staged), eh_site
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

IT load, PUE, Backup mode, Power architecture are asked only in the creation form; Expert's Grid study shows architecture read-only ("set when the project is created"). eh_site cannot be created from Expert (form is Guided-only, works over Expert). Study budget, levers and control modes exist only in Expert; none is marked assumed (shown as "default"). Allowed shortfall: Expert Solver & mode > Network > "Save settings" vs Guided section 8 "Allowed shortfall (hours per year)" (works: raising 4.58 to 6 h/yr flipped the industrial site to Certified).

## Why it matters

To change a form answer the user must make a new project; the same decision has different wording in the two modes (EXPERT-GUIDED-1).

## Suggested fix

Make the form answers editable in both modes with one label; mark every template default as assumed.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Question for the coordinator: how should the creation-form answers (IT load, PUE, backup mode, power architecture) change? (1) Recommended: editable after creation in both modes under one label, with template defaults marked assumed. Largest change, GK A. (2) Read-only in both modes, with a plain 'start a new project to change this' line; mark defaults assumed. Small. (3) Editable in Expert only. Also one fix is not in question: let eh_site be created from Expert (it works over Expert today).
