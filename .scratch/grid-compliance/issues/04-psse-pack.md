<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04 PSS/E pack: RAW v35/36, .seq, WECC second-generation .dyr, .con, psspy test scripts

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (uses the detailed model until GC-01 lands; one call site switches to the model set)
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: L (four PRs: test battery first, then RAW/.seq, then .dyr, then .con + scripts)
Owner decision: GC-SCOPE-1 (narrows SAGC-1 "PSS/E RAW export not chosen"), GC-TEXT-1 (2026-11-17)
TRGC: 03, 04/05, 06, 15, 16 (RMS side)
Likely files: `gridspine/handoff/raw_writer.py`, `gridspine/handoff/dyr_writer.py`, `gridspine/handoff/contingencies.py`, `gridspine/handoff/bundle.py`, `gridspine/handoff/campus_bundle.py` (shared with GC-06: each adds its own pack call), `gridspine/templates/unit_params.py`, `gridspine/templates/data/campus_assets.yaml`, new `gridspine/handoff/test_battery.py`, new `gridspine/handoff/seq_writer.py`, new `gridspine/handoff/psspy_scripts.py`, `tests/gridspine/test_dyr_writer.py`, new `tests/gridspine/test_raw_v35.py`, `tests/gridspine/test_seq_writer.py`, `tests/gridspine/test_test_battery.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

The handoff writes RAW v33 and a `.dyr` with GENROU/GENSAL and REGCA1+REECA1 only. TRGC 03–06
and 16 need current RAW versions, sequence data, the full WECC second-generation plant set
including the plant controller, contingency files, and scripted model-acceptance and compliance
cases.

## What to build

1. **Test battery** (`test_battery.py`, tool-neutral, first PR): the DMAT and compliance cases
   as data: SMIB at Sk min/max and X/R per case; 3-ph and 1-ph faults with depth and duration;
   V, Q, P setpoint steps; frequency ramps and steps; phase-angle jump. Each case names its
   required output channels from one channel contract (`v_poc_pu`, `p_poc_mw`, `q_poc_mvar`,
   `iq_pu`, `id_pu`, `f_hz`, ...). GC-06 and GC-07 consume this module.
2. **RAW v35/36** in `raw_writer.py` (v33 kept, `version=` argument), including the system-wide
   data and the v35 record changes.
3. **`.seq`** (`seq_writer.py`): zero/negative sequence for machines, branches, transformers
   (winding connection codes) from the existing sequence data (`static/island_sc_seq.py` inputs).
4. **`.dyr` WECC second-generation set**: REGC_A/B, REEC_A/C/D (BESS), REPC_A plant controller
   (with its remote bus and branch), WT3/WT4 set; parameter groups in `unit_params.py` /
   `campus_assets.yaml`, generic defaults labelled "assumed".
5. **`.con`** from the generated set or the GC-03 imported list (`contingencies.py`), plus
   `.sub`/`.mon`.
6. **psspy scripts** (`psspy_scripts.py`): one script per battery case that loads RAW/.seq/.dyr,
   builds the SMIB, applies the event and writes channels with contract names.

## Done when

- Battery: every case has unique id, named channels from the contract, and finite parameters; a
  case with Sk ≤ 0 or negative duration is refused.
- RAW v35 of the case39 fixture: record counts, bus numbers, and branch R/X/B equal the v33 export
  field by field; a golden-file test pins the header and section terminators.
- `.seq` of a YNd transformer fixture carries the right connection code; zero-sequence
  impedance equals the source value.
- `.dyr` REPC_A record has the expected ICON/CON counts; ID/bus match the RAW; extending the
  existing `test_dyr_writer.py` round-trip.
- Generated scripts compile (`compile()`), reference only files in the pack, and contain every
  battery channel name (no psspy needed in CI).
- Pack manifest cites the model-set id when GC-01 is present.

Release note: one new file under `doc/release-notes.d/` (see its README).

**GK A2 on the PR2 approach (2026-11-17): GO with five changes.**
- Point 1 citations accepted: cite the PSS/E-written files (their `@!` field names, version, repo path); omitted trailing optional fields stated in the docstring; one golden test of each v35/v36 section order including empty sections. (GC-TEXT-1 check: the builder kept the utility-guide name out of code; confirm with GK whether it may appear in PR text.)
- Q1 No catch-all CC: map each vector group the campus schema allows (YNyn, YNy, Yyn, Yy, YNd, Dyn, Yd, Dy, Dd, Z if allowed) to its own code; refuse an unknown one with a named ContractError; one test per group plus the refusal.
- Q2 v33 uses CC 1–9 (11+ came later): write the v33 table for v33 (cited) or refuse v33 .seq; test YNd and Dyn in both versions.
- Q3 Reuse island_sc_seq.py's helpers for line R0/X0/C0, genset x2/x0 defaults and neutral data (call them or promote one to public with no behaviour change); test against the draft's value, not a retyped literal; state 3·0.727 ohm/km with units and source.
- Q4 Nothing missing silently: list each omitted earthing transformer ("not written: zero-sequence source omitted") in the pack manifest and file header, with a test (better: write it if the target version has a zero-sequence fixed-shunt record); list the assumed values (grid X0 = X1, vk0 = vk, OPEN_PU, genset X'/Xs = x''d) in the manifest, with a test.
- Q5 Cite that PSS/E triples the grounding impedance; hand-value test with nonzero R_N checking the file holds R_N (not 3·R_N) on the right base.
- Also run the test-side reader on .seq: counts and ids match the RAW, values match the source params.
