<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — Route, chat tools and workbook sheet

Status: needs-triage (blocked by 05)
Type: task
Blocked by: 05
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §7, `harness/README.md`, the DC
[05](../../dc-business-case/issues/05-route-tool-workbook.md) it replaces and GK B
BC10 first.

## What to build

1. `POST /api/results/business_case` (after an IC exists; else "run the
   investment case first"), `GET` the stored result; the IC route's job lifecycle
   and abort; `PUT` the inputs (lines) with 02's validation.
2. Chat tools `run_business_case`, `get_business_case`,
   `set_business_case_lines` (fills rows from the user's words; never invents a
   price); catalogue rows, route-parity declarations
   (`assistant_route_parity_declarations.d/`), error kinds (`tool-error-kinds.d/`).
3. Workbook "Business case" sheet from the stored result, else `not_established`
   with "run the business case" (RW-KPI-1).

## Tests

BC10: route ACL, `route_chat_disposition`, write-surface lock, the error-kind
fragment, inventory, route-parity and control-matrix `--check`s; solver config
through `_solver_config_from_dict` (#1009).
