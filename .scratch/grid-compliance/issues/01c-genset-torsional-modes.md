<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-01c Torsional modes of a genset in the campus description

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A2, gridspine)
Model tier: opus
Size: S
Owner decision: GC-01c-TORSION-1 (2026-11-17), GC-SCOPE-1, GC-TEXT-1
TRGC: 20 (sub-synchronous resonance screening, GC-08)
Likely files: `gridspine/ingest/campus.py`, `gridspine/schema/campus.py`, `gridspine/drivers/campus_study.py` (the `ssti_screens` step passes the modes on), `tests/gridspine/test_campus_torsional_modes.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-08's sub-synchronous scan takes `torsional_modes={machine: [f_mode_hz, ...]}`. The campus has no
way to enter them, so the series-resonance verdict reads `check` ("no torsional modes given") on
every project, and the scorecard's `subsync_series_resonance` row stays unknown. GK A2 asked for this
as its own issue, kept separate from GC-01b, whose plan already has its GO.

## What to build

1. An optional per-genset key, e.g. `torsional_modes_hz`: a list of positive finite frequencies
   below f0, each with a `source` (`code` / `datasheet` / `assumed`) like other campus values.
   Anything else is refused with a ContractError.
2. `campus_study.ssti_screens_campus` passes them to `subsync_scan(..., torsional_modes=...)`.
3. A genset with no modes keeps reading `check`; one with modes is judged against the clearance.

## Done when

- A campus with a genset whose modes are entered builds. The scan judges its series resonances
  against them (flag below the clearance, clear at or above it), and the scorecard row reads warn or pass.
- Bad input is refused: negative, non-finite, at or above f0, or a non-list.
- A genset without modes still reads `check`, and existing GC-08 tests pass unchanged.

## Comments

**2026-11-17.** Filed from GC-08 PR3 (the owner picked option 1 via the coordinator). Conditions
from GK A2's GC-08 rulings that carry over: an uncomputable row reads "not evaluated" with a reason,
and a reader round trip is not an oracle (test the margin against a hand-computed f0 − f_er).
