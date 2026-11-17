<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06 PSCAD / EMT pack: parameters, Thevenin min/max, case schedule, automation stub, RSCAD notes

Status: done (#1587, #1596; follow-ups 06a–06d)
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

## Comments

**GK A2 plan verdict (2026-11-17): GO, two PRs.**

Answers: (a) the EMT source is always the PCC grid data; per-stage fault levels are reference
columns labelled "reference, not the EMT source". (b) Z0 per corner in GC-06's own files from
`x0x_<case>` / `r0x0_<case>`; a case without it goes to to_be_supplied with a named reason; a
fallback borrowed from the max case is an explicit "assumed: from max" row; where a schedule row's
source Z0 differs from the battery's single z0, the manifest lists it. The GC-04 battery file is
left alone (per-corner z0 in the battery is a GC-04 follow-up if needed).

PR1 `emt_pack.py`: Thevenin set, case schedule, automation stub, manifest
- E1 Every Thevenin row states "IEC 60909 c not applied (as the test battery); with c_max = 1.1 the
  max-case |Z| is 10 % higher"; the manifest lists it under assumptions to confirm with the TSO;
  a test pins the note on the max row.
- E2 rx = 0 refused ("X/R not given / infinite: an EMT source needs a finite X/R"); sk_min > sk_max
  and rx = 0 each raise their own message, two tests, each guard mutated.
- E3 Hand calc of one corner's R and X to 1e-6 with literals; a second test with X/R from rx_max
  kills the 1/rx mapping and the min/max swap.
- E4 Every battery case exactly once; channel names equal the contract in order; round trip gives
  the same Case ids; fault R/X from `fault_impedance_ohm`.
- E5 run_schedule.py compiles; AST checks (pack-only file literals, no absolute paths, allowed
  imports); no eval/exec/subprocess/os.system; header "generated, never executed in CI; written for
  the mhi.pscad API version N, untested against a PSCAD install".
- E6 Manifest bytes deterministic; data_hash covers every listed file and moves when one changes;
  model_set_id only when passed, tested both ways.
- E7 EmtPackError subclasses ContractError; a catching-site test kills the base-class mutant.

PR2 parameter workbook, RSCAD note, bundle call
- W1 Never 0: each missing required parameter goes to to_be_supplied.csv; one unit missing one
  parameter, "write 0" mutant killed.
- W2 Units in every column header; values keep their source tag; a unit-precedence case where
  catalogue and explicit units differ (#1057).
- W3 campus_bundle.py: one call and one manifest entry; GC-04's lines byte-unchanged; line order
  agreed with GC-04.

Both PRs: GC-TEXT-1 grep, release-note fragment, mutants on emt_pack.py with survivors argued.
