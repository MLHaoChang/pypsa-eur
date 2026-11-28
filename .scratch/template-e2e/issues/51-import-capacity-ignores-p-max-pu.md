<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 51 — The connection limit reads the import link's p_nom and ignores its p_max_pu

Status: needs-triage
Source: te 20 PR A (GK A 40, 2026-11-28); found by te 18
Severity: wrong figure
Area: GK A (engine: `flow_service.import_capacity`)
Blocked by: none

## What

`flow_service.import_capacity` sums the grid import Links' `p_nom` and does not read `p_max_pu`. A tie that can carry only part of its rating reads as its full rating. This affects:

- Guided readiness (the connection row and the `CAP_*` sentences come from the same function);
- the report's sizing row "Grid connection (import limit, not counted)";
- the te 18 Summary grid note.

On the island microgrid's off_grid pack, the tie's `p_max_pu` is 0, so the site runs off the grid, but the report reads "20.0 MW · peak import 0.0 MW · within the limit".

## Failing cases

| Import link | Reads today | Expected effective limit |
|---|---|---|
| p_nom 20, `p_max_pu` 0 static and in every series value | 20 MW | 0 MW: off the grid (te 18's words "none, the site runs off the grid") |
| p_nom 20, static `p_max_pu` 0.5 | 20 MW | 10 MW |
| p_nom 20, `p_max_pu` series with max 0.5 | 20 MW | 10 MW, per period on a staged plan |

Expected: the effective limit is p_nom × max(p_max_pu), per period on a multi-period network, summed over the import Links. The connection check compares each period's peak against that period's limit.

## Notes

- The readiness `CAP_*` sentences read the same function. A 0 effective limit needs its own sentence; `CAP_ZERO` today says "set one or choose another site type", which is wrong for an islanded tie.
- Separate from te 20's follow-up (a). There, `eh_study.py` passes the study's request, and an off_grid request makes `eh_report._connection` return check "off_grid", rendered in te 18's words.
- The pins in `tests/test_sized_design_parity.py` and `tests/test_flow_connection_limit.py` would need updating.
