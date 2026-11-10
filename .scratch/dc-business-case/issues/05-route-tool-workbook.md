<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Route, chat tool and workbook sheet

Status: needs-triage (blocked by 04)
Type: task
Blocked by: 04
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §6–§7, `harness/README.md` and GK B condition BC10 in [01](01-design-plan.md) ## Comments first.

## What to build

1. `POST /api/results/business_case` (runs after an IC exists; refuses with
   "run the investment case first" otherwise), `GET` for the stored result,
   the same job lifecycle and abort as the IC route (`routers/results.py:2204-2293`).
2. Chat tools `run_business_case` and `get_business_case`; catalogue rows, route
   parity declarations under `assistant_route_parity_declarations.d/`, new error
   kinds under `tool-error-kinds.d/`.
3. Workbook: a "Business case" sheet from the stored result, else
   `not_established` with "run the business case" (RW-KPI-1 pattern); export
   never runs it.
