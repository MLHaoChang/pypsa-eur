<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 One electrical equipment schedule, with switchgear and cable ratings chosen, not only declared

Status: needs-triage
Blocked by: none (gc/24 fixes the PASS shown with no switchgear rating; this issue picks the rating)
Source: DC template review 2026-11-24 (`../README.md`). Stage 5.
Severity: major
Model: opus. Review: GK A + UX.

## Problem

Transformers are selected from the library by the campus MILP with N-1 and margin (`services/campus_electrical_service.py:157`, `SizingCriteria` at `:1983`, `:2396`), and the STATCOM is an LP variable (`services/solver/pcs_statcom.py:273-286`). The rest is declared and checked:

- switchgear: `ik_rated_ka` is an optional field the user types into the campus file (`gridspine/ingest/campus.py` `_BUS_OPTIONAL`, `_PCC_OPTIONAL`); nothing picks a rated short-time or breaking current from the fault study;
- cables: `max_i_ka` is typed per cable (`_CABLE_FIELDS`); no ampacity selection (installation method, derating, voltage drop, short-circuit withstand);
- there is no single output that lists every electrical asset with its rating, basis and source; `services/equipment_table.py` is an upload mapper.

## Acceptance criteria

- [ ] Switchgear rating chosen per bus from the max-case IEC 60909 result (`ip`, `Ib`, `Ith`) plus margin, from a standard ladder (e.g. 25/31.5/40/50/63 kA), with the governing case named.
- [ ] Cable cross-section chosen per run from load current (N-1 case), a derating factor (assumed, shown), voltage drop and short-circuit withstand, from a library ladder.
- [ ] An equipment schedule (route + Results › Hand-off table + XLSX): transformers, STATCOM/reactive, filters (08), switchgear, cables, gensets, BESS/UPS, each with rating, quantity, governing criterion, source (computed/declared/assumed) and capex.
- [ ] Declared ratings below the chosen ones are flagged, not overwritten.

## Likely files

`services/campus_electrical_service.py`, `gridspine/static/campus_sc.py`, `gridspine/ingest/campus.py`, `services/library/defaults_pack/`, `frontend/src/pages/CampusElectricalPanel.tsx`.

## Comments

- 2026-11-24 (builder): the schedule is served by `GET /api/campus-electrical/{name}/equipment-schedule` and `GET /api/campus-electrical/{name}/equipment-schedule.xlsx`, shown in Results › Hand-off › Equipment schedule and in the Short-circuit section of Campus electrical. A chat tool for it is not part of this issue (not_via_chat entry `equipment_schedule`, disposition unbuilt).
