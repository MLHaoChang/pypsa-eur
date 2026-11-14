<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 16 — Chat reads of the line table's options and the preset

Status: needs-triage
Gated: GK B
Type: enhancement
Parent: business-case 09 (the line-item editor)

## Problem

The line table (09) reads two routes no chat tool serves yet:

- `GET /api/results/business_case/options`: the template's variants and offered
  rows, this project's linked outputs with their units, the typed units per kind,
  and every refusal of the stored lines (G9).
- `GET /api/results/business_case/preset`: the template's preset as lines.

The assistant fills lines with `set_business_case_lines`, so without these reads it
cannot offer only the units a source allows or name each refusal the table shows.
Both are held here in the route-parity declarations until a tool serves them. The
options route is declared `unbuilt` in `services/not_via_chat.py`
(`business_case_table_options`); the preset stays out of chat there by GK B (54) Q3,
so this issue settles only its route-parity limitation for the preset.

## What to build

A read tool (or an extension of `get_business_case`) that serves both routes, with
the route-parity binding, and the held declarations removed.
