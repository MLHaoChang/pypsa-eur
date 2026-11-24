<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08b SSTI UIF screen: the islanded case

Status: done
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (follows GC-08)
Gated: **yes** (GK A2, gridspine)
Model tier: opus
Size: S
Priority: ahead of GC-11 (GK A2 R4c)
Owner decision: GC-SCOPE-1, GC-TEXT-1
TRGC: 19 (screening; binding studies stay in PSCAD)
Likely files: `gridspine/static/ssti_uif.py`, `gridspine/static/campus_sc.py` (read-only unless the grid-out case needs a flag), `tests/gridspine/test_ssti_uif.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

`ssti_uif.campus_uif` counts the grid's infeed (the campus `ext_grid`) in SC_i and SC_tot in every
case, including on a campus whose PCC tie is normally open (`pcc.tie_normally_open`). That
understates the islanded UIF badly. Measured on the GC-08 test hub with one 10 MVA genset on MV,
S_plant 82.6 MVA at MV and a 2000 MVA grid:

- with the grid counted (what the screen does today): SC_i 531.9 MVA, SC_tot 604.4 MVA, UIF 0.119 (MV, max);
- islanded, the true case: SC_i ≈ 0 (no other machine) and SC_tot = the genset's own SC, so UIF ≈ S_plant / S_i ≈ 8.3.

That is about 70× low. For now the scorecard's `ssti_uif` row reads unknown when the tie is open
(GC-08 PR3, GK A2 R4a), and the GC-09 pack entry marks the islanded case "not evaluated" (R4b).
A campus whose tie is normally closed but can island is also unscreened in islanded operation.
Every judged UIF row says "grid-connected state only" (R4d).

## What to build

1. In `campus_uif`, add an `island` case with the ext_grid out when the tie is normally open. Decide
   with GK A2 whether to also add it for every campus that can island. SC_i and SC_tot come from
   the machines alone in that case.
2. The verdict is the worst over the grid cases and the island case. The island rows carry their own
   case label and c.
3. Once the engine evaluates the island, the scorecard's open-tie override
   (`grid_readiness._ssti_rows`, `SSTI_ISLAND`) and the pack's "not evaluated" mark come off.

## Done when

- The fixture above, with the tie open, gives the island UIF hand-computed from the genset's own
  fault level to 1e-6 (≈ 8.3), and the verdict flags.
- A two-genset island gives (S_plant/S_i)(1 − SC_i/SC_tot)² with SC from the machines only, hand-computed.
- Existing GC-08 tests pass unchanged.
