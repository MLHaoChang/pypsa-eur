<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-05 PowerFactory DGS export with harmonic sources; background-harmonic import

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: 13, 14, 18
Likely files: new `gridspine/handoff/dgs_writer.py`, new `gridspine/ingest/background_harmonics.py`, `gridspine/readback/pf_compare.py`, `gridspine/readback/results.py`, `gridspine/readback/__init__.py` (shared with GC-07: exports only), `gridspine/static/harmonics.py` (read-only use), `gridspine/drivers/campus_study.py` (shared; own step), new `tests/gridspine/test_dgs_writer.py`, `tests/gridspine/test_background_harmonics.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

TRGC 13/14 (power quality) and 18 (small signal) run in PowerFactory. GridSpine can compare a
PowerFactory load-flow export (`readback/pf_compare.py`) but cannot write a network PowerFactory
reads, and the PQ screen assumes zero background distortion.

## What to build

1. **DGS writer**: buses, lines, cables, 2-winding transformers, loads, static generators,
   synchronous machines, shunts and filters, external grid at Sk min/max, in DGS (CSV-in-ZIP or
   ASCII `.dgs`). Harmonic current sources per converter from the spectra the harmonic study
   already uses (IEC 61000-3-6 summation exponents as attributes).
2. **Background-harmonic import**: field measurement per order (95th-percentile voltage
   harmonics, IEC 61000-4-30 style CSV) at the PoC, stored with source and date; the existing
   harmonics study adds it per IEC 61000-3-6 summation and labels the row "with background".
3. **Read-back extension**: `results.read_back` accepts the PowerFactory LF export of the DGS
   network (bus names from the DGS), so the compare is closed-loop.

## Done when

- DGS of a fixture parses back (own reader in the test) to the same element counts and
  impedances; per-unit values match the pandapower net within 1e-6.
- Each harmonic source carries the spectrum of its unit; a unit with no spectrum is written
  without one and listed.
- Background import: the combined voltage at order h equals the IEC 61000-3-6 sum
  (α = 1.4 for 5th–10th, hand value); a negative or missing order is rejected.
- LF read-back of a synthetic PowerFactory CSV keyed by DGS names matches within tolerance.

Release note: one new file under `doc/release-notes.d/` (see its README).
