<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08e SSTI UIF: the scorecard and pack judge the island rows

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: GC-08b (`08b-ssti-island-case.md`)
Gated: **yes** (GK A2 engine; GK B for the backend part)
Owner decision: GC-SCOPE-1, GC-TEXT-1
TRGC: 19 (screening; binding studies stay in PSCAD)
Likely files: `pypsa-gui/backend/services/adequacy/grid_readiness.py`, `pypsa-gui/backend/services/grid_compliance/ssti_source.py`, `pypsa-gui/backend/services/campus_electrical_service.py`, `gridspine/drivers/campus_study.py`, `gridspine/static/ssti_uif.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-08b screens an open-tie campus (`tie_normally_open`) with the grid out, but keeps the island rows out of every GC-08 reader (GK A2 U4): their role is
`ssti_uif.ROLE_ISLAND`, their verdict is `UifStudy.island_verdict` / `island_worst_uif`, and the run's meta
carries only the grid cases' verdict and worst. So the scorecard still reads unknown on an open tie
(`grid_readiness.SSTI_ISLAND`), says "grid-connected state only" on every judged row (`SSTI_GRID_ONLY`), and
the pack prints the island rows "not judged".

## What to build

0. `ssti_uif.islands` also selects a closed-tie campus that declares island requirements
   (`campus.island["requirements"]`), per GK A2's GC-08b ruling (Q1 = B there, widening here).
1. The SSTI screens' meta records the island verdict and worst UIF (`campus_study.ssti_screens_campus`,
   `campus_electrical_service`).
2. The scorecard's UIF row judges the island rows on a campus that islands: the open-tie override
   (`SSTI_ISLAND`) comes off, and the row's note names the case its worst row comes from instead of
   `SSTI_GRID_ONLY`.
3. The pack judges the island rows under the row's limit and rule, and drops "not evaluated (islanded)"
   for the grid rows of an islanded project in favour of the island rows.

## Done when

- An open-tie campus whose island UIF flags reads warn on the scorecard, naming the island case.
- A closed-tie campus with island requirements whose worst UIF is islanded says so in the note.
- The pack prints the island rows judged; existing GC-08 / GC-08b tests pass unchanged.
