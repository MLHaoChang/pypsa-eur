<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 18 — A volume class the user states on a typed line

Status: needs-triage
Gated: GK B2
Type: enhancement
Parent: business-case 15 (lease-up volume factor), GK B2 plan answer Q1

## Problem

Under a lease-up below 100 % (business-case 15), a typed revenue or opex line refuses `volume_factor_typed_line`: a typed quantity states an amount, not whether it moves with served volume (ruling 6, fail closed). A case with a typed sale or a typed volume cost cannot use lease-up at all.

## Ask

A per-line volume class the user states: `fixed` never scales, `volume` scales by the year's factor; unset still refuses. The edit card and the workbook show it; the business-case hash covers it only where it changes an outcome.
