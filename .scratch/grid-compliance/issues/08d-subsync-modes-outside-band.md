<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08d Torsional modes outside the sub-synchronous scan band

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A2, gridspine)
Owner decision: GC-SCOPE-1, GC-TEXT-1
Likely files: `gridspine/static/subsync_scan.py`, `gridspine/drivers/campus_study.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-01c lets a genset list torsional modes in (0, f0) Hz. The scan covers 5 to f0 − 5 Hz
(`subsync_scan.F_MIN_HZ`), so a series resonance whose complement f0 − f_er falls near a mode
outside that band is never found, and nothing tells the user that such a mode was not judged.

## Done when

- A mode outside the scan band is named on the resonance rows (or the screen's notes) as not judged
  by the scan, with the band, and the scorecard reason carries it.
- A test pins the note text literally for a mode below 5 Hz and one above f0 − 5 Hz.
- A run whose only series rows are far-side "no series resonance in the band" rows says so on the
  scorecard (today it reads unknown with "The run wrote no torsional margin for a series resonance").

## Comments

**2026-11-23.** Filed from GK A2's review of #1930 (GC-01c).
