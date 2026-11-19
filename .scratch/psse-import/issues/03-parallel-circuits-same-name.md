<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PSS/E import 03: parallel circuits with the same CKT are lost

Status: done (#1720)
Type: bug
Reporter: GK A (31)
Owner decision: IMPORTS-1
Gated: yes (GK A, gridspine)
Files: `gridspine/ingest/psse_import.py`, `tests/gridspine/test_psse_import.py`

## What

Two RAW branches with the same `(I, J, CKT)` get the same name in `_branch_names`, and
`n.add("Line", …)` keeps one: two `1,2,'1'` branches with different R give one line while
`counts["Lines"]` is 2, with no unmapped row or warning. A parallel circuit is silently lost.

PSS/E identifies a branch by `(I, J, CKT)` in either direction and rejects a repeat. The same
holds for loads and generators by `(I, ID)`; a repeated generator crashes the import with a bare
`ValueError` from PyPSA.

## Fix

`build_network` refuses a repeated identity before building, naming both RAW lines: branches by
`(I, J, CKT)` in either direction, loads and generators by `(I, ID)`. Parallel circuits with
distinct CKT stay distinct lines (ledger test: `counts["Lines"]` equals the lines built).

Transformers are left as they are: their names already deduplicate, so nothing is lost.
