<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The CSV, netCDF and MATPOWER imports: viewer row to match /import/excel

Status: needs-triage
Type: task
Source: Excel input workbook S2 (#1798). The plan's Q2 default, which GK B 68 approved, was "write active for excel only, ticket for sibling imports". This is that ticket.

## Problem

S2 changes the viewer row for `POST /api/io/import/excel` from `none` to `write active` (`services/_project_write_rules.py`). The three sibling imports keep `none` ("replaces the open network with an unsaved scratch copy; writes no project"):
- `POST /api/io/import/csv`
- `POST /api/io/import/netcdf`
- `POST /api/io/import/matpower`

A viewer of the open project is therefore refused the Excel import, but can replace the open network with the same tables as a CSV zip. The rows disagree about the same action.

## To decide

Either:
- give all three the `write active` row, with the gate matrix in `test_viewer_role_gate` and a "viewer refused, network unchanged" test for each; or
- record why a scratch-copy replace stays `none` for these three and move `/import/excel` back with them.
