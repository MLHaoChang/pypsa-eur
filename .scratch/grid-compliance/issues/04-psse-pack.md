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

## Comments

**GK A2 plan verdict (2026-11-17): GO with conditions.** Four slices, one PR each (PR-SIZE-1).

PR1 `test_battery.py`
- B1 Finite means `math.isfinite`; test inf and NaN separately for Sk, X/R, every param and every time.
- B2 One refusal test per guard, asserting its own reason, each killed by a mutant of that guard alone: Sk ≤ 0 (at 0 exactly), X/R ≤ 0, duration ≤ 0 (0 refused), t_event ≥ t_end (equal case), fault clearing at or after t_end, unknown channel, missing kind-required param, duplicate id; also empty channels, sk_min > sk_max, xr_min > xr_max.
- B3 Fault severity is `v_retained_pu` in [0, 1); |phase jump| < 180°; ramps and steps nonzero.
- B4 Each channel states unit, base, sign convention and measurement point (iq/id in pu of plant rated current, Q positive = injection).
- B5 `battery_json` carries `contract_version`, deterministic (sorted keys, fixed float repr, byte-equal test); `cases_from_json` refuses a mismatched version.
- B6 cases → frame/JSON → cases identical field by field, for the built battery and an overridden one.
- B7 Pin case count and ids for one input set with a hand-listed expected set.
- B8 Generic defaults carry `source='assumed'`; TRGC cited by number only (GC-TEXT-1).

PR2 RAW v35/36 and `.seq`
- R1 Parse written files back with a test-side reader and compare with the network (counts, identities, R/X/B pu on SBASE) for v33, v35, v36.
- R2 Golden v35 header (SBASE, BASFRQ), system-wide block and every terminator in order; each v35/v36 field difference tested, citing the PSS/E POM by section.
- R3 `.seq` winding code for YNd and Dyn (winding swap mutant dies); zero-sequence Z on a transformer rated ≠ SBASE; machine/branch values checked against `island_sc_seq` sources.
- R4 v33 default output byte-identical (master's raw tests pass).

PR3 `.dyr`
- D1 Pin ICON/CON counts per model from the PSS/E model library with citation; state earliest PSS/E version per model; refuse a model the target version does not ship.
- D2 REPCA1 remote bus/branch must exist in the RAW; a missing branch is refused, not written as 0.
- D3 Round trip: model, bus, ID match the RAW; value counts match the pin; existing GENROU/GENSAL/REGCA1/REECA1 output byte-identical.

PR4 `.con`/`.sub`/`.mon`, psspy scripts, pack call
- S1 Scripts checked with `ast`: compile; every referenced file in the manifest (missing-reference test fails); every case channel written by contract name; no absolute paths, no network/OS calls beyond psspy, dyntools and os.path within the pack dir.
- S2 `.con` format stable for GC-03's import test; writer refuses an element not in the network.
- S3 `campus_bundle.py`: one pack call only, GC-06's untouched; manifest cites model_set_id/data_hash/variant only when GC-01 is present, tested both ways.

Every PR: release-note fragment, GC-TEXT-1 grep of the diff, targeted mutants and killing tests listed in the PR body.

**GK A2 on the PR2 approach (2026-11-17): GO with five changes.**
- Point 1 citations accepted: cite the PSS/E-written files (their `@!` field names, version, repo path); omitted trailing optional fields stated in the docstring; one golden test of each v35/v36 section order including empty sections. (GC-TEXT-1 check: the builder kept the utility-guide name out of code; confirm with GK whether it may appear in PR text.)
- Q1 No catch-all CC: map each vector group the campus schema allows (YNyn, YNy, Yyn, Yy, YNd, Dyn, Yd, Dy, Dd, Z if allowed) to its own code; refuse an unknown one with a named ContractError; one test per group plus the refusal.
- Q2 v33 uses CC 1–9 (11+ came later): write the v33 table for v33 (cited) or refuse v33 .seq; test YNd and Dyn in both versions.
- Q3 Reuse island_sc_seq.py's helpers for line R0/X0/C0, genset x2/x0 defaults and neutral data (call them or promote one to public with no behaviour change); test against the draft's value, not a retyped literal; state 3·0.727 ohm/km with units and source.
- Q4 Nothing missing silently: list each omitted earthing transformer ("not written: zero-sequence source omitted") in the pack manifest and file header, with a test (better: write it if the target version has a zero-sequence fixed-shunt record); list the assumed values (grid X0 = X1, vk0 = vk, OPEN_PU, genset X'/Xs = x''d) in the manifest, with a test.
- Q5 Cite that PSS/E triples the grounding impedance; hand-value test with nonzero R_N checking the file holds R_N (not 3·R_N) on the right base.
- Also run the test-side reader on .seq: counts and ids match the RAW, values match the source params.

**GK A2 on the PR3 plan (2026-11-17): GO with conditions** (recorded verbatim by the PR3 builder).
- Decision (a): refuse REGC_B and REEC_D with a named ContractError, "user model only (USRMDL) up to 35.2; not written".
- P1: each MODEL_PIN row names its source (repo path + doc title). The pack README/manifest says "dyr ICON/CON counts and order from public parser tables; pending verification on first PSS/E load", with a test. Never claim the counts come from the PSS/E manual.
- P2: REPCA1 attaches to a machine. Either every IBR machine of a plant gets its own REPCA1 (same BUSR/BUS1/BUS2/CKT), or the GC-01 aggregate gets one. Choose and state which. Test: every REGCA1 machine of a controlled plant has exactly one REPCA1.
- P3: BUS1 is the plant side of the PoC branch and BUS2 the grid side (export positive). Test with a branch whose RAW I is the grid side. CKT is the RAW's. A missing branch is refused (D2).
- P4: type 3 (WTTQA1) requires REECA1 PFLAG = 1. The REECA1/REPCA1 flags are checked as a set (REPCA1 drives Qext; PFFLAG = 0 under plant control). A contradictory set is refused, with one test and one mutant per rule.
- P5: every new CON is finite and in range like _check_ibr: time constants > 0 where PSS/E divides by them, min < max, breakpoints ordered. One refusal test per rule class. Assumed defaults carry source 'assumed' and are listed in the pack assumptions.
- P6: write_dyr version in {33, 35}, anything else refused by name. A v35 file of a v33-only set is byte-identical to v33. A golden tests/gridspine/data/case39_v33.raw from master's writer pins test_v33_default_unchanged (GK's PR2 follow-up); its commit or docstring names the master sha.
- P7: unit_params.py and campus_assets.yaml are additive only, with a pin test. Check GC-01 PR2 for touches to them first.
- Mutants on dyr_writer and unit_params; one targeted mutant per new guard.

PR3 research (secondary public sources only; no vendor primary is public). ICON/CON counts, earliest library version: REGCA1 1/14 (33); REECA1 6/45 (33); REECC1 5/45 (35; REECCU1 is a user model in 33/34); REPCA1 7/27 (33); WTDTA1 0/5, WTARA1 0/2, WTPTA1 0/10, WTTQA1 1/15 (all 33); REGCBU1 2/7 and REECDU1 6/77 user models only. Sources: andes `andes/io/psse-dyr.yaml`; GridPACK `src/parser/parser_classes`; PowerSystemsTestData `psse_dyr/240busWECC_2018_PSS.dyr` l.800-807 (REPCA1 sample); WECC "Summary of 2nd Generation Generic RES Models Rev5" p.4 (model sets).
