<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08 SSTI unit-interaction-factor screen and sub-synchronous impedance scan

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17); picks up SAGC-1 G8 ("later")
TRGC: 19, 20 (screening; binding studies stay in PSCAD)
Likely files: new `gridspine/static/ssti_uif.py`, new `gridspine/static/subsync_scan.py`, `gridspine/static/harmonic_scan.py` (read-only reuse of `_branches`; promote it to public, no behaviour change), `gridspine/drivers/campus_study.py` (shared; own step), `pypsa-gui/backend/services/campus_electrical_service.py` (shared; own section), new `tests/gridspine/test_ssti_uif.py`, `tests/gridspine/test_subsync_scan.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

TRGC 19 and 20 decide whether full EMT SSTI / SSR / SSCI studies are needed. A screen tells the
user early which nearby machines or series-compensated lines warrant them.

## What to build

1. **UIF screen** (EPRI method): UIF_i = (S_plant / S_i) · (1 − SC_i / SC_tot)² for each nearby
   synchronous machine i, with SC from the existing fault levels with and without the plant.
   Flag UIF above the profile threshold (default 0.1, labelled "assumed").
2. **Sub-synchronous scan**: R(f) and X(f) seen from the PoC for f in 5–(f0 − 5) Hz, with series
   capacitors modelled; report frequencies where X crosses zero with R < 0 margin risk, and series
   resonances against machine torsional modes where given.
3. Rows labelled "screening; binding answer is the EMT study".

## Done when

- UIF: hand calculation on a two-machine fixture to 1e-6; a machine with SC_i = SC_tot gives 0.
- Scan: a radial line with series capacitor of compensation k shows electrical resonance at
  f0·√k within one frequency step.
- Threshold edge test kills "≥ vs >"; no profile threshold → "assumed" label present.
- Existing harmonic-scan tests pass unchanged.

Release note: one new file under `doc/release-notes.d/` (see its README).
