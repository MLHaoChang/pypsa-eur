<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06 PSCAD / EMT pack: parameters, Thevenin min/max, case schedule, automation stub, RSCAD notes

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: 04 (part 1 only, `gridspine/handoff/test_battery.py`); parts 2–3 can start at once
Gated: **yes** (GK A, gridspine)
Model tier: sonnet
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: 02, 03, 05, 07, 17, 19–23 (EMT inputs)
Likely files: new `gridspine/handoff/emt_pack.py`, `gridspine/handoff/campus_bundle.py` (shared with GC-04: adds only the EMT pack call), `gridspine/static/campus_sc.py` (read-only use), `gridspine/static/shortcircuit.py` (read-only use), new `tests/gridspine/test_emt_pack.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

EMT studies (TRGC 05, 07, 17, 19–22) and HIL (23) are built by hand in PSCAD / RSCAD from plant
data. The app holds that data but exports none of it in a form an EMT engineer can load.

## What to build

1. **Case schedule**: the GC-04 test battery written as a PSCAD case schedule (CSV/JSON: case id,
   Thevenin set, event, timings, output channel names from the contract) plus an `mhi.pscad`
   automation stub that loops the schedule. The stub is generated text, not executed.
2. **Thevenin set**: Sk min/max and X/R at the PoC per stage and per study case, from the existing
   IEC 60909 fault levels, as CSV/JSON with source notes.
3. **Parameter workbook**: plant, converter, transformer (incl. saturation/inrush data where held),
   cable, filter, protection-relay settings and insulation data (BIL, arrester data where held),
   one sheet each; missing fields listed as "to be supplied", not invented. A short RSCAD note
   names the same sheets for HIL use.
4. Pack manifest (model-set id when GC-01 is present, data hash, file list).

## Done when

- Thevenin R and X from Sk and X/R match a hand calculation for one fixture case (1e-6); Sk min ≤
  Sk max is enforced with a named error.
- Every battery case appears once in the schedule; channel names equal the contract's.
- The automation stub compiles (`compile()`) and references only files in the pack.
- A unit missing a required parameter appears in the "to be supplied" list (test kills "write
  zero instead").

Release note: one new file under `doc/release-notes.d/` (see its README).
