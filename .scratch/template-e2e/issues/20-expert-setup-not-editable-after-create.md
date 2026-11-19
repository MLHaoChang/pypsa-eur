<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 20 — DC form and site decisions are not editable in Expert after creation

Status: needs-triage
Impact: medium
Templates: eh_datacenter(+staged), eh_site
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

IT load, PUE, Backup mode, Power architecture are asked only in the creation form; Expert's Grid study shows architecture read-only ("set when the project is created"). eh_site cannot be created from Expert (form is Guided-only, works over Expert). Study budget, levers and control modes exist only in Expert; none is marked assumed (shown as "default"). Allowed shortfall: Expert Solver & mode > Network > "Save settings" vs Guided section 8 "Allowed shortfall (hours per year)" (works: raising 4.58 to 6 h/yr flipped the industrial site to Certified).

## Why it matters

To change a form answer the user must make a new project; the same decision has different wording in the two modes (EXPERT-GUIDED-1).

## Suggested fix

Make the form answers editable in both modes with one label; mark every template default as assumed.
