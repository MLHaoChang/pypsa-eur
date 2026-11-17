<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-03 Planning criteria and SCR/ESCR/WSCR in the campus run; TSO contingency-list import

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17); builds on SAGC-1 G4, G6
TRGC: 15 (and the strength inputs of 04/05, 16, 19–21)
Likely files: `gridspine/drivers/campus_study.py` (shared; own step function), `gridspine/static/planning_criteria.py` (read-only use of `screen_planning`), `gridspine/static/strength.py`, `gridspine/static/strength_outages.py`, `gridspine/static/contingency_set.py`, new `gridspine/ingest/contingency_list.py`, `pypsa-gui/backend/services/campus_electrical_service.py` (shared; own section), `pypsa-gui/backend/services/adequacy/grid_readiness.py`, new `tests/gridspine/test_contingency_list.py`, `tests/gridspine/test_campus_study.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

`static/planning_criteria.py` (`screen_planning`) and `static/strength.py`
(`strength_table`, ESCR/WSCR) exist and are tested, but nothing in the campus run or scorecard
calls them. TRGC 15 is run against the TSO's own contingency list, which cannot be imported.

## What to build

1. **Campus step** `planning_campus(run_dir, profile)` in `campus_study.py`: runs
   `screen_planning` over the campus contingency set, writes `campus_planning.csv`.
2. **Campus step** `strength_campus(run_dir, profile)`: intact and worst-outage SCR, ESCR, WSCR per
   PoC and stage (`strength_table` + `outage_fault_levels`), writes `campus_strength.csv`.
3. **TSO contingency-list import** (`ingest/contingency_list.py`): read a PSS/E `.con` subset
   (`OPEN BRANCH`, `OPEN LINE`, `TRIP`/`DISCONNECT` bus/machine) and a CSV form; map elements
   to the campus net by bus number/name; unmatched elements are listed, not dropped. The result has
   the `contingency_set` columns so `screen_planning` takes it unchanged.
4. **Scorecard rows** in `grid_readiness.py` and a service section, labelled
   "static screen; binding answer is the RMS/EMT study" for strength.

## Done when

- A run on the existing campus fixture writes both CSVs; worst-outage SCR ≤ intact SCR per PoC.
- `.con` round-trip: `handoff/contingencies.write_contingencies` output → import → identical
  element sets. Malformed lines and unknown elements raise/list with line numbers.
- An imported list with one unmatched branch reports it as unmatched and still screens the rest
  (test kills "silently drop unmatched").
- Service and scorecard tests show the new rows; a missing profile band reads "unknown".

Release note: one new file under `doc/release-notes.d/` (see its README).

## Comments

**2026-11-17, GK A2 plan GO** (three PRs: PR1 contingency-list import, PR2 campus steps, PR3 service and scorecard). Conditions:

- (a) "`.con` round-trip" now means the CSV round-trip through `write_contingencies`, plus the `.con` parser tested on fixture text in real PSS/E syntax (POM contingency description data). The true `.con` round-trip is a follow-up once GC-04 lands a `.con` writer to the shared grammar in `ingest/contingency_list.py`. Accept `CKT` and `CIRCUIT`, and `REMOVE MACHINE id FROM BUS n`. A valid PSS/E command outside the subset is listed as `unsupported` with its line number, never as malformed, and its contingency is handled like a partial one.
- (b) Fail closed. A partly matched contingency is excluded and listed. The planning row never reads pass while any imported contingency went unscreened (partial, unmatched, unsupported); it shows the count. A test pins this.
- (c) A new optional profile key `scr_min` ({value, clause, source}) is added to `PROFILE_KEYS`. No value ships unless the public code states one, cited by clause. A missing key reads "unknown", never pass.
- PR1:
  - C1: a bus name matching more than one bus is listed, never matched to the first. A test checks that the importer and `write_raw` number buses identically.
  - C2: each refusal and each unmatched reason has its own test with its line number.
  - C3: the unmatched-element test also checks that the matched rest of that contingency is not screened and that other contingencies are.
- PR2:
  - P1: run at the max-import and the max-export hour per period.
  - P2: a non-converged case is a violation, and an empty set or zero screened cases never reads pass.
  - P3: strength values are hand-calculated with literal expected values. SCR = S_k,min / P_rated, and ESCR subtracts the PoC's shunt and filter Mvar (tested with a capacitor). WSCR = Σ(S_k,i·P_i)/(ΣP_i)², checked on two PoCs with unequal P. Cite the c factor (IEC 60909 c_min).
  - P4: only the two new step functions are added to `campus_study.py`.
- PR3:
  - S1: a failed step writes ERROR and its rows read error or unknown, never pass.
  - S2: the RMS/EMT note appears exactly once per strength row, and the existing `_scr_rows` stay byte-identical.
  - S3: only this package's wrappers, sections and two `RUN_STAGES` entries go into the service. A merge with GC-08 keeps both sets of entries.
- Every PR: a release-note fragment, a GC-TEXT-1 grep of the diff, and the targeted mutants with their killing tests listed in the PR body.
