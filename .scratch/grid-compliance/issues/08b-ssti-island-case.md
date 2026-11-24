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

## Comments

**GK A2 plan GO (2026-11-24), on conditions U1–U6, verbatim:**

> - **Q1 = A.** Screening more campuses islanded is the conservative side.
> - **Q2 = A, but only with U4.** Until 08e lands, the island rows must not change what any existing reader judges.
>
> U1. Literal oracles (M6a). Hand-compute SC_i and SC_tot from x''d, K_G (IEC 60909 genset correction) and c = c_factor(vn, max/min), and state them as literals. Compare at rel 1e-9 or abs 1e-6, never with pytest.approx's default. Cover both cases: one genset (UIF = S_plant/S_i, SC_i = 0) and two gensets ((S_plant/S_i)(1−SC_i/SC_tot)²). Pin each island case, max and min, separately.
>
> U2. No source in the island. If the islanded set has no synchronous machine (converters only, or every genset out of the installed set), SC_tot = 0. The rows read "not evaluated" with that reason: never inf, NaN or a 500, and never clear. Test it. A converter-only campus with island data must not crash campus_uif.
>
> U3. Selection, both ways. Tests:
> - tie_normally_open gets the island case;
> - a closed tie with island requirements gets the island case;
> - a closed tie without island data gets no island calls and the same rows as today.
> "Islands" reads the campus's own fields (tie flag, island requirements), keyed in one predicate that campus_uif and its tests share.
>
> U4. The grid side is unchanged (ruling (b) and the GC-08 contract). For a campus that islands:
> - the grid-case rows equal literal values recorded from master;
> - island rows carry their own case label;
> - every existing reader still judges grid cases only: the grid SSTI card in grid_readiness, the GC-08 pack table, and the meta verdict/worst they read.
>
> Until 08e lands, either keep island rows out of the meta verdict and worst that those readers use, or mark them so the readers skip them. Either way, a backend test shows the grid card and the pack table are the same as master for an islanding campus, and the pack prints island rows as "not judged". If you'd rather do item 3 here, I'll review the backend part too; say which you choose in the body.
>
> U5. Mutants. Run these, each on its targeted test with -x and a timeout, and name each killer:
> - island=False at the fault-level call;
> - the SC_i = 0 shortcut removed or applied with two gensets;
> - c_max and c_min swapped;
> - the selection predicate dropping either half;
> - island rows entering the grid verdict.
>
> U6. Body and docs. File 08e: the scorecard SSTI_ISLAND override reads the island rows, and the pack judges them. Add a release note that names the new island cases and that grid results are unchanged. Confidentiality (GC-TEXT-1) applies to fixtures and names.

**Built (builder).** Island rows carry the role `island converter bus (not judged)` and the cases `island max` / `island min`; `UifStudy.island_verdict` / `island_worst_uif` hold their verdict, while `verdict` / `worst_uif` (the meta the readers use) stay the grid cases'. Item 3 is GC-08e. The pandapower two-genset anomaly found here is GC-08f.
