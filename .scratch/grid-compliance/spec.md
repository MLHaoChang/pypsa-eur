<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Grid-compliance workflow and external-tool export packs: spec

Status: ready-for-agent
Owner input: 2026-11-17
Owner decisions: GC-SCOPE-1, GC-TEXT-1 (2026-11-17); builds on SAGC-1 (2026-10-23)

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC and its Technical
Requirements for Grid Connection reports, TRGC, published by SE / NGSA) and generic engineering.
TRGC report numbers and study titles may be named. Never name a project, customer, consultant,
EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Goal, in the owner's words

> "See what features we're currently missing in terms of getting the implementation done ...
> create a workflow to add the additional features that can be integrated within GridSpine, and
> also create the corresponding export needs to additional software solutions that then can be
> followed up for the grid compliance assessment."

GridSpine already screens the static side of a connection (load flow, N-1/N-2, short circuit,
SCR/ESCR, harmonics, flicker, unbalance, reactive capability). The TRGC chain also needs RMS
dynamics, EMT, small-signal and field-test studies. Those run in PSS/E, PowerFactory, PSCAD and
RSCAD (GC-SCOPE-1). The app prepares their inputs, test definitions and scripts, reads their
results back, judges them against the grid-code profile, and assembles the report pack.

## Owner decisions

- **GC-SCOPE-1.** Build all of GC-01..GC-09. Exports and result read-back for PSS/E,
  PowerFactory and PSCAD are in scope. This narrows SAGC-1's "PSS/E RAW import/export not
  chosen". Simulations (RMS dynamics, EMT, small signal, insulation coordination, HIL) stay in the
  external tools.
- **GC-TEXT-1.** TRGC numbers and titles may be named; no project, customer, consultant, EPC or
  OEM names; cite only the public code and generic engineering.

## The TRGC chain: four phases

| Phase | TRGC | Study | External tool |
|---|---|---|---|
| 1. Preliminary design and model | 01 / 02 | Detailed and aggregated plant model | PSS/E, PSCAD |
| | 03 | Dynamic performance and control tests | PSS/E, PSCAD |
| | 04 / 05 | Dynamic model acceptance tests (DMAT) | PSS/E, PSCAD |
| | 09 / 10 | Model user guides (RMS, EMT) | n/a (documents) |
| | 11 | Plant datasheets | n/a |
| | 12 | Voltage control strategy | load flow |
| 2. Compliance | 06 / 07 | Grid-code compliance, RMS and EMT | PSS/E, PSCAD |
| | 08 | RMS vs EMT model benchmark | PSS/E + PSCAD |
| 3. Grid impact | 13 / 14 | Power quality (field background, network study) | PowerFactory |
| | 15 | Steady state, N-1 / N-2 | PSS/E |
| | 16 | Dynamic stability | PSS/E |
| | 17 | Auto-reclose | PSCAD |
| | 18 | Small-signal stability | PowerFactory |
| | 19 | Sub-synchronous torsional interaction (SSTI) screening | PSCAD |
| | 20 | SSR / SSCI | PSCAD |
| | 21 | Control interaction | PSCAD |
| | 22 | Insulation coordination | PSCAD |
| 4. Testing and field validation | 23 | Hardware-in-the-loop (HIL) | RSCAD |
| | 24 / 25 | Field tests and model validation | PSS/E, PSCAD |

## Coverage today and the gap

| TRGC | App today | Gap → package |
|---|---|---|
| 01/02 | Detailed campus model (`gridspine/ingest/campus.py`), Thevenin at the PCC | No aggregated equivalent, no vendor-model slot, no model versioning or data freeze → GC-01 |
| 03 | none (dynamics external by design) | Test-battery definition and export → GC-04, GC-06 |
| 04/05 | none | DMAT case set (SMIB at Sk min/max and X/R; faults, setpoint and frequency steps) as psspy scripts and a PSCAD case schedule → GC-04, GC-06 |
| 06/07 | Dynamic requirements listed as "needs an RMS study" (`templates/grid_codes.py`) | Read back channels, judge against profile envelopes (LVRT/HVRT, reactive current, recovery, settling) → GC-07 |
| 08 | LF-only PowerFactory compare (`readback/pf_compare.py`) | Time-series benchmark metrics (windowed errors, IEC 61400-27-2 style) → GC-07 |
| 09/10, 11 | Control-settings sheet only | Model user guides and connection datasheets generated from model + export metadata → GC-09 |
| 12 | Q band, PF rows, Q capability (`static/campus_reactive.py`); deadband stored only | Q(V), V-control and PF modes plus PPC setpoint in the LF; PoC P–Q at 0.9–1.1 pu → GC-02 |
| 13/14 | Harmonics, TDD, scan, flicker, unbalance | Background-harmonic measurement import; PowerFactory DGS export → GC-05 |
| 15 | LF, N-1 AC, N-2 LODF, contingency generation | `static/planning_criteria.py` and ESCR/WSCR (`static/strength.py`) engine-only; no TSO list import; no `.con` from the TSO list → GC-03, GC-04 |
| 16 | `.dyr` GENROU/GENSAL, REGCA1+REECA1 (`handoff/dyr_writer.py`); RAW v33 | No plant controller (REPCA1), no WTG/BESS models, no RAW v35/36, no `.seq` → GC-04 |
| 17, 21, 22, 23 | none | External; data in the GC-06 pack |
| 18 | none | External; DGS export GC-05 |
| 19 | none | UIF screen (EPRI method; SAGC-1 G8 "later") → GC-08 |
| 20 | Harmonic scan engine, no sub-synchronous range | Sub-synchronous impedance scan screen → GC-08 |
| 24/25 | none | Field-measurement import + the same benchmark engine → GC-07 |

## In-app workflow

1. **Data gathering and data freeze.** A data list per study (what each TRGC study needs, who
   supplies it, status). Freezing hashes the campus input and parameter sets into a model version
   (GC-01 engine, GC-09 UI).
2. **Model, detailed and aggregated, versioned.** The detailed campus model and its aggregated
   equivalent (WECC single-machine / collector equivalent), each tagged with the frozen data
   version (GC-01).
3. **In-app screens.** Static studies run in GridSpine and land on the scorecard: planning
   criteria, SCR/ESCR/WSCR (GC-03), voltage-control strategy (GC-02), PQ (existing + GC-05),
   SSTI/SSCI screens (GC-08). Each row says "screening" where the binding answer is external.
4. **Export packs per tool.** PSS/E (GC-04), PowerFactory (GC-05), PSCAD / RSCAD (GC-06). Each
   pack carries a manifest: model version, data hash, contents, the test battery it implements,
   the channel names it writes.
5. **Read back, judge, benchmark.** Result files come back; the app judges each test against the
   profile envelope and computes RMS vs EMT and model vs field benchmarks (GC-07).
6. **Report pack.** Per TRGC deliverable: report skeleton, datasheets, user guides, the pass/fail
   tables, and a TSO comment / revision log per deliverable (GC-09).

## Export-format matrix

| Format | Target tool | Contents | Package |
|---|---|---|---|
| RAW v35 / v36 (v33 kept) | PSS/E | Network, machines, plant at PoC; detailed or aggregated | GC-04 |
| `.seq` | PSS/E | Sequence data for unbalanced faults | GC-04 |
| `.dyr` | PSS/E | GENROU/GENSAL, REGC_A/B, REEC_A/C/D, REPC_A, WTG (WT3/WT4) set | GC-04 |
| `.con` (+ `.sub`, `.mon`) | PSS/E | Contingencies from the generated set or the imported TSO list | GC-04 |
| psspy `.py` scripts | PSS/E | DMAT and compliance cases (SMIB, faults, steps), channel output | GC-04 |
| DGS (`.dgs` / CSV-in-ZIP) | PowerFactory | Network, plant, harmonic current sources, background harmonics | GC-05 |
| Parameter workbook (CSV/XLSX) | PSCAD, RSCAD | Plant, transformer, cable, filter, protection and equipment data | GC-06 |
| Thevenin set (CSV/JSON) | PSCAD | Sk min/max, X/R per case and stage | GC-06 |
| Case schedule + `mhi.pscad` stub | PSCAD | DMAT and compliance tests, run order, channel names | GC-06 |
| Channel CSV / COMTRADE in | read-back | PSS/E `.out` → CSV, PSCAD `.out`/`.inf`, field COMTRADE | GC-07 |
| Report pack (DOCX/PDF + CSV) | TSO | Skeletons, datasheets, user guides, results, revision log | GC-09 |

## Out of scope

- Running RMS, EMT, small-signal, auto-reclose, control-interaction, insulation-coordination or
  HIL simulations inside the app (GC-SCOPE-1).
- Writing vendor (black-box) models; GC-01 holds a slot that points at a vendor model file.
- RAW import into GridSpine (export only; read-back is of results, not networks).
- Naming any project, customer, consultant, EPC or OEM (GC-TEXT-1).
- Licence-bound tool automation that needs a running PSS/E, PowerFactory or PSCAD in CI: scripts
  are generated and checked for syntax and content, never executed in CI.

## Packages

| Id | Package | Model | Gate | Blocked by |
|---|---|---|---|---|
| GC-01 | Plant model aggregation + versioned model set, data freeze | opus | GK A | none |
| GC-02 | Voltage-control strategy in the LF (Q(V), V, PF, PPC); PoC P–Q across voltage | opus | GK A | none |
| GC-03 | Planning criteria + ESCR/WSCR in the campus run and scorecard; TSO contingency-list import | opus | GK A | none |
| GC-04 | PSS/E pack: RAW v35/36, `.seq`, WECC 2nd-gen `.dyr`, `.con`, psspy DMAT/compliance scripts; owns the test battery | opus | GK A | none |
| GC-05 | PowerFactory DGS export + harmonic sources; background-harmonic import | opus | GK A | none |
| GC-06 | PSCAD / EMT pack: parameters, Thevenin min/max, case schedule, automation stub; RSCAD notes | sonnet | GK A | 04 (test battery only) |
| GC-07 | Read-back, automatic compliance judgment, RMS–EMT and field benchmark | opus | GK A | none (parts 1–4); part 5 waits on 04 and 06 |
| GC-08 | SSTI UIF + sub-synchronous impedance scan screens | opus | GK A | none |
| GC-09 | Compliance workflow UI: deliverable tracker, data list, revision log, report pack | opus | GK B + UX | none (rows light up as 01–08 land) |

**How they meet.** GC-04 owns the tool-neutral test battery and channel contract
(`gridspine/handoff/test_battery.py`); GC-06 writes PSCAD cases from it and GC-07 reads results
named by it. GC-01's model-set manifest is what every pack's manifest cites; packs work on the
detailed model until it lands. GC-09 reads each package's outputs through the campus electrical
service and owns no engine code.

**Shared files.** `gridspine/drivers/campus_study.py` and
`pypsa-gui/backend/services/campus_electrical_service.py` (GC-02, GC-03, GC-05, GC-08: each adds
its own step function and section; no edits to another's); `gridspine/templates/grid_codes.py`
(GC-02 voltage-control keys, GC-07 dynamic envelope keys); `gridspine/readback/__init__.py`
(GC-05, GC-07 exports only); `gridspine/handoff/campus_bundle.py` (GC-04, GC-06: one pack call each).
